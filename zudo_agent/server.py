"""Loopback-only, GET-only UI. No remote assets, write APIs, or terminal access."""

import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .collector import discover
from .sample import sample_snapshot
from .store import Store


def make_server(config, db_path, port=8765, sample=False):
    assets = Path(__file__).with_name("web")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                self.send_error(403)
                return
            route = self.path.split("?", 1)[0]
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
            elif route in {"/", "/app.js", "/style.css"}:
                name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[route]
                body = (assets / name).read_bytes()
                mime = {"/": "text/html", "/app.js": "text/javascript", "/style.css": "text/css"}[route]
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

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


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


def serve(config, db_path, port, sample=False):
    server = make_server(config, db_path, port, sample)
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
