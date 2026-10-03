"""Non-secret fixed dummy credentials and loopback-only isolated networking."""
import base64
import copy
import ctypes
import hashlib
import http.client
import json
import os
import platform
import sqlite3
import ssl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from zudo_agent import collector, native, setup
from zudo_agent.hub import HubStore, Rejected, make_hub_server, validate_registry
from zudo_agent.model import digest
from zudo_agent.store import Store
from zudo_agent.transport import Forwarder, MAX_BODY, MAX_RUNS, destination, frame_from_snapshot, read_token, send_frame

TOKEN_A = "a" * 64  # Public dummy fixtures, never production credentials.
TOKEN_B = "b" * 64
VIEWER = "c" * 64
REPO = "example.invalid/demo/project"


def registry_raw():
    hashed = lambda token: hashlib.sha256(token.encode()).hexdigest()
    return dict(projects=[dict(id="shared-project", repository=REPO)],
                devices=[dict(machine=name, stream="install-1", token_sha256=hashed(token), repositories=[REPO]) for name, token in [("device-a", TOKEN_A), ("device-b", TOKEN_B)]],
                viewer_sha256=hashed(VIEWER), allowed_hosts=["127.0.0.1", "hub.example.test"], stale_after=30, offline_after=90)


def frame(machine="device-a", seq=1, now=None, state="working"):
    now = time.time() if now is None else now
    return dict(schema_version=1, machine=machine, stream="install-1", sequence=seq, sampled_at=now,
                collector=dict(status="connected", checked_at=now, panes=1, unmatched_panes=0), omitted_runs=0,
                runs=[dict(repository=REPO, run_id=digest("same-public-fixture-run"), source="claude", state=state,
                           state_at=now, last_seen=now, reachability="present")])


class HubFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zudo-hub-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.registry = validate_registry(registry_raw())
        self.path = self.root / "hub.sqlite3"
        self.store = HubStore(self.path, self.registry)
        self.addCleanup(self.store.close)
        self.now = time.time()

    def test_two_devices_group_by_repo_and_namespace_same_run_id(self):
        self.store.ingest(frame(now=self.now), "device-a", self.now)
        self.store.ingest(frame("device-b", now=self.now), "device-b", self.now)
        snapshot = self.store.snapshot(self.now)
        self.assertEqual(len(snapshot["projects"]), 1)
        runs = snapshot["projects"][0]["runs"]
        self.assertEqual(len(runs), 2)
        self.assertEqual(len({r["id"] for r in runs}), 2)
        self.assertEqual({r["machine"] for r in runs}, {"device-a", "device-b"})
        self.assertEqual(snapshot["projects"][0]["completion"], "unknown")

    def test_unknown_stale_offline_and_replay_does_not_refresh_health(self):
        self.assertTrue(all(c["status"] == "unknown" for c in self.store.snapshot(self.now)["collectors"]))
        original = frame(now=self.now)
        self.store.ingest(original, "device-a", self.now)
        self.store.ingest(original, "device-a", self.now+500)
        self.assertEqual(self.store.snapshot(self.now+40)["collectors"][0]["status"], "stale")
        snap = self.store.snapshot(self.now+100)
        self.assertEqual(snap["collectors"][0]["status"], "offline")
        self.assertEqual(snap["projects"][0]["runs"][0]["state"], "working")
        self.assertEqual(snap["projects"][0]["runs"][0]["reachability"], "offline")

    def test_out_of_order_conflicting_duplicate_and_old_stream_rejected(self):
        latest = frame(seq=2, now=self.now)
        self.store.ingest(latest, "device-a", self.now)
        for invalid in [frame(seq=1, now=self.now), frame(seq=2, now=self.now, state="idle"), dict(latest, stream="old-install")]:
            with self.subTest(invalid=invalid), self.assertRaises(Rejected):
                self.store.ingest(invalid, "device-a", self.now)
        self.assertEqual(self.store.snapshot(self.now)["projects"][0]["runs"][0]["state"], "working")

    def test_stream_reset_requires_registration_and_namespaces_new_run(self):
        self.store.ingest(frame(now=self.now), "device-a", self.now)
        old_id = self.store.snapshot(self.now)["projects"][0]["runs"][0]["id"]
        self.registry["devices"]["device-a"]["stream"] = "install-2"
        self.assertFalse(self.store.snapshot(self.now)["projects"][0]["runs"])
        new = dict(frame(now=self.now), stream="install-2")
        self.store.ingest(new, "device-a", self.now)
        self.assertNotEqual(old_id, self.store.snapshot(self.now)["projects"][0]["runs"][0]["id"])

    def test_machine_spoofing_unknown_repo_and_completion_rejected(self):
        cases = [frame("device-b", now=self.now)]
        for change in [dict(repository="example.invalid/other/secret"), dict(state="completed"), dict(source="tmux", state="working")]:
            altered = frame(now=self.now)
            altered["runs"][0].update(change)
            cases.append(altered)
        for invalid in cases:
            with self.assertRaises(Rejected):
                self.store.ingest(invalid, "device-a", self.now)
        self.assertFalse(self.store.snapshot(self.now)["projects"][0]["runs"])

    def test_privacy_extra_fields_rejected_at_every_level(self):
        for target in ["frame", "run", "collector"]:
            invalid = frame(now=self.now)
            obj = invalid if target == "frame" else invalid["runs"][0] if target == "run" else invalid["collector"]
            obj["prompt"] = "PRIVATE-SYNTHETIC-MARKER"
            with self.assertRaises(Rejected):
                self.store.ingest(invalid, "device-a", self.now)
        self.assertNotIn("PRIVATE-SYNTHETIC-MARKER", json.dumps(self.store.snapshot(self.now)))

    def test_payload_run_count_and_timestamp_bounds(self):
        invalid = frame(now=self.now)
        invalid["runs"] *= MAX_RUNS+1
        with self.assertRaises(Rejected):
            self.store.ingest(invalid, "device-a", self.now)
        for timestamp in [float("nan"), float("inf"), time.time()+3600, True]:
            invalid = frame(now=self.now)
            invalid["sampled_at"] = timestamp
            with self.assertRaises(ValueError):
                self.store.ingest(invalid, "device-a", self.now)

    def test_registry_rejects_duplicate_tokens_and_identity(self):
        for variant in ["token", "identity", "viewer", "repository"]:
            raw = registry_raw()
            if variant == "token":
                raw["devices"][1]["token_sha256"] = raw["devices"][0]["token_sha256"]
            elif variant == "identity":
                raw["devices"][1]["machine"] = "device-a"
            elif variant == "viewer":
                raw["viewer_sha256"] = raw["devices"][0]["token_sha256"]
            else:
                raw["projects"].append(dict(id="duplicate", repository=REPO.upper()))
            with self.assertRaises(ValueError):
                validate_registry(raw)

    def server(self):
        server = make_hub_server(self.registry, self.path, port=0)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        return server

    def test_authenticated_http_two_devices_viewer_and_spoofing(self):
        server = self.server()
        url = f"http://127.0.0.1:{server.server_port}"
        for machine, token in [("device-a", TOKEN_A), ("device-b", TOKEN_B)]:
            token_file = self.root / (machine+".token")
            token_file.write_text(token)
            token_file.chmod(0o600)
            cfg = dict(hub_url=url, stream="install-1", token_file=str(token_file))
            self.assertTrue(send_frame(cfg, json.dumps(frame(machine, now=self.now)).encode())["ok"])
            self.assertFalse(send_frame(cfg, json.dumps(frame("spoof", seq=2, now=self.now)).encode())["ok"])
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/snapshot", headers={"Authorization": "Bearer "+TOKEN_A})
        self.assertEqual(connection.getresponse().status, 401)
        connection.close()
        auth = "Basic " + base64.b64encode(("viewer:"+VIEWER).encode()).decode()
        connection.request("GET", "/api/snapshot", headers={"Authorization": auth})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        snapshot = json.loads(response.read())
        self.assertEqual(len(snapshot["projects"][0]["runs"]), 2)
        self.assertNotIn(TOKEN_A, json.dumps(snapshot))
        self.assertNotIn(self.registry["viewer_sha256"], json.dumps(snapshot))
        connection.request("POST", "/api/ingest", body=b"{}", headers={"Authorization": auth, "Content-Type":"application/json"})
        self.assertEqual(connection.getresponse().status, 401)

    def test_http_limits_invalid_json_and_no_control_endpoint(self):
        server = self.server()
        def request(path, body, headers):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                conn.request("POST", path, body=body, headers=headers)
                res = conn.getresponse()
                res.read()
                return res.status
            finally:
                conn.close()
        headers = {"Authorization": "Bearer "+TOKEN_A, "Content-Type": "application/json"}
        self.assertEqual(request("/api/ingest", b"x"*(MAX_BODY+1), headers), 413)
        self.assertEqual(request("/api/ingest", b'{"machine":"a","machine":"b"}', headers), 409)
        self.assertEqual(request("/api/control", b"{}", headers), 404)
        self.assertEqual(request("/api/ingest", b"{}", dict(headers, Host="evil.example")), 404)

    def test_nonloopback_insecure_or_public_bind_rejected_before_listening(self):
        for bind in ["0.0.0.0", "8.8.8.8", "192.168.1.2", "100.64.0.2"]:
            with self.assertRaises(ValueError):
                make_hub_server(self.registry, self.path, bind=bind, port=0)

    def test_revoked_device_and_repository_are_hidden(self):
        self.store.ingest(frame(now=self.now), "device-a", self.now)
        del self.registry["devices"]["device-a"]
        snapshot = self.store.snapshot(self.now)
        self.assertFalse(snapshot["projects"][0]["runs"])


class ForwarderFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zudo-forward-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cfg = dict(machine="device-a", projects={"local-alias": dict(id="local-alias", repository=REPO, roots=[str(self.root)])}, stale_after=30,
                        tmux_socket=None, transport=dict(hub_url="http://127.0.0.1:9876", stream="install-1", token_file=str(self.root/"fixture.token")))
        self.path = self.root / "local.sqlite3"
        self.local = Store(self.path, self.cfg)
        self.addCleanup(self.local.close)
        self.local.ingest(dict(project_id="local-alias", run_id=digest("fixture"), machine="device-a", source="claude", kind="turn-stop", observed_at=time.time()))

    def test_retry_restart_sequence_and_no_sample_or_private_fields(self):
        sent = []
        def offline(_config, body):
            sent.append(body)
            return dict(ok=False, status="unreachable")
        first = Forwarder(self.local, self.cfg, offline)
        first.cycle(100)
        first.cycle(101)
        self.assertEqual(len(sent), 1)
        second = Forwarder(self.local, self.cfg, lambda _c, body: (sent.append(body) or dict(ok=True, status="connected")))
        second.cycle(200)
        self.assertEqual(sent[0], sent[1])
        second.cycle(206)
        self.assertEqual(json.loads(sent[2])["sequence"], 2)
        data = json.loads(sent[2])
        self.assertEqual(data["runs"][0]["repository"], REPO)
        self.assertNotIn("local-alias", sent[2].decode())
        self.assertNotIn(str(self.root), sent[2].decode())
        self.assertNotIn("recent_events", sent[2].decode())
        self.assertEqual(self.local.snapshot()["transport"]["status"], "connected")

    def test_single_persistent_pending_frame_during_long_outage(self):
        worker = Forwarder(self.local, self.cfg, lambda *_: dict(ok=False, status="unreachable"))
        for i in range(10):
            worker.cycle(1000+i*100)
        row = self.local.db.execute("SELECT sequence,COUNT(*) FROM outbox").fetchone()
        self.assertEqual(row, (1, 1))

    def test_plaintext_remote_public_and_url_credentials_refused(self):
        for url in ["http://192.168.1.2:8765", "http://100.64.0.2:8765", "https://8.8.8.8", "https://user:secret@hub.example", "https://hub.example/path", "http://localhost:8765"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                destination(url)
        self.assertEqual(destination("https://hub.example.ts.net:8765")[1], 8765)

    def test_dns_public_resolution_cannot_receive_token(self):
        cfg = dict(self.cfg["transport"], hub_url="https://hub.example.test")
        with patch("socket.getaddrinfo", return_value=[(2,1,6,"",("8.8.8.8",443))]), patch("socket.create_connection") as connect, self.assertRaises(ValueError):
            send_frame(cfg, json.dumps(frame()).encode())
        connect.assert_not_called()

    def test_tls_uses_pinned_private_address_verified_context_and_original_hostname(self):
        cfg = dict(self.cfg["transport"], hub_url="https://hub.example.test")
        sock, wrapped, ctx, conn = Mock(), Mock(), Mock(), Mock()
        ctx.wrap_socket.return_value = wrapped
        conn.getresponse.return_value.status = 200
        conn.getresponse.return_value.read.return_value = b'{"accepted":true,"sequence":1}'
        with patch("socket.getaddrinfo", return_value=[(2,1,6,"",("100.64.0.2",443))]), patch("socket.create_connection", return_value=sock) as connect, patch("ssl.create_default_context", return_value=ctx) as context, patch("zudo_agent.transport.read_token", return_value=TOKEN_A), patch("http.client.HTTPConnection", return_value=conn):
            self.assertTrue(send_frame(cfg, json.dumps(frame()).encode())["ok"])
        context.assert_called_once_with(cafile=None)
        connect.assert_called_once_with(("100.64.0.2", 443), timeout=5)
        ctx.wrap_socket.assert_called_once_with(sock, server_hostname="hub.example.test")
        self.assertEqual(conn.sock, wrapped)

    def test_token_permissions_symlink_and_content_restrictions(self):
        token = self.root / "fixture.token"
        token.write_text(TOKEN_A)
        token.chmod(0o600)
        self.assertEqual(read_token(token), TOKEN_A)
        token.chmod(0o644)
        with self.assertRaises(ValueError):
            read_token(token)
        token.chmod(0o600)
        link = self.root / "link.token"
        link.symlink_to(token)
        with self.assertRaises(ValueError):
            read_token(link)

    def test_untrusted_tls_certificate_never_sends_http_credentials(self):
        cfg = dict(self.cfg["transport"], hub_url="https://hub.example.test")
        ctx = Mock()
        ctx.wrap_socket.side_effect = ssl.SSLCertVerificationError("synthetic untrusted certificate")
        conn = Mock()
        with patch("socket.getaddrinfo", return_value=[(2,1,6,"",("192.168.1.20",443))]), patch("socket.create_connection", return_value=Mock()), patch("ssl.create_default_context", return_value=ctx), patch("zudo_agent.transport.read_token", return_value=TOKEN_A), patch("http.client.HTTPConnection", return_value=conn), self.assertRaises(ssl.SSLCertVerificationError):
            send_frame(cfg, json.dumps(frame()).encode())
        conn.request.assert_not_called()

    def test_snapshot_limit_is_explicit_and_drops_local_cloud_imports(self):
        snapshot = self.local.snapshot()
        base = snapshot["projects"][0]["runs"][0]
        snapshot["projects"][0]["runs"] = [dict(base, id=digest(i)) for i in range(MAX_RUNS+10)] + [dict(base, source="cloud-import")]
        result = frame_from_snapshot(snapshot, self.cfg, 1)
        self.assertLessEqual(len(result["runs"]), MAX_RUNS)
        self.assertEqual(len(result["runs"])+result["omitted_runs"], MAX_RUNS+10)
        self.assertTrue(all(r["source"] != "cloud-import" for r in result["runs"]))


class MacMetadataFixtures(unittest.TestCase):
    def test_libproc_structure_fields_and_pid_reuse(self):
        lib = Mock()
        self.assertIsNone(native.mac_process(0, lib))
        lib.proc_pidinfo.assert_not_called()
        def fake_info(pid, flavor, arg, buffer, size):
            self.assertEqual(flavor, 3)
            self.assertEqual(size, 136)
            info = ctypes.cast(buffer, ctypes.POINTER(native.BsdInfo)).contents
            info.pid, info.ppid, info.start_sec, info.start_usec = pid, 1, 1000, 20
            info.comm = b"claude"
            return size
        lib.proc_pidinfo.side_effect = fake_info
        value = native.mac_process(123, lib)
        self.assertEqual(value, dict(pid=123, parent=1, start="1000.000020", agent="claude"))
        a = collector.run_identity("fixture", "darwin-absolute-start", value)
        value["start"] = "1001.000020"
        self.assertNotEqual(a, collector.run_identity("fixture", "darwin-absolute-start", value))
        lib.proc_pidinfo.side_effect = None
        lib.proc_pidinfo.return_value = 0
        self.assertIsNone(native.mac_process(123, lib))

    def test_mac_process_inventory_and_partial_read(self):
        lib = Mock()
        def pids(buffer, size):
            if buffer is None:
                return 2
            buffer[0], buffer[1] = 11, 12
            return 2
        lib.proc_listallpids.side_effect = pids
        with patch("zudo_agent.native.mac_process", side_effect=[dict(pid=11), None]):
            self.assertEqual(native.mac_processes(lib), {11: dict(pid=11)})

    @unittest.skipUnless(platform.system() == "Darwin", "Native libproc smoke runs only on macOS CI")
    def test_native_mac_self_metadata_without_arguments(self):
        value = native.mac_process(os.getpid())
        self.assertEqual(value["pid"], os.getpid())
        self.assertEqual(value["parent"], os.getppid())
        self.assertGreater(float(value["start"]), 0)
        self.assertIn(os.getpid(), native.mac_processes())


class HubSetupFixtures(unittest.TestCase):
    def test_hub_registry_preview_apply_and_owned_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            candidate, target = root / "candidate.json", root / "hub.json"
            candidate.write_text(json.dumps(registry_raw()))
            plan = setup.hub_plan(target, candidate)
            self.assertFalse(target.exists())
            result = setup.apply_plan(plan, plan["approval"], root / "receipts")
            self.assertTrue(target.exists())
            registry = json.loads(target.read_text())
            registry["allowed_hosts"].append("later.example")
            target.write_text(json.dumps(registry))
            rollback = setup.rollback_plan(result["receipt"])
            self.assertTrue(rollback["warnings"])
            self.assertFalse(rollback["changes"])
            self.assertIn("later.example", target.read_text())

    def test_transport_preview_does_not_read_or_create_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project = root / "project"
            (project / ".git").mkdir(parents=True)
            config = root / "config.json"
            transport = dict(hub_url="https://hub.example.test:8765", stream="install-1", token_file=str(root/"not-provisioned.token"))
            env = dict(supported=True, agents={"codex": dict(supported=True, binary="/fixture/codex")}, tmux=dict(supported=True, binary="/fixture/tmux"))
            with patch("zudo_agent.setup.doctor", return_value=env), patch("zudo_agent.transport.read_token") as token:
                plan = setup.build_plan(project=project, project_id="fixture", repository=REPO, machine="device-a", config=config,
                                        db=root/"state.sqlite3", providers=["codex"], transport=transport)
                self.assertFalse(config.exists())
                setup.apply_plan(plan, plan["approval"], root/"receipts")
                token.assert_not_called()
            self.assertEqual(json.loads(config.read_text())["transport"], transport)
            self.assertFalse(Path(transport["token_file"]).exists())


if __name__ == "__main__":
    unittest.main()
