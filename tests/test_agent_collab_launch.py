import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

from agent_collab_test_support import (
    CLAUDE_EFFORT_KEYS,
    PEER_SCHEMA,
    REPO_ROOT,
    availability_attestation,
    claude_result_runner,
    load_availability,
    load_host,
    load_peer,
    process_state,
    record_claude_stop,
    request,
    setUpModule,
    tearDownModule,
    valid_report,
)


class PeerTests(TestCase):
    def test_request_is_strict_complete_v3(self):
        peer = load_peer()
        peer.validate_request(request())
        for change in (
            {"schema_version": "1.0"},
            {"extra": True},
            {"run_id": "../escape"},
            {"peer_timeout_seconds": 59},
            {"peer_effort": "none"},
        ):
            with self.subTest(change=change), self.assertRaises(peer.RequestValidationError):
                peer.validate_request(request(**change))
        incomplete = request()
        del incomplete["peer_model"]
        with self.assertRaises(peer.RequestValidationError):
            peer.validate_request(incomplete)
        for owned_key in (
            "model",
            "model_reasoning_effort",
            "model_provider",
            "model_providers.custom.base_url",
            "profile",
            "profiles.custom.model",
            "web_search",
            "sandbox_mode",
            "approval_policy",
            "agents",
            "agents.enabled",
            "agents.max_concurrent_threads_per_session",
            "features",
            "features.multi_agent_v2.enabled",
            "approvals_reviewer",
            "auto_review.policy",
            "openai_base_url",
            "chatgpt_base_url",
            "model_catalog_json",
            "oss_provider",
            "service_tier",
            "sandbox_workspace_write.network_access",
            "permissions.default.network",
            "default_permissions",
            "tools.web_search.context_size",
            "tools",
            "memories.use_memories",
        ):
            with self.subTest(owned_key=owned_key), self.assertRaisesRegex(
                peer.RequestValidationError, "Agent Collab-owned key"
            ):
                peer.validate_request(request(codex_config=[f"{owned_key}=true"]))
        for invalid_config in ('"model"="other"', "agents . enabled=false", "agents.\"enabled\"=false"):
            with self.subTest(invalid_config=invalid_config), self.assertRaisesRegex(
                peer.RequestValidationError, "canonical bare dotted syntax"
            ):
                peer.validate_request(request(codex_config=[invalid_config]))
        with self.assertRaisesRegex(peer.RequestValidationError, "sentinel 'default'"):
            peer.validate_request(request(claude_tools="default,Bash"))
        with self.assertRaisesRegex(peer.RequestValidationError, "origin must match host"):
            peer.validate_request(request(origin="claude"))

    def test_prompt_is_single_compact_escaped_xml_contract(self):
        peer = load_peer()
        prompt = peer.build_prompt(request(target="A < B && C > D"), REPO_ROOT, PEER_SCHEMA)
        for section in ("role", "outcome", "evidence", "boundaries", "task", "stop"):
            self.assertEqual(prompt.count("<" + section + ">"), 1)
        self.assertIn("&lt;x/&gt;", prompt)
        self.assertIn("<target>A &lt; B &amp;&amp; C &gt; D</target>", prompt)
        self.assertNotIn("<request_json>", prompt)
        self.assertNotIn("<response_schema>", prompt)
        self.assertNotIn('"schema_version"', prompt)

    def test_claude_command_exact_defaults_and_full_capability(self):
        peer = load_peer()
        command = peer.build_peer_command(request(), "prompt", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}, "git")
        args = command.args
        self.assertEqual(args[:2], ["claude", "-p"])
        self.assertEqual(args[args.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(args[args.index("--effort") + 1], "xhigh")
        self.assertEqual(args[args.index("--permission-mode") + 1], "bypassPermissions")
        self.assertEqual(args[args.index("--tools") + 1], "default")
        self.assertNotIn("--max-turns", args)
        self.assertIn("--no-session-persistence", args)
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertNotIn("--allowedTools", args)
        self.assertEqual(command.stdin, "prompt")

        opted_in = peer.build_peer_command(
            request(claude_max_turns=50),
            "prompt",
            REPO_ROOT,
            PEER_SCHEMA,
            Path("/tmp/out"),
            {},
            "git",
        ).args
        self.assertEqual(opted_in[opted_in.index("--max-turns") + 1], "50")

    def test_claude_fable_safe_offline_and_custom_tools(self):
        peer = load_peer()
        fable = peer.build_peer_command(
            request(peer_model="claude-fable-5"), "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}, "git"
        ).args
        self.assertEqual(fable[fable.index("--model") + 1], "claude-fable-5")
        safe = peer.build_peer_command(
            request(safe_mode=True, online_research=False, claude_tools="Read,Grep,WebSearch"),
            "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}, "git"
        ).args
        self.assertEqual(safe[safe.index("--permission-mode") + 1], "plan")
        self.assertEqual(safe[safe.index("--tools") + 1], "Read,Grep")
        self.assertEqual(safe[safe.index("--disallowedTools") + 1], "Workflow,WebSearch,WebFetch,Agent(fork)")

    def test_codex_command_has_current_global_flag_shape(self):
        peer = load_peer()
        req = request(peer="codex", host="claude", origin="claude")
        args = peer.build_peer_command(req, "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}, "git").args
        self.assertEqual(args[:4], ["codex", "exec", "--strict-config", "--ephemeral"])
        self.assertNotIn("--ask-for-approval", args)
        self.assertNotIn("features.memory_tool=false", args)
        for pinned in ('approval_policy="never"', 'approvals_reviewer="user"',
                       "features.memories=false", "memories.use_memories=false",
                       "memories.generate_memories=false", "features.agent_message_board=false"):
            self.assertEqual(args.count(pinned), 1)
            self.assertEqual(args[args.index(pinned) - 1], "-c")
        self.assertEqual(args[args.index("--model") + 1], "gpt-6-astra")
        self.assertEqual(args[args.index("--sandbox") + 1], "danger-full-access")
        self.assertIn('model_reasoning_effort="xhigh"', args)
        self.assertIn('web_search="live"', args)
        self.assertIn("--json", args)
        self.assertEqual(args[-1], "-")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertNotIn("--skip-git-repo-check", args)

    def test_codex_non_git_workspace_skips_only_git_check(self):
        peer = load_peer()
        req = request(peer="codex", host="claude", origin="claude")
        with tempfile.TemporaryDirectory() as tmp:
            args = peer.build_peer_command(
                req, "p", Path(tmp), PEER_SCHEMA, Path(tmp) / "out", {}, "filesystem"
            ).args
        self.assertEqual(args.count("--skip-git-repo-check"), 1)
        self.assertIn("--strict-config", args)
        self.assertEqual(args[args.index("--sandbox") + 1], "danger-full-access")
        with self.assertRaisesRegex(ValueError, "unsupported workspace snapshot mode"):
            peer.build_peer_command(req, "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}, "unknown")

    def test_codex_helper_defaults_follow_exact_per_run_selection(self):
        peer = load_peer()
        for model, effort in (("gpt-6-astra", "xhigh"), ("gpt-5.6-sol", "max")):
            with self.subTest(model=model, effort=effort):
                req = request(peer="codex", host="claude", origin="claude",
                              peer_model=model, peer_effort=effort, codex_config=["foo=true"])
                args = peer.build_peer_command(req, "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}, "git").args
                for key, value in (("agents.default_subagent_model", model),
                                   ("agents.default_subagent_reasoning_effort", effort)):
                    setting = f"{key}={json.dumps(value)}"
                    self.assertGreater(args.index(setting), args.index("foo=true"))
                    self.assertEqual(args.count(setting), 1)
                    with self.assertRaises(peer.RequestValidationError):
                        peer.validate_request({**req, "codex_config": [f'{key}="wrong"']})

    def test_codex_safe_offline_and_invariant_order(self):
        peer = load_peer()
        req = request(
            peer="codex", host="claude", origin="claude", safe_mode=True, online_research=False,
            codex_config=["foo=true"],
        )
        args = peer.build_peer_command(req, "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}, "git").args
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertIn('web_search="disabled"', args)
        self.assertGreater(args.index('model_reasoning_effort="xhigh"'), args.index("foo=true"))

    def test_environment_cannot_override_resolved_request(self):
        peer = load_peer()
        env = {
            "CLAUDE_AGENT_COLLAB_MODEL": "wrong",
            "CLAUDE_AGENT_COLLAB_EFFORT": "low",
            "AGENT_COLLAB_SAFE_MODE": "1",
            "AGENT_COLLAB_WEB_RESEARCH": "disabled",
        }
        args = peer.build_peer_command(request(), "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), env, "git").args
        self.assertEqual(args[args.index("--model") + 1], "claude-opus-5-5")
        self.assertEqual(args[args.index("--permission-mode") + 1], "bypassPermissions")

    def test_current_effort_catalog_and_exact_claude_launch_controls(self):
        peer = load_peer()
        availability = load_availability()
        self.assertFalse(hasattr(peer, "CODEX_EFFORT_CHOICES"))
        self.assertEqual(
            availability.CLAUDE_EFFORT_CHOICES,
            {"low", "medium", "high", "xhigh", "max", "ultracode"},
        )
        args = peer.build_peer_command(request(peer_effort="xhigh"), "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}, "git").args
        self.assertEqual(args[args.index("--effort") + 1], "xhigh")
        settings = json.loads(args[args.index("--settings") + 1])
        self.assertEqual(settings["availableModels"], ["claude-opus-5-5"])
        self.assertEqual(settings["fallbackModel"], [])
        self.assertIs(settings["switchModelsOnFlag"], False)
        self.assertIs(settings["disableAllHooks"], False)
        self.assertEqual(settings["crossSessionInbound"], "refuse")
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["hooks"][0]["onFailure"], "block")
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["matcher"], "Agent")
        self.assertEqual(args[-2:], ["--tools", "default"])
        self.assertIn("Agent(fork)", args[args.index("--disallowedTools") + 1].split(","))
        self.assertNotIn("--fallback-model", args)
        peer.validate_request(request(peer_effort="ultracode"))
        with self.assertRaisesRegex(ValueError, "cannot enforce its helper bound"):
            peer.build_peer_command(
                request(peer_effort="ultracode"), "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}, "git"
            )
        self.assertEqual(settings["env"]["CLAUDE_CODE_DISABLE_WORKFLOWS"], "1")
        self.assertEqual(settings["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
        self.assertIn("Workflow", args[args.index("--disallowedTools") + 1].split(","))

        no_agents = peer.build_peer_command(
            request(local_subagents_allowed=False, max_local_subagents=0),
            "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}, "git",
        ).args
        self.assertIn("Agent", no_agents[no_agents.index("--disallowedTools") + 1].split(","))

    def test_request_requires_matching_successful_availability_attestation(self):
        peer = load_peer()
        missing = request()
        del missing["availability_attestation"]
        with self.assertRaises(peer.RequestValidationError):
            peer.validate_request(missing)
        mismatched = request()
        mismatched["availability_attestation"] = availability_attestation(
            "claude", "claude-fable-5", "max"
        )
        with self.assertRaises(peer.RequestValidationError):
            peer.validate_request(mismatched)
        unavailable = request()
        unavailable["availability_attestation"]["status"] = "unknown"
        with self.assertRaises(peer.RequestValidationError):
            peer.validate_request(unavailable)

    def test_claude_helper_environment_overrides_ambient_selection_and_forks(self):
        peer = load_peer()
        cases = (
            ("claude-opus-5-5", "xhigh", "xhigh", False),
            ("claude-fable-5", "max", "max", False),
            ("claude-opus-5-5", "high", "high", True),
        )
        for model, effort, effective_effort, safe_mode in cases:
            with self.subTest(model=model, effort=effort, safe_mode=safe_mode):
                captured = {}
                envelope = {
                    "type": "result",
                    "structured_output": valid_report(),
                    "modelUsage": {model: {}},
                }

                def fake_run(args, **kwargs):
                    captured.update(args=args, env=kwargs["env"], input=kwargs["input"])
                    record_claude_stop(args)
                    return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")

                overrides = {key: "forced" for key in CLAUDE_EFFORT_KEYS}
                overrides.update({
                    "CLAUDE_CODE_SUBAGENT_MODEL": "claude-haiku-wrong",
                    "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "0",
                    "CLAUDE_CODE_FORK_SUBAGENT": "1",
                })
                with (
                    tempfile.TemporaryDirectory() as tmp,
                    mock.patch.object(peer, "git_mutation_snapshot", return_value={"mode": "git", "same": True}),
                    mock.patch.object(peer.SAFETY.shutil, "which", return_value="/usr/bin/claude"),
                    mock.patch.object(peer.SAFETY, "sandbox_preflight") as sandbox_preflight,
                    mock.patch.object(peer, "run_peer_command", side_effect=fake_run),
                ):
                    result = peer.run_request(
                        request(peer_model=model, peer_effort=effort, safe_mode=safe_mode),
                        Path(tmp), env=overrides,
                    )
                self.assertEqual(result["status"], "ok")
                sandbox_preflight.assert_not_called()
                settings = json.loads(captured["args"][captured["args"].index("--settings") + 1])
                for key, value in settings["env"].items():
                    self.assertEqual(captured["env"][key], value)
                self.assertEqual(captured["env"]["CLAUDE_CODE_EFFORT_LEVEL"], effective_effort)
                self.assertEqual(captured["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], model)
                self.assertEqual(captured["env"]["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"], "1")
                self.assertEqual(captured["env"]["CLAUDE_CODE_FORK_SUBAGENT"], "0")
                self.assertEqual(captured["env"]["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"], "1")
                self.assertEqual(captured["args"][captured["args"].index("--effort") + 1], effort)
                self.assertEqual(captured["args"][captured["args"].index("--model") + 1], model)
                self.assertIn("<task>", captured["input"])
                self.assertIn("</task>", captured["input"])

    def test_peer_launch_rejects_a_stale_availability_attestation(self):
        peer = load_peer()
        stale = request()
        stale["availability_attestation"]["checked_at"] = "2026-01-01T00:00:00Z"
        peer.validate_request(stale)
        with self.assertRaisesRegex(peer.RequestValidationError, "not fresh enough"):
            peer.validate_request(stale, require_fresh_attestation=True)

    def test_xml_encoded_peer_target_is_canonicalized_at_invocation(self):
        peer = load_peer()
        req = request(target="A < B && C > D")
        envelope = {
            "type": "result",
            "structured_output": valid_report(target="A &lt; B &amp;&amp; C &gt; D"),
            "modelUsage": {"claude-opus-5-5": {}},
        }
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")
        with (
            tempfile.TemporaryDirectory() as tmp_name,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"mode": "git", "same": True}),
            mock.patch.object(peer.SAFETY.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(peer, "run_peer_command", side_effect=claude_result_runner(completed)),
        ):
            tmp = Path(tmp_name)
            normalization = tmp / "peer-normalization.json"
            result = peer.run_request(
                req,
                tmp,
                raw_output_path=tmp / "peer.raw.json",
                normalization_output_path=normalization,
            )
            metadata = json.loads(normalization.read_text(encoding="utf-8"))
            host = load_host()
            host.ARTIFACTS.validate_peer_normalization(metadata)
            host.CONFIG.STRICT_JSON.write(tmp / 'peer-report.json', result)
            host.ARTIFACTS.normalize_peer_report_from_artifacts(peer, tmp, req)
            rebuilt = json.loads(normalization.read_text(encoding='utf-8'))
            host.ARTIFACTS.validate_peer_normalization(rebuilt)
            self.assertEqual(rebuilt['artifact_source'], 'peer_report')
            self.assertIn('target_xml_entities_canonicalized', rebuilt['warnings'])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["target"], req["target"])
        self.assertIn("target_xml_entities_canonicalized", metadata["warnings"])

    def test_safe_mode_fails_closed_before_snapshot_or_peer_launch(self):
        peer = load_peer()
        unavailable = {
            "status": "sandbox_unavailable",
            "backend": "bwrap",
            "details": "user namespace unavailable",
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer.SAFETY, "sandbox_preflight", return_value=unavailable),
            mock.patch.object(peer, "git_mutation_snapshot") as snapshot,
            mock.patch.object(peer, "run_peer_command") as run,
        ):
            result = peer.run_request(
                request(peer="codex", host="claude", origin="claude", safe_mode=True),
                Path(tmp),
            )
        self.assertEqual(result["error"]["kind"], "sandbox_unavailable")
        self.assertIn("bwrap", result["error"]["details"])
        snapshot.assert_not_called()
        run.assert_not_called()

    def test_codex_requires_output_artifact_and_never_falls_back_to_stdout(self):
        peer = load_peer()
        req = request(peer="codex", host="claude", origin="claude")
        success = SimpleNamespace(returncode=0, stdout=json.dumps(valid_report()), stderr="")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"mode": "git", "same": True}),
            mock.patch.object(peer.SAFETY.shutil, "which", return_value="/usr/bin/codex"),
            mock.patch.object(peer, "run_peer_command", return_value=success),
        ):
            result = peer.run_request(req, Path(tmp))
        self.assertEqual(result["error"]["kind"], "missing_output_artifact")

    def test_codex_reads_only_current_output_artifact(self):
        peer = load_peer()
        req = request(peer="codex", host="claude", origin="claude")
        report = valid_report(origin="claude", host="claude", peer="codex")

        telemetry = json.dumps({
            "type": "turn.completed",
            "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 20},
        }) + "\n"

        def fake_run(args, **kwargs):
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text(json.dumps(report))
            return SimpleNamespace(returncode=0, stdout=telemetry, stderr="")

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"mode": "git", "same": True}),
            mock.patch.object(peer.SAFETY.shutil, "which", return_value="/usr/bin/codex"),
            mock.patch.object(peer, "run_peer_command", side_effect=fake_run),
        ):
            raw = Path(tmp) / "run" / "peer-raw.txt"
            raw.parent.mkdir(mode=0o700)
            result = peer.run_request(req, Path(tmp), raw_output_path=raw)
            saved_stdout = raw.with_name("peer-stdout.txt")
            self.assertEqual(saved_stdout.read_text(), telemetry)
            self.assertEqual(stat.S_IMODE(saved_stdout.stat().st_mode), 0o600)
            self.assertEqual(json.loads(raw.read_text()), report)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["peer"], "codex")


    def test_successful_pipe_capture_proves_provider_quiescence_once(self):
        peer = load_peer()
        with mock.patch.object(
            peer, "_cleanup_provider_process", wraps=peer._cleanup_provider_process
        ) as cleanup:
            completed = peer.run_peer_command(
                [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
                input="peer prompt",
                text=True,
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=True,
            )
        self.assertEqual(completed.stdout, "peer prompt")
        self.assertEqual(cleanup.call_count, 1)

    def test_process_commands_require_positive_finite_timeouts_before_launch(self):
        peer = load_peer()
        availability = load_availability()
        for runner in (peer.run_peer_command, availability.run_bounded_command):
            for options in ({}, *({'timeout': value} for value in (None, 0, -1, float('inf'), float('nan')))):
                with (self.subTest(runner=runner.__name__, options=options),
                      mock.patch.object(peer.SAFETY, 'popen_group') as launch,
                      self.assertRaisesRegex(ValueError, 'positive finite timeout')):
                    runner([sys.executable, '-c', 'pass'], text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
                launch.assert_not_called()

    def test_peer_timeout_terminates_provider_process_group(self):
        if os.name != "posix":
            self.skipTest("process-group timeout contract is POSIX-only")
        peer = load_peer()
        with tempfile.TemporaryDirectory() as tmp_name:
            child_pid_path = Path(tmp_name) / "child.pid"
            parent_code = (
                "import pathlib,subprocess,sys,time;"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid),encoding='utf-8');"
                "time.sleep(30)"
            )
            with self.assertRaises(subprocess.TimeoutExpired):
                peer.run_peer_command(
                    [sys.executable, "-c", parent_code, str(child_pid_path)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=0.2,
                    check=False,
                )
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 2
            while process_state(child_pid) not in {None, "Z"} and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertIn(process_state(child_pid), {None, "Z"})

    def test_peer_timeout_is_a_structured_runtime_result(self):
        peer = load_peer()
        with (
            tempfile.TemporaryDirectory() as tmp_name,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"mode": "git", "same": True}),
            mock.patch.object(peer.SAFETY.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(
                peer,
                "run_peer_command",
                side_effect=subprocess.TimeoutExpired(["claude"], 1),
            ),
        ):
            result = peer.run_request(request(), Path(tmp_name))
        self.assertEqual(result["status"], "peer_failed")
        self.assertEqual(result["error"]["kind"], "timeout")

    def test_edit_authorization_controls_mutation_reporting(self):
        peer = load_peer()
        envelope = {
            "type": "result",
            "structured_output": valid_report(),
            "modelUsage": {"claude-opus-5-5": {}},
        }
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")
        for edit_allowed in (False, True):
            with self.subTest(edit_allowed=edit_allowed), tempfile.TemporaryDirectory() as tmp_name:
                root = Path(tmp_name)
                raw = root / "peer.raw.json"
                with (
                    mock.patch.object(
                        peer,
                        "git_mutation_snapshot",
                        side_effect=[{"mode": "git", "digest": "before"}, {"mode": "git", "digest": "after"}],
                    ),
                    mock.patch.object(peer, "mutation_error_details", return_value={"source": "peer_snapshot"}),
                    mock.patch.object(peer.SAFETY.shutil, "which", return_value="/usr/bin/claude"),
                    mock.patch.object(peer, "run_peer_command", side_effect=claude_result_runner(completed)),
                ):
                    result = peer.run_request(
                        request(edit_allowed=edit_allowed),
                        root,
                        raw_output_path=raw,
                    )
                mutation_path = root / "workspace-mutation.json"
                if edit_allowed:
                    self.assertNotIn("Workspace changed while edit_allowed=false", result["limitations"])
                    self.assertFalse(mutation_path.exists())
                else:
                    self.assertIn("Workspace changed while edit_allowed=false", result["limitations"])
                    self.assertTrue(mutation_path.exists())

        host = load_host()
        parsed = host.build_parser().parse_args(
            ["start", "--host", "codex", "--target", "diff", "--brief", "review"]
        )
        self.assertIs(parsed.edit_allowed, False)

    def test_peer_timeout_kills_redirected_sigterm_ignoring_descendant(self):
        if os.name != "posix":
            self.skipTest("process-group timeout contract is POSIX-only")
        peer = load_peer()
        child_pid = None
        with tempfile.TemporaryDirectory() as tmp_name:
            child_pid_path = Path(tmp_name) / "child.pid"
            child_code = (
                "import signal,time;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                "time.sleep(30)"
            )
            parent_code = (
                "import pathlib,subprocess,sys,time;"
                f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid),encoding='utf-8');"
                "time.sleep(30)"
            )
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    peer.run_peer_command(
                        [sys.executable, "-c", parent_code, str(child_pid_path)],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=0.2,
                        check=False,
                    )
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                self.assertIn(process_state(child_pid), {None, "Z"})
            finally:
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    time.sleep(0.1)

    def test_escaped_session_pipe_holder_fails_fast_and_marks_tracker_nonquiescent(self):
        if os.name != "posix":
            self.skipTest("process-group lifecycle contract is POSIX-only")
        peer = load_peer()
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            child_pid_path = tmp / "escaped.pid"
            tracker_path = tmp / "provider-process.json"
            parent_code = (
                "import pathlib,subprocess,sys,time;"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
                "start_new_session=True,stdin=subprocess.DEVNULL);"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid),encoding='utf-8');"
                "time.sleep(30)"
            )
            started = time.monotonic()
            try:
                with self.assertRaises(peer.ProviderCleanupError):
                    peer.run_peer_command(
                        [sys.executable, "-c", parent_code, str(child_pid_path)],
                        text=True,
                        errors="replace",
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=0.3,
                        check=False,
                        provider_process_path=tracker_path,
                        run_id=request()["run_id"],
                    )
                self.assertLess(time.monotonic() - started, 5)
                tracker = json.loads(tracker_path.read_text())
                self.assertEqual(tracker["status"], "cleanup_failed")
                self.assertEqual(tracker["cleanup_outcome"], "capture_channel_not_quiescent")
            finally:
                if child_pid_path.exists():
                    child_pid = int(child_pid_path.read_text())
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    time.sleep(0.1)

    def test_peer_output_capture_is_bounded_during_execution(self):
        if os.name != "posix":
            self.skipTest("bounded provider group contract is POSIX-only")
        peer = load_peer()
        with mock.patch.object(peer, "MAX_PEER_OUTPUT_BYTES", 1024):
            with self.assertRaises(peer.PeerOutputLimitExceeded) as raised:
                peer.run_peer_command(
                    [
                        sys.executable,
                        "-c",
                        "import sys,time;sys.stdout.write('x'*100000);sys.stdout.flush();time.sleep(30)",
                    ],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    check=False,
                )
        self.assertGreater(raised.exception.details["stdout_bytes"], 1024)

    def test_fable_usage_detects_observable_opus_fallback(self):
        peer = load_peer()
        good = json.dumps({"modelUsage": {"claude-fable-5": {}, "claude-haiku-4-5": {}}})
        fallback = json.dumps({"modelUsage": {"claude-fable-5": {}, "claude-opus-4-8": {}}})
        missing = json.dumps({"modelUsage": {"claude-haiku-4-5": {}}})
        self.assertIsNone(peer.claude_selection_mismatch_details(good, "claude-fable-5", None))
        self.assertIn("unexpected model usage", peer.claude_selection_mismatch_details(fallback, "claude-fable-5", None)["reason"])
        self.assertIn("did not report", peer.claude_selection_mismatch_details(missing, "claude-fable-5", None)["reason"])
        self.assertIn("unexpected model usage", peer.claude_selection_mismatch_details(fallback, "claude-opus-4-8", None)["reason"])
        downgrade = json.dumps({"effort": "high", "modelUsage": {"claude-fable-5": {}}})
        self.assertIn(
            "reported effort",
            peer.claude_selection_mismatch_details(downgrade, "claude-fable-5", "max")["reason"],
        )
        ultracode = json.dumps({"effort": "xhigh", "modelUsage": {"claude-opus-5": {}}})
        self.assertIsNone(
            peer.claude_selection_mismatch_details(ultracode, "claude-opus-5", "ultracode")
        )

    def test_claude_model_attestation_rejects_broad_family_aliases(self):
        availability = load_availability()
        self.assertFalse(availability.model_identifier_matches("claude", "claude-fable-5"))
        self.assertFalse(availability.model_identifier_matches("opus", "claude-opus-5"))
        self.assertFalse(
            availability.model_identifier_matches(
                "claude-opus-5", "claude-opus-5-20260701"
            )
        )
        self.assertFalse(
            availability.model_identifier_matches(
                "claude-opus-5", "claude-opus-5-fast"
            )
        )

    def test_codex_jsonl_detects_exec_model_reroute_error_items(self):
        peer = load_peer()

        def reroute(source, target, reason="HighRiskCyberActivity"):
            return json.dumps({"type": "item.completed", "item": {
                "id": "item_3", "type": "error",
                "message": f"model rerouted: {source} -> {target} ({reason})",
            }})

        real_events = "\n".join(json.dumps(event) for event in (
            {"type": "thread.started", "thread_id": "t"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "ok"}},
            {"type": "item.completed", "item": {"id": "item_1", "type": "error", "message": "local warning"}},
            {"type": "turn.completed", "usage": {}},
        ))
        self.assertIsNone(peer.codex_selection_mismatch_details(real_events, "gpt-6-astra", "xhigh"))
        mismatch = peer.codex_selection_mismatch_details(
            real_events + "\n" + reroute("gpt-6-astra", "gpt-6-mini"), "gpt-6-astra", "xhigh"
        )
        self.assertEqual(mismatch["observed_models"], ["gpt-6-mini"])
        self.assertIn("model reroute", mismatch["reason"])
        self.assertIsNone(peer.codex_selection_mismatch_details(
            reroute("codex-auto-review", "gpt-6-astra"), "gpt-6-astra", "xhigh"
        ))
        malformed = json.dumps({"type": "item.completed", "item": {
            "id": "item_4", "type": "error", "message": "model rerouted: unparseable",
        }})
        self.assertEqual(
            peer.codex_selection_mismatch_details(malformed, "gpt-6-astra", "xhigh")["observed_models"], []
        )

    def test_model_entitlement_errors_are_classified_without_fallback(self):
        peer = load_peer()
        message = peer.model_unavailable_message(
            "",
            "The gpt-5.6-sol model is not supported when using Codex with this account.",
            "gpt-5.6-sol",
        )
        self.assertEqual(message, "Requested peer model is unavailable: gpt-5.6-sol")

    def test_codex_nonzero_exit_ignores_model_errors_in_tool_and_assistant_text(self):
        peer = load_peer()
        for item in (
            {"type": "command_execution", "aggregated_output": "model is unavailable; invalid effort"},
            {"type": "agent_message", "text": "model is unavailable; invalid effort"},
            {"type": "mcp_tool_call", "result": {"content": "model is unavailable"}},
        ):
            with self.subTest(item=item["type"]):
                output = json.dumps({"type": "item.completed", "item": item})
                completed = subprocess.CompletedProcess([], 1, output, "unrelated I/O failure")
                result = peer.check_provider_result(request(peer="codex"), completed, None, Path("unused"), None)
                self.assertEqual(result["error"]["kind"], "peer_nonzero_exit")

    def test_codex_provider_error_events_preserve_selection_diagnostics(self):
        peer = load_peer()
        for message in ("model is unavailable", "invalid effort"):
            for event in (
                {"type": "error", "message": message},
                {"type": "turn.failed", "error": {"message": message}},
                {"type": "item.completed", "item": {"type": "error", "message": message}},
            ):
                with self.subTest(message=message, event=event["type"]):
                    completed = subprocess.CompletedProcess([], 1, json.dumps(event), "")
                    result = peer.check_provider_result(request(peer="codex"), completed, None, Path("unused"), None)
                    self.assertEqual(result["error"]["kind"], "model_unavailable")
                    self.assertIn("effort" if message == "invalid effort" else "model", result["summary"])
        for output in ("model is unavailable", '{"type":"error","message":42}', '["model is unavailable"]'):
            self.assertIsNone(peer.model_unavailable_message(output, "", "gpt-6-astra", "xhigh"))

    def test_report_v3_semantics_and_current_provider_schema(self):
        peer = load_peer()
        peer.validate_peer_report(valid_report())
        with self.assertRaises(peer.PeerReportValidationError):
            peer.validate_peer_report(valid_report(schema_version="1.0"))
        with self.assertRaises(peer.PeerReportValidationError):
            peer.validate_peer_report(valid_report(error={"kind": "x", "message": "x"}))
        failed = valid_report(
            status="peer_failed",
            verdict="blocked",
            error={"kind": "x", "message": "x", "details": None},
        )
        peer.validate_peer_report(failed)
        peer.validate_peer_report_matches_request(valid_report(), request())
        with self.assertRaises(peer.PeerReportValidationError):
            peer.validate_peer_report_matches_request(valid_report(run_id="invented"), request())
        encoded_request = request(target="A < B && C > D")
        canonical, changed = peer.canonicalize_peer_report_target(
            valid_report(target="A &lt; B &amp;&amp; C &gt; D"), encoded_request
        )
        self.assertIs(changed, True)
        self.assertEqual(canonical["target"], encoded_request["target"])
        peer.validate_peer_report_matches_request(canonical, encoded_request)
        noncanonical, changed = peer.canonicalize_peer_report_target(
            valid_report(target="A &#60; B &amp;&amp; C &gt; D"), encoded_request
        )
        self.assertIs(changed, False)
        with self.assertRaises(peer.PeerReportValidationError):
            peer.validate_peer_report_matches_request(noncanonical, encoded_request)
        schema = json.loads(PEER_SCHEMA.read_text(encoding="utf-8"))
        self.assertNotIn("$schema", schema)
        self.assertNotIn("$id", schema)
        self.assertNotIn("title", schema)
        self.assertEqual(schema["properties"]["schema_version"]["const"], "3.0")

        unsupported = {"allOf", "not", "dependentRequired", "dependentSchemas", "if", "then", "else"}

        def schema_keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key
                    yield from schema_keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from schema_keys(child)

        self.assertFalse(unsupported.intersection(schema_keys(schema)))

    def test_provider_and_internal_output_shapes_are_exact(self):
        peer = load_peer()
        report = valid_report()
        for payload, source in (({"structured_output": report}, "structured_output"), (report, "direct_json")):
            normalized = peer.normalize_json_payload(json.dumps(payload), source)
            self.assertEqual(normalized.report, report)
            self.assertEqual(normalized.metadata["source"], source)
            self.assertEqual(normalized.metadata["schema_version"], "3.0")
        with self.assertRaises(peer.PeerOutputContractError):
            peer.normalize_json_payload(json.dumps(report), "structured_output")
        with self.assertRaises(peer.PeerOutputContractError):
            peer.normalize_json_payload(json.dumps({"structured_output": report}), "direct_json")
        for legacy in ({"result": report}, {"result": json.dumps(report)}):
            with self.assertRaises(peer.PeerOutputContractError):
                peer.normalize_json_payload(json.dumps(legacy), "structured_output")
        with self.assertRaises(ValueError):
            peer.normalize_json_payload("prefix " + json.dumps(report), "direct_json")

    def test_cross_provider_output_shapes_are_rejected_at_invocation(self):
        peer = load_peer()
        bare_claude = {
            **valid_report(),
            "modelUsage": {"claude-opus-5-5": {}},
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"mode": "git", "same": True}),
            mock.patch.object(peer.SAFETY.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(
                peer,
                "run_peer_command",
                side_effect=claude_result_runner(
                    SimpleNamespace(returncode=0, stdout=json.dumps(bare_claude), stderr="")
                ),
            ),
        ):
            claude_result = peer.run_request(request(), Path(tmp))
        self.assertEqual(claude_result["error"]["kind"], "noncanonical_output")

        codex_request = request(peer="codex", host="claude", origin="claude")
        codex_report = valid_report(origin="claude", host="claude", peer="codex")

        def fake_codex_run(args, **kwargs):
            self.assertIn("--skip-git-repo-check", args)
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text(json.dumps({"structured_output": codex_report}))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"mode": "filesystem", "same": True}),
            mock.patch.object(peer.SAFETY.shutil, "which", return_value="/usr/bin/codex"),
            mock.patch.object(peer, "run_peer_command", side_effect=fake_codex_run),
        ):
            codex_result = peer.run_request(codex_request, Path(tmp))
        self.assertEqual(codex_result["error"]["kind"], "noncanonical_output")

    def test_nested_and_missing_cli_are_structured_failures(self):
        peer = load_peer()
        nested = peer.run_request(request(), REPO_ROOT, env={"AGENT_COLLAB_PEER_ONLY": "true"})
        self.assertEqual(nested["error"]["kind"], "nested_invocation_refused")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"mode": "git", "same": True}),
            mock.patch.object(peer.SAFETY.shutil, "which", return_value=None),
        ):
            missing = peer.run_request(request(), Path(tmp), env={"PATH": ""})
        self.assertEqual(missing["error"]["kind"], "missing_cli")

    def test_claude_api_error_preserves_model_usage(self):
        peer = load_peer()
        envelope = {"type": "result", "is_error": True, "result": "unavailable", "modelUsage": {"claude-opus-5-5": {}}}
        completed = SimpleNamespace(returncode=1, stdout=json.dumps(envelope), stderr="api error")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"mode": "git", "same": True}),
            mock.patch.object(peer.SAFETY.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(peer, "run_peer_command", side_effect=claude_result_runner(completed)),
        ):
            result = peer.run_request(request(), Path(tmp))
        self.assertEqual(result["error"]["kind"], "peer_api_error")
        details = json.loads(result["error"]["details"])
        self.assertEqual(details["modelUsage"], {"claude-opus-5-5": {}})

    def test_claude_turn_limit_is_distinct_and_reports_only_an_explicit_cap(self):
        peer = load_peer()
        envelope = {
            "type": "result",
            "subtype": "error_max_turns",
            "is_error": True,
            "result": "Reached max turns",
            "num_turns": 50,
        }
        kind, message, details = peer.claude_error_details(
            json.dumps(envelope), "", request()
        )
        self.assertEqual(kind, "turn_limit_reached")
        self.assertIn("provider turn limit", message)
        self.assertIsNone(details["configured_turn_limit"])

        kind, message, details = peer.claude_error_details(
            json.dumps(envelope), "", request(claude_max_turns=50)
        )
        self.assertEqual(kind, "turn_limit_reached")
        self.assertIn("explicit 50-turn limit", message)
        self.assertEqual(details["configured_turn_limit"], 50)

        completed = SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"mode": "git", "same": True}),
            mock.patch.object(peer.SAFETY.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(peer, "run_peer_command", side_effect=claude_result_runner(completed)),
        ):
            result = peer.run_request(request(), Path(tmp))
        self.assertEqual(result["error"]["kind"], "turn_limit_reached")

