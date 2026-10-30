import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_agent_collab_runtime as fixtures

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class FinalAuditTests(TestCase):
    def test_parallel_agent_calls_share_one_finite_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'agent-calls.count'
            command = [sys.executable, str(fixtures.RUNTIME_ROOT / 'scripts/safety.py'),
                       'claude-agent-guard', '--counter', str(path), '--limit', '5']
            with ThreadPoolExecutor(max_workers=10) as executor:
                codes = list(executor.map(lambda _: subprocess.run(
                    command, capture_output=True, text=True, check=False).returncode, range(20)))
            self.assertEqual(codes.count(0), 5)
            self.assertEqual(codes.count(2), 15)
            self.assertEqual(path.read_text(), '5')
            self.assertEqual(stat.S_IMODE(path.with_name(path.name + '.lock').stat().st_mode), 0o600)

    def test_provider_prefixed_fable_cannot_be_a_default(self):
        host = fixtures.load_host()
        for model in ('us.anthropic.claude-fable-5-v1:0', 'anthropic/claude-fable-5', 'fable-5'):
            with self.subTest(model=model):
                for source in ('local', 'global'):
                    with self.assertRaisesRegex(ValueError, 'cannot persist Fable'):
                        host.CONFIG.validate_v2_settings_types(fixtures.complete_settings(host, claude_model=model), source)
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    with (mock.patch.object(host.CONFIG, 'default_global_settings_path', return_value=root / 'g'),
                          mock.patch.object(host.CONFIG, 'default_local_settings_path', return_value=root / 'l')):
                        resolved = host.CONFIG.resolve_settings(root, {'CLAUDE_AGENT_COLLAB_MODEL': model})
                self.assertIn('cannot persist Fable', resolved['environment_errors'][0])

    def test_agent_counter_rejects_noncanonical_counts(self):
        safety = fixtures.load_peer().SAFETY
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'counter'
            for raw in ('-7', '+1', '01', '1\n', ' ', '', '9' * 100):
                path.write_text(raw)
                with self.subTest(raw=raw):
                    self.assertEqual(safety._run_claude_agent_guard(path, 8), 2)
                    self.assertEqual(path.read_text(), raw)
            path.unlink()
            self.assertEqual(safety._run_claude_agent_guard(path, 8), 0)
            self.assertEqual(path.read_text(), '1')

    def test_git_control_change_does_not_label_unchanged_dirty_files(self):
        snapshot = fixtures.load_snapshot()
        before = {'mode': 'git', 'digest': 'a', 'sections': {
            'status': ' M existing.txt\n', 'status_z': ' M existing.txt\0',
            'diff': 'existing difference', 'git_control': {'worktree/index': 'before'},
        }}
        after = {**before, 'digest': 'b', 'sections': {
            **before['sections'], 'git_control': {'worktree/index': 'after'},
        }}
        self.assertEqual(snapshot.diff_snapshots(before, after)['changed_paths'], ['<git-control>/worktree/index'])

    def test_cancel_evidence_hashes_require_strings(self):
        host = fixtures.load_host()
        request = fixtures.request()
        with tempfile.TemporaryDirectory() as tmp:
            report = {
                'schema_version': '2.0', 'run_id': request['run_id'],
                'status': 'cancelled', 'outcome': 'cancelled_quiescent',
                'forced': False, 'reason': 'peer_not_running', 'elapsed_seconds': 61,
                'minimum_wait_seconds': 60, 'before_minimum_wait': False,
                'peer_was_alive': False, 'process_cleanup': {
                    'quiescent': True,
                    'provider': {'outcome': 'already_quiescent', 'quiescent': True},
                    'wrapper': {'outcome': 'not_running', 'quiescent': True, 'pgid': 43210},
                },
                'preserved_artifacts': dict.fromkeys(host.ARTIFACTS.CANCEL_PRESERVED_ARTIFACTS),
                'workspace_mutation': None,
                'workspace_mutation_sha256': host.ARTIFACTS.workspace_mutation_sha256(None),
                'completed_at': '2026-10-18T00:00:00Z',
            }
            host.ARTIFACTS.validate_cancel_result(report, request, Path(tmp))
            report['preserved_artifacts']['peer_report'] = int('1' * 64)
            with self.assertRaisesRegex(host.ARTIFACTS.ArtifactValidationError, 'preserved_artifacts peer_report'):
                host.ARTIFACTS.validate_cancel_result(report, request, Path(tmp))

    def test_process_run_directory_cannot_be_coerced_from_a_number(self):
        host = fixtures.load_host()
        run_dir = Path.cwd() / '123'
        process = fixtures.valid_peer_process(run_dir, Path.cwd(), Path.cwd() / 'guard')
        process['run_dir'] = 123
        with self.assertRaisesRegex(host.ARTIFACTS.ArtifactValidationError, 'run_dir'):
            host.ARTIFACTS.validate_peer_process(process, fixtures.request(), run_dir)

    def test_workspace_guard_run_id_must_be_a_string(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            guard = root / 'guard.json'
            with mock.patch.object(host.REGISTRY, 'workspace_guard_path', return_value=guard):
                host.REGISTRY.acquire_workspace_guard(root, '123', 'codex', root / 'run')
                data = host.CONFIG.STRICT_JSON.load(guard)
                data['runs']['123']['run_id'] = 123
                host.CONFIG.STRICT_JSON.write(guard, data)
                with self.assertRaisesRegex(SystemExit, 'run_id'):
                    host.REGISTRY.read_workspace_guard(root, '123')

    def test_request_rejects_boolean_helper_limit_and_numeric_digest(self):
        peer = fixtures.load_peer()
        invalid = fixtures.request(max_local_subagents=1)
        invalid['helper_enforcement']['limit'] = True
        with self.assertRaisesRegex(peer.RequestValidationError, 'helper_enforcement'):
            peer.validate_request(invalid)
        invalid = fixtures.request()
        invalid['availability_attestation']['evidence_sha256'] = int('1' * 64)
        with self.assertRaisesRegex(peer.RequestValidationError, 'evidence_sha256'):
            peer.validate_request(invalid)

    def test_snapshot_does_not_refresh_git_index(self):
        snapshot = fixtures.load_host().CONFIG.SNAPSHOT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for args in (['init', '-q'], ['config', 'user.name', 'Test'],
                         ['config', 'user.email', 'test@example.invalid']):
                subprocess.run(['git', *args], cwd=root, check=True)
            tracked = root / 'a.txt'
            tracked.write_text('unchanged\n')
            subprocess.run(['git', 'add', '.'], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-qm', 'initial'], cwd=root, check=True)
            st = tracked.stat()
            os.utime(tracked, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
            index_before = (root / '.git/index').read_bytes()
            first = snapshot.mutation_snapshot(root)
            second = snapshot.mutation_snapshot(root)
            self.assertEqual(first, second)
            self.assertEqual(index_before, (root / '.git/index').read_bytes())
            self.assertEqual(first['sections']['status'], '')

    def test_private_writers_reject_file_and_directory_symlinks(self):
        strict = fixtures.load_peer().STRICT_JSON
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'target'
            target.write_text('untouched')
            link = root / 'artifact'
            link.symlink_to(target)
            for write in (lambda: strict.write(link, {'value': 1}),
                          lambda: strict.write_text(link, 'text'),
                          lambda: strict.open_private_text(link)):
                with self.subTest(write=write), self.assertRaises(OSError):
                    write()
                self.assertTrue(link.is_symlink())
                self.assertEqual(target.read_text(), 'untouched')
            directory = root / 'directory'
            directory.mkdir(mode=0o755)
            before_mode = stat.S_IMODE(directory.stat().st_mode)
            directory_link = root / 'directory-link'
            directory_link.symlink_to(directory, target_is_directory=True)
            with self.assertRaises(OSError):
                strict.write(directory_link / 'file.json', {})
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), before_mode)
            self.assertFalse((directory / 'file.json').exists())

    def test_attestation_expiry_during_prelaunch_prevents_provider_exec(self):
        peer = fixtures.load_peer()
        request = fixtures.request()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / 'provider-ran'
            command = SimpleNamespace(args=[sys.executable, '-c',
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')", str(marker)], stdin='')
            def snapshot(*args, **kwargs):
                request['availability_attestation']['checked_at'] = '2026-01-01T00:00:00Z'
                return {'mode': 'git', 'same': True}
            with (mock.patch.object(peer, 'git_mutation_snapshot', side_effect=snapshot),
                  mock.patch.object(peer, 'build_peer_command', return_value=command)):
                result = peer.run_request(request, root, raw_output_path=root / 'peer.raw.json')
            self.assertEqual(result['error']['kind'], 'invalid_request')
            self.assertIn('not fresh enough', result['error']['message'])
            self.assertFalse(marker.exists())
            tracker = peer.STRICT_JSON.load(root / 'provider-process.json')
            self.assertEqual(tracker['status'], 'quiescent')
