from concurrent.futures import ThreadPoolExecutor
import filecmp
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, mock


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "tools" / "agent-collab"
CODEX_PLUGIN_ROOT = REPO_ROOT / "codex-plugin" / "agent-collab"
CODEX_SKILL_ROOT = CODEX_PLUGIN_ROOT / "skills" / "agent-collab"
CLAUDE_PLUGIN_ROOT = REPO_ROOT / "claude-plugin" / "agent-collab"
CLAUDE_SKILL_ROOT = CLAUDE_PLUGIN_ROOT / "skills" / "agent-collab"
PEER_RUNTIME = RUNTIME_ROOT / "scripts" / "peer.py"
HOST_RUNTIME = RUNTIME_ROOT / "scripts" / "host.py"
STATE_RUNTIME = RUNTIME_ROOT / "scripts" / "state.py"
SCHEMA = RUNTIME_ROOT / "schemas" / "peer-report.schema.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_peer_runtime():
    return load_module(PEER_RUNTIME, "agent_collab_peer")


def load_host_runtime():
    return load_module(HOST_RUNTIME, "agent_collab_host")


def load_state_runtime():
    return load_module(STATE_RUNTIME, "agent_collab_state_test")


def request(**overrides):
    data = {
        "origin": "codex",
        "host": "codex",
        "peer": "claude",
        "mode": "review",
        "target": "current diff",
        "brief": "Review this diff with quotes, `$VAR`, backticks, XML <x/>, and Unicode snowman \u2603.",
        "edit_allowed": False,
        "run_id": "agent-collab-test",
    }
    data.update(overrides)
    return data


def valid_peer_report(**overrides):
    data = {
        "schema_version": "1.0",
        "run_id": "agent-collab-test",
        "origin": "codex",
        "host": "codex",
        "peer": "claude",
        "mode": "review",
        "target": "current diff",
        "status": "ok",
        "verdict": "pass_with_concerns",
        "summary": "No blocking issues.",
        "findings": [],
        "claims": [
            {"claim": "Example claim", "status": "confirmed", "evidence": "test evidence"}
        ],
        "limitations": [],
        "next_actions": [],
    }
    data.update(overrides)
    return data


class RuntimeContractTests(TestCase):
    def test_prompt_embeds_request_json_and_ultra_defaults_without_pollution(self):
        runtime = load_peer_runtime()
        req = request()
        runtime.validate_request(req)

        prompt = runtime.build_prompt(req, REPO_ROOT, SCHEMA)

        self.assertIn("# Agent Collab Peer Request", prompt)
        self.assertIn("<role>", prompt)
        self.assertIn("You are an independent senior software reviewer.", prompt)
        self.assertIn("<objective>", prompt)
        self.assertIn("<context>", prompt)
        self.assertIn("<success_criteria>", prompt)
        self.assertIn("<constraints>", prompt)
        self.assertIn("<task_brief>", prompt)
        self.assertIn("<peer_contract>", prompt)
        self.assertIn("<prompt_contract>", prompt)
        self.assertIn("<request_json>", prompt)
        self.assertIn("<response_schema>", prompt)
        self.assertIn("<output_instruction>", prompt)
        self.assertIn("<structured_output_contract>", prompt)
        self.assertIn("Use latest official documentation for external/API/platform/dependency/tooling claims.", prompt)
        self.assertIn("Research online extensively when current external facts could affect the answer.", prompt)
        self.assertIn("Use native local subagents when that improves independent coverage or speed.", prompt)
        self.assertIn("divide work by independent lenses, wait for their results", prompt)
        self.assertIn("Profile: ultra", prompt)
        self.assertIn("Local subagents allowed: true", prompt)
        self.assertIn("Maximum local subagents: 6", prompt)
        self.assertIn("Do not invoke Agent Collab", prompt)
        self.assertIn("Do not modify files", prompt)
        self.assertNotIn("host conclusion", prompt.lower())
        embedded = prompt.split("<request_json>\n```json\n", 1)[1].split("\n```\n</request_json>", 1)[0]
        self.assertEqual(json.loads(embedded)["profile"], "ultra")

    def test_prompt_uses_mode_specific_roles(self):
        runtime = load_peer_runtime()

        expected_roles = {
            "review": "You are an independent senior software reviewer.",
            "audit": "You are an independent security and reliability auditor.",
            "brainstorm": "You are an independent technical ideation partner focused on repo-grounded architecture and design options.",
            "research": "You are an independent technical researcher.",
            "design": "You are an independent software architect.",
            "plan": "You are an independent implementation planner.",
            "plan-critique": "You are an independent plan reviewer.",
            "debug": "You are an independent debugging investigator.",
            "migration": "You are an independent migration architect.",
            "test-strategy": "You are an independent test strategist.",
            "verify": "You are an independent verifier.",
            "implement": "You are an independent implementation reviewer.",
        }

        for mode, role in expected_roles.items():
            with self.subTest(mode=mode):
                req = request(mode=mode)
                runtime.validate_request(req)
                prompt = runtime.build_prompt(req, REPO_ROOT, SCHEMA)
                role_block = prompt.split("<role>\n", 1)[1].split("\n</role>", 1)[0]
                self.assertIn(role, role_block)

    def test_host_parser_accepts_brainstorm_mode(self):
        host = load_host_runtime()

        parser = host.build_parser()
        args = parser.parse_args(
            [
                "start",
                "--host",
                "codex",
                "--mode",
                "brainstorm",
                "--target",
                "approach options",
                "--brief-file",
                "/tmp/brief.txt",
            ]
        )

        self.assertEqual(args.mode, "brainstorm")

    def test_agent_timeout_defaults_to_45_minutes_and_floors_at_45_minutes(self):
        runtime = load_peer_runtime()

        self.assertEqual(runtime.DEFAULT_AGENT_TIMEOUT_SECONDS, 2700)
        self.assertEqual(runtime.MIN_AGENT_TIMEOUT_SECONDS, 2700)
        self.assertEqual(runtime.timeout_seconds({}), 2700)
        self.assertEqual(runtime.timeout_seconds({"AGENT_COLLAB_TIMEOUT_SECONDS": "900"}), 2700)
        self.assertEqual(runtime.timeout_seconds({"AGENT_COLLAB_TIMEOUT_SECONDS": "2699"}), 2700)
        self.assertEqual(runtime.timeout_seconds({"AGENT_COLLAB_TIMEOUT_SECONDS": "2700"}), 2700)
        self.assertIsNone(runtime.timeout_seconds({"AGENT_COLLAB_TIMEOUT_SECONDS": "0"}))

    def test_host_default_storage_root_matches_runtime_location(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            packaged_root = repo_root / "codex-plugin" / "agent-collab" / "skills" / "agent-collab"
            with mock.patch.object(host, "resource_root", return_value=packaged_root):
                self.assertEqual(host.default_run_root(repo_root), packaged_root / "runs")
                self.assertEqual(host.default_local_settings_path(repo_root), packaged_root / "settings.local.json")

            tools_root = REPO_ROOT / "tools" / "agent-collab"
            with mock.patch.object(host, "resource_root", return_value=tools_root):
                self.assertEqual(host.default_run_root(repo_root), repo_root / "tools" / "agent-collab" / "runs")
                self.assertEqual(host.default_local_settings_path(repo_root), repo_root / "tools" / "agent-collab" / "settings.local.json")

    def test_utc_run_id_includes_entropy(self):
        host = load_host_runtime()

        with mock.patch.object(host.os, "urandom", side_effect=[b"\x00\x01\x02", b"\x00\x01\x03"]):
            first = host.utc_run_id("codex", "review")
            second = host.utc_run_id("codex", "review")

        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith("-codex-review-000102"))
        self.assertTrue(second.endswith("-codex-review-000103"))

    def test_claude_command_defaults_to_full_tools_and_bypass_permissions(self):
        runtime = load_peer_runtime()

        command = runtime.build_peer_command(
            request(peer="claude"),
            prompt="prompt text",
            repo_root=REPO_ROOT,
            schema_path=SCHEMA,
            output_path=Path("/tmp/out.json"),
            env={"AGENT_COLLAB_CLAUDE_ASSUME_FLAGS": "true"},
        )

        self.assertEqual(command.stdin, "prompt text")
        self.assertEqual(command.args[:2], ["claude", "-p"])
        self.assertNotIn("prompt text", command.args)
        self.assertEqual(command.args[command.args.index("--permission-mode") + 1], "bypassPermissions")
        self.assertEqual(command.args[command.args.index("--tools") + 1], "default")
        self.assertEqual(command.args[command.args.index("--model") + 1], "opus")
        self.assertEqual(command.args[command.args.index("--effort") + 1], "max")
        self.assertIn("--json-schema", command.args)
        self.assertIn("--no-session-persistence", command.args)
        self.assertIn("--max-budget-usd", command.args)
        self.assertIn("--max-turns", command.args)

    def test_claude_documented_flags_do_not_depend_on_help_visibility(self):
        runtime = load_peer_runtime()

        with mock.patch.object(runtime.subprocess, "run") as run:
            self.assertTrue(runtime.claude_supports_option("--max-turns", {}))
            self.assertTrue(runtime.claude_supports_option("--tools", {}))
            self.assertTrue(runtime.claude_supports_option("--permission-mode", {}))

        run.assert_not_called()

    def test_claude_command_uses_current_skip_permission_flag_when_permission_mode_is_unavailable(self):
        runtime = load_peer_runtime()

        def supports(option, env):
            return option in {
                "--dangerously-skip-permissions",
                "--model",
                "--effort",
                "--json-schema",
                "--output-format",
                "--no-session-persistence",
                "--max-budget-usd",
                "--max-turns",
            }

        with mock.patch.object(runtime, "claude_supports_option", side_effect=supports):
            command = runtime.build_peer_command(
                request(peer="claude"),
                prompt="prompt text",
                repo_root=REPO_ROOT,
                schema_path=SCHEMA,
                output_path=Path("/tmp/out.json"),
                env={},
            )

        self.assertIn("--dangerously-skip-permissions", command.args)
        self.assertNotIn("--permission-mode", command.args)
        self.assertNotIn("--tools", command.args)

    def test_claude_safe_mode_uses_plan_permissions(self):
        runtime = load_peer_runtime()

        command = runtime.build_peer_command(
            request(peer="claude"),
            prompt="prompt text",
            repo_root=REPO_ROOT,
            schema_path=SCHEMA,
            output_path=Path("/tmp/out.json"),
            env={"AGENT_COLLAB_SAFE_MODE": "1", "AGENT_COLLAB_CLAUDE_ASSUME_FLAGS": "true"},
        )

        self.assertEqual(command.args[command.args.index("--permission-mode") + 1], "plan")

    def test_claude_safe_mode_disallows_edit_tools_when_supported_without_permission_mode(self):
        runtime = load_peer_runtime()

        def supports(option, env):
            return option == "--disallowedTools"

        with mock.patch.object(runtime, "claude_supports_option", side_effect=supports):
            command = runtime.build_peer_command(
                request(peer="claude"),
                prompt="prompt text",
                repo_root=REPO_ROOT,
                schema_path=SCHEMA,
                output_path=Path("/tmp/out.json"),
                env={"AGENT_COLLAB_SAFE_MODE": "1"},
            )

        self.assertIn("--disallowedTools", command.args)
        self.assertEqual(command.args[command.args.index("--disallowedTools") + 1], "Edit,Write,MultiEdit")
        self.assertNotIn("--dangerously-skip-permissions", command.args)

    def test_claude_custom_tools_use_tools_flag_and_include_web_tools_when_web_research_is_enabled(self):
        runtime = load_peer_runtime()

        def supports(option, env):
            return option == "--tools"

        with mock.patch.object(runtime, "claude_supports_option", side_effect=supports):
            command = runtime.build_peer_command(
                request(peer="claude"),
                prompt="prompt text",
                repo_root=REPO_ROOT,
                schema_path=SCHEMA,
                output_path=Path("/tmp/out.json"),
                env={"CLAUDE_AGENT_COLLAB_TOOLS": "Read,Grep", "AGENT_COLLAB_WEB_RESEARCH": "cached"},
            )

        self.assertIn("--tools", command.args)
        self.assertNotIn("--allowedTools", command.args)
        self.assertNotIn("--allowed-tools", command.args)
        self.assertEqual(command.args[command.args.index("--tools") + 1], "Read,Grep,WebSearch,WebFetch")

    def test_claude_custom_tools_require_tools_flag(self):
        runtime = load_peer_runtime()

        with mock.patch.object(runtime, "claude_supports_option", return_value=False):
            with self.assertRaises(ValueError):
                runtime.build_peer_command(
                    request(peer="claude"),
                    prompt="prompt text",
                    repo_root=REPO_ROOT,
                    schema_path=SCHEMA,
                    output_path=Path("/tmp/out.json"),
                    env={"CLAUDE_AGENT_COLLAB_TOOLS": "Read,Grep"},
                )

    def test_claude_web_tools_are_removed_or_disallowed_when_web_research_is_disabled(self):
        runtime = load_peer_runtime()

        def supports(option, env):
            return option in {"--tools", "--disallowedTools"}

        with mock.patch.object(runtime, "claude_supports_option", side_effect=supports):
            default_command = runtime.build_peer_command(
                request(peer="claude"),
                prompt="prompt text",
                repo_root=REPO_ROOT,
                schema_path=SCHEMA,
                output_path=Path("/tmp/out.json"),
                env={"AGENT_COLLAB_WEB_RESEARCH": "disabled"},
            )
            custom_command = runtime.build_peer_command(
                request(peer="claude"),
                prompt="prompt text",
                repo_root=REPO_ROOT,
                schema_path=SCHEMA,
                output_path=Path("/tmp/out.json"),
                env={
                    "CLAUDE_AGENT_COLLAB_TOOLS": "Read,WebSearch,WebFetch,Grep",
                    "AGENT_COLLAB_WEB_RESEARCH": "disabled",
                },
            )

        self.assertIn("--disallowedTools", default_command.args)
        self.assertEqual(default_command.args[default_command.args.index("--disallowedTools") + 1], "WebSearch,WebFetch")
        self.assertEqual(custom_command.args[custom_command.args.index("--tools") + 1], "Read,Grep")

    def test_codex_command_uses_full_access_live_search_schema_and_stdin_prompt(self):
        runtime = load_peer_runtime()
        env = {
            "CODEX_AGENT_COLLAB_MODEL": "gpt-5.5",
            "CODEX_AGENT_COLLAB_EFFORT": "xhigh",
            "AGENT_COLLAB_CODEX_APPROVAL_FLAG": "bypass",
        }

        command = runtime.build_peer_command(
            request(peer="codex", host="claude", origin="claude"),
            prompt="prompt text",
            repo_root=REPO_ROOT,
            schema_path=SCHEMA,
            output_path=Path("/tmp/out.json"),
            env=env,
        )

        self.assertEqual(command.args[:3], ["codex", "exec", "--ephemeral"])
        self.assertEqual(command.stdin, "prompt text")
        self.assertNotIn("--search", command.args)
        self.assertIn('web_search="live"', command.args)
        self.assertEqual(command.args[command.args.index("--sandbox") + 1], "danger-full-access")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command.args)
        self.assertIn("--output-schema", command.args)
        self.assertIn("--output-last-message", command.args)
        self.assertEqual(command.args[command.args.index("--model") + 1], "gpt-5.5")
        self.assertIn('model_reasoning_effort="xhigh"', command.args)
        self.assertEqual(command.args[-1], "-")

    def test_codex_web_research_modes_map_to_web_search_config(self):
        runtime = load_peer_runtime()

        for mode in ("live", "cached", "disabled"):
            with self.subTest(mode=mode):
                command = runtime.build_peer_command(
                    request(peer="codex", host="claude", origin="claude"),
                    prompt="prompt text",
                    repo_root=REPO_ROOT,
                    schema_path=SCHEMA,
                    output_path=Path("/tmp/out.json"),
                    env={"AGENT_COLLAB_WEB_RESEARCH": mode, "AGENT_COLLAB_CODEX_APPROVAL_FLAG": "bypass"},
                )

                self.assertIn(f'web_search="{mode}"', command.args)

    def test_codex_config_is_passed_before_required_agent_collab_overrides(self):
        runtime = load_peer_runtime()

        command = runtime.build_peer_command(
            request(peer="codex", host="claude", origin="claude"),
            prompt="prompt text",
            repo_root=REPO_ROOT,
            schema_path=SCHEMA,
            output_path=Path("/tmp/out.json"),
            env={
                "CODEX_AGENT_COLLAB_CONFIG": json.dumps(
                    ['web_search="disabled"', 'model_reasoning_effort="low"', "experimental=true"]
                ),
                "CODEX_AGENT_COLLAB_EFFORT": "xhigh",
                "AGENT_COLLAB_WEB_RESEARCH": "live",
                "AGENT_COLLAB_CODEX_APPROVAL_FLAG": "bypass",
            },
        )

        config_values = [
            command.args[index + 1]
            for index, value in enumerate(command.args[:-1])
            if value == "-c"
        ]
        self.assertEqual(
            config_values,
            [
                'web_search="disabled"',
                'model_reasoning_effort="low"',
                "experimental=true",
                'model_reasoning_effort="xhigh"',
                'web_search="live"',
            ],
        )

    def test_codex_command_uses_official_approval_flag_when_supported(self):
        runtime = load_peer_runtime()

        command = runtime.build_peer_command(
            request(peer="codex", host="claude", origin="claude"),
            prompt="prompt text",
            repo_root=REPO_ROOT,
            schema_path=SCHEMA,
            output_path=Path("/tmp/out.json"),
            env={"AGENT_COLLAB_CODEX_APPROVAL_FLAG": "ask"},
        )

        self.assertIn("--ask-for-approval", command.args)
        self.assertEqual(command.args[command.args.index("--ask-for-approval") + 1], "never")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command.args)

    def test_codex_safe_mode_uses_read_only_without_bypass(self):
        runtime = load_peer_runtime()

        command = runtime.build_peer_command(
            request(peer="codex", host="claude", origin="claude"),
            prompt="prompt text",
            repo_root=REPO_ROOT,
            schema_path=SCHEMA,
            output_path=Path("/tmp/out.json"),
            env={"AGENT_COLLAB_SAFE_MODE": "1"},
        )

        self.assertEqual(command.args[command.args.index("--sandbox") + 1], "read-only")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command.args)

    def test_nested_invocation_returns_structured_failure(self):
        runtime = load_peer_runtime()

        result = runtime.run_request(request(), REPO_ROOT, env={"AGENT_COLLAB_CROSS_AGENT_DEPTH": "1"})

        self.assertEqual(result["status"], "peer_failed")
        self.assertEqual(result["error"]["kind"], "nested_invocation_refused")
        self.assertEqual(result["run_id"], "agent-collab-test")

    def test_peer_guard_blocks_host_cli(self):
        runtime = load_peer_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            guard = runtime.make_host_cli_guard(Path(tmp), "codex")
            completed = subprocess.run(
                [str(guard / "codex")],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(completed.returncode, 64)
        self.assertIn("host", completed.stderr)

    def test_missing_cli_returns_structured_failure(self):
        runtime = load_peer_runtime()

        with mock.patch.object(runtime.shutil, "which", return_value=None):
            result = runtime.invoke_peer(request(), REPO_ROOT, env={})

        self.assertEqual(result["status"], "peer_failed")
        self.assertEqual(result["error"]["kind"], "missing_cli")
        self.assertIn("claude", result["error"]["message"])

    def test_invalid_peer_json_returns_structured_failure(self):
        runtime = load_peer_runtime()
        completed = subprocess.CompletedProcess(["claude"], 0, stdout="not json", stderr="")

        with mock.patch.object(runtime.shutil, "which", return_value="/usr/bin/claude"):
            with mock.patch.object(runtime.subprocess, "run", return_value=completed):
                result = runtime.invoke_peer(request(), REPO_ROOT, env={})

        self.assertEqual(result["status"], "peer_failed")
        self.assertEqual(result["error"]["kind"], "invalid_json")

    def test_claude_api_error_envelope_returns_peer_api_error(self):
        runtime = load_peer_runtime()
        envelope = {
            "type": "result",
            "subtype": "success",
            "is_error": True,
            "api_error_status": 500,
            "duration_ms": 1234,
            "num_turns": 3,
            "result": "API Error: connection closed while thinking",
            "session_id": "session-123",
            "total_cost_usd": 1.25,
            "permission_denials": [],
            "modelUsage": {"claude-opus": {"costUSD": 1.25}},
        }
        completed = subprocess.CompletedProcess(["claude"], 1, stdout=json.dumps(envelope), stderr="")

        with mock.patch.object(runtime.shutil, "which", return_value="/usr/bin/claude"):
            with mock.patch.object(runtime.subprocess, "run", return_value=completed):
                result = runtime.invoke_peer(request(), REPO_ROOT, env={})

        self.assertEqual(result["status"], "peer_failed")
        self.assertEqual(result["error"]["kind"], "peer_api_error")
        self.assertEqual(result["error"]["message"], "API Error: connection closed while thinking")
        self.assertEqual(result["error"]["details"]["api_error_status"], 500)
        self.assertEqual(result["error"]["details"]["session_id"], "session-123")
        self.assertEqual(result["error"]["details"]["num_turns"], 3)

    def test_parse_claude_structured_output_envelope(self):
        runtime = load_peer_runtime()
        report = valid_peer_report(summary="Parsed from structured output.")
        envelope = {
            "type": "result",
            "subtype": "success",
            "result": "Done.",
            "structured_output": report,
        }

        parsed = runtime.parse_json_payload(json.dumps(envelope))

        self.assertEqual(parsed["summary"], "Parsed from structured output.")
        runtime.validate_peer_report(parsed)

    def test_parse_claude_result_json_envelope(self):
        runtime = load_peer_runtime()
        report = valid_peer_report(summary="Parsed from result JSON.")
        envelope = {
            "type": "result",
            "subtype": "success",
            "result": json.dumps(report),
        }

        parsed = runtime.parse_json_payload(json.dumps(envelope))

        self.assertEqual(parsed["summary"], "Parsed from result JSON.")
        runtime.validate_peer_report(parsed)

    def test_parse_claude_result_with_prose_before_json(self):
        runtime = load_peer_runtime()
        report = valid_peer_report(summary="Recovered after prose.")
        envelope = {
            "type": "result",
            "subtype": "success",
            "result": "I reviewed the repo and found the answer.\n\n```json\n"
            + json.dumps(report)
            + "\n```",
        }

        normalized = runtime.normalize_json_payload(json.dumps(envelope))

        self.assertEqual(normalized.report["summary"], "Recovered after prose.")
        self.assertEqual(normalized.metadata["source"], "result_embedded_json")
        self.assertIn("Recovered", normalized.metadata["warnings"][0])
        runtime.validate_peer_report(normalized.report)

    def test_parse_embedded_schema_valid_json_from_raw_text(self):
        runtime = load_peer_runtime()
        report = valid_peer_report(summary="Recovered from raw text.")

        normalized = runtime.normalize_json_payload("prefix\n" + json.dumps(report) + "\nsuffix")

        self.assertEqual(normalized.report["summary"], "Recovered from raw text.")
        self.assertEqual(normalized.metadata["source"], "embedded_json")
        runtime.validate_peer_report(normalized.report)

    def test_parse_claude_envelope_without_structured_report_fails_clearly(self):
        runtime = load_peer_runtime()
        envelope = {"type": "result", "subtype": "success", "result": "Done."}

        with self.assertRaises(ValueError):
            runtime.parse_json_payload(json.dumps(envelope))

    def test_raw_peer_output_is_written_before_normalization(self):
        runtime = load_peer_runtime()
        report = valid_peer_report()
        envelope = json.dumps({"type": "result", "structured_output": report})
        completed = subprocess.CompletedProcess(["claude"], 0, stdout=envelope, stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            raw_output = Path(tmp) / "peer.raw.json"
            with mock.patch.object(runtime.shutil, "which", return_value="/usr/bin/claude"):
                with mock.patch.object(runtime.subprocess, "run", return_value=completed):
                    result = runtime.invoke_peer(request(), REPO_ROOT, env={}, raw_output_path=raw_output)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(raw_output.read_text(encoding="utf-8"), envelope)
            normalization = json.loads((Path(tmp) / "peer-normalization.json").read_text())
            self.assertEqual(normalization["source"], "structured_output")
            self.assertEqual(normalization["validation_status"], "ok")

    def test_peer_report_schema_accepts_required_shape_and_rejects_missing_fields(self):
        runtime = load_peer_runtime()
        report = valid_peer_report()

        runtime.validate_peer_report(report)
        broken = dict(report)
        broken.pop("summary")
        with self.assertRaises(runtime.PeerReportValidationError):
            runtime.validate_peer_report(broken)
        malformed = valid_peer_report(
            findings=[
                {
                    "severity": "bogus",
                    "title": 123,
                    "details": False,
                    "confidence": "banana",
                    "extra": "x",
                }
            ],
            claims=[{"status": "confirmed", "source": "spoofed"}],
        )
        with self.assertRaises(runtime.PeerReportValidationError):
            runtime.validate_peer_report(malformed)

    def test_schema_mode_enums_include_brainstorm(self):
        host_request_schema = json.loads((RUNTIME_ROOT / "schemas" / "host-request.schema.json").read_text())
        peer_report_schema = json.loads((RUNTIME_ROOT / "schemas" / "peer-report.schema.json").read_text())

        self.assertIn("brainstorm", host_request_schema["properties"]["mode"]["enum"])
        self.assertIn("brainstorm", peer_report_schema["properties"]["mode"]["enum"])

    def test_git_snapshot_outputs_parseable_sections(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "snapshot.txt"
            host.run_snapshot(REPO_ROOT, output)
            text = output.read_text()

        self.assertIn("agent_collab_git_snapshot_v1", text)
        self.assertIn("-- status_porcelain_v1", text)
        self.assertIn("-- diff_name_status", text)


class HostRunnerTests(TestCase):
    def test_settings_precedence_env_local_global_builtin(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            global_home = Path(tmp) / "global"
            repo_root.mkdir()
            with mock.patch.dict(host.os.environ, {"AGENT_COLLAB_HOME": str(global_home)}, clear=False):
                host.write_settings_file(
                    host.default_global_settings_path(),
                    {"codex_model": "global-model", "claude_model": "global-claude", "profile": "standard"},
                )
                host.write_settings_file(
                    host.default_local_settings_path(repo_root),
                    {"codex_model": "local-model", "profile": "max"},
                )
                resolved = host.resolve_settings(repo_root, env={"CODEX_AGENT_COLLAB_MODEL": "env-model"})

        self.assertEqual(resolved["settings"]["codex_model"], "env-model")
        self.assertEqual(resolved["setting_sources"]["codex_model"] if "setting_sources" in resolved else resolved["sources"]["codex_model"], "env:CODEX_AGENT_COLLAB_MODEL")
        self.assertEqual(resolved["settings"]["claude_model"], "global-claude")
        self.assertEqual(resolved["settings"]["profile"], "max")

    def test_setup_writes_local_and_global_settings_and_reset_removes_them(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            global_home = Path(tmp) / "global"
            repo_root.mkdir()
            parser = host.build_parser()
            with mock.patch.dict(host.os.environ, {"AGENT_COLLAB_HOME": str(global_home)}, clear=False):
                local_args = parser.parse_args(
                    [
                        "setup",
                        "--scope",
                        "local",
                        "--no-input",
                        "--codex-model",
                        "gpt-test",
                        "--claude-model",
                        "opus-test",
                        "--web-research",
                        "cached",
                        "--codex-config",
                        "experimental=true",
                        "--codex-config",
                        "model_reasoning_summary=auto",
                        "--timeout-seconds",
                        "3600",
                        "--print-env",
                        "--repo-root",
                        str(repo_root),
                    ]
                )
                local_out = io.StringIO()
                with redirect_stdout(local_out):
                    host.setup(local_args)
                local_output = json.loads(local_out.getvalue())
                local_path = host.default_local_settings_path(repo_root)
                self.assertTrue(local_path.exists())
                self.assertEqual(local_output["env"]["CODEX_AGENT_COLLAB_MODEL"], "gpt-test")
                self.assertEqual(local_output["settings"]["web_research"], "cached")
                self.assertEqual(
                    local_output["settings"]["codex_config"],
                    ["experimental=true", "model_reasoning_summary=auto"],
                )
                self.assertEqual(local_output["env"]["AGENT_COLLAB_WEB_RESEARCH"], "cached")
                self.assertEqual(local_output["env"]["AGENT_COLLAB_HISTORY_RETAINED_RUNS"], "50")
                self.assertEqual(
                    json.loads(local_output["env"]["CODEX_AGENT_COLLAB_CONFIG"]),
                    ["experimental=true", "model_reasoning_summary=auto"],
                )
                self.assertEqual(local_output["settings"]["history_retained_runs"], 50)
                self.assertNotIn("CODEX_AGENT_COLLAB_WEB_SEARCH", local_output["env"])

                global_args = parser.parse_args(
                    [
                        "setup",
                        "--scope",
                        "global",
                        "--no-input",
                        "--claude-effort",
                        "high",
                        "--repo-root",
                        str(repo_root),
                    ]
                )
                with redirect_stdout(io.StringIO()):
                    host.setup(global_args)
                global_path = host.default_global_settings_path()
                self.assertTrue(global_path.exists())

                reset_args = parser.parse_args(["setup", "--reset", "all", "--repo-root", str(repo_root)])
                with redirect_stdout(io.StringIO()):
                    host.setup(reset_args)

            self.assertFalse(local_path.exists())
            self.assertFalse(global_path.exists())

    def test_setup_configures_history_retention_and_can_dry_run_clear_history(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            repo_root.mkdir()
            run_root = host.default_run_root(repo_root)
            run_dir = run_root / "completed-old"
            run_dir.mkdir(parents=True)
            (run_dir / "host-request.json").write_text(json.dumps(request(run_id="completed-old")), encoding="utf-8")
            state = host.load_state_runtime()
            state.upsert_job(run_root, {"id": "completed-old", "run_dir": str(run_dir), "status": "completed"})

            parser = host.build_parser()
            write_args = parser.parse_args(
                [
                    "setup",
                    "--scope",
                    "local",
                    "--no-input",
                    "--history-retained-runs",
                    "0",
                    "--repo-root",
                    str(repo_root),
                ]
            )
            with redirect_stdout(io.StringIO()):
                host.setup(write_args)

            self.assertEqual(json.loads(host.default_local_settings_path(repo_root).read_text())["settings"]["history_retained_runs"], 0)

            args = parser.parse_args(
                [
                    "setup",
                    "--scope",
                    "local",
                    "--no-input",
                    "--clear-history",
                    "--dry-run",
                    "--repo-root",
                    str(repo_root),
                ]
            )
            output = io.StringIO()
            with redirect_stdout(output):
                host.setup(args)

            parsed = json.loads(output.getvalue())
            self.assertEqual(parsed["settings"]["history_retained_runs"], 0)
            self.assertEqual({item["run_id"] for item in parsed["history_cleanup"]["deleted"]}, {"completed-old"})
            self.assertTrue(run_dir.exists())

    def test_setup_clear_history_requires_confirmation_without_dry_run(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            repo_root.mkdir()
            parser = host.build_parser()
            args = parser.parse_args(["setup", "--no-input", "--clear-history", "--repo-root", str(repo_root)])
            stderr = io.StringIO()
            with mock.patch.object(host.sys, "stderr", stderr):
                result = host.setup(args)

            self.assertEqual(result, 2)
            self.assertIn("--yes", stderr.getvalue())

    def test_setup_without_input_returns_clear_noninteractive_error(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            repo_root.mkdir()
            parser = host.build_parser()
            args = parser.parse_args(["setup", "--repo-root", str(repo_root)])
            stderr = io.StringIO()
            with mock.patch.object(host.sys, "stderr", stderr):
                with mock.patch.object(host, "interactive_setup", side_effect=EOFError):
                    result = host.setup(args)

            self.assertEqual(result, 2)
            self.assertIn("--no-input", stderr.getvalue())

    def test_state_upsert_preserves_concurrent_jobs(self):
        state = load_state_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"

            def add_job(index: int) -> None:
                state.upsert_job(run_root, {"id": f"job-{index:02d}", "status": "running"})

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(add_job, range(30)))

            jobs = state.list_jobs(run_root)
            self.assertEqual({job["id"] for job in jobs}, {f"job-{index:02d}" for index in range(30)})
            self.assertFalse(list(run_root.glob("state.json.*.tmp")))

    def test_state_pruning_keeps_active_jobs_and_caps_terminal_jobs(self):
        state = load_state_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            jobs = [
                {
                    "id": f"completed-{index:02d}",
                    "status": "completed",
                    "created_at": f"2026-01-01T00:{index:02d}:00Z",
                    "updated_at": f"2026-01-01T00:{index:02d}:00Z",
                }
                for index in range(55)
            ]
            jobs.append(
                {
                    "id": "still-running",
                    "status": "running",
                    "created_at": "2025-01-01T00:00:00Z",
                    "updated_at": "2025-01-01T00:00:00Z",
                }
            )

            state.save_state(run_root, {"jobs": jobs})
            kept = state.list_jobs(run_root)

            self.assertEqual(len(kept), state.MAX_JOBS)
            self.assertIn("still-running", {job["id"] for job in kept})

    def test_state_load_invalid_json_returns_default_with_warning(self):
        state = load_state_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            run_root.mkdir()
            (run_root / "state.json").write_text("{not json", encoding="utf-8")
            stderr = io.StringIO()
            with mock.patch.object(state.sys, "stderr", stderr):
                loaded = state.load_state(run_root)

            self.assertEqual(loaded, state.default_state())
            self.assertIn("invalid Agent Collab state file", stderr.getvalue())

    def test_state_find_job_ambiguous_prefix_raises(self):
        state = load_state_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            state.upsert_job(run_root, {"id": "agent-collab-alpha", "status": "completed"})
            state.upsert_job(run_root, {"id": "agent-collab-beta", "status": "completed"})

            with self.assertRaises(ValueError):
                state.find_job(run_root, "agent-collab")

    def test_start_creates_run_artifacts_and_launches_peer_first(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            brief = tmp_path / "brief.txt"
            brief.write_text("Review current diff.", encoding="utf-8")
            process = mock.Mock()
            process.pid = 12345

            parser = host.build_parser()
            args = parser.parse_args(
                [
                    "start",
                    "--host",
                    "codex",
                    "--mode",
                    "review",
                    "--target",
                    "current diff",
                    "--brief-file",
                    str(brief),
                    "--run-id",
                    "agent-collab-unit",
                    "--run-root",
                    str(tmp_path / "runs"),
                    "--repo-root",
                    str(REPO_ROOT),
                ]
            )
            with mock.patch.dict(host.os.environ, {}, clear=True):
                with mock.patch.object(host, "run_snapshot") as snapshot:
                    with mock.patch.object(host.subprocess, "Popen", return_value=process) as popen:
                        with redirect_stdout(io.StringIO()):
                            host.start(args)

            run_dir = tmp_path / "runs" / "agent-collab-unit"
            request_json = json.loads((run_dir / "host-request.json").read_text())
            self.assertEqual(request_json["profile"], "ultra")
            self.assertEqual(request_json["host"], "codex")
            self.assertEqual(request_json["peer"], "claude")
            self.assertTrue(request_json["local_subagents_allowed"])
            self.assertEqual(request_json["max_local_subagents"], 6)
            self.assertTrue((run_dir / "peer-process.json").exists())
            launched_args = popen.call_args.args[0]
            self.assertIn("--raw-output", launched_args)
            self.assertIn(str(run_dir / "peer.raw.json"), launched_args)
            self.assertIn("--normalization-output", launched_args)
            self.assertIn(str(run_dir / "peer-normalization.json"), launched_args)
            self.assertEqual(snapshot.call_count, 1)
            self.assertTrue(popen.called)

    def test_start_uses_settings_when_cli_values_are_not_supplied(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            brief = tmp_path / "brief.txt"
            brief.write_text("Review current diff.", encoding="utf-8")
            process = mock.Mock()
            process.pid = 12345
            repo_root = tmp_path / "repo"
            repo_root.mkdir()
            host.write_settings_file(
                host.default_local_settings_path(repo_root),
                {
                    "profile": "max",
                    "max_local_subagents": 3,
                    "local_subagents_allowed": False,
                    "codex_model": "gpt-settings",
                    "agent_timeout_seconds": "3600",
                },
            )

            parser = host.build_parser()
            args = parser.parse_args(
                [
                    "start",
                    "--host",
                    "claude",
                    "--mode",
                    "review",
                    "--target",
                    "current diff",
                    "--brief-file",
                    str(brief),
                    "--run-id",
                    "agent-collab-settings",
                    "--run-root",
                    str(tmp_path / "runs"),
                    "--repo-root",
                    str(repo_root),
                ]
            )
            with mock.patch.dict(host.os.environ, {}, clear=True):
                with mock.patch.object(host, "run_snapshot"):
                    with mock.patch.object(host.subprocess, "Popen", return_value=process) as popen:
                        with redirect_stdout(io.StringIO()):
                            host.start(args)

            run_dir = tmp_path / "runs" / "agent-collab-settings"
            request_json = json.loads((run_dir / "host-request.json").read_text())
            self.assertEqual(request_json["profile"], "max")
            self.assertFalse(request_json["local_subagents_allowed"])
            self.assertEqual(request_json["max_local_subagents"], 3)
            launched_env = popen.call_args.kwargs["env"]
            self.assertEqual(launched_env["CODEX_AGENT_COLLAB_MODEL"], "gpt-settings")
            self.assertEqual(launched_env["AGENT_COLLAB_TIMEOUT_SECONDS"], "3600")
            process_info = json.loads((run_dir / "peer-process.json").read_text())
            self.assertEqual(process_info["peer_timeout_seconds"], 3600)

    def test_start_records_indefinite_peer_timeout(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            brief = tmp_path / "brief.txt"
            brief.write_text("Review current diff.", encoding="utf-8")
            process = mock.Mock()
            process.pid = 12345
            repo_root = tmp_path / "repo"
            repo_root.mkdir()
            host.write_settings_file(
                host.default_local_settings_path(repo_root),
                {"agent_timeout_seconds": "0"},
            )

            parser = host.build_parser()
            args = parser.parse_args(
                [
                    "start",
                    "--host",
                    "codex",
                    "--mode",
                    "review",
                    "--target",
                    "current diff",
                    "--brief-file",
                    str(brief),
                    "--run-id",
                    "agent-collab-indefinite",
                    "--run-root",
                    str(tmp_path / "runs"),
                    "--repo-root",
                    str(repo_root),
                ]
            )
            with mock.patch.dict(host.os.environ, {}, clear=True):
                with mock.patch.object(host, "run_snapshot"):
                    with mock.patch.object(host.subprocess, "Popen", return_value=process):
                        with redirect_stdout(io.StringIO()):
                            host.start(args)

            process_info = json.loads((tmp_path / "runs" / "agent-collab-indefinite" / "peer-process.json").read_text())
            self.assertIsNone(process_info["peer_timeout_seconds"])

    def test_finish_wait_timeout_derives_from_metadata_and_overrides(self):
        host = load_host_runtime()

        timeout, source = host.resolve_finish_wait_timeout(None, {"peer_timeout_seconds": 3600})
        self.assertEqual(timeout, 3630)
        self.assertEqual(source, "peer_timeout_plus_grace")

        timeout, source = host.resolve_finish_wait_timeout(None, {"peer_timeout_seconds": None})
        self.assertIsNone(timeout)
        self.assertEqual(source, "peer_timeout_indefinite")

        timeout, source = host.resolve_finish_wait_timeout(0, {"peer_timeout_seconds": 3600})
        self.assertIsNone(timeout)
        self.assertEqual(source, "explicit_indefinite")

        timeout, source = host.resolve_finish_wait_timeout(None, {})
        self.assertEqual(timeout, 2700)
        self.assertEqual(source, "legacy_default")

    def test_finish_requires_host_first_pass_before_peer_read(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "host-request.json").write_text(json.dumps(request()), encoding="utf-8")
            (run_dir / "peer-process.json").write_text(
                json.dumps({"pid": 999999, "repo_root": str(REPO_ROOT)}),
                encoding="utf-8",
            )

            parser = host.build_parser()
            args = parser.parse_args(["finish", str(run_dir), "--timeout-seconds", "0", "--run-root", str(Path(tmp) / "runs")])
            with self.assertRaises(SystemExit):
                host.finish(args)

    def test_finish_writes_claim_matrix_and_adjudicator_placeholder(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "host-request.json").write_text(json.dumps(request()), encoding="utf-8")
            (run_dir / "peer-process.json").write_text(
                json.dumps({"pid": 999999, "repo_root": str(REPO_ROOT)}),
                encoding="utf-8",
            )
            (run_dir / "host-first-pass.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": "agent-collab-test",
                        "summary": "Independent host pass.",
                        "claims": [
                            {"claim": "Host claim", "status": "confirmed", "evidence": "host evidence"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            peer_report = valid_peer_report(
                claims=[
                    {
                        "claim": "Peer claim",
                        "status": "confirmed",
                        "evidence": "peer evidence",
                    }
                ]
            )
            (run_dir / "peer-report.json").write_text(json.dumps(peer_report), encoding="utf-8")

            parser = host.build_parser()
            args = parser.parse_args(["finish", str(run_dir), "--timeout-seconds", "0", "--run-root", str(Path(tmp) / "runs")])
            with mock.patch.object(host, "run_snapshot"):
                with redirect_stdout(io.StringIO()):
                    host.finish(args)

            claim_matrix = json.loads((run_dir / "claim-matrix.json").read_text())
            self.assertEqual([claim["source"] for claim in claim_matrix["claims"]], ["host", "peer"])
            adjudicator = json.loads((run_dir / "adjudicator-report.json").read_text())
            self.assertEqual(adjudicator["status"], "advisory_pending")

    def test_finish_claim_sources_cannot_be_spoofed(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "host-request.json").write_text(json.dumps(request()), encoding="utf-8")
            (run_dir / "peer-process.json").write_text(
                json.dumps({"pid": 999999, "repo_root": str(REPO_ROOT)}),
                encoding="utf-8",
            )
            (run_dir / "host-first-pass.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": "agent-collab-test",
                        "summary": "Independent host pass.",
                        "claims": [
                            {"claim": "Host claim", "status": "confirmed", "evidence": "host evidence", "source": "spoofed-host"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            peer_report = valid_peer_report(
                claims=[
                    {"claim": "Peer claim", "status": "confirmed", "evidence": "peer evidence"}
                ]
            )
            (run_dir / "peer-report.json").write_text(json.dumps(peer_report), encoding="utf-8")

            parser = host.build_parser()
            args = parser.parse_args(["finish", str(run_dir), "--timeout-seconds", "0", "--run-root", str(Path(tmp) / "runs")])
            with mock.patch.object(host, "run_snapshot"):
                with redirect_stdout(io.StringIO()):
                    host.finish(args)

            claim_matrix = json.loads((run_dir / "claim-matrix.json").read_text())
            self.assertEqual([claim["source"] for claim in claim_matrix["claims"]], ["host", "peer"])

    def test_finish_rewrites_invalid_peer_report_as_structured_failure(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "host-request.json").write_text(json.dumps(request()), encoding="utf-8")
            (run_dir / "peer-process.json").write_text(
                json.dumps({"pid": 999999, "repo_root": str(REPO_ROOT)}),
                encoding="utf-8",
            )
            (run_dir / "host-first-pass.json").write_text(
                json.dumps({"schema_version": "1.0", "run_id": "agent-collab-test", "summary": "Independent host pass.", "claims": []}),
                encoding="utf-8",
            )
            (run_dir / "peer-report.json").write_text("not json", encoding="utf-8")

            parser = host.build_parser()
            args = parser.parse_args(["finish", str(run_dir), "--timeout-seconds", "0", "--run-root", str(Path(tmp) / "runs")])
            with mock.patch.object(host, "run_snapshot"):
                with redirect_stdout(io.StringIO()):
                    host.finish(args)

            peer_report = json.loads((run_dir / "peer-report.json").read_text())
            self.assertEqual(peer_report["status"], "peer_failed")
            self.assertEqual(peer_report["error"]["kind"], "invalid_json")

    def test_finish_timeout_while_peer_alive_returns_still_running_without_peer_failure(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            run_root = Path(tmp) / "runs"
            (run_dir / "host-request.json").write_text(json.dumps(request()), encoding="utf-8")
            (run_dir / "peer-process.json").write_text(
                json.dumps({"pid": 12345, "repo_root": str(REPO_ROOT), "run_id": "agent-collab-test", "peer_timeout_seconds": 2700}),
                encoding="utf-8",
            )
            (run_dir / "host-first-pass.json").write_text(
                json.dumps({"schema_version": "1.0", "run_id": "agent-collab-test", "summary": "Independent host pass.", "claims": []}),
                encoding="utf-8",
            )

            parser = host.build_parser()
            args = parser.parse_args(["finish", str(run_dir), "--timeout-seconds", "0.5", "--run-root", str(run_root)])
            output = io.StringIO()
            with mock.patch.object(host, "process_alive", return_value=True):
                with mock.patch.object(host.time, "time", side_effect=[0, 2]):
                    with mock.patch.object(host, "run_snapshot") as snapshot:
                        with redirect_stdout(output):
                            result = host.finish(args)

            self.assertEqual(result, 1)
            parsed = json.loads(output.getvalue())
            self.assertEqual(parsed["status"], "peer_running")
            self.assertEqual(parsed["phase"], "waiting_for_peer")
            self.assertFalse((run_dir / "peer-report.json").exists())
            snapshot.assert_not_called()

    def test_finish_recovers_from_raw_when_peer_report_is_parser_failure_wrapper(self):
        host = load_host_runtime()
        peer = load_peer_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            run_root = Path(tmp) / "runs"
            (run_dir / "host-request.json").write_text(json.dumps(request()), encoding="utf-8")
            (run_dir / "peer-process.json").write_text(
                json.dumps({"pid": 999999, "repo_root": str(REPO_ROOT), "run_id": "agent-collab-test"}),
                encoding="utf-8",
            )
            (run_dir / "host-first-pass.json").write_text(
                json.dumps({"schema_version": "1.0", "run_id": "agent-collab-test", "summary": "Independent host pass.", "claims": []}),
                encoding="utf-8",
            )
            (run_dir / "peer-report.json").write_text(
                json.dumps(peer.failure("invalid_json", "result was not a JSON object", request())),
                encoding="utf-8",
            )
            recovered = valid_peer_report(summary="Recovered from raw Claude result.")
            raw_envelope = {
                "type": "result",
                "subtype": "success",
                "result": "Short preface before the report.\n\n" + json.dumps(recovered),
            }
            (run_dir / "peer.raw.json").write_text(json.dumps(raw_envelope), encoding="utf-8")

            parser = host.build_parser()
            args = parser.parse_args(["finish", str(run_dir), "--timeout-seconds", "0", "--run-root", str(run_root)])
            with mock.patch.object(host, "run_snapshot"):
                with redirect_stdout(io.StringIO()):
                    host.finish(args)

            peer_report = json.loads((run_dir / "peer-report.json").read_text())
            normalization = json.loads((run_dir / "peer-normalization.json").read_text())
            self.assertEqual(peer_report["summary"], "Recovered from raw Claude result.")
            self.assertEqual(normalization["artifact_source"], "peer_raw")
            self.assertEqual(normalization["source"], "result_embedded_json")
            self.assertEqual(normalization["validation_status"], "ok")

    def test_status_result_and_doctor_commands(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            run_dir = run_root / "agent-collab-test"
            run_dir.mkdir(parents=True)
            (run_dir / "host-request.json").write_text(json.dumps(request()), encoding="utf-8")
            (run_dir / "peer-process.json").write_text(
                json.dumps({"pid": 999999, "repo_root": str(REPO_ROOT), "run_id": "agent-collab-test"}),
                encoding="utf-8",
            )
            (run_dir / "peer-report.json").write_text(json.dumps(valid_peer_report()), encoding="utf-8")
            state = host.load_state_runtime()
            state.upsert_job(run_root, {"id": "agent-collab-test", "run_dir": str(run_dir), "status": "completed", "phase": "done"})

            parser = host.build_parser()
            status_args = parser.parse_args(["status", "--run-root", str(run_root), "--repo-root", str(REPO_ROOT)])
            status_out = io.StringIO()
            with redirect_stdout(status_out):
                host.status(status_args)
            self.assertEqual(json.loads(status_out.getvalue())["jobs"][0]["id"], "agent-collab-test")

            result_args = parser.parse_args(["result", "agent-collab", "--run-root", str(run_root), "--repo-root", str(REPO_ROOT)])
            result_out = io.StringIO()
            with redirect_stdout(result_out):
                host.result(result_args)
            self.assertEqual(json.loads(result_out.getvalue())["peer_report"]["status"], "ok")

            doctor_args = parser.parse_args(["doctor", "--run-root", str(run_root), "--repo-root", str(REPO_ROOT)])
            doctor_out = io.StringIO()
            def fake_capture(command, timeout=20):
                if command == ["codex", "exec", "--help"]:
                    return {
                        "available": True,
                        "returncode": 0,
                        "stdout": "--sandbox --output-schema --output-last-message --model -c --dangerously-bypass-approvals-and-sandbox",
                        "stderr": "",
                    }
                if command == ["claude", "--help"]:
                    return {
                        "available": True,
                        "returncode": 0,
                        "stdout": "--model --effort --json-schema --output-format --no-session-persistence --dangerously-skip-permissions --disallowedTools",
                        "stderr": "",
                    }
                if command == ["codex", "doctor", "--json"]:
                    return {
                        "available": True,
                        "returncode": 0,
                        "stdout": json.dumps({"overallStatus": "ok", "codexVersion": "0.test", "checks": {}}),
                        "stderr": "",
                    }
                if command == ["claude", "auth", "status", "--json"]:
                    return {
                        "available": True,
                        "returncode": 0,
                        "stdout": json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"}),
                        "stderr": "",
                    }
                return {"available": True, "returncode": 0, "stdout": "", "stderr": ""}

            with mock.patch.object(host.shutil, "which", return_value="/usr/bin/tool"):
                with mock.patch.object(host, "run_command_capture", side_effect=fake_capture):
                    with redirect_stdout(doctor_out):
                        host.doctor(doctor_args)
            doctor_output = json.loads(doctor_out.getvalue())
            self.assertTrue(doctor_output["ok"])
            self.assertEqual(doctor_output["checks"]["effective_web_research"]["value"], "live")
            self.assertTrue(doctor_output["checks"]["codex_web_config"]["value"]["config_flag_supported"])
            self.assertTrue(doctor_output["checks"]["claude_flags"]["value"]["effective"]["--max-turns"])
            self.assertFalse(doctor_output["checks"]["claude_flags"]["value"]["help_visible"]["--max-turns"])
            self.assertTrue(doctor_output["checks"]["claude_web_tools"]["value"]["tools_flag_supported"])
            self.assertTrue(doctor_output["checks"]["claude_web_tools"]["value"]["disallow_flag_supported"])

    def test_clear_history_prunes_terminal_runs_and_preserves_active_and_orphans(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            state = host.load_state_runtime()

            def make_run(run_id: str, timestamp: float) -> Path:
                run_dir = run_root / run_id
                run_dir.mkdir(parents=True)
                (run_dir / "host-request.json").write_text(json.dumps(request(run_id=run_id)), encoding="utf-8")
                os.utime(run_dir, (timestamp, timestamp))
                return run_dir

            make_run("completed-new", 300)
            make_run("completed-old", 200)
            make_run("orphan-old", 100)
            make_run("still-running", 50)
            state.save_state(
                run_root,
                {
                    "jobs": [
                        {
                            "id": "completed-new",
                            "run_dir": str(run_root / "completed-new"),
                            "status": "completed",
                            "updated_at": "2026-01-01T00:03:00Z",
                        },
                        {
                            "id": "completed-old",
                            "run_dir": str(run_root / "completed-old"),
                            "status": "failed",
                            "updated_at": "2026-01-01T00:02:00Z",
                        },
                        {
                            "id": "still-running",
                            "run_dir": str(run_root / "still-running"),
                            "status": "running",
                            "updated_at": "2026-01-01T00:00:50Z",
                        },
                    ]
                },
            )

            parser = host.build_parser()
            args = parser.parse_args(["clear-history", "--retain", "1", "--yes", "--run-root", str(run_root), "--repo-root", str(REPO_ROOT)])
            output = io.StringIO()
            with redirect_stdout(output):
                host.clear_history(args)

            parsed = json.loads(output.getvalue())
            self.assertEqual({item["run_id"] for item in parsed["deleted"]}, {"completed-old", "orphan-old"})
            self.assertEqual({item["run_id"] for item in parsed["active_preserved"]}, {"still-running"})
            self.assertTrue((run_root / "completed-new").exists())
            self.assertFalse((run_root / "completed-old").exists())
            self.assertFalse((run_root / "orphan-old").exists())
            self.assertTrue((run_root / "still-running").exists())
            self.assertEqual(
                {job["id"] for job in state.list_jobs(run_root)},
                {"completed-new", "still-running"},
            )

    def test_clear_history_dry_run_does_not_delete_or_update_state(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            run_dir = run_root / "completed-old"
            run_dir.mkdir(parents=True)
            (run_dir / "host-request.json").write_text(json.dumps(request(run_id="completed-old")), encoding="utf-8")
            state = host.load_state_runtime()
            state.upsert_job(run_root, {"id": "completed-old", "run_dir": str(run_dir), "status": "completed"})

            parser = host.build_parser()
            args = parser.parse_args(["clear-history", "--all", "--dry-run", "--run-root", str(run_root), "--repo-root", str(REPO_ROOT)])
            output = io.StringIO()
            with redirect_stdout(output):
                host.clear_history(args)

            parsed = json.loads(output.getvalue())
            self.assertEqual({item["run_id"] for item in parsed["deleted"]}, {"completed-old"})
            self.assertTrue(run_dir.exists())
            self.assertEqual({job["id"] for job in state.list_jobs(run_root)}, {"completed-old"})

    def test_clear_history_selected_active_run_is_preserved(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            run_dir = run_root / "still-running"
            run_dir.mkdir(parents=True)
            (run_dir / "host-request.json").write_text(json.dumps(request(run_id="still-running")), encoding="utf-8")
            state = host.load_state_runtime()
            state.upsert_job(run_root, {"id": "still-running", "run_dir": str(run_dir), "status": "running"})

            parser = host.build_parser()
            args = parser.parse_args(["clear-history", "--run", "still-running", "--yes", "--run-root", str(run_root), "--repo-root", str(REPO_ROOT)])
            output = io.StringIO()
            with redirect_stdout(output):
                host.clear_history(args)

            parsed = json.loads(output.getvalue())
            self.assertEqual(parsed["deleted"], [])
            self.assertEqual({item["run_id"] for item in parsed["active_preserved"]}, {"still-running"})
            self.assertTrue(run_dir.exists())

    def test_doctor_reports_not_ok_when_peer_clis_are_missing(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            parser = host.build_parser()
            args = parser.parse_args(["doctor", "--run-root", str(Path(tmp) / "runs"), "--repo-root", str(REPO_ROOT)])
            doctor_out = io.StringIO()
            with mock.patch.object(host.shutil, "which", return_value=None):
                with redirect_stdout(doctor_out):
                    host.doctor(args)

            output = json.loads(doctor_out.getvalue())
            self.assertFalse(output["ok"])
            self.assertFalse(output["checks"]["codex"]["ok"])
            self.assertFalse(output["checks"]["claude"]["ok"])

    def test_cancel_marks_job_cancelled_and_writes_failure_when_report_missing(self):
        host = load_host_runtime()

        with tempfile.TemporaryDirectory() as tmp:
            run_root = Path(tmp) / "runs"
            run_dir = run_root / "agent-collab-test"
            run_dir.mkdir(parents=True)
            (run_dir / "host-request.json").write_text(json.dumps(request()), encoding="utf-8")
            (run_dir / "peer-process.json").write_text(
                json.dumps({"pid": 12345, "repo_root": str(REPO_ROOT), "run_id": "agent-collab-test"}),
                encoding="utf-8",
            )
            state = host.load_state_runtime()
            state.upsert_job(run_root, {"id": "agent-collab-test", "run_dir": str(run_dir), "status": "running", "phase": "peer_running"})

            parser = host.build_parser()
            args = parser.parse_args(["cancel", "agent-collab-test", "--run-root", str(run_root), "--repo-root", str(REPO_ROOT)])
            with mock.patch.object(host, "terminate_process_group", return_value="terminated"):
                with redirect_stdout(io.StringIO()):
                    host.cancel(args)

            peer_report = json.loads((run_dir / "peer-report.json").read_text())
            jobs = state.list_jobs(run_root)
            self.assertEqual(peer_report["error"]["kind"], "cancelled")
            self.assertEqual(jobs[0]["status"], "cancelled")


class SkillMetadataTests(TestCase):
    def test_codex_and_claude_skill_metadata_allow_implicit_invocation(self):
        codex_manifest = CODEX_PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
        codex_skill = CODEX_SKILL_ROOT / "SKILL.md"
        codex_openai = CODEX_SKILL_ROOT / "agents" / "openai.yaml"
        claude_manifest = CLAUDE_PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
        claude_skill = CLAUDE_SKILL_ROOT / "SKILL.md"

        codex_plugin = json.loads(codex_manifest.read_text())
        codex_text = codex_skill.read_text()
        openai_text = codex_openai.read_text()
        claude_plugin = json.loads(claude_manifest.read_text())
        claude_text = claude_skill.read_text()

        self.assertEqual(codex_plugin["name"], "agent-collab")
        self.assertEqual(codex_plugin["skills"], "./skills/")
        self.assertEqual(codex_plugin["interface"]["displayName"], "Agent Collab")
        self.assertIn("name: agent-collab", codex_text)
        self.assertIn("allow_implicit_invocation: true", openai_text)
        self.assertIn('default_prompt: "Use $agent-collab', openai_text)
        self.assertEqual(claude_plugin["name"], "agent-collab")
        self.assertIn("when_to_use:", claude_text)
        self.assertIn("allowed-tools: Bash Read Grep Glob WebSearch WebFetch Edit Write MultiEdit Task", claude_text)
        self.assertNotIn("disable-model-invocation: true", claude_text)
        self.assertFalse((REPO_ROOT / ".agents" / "skills" / "agent-collab").exists())

    def test_packaged_resources_match_root_resources(self):
        for packaged in (CODEX_SKILL_ROOT, CLAUDE_SKILL_ROOT):
            for dirname in ("scripts", "references", "schemas"):
                comparison = filecmp.dircmp(RUNTIME_ROOT / dirname, packaged / dirname, ignore=["__pycache__"])
                self.assertEqual(comparison.left_only, [], f"{packaged}:{dirname}")
                self.assertEqual(comparison.right_only, [], f"{packaged}:{dirname}")
                self.assertEqual(comparison.diff_files, [], f"{packaged}:{dirname}")

            self.assertFalse((packaged / "tools").exists())

        for dirname in ("scripts", "references", "schemas"):
            self.assertFalse((CLAUDE_PLUGIN_ROOT / dirname).exists())

    def test_maintenance_scripts_are_executable(self):
        for script_name in (
            "sync-packages.sh",
            "sync-codex-skill.sh",
            "install-codex-skill.sh",
            "install-codex-plugin.sh",
            "install-claude-plugin.sh",
        ):
            script = REPO_ROOT / "scripts" / script_name
            self.assertTrue(script.exists())
            self.assertTrue(os.access(script, os.X_OK), f"{script} must be executable")

    def test_docs_use_peer_first_unpolluted_ultra_flow(self):
        docs = [
            REPO_ROOT / "README.md",
            CODEX_SKILL_ROOT / "SKILL.md",
            CLAUDE_SKILL_ROOT / "SKILL.md",
            RUNTIME_ROOT / "references" / "synthesize.md",
        ]

        for doc in docs:
            text = doc.read_text()
            self.assertIn("Default", text, str(doc))
            self.assertIn("ultra", text, str(doc))
            self.assertIn("neutral brief", text, str(doc))
            self.assertIn("before host analysis", text, str(doc))
            self.assertIn("Do not read", text, str(doc))
            self.assertIn("adjudicator", text, str(doc))
            self.assertIn("latest official documentation", text, str(doc))
            self.assertIn("research online extensively", text, str(doc))
            self.assertNotIn("There is no separate judge agent", text)

    def test_brainstorm_mode_is_documented_with_narrow_boundaries(self):
        readme = (REPO_ROOT / "README.md").read_text()
        codex_skill = (CODEX_SKILL_ROOT / "SKILL.md").read_text()
        claude_skill = (CLAUDE_SKILL_ROOT / "SKILL.md").read_text()

        for text in (readme, codex_skill, claude_skill):
            self.assertIn("brainstorm", text)
            self.assertIn("repo-grounded architecture brainstorming", text)
            self.assertIn("technical design ideation", text)
            self.assertIn("architecture tradeoffs", text)
            self.assertIn("casual brainstorming", text)
            self.assertIn("simple idea generation", text)

        self.assertIn("`brainstorm`: divergent repo-grounded option generation", readme)
        self.assertIn("`research`: source-backed facts", readme)
        self.assertIn("`design`: converge on one architecture", readme)
        self.assertIn("`plan`: implementation sequence", readme)

    def test_peer_guidance_distinguishes_brainstorm_research_design_and_plan(self):
        peer_only = (RUNTIME_ROOT / "references" / "peer-only.md").read_text()

        self.assertIn(
            "`brainstorm`: generate multiple viable repo-grounded technical options, compare tradeoffs and decision criteria, surface unknowns, and recommend the next direction without turning it into a detailed architecture or implementation plan unless asked.",
            peer_only,
        )
        self.assertIn("`research`: gather source-backed facts and current external evidence", peer_only)
        self.assertIn("`design`: converge on one repo-grounded architecture", peer_only)
        self.assertIn("`plan`: produce an implementation sequence", peer_only)
        self.assertIn("`plan-critique`: check ordering, assumptions, missing steps, rollback/verification gaps, and readiness", peer_only)

    def test_readme_reflects_current_architecture(self):
        readme = (REPO_ROOT / "README.md").read_text()

        self.assertIn("The default profile is `ultra`", readme)
        self.assertIn("tools/agent-collab/scripts/host.py", readme)
        self.assertIn("tools/agent-collab/scripts/peer.py", readme)
        self.assertIn("peer.raw.json", readme)
        self.assertIn("peer-normalization.json", readme)
        self.assertIn("host.py\" status", readme)
        self.assertIn("host.py\" result", readme)
        self.assertIn("host.py\" cancel", readme)
        self.assertIn("host.py\" setup", readme)
        self.assertIn("host.py\" doctor", readme)
        self.assertIn("settings.local.json", readme)
        self.assertIn("environment variables > local Agent Collab settings > global Agent Collab settings > built-in defaults", readme)
        self.assertIn("AGENT_COLLAB_WEB_RESEARCH", readme)
        self.assertIn("CODEX_AGENT_COLLAB_CONFIG", readme)
        self.assertIn("WebSearch", readme)
        self.assertNotIn("CODEX_AGENT_COLLAB_WEB_SEARCH", readme)
        self.assertIn("advisory adjudicator", readme)
        self.assertIn("--dangerously-skip-permissions", readme)
        self.assertIn("`finish` is the normal synchronization point", readme)
        self.assertIn("`status --wait` is for manual inspection and debugging", readme)
        self.assertIn("does not change Codex's background terminal polling model", readme)
        self.assertIn("host.py\" clear-history", readme)
        self.assertIn("history_retained_runs", readme)
        self.assertIn("setup --clear-history", readme)
        self.assertIn("scripts/", readme)
        self.assertIn("references/", readme)
        self.assertIn("schemas/", readme)

    def test_docs_make_finish_the_normal_wait_path(self):
        codex_skill = (CODEX_SKILL_ROOT / "SKILL.md").read_text()
        claude_skill = (CLAUDE_SKILL_ROOT / "SKILL.md").read_text()
        synth_docs = [
            RUNTIME_ROOT / "references" / "synthesize.md",
            CODEX_SKILL_ROOT / "references" / "synthesize.md",
            CLAUDE_SKILL_ROOT / "references" / "synthesize.md",
        ]

        for skill_text in (codex_skill, claude_skill):
            self.assertIn("Do not call `status --wait` during independent host analysis", skill_text)
            self.assertIn("`finish` is the normal synchronization point", skill_text)
            self.assertIn("Use `clear-history` to remove old terminal run artifacts", skill_text)

        for path in synth_docs:
            text = path.read_text()
            self.assertIn("status polling is not part of the normal independent-host phase", text, str(path))
            self.assertIn("Use `finish` as the synchronization point", text, str(path))

    def test_active_docs_do_not_reference_removed_legacy_entrypoints(self):
        active_paths = [
            REPO_ROOT / "README.md",
            RUNTIME_ROOT / "references" / "synthesize.md",
            CODEX_SKILL_ROOT / "references" / "synthesize.md",
            CLAUDE_SKILL_ROOT / "references" / "synthesize.md",
            CODEX_SKILL_ROOT / "SKILL.md",
            CLAUDE_SKILL_ROOT / "SKILL.md",
        ]

        for path in active_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("agent-collab-host.py", text, str(path))
            self.assertNotIn("agent-collab-peer.py", text, str(path))

    def test_active_host_config_is_untracked_and_ignored(self):
        tracked = subprocess.run(
            ["git", "ls-files", ".codex", ".claude", ".agents", "codex-skill"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        gitignore = (REPO_ROOT / ".gitignore").read_text()

        self.assertEqual(tracked.stdout.strip(), "")
        self.assertIn(".codex/", gitignore)
        self.assertIn(".claude/", gitignore)
        self.assertIn(".agents/", gitignore)
        self.assertIn("codex-skill/", gitignore)
        self.assertIn("codex-plugin/agent-collab/skills/agent-collab/runs/*", gitignore)
        self.assertIn("claude-plugin/agent-collab/skills/agent-collab/runs/*", gitignore)

    def test_claude_plugin_agents_are_plugin_compatible(self):
        agent_files = sorted((CLAUDE_PLUGIN_ROOT / "agents").glob("agent-collab-*.md"))
        names = {path.stem for path in agent_files}
        self.assertIn("agent-collab-adjudicator", names)
        self.assertIn("agent-collab-security-auditor", names)
        self.assertIn("agent-collab-test-strategist", names)

        for agent_file in agent_files:
            text = agent_file.read_text()
            self.assertIn("model: opus", text)
            self.assertIn("effort: max", text)
            self.assertIn("disallowedTools: Task", text)
            self.assertNotIn("permissionMode:", text)
            self.assertNotIn("hooks:", text)
            self.assertNotIn("mcpServers:", text)
            self.assertNotIn("tools: Read, Glob, Grep, Bash, WebSearch, WebFetch", text)
            self.assertIn("Use latest official documentation for external/API/platform/dependency/tooling claims.", text)
            self.assertIn("Research online extensively when current external facts could affect the answer.", text)
            self.assertIn("Do not invoke Agent Collab", text)

    def test_schema_files_are_valid_json(self):
        for schema in (RUNTIME_ROOT / "schemas").glob("*.schema.json"):
            json.loads(schema.read_text())

    def test_old_runtime_paths_are_removed(self):
        top_level_files = [path for path in RUNTIME_ROOT.iterdir() if path.is_file()]

        self.assertEqual(top_level_files, [])
        self.assertFalse((CODEX_SKILL_ROOT / "tools").exists())
        self.assertFalse((CLAUDE_PLUGIN_ROOT / "tools").exists())
        self.assertFalse((REPO_ROOT / "codex-skill").exists())
