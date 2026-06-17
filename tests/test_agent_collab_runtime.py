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
PEER_RUNTIME = RUNTIME_ROOT / "scripts" / "peer.py"
HOST_RUNTIME = RUNTIME_ROOT / "scripts" / "host.py"
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

        self.assertIn("Agent Collab peer request", prompt)
        self.assertIn("Use latest official documentation for external/API/platform/dependency/tooling claims.", prompt)
        self.assertIn("Research online extensively when current external facts could affect the answer.", prompt)
        self.assertIn("Use native local subagents when that improves independent coverage or speed.", prompt)
        self.assertIn("Profile: ultra", prompt)
        self.assertIn("Local subagents allowed: true", prompt)
        self.assertIn("Maximum local subagents: 6", prompt)
        self.assertIn("Do not invoke Agent Collab", prompt)
        self.assertIn("Do not modify files", prompt)
        self.assertNotIn("host conclusion", prompt.lower())
        embedded = prompt.split("REQUEST JSON:\n", 1)[1].split("\n\nRESPONSE SCHEMA", 1)[0]
        self.assertEqual(json.loads(embedded)["profile"], "ultra")

    def test_agent_timeout_defaults_to_45_minutes_and_floors_at_45_minutes(self):
        runtime = load_peer_runtime()

        self.assertEqual(runtime.DEFAULT_AGENT_TIMEOUT_SECONDS, 2700)
        self.assertEqual(runtime.MIN_AGENT_TIMEOUT_SECONDS, 2700)
        self.assertEqual(runtime.timeout_seconds({}), 2700)
        self.assertEqual(runtime.timeout_seconds({"AGENT_COLLAB_TIMEOUT_SECONDS": "900"}), 2700)
        self.assertEqual(runtime.timeout_seconds({"AGENT_COLLAB_TIMEOUT_SECONDS": "2699"}), 2700)
        self.assertEqual(runtime.timeout_seconds({"AGENT_COLLAB_TIMEOUT_SECONDS": "2700"}), 2700)
        self.assertIsNone(runtime.timeout_seconds({"AGENT_COLLAB_TIMEOUT_SECONDS": "0"}))

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

    def test_claude_safe_mode_uses_plan_permissions(self):
        runtime = load_peer_runtime()

        command = runtime.build_peer_command(
            request(peer="claude"),
            prompt="prompt text",
            repo_root=REPO_ROOT,
            schema_path=SCHEMA,
            output_path=Path("/tmp/out.json"),
            env={"AGENT_COLLAB_SAFE_MODE": "1"},
        )

        self.assertEqual(command.args[command.args.index("--permission-mode") + 1], "plan")

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
            self.assertEqual(snapshot.call_count, 1)
            self.assertTrue(popen.called)

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
            args = parser.parse_args(["finish", str(run_dir), "--timeout-seconds", "0"])
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
            args = parser.parse_args(["finish", str(run_dir), "--timeout-seconds", "0"])
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
            args = parser.parse_args(["finish", str(run_dir), "--timeout-seconds", "0"])
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
            args = parser.parse_args(["finish", str(run_dir), "--timeout-seconds", "0"])
            with mock.patch.object(host, "run_snapshot"):
                with redirect_stdout(io.StringIO()):
                    host.finish(args)

            peer_report = json.loads((run_dir / "peer-report.json").read_text())
            self.assertEqual(peer_report["status"], "peer_failed")
            self.assertEqual(peer_report["error"]["kind"], "invalid_peer_report")


class SkillMetadataTests(TestCase):
    def test_codex_and_claude_skill_metadata_allow_implicit_invocation(self):
        codex_skill = REPO_ROOT / "codex-skill" / "agent-collab" / "SKILL.md"
        codex_openai = REPO_ROOT / "codex-skill" / "agent-collab" / "agents" / "openai.yaml"
        claude_skill = REPO_ROOT / ".claude" / "skills" / "agent-collab" / "SKILL.md"

        codex_text = codex_skill.read_text()
        openai_text = codex_openai.read_text()
        claude_text = claude_skill.read_text()

        self.assertIn("name: agent-collab", codex_text)
        self.assertIn("allow_implicit_invocation: true", openai_text)
        self.assertIn('default_prompt: "Use $agent-collab', openai_text)
        self.assertIn("when_to_use:", claude_text)
        self.assertIn("allowed-tools: Bash Read Grep Glob WebSearch WebFetch Edit Write MultiEdit Task", claude_text)
        self.assertNotIn("disable-model-invocation: true", claude_text)
        self.assertFalse((REPO_ROOT / ".agents" / "skills" / "agent-collab").exists())

    def test_packaged_codex_resources_match_root_resources(self):
        packaged = REPO_ROOT / "codex-skill" / "agent-collab"

        for dirname in ("scripts", "references", "schemas"):
            comparison = filecmp.dircmp(RUNTIME_ROOT / dirname, packaged / dirname, ignore=["__pycache__"])
            self.assertEqual(comparison.left_only, [], dirname)
            self.assertEqual(comparison.right_only, [], dirname)
            self.assertEqual(comparison.diff_files, [], dirname)

        self.assertFalse((packaged / "tools").exists())

    def test_maintenance_scripts_are_executable(self):
        for script_name in ("sync-codex-skill.sh", "install-codex-skill.sh"):
            script = REPO_ROOT / "scripts" / script_name
            self.assertTrue(script.exists())
            self.assertTrue(os.access(script, os.X_OK), f"{script} must be executable")

    def test_docs_use_peer_first_unpolluted_ultra_flow(self):
        docs = [
            REPO_ROOT / "README.md",
            REPO_ROOT / "codex-skill" / "agent-collab" / "SKILL.md",
            REPO_ROOT / ".claude" / "skills" / "agent-collab" / "SKILL.md",
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

    def test_readme_reflects_current_architecture(self):
        readme = (REPO_ROOT / "README.md").read_text()

        self.assertIn("The default profile is `ultra`", readme)
        self.assertIn("tools/agent-collab/scripts/host.py", readme)
        self.assertIn("tools/agent-collab/scripts/peer.py", readme)
        self.assertIn("peer.raw.json", readme)
        self.assertIn("advisory adjudicator", readme)
        self.assertIn("--tools default", readme)
        self.assertIn("scripts/", readme)
        self.assertIn("references/", readme)
        self.assertIn("schemas/", readme)

    def test_active_docs_do_not_reference_removed_legacy_entrypoints(self):
        active_paths = [
            REPO_ROOT / "README.md",
            RUNTIME_ROOT / "references" / "synthesize.md",
            REPO_ROOT / "codex-skill" / "agent-collab" / "references" / "synthesize.md",
            REPO_ROOT / "codex-skill" / "agent-collab" / "SKILL.md",
            REPO_ROOT / ".claude" / "skills" / "agent-collab" / "SKILL.md",
        ]

        for path in active_paths:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("agent-collab-host.py", text, str(path))
            self.assertNotIn("agent-collab-peer.py", text, str(path))

    def test_codex_config_defaults_to_max_capability_with_depth_guard(self):
        config = (REPO_ROOT / ".codex" / "config.toml").read_text()

        self.assertIn('model = "gpt-5.5"', config)
        self.assertIn('model_reasoning_effort = "xhigh"', config)
        self.assertIn('sandbox_mode = "danger-full-access"', config)
        self.assertIn('approval_policy = "never"', config)
        self.assertIn('web_search = "live"', config)
        self.assertIn("max_threads = 8", config)
        self.assertIn("max_depth = 1", config)

    def test_codex_local_agents_use_full_capability_and_research_policy(self):
        agent_files = sorted((REPO_ROOT / ".codex" / "agents").glob("agent-collab-*.toml"))
        names = {path.stem for path in agent_files}
        self.assertIn("agent-collab-adjudicator", names)
        self.assertIn("agent-collab-security-auditor", names)
        self.assertIn("agent-collab-test-strategist", names)

        for agent_file in agent_files:
            text = agent_file.read_text()
            self.assertIn('model = "gpt-5.5"', text)
            self.assertIn('model_reasoning_effort = "xhigh"', text)
            self.assertIn('sandbox_mode = "danger-full-access"', text)
            self.assertIn('approval_policy = "never"', text)
            self.assertIn('web_search = "live"', text)
            self.assertIn("Use only the neutral brief", text) if "adjudicator" not in agent_file.name else None
            self.assertIn("Use latest official documentation for external/API/platform/dependency/tooling claims.", text)
            self.assertIn("Research online extensively when current external facts could affect the answer.", text)
            self.assertIn("Do not invoke Agent Collab", text)

    def test_claude_local_agents_use_max_effort_full_inherited_tools_and_depth_guard(self):
        agent_files = sorted((REPO_ROOT / ".claude" / "agents").glob("agent-collab-*.md"))
        names = {path.stem for path in agent_files}
        self.assertIn("agent-collab-adjudicator", names)
        self.assertIn("agent-collab-security-auditor", names)
        self.assertIn("agent-collab-test-strategist", names)

        for agent_file in agent_files:
            text = agent_file.read_text()
            self.assertIn("model: opus", text)
            self.assertIn("effort: max", text)
            self.assertIn("permissionMode: bypassPermissions", text)
            self.assertIn("disallowedTools: Task", text)
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
        self.assertFalse((REPO_ROOT / "codex-skill" / "agent-collab" / "tools").exists())
