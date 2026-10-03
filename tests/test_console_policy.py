"""Credential setup tests use public fixture strings only, never real credentials."""
import base64
import contextlib
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import pty
import select
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import mock_open, patch

from test_console import Backend, Console, POLICY, config
from zudo_agent.console import load_policy
from zudo_agent.console_policy import configure, hidden_secret, git_directory

SECRET = "PUBLIC-DISPOSABLE-FIXTURE-SECRET-DO-NOT-USE-1234567890"
HASH = hashlib.sha256(SECRET.encode()).hexdigest()


class PolicySetup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        # macOS /var is a symlink; resolve fixture root, never accept policy links.
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "policy.json"
        self.config = config(self.root)
        # Some managed workspaces mark even /tmp as a Git worktree. Ignore only
        # fixture ancestors in this test process; actual policy dirs stay checked.
        self.ancestors = [(p.stat().st_dev, p.stat().st_ino) for p in self.root.parents]
        def fixture_git(fd):
            info = os.fstat(fd)
            return False if (info.st_dev, info.st_ino) in self.ancestors else git_directory(fd)
        guard = patch("zudo_agent.console_policy.git_directory", side_effect=fixture_git)
        guard.start(); self.addCleanup(guard.stop)

    def existing(self):
        self.path.write_text(json.dumps(dict(POLICY, allow_input=True)))
        self.path.chmod(0o600)
        return self.path.read_bytes()

    def run_setup(self, **kwargs):
        with patch("zudo_agent.console_policy.hidden_secret", return_value=HASH):
            configure(self.path, self.config, **kwargs)

    def test_create_private_read_only_and_authentication(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            self.run_setup(create=True, identity="fixture", projects=["example"])
        self.assertEqual(output.getvalue(), "")
        policy = load_policy(self.path, self.config)
        self.assertFalse(policy["allow_input"])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.stat().st_uid, os.getuid())
        self.assertNotIn(SECRET.encode(), self.path.read_bytes())
        console = Console(self.config, policy, Backend()); self.addCleanup(console.shutdown)
        from email.message import Message
        headers = Message(); headers["Authorization"] = "Basic " + base64.b64encode(("fixture:" + SECRET).encode()).decode()
        self.assertTrue(console.authorized(headers))

    def test_password_change_preserves_settings_and_creation_never_overwrites(self):
        before = self.existing()
        with self.assertRaises(ValueError): self.run_setup(create=True, identity="other", projects=["example"])
        self.assertEqual(self.path.read_bytes(), before)
        self.run_setup()
        self.assertEqual(load_policy(self.path, self.config), dict(POLICY, allow_input=True, password_sha256=HASH))

    def test_existing_validation_unknown_projects_and_no_write_on_prompt_failure(self):
        with self.assertRaises(ValueError): self.run_setup(create=True, identity="fixture", projects=["guessed"])
        self.assertFalse(self.path.exists())
        before = self.existing()
        with patch("zudo_agent.console_policy.hidden_secret", side_effect=ValueError("mismatch")):
            with self.assertRaises(ValueError): configure(self.path, self.config)
        self.assertEqual(self.path.read_bytes(), before)
        self.path.chmod(0o644)
        with self.assertRaises(ValueError): self.run_setup()
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_existing_policy_and_lock_symlink_never_prompt(self):
        self.existing()
        self.path.write_text(json.dumps(dict(POLICY, unexpected="fixture")))
        with patch("zudo_agent.console_policy.hidden_secret") as prompt:
            with self.assertRaises(ValueError): configure(self.path, self.config)
            prompt.assert_not_called()
        self.existing()
        lock = Path(str(self.path) + ".lock")
        if lock.exists(): lock.unlink()
        lock.symlink_to(self.path)
        before = self.path.read_bytes()
        with self.assertRaises(OSError): self.run_setup()
        self.assertEqual(self.path.read_bytes(), before)

    def test_links_unsafe_directories_and_git_are_rejected(self):
        self.existing()
        link = self.root / "link.json"; link.symlink_to(self.path)
        with self.assertRaises(OSError): configure(link, self.config)
        hard = self.root / "hard.json"; os.link(self.path, hard)
        with self.assertRaises(ValueError): self.run_setup()
        hard.unlink()
        alias = self.root / "alias"; alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError): configure(alias / "policy.json", self.config)
        self.root.chmod(0o777)
        with self.assertRaises(ValueError): self.run_setup()
        self.root.chmod(0o700)
        (self.root / ".git").write_text("gitdir: fixture")
        child = self.root / "child"; child.mkdir()
        with self.assertRaises(ValueError): configure(child / "policy.json", self.config)

    def test_concurrent_modification_creation_and_lock_fail_closed(self):
        self.existing()
        changed = json.dumps(dict(POLICY, identity="changed"))
        def concurrent():
            self.path.write_text(changed)
            return HASH
        with patch("zudo_agent.console_policy.hidden_secret", side_effect=concurrent):
            with self.assertRaises(ValueError): configure(self.path, self.config)
        self.assertEqual(self.path.read_text(), changed)
        self.path.unlink()
        with patch("zudo_agent.console_policy.hidden_secret", side_effect=concurrent):
            with self.assertRaises(FileExistsError): configure(self.path, self.config, create=True, identity="fixture", projects=["example"])
        self.assertEqual(self.path.read_text(), changed)
        import fcntl
        with open(str(self.path) + ".lock", "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ValueError): self.run_setup()
        self.assertFalse(list(self.root.glob(".pane-policy-*")))

    def prompt(self, values=None, warning=False):
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stderr.isatty", return_value=True), patch("builtins.open", mock_open()), patch("os.isatty", return_value=True):
            with patch("getpass.getpass", side_effect=getpass.GetPassWarning("fixture") if warning else values):
                return hidden_secret()

    def test_hidden_input_failures_and_confirmation(self):
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(ValueError): hidden_secret()
        with self.assertRaises(ValueError): self.prompt(warning=True)
        with self.assertRaises(ValueError): self.prompt([SECRET, SECRET + "x"])
        with self.assertRaises(ValueError): self.prompt(["short", "short"])
        self.assertEqual(self.prompt([SECRET, SECRET]), HASH)

    def test_actual_tty_cli_prompts_do_not_echo_secret_or_hash(self):
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(dict(machine="fixture", projects=[dict(id="example", repository="github.com/example/fixture", roots=[str(self.root)])])))
        pid, fd = pty.fork()
        if pid == 0:
            script = "import os; from zudo_agent import console_policy as p; from zudo_agent.cli import main; original=p.git_directory; ancestors=" + repr(self.ancestors) + "; p.git_directory=lambda fd: False if (os.fstat(fd).st_dev,os.fstat(fd).st_ino) in ancestors else original(fd); main()"
            os.execv(sys.executable, [sys.executable, "-c", script, "--config", str(config_path), "console-policy", "create", "--path", str(self.path), "--identity", "fixture", "--project", "example"])
        output = b""; deadline = time.monotonic() + 10; stage = 0; reaped = False
        try:
            while time.monotonic() < deadline:
                if select.select([fd], [], [], .1)[0]:
                    try: chunk = os.read(fd, 4096)
                    except OSError: break
                    if not chunk: break
                    output += chunk
                    if stage == 0 and b"hidden): " in output:
                        os.write(fd, (SECRET + "\n").encode()); stage = 1
                    elif stage == 1 and b"Confirm secret (hidden): " in output:
                        os.write(fd, (SECRET + "\n").encode()); stage = 2
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    reaped = True
                    self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                    break
            self.assertEqual(stage, 2)
            self.assertTrue(self.path.exists())
            self.assertNotIn(SECRET.encode(), output); self.assertNotIn(HASH.encode(), output)
            self.assertEqual(load_policy(self.path, self.config)["password_sha256"], HASH)
        finally:
            if not reaped:
                done, _ = os.waitpid(pid, os.WNOHANG)
                if not done:
                    os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
            os.close(fd)
