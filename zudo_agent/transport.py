"""Strict private transport, persistent single-frame outbox and bounded retries."""
import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import sqlite3
import stat
import time
from pathlib import Path
from urllib.parse import urlsplit

from .model import digest, slug

MAX_BODY = 256 * 1024
MAX_RUNS = 500
NETWORKS = [ipaddress.ip_network(n) for n in ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "::1/128", "fc00::/7"]]


def private_address(address):
    try:
        ip = ipaddress.ip_address(address)
        return any(ip.version == net.version and ip in net for net in NETWORKS)
    except ValueError:
        return False


def destination(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Expected a private hub origin URL without credentials or path")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise ValueError("Invalid hub port")
    try:
        ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        ip = None
    if ip and not private_address(str(ip)):
        raise ValueError("Public/reserved hub addresses are not supported")
    if parsed.scheme == "http" and (ip is None or not ip.is_loopback):
        raise ValueError("Plain HTTP is restricted to literal loopback; use verified TLS or an existing encrypted tunnel")
    return parsed, port


def validate_transport(raw):
    if not isinstance(raw, dict) or set(raw) - {"hub_url", "stream", "token_file", "ca_file"} or not {"hub_url", "stream", "token_file"} <= set(raw):
        raise ValueError("Transport requires hub_url, stream, token_file and optional ca_file")
    destination(raw["hub_url"])
    slug(raw["stream"])
    for name in ["token_file", "ca_file"]:
        if name in raw and (not isinstance(raw[name], str) or not Path(raw[name]).is_absolute()):
            raise ValueError("Transport file references must be absolute")
    return dict(raw)


def read_token(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Token file cannot be a symlink")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 128:
            raise ValueError("Token file must be private and owned by the current user")
        value = os.read(fd, 129).decode("ascii").strip()
        if not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("Token must contain 32 bytes encoded as lowercase hex")
        return value
    finally:
        os.close(fd)


def send_frame(transport, body):
    if len(body) > MAX_BODY:
        raise ValueError("Frame too large")
    parsed, port = destination(transport["hub_url"])
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    if not addresses or any(not private_address(a[4][0]) for a in addresses):
        raise ValueError("Hub DNS resolved outside permitted private networks")
    # Pin the validated address. No second DNS lookup, redirects, proxy environment,
    # public fallback, or insecure TLS override can receive the token.
    address = addresses[0][4][0]
    context = ssl.create_default_context(cafile=transport.get("ca_file")) if parsed.scheme == "https" else None
    token = read_token(transport["token_file"])
    conn = http.client.HTTPConnection(parsed.hostname, port, timeout=5)
    sock = socket.create_connection((address, port), timeout=5)
    try:
        conn.sock = context.wrap_socket(sock, server_hostname=parsed.hostname) if context else sock
        conn.request("POST", "/api/ingest", body=body,
                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = conn.getresponse()
        data = response.read(4097)
        if response.status != 200 or len(data) > 4096:
            return {"ok": False, "status": "rejected" if response.status in {400, 401, 403, 409, 413} else "unreachable"}
        ack = json.loads(data)
        expected = json.loads(body)["sequence"]
        okay = ack == {"accepted": True, "sequence": expected}
        return dict(ok=okay, status="connected" if okay else "invalid-acknowledgment")
    finally:
        conn.close()
        sock.close()


def frame_from_snapshot(snapshot, config, sequence):
    runs = []
    for project in snapshot["projects"]:
        for run in project["runs"]:
            if run["machine"] != config["machine"] or run["source"] not in {"tmux", "claude", "codex"}:
                continue
            runs.append(dict(repository=project["repository"], run_id=run["id"], source=run["source"],
                             state=run["state"], state_at=run["state_at"], last_seen=run["last_seen"], reachability=run["reachability"]))
    runs.sort(key=lambda r: (-r["last_seen"], r["repository"], r["run_id"]))
    collector = next((c for c in snapshot["collectors"] if c["machine"] == config["machine"]), None)
    health = dict(status=collector["status"] if collector else "unknown", checked_at=collector["checked_at"] if collector else 0,
                  panes=collector["panes"] if collector else 0, unmatched_panes=collector["unmatched_panes"] if collector else 0)
    frame = dict(schema_version=1, machine=config["machine"], stream=config["transport"]["stream"], sequence=sequence,
                sampled_at=snapshot["generated_at"], collector=health, runs=runs[:MAX_RUNS], omitted_runs=max(0, len(runs)-MAX_RUNS))
    while len(json.dumps(frame, sort_keys=True, separators=(",", ":")).encode()) > MAX_BODY and frame["runs"]:
        frame["runs"].pop()
        frame["omitted_runs"] += 1
    return frame


class Forwarder:
    def __init__(self, store, config, sender=send_frame):
        self.store, self.config, self.sender = store, config, sender
        self.transport = validate_transport(config["transport"])
        self.key = digest(config["machine"], self.transport["hub_url"], self.transport["stream"])
        self.next_retry, self.failures = 0, 0
        store.db.execute("CREATE TABLE IF NOT EXISTS outbox (key TEXT PRIMARY KEY, sequence INTEGER, pending TEXT, status TEXT, acknowledged REAL)")
        store.db.commit()

    def pending(self):
        db = self.store.db
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT sequence,pending FROM outbox WHERE key=?", (self.key,)).fetchone()
            if row and row[1]:
                return row[1].encode()
            sequence = (row[0] if row else 0) + 1
            frame = frame_from_snapshot(self.store.snapshot(), self.config, sequence)
            body = json.dumps(frame, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if len(body.encode()) > MAX_BODY:
                raise ValueError("Frame too large; reduce registered project/run count")
            db.execute("INSERT INTO outbox VALUES (?,?,?,'pending',NULL) ON CONFLICT(key) DO UPDATE SET sequence=excluded.sequence,pending=excluded.pending,status='pending'",
                       (self.key, sequence, body))
            return body.encode()

    def cycle(self, now=None):
        now = time.time() if now is None else now
        if now < self.next_retry:
            return dict(status="retry-pending")
        try:
            body = self.pending()
        except (ValueError, sqlite3.Error):
            self.next_retry = now + 5
            return dict(status="local-frame-unavailable")
        try:
            result = self.sender(self.transport, body)
        except (OSError, ValueError, http.client.HTTPException):
            result = dict(ok=False, status="unreachable-or-untrusted")
        with self.store.db:
            if result["ok"]:
                self.store.db.execute("UPDATE outbox SET pending=NULL,status='connected',acknowledged=? WHERE key=? AND pending=?",
                                      (now, self.key, body.decode()))
                self.failures = 0
            else:
                self.store.db.execute("UPDATE outbox SET status=? WHERE key=?", (result["status"], self.key))
                self.failures = min(self.failures + 1, 5)
        self.next_retry = now + (min(60, 5 * 2 ** (self.failures-1)) if self.failures else 5)
        return dict(status=result["status"], pending=not result["ok"])
