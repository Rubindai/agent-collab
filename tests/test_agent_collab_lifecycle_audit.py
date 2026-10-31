import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_collab_test_support as fixtures

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class LifecycleAuditTests(TestCase):
    def test_oversized_baseline_rolls_back_before_peer_launch(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            guard = root / "active-v2.json"
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "diff", "--brief", "review",
                "--run-id", "oversized", "--run-root", str(run_root), "--repo-root", str(root),
            ])
            resolved = {
                "settings": fixtures.complete_settings(host), "sources": {},
                "layers": {
                    "global": {"path": "g", "settings": {}, "error": None},
                    "local": {"path": "l", "settings": {}, "error": None},
                },
            }
            attestation = fixtures.availability_attestation("claude", "claude-opus-5-5", "xhigh")
            attestation["cli_version"] = "2.1.295"
            availability = SimpleNamespace(
                check=lambda *args, **kwargs: attestation,
                validate_result=lambda *args, **kwargs: None,
            )
            snapshot = host.CONFIG.SNAPSHOT
            with (
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard),
                mock.patch.object(host.CONFIG, "resolve_settings", return_value=resolved),
                mock.patch.object(host.CONFIG, "require_peer_cli_version", return_value="2.1.295"),
                mock.patch.object(host.CONFIG, "AVAILABILITY", new=availability),
                mock.patch.object(host.CONFIG, "SNAPSHOT", new=snapshot),
                mock.patch.object(snapshot, "mutation_snapshot", return_value={
                    "schema_version": "3.0", "mode": "git", "repo_root": str(root),
                    "digest": "a" * 64, "sections": {"diff": "x" * host.ARTIFACTS.MAX_JSON_ARTIFACT_BYTES},
                }),
                # Patch the launch itself: macOS reads process identity with ps
                # through subprocess.
                mock.patch.object(host.CONFIG.SAFETY, "popen_group") as launch,
                self.assertRaisesRegex(ValueError, "byte limit"),
            ):
                host.start(args)
            launch.assert_not_called()
            self.assertFalse(guard.exists())
            self.assertFalse((run_root / "oversized").exists())

    def test_uninspectable_starting_launcher_keeps_guard(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / "active-v2.json"
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                host.REGISTRY.acquire_workspace_guard(root, "active", "codex", root / "run")
                original = guard.read_bytes()
                for state in ("unknown", "matching"):
                    with (
                        self.subTest(state=state),
                        mock.patch.object(host.CONFIG.SAFETY, "process_identity_state", return_value=state),
                        self.assertRaisesRegex(SystemExit, "already active"),
                    ):
                        host.REGISTRY.acquire_workspace_guard(root, "active", "codex", root / "run")
                    self.assertEqual(guard.read_bytes(), original)
                with mock.patch.object(host.CONFIG.SAFETY, "process_identity_state", return_value="dead_or_replaced"):
                    host.REGISTRY.acquire_workspace_guard(root, "active", "codex", root / "run")
                self.assertTrue(guard.exists())
                host.REGISTRY.release_workspace_guard(root, "active", owner=host.REGISTRY.read_workspace_guard(root, "active")[1])

    def prepare_run(self, host, root, run_dir, guard):
        req = fixtures.request()
        run_dir.mkdir(parents=True)
        host.REGISTRY.acquire_workspace_guard(root, req["run_id"], "codex", run_dir)
        for name, data in {
            "host-request.json": req,
            "peer-process.json": fixtures.valid_peer_process(run_dir, root, guard),
            "provider-process.json": fixtures.valid_provider_process(),
            "peer-report.json": fixtures.valid_report(),
        }.items():
            host.CONFIG.STRICT_JSON.write(run_dir / name, data)
        fixtures.admit_host_input(host, run_dir, "host-first-pass.json", fixtures.valid_host_first_pass())
        return req

    def assert_report_change_requires_resynthesis(self, artifact_name, changed_report):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            run_dir = run_root / fixtures.request()["run_id"]
            guard = root / "active-v2.json"
            with (
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard),
                mock.patch.object(host.LIFECYCLE, "wait_for_peer_report", return_value="report_ready"),
                mock.patch.object(host.LIFECYCLE, "record_host_workspace_mutation", return_value=None),
                mock.patch.object(host.CONFIG.SAFETY, "process_group_alive", return_value=False),
                redirect_stdout(io.StringIO()),
            ):
                self.prepare_run(host, root, run_dir, guard)
                common = [str(run_dir), "--run-root", str(run_root), "--repo-root", str(root)]
                finish_args = host.build_parser().parse_args(["finish", *common])
                complete_args = host.build_parser().parse_args(["complete", *common])
                self.assertEqual(host.LIFECYCLE.finish(finish_args), 0)
                synthesis = fixtures.valid_host_synthesis()
                synthesis["claim_matrix_sha256"] = host.ARTIFACTS.load_json(run_dir / "host-result.json")["claim_matrix_sha256"]
                complete_args.synthesis = fixtures.author_synthesis_input(host, run_dir, synthesis)
                if artifact_name in host.CONFIG.REVIEW.HOST_INPUT_FILES:
                    fixtures.admit_host_input(host, run_dir, artifact_name, changed_report)
                else:
                    host.CONFIG.STRICT_JSON.write(run_dir / artifact_name, changed_report)
                self.assertEqual(host.LIFECYCLE.finish(finish_args), 0)
                with self.assertRaisesRegex(ValueError, "stale review"):
                    host.LIFECYCLE.complete(complete_args)
                self.assertTrue(guard.exists())
                synthesis["claim_matrix_sha256"] = host.ARTIFACTS.load_json(run_dir / "host-result.json")["claim_matrix_sha256"]
                complete_args.synthesis = fixtures.author_synthesis_input(host, run_dir, synthesis)
                self.assertEqual(host.LIFECYCLE.complete(complete_args), 0)
                self.assertFalse(guard.exists())

    def test_complete_one_run_preserves_another_hosts_active_run(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_root = root / "runs"
            run_dir = run_root / fixtures.request()["run_id"]
            other_dir = root / "other-host-runs" / "other-run"
            other_dir.mkdir(parents=True)
            (other_dir / "host-request.json").write_text("{}")
            guard = root / "active-v2.json"
            with (
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard),
                mock.patch.object(host.LIFECYCLE, "wait_for_peer_report", return_value="report_ready"),
                mock.patch.object(host.LIFECYCLE, "record_host_workspace_mutation", return_value=None),
                mock.patch.object(host.CONFIG.SAFETY, "process_group_alive", return_value=False),
                redirect_stdout(io.StringIO()),
            ):
                self.prepare_run(host, root, run_dir, guard)
                host.REGISTRY.acquire_workspace_guard(root, "other-run", "claude", other_dir)
                self.assertEqual(host.RUNS.resolve_run_reference(root, "other-run", run_root), other_dir)
                common = [str(run_dir), "--run-root", str(run_root), "--repo-root", str(root)]
                self.assertEqual(host.LIFECYCLE.finish(host.build_parser().parse_args(["finish", *common])), 0)
                synthesis = fixtures.valid_host_synthesis(run_dir=run_dir)
                common.extend(["--synthesis", str(fixtures.author_synthesis_input(host, run_dir, synthesis))])
                self.assertEqual(host.LIFECYCLE.complete(host.build_parser().parse_args(["complete", *common])), 0)
                self.assertEqual(set(host.REGISTRY.read_workspace_guards(root)[1]), {"other-run"})
                # Repeating completion must leave the other host's entry intact.
                self.assertEqual(host.LIFECYCLE.complete(host.build_parser().parse_args(["complete", *common])), 0)
                self.assertEqual(set(host.REGISTRY.read_workspace_guards(root)[1]), {"other-run"})
                host.REGISTRY.release_workspace_guard(root, "other-run", owner=host.REGISTRY.read_workspace_guard(root, "other-run")[1])

    def test_changed_helper_claims_require_new_synthesis_after_finish(self):
        self.assert_report_change_requires_resynthesis("helper-reports.json", {
            "schema_version": "3.0", "run_id": fixtures.request()["run_id"], "reports": [{
                "name": "later-review", "summary": "Additional evidence.", "claims": [{
                    "claim": "New material finding", "status": "confirmed", "evidence": "source:42",
                }],
            }],
        })

    def test_changed_peer_findings_with_identical_claims_require_resynthesis(self):
        self.assert_report_change_requires_resynthesis("peer-report.json", fixtures.valid_report(findings=[{
            "severity": "high", "title": "New finding", "details": "Changed source evidence.",
            "files": ["source.py:42"], "recommendation": "Correct the behavior.", "confidence": "high",
        }]))

    def test_synthesis_digest_is_required_and_strictly_typed(self):
        host = fixtures.load_host()
        for value in (None, 1, "A" * 64, "a" * 63):
            synthesis = {**fixtures.valid_host_synthesis(), "claim_matrix_sha256": value}
            with self.subTest(value=value), self.assertRaises(host.ARTIFACTS.ArtifactValidationError):
                host.ARTIFACTS.validate_host_synthesis(synthesis, fixtures.request())
        synthesis = fixtures.valid_host_synthesis()
        synthesis.pop("claim_matrix_sha256", None)
        with self.assertRaises(host.ARTIFACTS.ArtifactValidationError):
            host.ARTIFACTS.validate_host_synthesis(synthesis, fixtures.request())

    def test_helper_claims_cannot_omit_the_admitted_report_digest(self):
        host = fixtures.load_host()
        req = fixtures.request(adjudicator_enabled=False)
        helper_claims = [{"claim": "Helper claim", "status": "confirmed", "evidence": "source", "source": "helper"}]
        with self.assertRaisesRegex(host.ARTIFACTS.ArtifactValidationError, "admitted helper-report digest"):
            host.ARTIFACTS.build_claim_matrix(req, fixtures.valid_host_first_pass(), fixtures.valid_report(), helper_claims)

    def test_interrupted_cancel_reuses_large_snapshot_and_detects_tampering(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, run_root = base / "repo", base / "runs"
            root.mkdir()
            tracked = root / "tracked.txt"
            tracked.write_text("before")
            run_dir = run_root / fixtures.request()["run_id"]
            guard = base / "active-v2.json"
            with (
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard),
                mock.patch.object(host.CONFIG.SAFETY, "process_group_alive", return_value=False),
                mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=False),
                redirect_stdout(io.StringIO()),
            ):
                req = self.prepare_run(host, root, run_dir, guard)
                (run_dir / "peer-report.json").write_text("{malformed")
                host.LIFECYCLE.run_snapshot(root, run_dir / "before.snapshot", ignored_paths=[run_dir])
                tracked.write_text("after")
                snapshot = host.CONFIG.SNAPSHOT
                after = snapshot.mutation_snapshot(root, ignored_paths=[run_dir])
                # A later snapshot can exceed the baseline reader limit.
                after["padding"] = "x" * host.ARTIFACTS.MAX_JSON_ARTIFACT_BYTES
                args = host.build_parser().parse_args([
                    "cancel", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                write_once = host.LIFECYCLE.write_json_once

                def interrupt_result(path, data):
                    if path.name == "cancel-result.json":
                        raise KeyboardInterrupt("interrupted after committing mutation")
                    return write_once(path, data)

                with (
                    mock.patch.object(host.CONFIG, "SNAPSHOT", new=snapshot),
                    mock.patch.object(snapshot, "mutation_snapshot", return_value=after),
                    mock.patch.object(host.LIFECYCLE, "write_json_once", side_effect=interrupt_result),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    host.LIFECYCLE.cancel(args)
                mutation_path = run_dir / "cancel-workspace-mutation.json"
                after_path = run_dir / "cancel-after.snapshot.json"
                original_mutation, original_after = mutation_path.read_bytes(), after_path.read_bytes()
                self.assertGreater(len(original_after), host.ARTIFACTS.MAX_JSON_ARTIFACT_BYTES)
                self.assertTrue(guard.exists())
                self.assertFalse((run_dir / "cancel-result.json").exists())
                tracked.write_text("later unrelated edit")
                after_path.write_text("{}")
                with self.assertRaisesRegex(ValueError, "snapshot changed"):
                    host.LIFECYCLE.cancel(args)
                self.assertTrue(guard.exists())
                after_path.write_bytes(original_after)
                with mock.patch.object(host.LIFECYCLE, "run_snapshot", side_effect=AssertionError("must reuse capture")):
                    self.assertEqual(host.LIFECYCLE.cancel(args), 0)
                self.assertEqual(mutation_path.read_bytes(), original_mutation)
                self.assertEqual(after_path.read_bytes(), original_after)
                self.assertFalse(guard.exists())
                result = host.ARTIFACTS.load_json(run_dir / "cancel-result.json")
                host.ARTIFACTS.validate_cancel_result(result, req, run_dir)
