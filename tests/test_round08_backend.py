"""Focused round08 backend checks; no real credentials or user tmux sockets."""
import http.client
import hashlib
import base64
from contextlib import nullcontext
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

from zudo_agent.server import make_server
from zudo_agent.console import Console, ConsoleError, PaneBackend
from zudo_agent.hub import HubStore, make_hub_server, validate_registry
from zudo_agent.model import digest
from zudo_agent.store import Store
from zudo_agent.workflow import Workflow, known_runs, run_id


class WorkflowFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = dict(machine="fixture", tmux_socket="unique-fixture-only", stale_after=120,
                           projects={"example": dict(id="example", repository="github.com/example/test", roots=[self.tmp.name])})
        self.store = Store(Path(self.tmp.name) / "observations.sqlite", self.config)
        self.addCleanup(self.store.close)

    def test_alias_revisions_and_staged_discovery_preserve_explicit_edit_time(self):
        from unittest.mock import patch
        workflow = Workflow()
        first, second, canonical = (run_id("example", "fixture", x * 64) for x in "abc")
        with patch("zudo_agent.store.time.time", return_value=10):
            workflow.move(self.store, first, "review", 0)
        with patch("zudo_agent.store.time.time", return_value=20):
            workflow.move(self.store, second, "done", 0)
        with patch("zudo_agent.store.time.time", return_value=30):
            self.store.workflow_reconcile(canonical, [first])
        self.store.workflow_reconcile(canonical, [first, second])
        current = self.store.workflow_get(canonical)
        self.assertEqual(current["lane"], "done")
        self.assertGreater(current["revision"], 1)
        self.assertEqual(self.store.db.execute("SELECT edited FROM workflow WHERE id=?", (canonical,)).fetchone()[0], 20)
        updated, conflict = workflow.move(self.store, first, "inbox", 1)
        self.assertIsNone(updated)
        self.assertIn("identity changed", conflict["error"])
        self.assertEqual(self.store.workflow_get(canonical), current)
        self.store.workflow_reconcile(canonical, [first, second])
        self.assertEqual(self.store.workflow_get(canonical), current, "repeated discovery must not change revision")

    def test_latest_explicit_edit_survives_wall_clock_regression(self):
        from unittest.mock import patch
        first, second, canonical = (run_id("example", "fixture", x * 64) for x in "abc")
        with patch("zudo_agent.store.time.time", return_value=20):
            self.store.workflow_move(first, "review", 0)
        with patch("zudo_agent.store.time.time", return_value=10):
            self.store.workflow_move(second, "done", 0)
        self.store.workflow_reconcile(canonical, [first, second])
        self.assertEqual(self.store.workflow_get(canonical)["lane"], "done")

    def test_alias_created_after_authorization_cannot_redirect_a_write(self):
        workflow = Workflow()
        run, canonical = (run_id("example", "fixture", x * 64) for x in "ab")
        self.assertEqual(self.store.workflow_canonical(run), run)  # HTTP authorization snapshot.
        other = Store(Path(self.tmp.name) / "observations.sqlite", self.config)
        try:
            other.workflow_reconcile(canonical, [run])
        finally:
            other.close()
        updated, conflict = workflow.move(self.store, run, "done", 0)
        self.assertIsNone(updated)
        self.assertIn("identity changed", conflict["error"])
        self.assertEqual(self.store.workflow_get(canonical), {"lane": "inbox", "revision": 0})

    def test_revision_conflict_alias_and_separate_observations(self):
        workflow = Workflow()
        key = run_id("example", "fixture", "a" * 64)
        before = self.store.snapshot(now=1234)
        updated, conflict = workflow.move(self.store, key, "review", 0)
        self.assertIsNone(conflict)
        self.assertEqual(updated, {"lane": "review", "revision": 1})
        updated, conflict = workflow.move(self.store, key, "done", 0)
        self.assertIsNone(updated)
        self.assertEqual(conflict, {"lane": "review", "revision": 1})
        canonical = run_id("example", "fixture", "b" * 64)
        self.store.workflow_reconcile(canonical, [key])
        self.assertEqual(self.store.workflow_canonical(key), canonical)
        self.assertEqual(self.store.workflow_get(canonical)["lane"], "review")
        self.assertEqual(self.store.snapshot(now=1234), before)

    def test_stale_observation_keeps_manual_lane_but_ended_does_not(self):
        snapshot = dict(projects=[dict(id="example", runs=[
            dict(id="a", machine="fixture", state="working", freshness="stale", reachability="disconnected"),
            dict(id="b", machine="fixture", state="ended", freshness="fresh", reachability="present")])])
        current = known_runs(snapshot, current=True)
        self.assertIn(run_id("example", "fixture", "a"), current)
        self.assertNotIn(run_id("example", "fixture", "b"), current)

    def test_sample_workflow_origin_csrf_and_duplicate_header(self):
        server = make_server(None, None, port=0, sample=True)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        authority = f"127.0.0.1:{server.server_port}"
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", "/api/workflow")
        result = conn.getresponse()
        self.assertEqual(result.status, 200)
        data = json.loads(result.read())
        self.assertEqual(data["lanes"], ["inbox", "progress", "review", "done"])
        payload = b'{"id":"' + b"a" * 64 + b'","lane":"review","revision":0}'
        headers = {"Origin": f"http://{authority}", "Content-Type": "application/json", "X-Workflow-CSRF": data["csrf"]}
        conn.request("POST", "/api/workflow", payload, dict(headers, Origin="https://evil.example"))
        response = conn.getresponse()
        self.assertEqual(response.status, 403)
        response.read()
        conn.request("POST", "/api/workflow", payload, headers)
        response = conn.getresponse()
        self.assertEqual(response.status, 409)  # Unknown IDs never create metadata.
        response.read()

    def test_live_workflow_http_success_conflict_and_validation(self):
        run = digest("fixture-run")
        self.store.ingest(dict(project_id="example", run_id=run, machine="fixture", source="codex",
                               kind="working", observed_at=time.time()))
        server = make_server(self.config, Path(self.tmp.name) / "observations.sqlite", port=0)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        authority = f"127.0.0.1:{server.server_port}"

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request(method, path, body, headers or {})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        status, data = request("GET", "/api/workflow")
        self.assertEqual(status, 200)
        key = data["run_keys"][0]["id"]
        self.assertEqual(data["items"][key], {"lane": "inbox", "revision": 0})
        headers = {"Origin": f"http://{authority}", "Content-Type": "application/json",
                   "X-Workflow-CSRF": data["csrf"]}
        body = json.dumps(dict(id=key, lane="progress", revision=0))
        self.assertEqual(request("POST", "/api/workflow", body, headers),
                         (200, dict(id=key, lane="progress", revision=1)))
        self.assertEqual(request("GET", "/api/workflow")[1]["items"][key],
                         {"lane": "progress", "revision": 1})
        self.assertEqual(request("POST", "/api/workflow", body, headers),
                         (409, dict(id=key, lane="progress", revision=1)))
        self.assertEqual(request("POST", "/api/workflow", body.replace("progress", "bogus"), headers)[0], 400)
        self.assertEqual(request("POST", "/api/workflow", body, dict(headers, Origin="https://evil.test"))[0], 403)
        self.assertEqual(request("POST", "/api/workflow", body, dict(headers, Host="evil.test"))[0], 403)
        self.assertEqual(request("POST", "/api/workflow", body, dict(headers, **{"X-Workflow-CSRF": "wrong"}))[0], 403)
        self.assertEqual(self.store.snapshot(now=1234)["projects"][0]["runs"][0]["state"], "working")

        for duplicate in ("Host", "Origin", "X-Workflow-CSRF"):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.putrequest("POST", "/api/workflow", skip_host=True)
                connection.putheader("Host", authority)
                for name, value in headers.items():
                    connection.putheader(name, value)
                connection.putheader("Content-Length", str(len(body)))
                connection.putheader(duplicate, authority if duplicate == "Host" else headers[duplicate])
                connection.endheaders(body.encode())
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
            finally:
                connection.close()

    def test_console_workflow_requires_basic_and_exact_workflow_csrf(self):
        run = digest("authenticated-workflow-fixture")
        canonical = digest("authenticated-session-workflow-fixture")
        self.store.ingest(dict(project_id="example", run_id=run, machine="fixture", source="codex",
                               kind="working", observed_at=time.time()))
        target = dict(project="example", machine="fixture", id="pane-fixture", session_id="session-fixture",
                      workflow_id=canonical, run=run, pane="%1", server=(123, 1.0),
                      foreground="sh", cols=80, rows=24)
        secret = "PUBLIC-CONSOLE-WORKFLOW-FIXTURE-SECRET"
        password_sha256 = hashlib.sha256(secret.encode()).hexdigest()
        policy = dict(identity="fixture", password_sha256=password_sha256, projects=["example"], allow_input=False)

        class FakeConsole:
            def __init__(self, *_args):
                self.csrf = "console-csrf-fixture"
                self.policy = policy

            def authorized(self, headers):
                return headers.get("Authorization") == "Basic " + base64.b64encode(
                    f"fixture:{secret}".encode()).decode()

            def handle(self, action, _body):
                if action == "targets":
                    return {"targets": [target]}
                raise AssertionError(f"Unexpected console action: {action}")

            def shutdown(self):
                pass

        with patch("zudo_agent.console.Console", FakeConsole):
            server = make_server(self.config, Path(self.tmp.name) / "observations.sqlite", port=0,
                                 console_policy=policy)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        authority = f"127.0.0.1:{server.server_port}"
        authorization = "Basic " + base64.b64encode(f"fixture:{secret}".encode()).decode()

        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request(method, path, body, headers or {})
                response = connection.getresponse()
                payload = response.read()
                return response.status, json.loads(payload) if payload else None
            finally:
                connection.close()

        self.assertEqual(request("GET", "/api/console/workflow")[0], 401)
        status, open_data = request("GET", "/api/workflow")
        self.assertEqual(status, 200)
        self.assertEqual(open_data["sessions"], [], "unauthenticated workflow reads disclose no session targets")
        self.assertNotIn(canonical, open_data["items"])

        status, data = request("GET", "/api/console/workflow", headers={"Authorization": authorization})
        self.assertEqual(status, 200)
        self.assertEqual(data["sessions"], [dict(id=canonical, session_id="session-fixture",
            project="example", machine="fixture", panes=["pane-fixture"], runs=[run])])
        self.assertEqual(data["run_keys"][0]["canonical"], canonical)
        self.assertEqual(data["items"][canonical], {"lane": "inbox", "revision": 0})

        body = json.dumps(dict(id=canonical, lane="review", revision=0))
        headers = {"Authorization": authorization, "Origin": f"http://{authority}",
                   "Content-Type": "application/json", "X-Workflow-CSRF": data["csrf"]}
        self.assertEqual(request("POST", "/api/console/workflow", body, dict(headers, Authorization="Basic invalid"))[0], 401)
        self.assertEqual(request("POST", "/api/console/workflow", body, dict(headers, Origin="https://evil.test"))[0], 403)
        self.assertEqual(request("POST", "/api/console/workflow", body,
                                 dict(headers, **{"X-Workflow-CSRF": "wrong"}))[0], 403)
        self.assertEqual(request("POST", "/api/console/workflow", body, headers),
                         (200, dict(id=canonical, lane="review", revision=1)))

        # The unauthenticated observation route remains available and distinct;
        # it cannot mutate the canonical authenticated session identifier.
        self.assertEqual(request("POST", "/api/workflow", body,
            {"Origin": f"http://{authority}", "Content-Type": "application/json",
             "X-Workflow-CSRF": data["csrf"]})[0], 409)


class ConsolePreviewFixture(unittest.TestCase):
    def test_preview_scope_stale_and_control_off(self):
        target = dict(project="example", machine="fixture", id=digest("pane"), pane="%0",
                      session="$0", session_created="1", boot="fixture-boot", server=(123, 1),
                      root=(124, 2), run=None, foreground="sh", cols=80, rows=24)

        class Backend:
            current = [target]
            def targets(self):
                return self.current
            def screen(self, selected):
                if selected not in self.current:
                    raise ConsoleError(409, "stale")
                return dict(screen="fixture capture", lines=1, limit=500, truncated=False,
                            byte_truncated=False, foreground="sh", cols=80, rows=24)

        backend = Backend()
        console = Console(dict(machine="fixture"), dict(projects=["example"], allow_input=False), backend)
        body = dict(project="example", machine="fixture", id=target["id"])
        self.assertEqual(console.handle("preview", body)["screen"], "fixture capture")
        self.assertEqual(console.leases, {})
        for field in ("project", "machine", "id"):
            with self.assertRaises(ConsoleError):
                console.handle("preview", dict(body, **{field: "other"}))
        backend.current = []
        with self.assertRaises(ConsoleError) as error:
            console.handle("preview", body)
        self.assertEqual(error.exception.status, 409)

    def test_preview_http_requires_basic_origin_and_console_csrf(self):
        password = "fixture-public-password"
        policy = dict(identity="fixture", password_sha256=hashlib.sha256(password.encode()).hexdigest(),
                      projects=["example"], allow_input=False)
        target = dict(project="example", machine="fixture", id=digest("pane"), pane="%0",
                      session="$0", session_created="1", boot="fixture-boot", server=(123, 1),
                      root=(124, 2), run=None, foreground="sh", cols=80, rows=24)

        class Backend:
            def targets(self):
                return [target]
            def preview(self, _target):
                return dict(screen="fixture", lines=1, limit=500, truncated=False, byte_truncated=False,
                            foreground="sh", cols=80, rows=24)
            def screen(self, _target):
                raise AssertionError("Preview must use bounded preview path")

        console = Console(dict(machine="fixture"), policy, Backend())
        with tempfile.TemporaryDirectory(prefix="zudo-round08-preview-") as folder:
            config = dict(machine="fixture", tmux_socket="disposable-fixture-socket", stale_after=120,
                          projects={"example": dict(id="example", repository="github.com/example/test", roots=[folder])})
            with patch("zudo_agent.console.Console", return_value=console):
                server = make_server(config, Path(folder) / "observations.sqlite", port=0, console_policy=policy)
            try:
                threading.Thread(target=server.serve_forever, daemon=True).start()
                authority = f"127.0.0.1:{server.server_port}"
                auth = "Basic " + base64.b64encode(("fixture:" + password).encode()).decode()
                body = json.dumps({k: target[k] for k in ("project", "machine", "id")})

                def request(headers):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    try:
                        connection.request("POST", "/api/console/preview", body, headers)
                        response = connection.getresponse()
                        return response.status, json.loads(response.read()) if response.status != 401 else None
                    finally:
                        connection.close()

                headers = {"Authorization": auth, "Origin": "http://" + authority,
                           "Content-Type": "application/json", "X-Console-CSRF": console.csrf}
                self.assertEqual(request(dict(headers, Authorization="Basic invalid"))[0], 401)
                self.assertEqual(request(dict(headers, Origin="https://evil.test"))[0], 403)
                self.assertEqual(request(dict(headers, **{"X-Console-CSRF": "wrong"}))[0], 403)
                status, data = request(headers)
                self.assertEqual(status, 200)
                self.assertEqual((data["screen"], data["limit"]), ("fixture", 500))
                self.assertEqual(console.leases, {})
            finally:
                server.shutdown()
                server.server_close()

    def test_project_change_after_capture_discards_preview(self):
        backend = PaneBackend(dict(machine="fixture", tmux_socket="disposable-fixture-socket"))
        target = dict(project="example", machine="fixture", pane="%0", id=digest("pane"))

        class Channel:
            def command(self, command):
                return "secret fixture output" if command.startswith("capture-pane") else "metadata"

        checks = iter([None, ConsoleError(409, "Pane left its authorized project")])
        def check(*_args):
            outcome = next(checks)
            if outcome:
                raise outcome
        with patch.object(backend, "connection", return_value=nullcontext(Channel())), \
             patch.object(backend, "validate", return_value=dict(foreground="sh", cols=80, rows=24)), \
             patch.object(backend, "_validate_project", side_effect=check):
            with self.assertRaises(ConsoleError) as error:
                backend.preview(target)
        self.assertEqual(error.exception.status, 409)


class HubWorkflowFixture(unittest.TestCase):
    def test_viewer_only_origin_csrf_and_conflict(self):
        with tempfile.TemporaryDirectory(prefix="zudo-round08-hub-") as folder:
            token = "c" * 64  # Public dummy fixture.
            collector = "a" * 64
            repository = "example.invalid/demo/project"
            registry = validate_registry(dict(projects=[dict(id="example", repository=repository)],
                devices=[dict(machine="device", stream="fixture", token_sha256=hashlib.sha256(collector.encode()).hexdigest(), repositories=[repository])],
                viewer_sha256=hashlib.sha256(token.encode()).hexdigest(), allowed_hosts=["127.0.0.1"],
                stale_after=30, offline_after=90))
            path = Path(folder) / "hub.sqlite"
            now = time.time()
            store = HubStore(path, registry)
            store.ingest(dict(schema_version=1, machine="device", stream="fixture", sequence=1, sampled_at=now,
                collector=dict(status="connected", checked_at=now, panes=0, unmatched_panes=0), omitted_runs=0,
                runs=[dict(repository=repository, run_id=digest("fixture"), source="codex", state="working",
                           state_at=now, last_seen=now, reachability="present")]), "device")
            store.close()
            server = make_hub_server(registry, path, port=0)
            try:
                threading.Thread(target=server.serve_forever, daemon=True).start()
                authority = f"127.0.0.1:{server.server_port}"
                viewer = "Basic " + base64.b64encode(("viewer:" + token).encode()).decode()

                def request(method, route, body=None, headers=None):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    try:
                        connection.request(method, route, body, headers or {})
                        response = connection.getresponse()
                        return response.status, json.loads(response.read())
                    finally:
                        connection.close()

                self.assertEqual(request("GET", "/api/workflow", headers={"Authorization": "Bearer " + collector})[0], 401)
                status, data = request("GET", "/api/workflow", headers={"Authorization": viewer})
                self.assertEqual(status, 200)
                self.assertEqual(data["sessions"], [])
                key = data["run_keys"][0]["id"]
                headers = {"Authorization": viewer, "Origin": "http://" + authority,
                           "Content-Type": "application/json", "X-Workflow-CSRF": data["csrf"]}
                body = json.dumps(dict(id=key, lane="review", revision=0))
                self.assertEqual(request("POST", "/api/workflow", body, dict(headers, Origin="https://evil.test"))[0], 403)
                self.assertEqual(request("POST", "/api/workflow", body, dict(headers, Authorization="Bearer " + collector))[0], 401)
                self.assertEqual(request("POST", "/api/workflow", body, headers)[0], 200)
                self.assertEqual(request("POST", "/api/workflow", body, headers)[0], 409)
            finally:
                server.shutdown()
                server.server_close()



@unittest.skipUnless(shutil.which("tmux") and sys.platform == "linux", "Disposable Linux tmux fixture")
class CaptureFixture(unittest.TestCase):
    def test_recent_history_is_bounded_to_500_lines(self):
        with tempfile.TemporaryDirectory(prefix="zudo-round08-capture-") as folder:
            socket = "zudo-round08-" + os.urandom(8).hex()
            command = ["tmux", "-L", socket, "-f", "/dev/null"]
            config = dict(machine="fixture", tmux_socket=socket, stale_after=120,
                          projects={"example": dict(id="example", repository="github.com/example/test", roots=[folder])})
            try:
                subprocess.run(command + ["new-session", "-d", "-c", folder,
                            "sh -c 'seq -f LINE-%04g 1 650; sleep 30'"], check=True, timeout=5)
                backend = PaneBackend(config)
                target = backend.targets()[0]
                output = backend.screen(target)
                preview = backend.preview(target)
                self.assertLessEqual(output["lines"], 500)
                self.assertLessEqual(preview["lines"], 500)
                self.assertEqual(output["limit"], 500)
                self.assertIn("LINE-0650", output["screen"])
                self.assertNotIn("LINE-0001", output["screen"])
                self.assertTrue(output["truncated"])
            finally:
                subprocess.run(command + ["kill-server"], capture_output=True, timeout=5)


if __name__ == "__main__":
    unittest.main()
