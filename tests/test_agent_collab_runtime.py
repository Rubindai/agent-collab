import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, mock


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = REPO_ROOT / "tools" / "agent-collab" / "agent-collab-peer.py"
SCHEMA = REPO_ROOT / "tools" / "agent-collab" / "peer-report.schema.json"


def load_runtime():
    spec = importlib.util.spec_from_file_location("agent_collab_peer", RUNTIME)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


class RuntimeContractTests(TestCase):
    def test_prompt_embeds_request_json_without_shell_interpolation_and_forbids_edits(self):
        runtime = load_runtime()
        req = request()

        prompt = runtime.build_prompt(req, REPO_ROOT, SCHEMA)

        self.assertIn("STRICT PEER-ONLY CONTRACT", prompt)
        self.assertIn("Do not modify files", prompt)
        self.assertIn("Do not invoke Agent Collab", prompt)
        self.assertIn("Respond with exactly one JSON object", prompt)
        embedded = prompt.split("REQUEST JSON:\n", 1)[1].split("\n\nRESPONSE SCHEMA", 1)[0]
        self.assertEqual(json.loads(embedded), req)

    def test_claude_command_uses_inline_schema_safe_mode_and_no_max_turns(self):
        runtime = load_runtime()
        env = {"CLAUDE_MODEL": "opus", "CLAUDE_EFFORT": "max", "AGENT_COLLAB_SAFE_MODE": "1"}

        command = runtime.build_peer_command(
            request(peer="claude"),
            prompt="prompt text",
            repo_root=REPO_ROOT,
            schema_path=SCHEMA,
            output_path=Path("/tmp/out.json"),
            env=env,
        )

        self.assertEqual(command.stdin, None)
        self.assertEqual(command.args[:3], ["claude", "-p", "prompt text"])
        self.assertIn("--json-schema", command.args)
        self.assertEqual(command.args[command.args.index("--permission-mode") + 1], "plan")
        self.assertIn("--no-session-persistence", command.args)
        self.assertNotIn("--max-turns", command.args)

    def test_claude_peer_defaults_to_highest_reasoning_effort(self):
        runtime = load_runtime()

        command = runtime.build_peer_command(
            request(peer="claude"),
            prompt="prompt text",
            repo_root=REPO_ROOT,
            schema_path=SCHEMA,
            output_path=Path("/tmp/out.json"),
            env={},
        )

        self.assertEqual(command.args[command.args.index("--model") + 1], "opus")
        self.assertEqual(command.args[command.args.index("--effort") + 1], "max")

    def test_codex_command_uses_schema_output_file_and_stdin_prompt(self):
        runtime = load_runtime()
        env = {
            "CODEX_MODEL": "gpt-5.5",
            "CODEX_EFFORT": "xhigh",
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
        self.assertIn("--cd", command.args)
        self.assertIn(str(REPO_ROOT), command.args)
        self.assertIn("--output-schema", command.args)
        self.assertIn(str(SCHEMA), command.args)
        self.assertIn("--output-last-message", command.args)
        self.assertIn("/tmp/out.json", command.args)
        self.assertIn("--sandbox", command.args)
        self.assertEqual(command.args[command.args.index("--sandbox") + 1], "danger-full-access")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", command.args)
        self.assertNotIn("--ask-for-approval", command.args)
        self.assertEqual(command.args[-1], "-")

    def test_codex_command_uses_official_approval_flag_when_supported(self):
        runtime = load_runtime()

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
        runtime = load_runtime()

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

    def test_codex_peer_defaults_to_highest_reasoning_effort(self):
        runtime = load_runtime()

        command = runtime.build_peer_command(
            request(peer="codex", host="claude", origin="claude"),
            prompt="prompt text",
            repo_root=REPO_ROOT,
            schema_path=SCHEMA,
            output_path=Path("/tmp/out.json"),
            env={},
        )

        self.assertIn('model_reasoning_effort="xhigh"', command.args)

    def test_nested_invocation_returns_structured_failure(self):
        runtime = load_runtime()

        result = runtime.run_request(request(), REPO_ROOT, env={"AGENT_COLLAB_DEPTH": "1"})

        self.assertEqual(result["status"], "peer_failed")
        self.assertEqual(result["error"]["kind"], "nested_invocation_refused")
        self.assertEqual(result["run_id"], "agent-collab-test")

    def test_missing_cli_returns_structured_failure(self):
        runtime = load_runtime()

        with mock.patch.object(runtime.shutil, "which", return_value=None):
            result = runtime.invoke_peer(request(), REPO_ROOT, env={})

        self.assertEqual(result["status"], "peer_failed")
        self.assertEqual(result["error"]["kind"], "missing_cli")
        self.assertIn("claude", result["error"]["message"])

    def test_invalid_peer_json_returns_structured_failure(self):
        runtime = load_runtime()
        completed = subprocess.CompletedProcess(["claude"], 0, stdout="not json", stderr="")

        with mock.patch.object(runtime.shutil, "which", return_value="/usr/bin/claude"):
            with mock.patch.object(runtime.subprocess, "run", return_value=completed):
                result = runtime.invoke_peer(request(), REPO_ROOT, env={})

        self.assertEqual(result["status"], "peer_failed")
        self.assertEqual(result["error"]["kind"], "invalid_json")

    def test_peer_report_schema_accepts_required_shape_and_rejects_missing_fields(self):
        runtime = load_runtime()
        report = {
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
            "claims": [],
            "limitations": [],
            "next_actions": [],
        }

        runtime.validate_peer_report(report)
        broken = dict(report)
        broken.pop("summary")
        with self.assertRaises(runtime.PeerReportValidationError):
            runtime.validate_peer_report(broken)

    def test_git_snapshot_script_outputs_parseable_sections(self):
        script = REPO_ROOT / "tools" / "agent-collab" / "git-snapshot.sh"

        completed = subprocess.run(
            ["bash", str(script)],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("agent_collab_git_snapshot_v1", completed.stdout)
        self.assertIn("-- status_porcelain_v1", completed.stdout)
        self.assertIn("-- diff_name_status", completed.stdout)


class SkillMetadataTests(TestCase):
    def test_codex_and_claude_skill_metadata_match_contract(self):
        codex_skill = REPO_ROOT / ".agents" / "skills" / "agent-collab" / "SKILL.md"
        codex_openai = REPO_ROOT / ".agents" / "skills" / "agent-collab" / "agents" / "openai.yaml"
        claude_skill = REPO_ROOT / ".claude" / "skills" / "agent-collab" / "SKILL.md"

        codex_text = codex_skill.read_text()
        openai_text = codex_openai.read_text()
        claude_text = claude_skill.read_text()

        self.assertIn("name: agent-collab", codex_text)
        self.assertIn("allow_implicit_invocation: true", openai_text)
        self.assertIn('default_prompt: "Use $agent-collab', openai_text)
        self.assertIn("when_to_use:", claude_text)
        self.assertIn("allowed-tools: Bash Read Grep Glob WebSearch WebFetch", claude_text)
        self.assertNotIn("disable-model-invocation: true", claude_text)

    def test_claude_slash_command_is_backed_by_skill_directory(self):
        skill = REPO_ROOT / ".claude" / "skills" / "agent-collab" / "SKILL.md"
        self.assertTrue(skill.exists())
        self.assertEqual(skill.parent.name, "agent-collab")

    def test_codex_local_agents_use_highest_reasoning_effort(self):
        for agent_file in (REPO_ROOT / ".codex" / "agents").glob("agent-collab-*.toml"):
            self.assertIn('model_reasoning_effort = "xhigh"', agent_file.read_text())

    def test_claude_local_agents_use_highest_reasoning_effort(self):
        for agent_file in (REPO_ROOT / ".claude" / "agents").glob("agent-collab-*.md"):
            text = agent_file.read_text()
            self.assertIn("model: opus", text)
            self.assertIn("effort: max", text)
