from concurrent.futures import ThreadPoolExecutor
import filecmp
import hashlib
import importlib.util
import io
import json
import os
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "tools" / "agent-collab"
CODEX_PLUGIN_ROOT = REPO_ROOT / "plugins" / "agent-collab"
CODEX_SKILL_ROOT = CODEX_PLUGIN_ROOT / "skills" / "agent-collab"
CLAUDE_PLUGIN_ROOT = CODEX_PLUGIN_ROOT
CLAUDE_SKILL_ROOT = CLAUDE_PLUGIN_ROOT / "skills" / "agent-collab"
PEER_RUNTIME = RUNTIME_ROOT / "scripts" / "peer.py"
HOST_RUNTIME = RUNTIME_ROOT / "scripts" / "host.py"
STATE_RUNTIME = RUNTIME_ROOT / "scripts" / "state.py"
SNAPSHOT_RUNTIME = RUNTIME_ROOT / "scripts" / "snapshot.py"
AVAILABILITY_RUNTIME = RUNTIME_ROOT / "scripts" / "availability.py"
PEER_SCHEMA = RUNTIME_ROOT / "schemas" / "peer-report.schema.json"
CANONICAL_MODES = ["debug", "design", "plan", "research", "review"]
TEST_ENV_PREFIXES = ("AGENT_COLLAB_", "CLAUDE_AGENT_COLLAB_", "CODEX_AGENT_COLLAB_")
_SAVED_TEST_ENV = {}


def setUpModule():
    _SAVED_TEST_ENV.clear()
    for key in list(os.environ):
        if key.startswith(TEST_ENV_PREFIXES):
            _SAVED_TEST_ENV[key] = os.environ.pop(key)


def tearDownModule():
    for key in list(os.environ):
        if key.startswith(TEST_ENV_PREFIXES):
            os.environ.pop(key)
    os.environ.update(_SAVED_TEST_ENV)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_peer():
    return load_module(PEER_RUNTIME, "agent_collab_peer_test")


def load_host():
    return load_module(HOST_RUNTIME, "agent_collab_host_test")


def load_state():
    return load_module(STATE_RUNTIME, "agent_collab_state_test")


def load_snapshot():
    return load_module(SNAPSHOT_RUNTIME, "agent_collab_snapshot_test")


def load_availability():
    return load_module(AVAILABILITY_RUNTIME, "agent_collab_availability_test")


def availability_attestation(peer, model, effort):
    return {
        "schema_version": "2.0",
        "status": "available",
        "peer": peer,
        "requested_model": model,
        "requested_effort": effort,
        "supported_efforts": [effort],
        "source": "claude_cli_probe" if peer == "claude" else "codex_debug_models",
        "checked_at": "2026-07-19T00:00:00Z",
        "cli_version": "2.1.214" if peer == "claude" else "0.144.5",
        "effort_evidence": "stop_hook_observed" if peer == "claude" else "catalog_advertised",
        "evidence_sha256": "0" * 64,
        "details": "test availability attestation",
    }


def request(**overrides):
    peer = overrides.get("peer", "claude")
    host = overrides.get("host", "codex" if peer == "claude" else "claude")
    model = overrides.get("peer_model", "claude-opus-4-8" if peer == "claude" else "gpt-5.6-sol")
    effort = overrides.get("peer_effort", "max")
    data = {
        "schema_version": "2.0",
        "origin": host,
        "host": host,
        "peer": peer,
        "mode": "review",
        "target": "current diff",
        "brief": "Review XML <x/>, a variable, and Unicode.",
        "edit_allowed": False,
        "run_id": "agent-collab-test",
        "profile": "ultra",
        "local_subagents_allowed": True,
        "max_local_subagents": 8,
        "online_research": True,
        "safe_mode": False,
        "peer_model": model,
        "peer_effort": effort,
        "availability_attestation": availability_attestation(peer, model, effort),
        "peer_timeout_seconds": 2700,
        "codex_config": [],
        "claude_tools": "default",
        "claude_max_turns": 50,
    }
    data.update(overrides)
    return data


def valid_report(**overrides):
    req = request()
    data = {
        "schema_version": "2.0",
        "run_id": req["run_id"],
        "origin": req["origin"],
        "host": req["host"],
        "peer": req["peer"],
        "mode": req["mode"],
        "target": req["target"],
        "status": "ok",
        "verdict": "pass_with_concerns",
        "summary": "No blocking issues.",
        "findings": [],
        "claims": [{"claim": "Example", "status": "confirmed", "evidence": "test"}],
        "limitations": [],
        "next_actions": [],
        "error": None,
    }
    data.update(overrides)
    return data


def complete_settings(host, **overrides):
    data = {key: list(value) if isinstance(value, list) else value for key, value in host.SETTING_DEFAULTS.items()}
    data.update(overrides)
    return data


def valid_host_first_pass(run_id="agent-collab-test"):
    return {
        "schema_version": "2.0",
        "run_id": run_id,
        "summary": "Independent host pass.",
        "claims": [{"claim": "Host claim", "status": "confirmed", "evidence": "host evidence"}],
    }


def valid_host_synthesis(run_id="agent-collab-test"):
    return {
        "schema_version": "2.0",
        "run_id": run_id,
        "summary": "Host synthesis is ready for the final answer.",
        "verdict": "pass_with_concerns",
        "claims": [{"claim": "Synthesized claim", "status": "confirmed", "evidence": "verified evidence"}],
        "unresolved_risks": [],
        "workspace_mutation_sha256": hashlib.sha256(b"null").hexdigest(),
        "final_answer_ready": True,
    }


def valid_process_identity(pid=43210, pgid=43210):
    return {
        "kind": "linux_proc",
        "pid": pid,
        "pgid": pgid,
        "boot_id": "test-boot-id",
        "start_time": "12345",
    }


def valid_peer_process(run_dir, repo_root, guard_path, **overrides):
    req = request()
    data = {
        "schema_version": "2.0",
        "pid": 43210,
        "pgid": 43210,
        "process_identity": valid_process_identity(),
        "run_id": req["run_id"],
        "run_dir": str(run_dir),
        "repo_root": str(repo_root),
        "host": req["host"],
        "peer": req["peer"],
        "profile": req["profile"],
        "started_at": "2026-07-18T00:00:00Z",
        "started_at_epoch": 1784332800.0,
        "peer_timeout_seconds": 2700,
        "peer_cli_version": "2.1.214",
        "workspace_guard": str(guard_path),
        "settings": {"local": "local.json", "global": "global.json"},
        "peer_report": str(run_dir / "peer-report.json"),
        "peer_raw": str(run_dir / "peer.raw.json"),
        "peer_normalization": str(run_dir / "peer-normalization.json"),
        "provider_process": str(run_dir / "provider-process.json"),
        "host_first_pass": str(run_dir / "host-first-pass.json"),
    }
    data.update(overrides)
    return data


def valid_provider_process(run_id="agent-collab-test", **overrides):
    data = {
        "schema_version": "2.0",
        "run_id": run_id,
        "pid": 54321,
        "pgid": 54321,
        "process_identity": valid_process_identity(54321, 54321),
        "status": "quiescent",
        "cleanup_outcome": "terminated",
        "completed_at": "2026-07-18T00:45:00Z",
    }
    data.update(overrides)
    return data


def valid_state_job(job_id="agent-collab-test", **overrides):
    data = {
        "created_at": "2026-07-18T00:00:00Z",
        "updated_at": "2026-07-18T00:00:00Z",
        "id": job_id,
        "run_dir": f"/tmp/runs/{job_id}",
        "repo_root": "/tmp/repo",
        "host": "codex",
        "peer": "claude",
        "mode": "review",
        "target": "current diff",
        "profile": "ultra",
        "status": "completed",
        "phase": "done",
    }
    data.update(overrides)
    return data


def valid_state_job_patch(job_id="agent-collab-test", **overrides):
    data = valid_state_job(job_id, **overrides)
    del data["created_at"]
    del data["updated_at"]
    return data


class AvailabilityV2Tests(TestCase):
    @staticmethod
    def codex_catalog():
        return json.dumps({
            "models": [
                {
                    "slug": "gpt-test",
                    "supported_reasoning_levels": [
                        {"effort": "minimal", "description": "Minimal"},
                        {"effort": "high", "description": "High"},
                    ],
                }
            ]
        })

    @staticmethod
    def claude_stop(effort):
        return {
            "session_id": "00000000-0000-4000-8000-000000000000",
            "transcript_path": "/tmp/transcript.jsonl",
            "cwd": str(REPO_ROOT),
            "permission_mode": "plan",
            "hook_event_name": "Stop",
            "effort": {"level": effort},
        }

    def claude_runner(
        self, model, effort, *, usage_model=None, captured=None, returncode=0, stderr=""
    ):
        def fake_run(*args, **kwargs):
            if captured is not None:
                captured["command"] = args[0]
                captured["kwargs"] = kwargs
            effort_path = Path(kwargs["env"]["AGENT_COLLAB_EFFORT_CAPTURE"])
            effort_path.write_text(json.dumps(self.claude_stop(effort)), encoding="utf-8")
            envelope = {
                "type": "result",
                "is_error": returncode != 0,
                "modelUsage": {usage_model or model: {}},
            }
            return SimpleNamespace(returncode=returncode, stdout=json.dumps(envelope), stderr=stderr)

        return fake_run

    def test_codex_live_catalog_preserves_model_specific_efforts(self):
        availability = load_availability()
        models = availability.parse_codex_model_catalog(self.codex_catalog())
        self.assertEqual(models, [{"model": "gpt-test", "supported_efforts": ["minimal", "high"]}])

        def fake_run(*args, **kwargs):
            self.assertEqual(args[0], ["codex", "debug", "models"])
            self.assertNotIn("--bundled", args[0])
            self.assertNotIn("input", kwargs)
            return SimpleNamespace(returncode=0, stdout=self.codex_catalog(), stderr="")

        available = availability.check(
            "codex", "gpt-test", "minimal", "0.144.5", REPO_ROOT, 5, runner=fake_run, env={}
        )
        self.assertEqual(available["status"], "available")
        self.assertEqual(available["requested_model"], "gpt-test")
        self.assertEqual(available["requested_effort"], "minimal")
        self.assertEqual(available["supported_efforts"], ["minimal", "high"])
        availability.validate_result(available, require_available=True)

        unavailable = availability.check(
            "codex", "gpt-test", "max", "0.144.5", REPO_ROOT, 5, runner=fake_run, env={}
        )
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["requested_effort"], "max")

    def test_codex_missing_model_is_unavailable_and_malformed_catalog_is_unknown(self):
        availability = load_availability()

        def complete(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout=self.codex_catalog(), stderr="")

        result = availability.check(
            "codex", "missing-model", "high", "0.144.5", REPO_ROOT, 5,
            runner=complete, env={},
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["requested_model"], "missing-model")
        self.assertNotIn("observed_model", result)

        malformed = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="not-json", stderr="")
        result = availability.check(
            "codex", "gpt-test", "high", "0.144.5", REPO_ROOT, 5,
            runner=malformed, env={},
        )
        self.assertEqual(result["status"], "unknown")

    def test_claude_probe_is_exact_bounded_and_detects_substitution_or_downgrade(self):
        availability = load_availability()
        captured = {}

        env = {key: "forced" for key in availability.CLAUDE_EFFORT_ENV_OVERRIDES}
        result = availability.check(
            "claude", "claude-fable-5", "max", "2.1.214", REPO_ROOT, 7,
            runner=self.claude_runner("claude-fable-5", "max", captured=captured), env=env,
        )
        self.assertEqual(result["status"], "available")
        self.assertIn("-p", captured["command"])
        self.assertNotIn("--init-only", captured["command"])
        self.assertEqual(captured["command"][captured["command"].index("--model") + 1], "claude-fable-5")
        self.assertEqual(captured["command"][captured["command"].index("--effort") + 1], "max")
        self.assertEqual(captured["command"][captured["command"].index("--setting-sources") + 1], "")
        self.assertIn("--strict-mcp-config", captured["command"])
        self.assertEqual(
            captured["command"][captured["command"].index("--disallowedTools") + 1],
            "mcp__*",
        )
        self.assertEqual(captured["command"][captured["command"].index("--max-turns") + 1], "1")
        self.assertEqual(captured["command"][-2:], ["--tools", ""])
        self.assertEqual(captured["kwargs"]["input"], "Reply with exactly AVAILABLE. Do not use tools.")
        self.assertNotEqual(Path(captured["kwargs"]["cwd"]).resolve(), REPO_ROOT.resolve())
        probe_settings = json.loads(captured["command"][captured["command"].index("--settings") + 1])
        self.assertEqual(probe_settings["fallbackModel"], [])
        self.assertIs(probe_settings["disableAllHooks"], False)
        self.assertEqual(set(probe_settings["hooks"]), {"Stop"})
        self.assertEqual(captured["kwargs"]["timeout"], 7)
        for key in availability.CLAUDE_EFFORT_ENV_OVERRIDES:
            self.assertNotIn(key, captured["kwargs"]["env"])

        result = availability.check(
            "claude", "claude-fable-5", "max", "2.1.214", REPO_ROOT, 7,
            runner=self.claude_runner(
                "claude-fable-5", "max", usage_model="claude-opus-4-8"
            ), env={},
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("claude-opus-4-8", result["details"])

        result = availability.check(
            "claude", "claude-fable-5", "max", "2.1.214", REPO_ROOT, 7,
            runner=self.claude_runner("claude-fable-5", "high"), env={},
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("observed_effort=high", result["details"])

        result = availability.check(
            "claude", "claude-opus-4-8", "max", "2.1.214", REPO_ROOT, 7,
            runner=self.claude_runner(
                "claude-opus-4-8", "max", usage_model="claude-haiku-4-5-20251001"
            ), env={},
        )
        self.assertEqual(result["status"], "unavailable")

    def test_claude_known_effort_matrix_and_hook_observation_are_fail_closed(self):
        availability = load_availability()
        captured = {}
        result = availability.check(
            "claude", "claude-opus-4-8", "ultracode", "2.1.214", REPO_ROOT, 7,
            runner=self.claude_runner(
                "claude-opus-4-8", "xhigh", captured=captured
            ), env={},
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["requested_effort"], "ultracode")
        self.assertIn("effective provider effort xhigh", result["details"])
        self.assertEqual(result["supported_efforts"], ["ultracode"])
        self.assertEqual(captured["command"][captured["command"].index("--effort") + 1], "ultracode")

        result = availability.check(
            "claude", "claude-fable-5", "xhigh", "2.1.214", REPO_ROOT, 7,
            runner=self.claude_runner("claude-fable-5", "xhigh"), env={},
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["effort_evidence"], "stop_hook_observed")

        forged = availability_attestation("claude", "claude-fable-5", "max")
        forged["observed_effort"] = "high"
        with self.assertRaises(ValueError):
            availability.validate_result(forged, require_available=True)

    def test_claude_probe_timeout_and_missing_metadata_are_unknown(self):
        availability = load_availability()

        def timed_out(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

        result = availability.check(
            "claude", "claude-opus-4-8", "max", "2.1.214", REPO_ROOT, 3,
            runner=timed_out, env={},
        )
        self.assertEqual(result["status"], "unknown")
        missing = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr="")
        result = availability.check(
            "claude", "claude-opus-4-8", "max", "2.1.214", REPO_ROOT, 3,
            runner=missing, env={},
        )
        self.assertEqual(result["status"], "unknown")

        rejected = lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="effort is not supported for this model"
        )
        result = availability.check(
            "claude", "claude-fable-5", "max", "2.1.214", REPO_ROOT, 3,
            runner=rejected, env={},
        )
        self.assertEqual(result["status"], "unavailable")

    def test_bounded_probe_cleans_startup_failure_and_caps_output(self):
        availability = load_availability()
        real_popen = subprocess.Popen
        spawned = {}

        def capture_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned["process"] = process
            return process

        with (
            mock.patch.object(availability.subprocess, "Popen", side_effect=capture_popen),
            mock.patch.object(availability.SAFETY, "process_identity", side_effect=RuntimeError("identity unavailable")),
            self.assertRaises(RuntimeError),
        ):
            availability.run_bounded_command(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                text=True,
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )
        self.assertIsNotNone(spawned["process"].poll())
        self.assertFalse(availability.SAFETY.process_group_alive(spawned["process"].pid))

        with (
            mock.patch.object(availability, "MAX_PROBE_OUTPUT_BYTES", 4096),
            self.assertRaisesRegex(OSError, "finite 4096-byte capture limit"),
        ):
            availability.run_bounded_command(
                [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)"],
                text=True,
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
                check=False,
            )

        completed = availability.run_bounded_command(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
            input="bounded input",
            text=True,
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=True,
        )
        self.assertEqual(completed.stdout, "bounded input")


class PeerV2Tests(TestCase):
    def test_request_is_strict_complete_v2(self):
        peer = load_peer()
        peer.validate_request(request())
        for change in (
            {"schema_version": "1.0"},
            {"extra": True},
            {"run_id": "../escape"},
            {"peer_timeout_seconds": 2699},
            {"peer_effort": "none"},
        ):
            with self.subTest(change=change), self.assertRaises(peer.RequestValidationError):
                peer.validate_request(request(**change))
        incomplete = request()
        del incomplete["peer_model"]
        with self.assertRaises(peer.RequestValidationError):
            peer.validate_request(incomplete)

    def test_prompt_is_single_compact_escaped_xml_contract(self):
        peer = load_peer()
        prompt = peer.build_prompt(request(target="A < B && C > D"), REPO_ROOT, PEER_SCHEMA)
        for section in ("role", "outcome", "evidence", "boundaries", "task", "stop"):
            self.assertEqual(prompt.count("<" + section + ">"), 1)
        self.assertIn("&lt;x/&gt;", prompt)
        self.assertIn("Seek disconfirming evidence", prompt)
        self.assertIn("current official documentation or primary sources", prompt)
        self.assertIn("Do not modify files", prompt)
        self.assertIn("run_id=agent-collab-test", prompt)
        self.assertIn("origin=codex; host=codex; peer=claude", prompt)
        self.assertIn("<target>A &lt; B &amp;&amp; C &gt; D</target>", prompt)
        self.assertIn("copy the decoded task target exactly", prompt)
        self.assertNotIn("<request_json>", prompt)
        self.assertNotIn("<response_schema>", prompt)
        self.assertNotIn('"schema_version"', prompt)

    def test_prompt_applies_modes_and_resolved_boundaries(self):
        peer = load_peer()
        for mode in CANONICAL_MODES:
            prompt = peer.build_prompt(request(mode=mode), REPO_ROOT, PEER_SCHEMA)
            self.assertIn(peer.ROLE_BY_MODE[mode], prompt)
            self.assertIn(peer.MODE_CONTRACT_BY_MODE[mode], prompt)
        offline = peer.build_prompt(
            request(online_research=False, local_subagents_allowed=False, max_local_subagents=0),
            REPO_ROOT,
            PEER_SCHEMA,
        )
        self.assertIn("Do not research online", offline)
        self.assertIn("Do not use local subagents", offline)

    def test_claude_command_exact_defaults_and_full_capability(self):
        peer = load_peer()
        command = peer.build_peer_command(request(), "prompt", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {})
        args = command.args
        self.assertEqual(args[:2], ["claude", "-p"])
        self.assertEqual(args[args.index("--model") + 1], "claude-opus-4-8")
        self.assertEqual(args[args.index("--effort") + 1], "max")
        self.assertEqual(args[args.index("--permission-mode") + 1], "bypassPermissions")
        self.assertEqual(args[args.index("--tools") + 1], "default")
        self.assertEqual(args[args.index("--max-turns") + 1], "50")
        self.assertIn("--no-session-persistence", args)
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertNotIn("--allowedTools", args)
        self.assertEqual(command.stdin, "prompt")

    def test_claude_fable_safe_offline_and_custom_tools(self):
        peer = load_peer()
        fable = peer.build_peer_command(
            request(peer_model="claude-fable-5"), "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}
        ).args
        self.assertEqual(fable[fable.index("--model") + 1], "claude-fable-5")
        safe = peer.build_peer_command(
            request(safe_mode=True, online_research=False, claude_tools="Read,Grep,WebSearch"),
            "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}
        ).args
        self.assertEqual(safe[safe.index("--permission-mode") + 1], "plan")
        self.assertEqual(safe[safe.index("--tools") + 1], "Read,Grep")
        self.assertEqual(safe[safe.index("--disallowedTools") + 1], "WebSearch,WebFetch")

    def test_codex_command_has_current_global_flag_shape(self):
        peer = load_peer()
        req = request(peer="codex", host="claude", origin="claude")
        args = peer.build_peer_command(req, "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}).args
        self.assertEqual(args[:6], ["codex", "--ask-for-approval", "never", "exec", "--strict-config", "--ephemeral"])
        self.assertEqual(args[args.index("--model") + 1], "gpt-5.6-sol")
        self.assertEqual(args[args.index("--sandbox") + 1], "danger-full-access")
        self.assertIn('model_reasoning_effort="max"', args)
        self.assertIn('web_search="live"', args)
        self.assertIn("--json", args)
        self.assertEqual(args[-1], "-")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)

    def test_codex_safe_offline_and_invariant_order(self):
        peer = load_peer()
        req = request(
            peer="codex", host="claude", origin="claude", safe_mode=True, online_research=False,
            codex_config=['model_reasoning_effort="low"', "foo=true"],
        )
        args = peer.build_peer_command(req, "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}).args
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertIn('web_search="disabled"', args)
        self.assertGreater(args.index('model_reasoning_effort="max"'), args.index('model_reasoning_effort="low"'))

    def test_environment_cannot_override_resolved_request(self):
        peer = load_peer()
        env = {
            "CLAUDE_AGENT_COLLAB_MODEL": "wrong",
            "CLAUDE_AGENT_COLLAB_EFFORT": "low",
            "AGENT_COLLAB_SAFE_MODE": "1",
            "AGENT_COLLAB_WEB_RESEARCH": "disabled",
        }
        args = peer.build_peer_command(request(), "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), env).args
        self.assertEqual(args[args.index("--model") + 1], "claude-opus-4-8")
        self.assertEqual(args[args.index("--permission-mode") + 1], "bypassPermissions")

    def test_current_effort_catalog_and_exact_claude_launch_controls(self):
        peer = load_peer()
        availability = load_availability()
        self.assertFalse(hasattr(peer, "CODEX_EFFORT_CHOICES"))
        self.assertEqual(
            availability.CLAUDE_EFFORT_CHOICES,
            {"low", "medium", "high", "xhigh", "max", "ultracode"},
        )
        args = peer.build_peer_command(request(peer_effort="xhigh"), "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}).args
        self.assertEqual(args[args.index("--effort") + 1], "xhigh")
        settings = json.loads(args[args.index("--settings") + 1])
        self.assertEqual(settings["fallbackModel"], [])
        self.assertIs(settings["disableAllHooks"], False)
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["matcher"], "Agent")
        self.assertEqual(args[-2:], ["--tools", "default"])
        self.assertNotIn("--fallback-model", args)
        peer.validate_request(request(peer_effort="ultracode"))
        ultracode_args = peer.build_peer_command(
            request(peer_effort="ultracode"), "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {}
        ).args
        self.assertEqual(ultracode_args[ultracode_args.index("--effort") + 1], "ultracode")

        no_agents = peer.build_peer_command(
            request(local_subagents_allowed=False, max_local_subagents=0),
            "p", REPO_ROOT, PEER_SCHEMA, Path("/tmp/out"), {},
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

    def test_claude_effort_environment_is_removed_before_launch(self):
        peer = load_peer()
        captured = {}
        envelope = {
            "type": "result",
            "structured_output": valid_report(),
            "modelUsage": {"claude-opus-4-8": {}},
        }

        def fake_run(*args, **kwargs):
            captured["env"] = kwargs["env"]
            captured["input"] = kwargs["input"]
            return SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")

        overrides = {key: "forced" for key in peer.CLAUDE_EFFORT_ENV_OVERRIDES}
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"same": True}),
            mock.patch.object(peer.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(peer.SAFETY, "sandbox_preflight") as sandbox_preflight,
            mock.patch.object(peer, "run_peer_command", side_effect=fake_run),
        ):
            result = peer.run_request(request(), Path(tmp), env=overrides)
        self.assertEqual(result["status"], "ok")
        sandbox_preflight.assert_not_called()
        for key in peer.CLAUDE_EFFORT_ENV_OVERRIDES:
            self.assertNotIn(key, captured["env"])
        self.assertIn("<task>", captured["input"])
        self.assertIn("</task>", captured["input"])

    def test_xml_encoded_peer_target_is_canonicalized_at_invocation(self):
        peer = load_peer()
        req = request(target="A < B && C > D")
        envelope = {
            "type": "result",
            "structured_output": valid_report(target="A &lt; B &amp;&amp; C &gt; D"),
            "modelUsage": {"claude-opus-4-8": {}},
        }
        completed = SimpleNamespace(returncode=0, stdout=json.dumps(envelope), stderr="")
        with (
            tempfile.TemporaryDirectory() as tmp_name,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"same": True}),
            mock.patch.object(peer.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(peer, "run_peer_command", return_value=completed),
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
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"same": True}),
            mock.patch.object(peer.shutil, "which", return_value="/usr/bin/codex"),
            mock.patch.object(peer, "run_peer_command", return_value=success),
        ):
            result = peer.run_request(req, Path(tmp))
        self.assertEqual(result["error"]["kind"], "missing_output_artifact")

    def test_codex_reads_only_current_output_artifact(self):
        peer = load_peer()
        req = request(peer="codex", host="claude", origin="claude")
        report = valid_report(origin="claude", host="claude", peer="codex")

        def fake_run(args, **kwargs):
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text(json.dumps(report))
            return SimpleNamespace(returncode=0, stdout="not the report", stderr="")

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"same": True}),
            mock.patch.object(peer.shutil, "which", return_value="/usr/bin/codex"),
            mock.patch.object(peer, "run_peer_command", side_effect=fake_run),
        ):
            result = peer.run_request(req, Path(tmp))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["peer"], "codex")

    def test_host_cli_shadow_is_executable_and_refuses_peer_recursion(self):
        peer = load_peer()
        with tempfile.TemporaryDirectory() as tmp:
            guard_bin = peer.make_host_cli_guard(Path(tmp), "codex")
            completed = subprocess.run(
                [str(guard_bin / "codex")], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        self.assertEqual(completed.returncode, 64)
        self.assertIn("may not call the host CLI", completed.stderr)

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

    def test_peer_command_requires_positive_finite_timeout(self):
        peer = load_peer()
        for timeout in (None, 0, -1, float("inf"), float("nan")):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(
                ValueError, "positive finite timeout"
            ):
                peer.run_peer_command(
                    [sys.executable, "-c", "pass"],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                )
        with self.assertRaisesRegex(ValueError, "positive finite timeout"):
            peer.run_peer_command(
                [sys.executable, "-c", "pass"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

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
            while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
                stat_text = Path(f"/proc/{child_pid}/stat").read_text(encoding="utf-8")
                if stat_text.rsplit(")", 1)[1].strip().startswith("Z"):
                    break
                time.sleep(0.02)
            if Path(f"/proc/{child_pid}/stat").exists():
                stat_text = Path(f"/proc/{child_pid}/stat").read_text(encoding="utf-8")
                self.assertTrue(stat_text.rsplit(")", 1)[1].strip().startswith("Z"))

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
                if Path(f"/proc/{child_pid}/stat").exists():
                    stat_text = Path(f"/proc/{child_pid}/stat").read_text(encoding="utf-8")
                    self.assertTrue(stat_text.rsplit(")", 1)[1].strip().startswith("Z"))
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
        self.assertIsNone(peer.claude_model_mismatch_details(good, "claude-fable-5"))
        self.assertIn("unexpected model usage", peer.claude_model_mismatch_details(fallback, "claude-fable-5")["reason"])
        self.assertIn("did not report", peer.claude_model_mismatch_details(missing, "claude-fable-5")["reason"])
        self.assertIn("unexpected model usage", peer.claude_model_mismatch_details(fallback, "claude-opus-4-8")["reason"])
        downgrade = json.dumps({"effort": "high", "modelUsage": {"claude-fable-5": {}}})
        self.assertIn(
            "reported effort",
            peer.claude_selection_mismatch_details(downgrade, "claude-fable-5", "max")["reason"],
        )
        ultracode = json.dumps({"effort": "xhigh", "modelUsage": {"claude-opus-4-8": {}}})
        self.assertIsNone(
            peer.claude_selection_mismatch_details(ultracode, "claude-opus-4-8", "ultracode")
        )

    def test_claude_model_attestation_rejects_broad_family_aliases(self):
        availability = load_availability()
        self.assertFalse(availability.model_identifier_matches("claude", "claude-fable-5"))
        self.assertFalse(availability.model_identifier_matches("opus", "claude-opus-4-8"))
        self.assertFalse(
            availability.model_identifier_matches(
                "claude-opus-4-8", "claude-opus-4-8-20260701"
            )
        )
        self.assertFalse(
            availability.model_identifier_matches(
                "claude-opus-4-8", "claude-opus-4-8-fast"
            )
        )

    def test_codex_jsonl_detects_observable_model_reroute_and_effort_downgrade(self):
        peer = load_peer()
        reroute = json.dumps({
            "type": "model/rerouted", "fromModel": "gpt-5.6-sol", "toModel": "gpt-5.6-safe",
        })
        mismatch = peer.codex_selection_mismatch_details(reroute, "gpt-5.6-sol", "max")
        self.assertIn("model reroute", mismatch["reason"])
        exact_reroute = json.dumps({
            "type": "model/rerouted", "fromModel": "codex-auto-review", "toModel": "gpt-5.6-sol",
        })
        self.assertIsNone(peer.codex_selection_mismatch_details(exact_reroute, "gpt-5.6-sol", "max"))
        downgraded = json.dumps({
            "type": "session/configured", "model": "gpt-5.6-sol", "reasoningEffort": "high",
        })
        mismatch = peer.codex_selection_mismatch_details(downgraded, "gpt-5.6-sol", "max")
        self.assertEqual(mismatch["observed_efforts"], ["high"])

    def test_model_entitlement_errors_are_classified_without_fallback(self):
        peer = load_peer()
        message = peer.model_unavailable_message(
            "",
            "The gpt-5.6-sol model is not supported when using Codex with this account.",
            "gpt-5.6-sol",
        )
        self.assertEqual(message, "Requested peer model is unavailable: gpt-5.6-sol")

    def test_report_v2_semantics_and_current_provider_schema(self):
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
        self.assertEqual(schema["properties"]["schema_version"]["const"], "2.0")

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
            self.assertEqual(normalized.metadata["schema_version"], "2.0")
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
            "modelUsage": {"claude-opus-4-8": {}},
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"same": True}),
            mock.patch.object(peer.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(
                peer,
                "run_peer_command",
                return_value=SimpleNamespace(returncode=0, stdout=json.dumps(bare_claude), stderr=""),
            ),
        ):
            claude_result = peer.run_request(request(), Path(tmp))
        self.assertEqual(claude_result["error"]["kind"], "noncanonical_output")

        codex_request = request(peer="codex", host="claude", origin="claude")
        codex_report = valid_report(origin="claude", host="claude", peer="codex")

        def fake_codex_run(args, **kwargs):
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text(json.dumps({"structured_output": codex_report}))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"same": True}),
            mock.patch.object(peer.shutil, "which", return_value="/usr/bin/codex"),
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
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"same": True}),
            mock.patch.object(peer.shutil, "which", return_value=None),
        ):
            missing = peer.run_request(request(), Path(tmp), env={"PATH": ""})
        self.assertEqual(missing["error"]["kind"], "missing_cli")

    def test_claude_api_error_preserves_model_usage(self):
        peer = load_peer()
        envelope = {"type": "result", "is_error": True, "result": "unavailable", "modelUsage": {"claude-opus-4-8": {}}}
        completed = SimpleNamespace(returncode=1, stdout=json.dumps(envelope), stderr="api error")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(peer, "git_mutation_snapshot", return_value={"same": True}),
            mock.patch.object(peer.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(peer, "run_peer_command", return_value=completed),
        ):
            result = peer.run_request(request(), Path(tmp))
        self.assertEqual(result["error"]["kind"], "peer_api_error")
        details = json.loads(result["error"]["details"])
        self.assertEqual(details["modelUsage"], {"claude-opus-4-8": {}})


class HostV2Tests(TestCase):
    def test_current_defaults_and_removed_settings(self):
        host = load_host()
        self.assertEqual(host.SETTINGS_SCHEMA_VERSION, "2.0")
        self.assertEqual(host.SETTING_DEFAULTS["codex_model"], "gpt-5.6-sol")
        self.assertEqual(host.SETTING_DEFAULTS["codex_effort"], "max")
        self.assertEqual(host.SETTING_DEFAULTS["claude_model"], "claude-opus-4-8")
        self.assertEqual(host.SETTING_DEFAULTS["claude_effort"], "max")
        self.assertIs(host.SETTING_DEFAULTS["online_research"], True)
        self.assertNotIn("claude_max_budget_usd", host.SETTING_DEFAULTS)
        self.assertNotIn("web_research", host.SETTING_DEFAULTS)

    def test_settings_are_exact_v2_with_env_local_global_precedence(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            global_path, local_path = root / "global.json", root / "local.json"
            host.write_settings_file(global_path, complete_settings(host, claude_model="global"))
            host.write_settings_file(local_path, complete_settings(host, claude_model="local"))
            with (
                mock.patch.object(host, "default_global_settings_path", return_value=global_path),
                mock.patch.object(host, "default_local_settings_path", return_value=local_path),
            ):
                resolved = host.resolve_settings(
                    root, {"CLAUDE_AGENT_COLLAB_MODEL": "env", "AGENT_COLLAB_ONLINE_RESEARCH": "0"}
                )
        self.assertEqual(resolved["settings"]["claude_model"], "env")
        self.assertIs(resolved["settings"]["online_research"], False)

    def test_legacy_and_partial_settings_are_rejected(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"schema_version": "1.0", "updated_at": "x", "settings": {}}))
            _, error = host.load_settings_file(path, "test")
            self.assertIn("legacy settings", error)
            path.write_text(json.dumps({"schema_version": "2.0", "updated_at": "x", "settings": {"safe_mode": False}}))
            _, error = host.load_settings_file(path, "test")
            self.assertIn("match v2 exactly", error)

    def test_persisted_settings_reject_legacy_type_coercion(self):
        host = load_host()
        cases = (
            ("safe_mode", "false", "JSON boolean"),
            ("max_local_subagents", "8", "JSON integer"),
            ("codex_config", "foo=true", "JSON array"),
            ("claude_max_turns", 50, "JSON string"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            for key, value, expected in cases:
                settings = complete_settings(host)
                settings[key] = value
                path.write_text(json.dumps({
                    "schema_version": "2.0", "updated_at": "2026-07-18T00:00:00Z", "settings": settings,
                }))
                loaded, error = host.load_settings_file(path, "test")
                self.assertEqual(loaded, {})
                self.assertIn(expected, error)

    def test_removed_environment_key_is_ignored(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(host, "default_global_settings_path", return_value=root / "g"),
                mock.patch.object(host, "default_local_settings_path", return_value=root / "l"),
            ):
                resolved = host.resolve_settings(root, {"AGENT_COLLAB_WEB_RESEARCH": "disabled"})
        self.assertIs(resolved["settings"]["online_research"], True)

    def test_parser_accepts_user_model_effort_and_research(self):
        host = load_host()
        args = host.build_parser().parse_args([
            "start", "--host", "codex", "--target", "diff", "--brief", "review",
            "--peer-model", "claude-fable-5", "--peer-effort", "max", "--no-online-research",
        ])
        self.assertEqual(args.peer_model, "claude-fable-5")
        self.assertEqual(args.peer_effort, "max")
        self.assertIs(args.online_research, False)

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
            host.normalize_settings({"claude_model": "claude-fable-5"}, "test")
        settings = complete_settings(host, claude_model="claude-fable-5")
        with self.assertRaisesRegex(ValueError, "cannot persist Fable"):
            host.validate_v2_settings_types(settings, "test")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(host, "default_global_settings_path", return_value=root / "g"),
                mock.patch.object(host, "default_local_settings_path", return_value=root / "l"),
            ):
                resolved = host.resolve_settings(
                    root,
                    {"CLAUDE_AGENT_COLLAB_MODEL": "claude-fable-5"},
                )
        self.assertEqual(resolved["settings"]["claude_model"], "claude-opus-4-8")
        self.assertIn("cannot persist Fable", resolved["environment_errors"][0])

    def test_cli_version_parser_and_floor(self):
        host = load_host()
        self.assertEqual(host.parse_cli_version("codex-cli 0.144.5"), (0, 144, 5))
        self.assertEqual(host.parse_cli_version("2.1.212 (Claude Code)"), (2, 1, 212))
        self.assertEqual(host.MIN_CLI_VERSIONS["claude"], (2, 1, 214))
        old = SimpleNamespace(returncode=0, stdout="codex-cli 0.144.4", stderr="")
        with (
            mock.patch.object(host.shutil, "which", return_value="/usr/bin/codex"),
            mock.patch.object(host.subprocess, "run", return_value=old),
            self.assertRaises(SystemExit),
        ):
            host.require_peer_cli_version("codex")
        old_claude = SimpleNamespace(returncode=0, stdout="2.1.213 (Claude Code)", stderr="")
        with (
            mock.patch.object(host.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(host.subprocess, "run", return_value=old_claude),
            self.assertRaises(SystemExit),
        ):
            host.require_peer_cli_version("claude")

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
            mock.patch.object(host, "load_availability_runtime", return_value=availability),
            mock.patch.object(host, "require_peer_cli_version", return_value="2.1.214"),
            mock.patch.object(availability, "check", return_value=output) as check,
            redirect_stdout(stream),
        ):
            self.assertEqual(host.check_availability(args), 0)
        parsed = json.loads(stream.getvalue())
        self.assertEqual(parsed, output)
        self.assertEqual(set(parsed), availability.RESULT_KEYS)
        check.assert_called_once_with(
            "claude", "claude-fable-5", "max", "2.1.214", REPO_ROOT, 5.0
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
                model="claude-opus-4-8",
                effort="max",
                source="claude_cli_probe",
                cli_version="2.1.214",
                details="probe metadata unavailable",
                evidence="test unknown",
            )
            availability_runtime = SimpleNamespace(check=lambda *args, **kwargs: unknown)
            with (
                mock.patch.object(host, "workspace_guard_path", return_value=guard),
                mock.patch.object(host, "resolve_settings", return_value=resolved),
                mock.patch.object(host, "require_peer_cli_version", return_value="2.1.214"),
                mock.patch.object(host, "load_availability_runtime", return_value=availability_runtime),
                mock.patch.object(host, "acquire_workspace_guard") as acquire,
                mock.patch.object(host.subprocess, "Popen") as popen,
                self.assertRaises(SystemExit) as raised,
            ):
                host.start(args)
            self.assertIn('"status": "unknown"', str(raised.exception))
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
                mock.patch.object(host, "resolve_settings", return_value=resolved),
                mock.patch.object(host, "require_peer_cli_version", return_value="2.1.214"),
                mock.patch.object(host, "load_availability_runtime", return_value=availability_runtime),
                mock.patch.object(host, "acquire_workspace_guard") as acquire,
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
                mock.patch.object(host, "workspace_guard_path", return_value=guard),
                mock.patch.object(host, "resolve_settings", return_value=resolved),
                mock.patch.object(host.SAFETY, "sandbox_preflight", return_value=sandbox),
                mock.patch.object(host, "require_peer_cli_version") as cli_version,
                mock.patch.object(host, "load_availability_runtime") as availability,
                mock.patch.object(host, "acquire_workspace_guard") as acquire,
                mock.patch.object(host.subprocess, "Popen") as popen,
                self.assertRaises(SystemExit) as raised,
            ):
                host.start(args)
            failure = json.loads(str(raised.exception))
            self.assertEqual(failure["status"], "sandbox_unavailable")
            cli_version.assert_not_called()
            availability.assert_called_once()
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
            ])
            resolved = {
                "settings": complete_settings(host),
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
                mock.patch.object(host, "resolve_settings", return_value=resolved),
                mock.patch.object(host, "require_peer_cli_version", return_value="2.1.214"),
                mock.patch.object(host, "load_availability_runtime", return_value=availability_runtime),
                mock.patch.object(host, "run_snapshot"),
                mock.patch.object(host, "workspace_guard_path", return_value=root / "active-v2.json"),
                mock.patch.object(host.SAFETY, "sandbox_preflight") as sandbox_preflight,
                mock.patch.object(host.SAFETY, "process_identity", return_value=valid_process_identity()),
                mock.patch.object(host.subprocess, "Popen", return_value=FakePopen()),
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(host.start(args), 0)
            sandbox_preflight.assert_not_called()
            saved = json.loads((run_root / "override" / "host-request.json").read_text())
            process = json.loads((run_root / "override" / "peer-process.json").read_text())
            self.assertTrue((root / "active-v2.json").exists())
        self.assertEqual(saved["peer_model"], "claude-fable-5")
        self.assertEqual(saved["peer_effort"], "max")
        self.assertEqual(saved["availability_attestation"], attestation)
        self.assertIs(saved["online_research"], False)
        self.assertEqual(process["schema_version"], "2.0")
        self.assertEqual(process["peer_cli_version"], "2.1.214")

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
            (run_root / "state.json").write_text('{"version":1,"jobs":[]}')
            guard = root / "active-v2.json"
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "diff", "--brief", "review",
                "--run-id", "strict-state", "--run-root", str(run_root), "--repo-root", str(root),
            ])
            with (
                mock.patch.object(host, "workspace_guard_path", return_value=guard),
                mock.patch.object(host, "acquire_workspace_guard") as acquire,
                mock.patch.object(host.subprocess, "Popen") as popen,
                self.assertRaises(SystemExit) as raised,
            ):
                host.start(args)
            self.assertIn("invalid v2 Agent Collab state", str(raised.exception))
            acquire.assert_not_called()
            popen.assert_not_called()
            self.assertFalse(guard.exists())
            self.assertFalse((run_root / "strict-state").exists())

    def test_workspace_guard_blocks_helpers_and_concurrent_starts(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / "active-v2.json"
            with mock.patch.object(host, "workspace_guard_path", return_value=guard):
                host.acquire_workspace_guard(root, "outer", "codex", root / "runs" / "outer")
                with self.assertRaises(SystemExit) as raised:
                    host.acquire_workspace_guard(root, "inner", "codex", root / "runs" / "inner")
                self.assertIn("active run=outer", str(raised.exception))
                args = host.build_parser().parse_args([
                    "start", "--host", "codex", "--target", "diff", "--brief", "review",
                    "--run-id", "inner", "--run-root", str(root / "runs"), "--repo-root", str(root),
                ])
                with self.assertRaises(SystemExit):
                    host.start(args)
                self.assertFalse((root / "runs" / "inner").exists())
                host.release_workspace_guard(root, "outer")
                self.assertFalse(guard.exists())

    def test_workspace_guard_lock_wait_is_bounded(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / "active-v2.json"
            with (
                mock.patch.object(host, "workspace_guard_path", return_value=guard),
                mock.patch.object(host, "WORKSPACE_GUARD_LOCK_TIMEOUT_SECONDS", 0.05),
                mock.patch.object(host, "WORKSPACE_GUARD_LOCK_POLL_SECONDS", 0.005),
                host.workspace_guard_transaction(root),
                ThreadPoolExecutor(max_workers=1) as executor,
            ):
                blocked = executor.submit(host.read_workspace_guard, root)
                with self.assertRaisesRegex(RuntimeError, "timed out acquiring"):
                    blocked.result(timeout=1)

    def test_workspace_guard_only_auto_clears_a_dead_starting_launcher(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / "active-v2.json"
            with mock.patch.object(host, "workspace_guard_path", return_value=guard):
                host.acquire_workspace_guard(root, "stale", "codex", root / "runs" / "stale")
                with mock.patch.object(host.SAFETY, "process_identity_matches", return_value=False):
                    host.require_workspace_guard_available(root)
                self.assertFalse(guard.exists())

                host.acquire_workspace_guard(root, "active", "codex", root / "runs" / "active")
                host.update_workspace_guard(
                    root,
                    "active",
                    phase="peer_running",
                    peer_identity=valid_process_identity(),
                )
                with (
                    mock.patch.object(host.SAFETY, "process_identity_matches", return_value=False),
                    self.assertRaises(SystemExit),
                ):
                    host.require_workspace_guard_available(root)
                self.assertTrue(guard.exists())
                host.release_workspace_guard(root, "active")

    def test_stale_start_guard_recovery_serializes_replacement(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / "active-v2.json"
            with mock.patch.object(host, "workspace_guard_path", return_value=guard):
                host.acquire_workspace_guard(root, "stale", "codex", root / "runs" / "stale")
                identity_checked = threading.Event()
                allow_recovery = threading.Event()

                def dead_launcher(_identity):
                    identity_checked.set()
                    self.assertTrue(allow_recovery.wait(timeout=2))
                    return False

                with (
                    mock.patch.object(
                        host.SAFETY, "process_identity_matches", side_effect=dead_launcher
                    ),
                    ThreadPoolExecutor(max_workers=2) as executor,
                ):
                    recovery = executor.submit(host.require_workspace_guard_available, root)
                    self.assertTrue(identity_checked.wait(timeout=2))
                    replacement = executor.submit(
                        host.acquire_workspace_guard,
                        root,
                        "replacement",
                        "codex",
                        root / "runs" / "replacement",
                    )
                    time.sleep(0.05)
                    self.assertFalse(replacement.done())
                    allow_recovery.set()
                    recovery.result(timeout=2)
                    replacement.result(timeout=2)

                active = json.loads(guard.read_text(encoding="utf-8"))
                self.assertEqual(active["run_id"], "replacement")
                host.release_workspace_guard(root, "replacement")

    def test_clear_history_never_deletes_the_guarded_orphan_run(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp_name:
            base = Path(tmp_name)
            root, run_root = base / "repo", base / "runs"
            root.mkdir()
            run_dir = run_root / "guarded-orphan"
            run_dir.mkdir(parents=True)
            (run_dir / "host-request.json").write_text("{}")
            guard = base / "active-v2.json"
            with mock.patch.object(host, "workspace_guard_path", return_value=guard):
                host.acquire_workspace_guard(root, "guarded-orphan", "codex", run_dir)
                args = host.build_parser().parse_args([
                    "clear-history", "--all", "--yes", "--run-root", str(run_root),
                    "--repo-root", str(root),
                ])
                status, output = host.build_clear_history_output(args)
            self.assertEqual(status, 0)
            self.assertEqual(output["deleted"], [])
            self.assertEqual([item["run_id"] for item in output["active_preserved"]], ["guarded-orphan"])
            self.assertTrue(run_dir.exists())
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
            attestation = availability_attestation("claude", "claude-opus-4-8", "max")
            availability_runtime = SimpleNamespace(
                check=lambda *args, **kwargs: attestation,
                validate_result=lambda *args, **kwargs: None,
            )
            with (
                mock.patch.object(host, "workspace_guard_path", return_value=guard),
                mock.patch.object(host, "resolve_settings", return_value=resolved),
                mock.patch.object(host, "require_peer_cli_version", return_value="2.1.214"),
                mock.patch.object(host, "load_availability_runtime", return_value=availability_runtime),
                mock.patch.object(host, "run_snapshot"),
                mock.patch.object(host.subprocess, "Popen", side_effect=OSError("launch failed")),
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
            attestation = availability_attestation("claude", "claude-opus-4-8", "max")
            availability_runtime = SimpleNamespace(
                check=lambda *args, **kwargs: attestation,
                validate_result=lambda *args, **kwargs: None,
            )
            with (
                mock.patch.object(host, "workspace_guard_path", return_value=guard),
                mock.patch.object(host, "resolve_settings", return_value=resolved),
                mock.patch.object(host, "require_peer_cli_version", return_value="2.1.214"),
                mock.patch.object(host, "load_availability_runtime", return_value=availability_runtime),
                mock.patch.object(host, "run_snapshot"),
                mock.patch.object(host.subprocess, "Popen", return_value=SpawnedProcess()),
                mock.patch.object(
                    host.SAFETY,
                    "process_identity",
                    side_effect=[valid_process_identity(os.getpid(), os.getpgrp()), RuntimeError("identity failed")],
                ),
                mock.patch.object(
                    host.SAFETY,
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
            host.validate_peer_process(process, request(), run_dir)
            for changed in (
                {**process, "extra": True},
                {**process, "pid": "43210"},
                {**process, "run_id": "wrong"},
            ):
                with self.assertRaises(host.ArtifactValidationError):
                    host.validate_peer_process(changed, request(), run_dir)

    def test_helper_reports_require_one_strict_v2_envelope(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            path = run_dir / "helper-reports.json"
            payload = {
                "schema_version": "2.0",
                "run_id": request()["run_id"],
                "reports": [{
                    "name": "reviewer", "summary": "checked",
                    "claims": [{"claim": "x", "status": "confirmed", "evidence": "test"}],
                }],
            }
            path.write_text(json.dumps(payload))
            claims = host.load_helper_claims(run_dir, request())
            self.assertEqual(claims[0]["source"], "helper")
            duplicate = {**payload, "reports": payload["reports"] * 2}
            path.write_text(json.dumps(duplicate))
            with self.assertRaisesRegex(host.ArtifactValidationError, "duplicate helper"):
                host.load_helper_claims(run_dir, request())
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(host.ArtifactValidationError, "resolved per-run limit is 0"):
                host.load_helper_claims(
                    run_dir,
                    request(local_subagents_allowed=False, max_local_subagents=8),
                )
            for legacy in ([payload["reports"][0]], {"claims": payload["reports"][0]["claims"]}):
                path.write_text(json.dumps(legacy))
                with self.assertRaises(host.ArtifactValidationError):
                    host.load_helper_claims(run_dir, request())

    def test_finish_then_complete_holds_and_releases_workspace_guard(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            run_dir = run_root / request()["run_id"]
            run_dir.mkdir(parents=True)
            guard = root / "active-v2.json"
            with mock.patch.object(host, "workspace_guard_path", return_value=guard):
                host.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                (run_dir / "host-request.json").write_text(json.dumps(request()))
                (run_dir / "peer-process.json").write_text(json.dumps(valid_peer_process(run_dir, root, guard)))
                (run_dir / "provider-process.json").write_text(json.dumps(valid_provider_process()))
                (run_dir / "host-first-pass.json").write_text(json.dumps(valid_host_first_pass()))
                (run_dir / "peer-report.json").write_text(json.dumps(valid_report()))
                finish_args = host.build_parser().parse_args([
                    "finish", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                with (
                    mock.patch.object(host, "wait_for_peer_report", return_value="report_ready"),
                    mock.patch.object(host, "record_host_workspace_mutation", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.finish(finish_args), 0)
                self.assertTrue(guard.exists())
                self.assertEqual(json.loads((run_dir / "host-result.json").read_text())["phase"], "ready_for_synthesis")
                self.assertEqual(host.load_state_runtime().find_job(run_root, request()["run_id"])["status"], "synthesizing")
                with (
                    mock.patch.object(host, "wait_for_peer_report", return_value="report_ready"),
                    mock.patch.object(host, "record_host_workspace_mutation", return_value=None),
                    mock.patch.object(
                        host.SAFETY,
                        "process_group_alive",
                        side_effect=AssertionError("historical PGID must not be rechecked"),
                    ),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.finish(finish_args), 0)
                complete_args = host.build_parser().parse_args([
                    "complete", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                with (
                    mock.patch.object(host, "record_host_workspace_mutation", return_value=None),
                    self.assertRaises(SystemExit),
                ):
                    host.complete(complete_args)
                self.assertTrue(guard.exists())
                (run_dir / "host-synthesis.json").write_text(json.dumps(valid_host_synthesis()))
                with (
                    mock.patch.object(host, "process_identity_matches", return_value=True),
                    mock.patch.object(host, "process_alive", return_value=True),
                    mock.patch.object(host, "record_host_workspace_mutation", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.complete(complete_args), 0)
                self.assertFalse(guard.exists())
                self.assertEqual(json.loads((run_dir / "host-result.json").read_text())["phase"], "done")
                self.assertEqual(host.load_state_runtime().find_job(run_root, request()["run_id"])["status"], "completed")
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(host.complete(complete_args), 0)
                host.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                host.update_workspace_guard(
                    root,
                    request()["run_id"],
                    phase="peer_running",
                    peer_identity=valid_process_identity(),
                )
                with self.assertRaisesRegex(SystemExit, "wrapper-quiescence proof"):
                    host.complete(complete_args)
                self.assertTrue(guard.exists())
                host.release_workspace_guard(root, request()["run_id"])
                (run_dir / "host-synthesis.json").write_text(
                    json.dumps({**valid_host_synthesis(), "final_answer_ready": False})
                )
                with self.assertRaises(SystemExit):
                    host.complete(complete_args)

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
            with mock.patch.object(host, "workspace_guard_path", return_value=guard):
                host.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                (run_dir / "host-request.json").write_text(json.dumps(request()))
                (run_dir / "peer-process.json").write_text(
                    json.dumps(valid_peer_process(run_dir, root, guard))
                )
                (run_dir / "provider-process.json").write_text(json.dumps(valid_provider_process()))
                (run_dir / "host-first-pass.json").write_text(json.dumps(valid_host_first_pass()))
                (run_dir / "peer-report.json").write_text(json.dumps(valid_report()))
                host.run_snapshot(root, run_dir / "before.snapshot", ignored_paths=[run_dir])
                finish_args = host.build_parser().parse_args([
                    "finish", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                with (
                    mock.patch.object(host, "wait_for_peer_report", return_value="report_ready"),
                    mock.patch.object(host.SAFETY, "process_group_alive", return_value=False),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.finish(finish_args), 0)
                (run_dir / "host-synthesis.json").write_text(json.dumps(valid_host_synthesis()))
                tracked.write_text("after", encoding="utf-8")
                complete_args = host.build_parser().parse_args([
                    "complete", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                with self.assertRaisesRegex(SystemExit, "resynthesize"):
                    host.complete(complete_args)
                self.assertTrue(guard.exists())
                refreshed = json.loads((run_dir / "host-result.json").read_text())
                self.assertIs(refreshed["workspace_mutation"]["changed"], True)
                self.assertIn("tracked.txt", refreshed["workspace_mutation"]["changed_paths"])
                synthesis = valid_host_synthesis()
                synthesis["workspace_mutation_sha256"] = refreshed["workspace_mutation_sha256"]
                (run_dir / "host-synthesis.json").write_text(json.dumps(synthesis))
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(host.complete(complete_args), 0)
                self.assertFalse(guard.exists())

    def test_cancel_releases_workspace_guard_without_pid_reuse_risk(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            run_dir = run_root / request()["run_id"]
            run_dir.mkdir(parents=True)
            guard = root / "active-v2.json"
            with mock.patch.object(host, "workspace_guard_path", return_value=guard):
                host.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                (run_dir / "host-request.json").write_text(json.dumps(request()))
                (run_dir / "peer-process.json").write_text(json.dumps(valid_peer_process(run_dir, root, guard)))
                (run_dir / "provider-process.json").write_text(json.dumps(valid_provider_process()))
                (run_dir / "peer-report.json").write_text("{malformed")
                args = host.build_parser().parse_args([
                    "cancel", str(run_dir), "--run-root", str(run_root), "--repo-root", str(root),
                ])
                with (
                    mock.patch.object(host, "process_identity_matches", return_value=True),
                    mock.patch.object(host, "process_alive", return_value=False),
                    mock.patch.object(
                        host.SAFETY,
                        "terminate_process_group",
                        return_value={"outcome": "not_running", "quiescent": True, "pgid": 43210},
                    ),
                    mock.patch.object(
                        host,
                        "record_host_workspace_mutation",
                        side_effect=host.ArtifactValidationError("post-cancel snapshot failed"),
                    ),
                    self.assertRaises(SystemExit),
                ):
                    host.cancel(args)
                self.assertTrue(guard.exists())
                self.assertEqual((run_dir / "peer.cancelled.raw").read_text(), "{malformed")
                with (
                    mock.patch.object(host, "process_identity_matches", return_value=True),
                    mock.patch.object(host, "process_alive", return_value=False),
                    mock.patch.object(
                        host.SAFETY,
                        "terminate_process_group",
                        return_value={"outcome": "not_running", "quiescent": True, "pgid": 43210},
                    ),
                    mock.patch.object(host, "record_host_workspace_mutation", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.cancel(args), 0)
                self.assertFalse(guard.exists())
                self.assertEqual(host.load_state_runtime().find_job(run_root, request()["run_id"])["status"], "cancelled")
                cancelled = json.loads((run_dir / "peer-report.json").read_text())
                self.assertEqual(cancelled["error"]["kind"], "cancelled")
                self.assertEqual((run_dir / "peer.cancelled.raw").read_text(), "{malformed")

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
        identity = host.SAFETY.process_identity(process.pid)
        process.wait(timeout=2)
        try:
            with tempfile.TemporaryDirectory() as tmp_name:
                run_dir = Path(tmp_name)
                host.write_json(
                    run_dir / "provider-process.json",
                    {
                        "schema_version": "2.0",
                        "run_id": request()["run_id"],
                        "pid": None,
                        "pgid": None,
                        "process_identity": None,
                        "status": "pending",
                        "cleanup_outcome": None,
                        "completed_at": None,
                    },
                )
                cleanup = host.terminate_tracked_process_tree(
                    {"pid": process.pid, "pgid": identity["pgid"], "process_identity": identity},
                    run_dir,
                    request(),
                )
            self.assertIs(cleanup["quiescent"], False)
            self.assertTrue(host.SAFETY.process_group_alive(identity["pgid"]))
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
            host.write_json(run_dir / "provider-process.json", running)
            process_info = {
                "pid": 43210,
                "pgid": 43210,
                "process_identity": valid_process_identity(),
            }
            with (
                mock.patch.object(host, "process_identity_matches", return_value=False),
                mock.patch.object(host.SAFETY, "process_identity_matches", return_value=False),
                mock.patch.object(host.SAFETY, "process_group_alive", return_value=False),
            ):
                cleanup = host.terminate_tracked_process_tree(process_info, run_dir, request())
            self.assertIs(cleanup["quiescent"], True)
            terminal = json.loads((run_dir / "provider-process.json").read_text())
            self.assertEqual(terminal["status"], "quiescent")
            self.assertEqual(terminal["cleanup_outcome"], "identity_mismatch_group_empty")

            null_identity = valid_provider_process(
                status="cleanup_failed",
                process_identity=None,
                cleanup_outcome="identity_unavailable",
            )
            host.write_json(run_dir / "provider-process.json", null_identity)
            with (
                mock.patch.object(host, "process_identity_matches", return_value=False),
                mock.patch.object(host.SAFETY, "process_group_alive", return_value=False),
            ):
                cleanup = host.terminate_tracked_process_tree(process_info, run_dir, request())
            self.assertIs(cleanup["quiescent"], True)
            terminal = json.loads((run_dir / "provider-process.json").read_text())
            host.validate_provider_process(terminal, request())
            self.assertIsNone(terminal["process_identity"])
            self.assertEqual(terminal["cleanup_outcome"], "identity_mismatch_group_empty")

            host.write_json(run_dir / "provider-process.json", running)

            def group_alive(pgid):
                return pgid == running["pgid"]

            with (
                mock.patch.object(host, "process_identity_matches", return_value=False),
                mock.patch.object(host.SAFETY, "process_identity_matches", return_value=False),
                mock.patch.object(host.SAFETY, "process_group_alive", side_effect=group_alive),
                mock.patch.object(host.SAFETY, "terminate_process_group") as terminate,
            ):
                cleanup = host.terminate_tracked_process_tree(process_info, run_dir, request())
            self.assertIs(cleanup["quiescent"], False)
            self.assertEqual(cleanup["provider"]["outcome"], "identity_mismatch_group_live")
            terminate.assert_not_called()

            escaped_tracker = valid_provider_process(
                status="cleanup_failed",
                cleanup_outcome="capture_channel_not_quiescent",
            )
            host.write_json(run_dir / "provider-process.json", escaped_tracker)
            with (
                mock.patch.object(host, "process_identity_matches", return_value=False),
                mock.patch.object(host.SAFETY, "process_identity_matches", return_value=False),
                mock.patch.object(host.SAFETY, "process_group_alive", return_value=False),
            ):
                cleanup = host.terminate_tracked_process_tree(process_info, run_dir, request())
            self.assertIs(cleanup["quiescent"], False)
            self.assertEqual(cleanup["provider"]["outcome"], "capture_channel_not_quiescent")
            self.assertEqual(
                json.loads((run_dir / "provider-process.json").read_text())["status"],
                "cleanup_failed",
            )

    def test_finish_timeout_and_artifacts_require_v2(self):
        host = load_host()
        with self.assertRaises(ValueError):
            host.resolve_finish_wait_timeout(None, {})
        with self.assertRaises(ValueError):
            host.resolve_finish_wait_timeout(None, {"peer_timeout_seconds": "invalid"})
        self.assertEqual(
            host.resolve_finish_wait_timeout(None, {"peer_timeout_seconds": 2700}),
            (2730.0, "peer_timeout_plus_grace"),
        )
        actual_wait, hard_deadline = host.remaining_finish_wait_seconds(
            {"started_at_epoch": 100.0, "peer_timeout_seconds": 2700},
            2730.0,
            now=3000.0,
        )
        self.assertEqual(actual_wait, 0.0)
        self.assertEqual(hard_deadline, 2830.0)
        with self.assertRaises(host.ArtifactValidationError):
            host.validate_host_first_pass(
                {"schema_version": "1.0", "run_id": "x", "summary": "x", "claims": []},
                request(run_id="x"),
            )
        with self.assertRaises(host.ArtifactValidationError):
            host.validate_host_first_pass(
                {"schema_version": "2.0", "run_id": "x", "summary": "x", "claims": [], "extra": True},
                request(run_id="x"),
            )

    def test_finish_reaps_live_provider_after_dead_wrapper_at_hard_deadline(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp_name:
            root, run_root = Path(tmp_name), Path(tmp_name) / "runs"
            run_dir = run_root / request()["run_id"]
            run_dir.mkdir(parents=True)
            guard = root / "active-v2.json"
            with mock.patch.object(host, "workspace_guard_path", return_value=guard):
                host.acquire_workspace_guard(root, request()["run_id"], "codex", run_dir)
                (run_dir / "host-request.json").write_text(json.dumps(request()))
                (run_dir / "peer-process.json").write_text(
                    json.dumps(
                        valid_peer_process(
                            run_dir,
                            root,
                            guard,
                            started_at="2020-01-01T00:00:00Z",
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
                (run_dir / "host-first-pass.json").write_text(json.dumps(valid_host_first_pass()))
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
                    mock.patch.object(host, "wait_for_peer_report", return_value="peer_exited"),
                    mock.patch.object(host, "require_process_tree_quiescent", side_effect=host.ArtifactValidationError("provider running")),
                    mock.patch.object(host, "terminate_tracked_process_tree", return_value=cleanup) as terminate,
                    mock.patch.object(host, "record_host_workspace_mutation", return_value=None),
                    redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(host.finish(args), 0)
                terminate.assert_called_once()
                timed_out = json.loads((run_dir / "peer-report.json").read_text())
                self.assertEqual(timed_out["error"]["kind"], "timeout")

    def test_finish_wait_distinguishes_ready_report_from_exited_peer(self):
        host = load_host()
        process = {"pid": 123, "process_identity": valid_process_identity(123, 123)}
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(host, "process_identity_matches", return_value=False),
            mock.patch.object(host, "process_alive", return_value=False),
        ):
            report = Path(tmp) / "peer-report.json"
            self.assertEqual(host.wait_for_peer_report(report, process, 2700), "peer_exited")
            report.write_text("{}")
            self.assertEqual(host.wait_for_peer_report(report, process, 2700), "report_ready")
            with (
                mock.patch.object(host, "process_identity_matches", return_value=True),
                mock.patch.object(host, "process_alive", return_value=True),
            ):
                self.assertEqual(host.wait_for_peer_report(report, process, 0), "timeout")

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
                mock.patch.object(host, "process_identity_matches", return_value=False),
                mock.patch.object(host.SAFETY, "process_group_alive", return_value=False),
            ):
                summary = host.summarize_run(run_dir)
            self.assertIs(summary["peer_alive"], True)
            self.assertIs(summary["provider_active"], True)
            self.assertIs(summary["process_tree_quiescent"], False)
            self.assertIs(summary["peer_report_incomplete"], True)

            (run_dir / "provider-process.json").write_text(json.dumps(valid_provider_process()))
            with (
                mock.patch.object(host, "process_identity_matches", return_value=False),
                mock.patch.object(host.SAFETY, "process_group_alive", return_value=False),
                self.assertRaises(SystemExit),
            ):
                host.summarize_run(run_dir)

    def test_host_normalization_never_recovers_from_peer_raw(self):
        host = load_host()
        peer = load_peer()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "peer-report.json").write_text("not-json")
            (run_dir / "peer.raw.json").write_text(json.dumps(valid_report()))
            report, status = host.normalize_peer_report_from_artifacts(peer, run_dir, request())
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
            report, status = host.normalize_peer_report_from_artifacts(
                peer,
                run_dir,
                request(),
            )
        self.assertEqual(status, "invalid_json")
        self.assertEqual(report["status"], "peer_failed")
        self.assertIn("16777216-byte limit", report["error"]["message"])

    def test_host_artifact_normalization_requires_direct_v2_report(self):
        host = load_host()
        peer = load_peer()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "peer-report.json").write_text(json.dumps({"structured_output": valid_report()}))
            report, status = host.normalize_peer_report_from_artifacts(peer, run_dir, request())
            metadata = json.loads((run_dir / "peer-normalization.json").read_text())
        self.assertEqual(status, "noncanonical_output")
        self.assertEqual(report["error"]["kind"], "noncanonical_output")
        self.assertEqual(metadata["validation_status"], "noncanonical_output")

    def test_doctor_reports_invalid_state_and_returns_failure(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root, run_root = Path(tmp), Path(tmp) / "runs"
            run_root.mkdir()
            (run_root / "state.json").write_text('{"version":1,"jobs":[]}')
            args = host.build_parser().parse_args([
                "doctor", "--run-root", str(run_root), "--repo-root", str(root),
            ])
            output = io.StringIO()
            with mock.patch.object(host.shutil, "which", return_value=None), redirect_stdout(output):
                self.assertEqual(host.doctor(args), 1)
            report = json.loads(output.getvalue())
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"]["state_file"]["ok"])
        self.assertIn("exactly schema_version and jobs", report["checks"]["state_file"]["error"])

    def test_state_and_snapshots_are_v2(self):
        state = load_state()
        snapshot = load_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state.json").write_text('{"version": 1, "jobs": [{"id": "old"}]}')
            with self.assertRaises(ValueError):
                state.load_state(root)
            for malformed in (
                {"schema_version": "2.0", "jobs": [{"id": "incomplete"}]},
                {"schema_version": "2.0", "jobs": [{**valid_state_job(), "legacy": True}]},
                {"schema_version": "2.0", "jobs": [{**valid_state_job(), "pid": "1"}]},
                {"schema_version": "2.0", "jobs": [{**valid_state_job(), "status": "running", "phase": "done"}]},
                {"schema_version": "2.0", "jobs": [valid_state_job(), valid_state_job()]},
            ):
                (root / "state.json").write_text(json.dumps(malformed))
                with self.assertRaises(ValueError):
                    state.load_state(root)
            (root / "state.json").unlink()
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: state.upsert_job(root, valid_state_job_patch("job-" + str(i))), range(8)))
            self.assertEqual(state.load_state(root)["schema_version"], "2.0")
            (root / "a.txt").write_text("a")
            self.assertEqual(snapshot.mutation_snapshot(root)["schema_version"], "2.0")
            self.assertEqual(snapshot.SNAPSHOT_HEADER, "agent_collab_workspace_snapshot_v2")

    def test_host_independently_records_workspace_mutation(self):
        host = load_host()
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            run_dir = root / "runs" / request()["run_id"]
            run_dir.mkdir(parents=True)
            changed = root / "tracked.txt"
            changed.write_text("before", encoding="utf-8")
            host.run_snapshot(root, run_dir / "before.snapshot", ignored_paths=[run_dir])
            changed.write_text("after", encoding="utf-8")
            diagnostic = host.record_host_workspace_mutation(root, run_dir, request())

        self.assertIsNotNone(diagnostic)
        self.assertIs(diagnostic["changed"], True)
        self.assertEqual(diagnostic["source"], "host_snapshot")
        self.assertIn("tracked.txt", diagnostic["changed_paths"])


class PackageTests(TestCase):
    def test_only_current_marketplace_package_layout_exists(self):
        self.assertTrue(CODEX_PLUGIN_ROOT.is_dir())
        self.assertTrue(CLAUDE_PLUGIN_ROOT.is_dir())
        self.assertEqual(CODEX_PLUGIN_ROOT, CLAUDE_PLUGIN_ROOT)
        self.assertFalse((REPO_ROOT / "plugins" / "claude").exists())
        for removed in ("codex-plugin", "claude-plugin", "codex-skill"):
            self.assertFalse((REPO_ROOT / removed).exists())
        for removed in (
            "install-codex-plugin.sh", "install-codex-skill.sh",
            "install-claude-plugin.sh", "sync-codex-skill.sh",
        ):
            self.assertFalse((REPO_ROOT / "scripts" / removed).exists())

    def test_release_versions_match(self):
        version = (REPO_ROOT / "VERSION").read_text().strip()
        self.assertEqual(version, "1.0.0")
        for manifest in (
            CODEX_PLUGIN_ROOT / ".codex-plugin" / "plugin.json",
            CLAUDE_PLUGIN_ROOT / ".claude-plugin" / "plugin.json",
        ):
            self.assertEqual(json.loads(manifest.read_text())["version"], version)
        self.assertIn("## 1.0.0", (REPO_ROOT / "CHANGELOG.md").read_text())

    def test_marketplaces_are_native_and_do_not_duplicate_version(self):
        codex = json.loads((REPO_ROOT / ".agents/plugins/marketplace.json").read_text())
        centry = codex["plugins"][0]
        self.assertEqual(centry["source"], {"source": "local", "path": "./plugins/agent-collab"})
        self.assertEqual(centry["policy"], {"installation": "AVAILABLE", "authentication": "ON_INSTALL"})
        self.assertEqual(centry["category"], "Productivity")
        self.assertNotIn("version", centry)
        claude = json.loads((REPO_ROOT / ".claude-plugin/marketplace.json").read_text())
        aentry = claude["plugins"][0]
        self.assertEqual(aentry["source"], "./plugins/agent-collab")
        self.assertNotIn("version", aentry)

    def test_packages_match_canonical_runtime_and_sync_check(self):
        self.assertEqual(CODEX_SKILL_ROOT, CLAUDE_SKILL_ROOT)
        for dirname in ("scripts", "references", "schemas"):
            comparison = filecmp.dircmp(RUNTIME_ROOT / dirname, CODEX_SKILL_ROOT / dirname)
            self.assertEqual(comparison.left_only, [])
            self.assertEqual(comparison.right_only, [])
            self.assertEqual(comparison.diff_files, [])
        completed = subprocess.run(
            [str(REPO_ROOT / "scripts/sync-packages.sh"), "--check"],
            cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_sync_rejects_and_removes_unexpected_packaged_payload(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            for relative in (".agents", ".claude-plugin"):
                shutil.copytree(REPO_ROOT / relative, root / relative)
            shutil.copytree(RUNTIME_ROOT, root / "tools" / "agent-collab")
            shutil.copytree(CODEX_PLUGIN_ROOT, root / "plugins" / "agent-collab")
            (root / "scripts").mkdir()
            shutil.copy2(REPO_ROOT / "scripts" / "sync-packages.sh", root / "scripts")
            shutil.copy2(REPO_ROOT / "VERSION", root)
            stale = root / "plugins" / "agent-collab" / "skills" / "agent-collab" / "legacy.txt"
            stale.write_text("legacy compatibility payload", encoding="utf-8")

            check = subprocess.run(
                [str(root / "scripts" / "sync-packages.sh"), "--check"],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(check.returncode, 0)
            self.assertIn("unexpected packaged skill entry", check.stderr)

            sync = subprocess.run(
                [str(root / "scripts" / "sync-packages.sh")],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
            self.assertFalse(stale.exists())

    def test_skills_expose_user_overrides_and_pin_current_defaults(self):
        codex = (CODEX_SKILL_ROOT / "SKILL.md").read_text()
        frontmatter = codex.split("---", 2)[1]
        self.assertEqual(
            [line.split(":", 1)[0] for line in frontmatter.splitlines() if ":" in line],
            ["name", "description"],
        )
        for token in ("--peer-model", "--peer-effort", "claude-fable-5", '"schema_version": "2.0"'):
            self.assertIn(token, codex)
        self.assertNotIn("plugins/cache", codex)
        self.assertEqual(CODEX_SKILL_ROOT, CLAUDE_SKILL_ROOT)
        self.assertNotIn("allowed-tools:", frontmatter)
        for token in ("gpt-5.6-sol", "claude-opus-4-8", "--peer-model", "--peer-effort"):
            self.assertIn(token, codex)

    def test_helpers_pin_opus_max_inherit_all_tools_and_forbid_recursion(self):
        for path in (CLAUDE_PLUGIN_ROOT / "agents").glob("*.md"):
            text = path.read_text()
            frontmatter = text.split("---", 2)[1]
            self.assertIn("model: claude-opus-4-8", frontmatter, path.name)
            self.assertIn("effort: max", frontmatter, path.name)
            self.assertNotIn("tools:", frontmatter, path.name)
            self.assertNotIn("disallowedTools:", frontmatter, path.name)
            self.assertIn("Do not invoke Agent Collab", text, path.name)
            self.assertIn("hard recursion boundary", text, path.name)

    def test_agents_claude_and_readme_document_current_contract(self):
        agents = (REPO_ROOT / "AGENTS.md").read_text()
        for token in ("gpt-5.6-sol", "claude-opus-4-8", "schema 2.0", "active-workflow guard"):
            self.assertIn(token, agents)
        self.assertEqual((REPO_ROOT / "CLAUDE.md").read_text(), "@AGENTS.md\n")
        readme = (REPO_ROOT / "README.md").read_text()
        for token in (
            "gpt-5.6-sol", "claude-opus-4-8", "claude-fable-5", "--peer-model",
            "--peer-effort", "Codex CLI 0.144.5", "Claude Code 2.1.214",
            "schema 2.0", "danger-full-access", "bypassPermissions",
        ):
            self.assertIn(token, readme)
        for removed in (
            "install-codex-plugin.sh", "install-codex-skill.sh",
            "install-claude-plugin.sh", "gpt-5.5", "AGENT_COLLAB_WEB_RESEARCH",
        ):
            self.assertNotIn(removed, readme)

    def test_no_duplicate_prompt_or_old_runtime_flags(self):
        for root in (RUNTIME_ROOT, CODEX_SKILL_ROOT, CLAUDE_SKILL_ROOT):
            self.assertFalse((root / "references/peer-prompt-blocks.md").exists())
        source = "\n".join(path.read_text() for path in (RUNTIME_ROOT / "scripts").glob("*.py"))
        for removed in (
            "gpt-5.5", "AGENT_COLLAB_WEB_RESEARCH",
            "CLAUDE_AGENT_COLLAB_MAX_BUDGET_USD",
            "--dangerously-skip-permissions",
            "--dangerously-bypass-approvals-and-sandbox", "MultiEdit",
            "codex-plugin", "claude-plugin",
        ):
            self.assertNotIn(removed, source)

    def test_json_schemas_validate_representatives(self):
        for path in [path for path in RUNTIME_ROOT.rglob("*.json") if "runs" not in path.parts] + [
            REPO_ROOT / ".agents/plugins/marketplace.json",
            REPO_ROOT / ".claude-plugin/marketplace.json",
            CODEX_PLUGIN_ROOT / ".codex-plugin/plugin.json",
            CLAUDE_PLUGIN_ROOT / ".claude-plugin/plugin.json",
        ]:
            json.loads(path.read_text())
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema unavailable")
        jsonschema.validate(request(), json.loads((RUNTIME_ROOT / "schemas/host-request.schema.json").read_text()))
        jsonschema.validate(
            availability_attestation("claude", "claude-opus-4-8", "max"),
            json.loads((RUNTIME_ROOT / "schemas/availability-attestation.schema.json").read_text()),
        )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                request(peer_effort=""),
                json.loads((RUNTIME_ROOT / "schemas/host-request.schema.json").read_text()),
            )
        peer_schema = json.loads(PEER_SCHEMA.read_text())
        jsonschema.validate(valid_report(), peer_schema)
        peer = load_peer()
        for invalid_report in (
            valid_report(error={"kind": "x", "message": "x", "details": None}),
            valid_report(status="peer_failed", verdict="blocked", error=None),
        ):
            jsonschema.validate(invalid_report, peer_schema)
            with self.assertRaises(peer.PeerReportValidationError):
                peer.validate_peer_report(invalid_report)
        jsonschema.validate(
            valid_provider_process(),
            json.loads((RUNTIME_ROOT / "schemas/provider-process.schema.json").read_text()),
        )
        host = load_host()
        payload = {"schema_version": "2.0", "updated_at": "2026-07-18T00:00:00Z", "settings": complete_settings(host)}
        settings_schema = json.loads((RUNTIME_ROOT / "schemas/settings.schema.json").read_text())
        jsonschema.validate(payload, settings_schema)
        for invalid_settings in (
            complete_settings(host, agent_timeout_seconds="999999"),
            complete_settings(host, claude_model="claude-fable-5"),
            complete_settings(host, codex_model="   "),
        ):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({**payload, "settings": invalid_settings}, settings_schema)
            with self.assertRaises(ValueError):
                host.validate_v2_settings_types(invalid_settings, "test settings")
        provider_schema = json.loads(
            (RUNTIME_ROOT / "schemas/provider-process.schema.json").read_text()
        )
        invalid_cleanup = valid_provider_process(
            status="cleanup_failed",
            pid=None,
            pgid=None,
            process_identity=None,
            cleanup_outcome=None,
            completed_at=None,
        )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(invalid_cleanup, provider_schema)
        with self.assertRaises(host.ArtifactValidationError):
            host.validate_provider_process(invalid_cleanup, request())
        jsonschema.validate(
            {"schema_version": "2.0", "jobs": [valid_state_job()]},
            json.loads((RUNTIME_ROOT / "schemas/state.schema.json").read_text()),
        )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {"schema_version": "2.0", "jobs": [{**valid_state_job(), "legacy": True}]},
                json.loads((RUNTIME_ROOT / "schemas/state.schema.json").read_text()),
            )
        jsonschema.validate(
            valid_host_synthesis(),
            json.loads((RUNTIME_ROOT / "schemas/host-synthesis.schema.json").read_text()),
        )
        jsonschema.validate(
            {
                "schema_version": "2.0", "run_id": request()["run_id"],
                "reports": [{"name": "reviewer", "summary": "ok", "claims": []}],
            },
            json.loads((RUNTIME_ROOT / "schemas/helper-reports.schema.json").read_text()),
        )

    def test_official_claude_plugin_validator_and_isolated_install(self):
        if shutil.which("claude") is None:
            self.skipTest("Claude Code CLI unavailable")
        validated = subprocess.run(
            ["claude", "plugin", "validate", ".", "--strict"], cwd=REPO_ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["CLAUDE_CONFIG_DIR"] = tmp
            added = subprocess.run(
                ["claude", "plugin", "marketplace", "add", "./"], cwd=REPO_ROOT, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
            installed = subprocess.run(
                [
                    "claude", "plugin", "install", "agent-collab@agent-collab",
                    "--scope", "user",
                ], cwd=REPO_ROOT, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)

    def test_maintenance_files_and_changelog_policy(self):
        self.assertTrue((REPO_ROOT / "scripts/sync-packages.sh").stat().st_mode & stat.S_IXUSR)
        self.assertFalse(any(CODEX_SKILL_ROOT.glob("CHANGELOG*")))
        self.assertFalse(any(CLAUDE_SKILL_ROOT.glob("CHANGELOG*")))


if __name__ == "__main__":
    import unittest
    unittest.main()
