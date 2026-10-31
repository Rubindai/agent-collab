import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

from agent_collab_test_support import (
    CLAUDE_EFFORT_KEYS,
    REPO_ROOT,
    availability_attestation,
    claude_probe_runner,
    load_availability,
    setUpModule,
    tearDownModule,
)


class AvailabilityTests(TestCase):
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



    def test_opus_55_availability_requires_exact_model_and_xhigh(self):
        availability = load_availability()
        for observed_model, observed_effort, expected in (
            ("claude-opus-5-5", "xhigh", "available"),
            ("claude-opus-5", "xhigh", "unavailable"),
            ("claude-opus-5-5", "high", "unavailable"),
        ):
            with self.subTest(model=observed_model, effort=observed_effort):
                runner = mock.Mock(wraps=claude_probe_runner(
                    "claude-opus-5-5", observed_effort, usage_model=observed_model
                ))
                result = availability.check(
                    "claude", "claude-opus-5-5", "xhigh", "2.1.296", REPO_ROOT, 5,
                    runner=runner, env={},
                )
                self.assertEqual(result["status"], expected)
                self.assertEqual(result["requested_model"], "claude-opus-5-5")
                self.assertEqual(result["requested_effort"], "xhigh")
                runner.assert_called_once()  # A mismatch must not trigger a fallback.

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

    def test_codex_catalog_tolerates_forward_metadata_and_bad_unrelated_rows(self):
        availability = load_availability()
        payload = json.loads(self.codex_catalog())
        payload["schema_version"] = "future"
        payload["models"].insert(0, {"slug": "unrelated-broken", "future": True})
        models = availability.parse_codex_model_catalog(
            json.dumps(payload),
            requested_model="gpt-test",
        )
        self.assertEqual(
            models,
            [{"model": "gpt-test", "supported_efforts": ["minimal", "high"]}],
        )
        duplicate = json.loads(self.codex_catalog())
        duplicate["models"].append(duplicate["models"][0])
        with self.assertRaisesRegex(ValueError, "duplicate requested model"):
            availability.parse_codex_model_catalog(
                json.dumps(duplicate),
                requested_model="gpt-test",
            )

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

        env = {key: "forced" for key in CLAUDE_EFFORT_KEYS}
        result = availability.check(
            "claude", "claude-fable-5", "max", "2.1.257", REPO_ROOT, 7,
            runner=claude_probe_runner("claude-fable-5", "max", captured=captured), env=env,
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
            "mcp__*,Agent,Workflow",
        )
        self.assertEqual(captured["command"][captured["command"].index("--max-turns") + 1], "1")
        self.assertEqual(captured["command"][-2:], ["--tools", ""])
        self.assertEqual(captured["kwargs"]["input"], "Reply with exactly AVAILABLE. Do not use tools.")
        self.assertNotEqual(Path(captured["kwargs"]["cwd"]).resolve(), REPO_ROOT.resolve())
        probe_settings = json.loads(captured["command"][captured["command"].index("--settings") + 1])
        self.assertEqual(probe_settings["availableModels"], ["claude-fable-5"])
        self.assertEqual(probe_settings["fallbackModel"], [])
        self.assertIs(probe_settings["switchModelsOnFlag"], False)
        self.assertIs(probe_settings["disableAllHooks"], False)
        self.assertEqual(set(probe_settings["hooks"]), {"Stop", "PreToolUse"})
        self.assertEqual(captured["kwargs"]["timeout"], 7)
        for key, value in probe_settings["env"].items():
            self.assertEqual(captured["kwargs"]["env"][key], value)
        self.assertEqual(probe_settings["env"]["CLAUDE_CODE_DISABLE_THINKING"], "0")
        self.assertEqual(probe_settings["env"]["MAX_THINKING_TOKENS"], "")
        self.assertEqual(probe_settings["hooks"]["PreToolUse"][0]["hooks"][0]["args"][-1], "0")
        self.assertEqual(captured["kwargs"]["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "max")

        result = availability.check(
            "claude", "claude-fable-5", "max", "2.1.257", REPO_ROOT, 7,
            runner=claude_probe_runner(
                "claude-fable-5", "max", usage_model="claude-opus-5"
            ), env={},
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("claude-opus-5", result["details"])

        result = availability.check(
            "claude", "claude-fable-5", "max", "2.1.257", REPO_ROOT, 7,
            runner=claude_probe_runner("claude-fable-5", "high"), env={},
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("observed_effort=high", result["details"])

        result = availability.check(
            "claude", "claude-opus-5", "max", "2.1.257", REPO_ROOT, 7,
            runner=claude_probe_runner(
                "claude-opus-5", "max", usage_model="claude-haiku-4-5-20251001"
            ), env={},
        )
        self.assertEqual(result["status"], "unavailable")

    def test_claude_known_effort_matrix_and_hook_observation_are_fail_closed(self):
        availability = load_availability()
        captured = {}
        result = availability.check(
            "claude", "claude-opus-5", "ultracode", "2.1.257", REPO_ROOT, 7,
            runner=claude_probe_runner(
                "claude-opus-5", "xhigh", captured=captured
            ), env={},
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["requested_effort"], "ultracode")
        self.assertIn("effective provider effort xhigh", result["details"])
        self.assertEqual(result["supported_efforts"], ["ultracode"])
        self.assertEqual(captured["command"][captured["command"].index("--effort") + 1], "ultracode")
        self.assertEqual(captured["kwargs"]["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "xhigh")

        result = availability.check(
            "claude", "claude-fable-5", "xhigh", "2.1.257", REPO_ROOT, 7,
            runner=claude_probe_runner("claude-fable-5", "xhigh"), env={},
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
            "claude", "claude-opus-5", "max", "2.1.257", REPO_ROOT, 3,
            runner=timed_out, env={},
        )
        self.assertEqual(result["status"], "unknown")
        missing = lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr="")
        result = availability.check(
            "claude", "claude-opus-5", "max", "2.1.257", REPO_ROOT, 3,
            runner=missing, env={},
        )
        self.assertEqual(result["status"], "unknown")

        rejected = lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="effort is not supported for this model"
        )
        result = availability.check(
            "claude", "claude-fable-5", "max", "2.1.257", REPO_ROOT, 3,
            runner=rejected, env={},
        )
        self.assertEqual(result["status"], "unavailable")

    def test_claude_probe_reports_provider_quota_and_access_failures(self):
        availability = load_availability()

        def provider_error(api_status, message):
            return lambda *args, **kwargs: SimpleNamespace(
                returncode=1,
                stdout=json.dumps(
                    {
                        "type": "result",
                        "is_error": True,
                        "terminal_reason": "api_error",
                        "api_error_status": api_status,
                        "result": message,
                        "modelUsage": {},
                    }
                ),
                stderr="",
            )

        quota = availability.check(
            "claude", "claude-opus-5", "max", "2.1.257", REPO_ROOT, 3,
            runner=provider_error(429, "You've hit your weekly limit; resets soon"), env={},
        )
        self.assertEqual(quota["status"], "unavailable")
        self.assertIn("API status 429", quota["details"])
        self.assertIn("weekly limit", quota["details"])

        denied = availability.check(
            "claude", "claude-opus-5", "max", "2.1.257", REPO_ROOT, 3,
            runner=provider_error(403, "Model access denied"), env={},
        )
        self.assertEqual(denied["status"], "unavailable")
        self.assertIn("API status 403", denied["details"])

        provider_fault = availability.check(
            "claude", "claude-opus-5", "max", "2.1.257", REPO_ROOT, 3,
            runner=provider_error(500, "Temporary provider fault"), env={},
        )
        self.assertEqual(provider_fault["status"], "unknown")
        self.assertIn("API status 500", provider_fault["details"])

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

