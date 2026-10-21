import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_agent_collab_runtime as fixtures

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class SettingsParsingTests(TestCase):
    def test_empty_boolean_environment_value_is_rejected_not_false(self):
        host = fixtures.load_host()
        with self.assertRaisesRegex(ValueError, "expected boolean"):
            host.normalize_settings({"safe_mode": ""}, "environment AGENT_COLLAB_SAFE_MODE")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(host, "default_global_settings_path", return_value=root / "g"),
                mock.patch.object(host, "default_local_settings_path", return_value=root / "l"),
            ):
                resolved = host.resolve_settings(root, {"AGENT_COLLAB_SAFE_MODE": ""})
        self.assertTrue(resolved["environment_errors"])
        self.assertEqual(resolved["sources"]["safe_mode"], "builtin")

    def test_integer_settings_accept_only_ascii_digits(self):
        host = fixtures.load_host()
        self.assertEqual(host.normalize_settings({"max_local_subagents": "8"}, "env")["max_local_subagents"], 8)
        for value in ("٨", "8.0", "-1", True, " "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.normalize_settings({"max_local_subagents": value}, "env")
        with self.assertRaises(ValueError):
            host.normalize_settings({"history_retained_runs": "٥"}, "env")

    def test_claude_tools_sentinel_must_be_lowercase_default(self):
        host = fixtures.load_host()
        peer = fixtures.load_peer()
        self.assertEqual(host.validate_claude_tools("default", "test"), "default")
        for value in ("DEFAULT", "Default"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "lowercase literal"):
                    host.validate_claude_tools(value, "test")
                with self.assertRaisesRegex(peer.RequestValidationError, "lowercase literal"):
                    peer.validate_request(fixtures.request(claude_tools=value))


class StateRootTests(TestCase):
    def test_inherited_claude_plugin_data_does_not_select_the_state_root(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            foreign = Path(tmp) / "plugins" / "data" / "other-plugin"
            xdg = Path(tmp) / "xdg-state"
            env = {"CLAUDE_PLUGIN_DATA": str(foreign), "XDG_STATE_HOME": str(xdg)}
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("AGENT_COLLAB_STATE_HOME", None)
                with mock.patch.object(host, "resource_root", return_value=Path(tmp) / "elsewhere"):
                    self.assertEqual(host.default_state_root(), xdg / "agent-collab")

    def test_active_run_is_found_through_the_worktree_guard_from_another_run_root(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            other_root = root / "other-host-runs"
            run_dir = other_root / "agent-collab-test"
            run_dir.mkdir(parents=True)
            (run_dir / "host-request.json").write_text("{}")
            guard = {"run_id": "agent-collab-test", "run_dir": str(run_dir)}
            with mock.patch.object(host, "read_workspace_guard", return_value=(root / "guard", guard)):
                self.assertEqual(host.guarded_run_dir(repo, "agent-collab-test"), run_dir.resolve())
                self.assertIsNone(host.guarded_run_dir(repo, "agent-collab-other"))
                resolved = host.resolve_run_reference(repo, "agent-collab-test", root / "my-runs")
                self.assertEqual(resolved, run_dir.resolve())
                args = SimpleNamespace(repo_root=repo, run_root=root / "my-runs", run="agent-collab-test")
                self.assertEqual(host.resolve_run_context(args), (repo.resolve(), other_root.resolve(), run_dir.resolve()))


class IsolationBoundaryTests(TestCase):
    def isolation(self):
        return fixtures.load_host().load_isolation_runtime()

    def test_sandbox_command_does_not_bind_the_run_directory_writable(self):
        isolation = self.isolation()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "home").mkdir()
            (root / "repo").mkdir()
            (root / "run").mkdir()
            with mock.patch.dict(os.environ, {"HOME": str(root / "home")}):
                workspace = isolation.Workspace(root / "repo", root / "run")
            workspace.mounts = [root / "m-home", root / "m-repo"]
            credential = root / "home" / ".codex" / "auth.json"
            command = workspace.command(["codex"], root / "tmp", [credential])
            pairs = [command[index + 1:index + 3] for index, item in enumerate(command) if item == "--bind"]
            self.assertNotIn([str((root / "run").resolve()), str((root / "run").resolve())], pairs)
            home_bind = pairs.index([str(root / "m-home"), str((root / "home").resolve())])
            self.assertGreater(pairs.index([str(credential), str(credential)]), home_bind)

    def test_credential_passthrough_selects_the_peer_products_regular_file(self):
        isolation = self.isolation()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            codex_auth = home / ".codex" / "auth.json"
            claude_auth = home / ".claude" / ".credentials.json"
            self.assertEqual(isolation.credential_passthrough("codex", {"HOME": str(home)}), [])
            codex_auth.parent.mkdir()
            codex_auth.write_text("{}")
            claude_auth.parent.mkdir()
            claude_auth.symlink_to(codex_auth)
            env = {"HOME": str(home)}
            self.assertEqual(isolation.credential_passthrough("codex", env), [codex_auth.resolve()])
            self.assertEqual(isolation.credential_passthrough("claude", env), [])
            custom = home / "custom-codex"
            custom.mkdir()
            (custom / "auth.json").write_text("{}")
            self.assertEqual(
                isolation.credential_passthrough("codex", {"HOME": str(home), "CODEX_HOME": str(custom)}),
                [(custom / "auth.json").resolve()],
            )

    def test_workspace_exit_ignores_a_rewritten_state_file(self):
        isolation = self.isolation()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "run"
            run.mkdir()
            own = root / "own-scratch"
            own.mkdir()
            victim = Path(tempfile.mkdtemp(prefix="agent-collab-cow-victim-", dir="/tmp"))
            try:
                (victim / "keep").write_text("untouched")
                workspace = isolation.Workspace(root, run)
                workspace.scratch = own
                (run / "isolation-state.json").write_text(json.dumps({
                    "schema_version": "2.0", "scratch": str(victim),
                    "mounts": [str(victim / "home" / "merged"), str(victim / "repo" / "merged")],
                    "status": "mounted",
                }))
                workspace.__exit__(None, None, None)
                self.assertEqual((victim / "keep").read_text(), "untouched")
                self.assertFalse(own.exists())
                state = json.loads((run / "isolation-state.json").read_text())
                self.assertEqual(state["scratch"], str(own))
                self.assertEqual(state["status"], "cleaned")
            finally:
                import shutil
                shutil.rmtree(victim, ignore_errors=True)

    def test_state_writer_survives_a_stale_temporary_file(self):
        isolation = self.isolation()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "isolation-state.json"
            (Path(tmp) / "isolation-state.json.tmp").write_text("stale")
            isolation._write_state(path, {"status": "mounted"})
            self.assertEqual(json.loads(path.read_text()), {"status": "mounted"})
            with self.assertRaises(ValueError):
                isolation._write_state(path, {"value": float("nan")})

    def test_real_overlay_discards_writes_and_passes_credentials_through(self):
        isolation = self.isolation()
        if sys.platform != "linux":
            self.skipTest("the isolated workspace backend is Linux-only")
        with tempfile.TemporaryDirectory() as probe_root:
            if isolation.preflight(Path(probe_root))["status"] != "available":
                self.skipTest("fuse-overlayfs and bwrap are not usable on this host")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, repo, scratch_tmp = root / "home", root / "repo", root / "provider-tmp"
            run = home / "state" / "runs" / "agent-collab-test"
            for directory in (home, repo, run, scratch_tmp):
                directory.mkdir(parents=True)
            (repo / "tracked.txt").write_text("original")
            (run / "host-request.json").write_text("host-owned")
            credential = home / ".codex" / "auth.json"
            credential.parent.mkdir()
            credential.write_text("old-token")
            script = (
                f"printf changed > {repo}/tracked.txt; printf new > {repo}/new.txt; "
                f"printf forged > {run}/host-request.json; printf rotated > {credential}; "
                f"printf out > {scratch_tmp}/peer-output.json"
            )
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                with isolation.Workspace(repo, run) as workspace:
                    command = workspace.command(["sh", "-c", script], scratch_tmp, [credential])
                    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    scratch = workspace.scratch
            self.assertEqual((repo / "tracked.txt").read_text(), "original")
            self.assertFalse((repo / "new.txt").exists())
            self.assertEqual((run / "host-request.json").read_text(), "host-owned")
            self.assertEqual(credential.read_text(), "rotated")
            self.assertEqual((scratch_tmp / "peer-output.json").read_text(), "out")
            self.assertFalse(scratch.exists())
            self.assertEqual(json.loads((run / "isolation-state.json").read_text())["status"], "cleaned")
