from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


safety = load_module("agent_collab_safety_test", "tools/agent-collab/scripts/safety.py")
strict_json = load_module("agent_collab_strict_json_test", "tools/agent-collab/scripts/strict_json.py")
snapshot = load_module("agent_collab_snapshot_safety_test", "tools/agent-collab/scripts/snapshot.py")


class SafetyTests(unittest.TestCase):
    def test_strict_json_rejects_duplicate_and_nonfinite_numbers(self):
        for payload in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e9999}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                strict_json.loads(payload)

    def test_strict_json_write_is_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "artifact.json"
            strict_json.write(path, {"schema_version": "2.0", "ok": True})
            self.assertEqual(strict_json.load(path), {"schema_version": "2.0", "ok": True})
            self.assertFalse(any(path.parent.glob(f".{path.name}.*")))

    @unittest.skipUnless(os.name == "posix", "POSIX owner-mode contract")
    def test_private_artifacts_ignore_a_permissive_umask(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name) / "runtime"
            previous = os.umask(0)
            try:
                strict_json.ensure_private_directory(root)
                json_path = root / "artifact.json"
                text_path = root / "peer.stderr.log"
                strict_json.write(json_path, {"schema_version": "2.0"})
                strict_json.write_text(text_path, "private\n")
                with strict_json.open_private_text(root / "peer-report.json") as handle:
                    handle.write("{}\n")
            finally:
                os.umask(previous)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            for path in root.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)

    def test_strict_json_file_limit_rejects_before_unbounded_text_read(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            path = Path(tmp_name) / "oversize.json"
            with path.open("wb") as handle:
                handle.write(b"{")
                handle.seek(10 * 1024 * 1024)
                handle.write(b"}")
            with (
                mock.patch.object(Path, "read_text", side_effect=AssertionError("unbounded read")),
                self.assertRaisesRegex(ValueError, "1024-byte limit"),
            ):
                strict_json.load(path, max_bytes=1024)

    def test_process_identity_detects_exact_live_process(self):
        identity = safety.process_identity(os.getpid())
        safety.validate_process_identity(identity)
        self.assertTrue(safety.process_identity_matches(identity))
        self.assertFalse(safety.process_identity_matches({**identity, "start_time": "not-the-start-time"}))

    def test_claude_agent_guard_blocks_after_finite_limit(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            counter = Path(tmp_name) / "agent-calls.count"
            command = [
                sys.executable,
                str(REPO_ROOT / "tools/agent-collab/scripts/safety.py"),
                "claude-agent-guard",
                "--counter",
                str(counter),
                "--limit",
                "1",
            ]
            first = subprocess.run(command, text=True, capture_output=True, check=False)
            second = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(first.returncode, 0)
            self.assertEqual(second.returncode, 2)
            self.assertIn("finite per-run limit is 1", second.stderr)
            self.assertEqual(counter.read_text(encoding="utf-8"), "1")

    @unittest.skipUnless(os.name == "posix", "POSIX hook counter contract")
    def test_claude_agent_guard_zero_limit_and_symlink_failure_are_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            counter = root / "agent-calls.count"
            self.assertEqual(safety._run_claude_agent_guard(counter, 0), 2)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(counter.stat().st_mode), 0o600)

            target = root / "target"
            target.write_text("0", encoding="utf-8")
            counter.unlink()
            counter.symlink_to(target)
            self.assertEqual(safety._run_claude_agent_guard(counter, 1), 2)
            self.assertEqual(target.read_text(encoding="utf-8"), "0")

    def test_claude_guard_settings_pin_model_disable_fallbacks_and_bound_depth(self):
        settings = safety.claude_agent_guard_settings(
            Path("/tmp/agent-calls.count"),
            8,
            "claude-opus-5",
        )
        self.assertEqual(settings["availableModels"], ["claude-opus-5"])
        self.assertEqual(settings["fallbackModel"], [])
        self.assertIs(settings["switchModelsOnFlag"], False)
        self.assertIs(settings["disableAllHooks"], False)
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["matcher"], "Agent")
        self.assertEqual(
            safety.helper_enforcement(
                "claude", local_subagents_allowed=True, max_local_subagents=8
            )["depth"],
            "provider_enforced",
        )
        with self.assertRaisesRegex(ValueError, "non-empty exact identifier"):
            safety.claude_agent_guard_settings(Path("/tmp/count"), 1, "")

    def test_codex_fanout_overrides_are_late_enforceable_controls(self):
        overrides = safety.codex_fanout_overrides(
            local_subagents_allowed=True,
            max_local_subagents=8,
        )
        self.assertEqual(
            overrides,
            [
                "agents.enabled=true",
                "agents.max_concurrent_threads_per_session=8",
            ],
        )
        self.assertEqual(
            safety.codex_fanout_overrides(
                local_subagents_allowed=False,
                max_local_subagents=8,
            ),
            ["agents.enabled=false"],
        )
        self.assertEqual(
            safety.helper_enforcement(
                "codex", local_subagents_allowed=True, max_local_subagents=8
            ),
            {
                "provider": "codex_v2_concurrency",
                "limit": 8,
                "limit_semantics": "concurrent_threads",
                "depth": "advisory",
                "runtime": "overall_peer_deadline",
            },
        )

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux bwrap contract")
    def test_sandbox_preflight_reports_bwrap_failure_structurally(self):
        completed = subprocess.CompletedProcess(
            ["/usr/bin/bwrap"],
            1,
            stdout="",
            stderr="bwrap: user namespace unavailable",
        )
        with (
            mock.patch.object(safety.shutil, "which", return_value="/usr/bin/bwrap"),
            mock.patch.object(safety.subprocess, "run", return_value=completed),
        ):
            result = safety.sandbox_preflight(REPO_ROOT)
        self.assertEqual(result["status"], "sandbox_unavailable")
        self.assertEqual(result["backend"], "bwrap")
        self.assertIn("user namespace unavailable", result["details"])

    def test_untracked_host_config_content_is_in_mutation_digest(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            repo = Path(tmp_name)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            config = repo / ".claude/settings.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"value": 1}), encoding="utf-8")
            before = snapshot.mutation_snapshot(repo)
            config.write_text(json.dumps({"value": 2}), encoding="utf-8")
            after = snapshot.mutation_snapshot(repo)
        self.assertNotEqual(before["digest"], after["digest"])
        difference = snapshot.diff_snapshots(before, after)
        self.assertIs(difference["changed"], True)
        self.assertIn(".claude/settings.json", difference["changed_paths"])
        self.assertIsInstance(strict_json.dumps(difference), str)

    def test_ignored_files_and_git_control_are_in_mutation_digest(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            repo = Path(tmp_name)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / ".gitignore").write_text("*.secret\n", encoding="utf-8")
            subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
            secret = repo / "provider.secret"
            secret.write_text("one", encoding="utf-8")
            subprocess.run(["git", "config", "audit.value", "one"], cwd=repo, check=True)
            before = snapshot.mutation_snapshot(repo)

            secret.write_text("two", encoding="utf-8")
            subprocess.run(["git", "config", "audit.value", "two"], cwd=repo, check=True)
            after = snapshot.mutation_snapshot(repo)

        difference = snapshot.diff_snapshots(before, after)
        self.assertIs(difference["changed"], True)
        self.assertIn("provider.secret", difference["changed_paths"])
        self.assertIn("<git-control>/common/config", difference["changed_paths"])

    def test_noncurrent_refs_and_raw_index_are_in_mutation_digest(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            repo = Path(tmp_name)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "config", "user.name", "Agent Collab Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            (repo / "tracked.txt").write_text("one", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, text=True, capture_output=True
            ).stdout.strip()
            subprocess.run(["git", "update-ref", "refs/heads/audit", head], cwd=repo, check=True)
            before = snapshot.mutation_snapshot(repo)
            subprocess.run(["git", "update-ref", "-d", "refs/heads/audit"], cwd=repo, check=True)
            after = snapshot.mutation_snapshot(repo)

        difference = snapshot.diff_snapshots(before, after)
        self.assertIs(difference["changed"], True)
        self.assertIn("<git-control>/common/refs/heads/audit", difference["changed_paths"])

    def test_next_generated_files_are_ignored_but_tracked_next_files_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            repo = Path(tmp_name)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "config", "user.name", "Agent Collab Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
            before_generated = snapshot.mutation_snapshot(repo)
            generated = repo / ".next/cache/output.bin"
            generated.parent.mkdir(parents=True)
            generated.write_bytes(b"generated")
            after_generated = snapshot.mutation_snapshot(repo)
            self.assertEqual(before_generated["digest"], after_generated["digest"])

            tracked = repo / ".next/contract.txt"
            tracked.write_text("one", encoding="utf-8")
            subprocess.run(["git", "add", "-f", ".next/contract.txt"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-qm", "track next contract"], cwd=repo, check=True)
            before_tracked = snapshot.mutation_snapshot(repo)
            tracked.write_text("two", encoding="utf-8")
            after_tracked = snapshot.mutation_snapshot(repo)

        difference = snapshot.diff_snapshots(before_tracked, after_tracked)
        self.assertIs(difference["changed"], True)
        self.assertIn(".next/contract.txt", difference["changed_paths"])


if __name__ == "__main__":
    unittest.main()
