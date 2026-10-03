"""Previewed, owned project-local setup. No agent control or implicit installation."""

import argparse
import base64
import copy
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path

from .model import load_config, slug

LIMIT = 2_000_000
BASELINES = {"claude": ((2, 1, 288), (2, 2, 0)), "codex": ((0, 159, 3), (0, 160, 0))}
EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest", "Stop", "SessionEnd"]


class SetupError(ValueError):
    pass


def encoded(data):
    return None if data is None else base64.b64encode(data).decode("ascii")


def decoded(data):
    return None if data is None else base64.b64decode(data, validate=True)


def serial(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SetupError("Duplicate JSON keys; repair the file before setup")
        result[key] = value
    return result


def parse(data):
    try:
        obj = json.loads(data, object_pairs_hook=unique_object,
                         parse_constant=lambda _s: (_ for _ in ()).throw(SetupError("Non-finite JSON value")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SetupError("Malformed JSON; no file was changed") from exc
    if not isinstance(obj, dict):
        raise SetupError("Configuration must be a JSON object")
    return obj


def safe_path(value):
    path = Path(os.path.abspath(os.path.expanduser(str(value))))
    for node in [path, *path.parents]:
        if node.is_symlink():
            raise SetupError(f"Refusing symlink path: {node}")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise SetupError(f"Expected a regular unshared file: {path}")
    return path


def read(path):
    path = safe_path(path)
    if not path.exists():
        return None
    if path.stat().st_size > LIMIT:
        raise SetupError("Local setup input exceeds 2 MB")
    return path.read_bytes()


def atomic(path, content, expected):
    path = safe_path(path)
    if read(path) != expected:
        raise SetupError(f"File changed since preview; re-plan: {path}")
    if content is None:
        if expected is not None:
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=".zudo-setup-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path)
        if read(path) != expected:
            raise SetupError(f"Concurrent edit; re-plan: {path}")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def version(binary, flag, provider):
    resolved = shutil.which(binary)
    if not resolved:
        return dict(binary=None, version=None, supported=False, reason="not-found")
    resolved = str(Path(resolved).absolute())
    try:
        result = subprocess.run([resolved, flag], capture_output=True, text=True, timeout=5, check=True)
        patterns = {"claude": r"^(\d+)\.(\d+)\.(\d+) \(Claude Code\)\s*$",
                    "codex": r"^codex-cli (\d+)\.(\d+)\.(\d+)\s*$",
                    "tmux": r"^tmux (\d+)\.(\d+)([a-z]?)\s*$"}
        match = re.fullmatch(patterns[provider], result.stdout.strip())
        if not match:
            raise ValueError("Unknown version output")
        if provider == "tmux":
            parts = (int(match[1]), int(match[2]))
            supported = (3, 4) <= parts < (4, 0)
            number = f"{match[1]}.{match[2]}{match[3]}"
        else:
            parts = tuple(map(int, match.groups()))
            low, high = BASELINES[provider]
            supported = low <= parts < high
            number = ".".join(map(str, parts))
        return dict(binary=resolved, version=number, supported=supported,
                    reason="tested-range" if supported else "outside-tested-range")
    except (OSError, ValueError, subprocess.SubprocessError):
        return dict(binary=resolved, version=None, supported=False, reason="unknown-version")


def doctor(binaries=None):
    binaries = binaries or {}
    system = platform.system()
    linux = system == "Linux" and Path("/proc/self/stat").exists()
    mac = False
    if system == "Darwin":
        try:
            from .native import library
            library()
            mac = True
        except OSError:
            pass
    return dict(os=system, wsl=linux and "microsoft" in platform.release().lower(),
                supported=(linux or mac) and sys.version_info >= (3, 11), python=platform.python_version(),
                collector="linux-proc" if linux else "darwin-libproc" if mac else "unsupported-native-process-collector",
                tmux=version(binaries.get("tmux", "tmux"), "-V", "tmux"),
                agents={p: version(binaries.get(p, p), "--version", p) for p in BASELINES},
                cloud="import-only", multi_host="explicit-authenticated-hub")


def require_supported(report, providers):
    if not report["supported"]:
        raise SetupError("Setup requires Linux/WSL or macOS with libproc, and Python 3.11+")
    if not report["tmux"]["supported"]:
        raise SetupError("tmux is missing or outside the tested 3.4..3.x range")
    for provider in providers:
        if not report["agents"][provider]["supported"]:
            raise SetupError(f"{provider} is missing or outside the tested setup range; run doctor")


def hook_document(data):
    obj = {} if data is None else parse(data)
    if not isinstance(obj.get("hooks", {}), dict):
        raise SetupError("hooks must be an object; existing settings were not repaired or replaced")
    for groups in obj.get("hooks", {}).values():
        if not isinstance(groups, list):
            raise SetupError("Each hook event must contain a list")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise SetupError("Malformed existing hook group")
            for handler in group["hooks"]:
                if not isinstance(handler, dict) or not isinstance(handler.get("type"), str):
                    raise SetupError("Malformed existing hook handler")
                if handler["type"] == "command" and not isinstance(handler.get("command"), str):
                    raise SetupError("Existing command hook requires a string")
    if obj.get("disableAllHooks") or obj.get("allowManagedHooksOnly"):
        raise SetupError("Project settings disable local hooks; setup will not override policy")
    return obj


def handlers(command, provider):
    extra = ["PostToolUseFailure", "StopFailure"] if provider == "claude" else ["Interrupt"]
    return {event: {"type": "command", "command": command, "timeout": 3} for event in EVENTS + extra}


def merge_hooks(before, desired, owner):
    obj = hook_document(before)
    result = copy.deepcopy(obj)
    hooks = result.setdefault("hooks", {})
    for groups in hooks.values():
        for group in groups:
            for handler in group["hooks"]:
                command = handler.get("command", "")
                if not isinstance(command, str) or owner in command:
                    continue
                try:
                    argv = shlex.split(command)
                except ValueError:
                    continue  # Preserve opaque user shell syntax; never execute it.
                dashboard = any(Path(arg).name in {"zudo-agent", "hook_runner.py"} or arg == "zudo_agent" for arg in argv)
                if dashboard and "hook" in argv:
                    raise SetupError("An unowned dashboard hook already exists; review it instead of adding duplicate observers")
    added = {}
    for event, handler in desired.items():
        groups = hooks.setdefault(event, [])
        owned = [(g, h) for g in groups for h in g["hooks"] if owner in h.get("command", "")]
        if owned:
            if len(owned) != 1 or owned[0][1] != handler or set(owned[0][0]) != {"hooks"}:
                raise SetupError("An owned hook was edited or duplicated; resolve it before setup")
            continue
        groups.append({"hooks": [handler]})
        added[event] = handler
    return result, added


def change(path, before, after, kind, owned):
    return dict(path=str(safe_path(path)), before=encoded(before), after=encoded(after), kind=kind, owned=owned)


def build_plan(*, project, project_id, repository, machine, config, db, providers, binaries=None, transport=None):
    project = Path(project).expanduser().resolve()
    if not project.is_dir() or not (project / ".git").exists() or project == Path.home().resolve():
        raise SetupError("Select an existing checkout root (.git directory or worktree file), not your home")
    project_id, machine = slug(project_id), slug(machine)
    if not providers or any(p not in BASELINES for p in providers) or len(set(providers)) != len(providers):
        raise SetupError("Choose claude, codex, or both once")
    report = doctor(binaries)
    require_supported(report, providers)
    config, db = safe_path(config), safe_path(db)
    if config == db:
        raise SetupError("Configuration and database paths must differ")
    before = read(config)
    if before is not None:
        current = parse(before)
        load_config(config)
        if current["machine"] != machine:
            raise SetupError("Existing machine ID differs; do not silently merge machines")
    else:
        current = dict(machine=machine, projects=[])
    updated = copy.deepcopy(current)
    if transport is not None:
        from .transport import validate_transport
        updated["transport"] = validate_transport(transport)
    original = next((p for p in current["projects"] if p["id"] == project_id), None)
    if any(p["id"] != project_id and any(Path(root).resolve() == project for root in p["roots"]) for p in current["projects"]):
        raise SetupError("Checkout root already registered under another project ID")
    same_repo = next((p for p in current["projects"] if p["repository"].lower() == repository.lower()), None)
    if same_repo is not None and same_repo["id"] != project_id:
        raise SetupError("Repository already registered under another project ID")
    if original is not None and original["repository"] != repository:
        raise SetupError("Project ID already belongs to a different repository")
    entry = next((p for p in updated["projects"] if p["id"] == project_id), None)
    if entry is None:
        entry = dict(id=project_id, repository=repository, roots=[str(project)])
        updated["projects"].append(entry)
    elif str(project) not in entry["roots"]:
        entry["roots"].append(str(project))
    # Reuse the exact runtime validator without writing into the target configuration.
    with tempfile.TemporaryDirectory(prefix="zudo-config-check-") as temporary:
        candidate = Path(temporary) / "config.json"
        candidate.write_bytes(serial(updated))
        load_config(candidate)
    changes = []
    if current != updated or before is None:
        changes.append(change(config, before, serial(updated), "config", dict(original=original, installed=entry,
                              transport_changed=current.get("transport") != updated.get("transport"))))
    runner = str(Path(__file__).with_name("hook_runner.py").resolve())
    python = str(Path(sys.executable).resolve())
    owner = fingerprint(dict(project=str(project), config=str(config), db=str(db), machine=machine))
    commands = {}
    guards = [dict(path=str(config), content=encoded(before))]
    for provider in providers:
        if provider == "codex":
            toml_path = safe_path(project / ".codex/config.toml")
            toml_bytes = read(toml_path)
            guards.append(dict(path=str(toml_path), content=encoded(toml_bytes)))
            if toml_bytes is not None:
                try:
                    cfg = tomllib.loads(toml_bytes.decode())
                except (ValueError, UnicodeError) as exc:
                    raise SetupError("Malformed project Codex TOML; repair before setup") from exc
                features = cfg.get("features", {})
                if not isinstance(features, dict):
                    raise SetupError("Project Codex features must be a TOML table")
                if any(features.get(key) is False for key in ["hooks", "codex_hooks"]):
                    raise SetupError("Project Codex hooks are disabled; setup will not override policy")
                if runner in toml_bytes.decode():
                    raise SetupError("Dashboard hook already referenced in inline TOML; resolve duplicate sources manually")
        target = safe_path(project / (".claude/settings.local.json" if provider == "claude" else ".codex/hooks.json"))
        if target in {config, db}:
            raise SetupError("Hook settings, dashboard config and database must use distinct paths")
        command = shlex.join([python, runner, "--owner", owner, "--config", str(config), "--db", str(db), "hook", provider])
        commands[provider] = command
        existing = read(target)
        guards.append(dict(path=str(target), content=encoded(existing)))
        merged, added = merge_hooks(existing, handlers(command, provider), owner)
        if added:
            changes.append(change(target, existing, serial(merged), "hooks", added))
    plan = dict(schema_version=1, operation="setup", project=str(project), providers=providers,
                selection=dict(machine=machine, project_id=project_id, repository=repository, config=str(config), db=str(db), transport=updated.get("transport")),
                environment=report, commands=commands, changes=changes, guards=guards,
                warnings=["Agent trust and global/admin policies are not changed or proven by setup.",
                          "Transport requires separately provisioned credentials and secure connectivity; setup never pairs devices.",
                          "Cloud imports only; hub must remain online to receive new observations."])
    return seal(plan)


def seal(plan):
    return dict(plan, approval=fingerprint(plan))


def hub_plan(registry_path, candidate_path):
    from .hub import validate_registry
    if platform.system() not in {"Linux", "Darwin"}:
        raise SetupError("Hub setup supports Linux/WSL and macOS")
    target = safe_path(registry_path)
    candidate_path = safe_path(candidate_path)
    if target == candidate_path:
        raise SetupError("Candidate must be separate from installed registry")
    candidate_bytes = read(candidate_path)
    candidate = parse(candidate_bytes)
    parsed = validate_registry(candidate)
    before = read(target)
    previous = validate_registry(parse(before)) if before is not None else None
    changes = [] if before is not None and parse(before) == candidate else [change(target, before, serial(candidate), "hub", {})]
    return seal(dict(schema_version=1, operation="hub-setup", changes=changes,
                     selection=dict(registry=str(target), devices=[dict(machine=d["machine"], stream=d["stream"], repositories=d["repositories"]) for d in candidate["devices"]],
                                    projects=candidate["projects"], allowed_hosts=candidate["allowed_hosts"],
                                    removed_devices=sorted(set(previous["devices"] if previous else {}) - set(parsed["devices"]))),
                     guards=[dict(path=str(candidate_path), content=encoded(candidate_bytes))],
                     warnings=["Review the full local candidate, including credential hashes and removed devices.",
                               "This registers devices only. No token/certificate generation, pairing, listener or network configuration occurs."]))


def verify_plan(plan, approval):
    actual = dict(plan)
    token = actual.pop("approval", None)
    if token != fingerprint(actual) or approval != token:
        raise SetupError("Approval token does not match this exact preview")
    if plan.get("schema_version") != 1 or plan.get("operation") not in {"setup", "hub-setup", "rollback"}:
        raise SetupError("Unknown setup plan format")
    return plan


@contextmanager
def lock(directory):
    import fcntl
    directory = Path(directory)
    path = safe_path(directory / ".setup.lock")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise SetupError("Another setup operation is active") from exc
    finally:
        os.close(fd)


def apply_plan(plan, approval, receipts):
    verify_plan(plan, approval)
    if plan["operation"] == "setup":
        env = plan["environment"]
        bins = {p: env["agents"][p]["binary"] for p in plan["providers"]}
        bins["tmux"] = env["tmux"]["binary"]
        fresh = doctor(bins)
        require_supported(fresh, plan["providers"])
        if any(fresh["agents"][p] != env["agents"][p] for p in plan["providers"]) or fresh["tmux"] != env["tmux"]:
            raise SetupError("Tool versions changed since preview; re-plan")
    receipts = Path(receipts).absolute()
    with lock(receipts):
        receipt_path = safe_path(receipts / f"{approval}.receipt.json")
        if read(receipt_path) is not None:
            prior = parse(read(receipt_path))
            if prior.get("status") == "applied" and all(read(c["path"]) == decoded(c["after"]) for c in plan["changes"]):
                return dict(status="already-applied", receipt=str(receipt_path))
            raise SetupError("Receipt already exists; inspect or roll it back instead of replaying")
        for guard in plan.get("guards", []):
            if read(guard["path"]) != decoded(guard["content"]):
                raise SetupError("Related policy/config changed since preview; re-plan")
        for item in plan["changes"]:
            if read(item["path"]) != decoded(item["before"]):
                raise SetupError(f"File changed since preview; re-plan: {item['path']}")
        if not plan["changes"]:
            return dict(status="no-changes", receipt=None, note="Keep any original installation receipt for rollback")
        receipt = dict(schema_version=1, status="prepared", plan=plan)
        atomic(receipt_path, serial(receipt), None)
        # This is an atomic replace per file, not a cross-file filesystem transaction.
        # The prepared receipt survives interruption and supports conservative recovery.
        try:
            for item in plan["changes"]:
                atomic(item["path"], decoded(item["after"]), decoded(item["before"]))
        except Exception as exc:
            raise SetupError(f"Apply interrupted; recover with rollback-plan --receipt {receipt_path}") from exc
        before_receipt = read(receipt_path)
        receipt["status"] = "applied"
        atomic(receipt_path, serial(receipt), before_receipt)
        return dict(status="applied", receipt=str(receipt_path), actual_agent_delivery="not-verified")


def undo_hooks(current, added):
    obj = hook_document(current)
    conflicts = []
    for event, handler in added.items():
        groups = obj.get("hooks", {}).get(event, [])
        matches = [(group, h) for group in groups for h in group["hooks"] if h.get("command") == handler["command"]]
        if not matches:
            # A changed command might still carry the owner marker: leave it untouched.
            owner = shlex.split(handler["command"])[3]
            if any(owner in h.get("command", "") for g in groups for h in g["hooks"]):
                conflicts.append(f"{event}: owned command edited; retained")
            continue
        if len(matches) != 1 or matches[0][1] != handler or set(matches[0][0]) != {"hooks"}:
            conflicts.append(f"{event}: owned handler or matcher edited/duplicated; retained")
            continue
        group, h = matches[0]
        group["hooks"].remove(h)
        if not group["hooks"]:
            groups.remove(group)
        if not groups:
            obj["hooks"].pop(event, None)
    return obj, conflicts


def undo_config(current, owned):
    obj = parse(current)
    if owned.get("transport_changed"):
        return obj, ["Transport-enabled configuration was edited later; retained to avoid disconnecting other projects"]
    installed, original = owned["installed"], owned["original"]
    if not isinstance(obj.get("projects"), list):
        raise SetupError("Dashboard configuration changed shape; retain for manual review")
    entries = [p for p in obj["projects"] if isinstance(p, dict) and p.get("id") == installed["id"]]
    if not entries:
        return obj, []
    if len(entries) != 1:
        return obj, ["Project identity duplicated; retained"]
    entry = entries[0]
    if original is None:
        if entry == installed:
            obj["projects"].remove(entry)
            return obj, []
        return obj, ["Newly registered project was edited later; retained"]
    if entry.get("repository") != installed["repository"] or not isinstance(entry.get("roots"), list):
        return obj, ["Project identity/roots edited; retained"]
    for root in installed["roots"]:
        if root not in original["roots"] and root in entry["roots"]:
            entry["roots"].remove(root)
    return obj, []


def rollback_plan(receipt_path):
    receipt_path = safe_path(receipt_path)
    receipt_bytes = read(receipt_path)
    receipt = parse(receipt_bytes)
    source = receipt["plan"]
    verify_plan(source, source["approval"])
    if source["operation"] not in {"setup", "hub-setup"}:
        raise SetupError("Rollback only accepts an original setup receipt")
    changes, conflicts = [], []
    for item in reversed(source["changes"]):
        if item["kind"] == "config" and conflicts:
            conflicts.append(f"{item['path']}: dashboard config retained while hook conflicts need review")
            continue
        current = read(item["path"])
        before, after = decoded(item["before"]), decoded(item["after"])
        if current is None or current == before:
            continue
        if current == after:
            desired = before
        elif item["kind"] == "hub":
            conflicts.append(f"{item['path']}: registry edited later; retained for review")
            continue
        else:
            try:
                obj, warnings = (undo_hooks(current, item["owned"]) if item["kind"] == "hooks" else undo_config(current, item["owned"]))
                conflicts.extend(f"{item['path']}: {w}" for w in warnings)
                desired = serial(obj) if obj != parse(current) else current
            except (SetupError, TypeError, KeyError, ValueError):
                conflicts.append(f"{item['path']}: changed/malformed file retained for manual review")
                continue
        if desired != current:
            changes.append(change(item["path"], current, desired, item["kind"], {"undo": item["owned"]}))
    return seal(dict(schema_version=1, operation="rollback", receipt=str(receipt_path), changes=changes,
                     selection=source.get("selection"),
                     guards=[dict(path=str(receipt_path), content=encoded(receipt_bytes))], warnings=conflicts))


def synthetic_verify(provider):
    if provider not in BASELINES:
        raise SetupError("Unsupported provider")
    # This does not load installed settings, invoke user hooks, or touch the live DB.
    from .store import Store
    with tempfile.TemporaryDirectory(prefix="zudo-synthetic-only-") as temporary:
        root = Path(temporary)
        config = root / "config.json"
        db = root / "synthetic.sqlite3"
        config.write_bytes(serial(dict(machine="synthetic-only", projects=[dict(id="synthetic-only", repository="example.invalid/synthetic/verification", roots=[str(root)])])))
        payload = dict(session_id="synthetic-only", cwd=str(root), hook_event_name="Stop", prompt="SYNTHETIC-NOT-REAL")
        runner = Path(__file__).with_name("hook_runner.py")
        result = subprocess.run([sys.executable, str(runner), "--owner", "a" * 64, "--config", str(config), "--db", str(db), "hook", provider],
                                input=json.dumps(payload), capture_output=True, text=True, timeout=10)
        store = Store(db, load_config(config))
        try:
            snap = store.snapshot()
        finally:
            store.close()
        runs = snap["projects"][0]["runs"]
        passed = result.returncode == 0 and not result.stdout and not result.stderr and len(runs) == 1 and runs[0]["state"] == "idle"
        if not passed:
            raise SetupError("Synthetic runner verification failed; no actual hook delivery was tested")
    return dict(mode="synthetic-only", passed=True, storage="temporary-deleted", actual_agent_delivery="not-verified", live_database_touched=False)


def preview_summary(plan):
    return dict(operation=plan["operation"], approval=plan["approval"],
                selection=plan.get("selection"),
                changes=[dict(path=c["path"], action="remove" if c["after"] is None else "create" if c["before"] is None else "update", kind=c["kind"], owned=c["owned"]) for c in plan["changes"]],
                warnings=plan["warnings"])


def save_plan(plan, output):
    # Plans/receipts may contain existing local settings. Never publish them.
    if read(output) is not None:
        raise SetupError("Preview file already exists; choose a new --out path")
    atomic(output, serial(plan), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("doctor")
    prepare = sub.add_parser("plan")
    for command_parser in [check, prepare]:
        for name in ["claude", "codex", "tmux"]:
            command_parser.add_argument(f"--{name}-bin", default=name)
    prepare.add_argument("--project", required=True)
    prepare.add_argument("--project-id", required=True)
    prepare.add_argument("--repository", required=True)
    prepare.add_argument("--machine", required=True)
    prepare.add_argument("--provider", choices=["claude", "codex", "both"], required=True)
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "zudo-agent"
    prepare.add_argument("--config", default=str(state / "config.json"))
    prepare.add_argument("--db", default=str(state / "observations.sqlite3"))
    prepare.add_argument("--out", required=True)
    prepare.add_argument("--hub-url")
    prepare.add_argument("--stream")
    prepare.add_argument("--token-file")
    prepare.add_argument("--ca-file")
    hub_prepare = sub.add_parser("hub-plan", help="Preview a user-authored hub registration candidate; never generate credentials")
    hub_prepare.add_argument("--registry", required=True)
    hub_prepare.add_argument("--candidate", required=True)
    hub_prepare.add_argument("--out", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--approve", required=True, help="Exact SHA printed by the reviewed preview")
    apply.add_argument("--receipts", default=str(state / "setup-receipts"))
    rollback = sub.add_parser("rollback-plan", aliases=["uninstall-plan"])
    rollback.add_argument("--receipt", required=True)
    rollback.add_argument("--out", required=True)
    verify = sub.add_parser("verify-synthetic")
    verify.add_argument("--provider", required=True, choices=["claude", "codex"])
    args = parser.parse_args()
    try:
        if args.action == "doctor":
            result = doctor({p: getattr(args, p + "_bin") for p in ["claude", "codex", "tmux"]})
        elif args.action == "plan":
            transport = None
            if any([args.hub_url, args.stream, args.token_file, args.ca_file]):
                if not all([args.hub_url, args.stream, args.token_file]):
                    raise SetupError("Hub forwarding requires --hub-url, --stream and --token-file together")
                transport = dict(hub_url=args.hub_url, stream=args.stream, token_file=args.token_file)
                if args.ca_file:
                    transport["ca_file"] = args.ca_file
            plan = build_plan(project=args.project, project_id=args.project_id, repository=args.repository,
                              machine=args.machine, config=args.config, db=args.db,
                              providers=["claude", "codex"] if args.provider == "both" else [args.provider],
                              binaries={p: getattr(args, p + "_bin") for p in ["claude", "codex", "tmux"]}, transport=transport)
            output = safe_path(args.out)
            if any(str(output) == c["path"] for c in plan["changes"]) or output == safe_path(args.db):
                raise SetupError("Preview output must not be a settings file or the database")
            save_plan(plan, output)
            result = dict(preview_summary(plan), plan_file=str(output), environment=plan["environment"], setup_performed=False)
        elif args.action == "hub-plan":
            plan = hub_plan(args.registry, args.candidate)
            if safe_path(args.out) in {safe_path(args.registry), safe_path(args.candidate)}:
                raise SetupError("Preview output must be separate from registry and candidate")
            save_plan(plan, args.out)
            result = dict(preview_summary(plan), plan_file=str(safe_path(args.out)), setup_performed=False)
        elif args.action == "apply":
            result = apply_plan(parse(read(args.plan)), args.approve, args.receipts)
        elif args.action in {"rollback-plan", "uninstall-plan"}:
            plan = rollback_plan(args.receipt)
            save_plan(plan, args.out)
            result = dict(preview_summary(plan), plan_file=str(safe_path(args.out)), setup_performed=False)
        else:
            result = synthetic_verify(args.provider)
        print(json.dumps(result, indent=2))
    except (SetupError, OSError, ValueError, KeyError, TypeError) as exc:
        # No input JSON or arbitrary version-command output is echoed.
        message = str(exc) if isinstance(exc, SetupError) else type(exc).__name__
        print(f"Setup stopped: {message}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
