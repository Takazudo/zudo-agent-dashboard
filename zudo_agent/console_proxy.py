"""Optional Serve-only loopback adapter. Never provisions access or credentials.

Trusted boundary: Tailscale Serve connects directly from 127.0.0.1 and replaces
identity/forwarding headers. Local processes are trusted; TCP loopback cannot
prove the sender is tailscaled. Console Basic auth remains independently enforced.
"""
import http.client
import json
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

from .hub import BoundedServer

MAX_BODY = 32768
MAX_RESPONSE = 2 * 1024 * 1024
IO_TIMEOUT = 5
REQUEST_TIMEOUT = 10
GET_ROUTES = {"/", "/app.js", "/style.css", "/console.html", "/console.js",
              "/preferences.js", "/tokens.css", "/dashboard.css", "/detail.css",
              "/editor.js", "/THIRD_PARTY_NOTICES.txt", "/favicon.svg", "/api/workflow",
              "/api/snapshot", "/api/console/status", "/api/console/bootstrap", "/api/console/workflow"}
POST_ROUTES = {"/api/console/" + action for action in
               ("targets", "open", "screen", "preview", "close", "control", "send", "resize", "workflow")} | {"/api/workflow"}


def settings(external_origin, allowed_login, backend_port, trust_local_serve):
    if trust_local_serve is not True:
        raise ValueError("Explicit trust of local Tailscale Serve is required")
    parsed = urlsplit(external_origin)
    if (external_origin != "https://" + parsed.netloc or parsed.scheme != "https" or parsed.path or parsed.query or parsed.fragment or
            parsed.username is not None or parsed.password is not None or
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[1-9][0-9]{0,4})?", parsed.netloc)):
        raise ValueError("A canonical explicit HTTPS origin is required")
    if parsed.port is not None and (not 1 <= parsed.port <= 65535 or parsed.port == 443):
        raise ValueError("Use canonical HTTPS origin; omit the default 443 port")
    if (not isinstance(allowed_login, str) or not allowed_login.isascii() or
            not 1 <= len(allowed_login) <= 254 or any(ord(c) <= 32 or ord(c) >= 127 for c in allowed_login)):
        raise ValueError("An exact ASCII Tailscale login is required")
    if type(backend_port) is not int or not 1 <= backend_port <= 65535:
        raise ValueError("A literal loopback backend port is required")
    return parsed.netloc


def interrupt(connection):
    try:
        connection.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def make_proxy(external_origin, allowed_login, backend_port, port=46208, *, trust_local_serve=False):
    authority = settings(external_origin, allowed_login, backend_port, trust_local_serve)
    if type(port) is not int or not 0 <= port <= 65535 or port == backend_port:
        raise ValueError("Proxy and backend require distinct loopback ports")
    backend_host = f"127.0.0.1:{backend_port}"

    class Handler(BaseHTTPRequestHandler):
        def handle(self):
            self.connection.settimeout(IO_TIMEOUT)
            timer = threading.Timer(REQUEST_TIMEOUT, interrupt, args=(self.connection,))
            timer.daemon = True
            timer.start()
            try:
                super().handle()
            finally:
                timer.cancel()

        def reply(self, status, body=b'{}', headers=()):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Connection", "close")
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def send_error(self, code, message=None, explain=None):
            # Parser/method errors must not reflect request data or cache it.
            self.reply(code)

        def one(self, name):
            values = self.headers.get_all(name, [])
            return values[0] if len(values) == 1 else None

        def proxy(self):
            # Forwarded headers are consistency checks on the declared local Serve
            # boundary, not an independent authentication mechanism.
            if (self.client_address[0] != "127.0.0.1" or
                    self.one("Host") != authority or
                    self.one("X-Forwarded-Host") != authority or
                    self.one("X-Forwarded-Proto") != "https" or
                    self.one("Tailscale-User-Login") != allowed_login or
                    self.headers.get_all("Tailscale-Funnel-Request") is not None or
                    self.headers.get_all("Upgrade") is not None or
                    any(token.strip().lower() == "upgrade" for value in self.headers.get_all("Connection", []) for token in value.split(","))):
                self.reply(403)
                return
            origins = self.headers.get_all("Origin", [])
            if (origins and origins != [external_origin]) or (self.command == "POST" and origins != [external_origin]):
                self.reply(403)
                return
            if self.command not in {"GET", "POST"}:
                self.reply(405)
                return
            if self.path not in (GET_ROUTES if self.command == "GET" else POST_ROUTES):
                self.reply(404)
                return
            if self.headers.get_all("Transfer-Encoding") is not None or self.headers.get_all("Expect") is not None:
                self.reply(400)
                return
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) > 1 or (lengths and (not re.fullmatch(r"[0-9]{1,10}", lengths[0]))):
                self.reply(400)
                return
            size = int(lengths[0]) if lengths else 0
            if size > MAX_BODY:
                self.reply(413)
                return
            if (self.command == "GET" and size) or (self.command == "POST" and not size):
                self.reply(400)
                return
            # Construct an allowlist rather than forwarding arbitrary identity,
            # Forwarded, Cookie, hop-by-hop, or client-selected routing headers.
            outgoing = {"Host": backend_host, "Connection": "close"}
            auth = self.headers.get_all("Authorization", [])
            if len(auth) > 1 or (auth and (len(auth[0]) > 1024 or not auth[0].startswith("Basic "))):
                self.reply(401)
                return
            if auth:
                outgoing["Authorization"] = auth[0]
            if self.command == "POST":
                csrf_name = "X-Workflow-CSRF" if self.path in {"/api/workflow", "/api/console/workflow"} else "X-Console-CSRF"
                csrf = self.one(csrf_name)
                if self.one("Content-Type") != "application/json" or not csrf or len(csrf) > 128:
                    self.reply(403)
                    return
                outgoing.update({"Origin": "http://" + backend_host, csrf_name: csrf,
                                 "Content-Type": "application/json"})
            upstream = http.client.HTTPConnection("127.0.0.1", backend_port, timeout=IO_TIMEOUT)
            timer = None
            try:
                body = self.rfile.read(size) if size else None
                if size and len(body) != size:
                    self.reply(400)
                    return
                upstream.connect()
                timer = threading.Timer(REQUEST_TIMEOUT, interrupt, args=(upstream.sock,))
                timer.daemon = True
                timer.start()
                # Exactly one upstream attempt; a timed-out mutation is never retried.
                upstream.request(self.command, self.path, body=body, headers=outgoing)
                response = upstream.getresponse()
                if not 200 <= response.status <= 599 or 300 <= response.status < 400 or response.getheader("Content-Encoding"):
                    self.reply(502)
                    return
                content = response.read(MAX_RESPONSE + 1)
                if len(content) > MAX_RESPONSE:
                    self.reply(502)
                    return
                allowed = {"content-type", "content-security-policy", "www-authenticate"}
                response_headers = [(k, v) for k, v in response.getheaders() if k.lower() in allowed]
                self.reply(response.status, content, response_headers)
            except (OSError, http.client.HTTPException):
                self.reply(502, json.dumps({"error": "Upstream unavailable; delivery may be uncertain. Reconnect and inspect; never retry input automatically."}).encode(), [("Content-Type", "application/json")])
            finally:
                if timer:
                    timer.cancel()
                upstream.close()

        do_GET = do_POST = do_OPTIONS = do_HEAD = do_PUT = do_DELETE = do_PATCH = proxy

        def log_message(self, *_args):
            pass

    return BoundedServer(("127.0.0.1", port), Handler)


def serve_proxy(external_origin, allowed_login, backend_port, port, *, trust_local_serve=False):
    server = make_proxy(external_origin, allowed_login, backend_port, port, trust_local_serve=trust_local_serve)
    print(f"Serve-only console adapter on 127.0.0.1:{server.server_port}; no Serve configuration changed", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
