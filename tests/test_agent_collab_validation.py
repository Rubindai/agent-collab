"""Acceptance boundaries retained by shared artifact validation."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, mock

import agent_collab_test_support as fixtures

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class ValidationBoundaryTests(TestCase):
    def test_malformed_request_enums_produce_structured_failures(self):
        peer = fixtures.load_peer()
        for key in ('origin', 'host', 'peer', 'mode'):
            for value in ([], {}, None, True, 1):
                with self.subTest(key=key, value=value):
                    request = fixtures.request(**{key: value})
                    with self.assertRaises(peer.RequestValidationError):
                        peer.validate_request(request)
                    with mock.patch.object(peer, 'invoke_peer') as invoke:
                        report = peer.run_request(request, Path.cwd())
                    invoke.assert_not_called()
                    peer.validate_peer_report(report)
                    self.assertEqual(report['error']['kind'], 'invalid_request')

    def test_shared_checks_keep_types_keys_and_digest_boundaries(self):
        host = fixtures.load_host()
        request = fixtures.request()
        valid = fixtures.valid_host_synthesis()
        host.ARTIFACTS.validate_host_synthesis(valid, request)
        invalid_fields = {
            'final_answer_ready': [1, 'true', False, None],
            'summary': ['', ' ', [], None],
            'claim_matrix_sha256': ['a' * 64 + '\n', 'A' * 64, '', None],
            'claims': [None, {}, [{'claim': 'x', 'status': [], 'evidence': 'y'}],
                       [{'claim': 'x', 'status': 'confirmed', 'evidence': ['y']}]],
        }
        for key, values in invalid_fields.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    report = {**valid, key: value}
                    with self.assertRaises(host.ARTIFACTS.ArtifactValidationError):
                        host.ARTIFACTS.validate_host_synthesis(report, request)
        for report in ({**valid, 'extra': True}, {k: v for k, v in valid.items() if k != 'summary'}):
            with self.assertRaises(host.ARTIFACTS.ArtifactValidationError):
                host.ARTIFACTS.validate_host_synthesis(report, request)

    def test_request_numeric_bounds_and_attestation_relations_remain_strict(self):
        peer = fixtures.load_peer()
        for key, values in {
            'max_local_subagents': [True, -1, 65, '8', 8.0],
            'peer_timeout_seconds': [True, 59, 86401, float('nan'), float('inf'), 10**400],
            'availability_attestation': [[], {}, None],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(peer.RequestValidationError):
                    peer.validate_request({**fixtures.request(), key: value})
        request = fixtures.request()
        request['availability_attestation']['requested_model'] = 'different-model'
        with self.assertRaises(peer.RequestValidationError):
            peer.validate_request(request)

    def test_entrypoints_resolve_trusted_siblings_with_safe_path_enabled(self):
        scripts = Path(fixtures.PEER_RUNTIME).parent
        with tempfile.TemporaryDirectory() as temporary:
            for name in ('host', 'peer', 'safety', 'claude_telemetry', 'provider_schema'):
                with self.subTest(name=name):
                    result = subprocess.run(
                        [sys.executable, '-B', '-P', str(scripts / f'{name}.py'), '--help'],
                        cwd=temporary, env={**os.environ, 'PYTHONSAFEPATH': '1'},
                        capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('usage:', result.stdout)

    def test_invalid_request_cli_never_emits_a_traceback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'request.json'
            path.write_text(json.dumps(fixtures.request(origin=[])))
            result = subprocess.run([sys.executable, str(fixtures.PEER_RUNTIME), str(path)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['error']['kind'], 'invalid_request')
            self.assertNotIn('Traceback', result.stderr)
