"""Linux /proc or macOS libproc plus tmux metadata. Never capture-pane or argv."""

import os
import platform
import subprocess
import time
from pathlib import Path

from .model import digest


def command(args):
    return subprocess.run(args, text=True, capture_output=True, timeout=3, check=True).stdout.strip()


def process(pid):
    if platform.system() == "Darwin":
        from .native import mac_process
        return mac_process(pid)
    try:
        text = Path(f"/proc/{int(pid)}/stat").read_text()
        comm = text[text.index("(") + 1:text.rindex(")")]
        fields = text[text.rindex(")") + 2:].split()
        return dict(pid=int(pid), parent=int(fields[1]), start=fields[19], agent=comm if comm in {"claude", "codex"} else None)
    except (OSError, ValueError, IndexError):
        return None


def processes():
    if platform.system() == "Darwin":
        from .native import mac_processes
        return mac_processes()
    return {int(p.name): value for p in Path("/proc").iterdir()
            if p.name.isdigit() and (value := process(p.name))}


def agent_descendant(pid, table):
    # Only return an unambiguous nearest agent. Nested subagents do not replace their parent.
    frontier = [int(pid)]
    seen = set()
    children = {}
    for p in table.values():
        children.setdefault(p["parent"], []).append(p["pid"])
    for _ in range(64):
        matches = [table[p] for p in frontier if p in table and table[p]["agent"]]
        if matches:
            return matches[0] if len(matches) == 1 else None
        seen.update(frontier)
        frontier = [child for p in frontier for child in children.get(p, []) if child not in seen]
        if not frontier:
            break
    return None


def boot_id():
    if platform.system() == "Darwin":
        # libproc start times are absolute seconds + microseconds, not boot ticks.
        return "darwin-absolute-start"
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def run_identity(machine, boot, proc):
    return digest(machine, boot, proc["pid"], proc["start"])


def project_for(cwd, config):
    path = Path(cwd).resolve()
    candidates = [(len(root), pid) for pid, p in config["projects"].items() for root in p["roots"]
                  if path == Path(root) or Path(root) in path.parents]
    if candidates:
        return max(candidates)[1]
    # Worktrees share a common git directory. No remote names or URLs are collected.
    try:
        common = command(["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"])
        for pid, p in config["projects"].items():
            for root in p["roots"]:
                try:
                    if common == command(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"]):
                        return pid
                except (OSError, subprocess.SubprocessError):
                    continue
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def parse_panes(text):
    panes = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) != 7:
            raise ValueError("Invalid tmux metadata frame")
        server, session, window, pane, pid, dead, cwd = fields
        if not server.isdigit() or not pid.isdigit() or dead not in {"0", "1"}:
            raise ValueError("Invalid tmux metadata identifiers")
        panes.append(dict(server=server, session=session, window=window, pane=pane, pid=int(pid), dead=dead == "1", cwd=cwd))
    return panes


def discover(config, store, now=None):
    now = time.time() if now is None else now
    args = ["tmux"]
    if config["tmux_socket"]:
        args += ["-L", config["tmux_socket"]]
    args += ["list-panes", "-a", "-F", "#{pid}\t#{session_id}\t#{window_id}\t#{pane_id}\t#{pane_pid}\t#{pane_dead}\t#{pane_current_path}"]
    try:
        panes = parse_panes(command(args))
        table = processes()
        boot = boot_id()
        events, unmatched = [], 0
        for pane in panes:
            if pane["dead"]:
                continue
            project = project_for(pane["cwd"], config)
            if project is None:
                unmatched += 1
                continue
            proc = agent_descendant(pane["pid"], table)
            if proc is None:
                # A shell is not an agent run. Count it in inventory, do not invent a run.
                unmatched += 1
                continue
            events.append(dict(project_id=project, run_id=run_identity(config["machine"], boot, proc),
                               machine=config["machine"], source="tmux", kind="discovered", observed_at=now))
        store.discovery(events, True, len(panes), unmatched, now)
        return dict(status="connected", panes=len(panes), observed_runs=len(events), unmatched_panes=unmatched)
    except (OSError, subprocess.SubprocessError, ValueError):
        store.discovery([], False, 0, 0, now)
        return dict(status="disconnected", panes=0, observed_runs=0, unmatched_panes=0)


HOOKS = {"SessionStart": "session-start", "UserPromptSubmit": "working", "PreToolUse": "working",
         "PostToolUse": "working", "PermissionRequest": "permission-request", "Stop": "turn-stop",
         "SessionEnd": "session-end", "PostToolUseFailure": "error-observed", "StopFailure": "error-observed",
         "Interrupt": "interrupted"}


def hook_event(raw, provider, config, now=None):
    # Deliberately select fields; never serialize raw payload, tool inputs, or transcript paths.
    if provider not in {"claude", "codex"} or not isinstance(raw, dict):
        return None
    kind = HOOKS.get(raw.get("hook_event_name"))
    if kind is None or not isinstance(raw.get("session_id"), str) or not isinstance(raw.get("cwd"), str):
        return None
    # agent_id identifies a child; Claude --agent also sets agent_type on main sessions.
    if raw.get("agent_id"):
        return None
    project = project_for(raw["cwd"], config)
    if project is None:
        return None
    proc = process(os.getppid())
    for _ in range(64):
        if proc is None or proc["agent"]:
            break
        proc = process(proc["parent"])
    if proc is not None and not proc["agent"]:
        proc = None
    run = (run_identity(config["machine"], boot_id(), proc) if proc is not None
           else digest(config["machine"], provider, raw["session_id"]))
    return dict(project_id=project, run_id=run, machine=config["machine"], source=provider,
                kind=kind, observed_at=time.time() if now is None else now)
