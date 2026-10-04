"""Simulated direct Serve headers and disposable backend only; no tailnet access."""
import base64
import contextlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from test_console import Backend, Console, PASSWORD, POLICY, TARGET, config
from zudo_agent.console_proxy import make_proxy, settings
from zudo_agent.server import make_server

ORIGIN = "https://console.example.ts.net:8443"
LOGIN = "fixture@example.test"
AUTH = "Basic " + base64.b64encode(f"fixture:{PASSWORD}".encode()).decode()


class ProxyFixtures(unittest.TestCase):
    def start(self, server):
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
        self.addCleanup(server.shutdown)
        return server

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.backend = Backend()
        self.console = Console(config(temp.name), dict(POLICY, allow_input=True), self.backend)
        self.addCleanup(self.console.shutdown)
        with patch("zudo_agent.console.Console", return_value=self.console):
            self.server = self.start(make_server(config(temp.name), Path(temp.name) / "fixture.sqlite", 0, console_policy=POLICY))
        self.proxy = self.start(make_proxy(ORIGIN, LOGIN, self.server.server_port, 0, trust_local_serve=True))
        self.headers = {"Host": ORIGIN[8:], "X-Forwarded-Host": ORIGIN[8:], "X-Forwarded-Proto": "https",
                        "Tailscale-User-Login": LOGIN, "Authorization": AUTH,
                        "Origin": ORIGIN, "X-Console-CSRF": self.console.csrf, "Content-Type": "application/json"}

    def request(self, route="/api/console/bootstrap", method="GET", body=None, changes=None, extra=(), source=None):
        headers = dict(self.headers)
        for k, v in (changes or {}).items():
            if v is None: headers.pop(k, None)
            else: headers[k] = v
        conn = http.client.HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=3, source_address=source)
        self.addCleanup(conn.close)
        conn.putrequest(method, route, skip_host=True, skip_accept_encoding=True)
        for k, v in list(headers.items()) + list(extra): conn.putheader(k, v)
        if body is not None and "Content-Length" not in headers: conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        return response.status, response.read(), response.headers

    def post(self, action, data, **kwargs):
        return self.request("/api/console/" + action, "POST", json.dumps(data).encode(), **kwargs)

    def test_configuration_requires_explicit_boundary_and_canonical_origin(self):
        for origin in ("http://example.test", ORIGIN + "/", ORIGIN + "?x", "https://user@example.test", "https://example.test:443", "https://Example.test", "https://example.test:65536"):
            with self.subTest(origin=origin), self.assertRaises(ValueError): settings(origin, LOGIN, 46207, True)
        for login in ("", "a b", "a\nb", "日本"):
            with self.assertRaises(ValueError): settings(ORIGIN, login, 46207, True)
        for port in (0, -1, True, 65536, "46207"):
            with self.assertRaises(ValueError): settings(ORIGIN, LOGIN, port, True)
        with self.assertRaises(ValueError): make_proxy(ORIGIN, LOGIN, 46207)
        with self.assertRaises(ValueError): make_proxy(ORIGIN, LOGIN, 46207, 46207, trust_local_serve=True)
        self.assertEqual(self.proxy.server_address[0], "127.0.0.1")

    def test_identity_host_origin_funnel_and_upgrade_fail_closed(self):
        for name in ("Host", "X-Forwarded-Host", "X-Forwarded-Proto", "Tailscale-User-Login"):
            for value in (None, "forged"):
                with self.subTest(name=name, value=value): self.assertEqual(self.request(changes={name: value})[0], 403)
            self.assertEqual(self.request(extra=[(name, self.headers[name])])[0], 403)
        for name, value in (("Origin", "https://evil.test"), ("Tailscale-Funnel-Request", ""), ("Tailscale-Funnel-Request", "?0"), ("Upgrade", "websocket"), ("Connection", "keep-alive, Upgrade")):
            self.assertEqual(self.request(changes={name: value})[0], 403)
        self.assertEqual(self.request(extra=[("Origin", ORIGIN)])[0], 403)
        # Other loopback addresses are not the declared direct Serve peer.
        if sys.platform == "linux":
            self.assertEqual(self.request(source=("127.0.0.2", 0))[0], 403)
        self.assertEqual(self.request(changes={"Origin": None})[0], 200)

    def test_basic_challenge_csrf_and_external_origin_before_rewrite(self):
        for auth in (None, "Basic !!!!", "Basic " + base64.b64encode(b"fixture:wrong").decode()):
            status, _, headers = self.request(changes={"Authorization": auth})
            self.assertEqual(status, 401)
            self.assertIn("Basic", headers["WWW-Authenticate"])
        status, body, headers = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["csrf"], self.console.csrf)
        self.assertEqual(headers["Cache-Control"], "no-store")
        for name, value in (("Origin", None), ("Origin", f"http://127.0.0.1:{self.server.server_port}"), ("X-Console-CSRF", None), ("X-Console-CSRF", "wrong"), ("Content-Type", "text/plain")):
            self.assertEqual(self.post("targets", {}, changes={name: value})[0], 403)
        self.assertEqual(self.post("targets", {})[0], 200)
        self.assertEqual(self.post("targets", {}, changes={"Authorization": None})[0], 401)

    def test_dashboard_commands_asset_is_forwarded(self):
        status, body, headers = self.request("/commands.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers.get("Content-Type", ""))
        self.assertIn("script-src 'self'", headers.get("Content-Security-Policy", ""))
        self.assertIn(b"window.DashboardCommands", body)

    def test_authenticated_workflow_route_and_workflow_csrf_forwarding(self):
        def respond(handler):
            self.assertEqual(handler.path, "/api/console/workflow")
            if handler.headers.get("Authorization") != AUTH:
                handler.send_response(401)
                handler.send_header("WWW-Authenticate", 'Basic realm="fixture"')
                handler.end_headers()
                return
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.end_headers()
            handler.wfile.write(b'{"csrf":"fixture-workflow-csrf","sessions":[]}')

        upstream = self.upstream(respond)
        status, body, _ = self.request("/api/console/workflow")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["sessions"], [])
        forwarded, _ = self.records[-1]
        self.assertEqual(forwarded["Authorization"], AUTH)
        self.assertEqual(forwarded["Host"], f"127.0.0.1:{upstream.server_port}")

        payload = json.dumps(dict(id="fixture-session", lane="review", revision=0)).encode()
        status, _, _ = self.request("/api/console/workflow", "POST", payload,
                                    changes={"X-Workflow-CSRF": "fixture-workflow-csrf"})
        self.assertEqual(status, 200)
        forwarded, forwarded_body = self.records[-1]
        self.assertEqual(forwarded["Authorization"], AUTH)
        self.assertEqual(forwarded["X-Workflow-CSRF"], "fixture-workflow-csrf")
        self.assertEqual(forwarded["Origin"], f"http://127.0.0.1:{upstream.server_port}")
        self.assertEqual(forwarded_body, payload)

        self.assertEqual(self.request("/api/console/workflow", changes={"Authorization": None})[0], 401)
        self.assertEqual(self.request("/api/console/workflow", "POST", payload,
                         changes={"X-Workflow-CSRF": None})[0], 403)
        self.assertEqual(self.request("/api/console/workflow", "POST", payload,
                         changes={"X-Workflow-CSRF": "fixture-workflow-csrf", "Origin": "https://evil.test"})[0], 403)

    def test_human_control_and_no_replay_through_adapter(self):
        status, body, _ = self.post("open", {k: TARGET[k] for k in ("project", "id", "machine")})
        self.assertEqual(status, 200); lease = json.loads(body)["lease"]
        self.assertEqual(self.post("send", dict(lease=lease, sequence=1, text="fixture"))[0], 403)
        self.assertEqual(self.post("control", dict(lease=lease, enabled=True))[0], 200)
        self.assertEqual(self.post("send", dict(lease=lease, sequence=1, text="fixture"))[0], 200)
        self.assertEqual(self.post("resize", dict(lease=lease, sequence=2, cols=90, rows=30))[0], 200)
        self.assertEqual(len(self.backend.sent), 2)
        self.assertEqual(self.post("send", dict(lease=lease, sequence=1, text="fixture"))[0], 409)
        self.assertEqual(len(self.backend.sent), 2)
        self.assertEqual(self.post("screen", dict(lease=lease))[0], 409)
        _, body, _ = self.post("open", {k: TARGET[k] for k in ("project", "id", "machine")})
        fresh = json.loads(body)["lease"]
        self.assertNotEqual(fresh, lease)
        self.assertEqual(self.post("send", dict(lease=fresh, sequence=1, text="fixture"))[0], 403)
        self.backend.current = [dict(TARGET, boot="replaced")]
        self.assertEqual(self.post("screen", dict(lease=fresh))[0], 409)
        self.assertEqual(len(self.backend.sent), 2)

    def test_read_only_policy_and_console_off_remain_off(self):
        self.console.policy = dict(POLICY, allow_input=False)
        _, body, _ = self.post("open", {k: TARGET[k] for k in ("project", "id", "machine")})
        lease = json.loads(body)["lease"]
        self.assertEqual(self.post("control", dict(lease=lease, enabled=True))[0], 403)
        self.assertEqual(self.post("send", dict(lease=lease, sequence=1, text="fixture"))[0], 403)
        off = self.start(make_server(None, None, 0, sample=True))
        self.proxy = self.start(make_proxy(ORIGIN, LOGIN, off.server_port, 0, trust_local_serve=True))
        status, body, _ = self.request("/api/console/status")
        self.assertEqual((status, json.loads(body)), (200, {"enabled": False}))
        self.assertEqual(self.request()[0], 403)
        self.assertEqual(self.post("targets", {})[0], 403)
        self.assertEqual(self.backend.sent, [])

    def test_preflight_paths_and_request_limits(self):
        status, _, headers = self.request(method="OPTIONS")
        self.assertEqual(status, 405); self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        status, body, headers = self.request(method="UNKNOWN_FIXTURE")
        self.assertEqual(status, 501); self.assertEqual(body, b"{}")
        self.assertEqual(headers["Cache-Control"], "no-store")
        for path in ("/api/console/bootstrap?x", "http://evil.test/", "/api/ingest", "/%61pi/console/bootstrap", "/commands.js?x"):
            self.assertEqual(self.request(path)[0], 404)
        for changes, expected in (({"Content-Length": "32769"}, 413), ({"Content-Length": "-1"}, 400), ({"Transfer-Encoding": "chunked"}, 400), ({"Expect": "100-continue"}, 400)):
            self.assertEqual(self.request(changes=changes)[0], expected)
        self.assertEqual(self.request(extra=[("Content-Length", "0"), ("Content-Length", "0")])[0], 400)
        self.assertEqual(self.request(extra=[("Authorization", AUTH)])[0], 401)
        self.assertEqual(self.post("targets", {}, extra=[("X-Console-CSRF", self.console.csrf)])[0], 403)

    def upstream(self, behavior):
        self.records = []
        records = self.records
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                records.append((dict(self.headers), body))
                behavior(self)
            do_GET = do_POST
            def log_message(self, *_): pass
        server = self.start(ThreadingHTTPServer(("127.0.0.1", 0), Handler))
        self.proxy = self.start(make_proxy(ORIGIN, LOGIN, server.server_port, 0, trust_local_serve=True))
        return server

    def test_only_approved_headers_forwarded_and_no_response_caching_or_logging(self):
        def respond(h):
            h.send_response(200); h.send_header("Content-Length", "2")
            h.send_header("Set-Cookie", "secret"); h.send_header("Cache-Control", "public")
            h.send_header("Access-Control-Allow-Origin", "*"); h.end_headers(); h.wfile.write(b"{}")
        upstream = self.upstream(respond)
        logs = io.StringIO()
        with contextlib.redirect_stderr(logs), contextlib.redirect_stdout(logs):
            status, _, headers = self.post("targets", {}, changes={"Cookie": "secret", "Forwarded": "host=evil", "X-Forwarded-For": "evil"})
        self.assertEqual(status, 200); self.assertEqual(logs.getvalue(), "")
        forwarded, body = self.records[0]
        self.assertEqual(forwarded["Authorization"], AUTH)
        self.assertEqual(forwarded["X-Console-CSRF"], self.console.csrf)
        self.assertEqual(forwarded["Host"], f"127.0.0.1:{upstream.server_port}")
        self.assertEqual(forwarded["Origin"], f"http://127.0.0.1:{upstream.server_port}")
        for key in ("Cookie", "Forwarded", "X-Forwarded-For", "Tailscale-User-Login"):
            self.assertNotIn(key, forwarded)
        for key in ("Set-Cookie", "Access-Control-Allow-Origin"):
            self.assertIsNone(headers.get(key))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(body, b"{}")

    def test_uncertain_upstream_delivery_is_not_retried(self):
        self.upstream(lambda h: h.connection.shutdown(socket.SHUT_RDWR))
        status, body, _ = self.post("send", {"text": "fixture-only"})
        self.assertEqual(status, 502); self.assertIn(b"uncertain", body)
        self.assertEqual(len(self.records), 1)

    def test_redirect_and_response_size_limit(self):
        def redirect(h):
            h.send_response(302); h.send_header("Location", "http://example.invalid"); h.end_headers()
        self.upstream(redirect)
        self.assertEqual(self.request()[0], 502); self.assertEqual(len(self.records), 1)
        def oversized(h):
            h.send_response(200); h.send_header("Content-Length", "100"); h.end_headers(); h.wfile.write(b"x" * 100)
        self.upstream(oversized)
        with patch("zudo_agent.console_proxy.MAX_RESPONSE", 16):
            self.assertEqual(self.request()[0], 502)

    def test_slow_upstream_and_incomplete_body_are_bounded(self):
        self.upstream(lambda h: time.sleep(.3))
        with patch("zudo_agent.console_proxy.IO_TIMEOUT", .05):
            self.assertEqual(self.request()[0], 502)
        self.assertEqual(len(self.records), 1)
        with patch("zudo_agent.console_proxy.REQUEST_TIMEOUT", .1):
            conn = socket.create_connection(("127.0.0.1", self.proxy.server_port), timeout=2)
            self.addCleanup(conn.close)
            conn.sendall(b"GET / HTTP/1.1\r\nHost: ")
            self.assertEqual(conn.recv(1024), b"")
