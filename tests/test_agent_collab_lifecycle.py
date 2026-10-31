from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

from agent_collab_test_support import (
    REPO_ROOT,
    admit_host_input,
    author_synthesis_input,
    availability_attestation,
    complete_settings,
    load_availability,
    load_host,
    load_peer,
    load_snapshot,
    load_state,
    request,
    setUpModule,
    tearDownModule,
    valid_host_first_pass,
    valid_host_synthesis,
    valid_peer_process,
    valid_process_identity,
    valid_provider_process,
    valid_report,
    valid_state_job,
    valid_state_job_patch,
)


class HostTests(TestCase):
    def test_mode_classifier_uses_token_and_phrase_boundaries(self):
        host = load_host()
        self.assertEqual(
            host.classify_mode("current provider behavior", "Research authoritative official documentation"),
            "research",
        )
        self.assertEqual(
            host.classify_mode("relationship behavior", "Research current platform behavior"),
            "research",
        )
        self.assertEqual(host.classify_mode("settings audit", "Audit the runtime"), "review")

    def test_start_fails_early_outside_the_supported_platforms(self):
        host = load_host()
        args = host.build_parser().parse_args([
            "start", "--host", "codex", "--target", "diff", "--brief", "review",
        ])
        for os_name, platform in (("nt", "cygwin"), ("posix", "freebsd14"), ("posix", "sunos5")):
            with (
                self.subTest(os_name=os_name, platform=platform),
                mock.patch.object(host.os, "name", os_name),
                mock.patch.object(host.sys, "platform", platform),
                mock.patch.object(host.CONFIG, "STATE") as state,
                self.assertRaisesRegex(SystemExit, "requires Linux, macOS, or Windows"),
            ):
                host.start(args)
            self.assertEqual(state.mock_calls, [])
        for os_name, platform in (("posix", "linux"), ("posix", "darwin"), ("nt", "win32")):
            with mock.patch.object(host.os, "name", os_name), mock.patch.object(host.sys, "platform", platform):
                self.assertTrue(host.CONFIG.is_supported_platform())
        self.assertEqual(host.CONFIG.platform_default_workspace_mode("linux"), "isolated")
        self.assertEqual(host.CONFIG.platform_default_workspace_mode("darwin"), "direct")
        self.assertEqual(host.CONFIG.platform_default_workspace_mode("win32"), "direct")

    def test_current_defaults_and_removed_settings(self):
        host = load_host()
        self.assertEqual(host.CONFIG.SETTINGS_SCHEMA_VERSION, "2.0")
        self.assertEqual(host.CONFIG.SETTING_DEFAULTS["codex_model"], "gpt-6-astra")
        self.assertEqual(host.CONFIG.SETTING_DEFAULTS["codex_effort"], "xhigh")
        self.assertEqual(host.CONFIG.SETTING_DEFAULTS["claude_model"], "claude-opus-5-5")
        self.assertEqual(host.CONFIG.SETTING_DEFAULTS["claude_effort"], "xhigh")
        self.assertIs(host.CONFIG.SETTING_DEFAULTS["online_research"], True)
        self.assertIs(host.CONFIG.SETTING_DEFAULTS["adjudicator_enabled"], True)
        self.assertEqual(host.CONFIG.SETTING_DEFAULTS["agent_timeout_seconds"], 2700)
        self.assertNotIn("profile", host.CONFIG.SETTING_DEFAULTS)
        self.assertNotIn("claude_max_turns", host.CONFIG.SETTING_DEFAULTS)
        self.assertNotIn("claude_max_budget_usd", host.CONFIG.SETTING_DEFAULTS)
        self.assertNotIn("web_research", host.CONFIG.SETTING_DEFAULTS)

    def test_settings_are_exact_v2_with_env_local_global_precedence(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            global_path, local_path = root / "global.json", root / "local.json"
            host.CONFIG.write_settings_file(global_path, complete_settings(host, claude_model="global"))
            host.CONFIG.write_settings_file(local_path, complete_settings(host, claude_model="local"))
            with (
                mock.patch.object(host.CONFIG, "default_global_settings_path", return_value=global_path),
                mock.patch.object(host.CONFIG, "default_local_settings_path", return_value=local_path),
            ):
                resolved = host.CONFIG.resolve_settings(
                    root, {"CLAUDE_AGENT_COLLAB_MODEL": "env", "AGENT_COLLAB_ONLINE_RESEARCH": "0"}
                )
        self.assertEqual(resolved["settings"]["claude_model"], "env")
        self.assertIs(resolved["settings"]["online_research"], False)

    def test_model_upgrade_preserves_existing_v2_model_and_effort_overrides(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            global_path, local_path = root / "global.json", root / "local.json"
            original = complete_settings(
                host, codex_model="gpt-5.6-sol", codex_effort="max",
                claude_model="claude-opus-5", claude_effort="max",
            )
            host.CONFIG.write_settings_file(local_path, original)
            original_bytes = local_path.read_bytes()
            with (
                mock.patch.object(host.CONFIG, "default_global_settings_path", return_value=global_path),
                mock.patch.object(host.CONFIG, "default_local_settings_path", return_value=local_path),
            ):
                resolved = host.CONFIG.resolve_settings(root, {})
            self.assertEqual(resolved["settings"], original)
            self.assertEqual(local_path.read_bytes(), original_bytes)

    def test_legacy_and_partial_settings_are_rejected(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"schema_version": "1.0", "updated_at": "x", "settings": {}}))
            _, error = host.CONFIG.load_settings_file(path, "test")
            self.assertIn("legacy settings", error)
            path.write_text(json.dumps({"schema_version": "2.0", "updated_at": "x", "settings": {"safe_mode": False}}))
            _, error = host.CONFIG.load_settings_file(path, "test")
            self.assertIn("match v2 exactly", error)

    def test_persisted_settings_reject_legacy_type_coercion(self):
        host = load_host()
        cases = (
            ("safe_mode", "false", "safe_mode must be a boolean"),
            ("max_local_subagents", "8", "max_local_subagents must be an integer"),
            ("codex_config", "foo=true", "codex_config must be an array"),
            ("agent_timeout_seconds", "2700", "agent_timeout_seconds must be a finite number"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            for key, value, expected in cases:
                settings = complete_settings(host)
                settings[key] = value
                path.write_text(json.dumps({
                    "schema_version": "2.0", "updated_at": "2026-07-18T00:00:00Z", "settings": settings,
                }))
                loaded, error = host.CONFIG.load_settings_file(path, "test")
                self.assertEqual(loaded, {})
                self.assertIn(expected, error)

    def test_removed_environment_key_is_ignored(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(host.CONFIG, "default_global_settings_path", return_value=root / "g"),
                mock.patch.object(host.CONFIG, "default_local_settings_path", return_value=root / "l"),
            ):
                resolved = host.CONFIG.resolve_settings(root, {"AGENT_COLLAB_WEB_RESEARCH": "disabled"})
        self.assertIs(resolved["settings"]["online_research"], True)

    def test_parser_accepts_user_model_effort_and_research(self):
        host = load_host()
        args = host.build_parser().parse_args([
            "start", "--host", "codex", "--target", "diff", "--brief", "review",
            "--peer-model", "claude-fable-5", "--peer-effort", "max", "--no-online-research",
            "--claude-max-turns", "200", "--no-adjudicator", "--safe-mode",
            "--timeout-seconds", "900", "--local-subagents",
        ])
        self.assertEqual(args.peer_model, "claude-fable-5")
        self.assertEqual(args.peer_effort, "max")
        self.assertIs(args.online_research, False)
        self.assertEqual(args.claude_max_turns, 200)
        self.assertIs(args.adjudicator, False)
        self.assertIs(args.safe_mode, True)
        self.assertEqual(args.timeout_seconds, 900)
        self.assertIs(args.local_subagents, True)

    def test_setup_preserves_user_effort_text_for_live_validation(self):
        host = load_host()
        codex = host.build_parser().parse_args(["setup", "--codex-effort", "minimal"])
        claude = host.build_parser().parse_args(["setup", "--claude-effort", "xhigh"])
        self.assertEqual(codex.codex_effort, "minimal")
        self.assertEqual(claude.claude_effort, "xhigh")
        future = host.build_parser().parse_args(["setup", "--codex-effort", "future-level"])
        self.assertEqual(future.codex_effort, "future-level")

    def test_fable_is_rejected_from_persisted_or_environment_defaults(self):
        host = load_host()
        with self.assertRaisesRegex(ValueError, "cannot persist Fable"):
            host.CONFIG.normalize_settings({"claude_model": "claude-fable-5"}, "test")
        settings = complete_settings(host, claude_model="claude-fable-5")
        with self.assertRaisesRegex(ValueError, "cannot persist Fable"):
            host.CONFIG.validate_v2_settings_types(settings, "test")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(host.CONFIG, "default_global_settings_path", return_value=root / "g"),
                mock.patch.object(host.CONFIG, "default_local_settings_path", return_value=root / "l"),
            ):
                resolved = host.CONFIG.resolve_settings(
                    root,
                    {"CLAUDE_AGENT_COLLAB_MODEL": "claude-fable-5"},
                )
        self.assertEqual(resolved["settings"]["claude_model"], "claude-opus-5-5")
        self.assertIn("cannot persist Fable", resolved["environment_errors"][0])

    def test_cli_version_parser_and_floor(self):
        host = load_host()
        self.assertEqual(host.CONFIG.parse_cli_version("codex-cli 0.144.5"), (0, 144, 5))
        self.assertEqual(host.CONFIG.parse_cli_version("2.1.212 (Claude Code)"), (2, 1, 212))
        self.assertEqual(host.CONFIG.MIN_CLI_VERSIONS["codex"], (0, 163, 0))
        self.assertEqual(host.CONFIG.MIN_CLI_VERSIONS["claude"], (2, 1, 295))
        old = SimpleNamespace(returncode=0, stdout="codex-cli 0.162.99", stderr="")
        with (
            mock.patch.object(host.shutil, "which", return_value="/usr/bin/codex"),
            mock.patch.object(host.subprocess, "run", return_value=old),
            self.assertRaises(SystemExit),
        ):
            host.CONFIG.require_peer_cli_version("codex")
        old_claude = SimpleNamespace(returncode=0, stdout="2.1.294 (Claude Code)", stderr="")
        with (
            mock.patch.object(host.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(host.subprocess, "run", return_value=old_claude),
            self.assertRaises(SystemExit),
        ):
            host.CONFIG.require_peer_cli_version("claude")

    def test_check_availability_prints_one_strict_json_result(self):
        host = load_host()
        availability = load_availability()
        output = availability_attestation("claude", "claude-fable-5", "max")
        args = host.build_parser().parse_args([
            "check-availability", "--peer", "claude", "--model", "claude-fable-5",
            "--effort", "max", "--timeout-seconds", "5", "--repo-root", str(REPO_ROOT),
        ])
        stream = io.StringIO()
        with (
            mock.patch.object(host.CONFIG, "AVAILABILITY", new=availability),
            mock.patch.object(host.CONFIG, "require_peer_cli_version", return_value="2.1.257"),
            mock.patch.object(availability, "check", return_value=output) as check,
            redirect_stdout(stream),
        ):
            self.assertEqual(host.check_availability(args), 0)
        parsed = json.loads(stream.getvalue())
        self.assertEqual(parsed, output)
        self.assertEqual(set(parsed), availability.RESULT_KEYS)
        check.assert_called_once_with(
            "claude", "claude-fable-5", "max", "2.1.257", REPO_ROOT, 5.0
        )

    def test_start_unknown_availability_stops_before_guard_run_or_peer(self):
        host = load_host()
        availability = load_availability()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            guard = root / "active-v2.json"
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "diff", "--brief", "review",
                "--run-id", "unknown-pair", "--run-root", str(run_root), "--repo-root", str(root),
            ])
            resolved = {
                "settings": complete_settings(host), "sources": {},
                "layers": {
                    "global": {"path": "g", "settings": {}, "error": None},
                    "local": {"path": "l", "settings": {}, "error": None},
                },
            }
            unknown = availability.result(
                status="unknown",
                peer="claude",
                model="claude-opus-5-5",
                effort="xhigh",
                source="claude_cli_probe",
                cli_version="2.1.257",
                details="probe metadata unavailable",
                evidence="test unknown",
            )
            availability_runtime = SimpleNamespace(check=lambda *args, **kwargs: unknown)
            with (
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard),
                mock.patch.object(host.CONFIG, "resolve_settings", return_value=resolved),
                mock.patch.object(host.CONFIG, "require_peer_cli_version", return_value="2.1.257"),
                mock.patch.object(host.CONFIG, "AVAILABILITY", new=availability_runtime),
                mock.patch.object(host.REGISTRY, "acquire_workspace_guard") as acquire,
                mock.patch.object(host.CONFIG.SAFETY, "popen_group") as popen,
                self.assertRaises(SystemExit) as raised,
            ):
                host.start(args)
            self.assertIn('"status": "unknown"', str(raised.exception))
            self.assertIn("Agent Collab stopped before run creation or peer launch", str(raised.exception))
            self.assertIn("claude-opus-5-5/xhigh", str(raised.exception))
            self.assertIn("No peer, helper, host-first-pass, or adjudicator was started", str(raised.exception))
            acquire.assert_not_called()
            popen.assert_not_called()
            self.assertFalse(guard.exists())
            self.assertFalse((run_root / "unknown-pair").exists())

    def test_start_preserves_empty_explicit_model_and_effort_for_rejection(self):
        host = load_host()
        availability = load_availability()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "diff", "--brief", "review",
                "--run-id", "empty-pair", "--run-root", str(run_root), "--repo-root", str(root),
                "--peer-model", "", "--peer-effort", "",
            ])
            resolved = {
                "settings": complete_settings(host), "sources": {},
                "layers": {
                    "global": {"path": "g", "settings": {}, "error": None},
                    "local": {"path": "l", "settings": {}, "error": None},
                },
            }
            seen = {}

            def check(peer, model, effort, *rest, **kwargs):
                seen.update(peer=peer, model=model, effort=effort)
                return availability.result(
                    status="unavailable",
                    peer=peer,
                    model=model,
                    effort=effort,
                    source="request_contract",
                    cli_version=None,
                    details="empty explicit selection",
                    evidence="empty explicit selection",
                )

            availability_runtime = SimpleNamespace(check=check)
            with (
                mock.patch.object(host.CONFIG, "resolve_settings", return_value=resolved),
                mock.patch.object(host.CONFIG, "require_peer_cli_version", return_value="2.1.257"),
                mock.patch.object(host.CONFIG, "AVAILABILITY", new=availability_runtime),
                mock.patch.object(host.REGISTRY, "acquire_workspace_guard") as acquire,
                self.assertRaises(SystemExit),
            ):
                host.start(args)
            self.assertEqual(seen, {"peer": "claude", "model": "", "effort": ""})
            acquire.assert_not_called()
            self.assertFalse((run_root / "empty-pair").exists())

    def test_start_safe_mode_fails_closed_before_availability_or_artifacts(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            guard = root / "active-v2.json"
            args = host.build_parser().parse_args([
                "start", "--host", "claude", "--target", "diff", "--brief", "review",
                "--run-id", "safe-fail", "--run-root", str(run_root), "--repo-root", str(root),
            ])
            resolved = {
                "settings": complete_settings(host, safe_mode=True),
                "sources": {},
                "layers": {
                    "global": {"path": "g", "settings": {}, "error": None},
                    "local": {"path": "l", "settings": {}, "error": None},
                },
            }
            sandbox = {
                "status": "sandbox_unavailable",
                "backend": "bwrap",
                "details": "user namespace unavailable",
            }
            with (
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard),
                mock.patch.object(host.CONFIG, "resolve_settings", return_value=resolved),
                mock.patch.object(host.CONFIG.SAFETY, "sandbox_preflight", return_value=sandbox),
                mock.patch.object(host.CONFIG, "require_peer_cli_version") as cli_version,
                mock.patch.object(host.CONFIG, "AVAILABILITY") as availability,
                mock.patch.object(host.REGISTRY, "acquire_workspace_guard") as acquire,
                mock.patch.object(host.CONFIG.SAFETY, "popen_group") as popen,
                self.assertRaises(SystemExit) as raised,
            ):
                host.start(args)
            failure = json.loads(str(raised.exception))
            self.assertEqual(failure["status"], "sandbox_unavailable")
            cli_version.assert_not_called()
            availability.check.assert_not_called()
            acquire.assert_not_called()
            popen.assert_not_called()
            self.assertFalse(run_root.exists())

    def test_start_saves_exact_per_run_override_and_v2_process(self):
        host = load_host()
        class FakePopen:
            pid = 43210
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "diff", "--brief", "review",
                "--run-id", "override", "--run-root", str(run_root), "--repo-root", str(root),
                "--peer-model", "claude-fable-5", "--peer-effort", "max", "--no-online-research",
                "--safe-mode", "--timeout-seconds", "900", "--local-subagents",
            ])
            resolved = {
                "settings": complete_settings(host, local_subagents_allowed=False),
                "sources": {},
                "layers": {
                    "global": {"path": "g", "settings": {}, "error": None},
                    "local": {"path": "l", "settings": {}, "error": None},
                },
            }
            attestation = availability_attestation("claude", "claude-fable-5", "max")
            availability_runtime = SimpleNamespace(
                check=lambda *args, **kwargs: attestation,
                validate_result=lambda *args, **kwargs: None,
            )
            with (
                mock.patch.object(host.CONFIG, "resolve_settings", return_value=resolved),
                mock.patch.object(host.CONFIG, "require_peer_cli_version", return_value="2.1.257"),
                mock.patch.object(host.CONFIG, "AVAILABILITY", new=availability_runtime),
                mock.patch.object(host.LIFECYCLE, "run_snapshot"),
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=root / "active-v2.json"),
                mock.patch.object(host.CONFIG.SAFETY, "sandbox_preflight") as sandbox_preflight,
                mock.patch.object(host.CONFIG.SAFETY, "process_identity", return_value=valid_process_identity()),
                mock.patch.object(host.CONFIG.SAFETY, "popen_group", return_value=FakePopen()),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(host.start(args), 0)
                start_output = json.loads(output.getvalue())
                args.run_id = "another-session"
                self.assertEqual(host.start(args), 0)
                self.assertEqual(set(host.REGISTRY.read_workspace_guards(root)[1]), {"override", "another-session"})
                self.assertEqual({job["id"] for job in host.CONFIG.STATE.list_jobs(run_root)},
                                 {"override", "another-session"})
            sandbox_preflight.assert_not_called()
            saved = json.loads((run_root / "override" / "host-request.json").read_text())
            process = json.loads((run_root / "override" / "peer-process.json").read_text())
            self.assertEqual(start_output['next_action']['stage'], 'first_pass')
            self.assertEqual(start_output['host_policy']['peer_timeout_seconds'], 900)
            self.assertFalse(start_output['host_policy']['online_research'])
            self.assertTrue(start_output['host_policy']['local_subagents_allowed'])
            action_args = host.build_parser().parse_args(start_output['next_action']['command'][2:])
            self.assertEqual(action_args.command, 'advance')
            self.assertEqual(action_args.run, str(run_root / 'override'))
            self.assertEqual(action_args.repo_root, root)
            self.assertEqual(action_args.first_pass, Path(start_output['next_action']['input_path']))
            private_modes = {
                "run_dir": stat.S_IMODE((run_root / "override").stat().st_mode),
                "request": stat.S_IMODE((run_root / "override" / "host-request.json").stat().st_mode),
                "report": stat.S_IMODE((run_root / "override" / "peer-report.json").stat().st_mode),
                "stderr": stat.S_IMODE((run_root / "override" / "peer.stderr.log").stat().st_mode),
                "state": stat.S_IMODE((run_root / "state.json").stat().st_mode),
                "state_lock": stat.S_IMODE((run_root / "state.lock").stat().st_mode),
            }
            self.assertTrue((root / "active-v2.json").exists())
        self.assertEqual(saved["peer_model"], "claude-fable-5")
        self.assertEqual(saved["peer_effort"], "max")
        self.assertEqual(saved["availability_attestation"], attestation)
        self.assertIs(saved["online_research"], False)
        self.assertIs(saved["safe_mode"], True)
        self.assertEqual(saved["peer_timeout_seconds"], 900)
        self.assertIs(saved["local_subagents_allowed"], True)
        self.assertIsNone(saved["claude_max_turns"])
        self.assertEqual(saved["helper_enforcement"]["limit_semantics"], "total_agent_calls")
        self.assertIs(saved["adjudicator_enabled"], True)
        self.assertEqual(process["schema_version"], "3.0")
        self.assertEqual(process["peer_cli_version"], "2.1.257")
        self.assertEqual(private_modes["run_dir"], 0o700)
        for name, mode in private_modes.items():
            if name != "run_dir":
                self.assertEqual(mode, 0o600, name)

    def test_nested_start_stops_before_artifacts(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "diff", "--brief", "review",
                "--run-id", "nested", "--run-root", str(root / "runs"), "--repo-root", str(root),
            ])
            with (
                mock.patch.dict(os.environ, {"AGENT_COLLAB_PEER_ONLY": "true"}, clear=False),
                self.assertRaises(SystemExit) as raised,
            ):
                host.start(args)
            self.assertIn("nested Agent Collab", str(raised.exception))
            self.assertFalse((root / "runs" / "nested").exists())

    def test_invalid_state_stops_before_guard_artifacts_or_peer_launch(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            run_root.mkdir()
            legacy_job = valid_state_job()
            legacy_job.pop("adjudicator_enabled")
            legacy_job["profile"] = "thorough"
            (run_root / "state.json").write_text(json.dumps({"schema_version": "3.0", "jobs": [legacy_job]}))
            guard = root / "active-v2.json"
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "diff", "--brief", "review",
                "--run-id", "strict-state", "--run-root", str(run_root), "--repo-root", str(root),
            ])
            with (
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard),
                mock.patch.object(host.REGISTRY, "acquire_workspace_guard") as acquire,
                mock.patch.object(host.CONFIG.SAFETY, "popen_group") as popen,
                self.assertRaises(SystemExit) as raised,
            ):
                host.start(args)
            self.assertIn("invalid v3 Agent Collab state", str(raised.exception))
            self.assertIn("fresh --run-root", str(raised.exception))
            acquire.assert_not_called()
            popen.assert_not_called()
            self.assertFalse(guard.exists())
            self.assertFalse((run_root / "strict-state").exists())

    def test_independent_runs_share_registry_and_release_only_their_own_entry(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / "active-v2.json"
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(host.REGISTRY.acquire_workspace_guard, root, run_id, origin,
                                               root / "runs" / run_id)
                               for run_id, origin in (("outer", "codex"), ("other", "claude"))]
                    for future in futures:
                        future.result(timeout=5)
                self.assertEqual(set(host.REGISTRY.read_workspace_guards(root)[1]), {"outer", "other"})
                with self.assertRaisesRegex(SystemExit, "already active"):
                    host.REGISTRY.acquire_workspace_guard(root, "outer", "codex", root / "runs" / "outer")
                host.REGISTRY.update_workspace_guard(root, "other", owner=host.REGISTRY.read_workspace_guard(root, "other")[1], phase="peer_running", peer_identity=valid_process_identity())
                host.REGISTRY.release_workspace_guard(root, "outer", owner=host.REGISTRY.read_workspace_guard(root, "outer")[1])
                self.assertEqual(host.REGISTRY.read_workspace_guard(root, "other")[1]["phase"], "peer_running")
                with self.assertRaisesRegex(SystemExit, "missing"):
                    host.REGISTRY.release_workspace_guard(root, "outer", owner=host.REGISTRY.read_workspace_guard(root, "outer")[1])
                host.REGISTRY.release_workspace_guard(root, "other", owner=host.REGISTRY.read_workspace_guard(root, "other")[1])
                self.assertFalse(guard.exists())

    def test_workspace_guard_lock_wait_is_bounded(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / "active-v2.json"
            with (
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard),
                mock.patch.object(host.REGISTRY, "WORKSPACE_GUARD_LOCK_TIMEOUT_SECONDS", 0.05),
                mock.patch.object(host.REGISTRY, "WORKSPACE_GUARD_LOCK_POLL_SECONDS", 0.005),
                host.REGISTRY.workspace_guard_transaction(root),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                blocked = executor.submit(host.REGISTRY.read_workspace_guards, root)
                with self.assertRaisesRegex(RuntimeError, "timed out acquiring"):
                    blocked.result(timeout=1)

    def test_stale_start_recovery_is_scoped_to_the_same_run_id(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / "active-v2.json"
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                host.REGISTRY.acquire_workspace_guard(root, "stale", "codex", root / "runs" / "stale")
                host.REGISTRY.acquire_workspace_guard(root, "other", "claude", root / "runs" / "other")
                with mock.patch.object(host.CONFIG.SAFETY, "process_identity_state", return_value="dead_or_replaced"):
                    host.REGISTRY.acquire_workspace_guard(root, "stale", "codex", root / "new" / "stale")
                self.assertEqual(set(host.REGISTRY.read_workspace_guards(root)[1]), {"stale", "other"})
                self.assertEqual(host.REGISTRY.read_workspace_guard(root, "stale")[1]["run_dir"], str(root / "new" / "stale"))
                host.REGISTRY.update_workspace_guard(root, "stale", owner=host.REGISTRY.read_workspace_guard(root, "stale")[1], phase="peer_running", peer_identity=valid_process_identity())
                with mock.patch.object(host.CONFIG.SAFETY, "process_identity_state", return_value="dead_or_replaced"):
                    with self.assertRaisesRegex(SystemExit, "already active"):
                        host.REGISTRY.acquire_workspace_guard(root, "stale", "codex", root / "runs" / "stale")

    def test_targeted_stale_start_recovery_preserves_other_runs(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=root / "guard.json"):
                host.REGISTRY.acquire_workspace_guard(root, "stale", "codex", root / "stale")
                host.REGISTRY.acquire_workspace_guard(root, "live", "claude", root / "live")
                host.REGISTRY.update_workspace_guard(root, "live", owner=host.REGISTRY.read_workspace_guard(root, "live")[1], phase="peer_running", peer_identity=valid_process_identity())
                with mock.patch.object(host.CONFIG.SAFETY, "process_identity_state", return_value="dead_or_replaced"):
                    host.REGISTRY.recover_workspace_guard(root, "stale")
                self.assertEqual(set(host.REGISTRY.read_workspace_guards(root)[1]), {"live"})

    def test_legacy_single_guard_is_rejected_without_overwriting_it(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / "guard.json"
            guard.write_text('{"schema_version":"3.0","run_id":"old"}')
            original = guard.read_bytes()
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                with self.assertRaisesRegex(SystemExit, "legacy"):
                    host.REGISTRY.require_workspace_guard_available(root)
            self.assertEqual(guard.read_bytes(), original)

    def test_stale_start_recovery_serializes_other_registry_updates(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / "active-v2.json"
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                host.REGISTRY.acquire_workspace_guard(root, "stale", "codex", root / "runs" / "stale")
                identity_checked = threading.Event()
                allow_recovery = threading.Event()
                def dead_launcher(_identity):
                    identity_checked.set()
                    self.assertTrue(allow_recovery.wait(timeout=2))
                    return "dead_or_replaced"
                with mock.patch.object(host.CONFIG.SAFETY, "process_identity_state", side_effect=dead_launcher), ThreadPoolExecutor(max_workers=2) as executor:
                    recovery = executor.submit(host.REGISTRY.acquire_workspace_guard, root, "stale", "codex", root / "new" / "stale")
                    self.assertTrue(identity_checked.wait(timeout=2))
                    other = executor.submit(host.REGISTRY.acquire_workspace_guard, root, "other", "claude", root / "runs" / "other")
                    time.sleep(0.05)
                    self.assertFalse(other.done())
                    allow_recovery.set()
                    recovery.result(timeout=2)
                    other.result(timeout=2)
                self.assertEqual(set(host.REGISTRY.read_workspace_guards(root)[1]), {"stale", "other"})

    def test_clear_history_never_deletes_the_guarded_orphan_run(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp_name:
            base = Path(tmp_name)
            root, run_root = base / "repo", base / "runs"
            root.mkdir()
            run_dir = run_root / "guarded-orphan"
            run_dir.mkdir(parents=True)
            (run_dir / "host-request.json").write_text("{}")
            other_dir = run_root / "other-orphan"
            other_dir.mkdir()
            (other_dir / "host-request.json").write_text("{}")
            guard = base / "active-v2.json"
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                host.REGISTRY.acquire_workspace_guard(root, "guarded-orphan", "codex", run_dir)
                host.REGISTRY.acquire_workspace_guard(root, "other-orphan", "claude", other_dir)
                args = host.build_parser().parse_args([
                    "clear-history", "--all", "--yes", "--run-root", str(run_root),
                    "--repo-root", str(root),
                ])
                status, output = host.ADMIN.build_clear_history_output(args)
            self.assertEqual(status, 0)
            self.assertEqual(output["deleted"], [])
            self.assertEqual({item["run_id"] for item in output["active_preserved"]}, {"guarded-orphan", "other-orphan"})
            self.assertTrue(run_dir.exists())
            self.assertTrue(other_dir.exists())
            self.assertTrue(guard.exists())

    def test_failed_start_rolls_back_guard_and_new_run_directory(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            guard = root / "active-v2.json"
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "diff", "--brief", "review",
                "--run-id", "failed", "--run-root", str(run_root), "--repo-root", str(root),
            ])
            resolved = {
                "settings": complete_settings(host), "sources": {},
                "layers": {
                    "global": {"path": "g", "settings": {}, "error": None},
                    "local": {"path": "l", "settings": {}, "error": None},
                },
            }
            attestation = availability_attestation("claude", "claude-opus-5-5", "xhigh")
            availability_runtime = SimpleNamespace(
                check=lambda *args, **kwargs: attestation,
                validate_result=lambda *args, **kwargs: None,
            )
            with (
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard),
                mock.patch.object(host.CONFIG, "resolve_settings", return_value=resolved),
                mock.patch.object(host.CONFIG, "require_peer_cli_version", return_value="2.1.257"),
                mock.patch.object(host.CONFIG, "AVAILABILITY", new=availability_runtime),
                mock.patch.object(host.LIFECYCLE, "run_snapshot"),
                mock.patch.object(host.CONFIG.SAFETY, "popen_group", side_effect=OSError("launch failed")),
                self.assertRaises(OSError),
            ):
                host.start(args)
            self.assertFalse(guard.exists())
            self.assertFalse((run_root / "failed").exists())

    def test_failed_start_retains_guard_when_process_cleanup_is_unproven(self):
        host = load_host()

        class SpawnedProcess:
            pid = 43210

            @staticmethod
            def poll():
                return None

            @staticmethod
            def wait(timeout=None):
                return 0

        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            guard = root / "active-v2.json"
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "diff", "--brief", "review",
                "--run-id", "cleanup-failed", "--run-root", str(run_root), "--repo-root", str(root),
            ])
            resolved = {
                "settings": complete_settings(host), "sources": {},
                "layers": {
                    "global": {"path": "g", "settings": {}, "error": None},
                    "local": {"path": "l", "settings": {}, "error": None},
                },
            }
            attestation = availability_attestation("claude", "claude-opus-5-5", "xhigh")
            availability_runtime = SimpleNamespace(
                check=lambda *args, **kwargs: attestation,
                validate_result=lambda *args, **kwargs: None,
            )
            with (
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard),
                mock.patch.object(host.CONFIG, "resolve_settings", return_value=resolved),
                mock.patch.object(host.CONFIG, "require_peer_cli_version", return_value="2.1.257"),
                mock.patch.object(host.CONFIG, "AVAILABILITY", new=availability_runtime),
                mock.patch.object(host.LIFECYCLE, "run_snapshot"),
                mock.patch.object(host.CONFIG.SAFETY, "popen_group", return_value=SpawnedProcess()),
                mock.patch.object(
                    host.CONFIG.SAFETY,
                    "process_identity",
                    side_effect=[valid_process_identity(os.getpid(), os.getpgrp()), RuntimeError("identity failed")],
                ),
                mock.patch.object(
                    host.CONFIG.SAFETY,
                    "terminate_process_group",
                    return_value={"outcome": "cleanup_failed", "quiescent": False, "pgid": 43210},
                ),
                self.assertRaisesRegex(RuntimeError, "guard and run artifacts were retained"),
            ):
                host.start(args)
            self.assertTrue(guard.exists())
            self.assertTrue((run_root / "cleanup-failed").exists())
            cleanup_artifact = json.loads(
                (run_root / "cleanup-failed" / "startup-cleanup-failure.json").read_text()
            )
            self.assertIs(cleanup_artifact["cleanup"]["quiescent"], False)

    def test_peer_process_is_exact_and_strict_v2(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / request()["run_id"]
            run_dir.mkdir(parents=True)
            process = valid_peer_process(run_dir, root, root / "guard")
            host.ARTIFACTS.validate_peer_process(process, request(), run_dir)
            for changed in (
                {**process, "extra": True},
                {**process, "pid": "43210"},
                {**process, "run_id": "wrong"},
            ):
                with self.assertRaises(host.ARTIFACTS.ArtifactValidationError):
                    host.ARTIFACTS.validate_peer_process(changed, request(), run_dir)

    def test_helper_reports_require_one_strict_v2_envelope(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            path = run_dir / "helper-reports.json"
            payload = {
                "schema_version": "3.0",
                "run_id": request()["run_id"],
                "reports": [{
                    "name": "reviewer", "summary": "checked",
                    "claims": [{"claim": "x", "status": "confirmed", "evidence": "test"}],
                }],
            }
            path.write_text(json.dumps(payload))
            claims = host.ARTIFACTS.load_helper_evidence(run_dir, request())[0]
            self.assertEqual(claims[0]["source"], "helper")
            duplicate = {**payload, "reports": payload["reports"] * 2}
            path.write_text(json.dumps(duplicate))
            with self.assertRaisesRegex(host.ARTIFACTS.ArtifactValidationError, "duplicate helper"):
                host.ARTIFACTS.load_helper_evidence(run_dir, request())[0]
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(host.ARTIFACTS.ArtifactValidationError, "resolved per-run limit is 0"):
                host.ARTIFACTS.load_helper_evidence(
                    run_dir,
                    request(local_subagents_allowed=False, max_local_subagents=8),
                )
            for legacy in ([payload["reports"][0]], {"claims": payload["reports"][0]["claims"]}):
                path.write_text(json.dumps(legacy))
                with self.assertRaises(host.ARTIFACTS.ArtifactValidationError):
                    host.ARTIFACTS.load_helper_evidence(run_dir, request())[0]

    def test_finish_then_complete_holds_and_releases_workspace_guard(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            run_dir = run_root / request()["run_id"]
            run_dir.mkdir(parents=True)
            guard = root / "active-v2.json"
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                host.REGISTRY.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                (run_dir / "host-request.json").write_text(json.dumps(request()))
                (run_dir / "peer-process.json").write_text(json.dumps(valid_peer_process(run_dir, root, guard)))
                (run_dir / "provider-process.json").write_text(json.dumps(valid_provider_process()))
                admit_host_input(host, run_dir, "host-first-pass.json", valid_host_first_pass())
                (run_dir / "peer-report.json").write_text(json.dumps(valid_report()))
                finish_args = host.build_parser().parse_args([
                    "finish", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                with (
                    mock.patch.object(host.LIFECYCLE, "wait_for_peer_report", return_value="report_ready"),
                    mock.patch.object(host.LIFECYCLE, "record_host_workspace_mutation", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.LIFECYCLE.finish(finish_args), 0)
                self.assertTrue(guard.exists())
                self.assertEqual(json.loads((run_dir / "host-result.json").read_text())["phase"], "ready_for_synthesis")
                self.assertEqual(host.CONFIG.STATE.find_job(run_root, request()["run_id"])["status"], "synthesizing")
                with (
                    mock.patch.object(host.LIFECYCLE, "wait_for_peer_report", return_value="report_ready"),
                    mock.patch.object(host.LIFECYCLE, "record_host_workspace_mutation", return_value=None),
                    mock.patch.object(
                        host.CONFIG.SAFETY,
                        "process_group_alive",
                        side_effect=AssertionError("historical PGID must not be rechecked"),
                    ),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.LIFECYCLE.finish(finish_args), 0)
                complete_args = host.build_parser().parse_args([
                    "complete", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                with (
                    mock.patch.object(host.LIFECYCLE, "record_host_workspace_mutation", return_value=None),
                    self.assertRaises(ValueError),
                ):
                    host.LIFECYCLE.complete(complete_args)
                self.assertTrue(guard.exists())
                complete_args.synthesis = author_synthesis_input(host, run_dir)
                adjudicator = json.loads((run_dir / "adjudicator-report.json").read_text())
                adjudicator.update(
                    status="ok",
                    summary="Adjudicated current evidence.",
                    claims=[{"claim": "current", "status": "confirmed", "evidence": "test"}],
                )
                admit_host_input(host, run_dir, "adjudicator-report.json", adjudicator)
                with (
                    mock.patch.object(host.LIFECYCLE, "record_host_workspace_mutation", return_value=None),
                    self.assertRaisesRegex(ValueError, "stale review"),
                ):
                    host.LIFECYCLE.complete(complete_args)
                with (
                    mock.patch.object(host.LIFECYCLE, "wait_for_peer_report", return_value="report_ready"),
                    mock.patch.object(host.LIFECYCLE, "record_host_workspace_mutation", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.LIFECYCLE.finish(finish_args), 0)
                complete_args.synthesis = author_synthesis_input(host, run_dir)
                with (
                    mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=True),
                    mock.patch.object(host.PROCESS_TREE, "process_alive", return_value=True),
                    mock.patch.object(host.LIFECYCLE, "record_host_workspace_mutation", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.LIFECYCLE.complete(complete_args), 0)
                self.assertFalse(guard.exists())
                self.assertEqual(json.loads((run_dir / "host-result.json").read_text())["phase"], "done")
                self.assertEqual(host.CONFIG.STATE.find_job(run_root, request()["run_id"])["status"], "completed")
                with self.assertRaises(SystemExit):
                    host.LIFECYCLE.finish(finish_args)
                self.assertEqual(
                    host.CONFIG.STATE.find_job(run_root, request()["run_id"])["status"],
                    "completed",
                )
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(host.LIFECYCLE.complete(complete_args), 0)
                host.REGISTRY.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                host.REGISTRY.update_workspace_guard(
                    root,
                    request()["run_id"], owner=host.REGISTRY.read_workspace_guard(root, request()["run_id"])[1],
                    phase="peer_running",
                    peer_identity=valid_process_identity(),
                )
                with self.assertRaisesRegex(SystemExit, "owner mismatch"):
                    host.LIFECYCLE.complete(complete_args)
                self.assertTrue(guard.exists())
                host.REGISTRY.release_workspace_guard(root, request()["run_id"], owner=host.REGISTRY.read_workspace_guard(root, request()["run_id"])[1])
                (run_dir / "host-synthesis.json").write_text(
                    json.dumps({**valid_host_synthesis(run_dir=run_dir), "final_answer_ready": False})
                )
                with self.assertRaisesRegex(ValueError, "cannot rewrite a completed synthesis"):
                    host.LIFECYCLE.complete(complete_args)

    def test_complete_requires_resynthesis_when_mutation_evidence_changes(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp_name:
            base = Path(tmp_name)
            root, run_root = base / "repo", base / "runs"
            root.mkdir()
            tracked = root / "tracked.txt"
            tracked.write_text("before", encoding="utf-8")
            run_dir = run_root / request()["run_id"]
            run_dir.mkdir(parents=True)
            guard = base / "active-v2.json"
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                host.REGISTRY.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                (run_dir / "host-request.json").write_text(json.dumps(request()))
                (run_dir / "peer-process.json").write_text(
                    json.dumps(valid_peer_process(run_dir, root, guard))
                )
                (run_dir / "provider-process.json").write_text(json.dumps(valid_provider_process()))
                admit_host_input(host, run_dir, "host-first-pass.json", valid_host_first_pass())
                (run_dir / "peer-report.json").write_text(json.dumps(valid_report()))
                host.LIFECYCLE.run_snapshot(root, run_dir / "before.snapshot", ignored_paths=[run_dir])
                finish_args = host.build_parser().parse_args([
                    "finish", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                with (
                    mock.patch.object(host.LIFECYCLE, "wait_for_peer_report", return_value="report_ready"),
                    mock.patch.object(host.CONFIG.SAFETY, "process_group_alive", return_value=False),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.LIFECYCLE.finish(finish_args), 0)
                draft = author_synthesis_input(host, run_dir)
                tracked.write_text("after", encoding="utf-8")
                complete_args = host.build_parser().parse_args([
                    "complete", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                complete_args.synthesis = draft
                with self.assertRaisesRegex(ValueError, "resynthesize"):
                    host.LIFECYCLE.complete(complete_args)
                self.assertTrue(guard.exists())
                refreshed = json.loads((run_dir / "host-result.json").read_text())
                self.assertIs(refreshed["workspace_mutation"]["changed"], True)
                self.assertIn("tracked.txt", refreshed["workspace_mutation"]["changed_paths"])
                synthesis = valid_host_synthesis(run_dir=run_dir)
                synthesis["workspace_mutation_sha256"] = refreshed["workspace_mutation_sha256"]
                complete_args.synthesis = author_synthesis_input(host, run_dir, synthesis)
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(host.LIFECYCLE.complete(complete_args), 0)
                self.assertFalse(guard.exists())

    def test_cancel_releases_workspace_guard_without_pid_reuse_risk(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            run_dir = run_root / request()["run_id"]
            run_dir.mkdir(parents=True)
            guard = root / "active-v2.json"
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                host.REGISTRY.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                (run_dir / "host-request.json").write_text(json.dumps(request()))
                (run_dir / "peer-process.json").write_text(json.dumps(valid_peer_process(run_dir, root, guard)))
                (run_dir / "provider-process.json").write_text(json.dumps(valid_provider_process()))
                (run_dir / "peer-report.json").write_text("{malformed")
                args = host.build_parser().parse_args([
                    "cancel", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                with (
                    mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=True),
                    mock.patch.object(host.PROCESS_TREE, "process_alive", return_value=False),
                    mock.patch.object(
                        host.CONFIG.SAFETY,
                        "terminate_process_group",
                        return_value={"outcome": "not_running", "quiescent": True, "pgid": 43210},
                    ),
                    mock.patch.object(
                        host.LIFECYCLE,
                        "record_cancel_workspace_mutation",
                        side_effect=host.ARTIFACTS.ArtifactValidationError("post-cancel snapshot failed"),
                    ),
                    self.assertRaises(ValueError),
                ):
                    host.LIFECYCLE.cancel(args)
                self.assertTrue(guard.exists())
                self.assertEqual((run_dir / "peer-report.json").read_text(), "{malformed")
                self.assertFalse((run_dir / "cancel-result.json").exists())
                with (
                    mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=True),
                    mock.patch.object(host.PROCESS_TREE, "process_alive", return_value=False),
                    mock.patch.object(
                        host.CONFIG.SAFETY,
                        "terminate_process_group",
                        return_value={"outcome": "not_running", "quiescent": True, "pgid": 43210},
                    ),
                    mock.patch.object(host.LIFECYCLE, "record_cancel_workspace_mutation", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.LIFECYCLE.cancel(args), 0)
                self.assertFalse(guard.exists())
                self.assertEqual(host.CONFIG.STATE.find_job(run_root, request()["run_id"])["status"], "cancelled")
                self.assertEqual((run_dir / "peer-report.json").read_text(), "{malformed")
                cancelled = json.loads((run_dir / "cancel-result.json").read_text())
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertEqual(cancelled["outcome"], "cancelled_quiescent")
                self.assertEqual(
                    cancelled["preserved_artifacts"]["peer_report"],
                    hashlib.sha256(b"{malformed").hexdigest(),
                )
                immutable_bytes = (run_dir / "cancel-result.json").read_bytes()
                with redirect_stdout(io.StringIO()) as retry_stdout:
                    self.assertEqual(host.LIFECYCLE.cancel(args), 0)
                self.assertEqual(json.loads(retry_stdout.getvalue())["outcome"], "already_cancelled")
                self.assertEqual((run_dir / "cancel-result.json").read_bytes(), immutable_bytes)

    def test_cancel_preserves_a_valid_terminal_report_and_closes_run(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            run_dir = run_root / request()["run_id"]
            run_dir.mkdir(parents=True)
            guard = root / "active-v2.json"
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                host.REGISTRY.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                (run_dir / "host-request.json").write_text(json.dumps(request()))
                (run_dir / "peer-process.json").write_text(
                    json.dumps(valid_peer_process(run_dir, root, guard))
                )
                (run_dir / "provider-process.json").write_text(json.dumps(valid_provider_process()))
                (run_dir / "peer-report.json").write_text(json.dumps(valid_report()))
                args = host.build_parser().parse_args([
                    "cancel", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                with (
                    mock.patch.object(host.CONFIG.SAFETY, "process_group_alive", return_value=False),
                    mock.patch.object(host.LIFECYCLE, "record_cancel_workspace_mutation", return_value=None) as mutation,
                    redirect_stdout(io.StringIO()) as stdout,
                ):
                    self.assertEqual(host.LIFECYCLE.cancel(args), 0)
                self.assertEqual(json.loads(stdout.getvalue())["outcome"], "cancelled_quiescent")
                mutation.assert_called_once()
                self.assertFalse(guard.exists())
                self.assertTrue((run_dir / "cancel-result.json").exists())
                self.assertEqual(json.loads((run_dir / "peer-report.json").read_text()), valid_report())

    def test_finish_cancel_race_preserves_cancellation_as_the_terminal_transition(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            run_dir = run_root / request()["run_id"]
            run_dir.mkdir(parents=True)
            guard = root / "active-v2.json"
            entered_wait = threading.Event()
            release_wait = threading.Event()
            finish_errors = []

            def wait_then_exit(*args, **kwargs):
                entered_wait.set()
                if not release_wait.wait(5):
                    raise AssertionError("finish wait was not released")
                return "peer_exited"

            with (
                mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard),
                mock.patch.object(host.LIFECYCLE, "wait_for_peer_report", side_effect=wait_then_exit),
                mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=True),
                mock.patch.object(host.PROCESS_TREE, "process_alive", return_value=False),
                mock.patch.object(
                    host.CONFIG.SAFETY,
                    "terminate_process_group",
                    return_value={"outcome": "not_running", "quiescent": True, "pgid": 43210},
                ),
                mock.patch.object(host.LIFECYCLE, "record_cancel_workspace_mutation", return_value=None),
            ):
                host.REGISTRY.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                (run_dir / "host-request.json").write_text(json.dumps(request()))
                (run_dir / "peer-process.json").write_text(
                    json.dumps(valid_peer_process(run_dir, root, guard))
                )
                (run_dir / "provider-process.json").write_text(json.dumps(valid_provider_process()))
                admit_host_input(host, run_dir, "host-first-pass.json", valid_host_first_pass())
                (run_dir / "peer-report.json").write_text("{malformed")
                finish_args = host.build_parser().parse_args([
                    "finish", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                cancel_args = host.build_parser().parse_args([
                    "cancel", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])

                def run_finish():
                    try:
                        host.LIFECYCLE.finish(finish_args)
                    except BaseException as exc:
                        finish_errors.append(exc)

                thread = threading.Thread(target=run_finish)
                thread.start()
                self.assertTrue(entered_wait.wait(5))
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(host.LIFECYCLE.cancel(cancel_args), 0)
                release_wait.set()
                thread.join(5)
                self.assertFalse(thread.is_alive())

            self.assertEqual(len(finish_errors), 1)
            self.assertIn("cancelled while finish was waiting", str(finish_errors[0]))
            self.assertFalse(guard.exists())
            self.assertTrue((run_dir / "cancel-result.json").exists())
            self.assertFalse((run_dir / "host-result.json").exists())
            self.assertEqual(
                host.CONFIG.STATE.find_job(run_root, request()["run_id"])["status"],
                "cancelled",
            )

    def test_finish_complete_race_does_not_reactivate_completed_state(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            run_dir = run_root / request()["run_id"]
            run_dir.mkdir(parents=True)
            guard = root / "active-v2.json"
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                host.REGISTRY.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                (run_dir / "host-request.json").write_text(json.dumps(request()))
                (run_dir / "peer-process.json").write_text(
                    json.dumps(valid_peer_process(run_dir, root, guard))
                )
                (run_dir / "provider-process.json").write_text(json.dumps(valid_provider_process()))
                admit_host_input(host, run_dir, "host-first-pass.json", valid_host_first_pass())
                (run_dir / "peer-report.json").write_text(json.dumps(valid_report()))
                finish_args = host.build_parser().parse_args([
                    "finish", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                with (
                    mock.patch.object(host.LIFECYCLE, "wait_for_peer_report", return_value="report_ready"),
                    mock.patch.object(host.LIFECYCLE, "record_host_workspace_mutation", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.LIFECYCLE.finish(finish_args), 0)
                draft = author_synthesis_input(host, run_dir)

                entered_wait = threading.Event()
                release_wait = threading.Event()
                finish_errors = []

                def wait_then_exit(*args, **kwargs):
                    entered_wait.set()
                    if not release_wait.wait(5):
                        raise AssertionError("finish wait was not released")
                    return "report_ready"

                def run_finish():
                    try:
                        host.LIFECYCLE.finish(finish_args)
                    except BaseException as exc:
                        finish_errors.append(exc)

                complete_args = host.build_parser().parse_args([
                    "complete", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                complete_args.synthesis = draft
                with (
                    mock.patch.object(host.LIFECYCLE, "wait_for_peer_report", side_effect=wait_then_exit),
                    mock.patch.object(host.LIFECYCLE, "record_host_workspace_mutation", return_value=None),
                ):
                    thread = threading.Thread(target=run_finish)
                    thread.start()
                    self.assertTrue(entered_wait.wait(5))
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(host.LIFECYCLE.complete(complete_args), 0)
                    release_wait.set()
                    thread.join(5)
                    self.assertFalse(thread.is_alive())

            self.assertEqual(len(finish_errors), 1)
            self.assertIn("already terminal", str(finish_errors[0]))
            self.assertFalse(guard.exists())
            self.assertEqual(
                host.CONFIG.STATE.find_job(run_root, request()["run_id"])["status"],
                "completed",
            )

    def test_dead_wrapper_leader_with_live_group_keeps_guard_blocked(self):
        if os.name != "posix":
            self.skipTest("process-group lifecycle contract is POSIX-only")
        host = load_host()
        parent_code = (
            "import subprocess,sys,time;"
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
            "time.sleep(0.3)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", parent_code],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        identity = host.CONFIG.SAFETY.process_identity(process.pid)
        process.wait(timeout=2)
        try:
            with tempfile.TemporaryDirectory() as tmp_name:
                run_dir = Path(tmp_name)
                host.CONFIG.STRICT_JSON.write(
                    run_dir / "provider-process.json",
                    {
                        "schema_version": "3.0",
                        "run_id": request()["run_id"],
                        "pid": None,
                        "pgid": None,
                        "process_identity": None,
                        "status": "pending",
                        "cleanup_outcome": None,
                        "completed_at": None,
                    },
                )
                cleanup = host.PROCESS_TREE.terminate_tracked_process_tree(
                    {"pid": process.pid, "pgid": identity["pgid"], "process_identity": identity},
                    run_dir,
                    request(),
                )
            self.assertIs(cleanup["quiescent"], False)
            self.assertTrue(host.CONFIG.SAFETY.process_group_alive(identity["pgid"]))
        finally:
            try:
                os.killpg(identity["pgid"], signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_stale_provider_tracker_recovers_only_when_recorded_group_is_empty(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp_name:
            run_dir = Path(tmp_name)
            running = valid_provider_process(
                status="running", cleanup_outcome=None, completed_at=None
            )
            host.CONFIG.STRICT_JSON.write(run_dir / "provider-process.json", running)
            process_info = {
                "pid": 43210,
                "pgid": 43210,
                "process_identity": valid_process_identity(),
            }
            with (
                mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=False),
                mock.patch.object(host.CONFIG.SAFETY, "process_identity_matches", return_value=False),
                mock.patch.object(host.CONFIG.SAFETY, "process_group_alive", return_value=False),
            ):
                cleanup = host.PROCESS_TREE.terminate_tracked_process_tree(process_info, run_dir, request())
            self.assertIs(cleanup["quiescent"], True)
            terminal = json.loads((run_dir / "provider-process.json").read_text())
            self.assertEqual(terminal["status"], "quiescent")
            self.assertEqual(terminal["cleanup_outcome"], "identity_mismatch_group_empty")

            null_identity = valid_provider_process(
                status="cleanup_failed",
                process_identity=None,
                cleanup_outcome="identity_unavailable",
            )
            host.CONFIG.STRICT_JSON.write(run_dir / "provider-process.json", null_identity)
            with (
                mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=False),
                mock.patch.object(host.CONFIG.SAFETY, "process_group_alive", return_value=False),
            ):
                cleanup = host.PROCESS_TREE.terminate_tracked_process_tree(process_info, run_dir, request())
            self.assertIs(cleanup["quiescent"], True)
            terminal = json.loads((run_dir / "provider-process.json").read_text())
            host.ARTIFACTS.validate_provider_process(terminal, request())
            self.assertIsNone(terminal["process_identity"])
            self.assertEqual(terminal["cleanup_outcome"], "identity_mismatch_group_empty")

            host.CONFIG.STRICT_JSON.write(run_dir / "provider-process.json", running)

            def group_alive(pgid):
                return pgid == running["pgid"]

            with (
                mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=False),
                mock.patch.object(host.CONFIG.SAFETY, "process_identity_matches", return_value=False),
                mock.patch.object(host.CONFIG.SAFETY, "process_group_alive", side_effect=group_alive),
                mock.patch.object(host.CONFIG.SAFETY, "terminate_process_group") as terminate,
            ):
                cleanup = host.PROCESS_TREE.terminate_tracked_process_tree(process_info, run_dir, request())
            self.assertIs(cleanup["quiescent"], False)
            self.assertEqual(cleanup["provider"]["outcome"], "identity_mismatch_group_live")
            terminate.assert_not_called()

            escaped_tracker = valid_provider_process(
                status="cleanup_failed",
                cleanup_outcome="capture_channel_not_quiescent",
            )
            host.CONFIG.STRICT_JSON.write(run_dir / "provider-process.json", escaped_tracker)
            with (
                mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=False),
                mock.patch.object(host.CONFIG.SAFETY, "process_identity_matches", return_value=False),
                mock.patch.object(host.CONFIG.SAFETY, "process_group_alive", return_value=False),
            ):
                cleanup = host.PROCESS_TREE.terminate_tracked_process_tree(process_info, run_dir, request())
            self.assertIs(cleanup["quiescent"], False)
            self.assertEqual(cleanup["provider"]["outcome"], "capture_channel_not_quiescent")
            self.assertEqual(
                json.loads((run_dir / "provider-process.json").read_text())["status"],
                "cleanup_failed",
            )

    def test_finish_timeout_and_artifacts_require_v3(self):
        host = load_host()
        with self.assertRaises(ValueError):
            host.LIFECYCLE.resolve_finish_wait_timeout(None, {})
        with self.assertRaises(ValueError):
            host.LIFECYCLE.resolve_finish_wait_timeout(None, {"peer_timeout_seconds": "invalid"})
        self.assertEqual(
            host.LIFECYCLE.resolve_finish_wait_timeout(None, {"peer_timeout_seconds": 2700}),
            (2730.0, "peer_timeout_plus_grace"),
        )
        actual_wait, hard_deadline = host.LIFECYCLE.remaining_finish_wait_seconds(
            {"started_at_epoch": 100.0, "peer_timeout_seconds": 2700},
            2730.0,
            now=3000.0,
        )
        self.assertEqual(actual_wait, 0.0)
        self.assertEqual(hard_deadline, 2830.0)
        with self.assertRaises(host.ARTIFACTS.ArtifactValidationError):
            host.ARTIFACTS.validate_host_first_pass(
                {"schema_version": "1.0", "run_id": "x", "summary": "x", "claims": []},
                request(run_id="x"),
            )
        with self.assertRaises(host.ARTIFACTS.ArtifactValidationError):
            host.ARTIFACTS.validate_host_first_pass(
                {"schema_version": "3.0", "run_id": "x", "summary": "x", "claims": [], "extra": True},
                request(run_id="x"),
            )

        with tempfile.TemporaryDirectory() as tmp_name:
            run_dir = Path(tmp_name)
            disabled_request = request(adjudicator_enabled=False)
            result = {
                "schema_version": "3.0",
                "run_id": disabled_request["run_id"],
                "run_dir": str(run_dir),
                "phase": "ready_for_synthesis",
                "peer_status": "ok",
                "validation_status": "ok",
                "finish_wait_seconds": 60,
                "finish_timeout_source": "explicit",
                "normalization": str(run_dir / "peer-normalization.json"),
                "claim_matrix": str(run_dir / "claim-matrix.json"),
                "adjudicator_report": None,
                "workspace_mutation": None,
                "workspace_mutation_sha256": hashlib.sha256(b"null").hexdigest(),
                "claim_matrix_sha256": "0" * 64,
            }
            host.ARTIFACTS.validate_host_result(result, disabled_request, run_dir)
            with self.assertRaisesRegex(host.ARTIFACTS.ArtifactValidationError, "must be null"):
                host.ARTIFACTS.validate_host_result(
                    {**result, "adjudicator_report": str(run_dir / "adjudicator-report.json")},
                    disabled_request,
                    run_dir,
                )

    def test_finish_reaps_live_provider_after_dead_wrapper_at_hard_deadline(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp_name:
            root, run_root = Path(tmp_name), Path(tmp_name) / "runs"
            run_dir = run_root / request()["run_id"]
            run_dir.mkdir(parents=True)
            guard = root / "active-v2.json"
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=guard):
                host.REGISTRY.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                (run_dir / "host-request.json").write_text(json.dumps(request()))
                (run_dir / "peer-process.json").write_text(
                    json.dumps(
                        valid_peer_process(
                            run_dir,
                            root,
                            guard,
                            started_at="1970-01-01T00:00:00Z",
                            started_at_epoch=0.0,
                        )
                    )
                )
                (run_dir / "provider-process.json").write_text(
                    json.dumps(
                        valid_provider_process(
                            status="running", cleanup_outcome=None, completed_at=None
                        )
                    )
                )
                admit_host_input(host, run_dir, "host-first-pass.json", valid_host_first_pass())
                (run_dir / "peer-report.json").write_text("")
                args = host.build_parser().parse_args([
                    "finish", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                cleanup = {
                    "quiescent": True,
                    "provider": {"outcome": "terminated", "quiescent": True},
                    "wrapper": {"outcome": "not_running", "quiescent": True},
                }
                with (
                    mock.patch.object(host.LIFECYCLE, "wait_for_peer_report", return_value="peer_exited"),
                    mock.patch.object(host.PROCESS_TREE, "require_process_tree_quiescent", side_effect=host.ARTIFACTS.ArtifactValidationError("provider running")),
                    mock.patch.object(host.PROCESS_TREE, "terminate_tracked_process_tree", return_value=cleanup) as terminate,
                    mock.patch.object(host.LIFECYCLE, "record_host_workspace_mutation", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.LIFECYCLE.finish(args), 0)
                terminate.assert_called_once()
                timed_out = json.loads((run_dir / "peer-report.json").read_text())
                self.assertEqual(timed_out["error"]["kind"], "timeout")

    def test_finish_wait_distinguishes_ready_report_from_exited_peer(self):
        host = load_host()
        process = {"pid": 123, "process_identity": valid_process_identity(123, 123)}
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=False),
            mock.patch.object(host.PROCESS_TREE, "process_alive", return_value=False),
        ):
            report = Path(tmp) / "peer-report.json"
            self.assertEqual(host.LIFECYCLE.wait_for_peer_report(report, process, 2700), "peer_exited")
            report.write_text("{}")
            self.assertEqual(host.LIFECYCLE.wait_for_peer_report(report, process, 2700), "report_ready")
            with (
                mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=True),
                mock.patch.object(host.PROCESS_TREE, "process_alive", return_value=True),
            ):
                self.assertEqual(host.LIFECYCLE.wait_for_peer_report(report, process, 0), "timeout")

    def test_status_tolerates_partial_report_only_while_process_tree_is_active(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            run_dir = root / "runs" / request()["run_id"]
            run_dir.mkdir(parents=True)
            (run_dir / "host-request.json").write_text(json.dumps(request()))
            (run_dir / "peer-process.json").write_text(
                json.dumps(valid_peer_process(run_dir, root, root / "guard"))
            )
            (run_dir / "provider-process.json").write_text(
                json.dumps(
                    valid_provider_process(
                        status="running", cleanup_outcome=None, completed_at=None
                    )
                )
            )
            (run_dir / "peer-report.json").write_text('{"schema_version":')
            with (
                mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=False),
                mock.patch.object(host.CONFIG.SAFETY, "process_group_alive", return_value=False),
            ):
                summary = host.RUNS.summarize_run(run_dir)
            self.assertIs(summary["peer_alive"], True)
            self.assertIs(summary["provider_active"], True)
            self.assertIs(summary["process_tree_quiescent"], False)
            self.assertIs(summary["peer_report_incomplete"], True)

            (run_dir / "provider-process.json").write_text(json.dumps(valid_provider_process()))
            with (
                mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=False),
                mock.patch.object(host.CONFIG.SAFETY, "process_group_alive", return_value=False),
                self.assertRaises(ValueError),
            ):
                host.RUNS.summarize_run(run_dir)

    def test_host_normalization_never_recovers_from_peer_raw(self):
        host = load_host()
        peer = load_peer()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "peer-report.json").write_text("not-json")
            (run_dir / "peer.raw.json").write_text(json.dumps(valid_report()))
            report, status = host.ARTIFACTS.normalize_peer_report_from_artifacts(peer, run_dir, request())
            metadata = json.loads((run_dir / "peer-normalization.json").read_text())
        self.assertEqual(status, "invalid_json")
        self.assertEqual(report["status"], "peer_failed")
        self.assertEqual(metadata["artifact_source"], "peer_report")

    def test_host_normalization_rejects_oversized_peer_report_with_bounded_read(self):
        host = load_host()
        peer = load_peer()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            report_path = run_dir / "peer-report.json"
            with report_path.open("wb") as handle:
                handle.write(b"{")
                handle.seek(17 * 1024 * 1024)
                handle.write(b"}")
            report, status = host.ARTIFACTS.normalize_peer_report_from_artifacts(
                peer,
                run_dir,
                request(),
            )
        self.assertEqual(status, "invalid_json")
        self.assertEqual(report["status"], "peer_failed")
        self.assertIn("16777216-byte limit", report["error"]["message"])

    def test_host_artifact_normalization_requires_direct_v3_report(self):
        host = load_host()
        peer = load_peer()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "peer-report.json").write_text(json.dumps({"structured_output": valid_report()}))
            report, status = host.ARTIFACTS.normalize_peer_report_from_artifacts(peer, run_dir, request())
            metadata = json.loads((run_dir / "peer-normalization.json").read_text())
        self.assertEqual(status, "noncanonical_output")
        self.assertEqual(report["error"]["kind"], "noncanonical_output")
        self.assertEqual(metadata["validation_status"], "noncanonical_output")

    def test_doctor_reports_invalid_state_and_returns_failure(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            run_root.mkdir()
            legacy_job = valid_state_job()
            legacy_job.pop("adjudicator_enabled")
            legacy_job["profile"] = "thorough"
            (run_root / "state.json").write_text(json.dumps({"schema_version": "3.0", "jobs": [legacy_job]}))
            args = host.build_parser().parse_args([
                "doctor", "--run-root", str(run_root), "--repo-root", str(root),
            ])
            output = io.StringIO()
            with mock.patch.object(host.shutil, "which", return_value=None), redirect_stdout(output):
                self.assertEqual(host.ADMIN.doctor(args), 1)
            report = json.loads(output.getvalue())
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["state_file"]["ok"])
        self.assertIn("missing required keys: ['adjudicator_enabled']", report["checks"]["state_file"]["error"])
        self.assertIn("Archive", report["checks"]["state_file"]["recovery"])
        self.assertTrue(report["checks"]["schemas"]["ok"])

    def test_state_and_snapshots_are_v3(self):
        state = load_state()
        snapshot = load_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state.json").write_text('{"version": 1, "jobs": [{"id": "old"}]}')
            with self.assertRaises(ValueError):
                state.load_state(root)
            for malformed in (
                {"schema_version": "3.0", "jobs": [{"id": "incomplete"}]},
                {"schema_version": "3.0", "jobs": [{**valid_state_job(), "legacy": True}]},
                {"schema_version": "3.0", "jobs": [{**valid_state_job(), "pid": "1"}]},
                {"schema_version": "3.0", "jobs": [{**valid_state_job(), "status": "running", "phase": "done"}]},
                {"schema_version": "3.0", "jobs": [{**valid_state_job(), "status": "cancelled", "phase": "cancelled"}]},
                {"schema_version": "3.0", "jobs": [{**valid_state_job(), "cancel_result": "/tmp/cancel-result.json"}]},
                {"schema_version": "3.0", "jobs": [valid_state_job(), valid_state_job()]},
            ):
                (root / "state.json").write_text(json.dumps(malformed))
                with self.assertRaises(ValueError):
                    state.load_state(root)
            (root / "state.json").unlink()
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: state.upsert_job(root, valid_state_job_patch("job-" + str(i))), range(8)))
            self.assertEqual(state.load_state(root)["schema_version"], "3.0")
            (root / "a.txt").write_text("a")
            self.assertEqual(snapshot.mutation_snapshot(root)["schema_version"], "3.0")
            self.assertEqual(snapshot.SNAPSHOT_HEADER, "agent_collab_workspace_snapshot_v3")

    def test_host_independently_records_workspace_mutation(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            run_dir = root / "runs" / request()["run_id"]
            run_dir.mkdir(parents=True)
            changed = root / "tracked.txt"
            changed.write_text("before", encoding="utf-8")
            host.LIFECYCLE.run_snapshot(root, run_dir / "before.snapshot", ignored_paths=[run_dir])
            changed.write_text("after", encoding="utf-8")
            diagnostic = host.LIFECYCLE.record_host_workspace_mutation(root, run_dir, request())

        self.assertIsNotNone(diagnostic)
        self.assertIs(diagnostic["changed"], True)
        self.assertEqual(diagnostic["source"], "host_snapshot")
        self.assertIn("tracked.txt", diagnostic["changed_paths"])

