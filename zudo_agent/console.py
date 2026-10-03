"""Opt-in human pane console. Never part of observation storage or forwarding.

This is a screen snapshot and deliberate text sender, not a PTY attachment.
All subprocess arguments are server-derived; user text is encoded as hex keys.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import threading
import time
from pathlib import Path

from . import collector
from .hub import exact, strict_json


class ConsoleError(ValueError):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def load_policy(path, config):
    p = Path(path)
    info = p.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 8192:
        raise ValueError("Console policy must be a private owner-only file")
    raw = strict_json(p.read_bytes())
    exact(raw, {"identity", "password_sha256", "projects", "allow_input"})
    if not isinstance(raw["identity"], str) or not re.fullmatch(r"[a-zA-Z0-9@._+-]{1,128}", raw["identity"]):
        raise ValueError("Invalid console identity")
    if not isinstance(raw["password_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", raw["password_sha256"]):
        raise ValueError("Operator-supplied strong password hash required")
    if (not isinstance(raw["projects"], list) or not raw["projects"] or
            any(not isinstance(p, str) or p not in config["projects"] for p in raw["projects"]) or
            type(raw["allow_input"]) is not bool):
        raise ValueError("Explicit project allowlist and input policy required")
    return raw


def bounded(args, limit=262144):
    # File output is deliberately never used. Bound both runtime and pipe memory.
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(3, process.kill)
    timer.start()
    try:
        output = process.stdout.read(limit + 1)
        if len(output) > limit:
            process.kill()
            raise ConsoleError(503, "Pane response exceeded the limit")
        if process.wait(timeout=4):
            raise ConsoleError(409, "Pane unavailable; reconnect explicitly")
        return output.decode("utf-8", errors="replace").strip()
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()


class PaneBackend:
    def __init__(self, config):
        self.config = config
        self.command = ["tmux"] + (["-L", config["tmux_socket"]] if config["tmux_socket"] else [])

    def targets(self):
        text = bounded(self.command + ["list-panes", "-a", "-F",
            "#{pid}\t#{session_id}\t#{window_id}\t#{pane_id}\t#{pane_pid}\t#{pane_dead}\t#{pane_current_path}"])
        table, boot = collector.processes(), collector.boot_id()
        targets = []
        for pane in collector.parse_panes(text):
            if pane["dead"] or not re.fullmatch(r"%[0-9]+", pane["pane"]):
                continue
            project = collector.project_for(pane["cwd"], self.config)
            agent = collector.agent_descendant(pane["pid"], table)
            server, root = table.get(int(pane["server"])), table.get(pane["pid"])
            if project is None or not agent or not server or not root:
                continue
            targets.append(dict(project=project, run=collector.run_identity(self.config["machine"], boot, agent),
                machine=self.config["machine"], boot=boot, server=(server["pid"], server["start"]),
                pane=pane["pane"], root=(root["pid"], root["start"]), agent=(agent["pid"], agent["start"])))
        return targets

    def validate(self, target):
        if target not in self.targets():
            raise ConsoleError(409, "Target changed or disappeared; reconnect explicitly")

    def screen(self, target):
        self.validate(target)
        screen = bounded(self.command + ["capture-pane", "-p", "-t", target["pane"], "-S", "0", "-E", "100"])
        self.validate(target)
        # Plain text only, no ANSI interpretation, clipboard/link escape handlers.
        return "".join(c for c in screen if c in "\n\t" or (ord(c) >= 32 and ord(c) != 127))

    def send(self, target, text, enter):
        self.validate(target)
        # The conditional and send execute together in tmux's command queue.
        # No renderer-supplied target or shell command is interpolated here.
        pane = target["pane"]
        keys = " ".join(f"{byte:02x}" for byte in text.encode("utf-8"))
        commands = f"send-keys -t {pane} -H {keys}"
        if enter:
            commands += f" ; send-keys -t {pane} Enter"
        condition = "#{&&:#{==:#{pid},%s},#{==:#{pane_pid},%s}}" % (target["server"][0], target["root"][0])
        result = bounded(self.command + ["if-shell", "-F", "-t", pane, condition,
            commands + " ; display-message -p delivered", "display-message -p stale"])
        if result != "delivered":
            raise ConsoleError(409, "Target changed; input was not sent")
        self.validate(target)


class Console:
    def __init__(self, config, policy, backend=None, clock=time.monotonic):
        self.policy, self.clock = policy, clock
        self.backend = backend or PaneBackend(config)
        self.machine = config["machine"]
        self.csrf = secrets.token_urlsafe(32)
        self.leases = {}
        self.lock = threading.Lock()

    def authorized(self, headers):
        values = headers.get_all("Authorization", [])
        if len(values) != 1 or not values[0].startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(values[0][6:], validate=True).decode("utf-8")
            identity, password = raw.split(":", 1)
        except (ValueError, UnicodeError):
            return False
        return (hmac.compare_digest(identity, self.policy["identity"]) and
                hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), self.policy["password_sha256"]))

    def handle(self, action, body):
        # No queues: an overlapping request is rejected rather than delayed/replayed.
        if not self.lock.acquire(blocking=False):
            raise ConsoleError(429, "Console busy; input is never automatically retried")
        try:
            now = self.clock()
            self.leases = {key: lease for key, lease in self.leases.items() if lease["expires"] > now}
            if action == "open":
                exact(body, {"project", "run", "machine"})
                if body["project"] not in self.policy["projects"] or body["machine"] != self.machine:
                    raise ConsoleError(403, "Target is not allowed on this machine")
                matches = [t for t in self.backend.targets() if all(t[k] == body[k] for k in body)]
                if len(matches) != 1:
                    raise ConsoleError(409, "Current run is unavailable or ambiguous")
                if len(self.leases) >= 8:
                    raise ConsoleError(429, "Too many open consoles")
                lease = secrets.token_urlsafe(32)
                self.leases[lease] = dict(target=matches[0], expires=now + 120, sequence=0)
                return dict(lease=lease, expires_in=120, allow_input=self.policy["allow_input"], sequence=1)
            if action not in {"screen", "send", "close"}:
                raise ConsoleError(404, "Unknown console operation")
            exact(body, {"lease", "sequence", "text", "enter"} if action == "send" else {"lease"})
            lease = self.leases.get(body["lease"])
            if lease is None:
                raise ConsoleError(409, "Console expired; reconnect explicitly")
            if action == "close":
                self.leases.pop(body["lease"])
                return dict(closed=True)
            if action == "screen":
                return dict(screen=self.backend.screen(lease["target"]), sampled_at=time.time())
            if not self.policy["allow_input"]:
                raise ConsoleError(403, "Input is disabled by server policy")
            if type(body["sequence"]) is not int or body["sequence"] != lease["sequence"] + 1:
                raise ConsoleError(409, "Duplicate or out-of-order input rejected")
            text = body["text"]
            if (not isinstance(text, str) or not text or len(text.encode("utf-8")) > 4096 or
                    any(ord(c) < 32 or ord(c) == 127 for c in text) or type(body["enter"]) is not bool):
                raise ConsoleError(400, "Send a single line of at most 4096 UTF-8 bytes")
            # Consume before dispatch. Lost responses never permit resending this input.
            lease["sequence"] += 1
            try:
                self.backend.send(lease["target"], text, body["enter"])
            except Exception:
                self.leases.pop(body["lease"], None)
                raise ConsoleError(409, "Delivery uncertain; input will not be retried. Inspect the pane before reconnecting") from None
            return dict(delivery="sent-to-pane", sequence=lease["sequence"] + 1)
        finally:
            self.lock.release()
