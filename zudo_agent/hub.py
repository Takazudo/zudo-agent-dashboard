"""Authenticated observation-only hub. Private binding, strict bounded snapshots."""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import ssl
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .model import DIGEST, REPO, digest, slug, timestamp
from .transport import MAX_BODY, MAX_RUNS, private_address
from .store import Store
from .workflow import Workflow, known_runs, LANES


class Rejected(ValueError):
    pass


def exact(obj, keys):
    if not isinstance(obj, dict) or set(obj) != set(keys):
        raise Rejected("Unexpected fields")


def integer(value, maximum=100_000):
    if type(value) is not int or not 0 <= value <= maximum:
        raise Rejected("Invalid integer")
    return value


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Rejected("Duplicate fields")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def validate_registry(raw):
    exact(raw, {"projects", "devices", "viewer_sha256", "allowed_hosts", "stale_after", "offline_after"})
    if not isinstance(raw["projects"], list) or not 1 <= len(raw["projects"]) <= 500:
        raise Rejected("Register 1..500 projects")
    projects, repositories = {}, set()
    for p in raw["projects"]:
        exact(p, {"id", "repository"})
        pid, repo = slug(p["id"]), p["repository"]
        if not isinstance(repo, str) or len(repo) > 256 or not REPO.fullmatch(repo) or ".." in repo:
            raise Rejected("Invalid repository identity")
        if pid in projects or repo.lower() in repositories:
            raise Rejected("Duplicate project or repository")
        projects[pid] = dict(id=pid, repository=repo)
        repositories.add(repo.lower())
    if not isinstance(raw["devices"], list) or not 1 <= len(raw["devices"]) <= 128:
        raise Rejected("Register 1..128 devices")
    hashes = {raw["viewer_sha256"]}
    if not isinstance(raw["viewer_sha256"], str) or not DIGEST.fullmatch(raw["viewer_sha256"]):
        raise Rejected("Viewer token hash required")
    devices = {}
    for d in raw["devices"]:
        exact(d, {"machine", "stream", "token_sha256", "repositories"})
        name, stream = slug(d["machine"]), slug(d["stream"])
        if name in devices or not isinstance(d["token_sha256"], str) or not DIGEST.fullmatch(d["token_sha256"]) or d["token_sha256"] in hashes:
            raise Rejected("Device identities and token hashes must be unique")
        if not isinstance(d["repositories"], list) or not d["repositories"] or any(not isinstance(r, str) or r.lower() not in repositories for r in d["repositories"]):
            raise Rejected("Device repository allowlist must use registered repositories")
        devices[name] = dict(d, stream=stream)
        hashes.add(d["token_sha256"])
    if not isinstance(raw["allowed_hosts"], list) or not raw["allowed_hosts"] or len(raw["allowed_hosts"]) > 32 or any(not isinstance(h, str) or not re.fullmatch(r"[a-z0-9.-]{1,253}", h) for h in raw["allowed_hosts"]):
        raise Rejected("Explicit lowercase hostnames/IPv4 addresses required")
    stale, offline = integer(raw["stale_after"], 3600), integer(raw["offline_after"], 86400)
    if not 10 <= stale < offline:
        raise Rejected("Require 10 <= stale_after < offline_after")
    return dict(raw, projects=projects, devices=devices)


def load_registry(path):
    if Path(path).stat().st_size > 1_000_000:
        raise Rejected("Registry too large")
    return validate_registry(strict_json(Path(path).read_bytes()))


def validate_frame(raw, machine, registry):
    exact(raw, {"schema_version", "machine", "stream", "sequence", "sampled_at", "collector", "runs", "omitted_runs"})
    device = registry["devices"][machine]
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or raw["machine"] != machine or raw["stream"] != device["stream"]:
        raise Rejected("Machine or installation identity mismatch")
    if not 1 <= integer(raw["sequence"], 2**53-1):
        raise Rejected("Invalid sequence")
    sampled = timestamp(raw["sampled_at"])
    integer(raw["omitted_runs"], 10_000_000)
    health = raw["collector"]
    exact(health, {"status", "checked_at", "panes", "unmatched_panes"})
    if health["status"] not in {"connected", "disconnected", "stale", "unknown"}:
        raise Rejected("Invalid collector health")
    if timestamp(health["checked_at"]) > sampled + 60:
        raise Rejected("Collector timestamp ahead of frame")
    if integer(health["unmatched_panes"]) > integer(health["panes"]):
        raise Rejected("Invalid inventory")
    if not isinstance(raw["runs"], list) or len(raw["runs"]) > MAX_RUNS:
        raise Rejected("Too many runs")
    seen = set()
    allow = {repo.lower() for repo in device["repositories"]}
    for run in raw["runs"]:
        exact(run, {"repository", "run_id", "source", "state", "state_at", "last_seen", "reachability"})
        if not isinstance(run["repository"], str) or run["repository"].lower() not in allow:
            raise Rejected("Unregistered device repository")
        if not isinstance(run["run_id"], str) or not DIGEST.fullmatch(run["run_id"]):
            raise Rejected("Invalid run identity")
        key = (run["repository"].lower(), run["run_id"])
        if key in seen:
            raise Rejected("Duplicate run in snapshot")
        seen.add(key)
        if run["source"] not in {"claude", "codex", "tmux"} or run["state"] not in {"unknown", "working", "idle", "needs-attention", "error-observed", "ended"}:
            raise Rejected("Invalid lifecycle state")
        if run["source"] == "tmux" and run["state"] != "unknown":
            raise Rejected("Metadata cannot assert activity")
        if run["reachability"] not in {"present", "absent", "not-observed", "disconnected", "stale", "unknown"}:
            raise Rejected("Invalid presence")
        if not timestamp(run["state_at"]) <= timestamp(run["last_seen"]) <= sampled + 60:
            raise Rejected("Invalid observation time")
    return raw


class HubStore:
    def __init__(self, path, registry):
        self.registry = registry
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            except FileExistsError:
                pass
        self.db = sqlite3.connect(path, timeout=3)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS frames(machine TEXT, stream TEXT, sequence INTEGER, fingerprint TEXT, body TEXT, received REAL, PRIMARY KEY(machine,stream))")
        self.db.execute("CREATE TABLE IF NOT EXISTS workflow(id TEXT PRIMARY KEY, lane TEXT NOT NULL, revision INTEGER NOT NULL, edited REAL NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS workflow_alias(alias TEXT PRIMARY KEY, canonical TEXT NOT NULL)")
        self.db.commit()

    def close(self):
        self.db.close()

    workflow_get = Store.workflow_get
    workflow_move = Store.workflow_move
    workflow_canonical = Store.workflow_canonical

    def ingest(self, raw, machine, now=None):
        frame = validate_frame(raw, machine, self.registry)
        now = time.time() if now is None else now
        body = json.dumps(frame, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(body.encode()) > MAX_BODY:
            raise Rejected("Frame too large")
        sha = hashlib.sha256(body.encode()).hexdigest()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            old = self.db.execute("SELECT sequence,fingerprint FROM frames WHERE machine=? AND stream=?", (machine, frame["stream"])).fetchone()
            if old and frame["sequence"] <= old[0]:
                if frame["sequence"] == old[0] and sha == old[1]:
                    return dict(accepted=True, sequence=frame["sequence"])
                raise Rejected("Old or conflicting sequence; review the registered stream if local state was reset")
            self.db.execute("INSERT OR REPLACE INTO frames VALUES (?,?,?,?,?,?)", (machine, frame["stream"], frame["sequence"], sha, body, now))
        return dict(accepted=True, sequence=frame["sequence"])

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        registry = self.registry
        stale, offline = registry["stale_after"], registry["offline_after"]
        projects = {pid: dict(p, completion="unknown", runs=[]) for pid, p in registry["projects"].items()}
        by_repo = {p["repository"].lower(): p for p in projects.values()}
        collectors = []
        for machine, device in registry["devices"].items():
            row = self.db.execute("SELECT body,received FROM frames WHERE machine=? AND stream=?", (machine, device["stream"])).fetchone()
            if row is None:
                collectors.append(dict(machine=machine, status="unknown", collector_status="unknown", checked_at=None, panes=0, unmatched_panes=0, omitted_runs=0))
                continue
            frame, received = json.loads(row[0]), row[1]
            age = now - received
            status = "offline" if age > offline else "stale" if age > stale or now-frame["sampled_at"] > stale else "online"
            health = frame["collector"]
            collectors.append(dict(machine=machine, status=status, collector_status=health["status"], checked_at=received,
                                   panes=health["panes"], unmatched_panes=health["unmatched_panes"], omitted_runs=frame["omitted_runs"]))
            allow = {r.lower() for r in device["repositories"]}
            for run in frame["runs"]:
                repo = run["repository"].lower()
                if repo not in by_repo or repo not in allow:
                    continue  # Revoked registration is effective even for stored frames.
                by_repo[repo]["runs"].append(dict(id=digest(machine, device["stream"], run["run_id"]), machine=machine,
                    source=run["source"], state=run["state"], state_at=run["state_at"], last_seen=run["last_seen"],
                    freshness="stale" if status != "online" or now-run["last_seen"] > stale else "fresh",
                    state_freshness="stale" if status != "online" or now-run["state_at"] > stale else "fresh",
                    reachability=run["reachability"] if status == "online" else status,
                    evidence="forwarded-metadata" if run["source"] == "tmux" else "forwarded-lifecycle", recent_events=[]))
        for project in projects.values():
            project["runs"].sort(key=lambda r: (-r["last_seen"], r["machine"], r["id"]))
        return dict(schema_version=1, mode="hub", generated_at=now, cloud=dict(status="import-only", live_connected=False), collectors=collectors, projects=list(projects.values()))


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        request.settimeout(5)
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            if getattr(self, "tls_context", None):
                request = self.tls_context.wrap_socket(request, server_side=True)
            super().process_request_thread(request, address)
        except (OSError, ssl.SSLError):
            request.close()
        finally:
            self.slots.release()

    def handle_error(self, *_args):
        pass  # Do not log raw HTTP input or authentication headers.


def make_hub_server(registry, db_path, bind="127.0.0.1", port=8765, cert=None, key=None):
    ip = ipaddress.ip_address(bind)
    if ip.version != 4 or not private_address(bind):
        raise ValueError("Bind a specific loopback/private IPv4 address; no wildcard/public listener")
    if bool(cert) != bool(key) or (not ip.is_loopback and not (cert and key)):
        raise ValueError("Non-loopback listeners require an explicitly supplied TLS certificate and key")
    assets = Path(__file__).with_name("web")
    initial = HubStore(db_path, registry)
    initial.close()
    workflow = Workflow()
    scheme = "https" if cert else "http"

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, body, mime="application/json", challenge=False):
            nonce = secrets.token_urlsafe(24)
            if mime == "text/html":
                body = body.replace(b"<head>", b'<head><meta name="csp-nonce" content="' + nonce.encode() + b'">', 1)
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'nonce-" + nonce + "'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            if challenge:
                self.send_header("WWW-Authenticate", 'Basic realm="Zudo read-only hub"')
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            values = self.headers.get_all("Host", [])
            if len(values) != 1:
                return False
            host = values[0]
            name, separator, port_text = host.partition(":")
            expected_port = self.server.server_port
            return (name in registry["allowed_hosts"] and
                    ((separator and port_text == str(expected_port)) or
                     (not separator and expected_port == (443 if cert else 80))))

        def auth(self, viewer=False):
            values = self.headers.get_all("Authorization", [])
            if len(values) != 1:
                return None
            value = values[0]
            if len(value) > 512:
                return None
            if viewer:
                try:
                    scheme, credential = value.split(" ", 1)
                    user, token = base64.b64decode(credential, validate=True).decode("ascii").split(":", 1)
                    if scheme != "Basic" or user != "viewer":
                        return None
                except (ValueError, UnicodeError):
                    return None
            else:
                if not value.startswith("Bearer "):
                    return None
                token = value[7:]
            if not re.fullmatch(r"[a-f0-9]{64}", token):
                return None
            hashed = hashlib.sha256(token.encode()).hexdigest()
            if viewer:
                return "viewer" if hmac.compare_digest(hashed, registry["viewer_sha256"]) else None
            for machine, device in registry["devices"].items():
                if hmac.compare_digest(hashed, device["token_sha256"]):
                    return machine
            return None

        def do_GET(self):
            if not self.host_ok():
                self.reply(403, b'{}')
                return
            if not self.auth(viewer=True):
                self.reply(401, b'{}', challenge=True)
                return
            route = self.path
            if route == "/api/snapshot":
                store = HubStore(db_path, registry)
                try:
                    body = json.dumps(store.snapshot()).encode()
                finally:
                    store.close()
                self.reply(200, body)
            elif route == "/api/workflow":
                store = None
                try:
                    store = HubStore(db_path, registry)
                    runs = known_runs(store.snapshot())
                    items = workflow.state(store, runs)
                    self.reply(200, json.dumps(dict(csrf=workflow.csrf, lanes=list(LANES), items=items,
                        run_keys=[dict(id=key, canonical=store.workflow_canonical(key), **item) for key, item in runs.items()], sessions=[])).encode())
                except sqlite3.OperationalError:
                    self.reply(503, b'{"error":"Workflow temporarily unavailable"}')
                finally:
                    if store: store.close()
            elif route in {"/", "/app.js", "/style.css", "/preferences.js", "/tokens.css", "/dashboard.css", "/detail.css", "/editor.js", "/THIRD_PARTY_NOTICES.txt", "/favicon.svg"}:
                name = "index.html" if route == "/" else route[1:]
                mime = "text/html" if route == "/" else "text/javascript" if name.endswith(".js") else "text/css" if name.endswith(".css") else "image/svg+xml" if name.endswith(".svg") else "text/plain"
                self.reply(200, (assets / name).read_bytes(), mime)
            else:
                self.reply(404, b'{}')

        def do_POST(self):
            if not self.host_ok() or self.path not in {"/api/ingest", "/api/workflow"}:
                self.reply(404, b'{}')
                return
            if self.path == "/api/workflow":
                if not self.auth(viewer=True):
                    self.reply(401, b'{}', challenge=True)
                    return
                if (self.headers.get_all("Origin", []) != [scheme + "://" + self.headers["Host"]] or
                        self.headers.get_all("X-Workflow-CSRF", []) != [workflow.csrf] or
                        self.headers.get_all("Content-Type", []) != ["application/json"] or
                        self.headers.get_all("Transfer-Encoding") is not None or
                        len(self.headers.get_all("Content-Length", [])) != 1):
                    self.reply(403, b'{}')
                    return
                store = None
                try:
                    size = int(self.headers["Content-Length"])
                    if not 0 < size <= 32768:
                        raise Rejected("Invalid size")
                    body = strict_json(self.rfile.read(size))
                    exact(body, {"id", "lane", "revision"})
                    store = HubStore(db_path, registry)
                    if body["id"] not in known_runs(store.snapshot(), current=True):
                        self.reply(409, b'{"error":"Workflow target no longer current"}')
                        return
                    updated, current = workflow.move(store, body["id"], body["lane"], body["revision"])
                    self.reply(409 if current else 200, json.dumps(dict(id=body["id"], **(current or updated))).encode())
                except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
                    self.reply(400, b'{}')
                except sqlite3.OperationalError:
                    self.reply(503, b'{"error":"Workflow temporarily unavailable"}')
                finally:
                    if store: store.close()
                return
            machine = self.auth()
            if not machine:
                self.reply(401, b'{}')
                return
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get("Transfer-Encoding") or self.headers.get("Content-Type") != "application/json":
                self.reply(400, b'{}')
                return
            size = int(lengths[0])
            if not 1 <= size <= MAX_BODY:
                self.reply(413, b'{}')
                return
            store = None
            try:
                body = self.rfile.read(size)
                if len(body) != size:
                    raise Rejected("Incomplete frame")
                frame = strict_json(body)
                store = HubStore(db_path, registry)
                result = store.ingest(frame, machine)
                self.reply(200, json.dumps(result).encode())
            except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
                self.reply(409, b'{"accepted":false}')
            finally:
                if store:
                    store.close()

        def log_message(self, *_args):
            pass

    context = None
    if cert:
        info = Path(key).lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError("TLS key must be a private regular file owned by the current user")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert, key)
    server = BoundedServer((bind, port), Handler)
    server.tls_context = context
    return server


def serve_hub(registry, db_path, bind, port, cert=None, key=None):
    server = make_hub_server(registry, db_path, bind, port, cert, key)
    print(f"Read-only hub: {'https' if cert else 'http'}://{bind}:{server.server_port}; registered devices: {len(registry['devices'])}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
