"""Console tests use synthetic metadata and private disposable tmux servers only."""
import base64
import copy
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from zudo_agent.console import Console, ConsoleError, PaneBackend, bounded, load_policy
from zudo_agent.model import digest
from zudo_agent.server import make_server

PASSWORD = "public-disposable-fixture-password"
POLICY = dict(identity="fixture", password_sha256=hashlib.sha256(PASSWORD.encode()).hexdigest(), projects=["example"], allow_input=False)
TARGET = dict(project="example", run=digest("fixture"), machine="fixture", boot="fixture-boot", server=(123, "1"), pane="%0", root=(124, "2"), agent=(125, "3"))


def config(root):
    return dict(machine="fixture", tmux_socket="fixture", stale_after=120,
                projects={"example": dict(id="example", repository="github.com/example/fixture", roots=[str(root)])})


class Backend:
    def __init__(self):
        self.current = [copy.deepcopy(TARGET)]
        self.sent = []
    def targets(self):
        return self.current
    def screen(self, target):
        if target not in self.current:
            raise ConsoleError(409, "stale")
        return "SYNTHETIC PANE ONLY <script>not executable</script>"
    def send(self, *args):
        self.sent.append(args)


class ConsoleFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = Backend()
        self.now = 10
        self.console = Console(config(self.root), POLICY, self.backend, clock=lambda: self.now)
    def opened(self):
        return self.console.handle("open", {k: TARGET[k] for k in ("project", "run", "machine")})
    def test_policy_private_file_and_control_true_refused(self):
        path = self.root / "policy.json"
        path.write_text(json.dumps(POLICY)); path.chmod(0o600)
        self.assertEqual(load_policy(path, config(self.root)), POLICY)
        link = self.root / "link"; link.symlink_to(path)
        with self.assertRaises((ValueError, OSError)): load_policy(link, config(self.root))
        path.chmod(0o644)
        with self.assertRaises(ValueError): load_policy(path, config(self.root))
        path.chmod(0o600); path.write_text(json.dumps(dict(POLICY, allow_input=True)))
        with self.assertRaises(ValueError): load_policy(path, config(self.root))
        with self.assertRaises(ValueError): Console(config(self.root), dict(POLICY, allow_input=True))
    def test_send_always_refused_including_replays_and_bad_sequences(self):
        lease = self.opened()["lease"]
        for sequence in (1, 1, 0, 2, -1, True):
            with self.assertRaises(ConsoleError) as err:
                self.console.handle("send", dict(lease=lease, sequence=sequence, text="synthetic", enter=True))
            self.assertEqual(err.exception.status, 403)
        self.assertEqual(self.backend.sent, [])
        with patch("zudo_agent.console.bounded") as call:
            with self.assertRaises(ConsoleError): PaneBackend(config(self.root)).send(TARGET, "test", True)
            call.assert_not_called()
    def test_stale_identity_at_each_generation_revokes_lease(self):
        for field in ("boot", "server", "pane", "root", "agent", "run", "machine", "project"):
            self.backend.current = [copy.deepcopy(TARGET)]
            lease = self.opened()["lease"]
            self.backend.current = [dict(TARGET, **{field: "changed"})]
            with self.assertRaises(ConsoleError): self.console.handle("screen", dict(lease=lease))
            self.assertNotIn(lease, self.console.leases)
    def test_expiry_close_restart_and_no_automatic_reconnect(self):
        lease = self.opened()["lease"]
        self.assertIn("SYNTHETIC", self.console.handle("screen", dict(lease=lease))["screen"])
        self.now = 131
        with self.assertRaises(ConsoleError): self.console.handle("screen", dict(lease=lease))
        lease = self.opened()["lease"]
        self.console.handle("close", dict(lease=lease))
        with self.assertRaises(ConsoleError): self.console.handle("screen", dict(lease=lease))
        with self.assertRaises(ConsoleError): Console(config(self.root), POLICY, self.backend).handle("screen", dict(lease=lease))
    def test_ambiguous_unknown_machine_and_project(self):
        self.backend.current *= 2
        with self.assertRaises(ConsoleError): self.opened()
        for field in ("machine", "project"):
            body = {k: TARGET[k] for k in ("project", "run", "machine")}; body[field] = "other"
            with self.assertRaises(ConsoleError): self.console.handle("open", body)
    def test_lease_limit_and_no_request_queue(self):
        for _ in range(8): self.opened()
        with self.assertRaises(ConsoleError): self.opened()
        with self.console.lock:
            with self.assertRaises(ConsoleError) as err: self.opened()
            self.assertEqual(err.exception.status, 429)
    def test_bounded_pipe_and_nonzero_child(self):
        with self.assertRaises(ConsoleError): bounded([sys.executable, "-c", "print('x'*100)"], limit=16)
        with self.assertRaises(ConsoleError): bounded([sys.executable, "-c", "raise SystemExit(1)"])
    def test_screen_sanitizes_controls_and_validates_after_capture(self):
        backend = PaneBackend(config(self.root))
        with patch.object(backend, "validate") as validate, patch("zudo_agent.console.bounded", return_value="hi\x1b\x7f\x85\u202e\n日本語"):
            self.assertEqual(backend.screen(TARGET), "hi\n日本語")
            self.assertEqual(validate.call_count, 2)
        with patch.object(backend, "validate", side_effect=[None, ConsoleError(409, "changed")]), patch("zudo_agent.console.bounded", return_value="must not escape"):
            with self.assertRaises(ConsoleError): backend.screen(TARGET)


class HttpFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.backend = Backend()
        c = Console(config(self.temp.name), POLICY, self.backend)
        self.console = c
        with patch("zudo_agent.console.Console", return_value=c):
            self.server = make_server(config(self.temp.name), Path(self.temp.name) / "fixture.sqlite", 0, console_policy=POLICY)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.headers = {"Authorization": "Basic " + base64.b64encode(f"fixture:{PASSWORD}".encode()).decode(), "Origin": self.origin, "X-Console-CSRF": c.csrf, "Content-Type": "application/json"}
    def request(self, route, method="GET", data=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method, route, data, self.headers if headers is None else headers)
            response = conn.getresponse(); body = response.read()
            return response.status, body, response.headers
        finally: conn.close()
    def test_auth_origin_csrf_and_websocket_checks(self):
        self.assertEqual(self.request("/api/console/bootstrap", headers={})[0], 401)
        for authorization in ("Basic !!!!", "Basic " + "a" * 1024, "Basic " + base64.b64encode("日本:wrong".encode()).decode()):
            self.assertEqual(self.request("/api/console/bootstrap", headers={"Authorization": authorization})[0], 401)
        self.assertEqual(self.request("/api/console/bootstrap")[0], 200)
        for change in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"X-Console-CSRF": "wrong"}, {"Host": "evil.example"}, {"Upgrade": "websocket"}, {"Content-Type": "text/plain"}):
            self.assertEqual(self.request("/api/console/open", "POST", "{}", dict(self.headers, **change))[0], 403)
        missing = dict(self.headers); missing.pop("Origin")
        self.assertEqual(self.request("/api/console/open", "POST", "{}", missing)[0], 403)
        self.assertEqual(self.request("/api/console/bootstrap", headers=dict(self.headers, Upgrade="websocket"))[0], 403)
    def test_duplicate_security_headers_rejected(self):
        for name, value in (("Host", f"127.0.0.1:{self.server.server_port}"), ("Authorization", self.headers["Authorization"]), ("Origin", self.origin), ("X-Console-CSRF", self.console.csrf), ("Content-Length", "2")):
            conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
            try:
                conn.putrequest("POST", "/api/console/open")
                for key, header in self.headers.items(): conn.putheader(key, header)
                conn.putheader("Content-Length", "2")
                conn.putheader(name, value)
                conn.endheaders(b"{}")
                response = conn.getresponse()
                self.assertIn(response.status, (401, 403)); response.read()
            finally: conn.close()
    def test_input_never_dispatched_and_bad_bodies_are_bounded(self):
        for payload in ('{}', '{"sequence":1,"text":"public fixture"}', '{"sequence":1,"text":"public fixture"}'):
            self.assertEqual(self.request("/api/console/send", "POST", payload)[0], 403)
        for payload in ('{', '[]', '{"lease":[]}', '{"lease":"x","lease":"y"}', '[' * 1500, 'x' * 33000):
            self.assertEqual(self.request("/api/console/screen", "POST", payload)[0], 400)
        self.assertEqual(self.backend.sent, [])
    def test_http_lease_screen_close_and_headers(self):
        status, body, _ = self.request("/api/console/open", "POST", json.dumps({k: TARGET[k] for k in ("project", "run", "machine")}))
        self.assertEqual(status, 200); lease = json.loads(body)["lease"]
        status, body, headers = self.request("/api/console/screen", "POST", json.dumps(dict(lease=lease)))
        self.assertEqual(status, 200); self.assertIn(b"SYNTHETIC", body)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.request("/api/console/close", "POST", json.dumps(dict(lease=lease)))[0], 200)
        self.assertEqual(self.request("/api/console/screen", "POST", json.dumps(dict(lease=lease)))[0], 409)
        self.assertFalse((Path(self.temp.name) / "fixture.sqlite").exists())
    def test_default_off_sample_and_missing_routes(self):
        with self.assertRaises(ValueError): make_server(None, None, 0, sample=True, console_policy=POLICY)
        server = make_server(None, None, 0, sample=True)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start(); self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port)
        try:
            conn.request("POST", "/api/console/send", "{}"); self.assertEqual(conn.getresponse().status, 403)
        finally: conn.close()
        self.assertEqual(self.request("/unrelated", "POST", "{}")[0], 404)


@unittest.skipUnless(shutil.which("tmux") and sys.platform == "linux", "Isolated tmux lifecycle fixture requires Linux and tmux")
class RealTmuxFixtures(unittest.TestCase):
    def test_private_pty_lifecycle_capture_and_reused_pane_rejection(self):
        with tempfile.TemporaryDirectory(prefix="zudo-console-fixture-") as directory:
            root = Path(directory)
            conf = config(root); conf["tmux_socket"] = "zudo-fixture-" + os.urandom(8).hex()
            command = ["tmux", "-L", conf["tmux_socket"], "-f", "/dev/null"]
            # This is a disposable Python echo fixture, never an installed agent.
            script = root / "fixture.py"
            script.write_text("import ctypes,sys\nctypes.CDLL(None).prctl(15,b'codex',0,0,0)\nprint('SYNTHETIC PTY READY',flush=True)\nfor line in sys.stdin:\n print('FIXTURE:'+line.strip(),flush=True)\n")
            def tmux(*args):
                return subprocess.run(command + list(args), check=True, capture_output=True, text=True, timeout=5).stdout.strip()
            try:
                pane = tmux("new-session", "-d", "-P", "-F", "#{pane_id}", "-c", str(root), sys.executable, str(script))
                backend = PaneBackend(conf)
                end = time.monotonic() + 5
                targets = []
                while time.monotonic() < end:
                    targets = backend.targets()
                    if targets: break
                    time.sleep(.05)
                self.assertEqual(len(targets), 1)
                target = targets[0]
                self.assertIn("SYNTHETIC PTY READY", backend.screen(target))
                # Only the test harness exercises PTY input; the product rejects it.
                tmux("send-keys", "-t", pane, "-l", "public-fixture-line"); tmux("send-keys", "-t", pane, "Enter")
                end = time.monotonic() + 5
                while "FIXTURE:public-fixture-line" not in backend.screen(target) and time.monotonic() < end: time.sleep(.05)
                self.assertIn("FIXTURE:public-fixture-line", backend.screen(target))
                with self.assertRaises(ConsoleError): backend.send(target, "MUST-NOT-ARRIVE", True)
                self.assertNotIn("MUST-NOT-ARRIVE", backend.screen(target))
                tmux("respawn-pane", "-k", "-t", pane, sys.executable, str(script))
                with self.assertRaises(ConsoleError): backend.screen(target)
                tmux("kill-pane", "-t", pane)
                with self.assertRaises((ConsoleError, OSError)): backend.screen(target)
            finally:
                subprocess.run(command + ["kill-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
