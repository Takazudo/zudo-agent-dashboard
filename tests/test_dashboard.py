import contextlib
import copy
import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from zudo_agent import cli
from zudo_agent.collector import agent_descendant, discover, hook_event, parse_panes, run_identity
from zudo_agent.model import digest, load_config, validate_event
from zudo_agent.server import make_server
from zudo_agent.store import Store, import_cloud

NOW = 1_700_000_000.0


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.config = dict(machine="test-machine", tmux_socket=None, stale_after=120,
                           projects={"example": dict(id="example", repository="github.com/example/project", roots=[str(self.root)])})
        self.db = self.root / "state.sqlite3"
        self.store = Store(self.db, self.config)
        self.addCleanup(self.store.close)

    def event(self, kind="working", at=NOW, **kwargs):
        return dict(project_id="example", run_id=digest("synthetic-run"), machine="test-machine",
                    source="claude", kind=kind, observed_at=at, **kwargs)

    def run_state(self, now=NOW):
        return self.store.snapshot(now)["projects"][0]["runs"][0]

    def test_dedup_and_out_of_order(self):
        event = self.event("turn-stop", NOW)
        self.assertEqual(self.store.ingest(event), 1)
        self.assertEqual(self.store.ingest(copy.deepcopy(event)), 0)
        self.store.ingest(self.event("working", NOW - 10))
        self.assertEqual(self.run_state()["state"], "idle")
        self.assertEqual(len(self.run_state()["recent_events"]), 2)

    def test_turn_stop_does_not_complete_project(self):
        self.store.ingest(self.event("turn-stop"))
        snapshot = self.store.snapshot(NOW)
        self.assertEqual(snapshot["projects"][0]["completion"], "unknown")
        self.assertEqual(self.run_state()["state"], "idle")

    def test_transient_error_recovers(self):
        self.store.ingest(self.event("error-observed", NOW-1))
        self.store.ingest(self.event("working"))
        self.assertEqual(self.run_state()["state"], "working")

    def test_conflicting_timestamp_is_unknown(self):
        self.store.ingest_many([self.event("working"), self.event("turn-stop")])
        self.assertEqual(self.run_state()["state"], "unknown")

    def test_stale_state_independent_of_presence(self):
        event = self.event("discovered", NOW+200)
        event["source"] = "tmux"
        self.store.ingest(self.event())
        self.store.discovery([event], True, 1, 0, NOW+200)
        run = self.run_state(NOW+200)
        self.assertEqual(run["freshness"], "fresh")
        self.assertEqual(run["state_freshness"], "stale")
        self.assertEqual(run["reachability"], "present")

    def test_disconnection_does_not_end_runs(self):
        event = self.event("discovered")
        event["source"] = "tmux"
        self.store.discovery([event], True, 1, 0, NOW)
        self.store.discovery([], False, 0, 0, NOW+1)
        self.assertEqual(self.run_state(NOW+1)["reachability"], "disconnected")
        self.assertEqual(self.run_state(NOW+1)["state"], "unknown")
        self.assertEqual(self.run_state(NOW+200)["reachability"], "stale")

    def test_absence_is_not_completion(self):
        event = self.event("discovered")
        event["source"] = "tmux"
        self.store.discovery([event], True, 1, 0, NOW)
        self.store.discovery([], True, 0, 0, NOW+1)
        self.assertEqual(self.run_state(NOW+1)["reachability"], "absent")
        self.assertEqual(self.run_state(NOW+1)["state"], "unknown")

    def test_strict_privacy_boundary(self):
        for field in ["prompt", "transcript", "command", "arguments", "token", "cwd"]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_event(self.event(**{field: "SYNTHETIC-SECRET"}), self.config["projects"])
        self.store.ingest(self.event())
        output = json.dumps(self.store.snapshot(NOW))
        self.assertNotIn(str(self.root), output)
        self.assertNotIn("synthetic-run", output)

    def test_invalid_timestamps_and_completion(self):
        for timestamp in [float("nan"), float("inf"), True, -1, time.time()+3600]:
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                self.store.ingest(self.event(at=timestamp))
        with self.assertRaises(ValueError):
            self.store.ingest(self.event("completed"))

    def test_import_atomic_validation_and_replay(self):
        event = self.event("completed")
        event["source"] = "cloud-import"
        path = self.root / "cloud.json"
        path.write_text(json.dumps(dict(schema_version=1, events=[event, self.event(prompt="SYNTHETIC-SECRET")])))
        with self.assertRaises(ValueError):
            import_cloud(path, self.store)
        self.assertFalse(self.store.snapshot(NOW)["projects"][0]["runs"])
        path.write_text(json.dumps(dict(schema_version=1, events=[event])))
        self.assertEqual(import_cloud(path, self.store), 1)
        self.assertEqual(import_cloud(path, self.store), 0)
        self.assertEqual(self.run_state()["state"], "completed")
        self.assertFalse(self.store.snapshot(NOW)["cloud"]["live_connected"])

    def test_hook_filters_payload_and_subagents(self):
        raw = dict(session_id="synthetic-private-id", cwd=str(self.root), hook_event_name="Stop",
                   prompt="SYNTHETIC-SECRET", transcript_path="/private/transcript", tool_input={"command": "secret args"})
        with patch("zudo_agent.collector.process", return_value=None):
            event = hook_event(raw, "claude", self.config, NOW)
        self.assertEqual(event["kind"], "turn-stop")
        self.assertNotIn("SYNTHETIC-SECRET", json.dumps(event))
        self.assertNotIn("synthetic-private-id", json.dumps(event))
        self.assertEqual(set(event), {"project_id", "run_id", "machine", "source", "kind", "observed_at"})
        raw["agent_id"] = "child"
        self.assertIsNone(hook_event(raw, "codex", self.config, NOW))

    def test_named_claude_main_preserves_identity_lifecycle_and_privacy(self):
        proc = dict(pid=100, parent=1, start="10", agent="claude")
        raw = dict(session_id="synthetic-private-id", cwd=str(self.root),
                   agent_type="synthetic-private-agent-name", prompt="SYNTHETIC-SECRET")
        with patch("zudo_agent.collector.process", return_value=proc), patch("zudo_agent.collector.boot_id", return_value="fake-boot"):
            for offset, (name, state) in enumerate([("SessionStart", "unknown"), ("UserPromptSubmit", "working"), ("Stop", "idle")]):
                with self.subTest(event=name):
                    payload = dict(raw, hook_event_name=name)
                    event = hook_event(payload, "claude", self.config, NOW+offset)
                    ordinary = dict(payload)
                    del ordinary["agent_type"]
                    self.assertEqual(event, hook_event(ordinary, "claude", self.config, NOW+offset))
                    self.assertEqual(event["run_id"], run_identity("test-machine", "fake-boot", proc))
                    self.assertEqual(set(event), {"project_id", "run_id", "machine", "source", "kind", "observed_at"})
                    self.assertNotIn("synthetic-private", json.dumps(event))
                    self.assertNotIn("SYNTHETIC-SECRET", json.dumps(event))
                    self.store.ingest(event)
                    self.assertEqual(self.run_state(NOW+offset)["state"], state)
                    self.assertEqual(self.store.snapshot(NOW+offset)["projects"][0]["completion"], "unknown")

    def test_subagent_id_rejected_with_or_without_agent_type(self):
        for provider in ["claude", "codex"]:
            for extra in [{}, {"agent_type": "fixture-child-profile"}]:
                with self.subTest(provider=provider, extra=extra):
                    raw = dict(session_id="fixture-parent", cwd=str(self.root), hook_event_name="Stop", agent_id="fixture-child", **extra)
                    with patch("zudo_agent.collector.process") as lookup:
                        self.assertIsNone(hook_event(raw, provider, self.config, NOW))
                        lookup.assert_not_called()

    def test_unrecognized_question_is_not_inferred(self):
        raw = dict(session_id="test", cwd=str(self.root), hook_event_name="Notification", message="Can you choose an option?")
        self.assertIsNone(hook_event(raw, "claude", self.config, NOW))

    def test_hook_no_output_or_decision_even_on_failure(self):
        fake_input = type("Input", (), {"buffer": io.BytesIO(b'{"hook_event_name":"Stop"}')})()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["zudo-agent", "--config", "missing-config", "hook", "claude"]), patch("sys.stdin", fake_input), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            cli.main()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_all_new_sessions_windows_panes_and_restart(self):
        frame = "50\t$1\t@1\t%1\t100\t0\t/tmp/example\n50\t$2\t@9\t%8\t200\t0\t/tmp/example"
        table = {100: dict(pid=100, parent=1, start="10", agent="claude"),
                 200: dict(pid=200, parent=1, start="20", agent="codex")}
        with patch("zudo_agent.collector.command", return_value=frame) as cmd, patch("zudo_agent.collector.processes", return_value=table), patch("zudo_agent.collector.boot_id", return_value="fake-boot"), patch("zudo_agent.collector.project_for", return_value="example"):
            self.assertEqual(discover(self.config, self.store, NOW)["observed_runs"], 2)
            self.assertIn("-a", cmd.call_args.args[0])
            table[100]["start"] = "30"
            discover(self.config, self.store, NOW+1)
        runs = self.store.snapshot(NOW+1)["projects"][0]["runs"]
        self.assertEqual(len(runs), 3)
        self.assertEqual(sum(r["reachability"] == "absent" for r in runs), 1)
        self.assertNotIn("/tmp/example", json.dumps(runs))

    def test_tmux_failure_preserves_previous(self):
        with patch("zudo_agent.collector.command", side_effect=subprocess.TimeoutExpired("tmux", 3)):
            result = discover(self.config, self.store, NOW)
        self.assertEqual(result["status"], "disconnected")

    def test_presence_heartbeats_coalesce_without_erasing_lifecycle(self):
        self.store.ingest(self.event("turn-stop", NOW-1))
        for offset in range(20):
            event = self.event("discovered", NOW+offset)
            event["source"] = "tmux"
            self.store.discovery([event], True, 1, 0, NOW+offset)
        self.store.discovery([], False, 0, 0, NOW-1)
        run = self.run_state(NOW+20)
        self.assertEqual(run["state"], "idle")
        self.assertEqual(run["reachability"], "present")
        self.assertEqual(len(run["recent_events"]), 2)

    def test_hook_only_run_does_not_claim_tmux_presence(self):
        self.store.ingest(self.event())
        self.store.discovery([], True, 10, 10, NOW)
        self.assertEqual(self.run_state()["reachability"], "not-observed")

    def test_later_scan_discovers_new_pane(self):
        first = "50\t$1\t@1\t%1\t100\t0\t/tmp/example"
        second = first + "\n50\t$2\t@2\t%2\t200\t0\t/tmp/example"
        table = {100: dict(pid=100, parent=1, start="10", agent="claude"),
                 200: dict(pid=200, parent=1, start="20", agent="codex")}
        with patch("zudo_agent.collector.command", side_effect=[first, second]), patch("zudo_agent.collector.processes", return_value=table), patch("zudo_agent.collector.boot_id", return_value="fake-boot"), patch("zudo_agent.collector.project_for", return_value="example"):
            self.assertEqual(discover(self.config, self.store, NOW)["observed_runs"], 1)
            self.assertEqual(discover(self.config, self.store, NOW+1)["observed_runs"], 2)

    def test_linked_worktree_groups_under_explicit_repository(self):
        from zudo_agent.collector import project_for
        repo = self.root / "repo"
        worktree = self.root / "linked"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "fixture"], check=True)
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", "-q", str(worktree)], check=True)
        self.config["projects"]["example"]["roots"] = [str(repo)]
        self.assertEqual(project_for(str(worktree), self.config), "example")

    def test_process_identity_and_ambiguity(self):
        root = dict(pid=10, parent=1, start="123", agent=None)
        child = dict(pid=11, parent=10, start="124", agent="claude")
        table = {10: root, 11: child}
        self.assertEqual(agent_descendant(10, table), child)
        self.assertNotEqual(run_identity("m", "boot", child), run_identity("m", "next-boot", child))
        table[12] = dict(pid=12, parent=10, start="125", agent="codex")
        self.assertIsNone(agent_descendant(10, table))

    def test_malformed_metadata_rejected(self):
        with self.assertRaises(ValueError):
            parse_panes("invalid frame")

    def test_config_rejects_credentials_and_duplicate_identity(self):
        path = self.root / "config.json"
        project = dict(id="example", repository="https://user:secret@github.com/example/repo", roots=[str(self.root)])
        data = dict(machine="test", projects=[project])
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            load_config(path)
        project["repository"] = "github.com/example/repo"
        data["projects"].append(dict(project, id="other"))
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            load_config(path)

    def test_http_loopback_get_only_sample_isolation_and_host(self):
        server = make_server(None, None, 0, sample=True)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/api/snapshot") as response:
            data = json.load(response)
            self.assertEqual(data["mode"], "sample")
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        for request, status in [(urllib.request.Request(base, method="POST", data=b"{}"), 501),
                                (urllib.request.Request(base, headers={"Host": "evil.example"}), 403),
                                (urllib.request.Request(base+"/../config.local.json"), 404)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, status)


if __name__ == "__main__":
    unittest.main()
