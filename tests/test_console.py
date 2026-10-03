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
TARGET = dict(project="example", run=digest("fixture"), machine="fixture", boot="fixture-boot", server=(123, "1"), pane="%0", root=(124, "2"), session="$0", id=digest("pane"), foreground="sh", cols=80, rows=24)


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
        return dict(screen="SYNTHETIC PANE ONLY <script>not executable</script>", foreground="sh", cols=80, rows=24)
    def send(self, *args):
        self.sent.append(args)
    def resize(self, *args):
        self.sent.append(args)


class ConsoleFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = Backend()
        self.now = 10
        self.console = Console(config(self.root), POLICY, self.backend, clock=lambda: self.now)
        self.addCleanup(self.console.shutdown)
    def opened(self):
        return self.console.handle("open", {k: TARGET[k] for k in ("project", "id", "machine")})
    def test_policy_private_file_and_explicit_control_flag(self):
        path = self.root / "policy.json"
        path.write_text(json.dumps(POLICY)); path.chmod(0o600)
        self.assertEqual(load_policy(path, config(self.root)), POLICY)
        link = self.root / "link"; link.symlink_to(path)
        with self.assertRaises((ValueError, OSError)): load_policy(link, config(self.root))
        path.chmod(0o644)
        with self.assertRaises(ValueError): load_policy(path, config(self.root))
        path.chmod(0o600); path.write_text(json.dumps(dict(POLICY, allow_input=True)))
        self.assertTrue(load_policy(path, config(self.root))["allow_input"])
        self.assertTrue(Console(config(self.root), dict(POLICY, allow_input=True)).policy["allow_input"])
    def test_send_always_refused_including_replays_and_bad_sequences(self):
        lease = self.opened()["lease"]
        for sequence in (1, 1, 0, 2, -1, True):
            with self.assertRaises(ConsoleError) as err:
                self.console.handle("send", dict(lease=lease, sequence=sequence, text="synthetic", enter=True))
            self.assertEqual(err.exception.status, 403)
        self.assertEqual(self.backend.sent, [])
    def test_stale_identity_at_each_generation_revokes_lease(self):
        for field in ("boot", "server", "pane", "root", "id", "machine", "project"):
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
            body = {k: TARGET[k] for k in ("project", "id", "machine")}; body[field] = "other"
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
    def test_input_requires_policy_and_human_enablement_and_consumes_sequence(self):
        self.console.policy = dict(POLICY, allow_input=True)
        lease = self.opened()["lease"]
        with self.assertRaises(ConsoleError): self.console.handle("send", dict(lease=lease, sequence=1, text="test"))
        self.console.handle("control", dict(lease=lease, enabled=True))
        self.assertEqual(self.console.handle("send", dict(lease=lease, sequence=1, text="日本語\r"))["sequence"], 2)
        self.assertEqual(self.backend.sent[0][1], "日本語\r")
        with self.assertRaises(ConsoleError): self.console.handle("send", dict(lease=lease, sequence=1, text="test"))
        self.assertNotIn(lease, self.console.leases)
        self.assertEqual(len(self.backend.sent), 1)

    def test_uncertain_delivery_never_replays_and_reconnect_starts_read_only(self):
        self.console.policy = dict(POLICY, allow_input=True)
        lease = self.opened()["lease"]
        self.console.handle("control", dict(lease=lease, enabled=True))
        def uncertain(*args):
            self.backend.sent.append(args)
            raise OSError("lost reply")
        with patch.object(self.backend, "send", side_effect=uncertain):
            for _ in range(2):
                with self.assertRaises(ConsoleError): self.console.handle("send", dict(lease=lease, sequence=1, text="once"))
        self.assertEqual(len(self.backend.sent), 1)
        new = self.opened()["lease"]
        with self.assertRaises(ConsoleError): self.console.handle("send", dict(lease=new, sequence=1, text="once"))

    def test_single_controller_and_expiry_cleanup(self):
        self.console.policy = dict(POLICY, allow_input=True)
        first, second = self.opened()["lease"], self.opened()["lease"]
        self.console.handle("control", dict(lease=first, enabled=True))
        with self.assertRaises(ConsoleError): self.console.handle("control", dict(lease=second, enabled=True))
        self.console.expire(first)
        self.console.handle("control", dict(lease=second, enabled=True))
        self.console.shutdown()
        self.assertEqual(self.console.leases, {})

    def test_input_and_resize_bounds_before_dispatch(self):
        self.console.policy = dict(POLICY, allow_input=True)
        lease = self.opened()["lease"]
        self.console.handle("control", dict(lease=lease, enabled=True))
        for text in ("", "x" * 4097, None, "\ud800"):
            with self.assertRaises((ValueError, UnicodeError)): self.console.handle("send", dict(lease=lease, sequence=1, text=text))
        for cols, rows in ((0, 24), (301, 24), (80, 201), (True, 24)):
            with self.assertRaises(ConsoleError): self.console.handle("resize", dict(lease=lease, sequence=1, cols=cols, rows=rows))
        self.assertEqual(self.backend.sent, [])
        self.console.handle("resize", dict(lease=lease, sequence=1, cols=100, rows=30))
        self.assertEqual(self.backend.sent[-1][1:], (100, 30))


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
        status, body, _ = self.request("/api/console/open", "POST", json.dumps({k: TARGET[k] for k in ("project", "id", "machine")}))
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
    def test_shell_continuation_input_resize_replacement_restart_and_literal_bytes(self):
        with tempfile.TemporaryDirectory(prefix="zudo-console-fixture-") as directory:
            root = Path(directory)
            conf = config(root); conf["tmux_socket"] = "zudo-fixture-" + os.urandom(8).hex()
            command = ["tmux", "-L", conf["tmux_socket"], "-f", "/dev/null"]
            # A synthetic agent-like child exits back to its existing shell.
            script = root / "fixture.py"
            script.write_text("import ctypes,sys\nctypes.CDLL(None).prctl(15,b'codex',0,0,0)\nprint('SYNTHETIC PTY READY',flush=True)\ninput()\n")
            def tmux(*args):
                return subprocess.run(command + list(args), check=True, capture_output=True, text=True, timeout=5).stdout.strip()
            backend = PaneBackend(conf)
            def wait_for(target, text):
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    screen = backend.screen(target)["screen"]
                    if text in screen: return screen
                    time.sleep(.05)
                self.fail("Fixture output deadline exceeded")
            try:
                pane = tmux("new-session", "-d", "-P", "-F", "#{pane_id}", "-c", str(root), "sh")
                target = backend.targets()[0]
                backend.connect(target)
                self.addCleanup(backend.disconnect, target)
                backend.send(target, sys.executable + " " + str(script) + "\r")
                wait_for(target, "SYNTHETIC PTY READY")
                active = backend.targets()[0]
                active["_channel"] = target["_channel"]
                self.assertIsNotNone(active["run"])
                self.assertEqual(target["id"], active["id"])
                backend.send(active, "\r")
                end = time.monotonic() + 5
                while backend.targets()[0]["run"] is not None and time.monotonic() < end: time.sleep(.05)
                self.assertIsNone(backend.targets()[0]["run"])
                backend.send(active, "printf 'SHELL-CONTINUATION-OK\\n'\r")
                wait_for(active, "SHELL-CONTINUATION-OK")
                tmux("split-window", "-h", "-t", pane, "sh")
                backend.resize(active, 50, 20)
                self.assertEqual(backend.screen(active)["cols"], 50)
                # Command-parser punctuation remains input bytes, never tmux commands.
                backend.send(active, "echo 'semi; percent% quote\"'\r")
                wait_for(active, "semi; percent% quote")
                backend.send(active, "printf '%%exit\\n%%begin fake\\n%%error fake\\n'\r")
                wait_for(active, "%error fake")
                self.assertIn("%exit", backend.screen(active)["screen"])
                original_validate = backend.validate
                def replace_after_validation(target, channel):
                    result = original_validate(target, channel)
                    tmux("respawn-pane", "-k", "-t", pane, "sh")
                    return result
                with patch.object(backend, "validate", side_effect=replace_after_validation):
                    with self.assertRaises(ConsoleError): backend.send(active, "RACE-MUST-NOT-ARRIVE\r")
                with self.assertRaises(ConsoleError): backend.screen(active)
                with self.assertRaises(ConsoleError): backend.send(active, "MUST-NOT-ARRIVE\r")
                replacement = backend.targets()[0]
                self.assertNotIn("MUST-NOT-ARRIVE", backend.screen(replacement)["screen"])
                backend.connect(replacement)
                self.addCleanup(backend.disconnect, replacement)
                tmux("kill-server")
                replacement["_channel"].process.wait(timeout=5)
                # kill-server can reply before its listening socket is gone.
                # Retry only disposable fixture STARTUP, never terminal input.
                deadline = time.monotonic() + 5
                while True:
                    try:
                        tmux("new-session", "-d", "-c", str(root), "sh")
                        break
                    except subprocess.CalledProcessError:
                        if time.monotonic() >= deadline: raise
                        time.sleep(.05)
                restarted = backend.targets()[0]
                self.assertNotEqual(restarted["server"], replacement["server"])
                self.assertEqual(restarted["pane"], replacement["pane"])
                with self.assertRaises((ConsoleError, OSError)): backend.send(replacement, "MUST-NOT-ARRIVE\r")
                self.assertNotIn("MUST-NOT-ARRIVE", backend.screen(restarted)["screen"])
            finally:
                subprocess.run(command + ["kill-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
