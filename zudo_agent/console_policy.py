"""Human-only credential setup. Never activates services or accepts secret arguments."""
import fcntl
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import warnings

from .console import validate_policy
from .hub import strict_json


class PolicySetupError(ValueError):
    """Only fixed, credential-free messages may reach the CLI."""


def hidden_secret():
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise PolicySetupError("Password setup requires an interactive local terminal; pipes are refused")
    try:
        with open("/dev/tty", "rb") as tty:
            if not os.isatty(tty.fileno()):
                raise PolicySetupError("A controlling terminal is required")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            first = getpass.getpass("New secret (password-manager random, at least 32 random bytes; hidden): ")
            second = getpass.getpass("Confirm secret (hidden): ")
    except (OSError, EOFError, getpass.GetPassWarning, KeyboardInterrupt):
        raise PolicySetupError("Hidden password entry unavailable or cancelled; policy unchanged") from None
    if not hmac.compare_digest(first.encode(), second.encode()):
        raise PolicySetupError("Secrets did not match; policy unchanged")
    # Length is a typo guard, not an entropy estimator. Never generate a secret.
    if not 43 <= len(first) <= 256 or not first.isascii() or any(ord(c) <= 32 or ord(c) >= 127 for c in first):
        raise PolicySetupError("Use 43–256 printable non-space ASCII characters from a password manager, encoding at least 32 random bytes")
    return hashlib.sha256(first.encode("utf-8")).hexdigest()


def git_directory(fd):
    try:
        os.stat(".git", dir_fd=fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def directory(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or path.name in {"", ".", ".git"}:
        raise PolicySetupError("Policy requires an absolute path outside Git")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parent.parts[1:]:
            if part == ".git":
                raise PolicySetupError("Policy must be outside Git")
            if git_directory(fd):
                raise PolicySetupError("Policy must be outside Git")
            # dir_fd traversal prevents symlink ancestors, including replacement races.
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = next_fd
        if git_directory(fd):
            raise PolicySetupError("Policy must be outside Git")
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise PolicySetupError("Policy directory must be owned by you and not writable by others")
        return fd, path.name
    except BaseException:
        os.close(fd)
        raise


def token(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_policy(fd, name, config):
    file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(file, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 8192):
            raise PolicySetupError("Existing policy must be your regular single-link mode-0600 file")
        data = stream.read(8193)
        if len(data) > 8192:
            raise PolicySetupError("Policy exceeds size limit")
        return validate_policy(strict_json(data), config), token(info)


def configure(path, config, *, create=False, identity=None, projects=None):
    fd, name = directory(path)
    lock = None
    temporary = None
    try:
        lock = os.open(name + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise PolicySetupError("Unsafe policy lock file")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PolicySetupError("Another policy setup is active") from None
        if create:
            try:
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise PolicySetupError("Policy already exists; use password to preserve its settings")
            policy = validate_policy(dict(identity=identity, projects=projects, allow_input=False, password_sha256="0" * 64), config)
            before = None
        else:
            policy, before = read_policy(fd, name, config)
        policy["password_sha256"] = hidden_secret()
        data = (json.dumps(policy, indent=2) + "\n").encode()
        if len(data) > 8192:
            raise PolicySetupError("Policy exceeds size limit; no update made")
        check_fd, _ = directory(path)
        try:
            original, current = os.fstat(fd), os.fstat(check_fd)
            if (original.st_dev, original.st_ino) != (current.st_dev, current.st_ino):
                raise PolicySetupError("Policy directory changed; no update made")
        finally:
            os.close(check_fd)
        temporary = ".pane-policy-" + secrets.token_hex(16)
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if create:
            # Atomic no-overwrite publication, even if another writer created it.
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        else:
            _, now = read_policy(fd, name, config)
            if now != before:
                raise PolicySetupError("Policy changed during password entry; no update made")
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            temporary = None
        if temporary:
            os.unlink(temporary, dir_fd=fd); temporary = None
        os.fsync(fd)
    finally:
        if temporary:
            os.unlink(temporary, dir_fd=fd)
        if lock is not None: os.close(lock)
        os.close(fd)
