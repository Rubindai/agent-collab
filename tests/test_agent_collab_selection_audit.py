import json
import sys
import fcntl
import stat
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_agent_collab_runtime as fixtures

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class SelectionAuditTests(TestCase):
    def test_claude_conflicting_envelope_effort_and_haiku_substitution_are_rejected(self):
        peer = fixtures.load_peer()
        payload = {'effort': 'xhigh', 'modelUsage': {'claude-opus-5-5': {'effortLevel': 'low'}}}
        self.assertIsNotNone(peer.claude_selection_mismatch_details(json.dumps(payload), 'claude-opus-5-5', 'xhigh'))
        payload = {'modelUsage': {'claude-haiku-4-5': {}, 'claude-haiku-9-9': {}}}
        self.assertIsNotNone(peer.claude_selection_mismatch_details(json.dumps(payload), 'claude-haiku-4-5', 'xhigh'))
        payload = {'modelUsage': {'claude-opus-5-5': {}, 'claude-haiku-4-5': {}}}
        self.assertIsNone(peer.claude_selection_mismatch_details(json.dumps(payload), 'claude-opus-5-5', 'xhigh'))
        availability = fixtures.load_availability()
        for model, envelope in (
            ('claude-opus-5-5', {'effort': 'xhigh', 'modelUsage': {'claude-opus-5-5': {'effortLevel': 'low'}}}),
            ('claude-haiku-4-5', {'modelUsage': {'claude-haiku-4-5': {}, 'claude-haiku-9-9': {}}}),
        ):
            original = fixtures.AvailabilityV2Tests().claude_runner(model, 'xhigh')
            def runner(*args, **kwargs):
                result = original(*args, **kwargs)
                result.stdout = json.dumps({'type': 'result', **envelope})
                return result
            with self.subTest(model=model):
                result = availability.check('claude', model, 'xhigh', '2.1.302', fixtures.REPO_ROOT, 3, runner=runner, env={})
                self.assertEqual(result['status'], 'unavailable')

    def test_codex_effort_config_is_toml_escaped(self):
        peer = fixtures.load_peer()
        effort = 'xhigh"\nmodel="other'
        req = fixtures.request(host='claude', peer='codex', peer_model='gpt-6-astra', peer_effort=effort)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = peer.build_peer_command(req, 'review', root, peer.default_schema_path(), root / 'output.json', {}, 'filesystem')
        config = next(value for value in command.args if value.startswith('model_reasoning_effort='))
        self.assertEqual(json.loads(config.split('=', 1)[1]), effort)
        self.assertNotIn('\n', config)

    def test_failed_stop_capture_is_not_hidden_by_a_previous_good_receipt(self):
        peer = fixtures.load_peer()
        telemetry = peer.CLAUDE_TELEMETRY
        report = json.dumps({'type': 'result', 'structured_output': fixtures.valid_report(),
                             'modelUsage': {'claude-opus-5-5': {}}})
        def run(args, **kwargs):
            settings = json.loads(args[args.index('--settings') + 1])
            words = settings['hooks']['Stop'][0]['hooks'][0]['args']
            path = Path(words[words.index('--output') + 1])
            telemetry.capture(path, fixtures.request()['run_id'], 'xhigh', json.dumps(
                {'hook_event_name': 'Stop', 'effort': {'level': 'xhigh'}}))
            with (mock.patch.object(fcntl, 'flock', side_effect=BlockingIOError),
                  mock.patch.object(telemetry.time, 'monotonic', side_effect=[0, 3]),
                  self.assertRaises(TimeoutError)):
                telemetry.capture(path, fixtures.request()['run_id'], 'xhigh', json.dumps(
                    {'hook_event_name': 'Stop', 'effort': {'level': 'low'}}))
            self.assertIsNone(telemetry.mismatch_details(telemetry.STRICT_JSON.load(path), fixtures.request()['run_id'], 'xhigh'))
            self.assertIsNotNone(telemetry.pending_capture_details(path))
            return SimpleNamespace(returncode=0, stdout=report, stderr='')
        with (tempfile.TemporaryDirectory() as tmp,
              mock.patch.object(peer, 'git_mutation_snapshot', return_value={'mode': 'git', 'same': True}),
              mock.patch.object(peer.shutil, 'which', return_value='/usr/bin/claude'),
              mock.patch.object(peer, 'run_peer_command', side_effect=run)):
            result = peer.run_request(fixtures.request(), Path(tmp), raw_output_path=Path(tmp) / 'raw.json')
        self.assertEqual(result['error']['kind'], 'effort_unverified')
        self.assertIn('capture failed', result['error']['message'])

    def test_codex_reroute_is_rejected_regardless_of_surrounding_events(self):
        peer = fixtures.load_peer()
        message = {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "done"}}
        reroute = {"type": "item.completed", "item": {
            "id": "item_2", "type": "error",
            "message": "model rerouted: gpt-6-astra -> gpt-6-mini (HighRiskCyberActivity)",
        }}
        for events in ((message, reroute), (reroute, message)):
            with self.subTest(events=events):
                self.assertIsNotNone(peer.codex_selection_mismatch_details(
                    "\n".join(map(json.dumps, events)), "gpt-6-astra", "xhigh"
                ))
        self.assertIsNone(peer.codex_selection_mismatch_details(
            "\n".join(map(json.dumps, (message, message))), "gpt-6-astra", "xhigh"
        ))

    def test_actual_claude_run_requires_valid_stop_evidence(self):
        peer = fixtures.load_peer()
        report = json.dumps({"type": "result", "structured_output": fixtures.valid_report(),
                             "modelUsage": {"claude-opus-5-5": {}}, "effort": "xhigh"})
        for events, expected_status in (([], "peer_failed"), ([None], "peer_failed"),
                                        (["low", "xhigh"], "peer_failed"), (["xhigh"], "ok")):
            with self.subTest(events=events), tempfile.TemporaryDirectory() as tmp:
                def run(args, **kwargs):
                    settings = json.loads(args[args.index("--settings") + 1])
                    words = settings["hooks"]["Stop"][0]["hooks"][0]["args"]
                    output = Path(words[words.index("--output") + 1])
                    for effort in events:
                        payload = {"hook_event_name": "Stop"}
                        if effort is not None:
                            payload["effort"] = {"level": effort}
                        peer.CLAUDE_TELEMETRY.capture(output, fixtures.request()["run_id"], "xhigh", json.dumps(payload))
                    return SimpleNamespace(returncode=0, stdout=report, stderr="")
                with (
                    mock.patch.object(peer, "git_mutation_snapshot", return_value={"mode": "git", "same": True}),
                    mock.patch.object(peer.shutil, "which", return_value="/usr/bin/claude"),
                    mock.patch.object(peer, "run_peer_command", side_effect=run),
                ):
                    raw = Path(tmp) / "peer.raw.json"
                    result = peer.run_request(fixtures.request(), Path(tmp), raw_output_path=raw)
                self.assertEqual(result["status"], expected_status)
                if expected_status == "peer_failed":
                    self.assertEqual(result["error"]["kind"], "effort_unverified")
                receipt_path = raw.with_name("claude-effort.json")
                self.assertEqual(stat.S_IMODE(receipt_path.stat().st_mode), 0o600)
                self.assertEqual(len(json.loads(receipt_path.read_text())["observations"]), len(events))

    def test_stop_capture_is_private_bounded_and_does_not_erase_conflicts(self):
        telemetry = fixtures.load_peer().CLAUDE_TELEMETRY
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "effort.json"
            telemetry.STRICT_JSON.write(path, telemetry.new_receipt("run", "xhigh"))
            with ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(lambda level: telemetry.capture(path, "run", "xhigh", json.dumps(
                    {"hook_event_name": "Stop", "effort": {"level": level}}
                )), ["xhigh", "low", "xhigh", "xhigh"]))
            receipt = telemetry.STRICT_JSON.load(path)
            self.assertEqual(len(receipt["observations"]), 4)
            self.assertIsNotNone(telemetry.mismatch_details(receipt, "run", "xhigh"))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.with_suffix(".lock").stat().st_mode), 0o600)
            with mock.patch.object(telemetry, "MAX_OBSERVATIONS", 4):
                self.assertFalse(telemetry.capture(path, "run", "xhigh", '{}'))
            receipt = telemetry.STRICT_JSON.load(path)
            self.assertEqual(len(receipt["observations"]), 4)
            self.assertIn("limit exceeded", receipt["observations"][-1]["error"])
            with self.assertRaises(ValueError):
                telemetry.validate_receipt({**receipt, "schema_version": "1.0"}, "run", "xhigh")
            with self.assertRaises(ValueError):
                telemetry.validate_receipt(receipt, "other-run", "xhigh")

    def test_explicit_ultracode_fails_before_provider_or_run_artifacts(self):
        peer = fixtures.load_peer()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(peer, "run_peer_command") as run:
            result = peer.run_request(fixtures.request(peer_effort="ultracode"), Path(tmp))
            self.assertEqual(result["error"]["kind"], "helper_policy_unavailable")
            run.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = host.build_parser().parse_args([
                "start", "--host", "codex", "--target", "probe", "--brief", "Review",
                "--peer-effort", "ultracode", "--repo-root", str(root),
                "--run-root", str(root / "runs"),
            ])
            with (
                mock.patch.object(host.CONFIG, "resolve_settings", return_value={"settings": fixtures.complete_settings(host), "errors": []}),
                mock.patch.object(host.CONFIG, "require_valid_settings"),
                mock.patch.object(host.CONFIG, "require_peer_cli_version") as cli,
                mock.patch.object(host.REGISTRY, "acquire_workspace_guard") as acquire,
                self.assertRaises(SystemExit) as error,
            ):
                host.start(args)
            evidence = json.loads(str(error.exception))
            self.assertEqual(evidence["status"], "helper_policy_unavailable")
            self.assertEqual(evidence["requested_effort"], "ultracode")
            cli.assert_not_called()
            acquire.assert_not_called()
            self.assertFalse((root / "runs").exists())
