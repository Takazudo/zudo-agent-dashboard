"""Loopback observation UI with a separately authorized, opt-in pane console."""

import json
import secrets
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from .collector import discover
from .sample import sample_snapshot
from .store import Store


def make_server(config, db_path, port=8765, sample=False, console_policy=None):
    assets = Path(__file__).with_name("web")
    if sample and console_policy is not None:
        raise ValueError("Sample mode cannot enable a real console")
    from .console import Console, ConsoleError
    from .hub import strict_json, BoundedServer
    from .hub import exact
    from .workflow import Workflow, known_runs, run_id, LANES
    console = Console(config, console_policy) if console_policy else None
    workflow = Workflow()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def host_ok(self):
            return (len(self.headers.get_all("Host", [])) == 1 and
                    self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"})

        def console_auth(self):
            if console is None:
                self.reply(403, dict(error="Pane console is disabled"))
                return False
            if not console.authorized(self.headers):
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Local pane console", charset="UTF-8"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return False
            return True

        def reply(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self.host_ok() or self.headers.get("Upgrade"):
                self.reply(403, dict(error="Invalid host or upgrade"))
                return
            is_workflow = self.path in {"/api/workflow", "/api/console/workflow"}
            if not is_workflow and not self.path.startswith("/api/console/"):
                self.reply(501 if console is None else 404, dict(error="Unknown operation"))
                return
            if self.path.startswith("/api/console/") and not self.console_auth():
                return
            origin = f"http://{self.headers['Host']}"
            if (self.headers.get_all("Origin", []) != [origin] or
                    self.headers.get_all("X-Workflow-CSRF" if is_workflow else "X-Console-CSRF", []) != [workflow.csrf if is_workflow else console.csrf] or
                    self.headers.get_all("Content-Type", []) != ["application/json"] or
                    self.headers.get_all("Transfer-Encoding") is not None or
                    len(self.headers.get_all("Content-Length", [])) != 1):
                self.reply(403, dict(error="Invalid request origin or CSRF proof"))
                return
            try:
                length = int(self.headers["Content-Length"])
                if not 0 < length <= 32768:
                    raise ValueError()
                body = strict_json(self.rfile.read(length))
                if is_workflow:
                    exact(body, {"id", "lane", "revision"})
                    store = None if sample else Store(db_path, config)
                    try:
                        snapshot = sample_snapshot() if sample else store.snapshot()
                        ids = known_runs(snapshot, current=True)
                        if console and console.authorized(self.headers):
                            try:
                                targets = console.handle("targets", {})["targets"]
                            except (ConsoleError, OSError, subprocess.SubprocessError):
                                targets = []
                            for target in targets:
                                ids[target["workflow_id"]] = target
                        if body["id"] not in ids:
                            self.reply(409, dict(error="Workflow target no longer current"))
                            return
                        if store and store.workflow_canonical(body["id"]) != body["id"]:
                            if not (console and console.authorized(self.headers)):
                                self.reply(403, dict(error="Session metadata requires console authentication"))
                                return
                            if store.workflow_canonical(body["id"]) not in ids:
                                self.reply(409, dict(error="Session no longer current"))
                                return
                        updated, current = workflow.move(store, body["id"], body["lane"], body["revision"])
                        self.reply(409 if current else 200, dict(id=body["id"], **(current or updated)))
                    finally:
                        if store: store.close()
                else:
                    self.reply(200, console.handle(self.path.removeprefix("/api/console/"), body))
            except ConsoleError as exc:
                self.reply(exc.status, dict(error=exc.message))
            except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
                self.reply(400, dict(error="Invalid console request"))
            except (OSError, subprocess.SubprocessError):
                self.reply(503, dict(error="Console unavailable; reconnect explicitly"))
            except sqlite3.OperationalError:
                self.reply(503, dict(error="Workflow temporarily unavailable"))

        def do_GET(self):
            if not self.host_ok() or self.headers.get("Upgrade"):
                self.send_error(403)
                return
            route = self.path
            if route == "/api/console/status":
                self.reply(200, dict(enabled=console is not None))
                return
            if route == "/api/console/bootstrap":
                if self.console_auth():
                    self.reply(200, dict(csrf=console.csrf, identity=console.policy["identity"], allow_input=console.policy["allow_input"]))
                return
            if route in {"/api/workflow", "/api/console/workflow"}:
                if route == "/api/console/workflow" and not self.console_auth():
                    return
                store = None
                try:
                    if not sample:
                        store = Store(db_path, config)
                    snapshot = sample_snapshot() if sample else store.snapshot()
                    ids = known_runs(snapshot)
                    sessions = []
                    authenticated = bool(console and console.authorized(self.headers))
                    if authenticated:
                        groups = {}
                        try:
                            targets = console.handle("targets", {})["targets"]
                        except (ConsoleError, OSError, subprocess.SubprocessError):
                            targets = []
                        for target in targets:
                            group = groups.setdefault(target["workflow_id"], dict(id=target["workflow_id"],
                                session_id=target["session_id"], project=target["project"], machine=target["machine"],
                                panes=[], runs=[]))
                            group["panes"].append(target["id"])
                            if target["run"] and target["run"] not in group["runs"]:
                                group["runs"].append(target["run"])
                        sessions = list(groups.values())
                        for group in sessions:
                            ids[group["id"]] = group
                            if store:
                                aliases = [run_id(group["project"], group["machine"], run) for run in group["runs"]]
                                store.workflow_reconcile(group["id"], aliases)
                    run_keys = [dict(id=key, canonical=store.workflow_canonical(key) if store and authenticated else key, **item)
                                for key, item in known_runs(snapshot).items()]
                    if authenticated:
                        ids.update({item["canonical"]: item for item in run_keys})
                    self.reply(200, dict(csrf=workflow.csrf, lanes=list(LANES),
                        items=workflow.state(store, ids), run_keys=run_keys, sessions=sessions))
                except sqlite3.OperationalError:
                    self.reply(503, dict(error="Workflow temporarily unavailable"))
                finally:
                    if store: store.close()
                return
            if route == "/console.html" and not self.console_auth():
                return
            if route == "/api/snapshot":
                if sample:
                    data = sample_snapshot()
                else:
                    store = None
                    try:
                        store = Store(db_path, config)
                        data = store.snapshot()
                    except sqlite3.OperationalError:
                        self.send_error(503, "Observations temporarily unavailable")
                        return
                    finally:
                        if store:
                            store.close()
                body = json.dumps(data).encode()
                mime = "application/json"
            elif route in {"/", "/app.js", "/style.css", "/console.html", "/console.js", "/preferences.js",
                           "/tokens.css", "/dashboard.css", "/detail.css", "/editor.js", "/THIRD_PARTY_NOTICES.txt", "/favicon.svg"}:
                name = "index.html" if route == "/" else route[1:]
                body = (assets / name).read_bytes()
                if name.endswith(".html"):
                    body = body.replace(b"<head>", b'<head><meta name="csp-nonce" content="__ZUDO_NONCE__">', 1)
                mime = "text/javascript" if name.endswith(".js") else "text/css" if name.endswith(".css") else "image/svg+xml" if name.endswith(".svg") else "text/plain" if name.endswith(".txt") else "text/html"
            else:
                self.send_error(404)
                return
            nonce = secrets.token_urlsafe(24)
            if mime == "text/html":
                body = body.replace(b"__ZUDO_NONCE__", nonce.encode())
            self.send_response(200)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'nonce-" + nonce + "'; connect-src 'self'; frame-src 'self'; frame-ancestors " + ("'self'" if route == "/console.html" else "'none'") + "; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    class LocalServer(BoundedServer):
        def server_close(self):
            if console:
                console.shutdown()
            super().server_close()

    return LocalServer(("127.0.0.1", port), Handler)


def collect_loop(config, db_path, stop, interval=5):
    from .transport import Forwarder
    store = None
    forwarder = None
    try:
        while not stop.is_set():
            try:
                if store is None:
                    store = Store(db_path, config)
                    forwarder = Forwarder(store, config) if "transport" in config else None
                discover(config, store)
                if forwarder:
                    forwarder.cycle()
            except (sqlite3.OperationalError, OSError):
                # Concurrent hook writers can briefly hold SQLite's write lock.
                if store:
                    store.close()
                store = None
                forwarder = None
            stop.wait(interval)
    finally:
        if store:
            store.close()


def serve(config, db_path, port, sample=False, console_policy=None):
    server = make_server(config, db_path, port, sample, console_policy)
    stop = threading.Event()

    worker = None if sample else threading.Thread(target=collect_loop, args=(config, db_path, stop), daemon=True)
    if worker:
        worker.start()
    print(f"{'SAMPLE' if sample else 'LOCAL'} dashboard: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        if worker:
            worker.join(timeout=5)
