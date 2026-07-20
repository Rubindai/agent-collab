from __future__ import annotations

import importlib.util
import json
import os
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

    def test_codex_fanout_overrides_are_late_enforceable_controls(self):
        overrides = safety.codex_fanout_overrides(
            local_subagents_allowed=True,
            max_local_subagents=8,
            timeout_seconds=2700,
        )
        self.assertEqual(
            overrides,
            [
                "features.multi_agent=true",
                "agents.max_threads=8",
                "agents.max_depth=1",
                "agents.job_max_runtime_seconds=2700",
            ],
        )
        self.assertEqual(
            safety.codex_fanout_overrides(
                local_subagents_allowed=False,
                max_local_subagents=8,
                timeout_seconds=2700,
            ),
            ["features.multi_agent=false"],
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


if __name__ == "__main__":
    unittest.main()
