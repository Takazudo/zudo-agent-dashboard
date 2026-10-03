"""All setup mutations are confined to disposable fixtures, never real agent settings."""
import copy
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zudo_agent import setup
from zudo_agent.model import load_config
from zudo_agent.store import Store


class SetupFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zudo setup fixture ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project with spaces"
        (self.project / ".git").mkdir(parents=True)
        self.config = self.root / "private config.json"
        self.db = self.root / "private state.sqlite3"
        self.receipts = self.root / "private receipts"
        self.env = dict(os="Linux", wsl=True, supported=True, python="3.12.3", collector="linux-proc",
                        tmux=dict(binary="/fixture/tmux", version="3.4", supported=True, reason="tested-range"),
                        agents={p: dict(binary=f"/fixture/{p}", version=v, supported=True, reason="tested-range")
                                for p, v in [("claude", "2.1.288"), ("codex", "0.159.3")]},
                        cloud="import-only", multi_host=False)
        self.detect = patch("zudo_agent.setup.doctor", return_value=self.env)
        self.detect.start()
        self.addCleanup(self.detect.stop)

    def make(self, **overrides):
        args = dict(project=self.project, project_id="fixture", repository="example.invalid/test/fixture",
                    machine="fixture-machine", config=self.config, db=self.db, providers=["claude", "codex"])
        args.update(overrides)
        return setup.build_plan(**args)

    def apply(self, plan):
        return setup.apply_plan(plan, plan["approval"], self.receipts)

    def path(self, provider):
        return self.project / (".claude/settings.local.json" if provider == "claude" else ".codex/hooks.json")

    def write(self, path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))

    def test_preview_does_not_install_or_create_database(self):
        plan = self.make()
        preview = self.root / "review.setup-plan.json"
        setup.save_plan(plan, preview)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.db.exists())
        self.assertFalse(self.path("claude").exists())
        self.assertEqual(stat.S_IMODE(preview.stat().st_mode), 0o600)
        with self.assertRaises(setup.SetupError):
            setup.save_plan(plan, preview)

    def test_idempotent_merge_preserves_existing_settings_hooks_and_matchers(self):
        custom = {"permissions": {"deny": ["Read(.env)"]}, "env": {"PRIVATE": "fixture-value"},
                  "hooks": {"Stop": [{"matcher": "custom", "hooks": [{"type": "command", "command": "existing-user-hook"}]}]}}
        self.write(self.path("claude"), custom)
        plan = self.make()
        self.assertNotIn("fixture-value", json.dumps(setup.preview_summary(plan)))
        first = self.apply(plan)
        installed = json.loads(self.path("claude").read_text())
        self.assertEqual(installed["permissions"], custom["permissions"])
        self.assertEqual(installed["env"], custom["env"])
        self.assertEqual(installed["hooks"]["Stop"][0], custom["hooks"]["Stop"][0])
        self.assertEqual(self.apply(plan)["status"], "already-applied")
        again = self.make()
        self.assertEqual(again["changes"], [])
        self.assertEqual(self.apply(again)["status"], "no-changes")
        self.assertTrue(Path(first["receipt"]).exists())

    def test_missing_or_wrong_approval_and_tampering_are_rejected(self):
        plan = self.make()
        with self.assertRaises(setup.SetupError):
            setup.apply_plan(plan, "unapproved", self.receipts)
        plan["changes"][0]["after"] = setup.encoded(b"{}")
        with self.assertRaises(setup.SetupError):
            self.apply(plan)
        self.assertFalse(self.config.exists())

    def test_changed_target_refuses_all_writes(self):
        plan = self.make()
        self.write(self.path("codex"), {"hooks": {}, "user-change": True})
        with self.assertRaises(setup.SetupError):
            self.apply(plan)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.path("claude").exists())

    def test_changed_related_toml_refuses_apply(self):
        plan = self.make()
        target = self.project / ".codex/config.toml"
        target.parent.mkdir(parents=True)
        target.write_text("[features]\nhooks = false\n")
        with self.assertRaises(setup.SetupError):
            self.apply(plan)
        self.assertFalse(self.config.exists())

    def test_malformed_duplicate_and_wrong_shape_json_rejected(self):
        bad = ['{"hooks":', '{"hooks": {}, "hooks": {}}', '[]', '{"hooks": null}',
               '{"hooks":{"Stop":{}}}', '{"hooks":{"Stop":[{}]}}',
               '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":7}]}]}}']
        target = self.path("claude")
        target.parent.mkdir(parents=True)
        for text in bad:
            with self.subTest(text=text):
                target.write_text(text)
                with self.assertRaises((setup.SetupError, ValueError)):
                    self.make()
                self.assertEqual(target.read_text(), text)
                self.assertFalse(self.config.exists())

    def test_malformed_dashboard_config_not_replaced(self):
        self.config.write_text('{"machine":"different","projects":[]}')
        with self.assertRaises(setup.SetupError):
            self.make()
        self.config.write_text('{"machine":"fixture-machine","projects":null}')
        with self.assertRaises((ValueError, TypeError)):
            self.make()

    def test_edited_owned_hook_conflicts_instead_of_duplicating(self):
        self.apply(self.make())
        obj = json.loads(self.path("claude").read_text())
        obj["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 10
        self.write(self.path("claude"), obj)
        with self.assertRaises(setup.SetupError):
            self.make()

    def test_unowned_dashboard_hooks_are_not_adopted_or_duplicated(self):
        obj = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/a/venv/bin/zudo-agent --config /a/config hook claude"}]}]}}
        self.write(self.path("claude"), obj)
        with self.assertRaises(setup.SetupError):
            self.make()
        self.assertEqual(json.loads(self.path("claude").read_text()), obj)

    def test_project_identity_conflict(self):
        self.apply(self.make())
        with self.assertRaises(setup.SetupError):
            self.make(repository="example.invalid/different/repo")
        with self.assertRaises(setup.SetupError):
            self.make(project_id="different")
        with self.assertRaises(setup.SetupError):
            self.make(project_id="different", repository="example.invalid/different/repo")

    def test_unmodified_rollback_restores_exact_original_bytes(self):
        target = self.path("claude")
        target.parent.mkdir(parents=True)
        original = b'{ "custom" : true, "hooks": {} }\n'
        target.write_bytes(original)
        result = self.apply(self.make())
        rollback = setup.rollback_plan(result["receipt"])
        self.apply(rollback)
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(self.path("codex").exists())
        self.assertFalse(self.config.exists())
        self.assertEqual(setup.rollback_plan(result["receipt"])["changes"], [])

    def test_rollback_preserves_later_hooks_settings_and_same_group_additions(self):
        result = self.apply(self.make())
        target = self.path("claude")
        obj = json.loads(target.read_text())
        extra = {"type": "command", "command": "later-user-command"}
        obj["hooks"]["Stop"][0]["hooks"].append(extra)
        obj["hooks"]["Stop"].append({"matcher": "later", "hooks": [extra]})
        obj["theme"] = "later-setting"
        self.write(target, obj)
        self.apply(setup.rollback_plan(result["receipt"]))
        restored = json.loads(target.read_text())
        self.assertEqual(restored["theme"], "later-setting")
        self.assertEqual(restored["hooks"]["Stop"], [{"hooks": [extra]}, {"matcher": "later", "hooks": [extra]}])
        self.assertNotIn("--owner", target.read_text())

    def test_rollback_preserves_edited_owned_hook_with_conflict(self):
        result = self.apply(self.make())
        target = self.path("codex")
        obj = json.loads(target.read_text())
        obj["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 12
        self.write(target, obj)
        rollback = setup.rollback_plan(result["receipt"])
        self.assertTrue(rollback["warnings"])
        self.apply(rollback)
        remaining = json.loads(target.read_text())
        self.assertEqual(remaining["hooks"]["Stop"][0]["hooks"][0]["timeout"], 12)
        self.assertTrue(self.config.exists(), "Retained hook conflicts must not lose their dashboard configuration")

    def test_rollback_retains_malformed_later_file(self):
        result = self.apply(self.make())
        target = self.path("claude")
        target.write_text("later malformed edit")
        rollback = setup.rollback_plan(result["receipt"])
        self.assertTrue(rollback["warnings"])
        self.apply(rollback)
        self.assertEqual(target.read_text(), "later malformed edit")

    def test_rollback_keeps_modified_new_project_and_other_projects(self):
        result = self.apply(self.make())
        obj = json.loads(self.config.read_text())
        obj["projects"][0]["roots"].append("/later/checkout")
        other = dict(id="other", repository="example.invalid/other/repo", roots=["/other"])
        obj["projects"].append(other)
        self.write(self.config, obj)
        rollback = setup.rollback_plan(result["receipt"])
        self.assertTrue(rollback["warnings"])
        self.apply(rollback)
        self.assertEqual(json.loads(self.config.read_text()), obj)

    def test_rollback_removes_only_added_root_from_existing_project(self):
        entry = dict(id="fixture", repository="example.invalid/test/fixture", roots=["/previous/root"])
        self.write(self.config, dict(machine="fixture-machine", projects=[entry]))
        result = self.apply(self.make())
        obj = json.loads(self.config.read_text())
        obj["projects"][0]["roots"].append("/later/root")
        self.write(self.config, obj)
        self.apply(setup.rollback_plan(result["receipt"]))
        self.assertEqual(json.loads(self.config.read_text())["projects"][0]["roots"], ["/previous/root", "/later/root"])

    def test_changed_file_after_rollback_preview_is_not_overwritten(self):
        result = self.apply(self.make())
        rollback = setup.rollback_plan(result["receipt"])
        self.config.write_text('{"later":true}')
        with self.assertRaises(setup.SetupError):
            self.apply(rollback)
        self.assertEqual(self.config.read_text(), '{"later":true}')

    def test_interrupted_apply_has_recoverable_receipt(self):
        plan = self.make()
        original_atomic = setup.atomic

        def fail_codex(path, content, expected):
            if Path(path) == self.path("codex"):
                raise OSError("fixture write failure")
            return original_atomic(path, content, expected)

        with patch("zudo_agent.setup.atomic", side_effect=fail_codex), self.assertRaises(setup.SetupError):
            self.apply(plan)
        receipt = next(self.receipts.glob("*.receipt.json"))
        self.assertEqual(json.loads(receipt.read_text())["status"], "prepared")
        self.apply(setup.rollback_plan(receipt))
        self.assertFalse(self.config.exists())
        self.assertFalse(self.path("claude").exists())

    def test_paths_with_spaces_execute_only_new_fixture_runner(self):
        plan = self.make(providers=["claude"])
        self.apply(plan)
        argv = shlex.split(plan["commands"]["claude"])
        self.assertIn(str(self.config), argv)
        self.assertIn(str(self.db), argv)
        result = subprocess.run(argv, input=json.dumps(dict(session_id="synthetic-fixture", cwd=str(self.project), hook_event_name="Stop")),
                                capture_output=True, text=True, timeout=10, cwd=self.root)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        store = Store(self.db, load_config(self.config))
        self.addCleanup(store.close)
        self.assertEqual(store.snapshot()["projects"][0]["runs"][0]["state"], "idle")

    def test_explicit_disabled_project_hooks_stop_setup(self):
        self.write(self.path("claude"), {"disableAllHooks": True})
        with self.assertRaises(setup.SetupError):
            self.make()
        self.path("claude").unlink()
        toml = self.project / ".codex/config.toml"
        toml.parent.mkdir(parents=True)
        toml.write_text("[features]\nhooks = false\n")
        with self.assertRaises(setup.SetupError):
            self.make()

    def test_inline_codex_hooks_preserved(self):
        toml = self.project / ".codex/config.toml"
        toml.parent.mkdir(parents=True)
        original = '[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype = "command"\ncommand = "user-command"\n'
        toml.write_text(original)
        self.apply(self.make())
        self.assertEqual(toml.read_text(), original)

    def test_malformed_codex_toml_and_feature_shape_refused(self):
        toml = self.project / ".codex/config.toml"
        toml.parent.mkdir(parents=True)
        for content in ["[features", "features = true\n"]:
            with self.subTest(content=content):
                toml.write_text(content)
                with self.assertRaises(setup.SetupError):
                    self.make()
                self.assertEqual(toml.read_text(), content)

    def test_symlink_and_hardlink_targets_refused(self):
        other = self.root / "other.json"
        other.write_text("{}")
        self.config.symlink_to(other)
        with self.assertRaises(setup.SetupError):
            self.make()
        self.config.unlink()
        os.link(other, self.config)
        with self.assertRaises(setup.SetupError):
            self.make()
        self.assertEqual(other.read_text(), "{}")

    def test_unsupported_platform_or_version_refuses_setup(self):
        self.env["supported"] = False
        with self.assertRaises(setup.SetupError):
            self.make()
        self.env["supported"] = True
        self.env["agents"]["codex"]["supported"] = False
        with self.assertRaises(setup.SetupError):
            self.make()
        self.assertFalse(self.config.exists())

    def test_version_changed_after_preview_refuses_apply(self):
        plan = self.make()
        changed = copy.deepcopy(self.env)
        changed["agents"]["codex"]["version"] = "0.159.4"
        with patch("zudo_agent.setup.doctor", return_value=changed), self.assertRaises(setup.SetupError):
            self.apply(plan)

    def test_synthetic_verification_is_isolated_and_labeled(self):
        for provider in ["claude", "codex"]:
            result = setup.synthetic_verify(provider)
            self.assertTrue(result["passed"])
            self.assertEqual(result["mode"], "synthetic-only")
            self.assertEqual(result["actual_agent_delivery"], "not-verified")
            self.assertFalse(result["live_database_touched"])
        self.assertFalse(self.db.exists())
        self.assertFalse(self.config.exists())

    def test_cli_from_outside_clone_preview_apply_and_uninstall(self):
        script = Path(__file__).resolve().parents[1] / "scripts/setup.py"
        fake_bins = {}
        for name, output in [("claude", "2.1.288 (Claude Code)"), ("codex", "codex-cli 0.159.3"), ("tmux", "tmux 3.4")]:
            binary = self.root / (name + " fixture binary")
            binary.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(output) + "\n")
            binary.chmod(0o700)
            fake_bins[name] = binary

        def invoke(*args):
            result = subprocess.run([sys.executable, str(script), *map(str, args)], cwd=self.root,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        preview = self.root / "cli.setup-plan.json"
        result = invoke("plan", "--project", self.project, "--project-id", "fixture", "--repository", "example.invalid/test/fixture",
                        "--machine", "fixture-machine", "--provider", "both", "--config", self.config, "--db", self.db,
                        "--claude-bin", fake_bins["claude"], "--codex-bin", fake_bins["codex"], "--tmux-bin", fake_bins["tmux"], "--out", preview)
        self.assertFalse(result["setup_performed"])
        self.assertFalse(self.config.exists())
        applied = invoke("apply", "--plan", preview, "--approve", result["approval"], "--receipts", self.receipts)
        self.assertEqual(applied["actual_agent_delivery"], "not-verified")
        removal = self.root / "cli-uninstall.setup-plan.json"
        rollback = invoke("uninstall-plan", "--receipt", applied["receipt"], "--out", removal)
        invoke("apply", "--plan", removal, "--approve", rollback["approval"], "--receipts", self.receipts)
        self.assertFalse(self.config.exists())
        self.assertFalse(self.path("claude").exists())
        self.assertFalse(self.path("codex").exists())


class DetectionFixtures(unittest.TestCase):
    def test_versions_and_unknown_prereleases(self):
        with tempfile.TemporaryDirectory(prefix="zudo binary path ") as directory:
            binary = Path(directory) / "fake agent"
            for text, provider, supported in [("codex-cli 0.159.3", "codex", True),
                                              ("codex-cli 0.100.0", "codex", False),
                                              ("codex-cli 0.159.3-beta.1", "codex", False),
                                              ("codex-cli 0.160.0", "codex", False),
                                              ("2.1.288 (Claude Code)", "claude", True),
                                              ("3.0.0 (Claude Code)", "claude", False),
                                              ("tmux 3.4", "tmux", True),
                                              ("tmux 2.9", "tmux", False)]:
                with self.subTest(text=text):
                    binary.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(text) + "\n")
                    binary.chmod(0o700)
                    result = setup.version(str(binary), "--version", provider)
                    self.assertEqual(result["supported"], supported)
            self.assertFalse(setup.version("/nonexistent/zudo-fixture", "--version", "codex")["supported"])

    def test_native_mac_and_wsl_detection(self):
        with patch("zudo_agent.setup.version", return_value={}), patch("platform.system", return_value="Windows"):
            self.assertFalse(setup.doctor()["supported"])
            self.assertEqual(setup.doctor()["collector"], "unsupported-native-process-collector")
        with patch("zudo_agent.setup.version", return_value={}), patch("platform.system", return_value="Darwin"), patch("zudo_agent.native.library", return_value=object()):
            self.assertTrue(setup.doctor()["supported"])
            self.assertEqual(setup.doctor()["collector"], "darwin-libproc")
        with patch("zudo_agent.setup.version", return_value={}), patch("platform.system", return_value="Linux"), patch("platform.release", return_value="6.6-microsoft-standard-WSL2"), patch("zudo_agent.setup.Path.exists", return_value=True):
            self.assertTrue(setup.doctor()["wsl"])


if __name__ == "__main__":
    unittest.main()
