"""Loopback observation UI with a separately authorized, opt-in pane console."""

import json
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
    console = Console(config, console_policy) if console_policy else None

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
            if not self.path.startswith("/api/console/"):
                self.reply(501 if console is None else 404, dict(error="Unknown operation"))
                return
            if not self.console_auth():
                return
            origin = f"http://{self.headers['Host']}"
            if (self.headers.get_all("Origin", []) != [origin] or
                    self.headers.get_all("X-Console-CSRF", []) != [console.csrf] or
                    self.headers.get_all("Content-Type", []) != ["application/json"] or
                    self.headers.get("Transfer-Encoding") or
                    len(self.headers.get_all("Content-Length", [])) != 1):
                self.reply(403, dict(error="Invalid request origin or CSRF proof"))
                return
            try:
                length = int(self.headers["Content-Length"])
                if not 0 < length <= 32768:
                    raise ValueError()
                body = strict_json(self.rfile.read(length))
                self.reply(200, console.handle(self.path.removeprefix("/api/console/"), body))
            except ConsoleError as exc:
                self.reply(exc.status, dict(error=exc.message))
            except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
                self.reply(400, dict(error="Invalid console request"))
            except (OSError, subprocess.SubprocessError):
                self.reply(503, dict(error="Console unavailable; reconnect explicitly"))

        def do_GET(self):
            if not self.host_ok() or self.headers.get("Upgrade"):
                self.send_error(403)
                return
            route = self.path.split("?", 1)[0]
            if route == "/api/console/status":
                self.reply(200, dict(enabled=console is not None))
                return
            if route == "/api/console/bootstrap":
                if self.console_auth():
                    self.reply(200, dict(csrf=console.csrf, identity=console.policy["identity"], allow_input=console.policy["allow_input"]))
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
            elif route in {"/", "/app.js", "/style.css", "/console.html", "/console.js"}:
                name = "index.html" if route == "/" else route[1:]
                body = (assets / name).read_bytes()
                mime = "text/javascript" if name.endswith(".js") else "text/css" if name.endswith(".css") else "text/html"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    return BoundedServer(("127.0.0.1", port), Handler)


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
