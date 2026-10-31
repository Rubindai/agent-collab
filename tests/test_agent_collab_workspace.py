import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

from agent_collab_test_support import (
    load_host,
    load_peer,
    request,
    setUpModule,
    tearDownModule,
)


class IsolatedWorkspaceTests(TestCase):
    def test_isolation_is_default_and_fails_before_run_creation(self):
        with mock.patch.object(sys, "platform", "linux"), tempfile.TemporaryDirectory() as temporary:
            host = load_host()
            self.assertEqual(host.CONFIG.SETTING_DEFAULTS["workspace_mode"], "isolated")
            root = Path(temporary)
            run_root = root / "runs"
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "diff", "--brief", "review",
                "--repo-root", str(root), "--run-root", str(run_root),
            ])
            unavailable = {"status": "sandbox_unavailable", "backend": "linux_fuse_overlay_bwrap", "details": "FUSE unavailable"}
            with (
                mock.patch.object(host.REGISTRY, "require_workspace_guard_available"),
                mock.patch.object(host.CONFIG, "ISOLATION", new=SimpleNamespace(preflight=lambda _root: unavailable)),
                mock.patch.object(host.CONFIG, "require_peer_cli_version") as cli,
                mock.patch.object(host.REGISTRY, "acquire_workspace_guard") as guard,
                self.assertRaises(SystemExit) as raised,
            ):
                host.start(args)
            self.assertEqual(json.loads(str(raised.exception))["status"], "sandbox_unavailable")
            cli.assert_not_called()
            guard.assert_not_called()
            self.assertFalse(run_root.exists())

    def test_macos_defaults_to_direct_without_isolation_fallback(self):
        with mock.patch.object(sys, "platform", "darwin"), tempfile.TemporaryDirectory() as temporary:
            host = load_host()
            self.assertEqual(host.CONFIG.SETTING_DEFAULTS["workspace_mode"], "direct")
            root = Path(temporary)
            run_root = root / "runs"
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "diff", "--brief", "review",
                "--repo-root", str(root), "--run-root", str(run_root),
            ])
            with (
                mock.patch.object(host.REGISTRY, "require_workspace_guard_available"),
                mock.patch.object(host.CONFIG, "load_settings_layers", return_value={
                    "global": {"settings": {}, "error": None},
                    "local": {"settings": {}, "error": None},
                }),
                mock.patch.object(host.CONFIG.ISOLATION, "preflight", side_effect=AssertionError("unexpected isolation preflight")),
                mock.patch.object(host.CONFIG, "require_peer_cli_version", side_effect=SystemExit("version probe reached")),
                self.assertRaisesRegex(SystemExit, "version probe reached"),
            ):
                host.start(args)
            self.assertFalse(run_root.exists())

    def test_isolated_request_is_strict_and_allows_temporary_validation(self):
        peer = load_peer()
        peer.validate_request(request(workspace_mode="isolated"))
        prompt = peer.build_prompt(request(workspace_mode="isolated"), Path.cwd(), Path("schema.json"))
        self.assertIn("temporary validation changes", prompt)
        for invalid in (["direct"], "fallback", None):
            with self.subTest(invalid=invalid), self.assertRaises(peer.RequestValidationError):
                peer.validate_request(request(workspace_mode=invalid))
        with self.assertRaises(peer.RequestValidationError):
            peer.validate_request(request(workspace_mode="isolated", edit_allowed=True))

    def test_isolated_snapshot_does_not_scan_repo(self):
        host = load_host()
        snapshot = host.CONFIG.SNAPSHOT
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                mock.patch.object(host.CONFIG, "SNAPSHOT", new=snapshot),
                mock.patch.object(snapshot, "mutation_snapshot", side_effect=AssertionError("content scan")),
            ):
                result = host.LIFECYCLE.run_snapshot(root, root / "before.snapshot", workspace_mode="isolated")
            self.assertEqual(result["mode"], "isolated")
            self.assertIn("no content scan", (root / "before.snapshot").read_text())

    def test_isolation_cleanup_rejects_untrusted_scratch_path(self):
        isolation = load_host().CONFIG.ISOLATION
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            victim = root / "agent-collab-cow-victim"
            victim.mkdir()
            (victim / "keep").write_text("untouched")
            state = root / "isolation-state.json"
            state.write_text(json.dumps({
                "schema_version": "3.0", "scratch": str(victim),
                "mounts": [str(victim / "home" / "merged"), str(victim / "repo" / "merged")],
                "status": "mounted",
            }))
            with self.assertRaises(isolation.IsolationUnavailable):
                isolation.cleanup_state(state)
            self.assertEqual((victim / "keep").read_text(), "untouched")

    def test_isolation_state_writer_rejects_symlink_destination(self):
        isolation = load_host().CONFIG.ISOLATION
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            victim = root / "victim"
            victim.write_text("unchanged")
            destination = root / "isolation-state.json"
            destination.symlink_to(victim)
            with self.assertRaises(isolation.IsolationUnavailable):
                isolation._write_state(destination, {})
            self.assertEqual(victim.read_text(), "unchanged")

