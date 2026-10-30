"""Regression cases reproduced by the installed-plugin audit."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, mock, skipUnless

import test_agent_collab_runtime as fixtures
import test_agent_collab_simplification as compact

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class AuditRegressionTests(TestCase):
    def workflow(self):
        case = compact.CompactWorkflowTests('test_semantic_inputs_complete_without_manual_metadata')
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_exact_active_id_precedes_local_prefix_and_historical_same_id(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local, other = root / 'local', root / 'other'
            for name in ('review', 'review-next'):
                run = local / name
                run.mkdir(parents=True)
                host.write_json(run / 'host-request.json', fixtures.request(run_id=name))
                host.CONFIG.load_state_runtime().upsert_job(local, {
                    key: value for key, value in fixtures.valid_state_job(
                        name, run_dir=str(run), repo_root=str(root)).items()
                    if key not in {'created_at', 'updated_at'}
                })
            exact = other / 'review'
            exact.mkdir(parents=True)
            host.write_json(exact / 'host-request.json', fixtures.request(run_id='review'))
            with mock.patch.object(host.REGISTRY, 'workspace_guard_path', return_value=root / 'guard.json'):
                host.REGISTRY.acquire_workspace_guard(root, 'review', 'codex', exact)
                self.assertEqual(host.resolve_run_reference(root, 'review', local), exact)
                (local / 'review' / 'host-request.json').unlink()
                (local / 'review').rmdir()
                host.CONFIG.load_state_runtime().remove_job(local, 'review')
                self.assertEqual(host.resolve_run_reference(root, 'review', local), exact)

    def test_cli_normalizes_explicit_git_subdirectory(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            sub = root / 'sub'
            sub.mkdir()
            self.assertEqual(host.CONFIG.canonical_repo_root(sub), root)
            host.REGISTRY.acquire_workspace_guard(root, 'active', 'codex', root / 'runs' / 'active')
            for path in (root, sub):
                output = io.StringIO()
                with redirect_stdout(output):
                    host.main(['status', '--active', '--repo-root', str(path)])
                self.assertEqual(json.loads(output.getvalue())['runs'][0]['repo_root'], str(root))

    def test_failed_normalization_keeps_revision_and_authored_draft(self):
        case = self.workflow()
        case.host.write_json(case.run / 'peer-report.json', {'invalid': 'report'})
        first = case.finish()
        draft = case.author(first)
        before = (case.run / 'peer-normalization.json').read_bytes()
        second = case.finish()
        self.assertEqual(first['validation_status'], 'schema_validation_failed')
        self.assertEqual(second['validation_status'], first['validation_status'])
        self.assertEqual(first['review_dir'], second['review_dir'])
        self.assertEqual((case.run / 'peer-normalization.json').read_bytes(), before)
        case.invoke('complete', '--synthesis', draft)

    def test_provider_failure_provenance_survives_finish(self):
        case = self.workflow()
        peer = fixtures.load_peer()
        for kind in ('invalid_json', 'noncanonical_output', 'schema_validation_failed', 'peer_report_mismatch'):
            with self.subTest(kind=kind):
                case.host.write_json(case.run / 'peer-report.json', peer.failure(kind, 'provider failure', fixtures.request()))
                metadata = {'schema_version': '2.0', 'source': 'none', 'artifact_source': 'peer_raw',
                            'input_bytes': 42, 'warnings': [], 'validation_status': kind, 'error': 'provider failure'}
                case.host.write_json(case.run / 'peer-normalization.json', metadata)
                self.assertEqual(case.finish()['validation_status'], kind)
                self.assertEqual(case.host.ARTIFACTS.load_json(case.run / 'peer-normalization.json'), metadata)

    @skipUnless(os.name == 'posix', 'POSIX zombie process')
    def test_zombie_wrapper_report_is_ready_and_survives_expired_finish(self):
        case = self.workflow()
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.1)'], start_new_session=True)
        try:
            identity = case.host.CONFIG.SAFETY.process_identity(child.pid)
            deadline = time.monotonic() + 5
            while fixtures.process_state(child.pid) != 'Z':
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.assertFalse(case.host.CONFIG.SAFETY.pid_alive(child.pid))
            self.assertEqual(case.host.CONFIG.SAFETY.process_identity_state(identity), 'dead_or_replaced')
            info = case.host.ARTIFACTS.load_json(case.run / 'peer-process.json')
            info.update(pid=child.pid, pgid=child.pid, process_identity=identity)
            case.host.write_json(case.run / 'peer-process.json', info)
            case.host.REGISTRY.update_workspace_guard(case.root, info['run_id'], owner=info,
                                                       phase='peer_running', peer_identity=identity)
            # Use the real wait function, not the fixture's report-ready stub.
            real_host = fixtures.load_host()
            self.assertEqual(real_host.wait_for_peer_report(case.run / 'peer-report.json', info, 0.1), 'report_ready')
            before = case.host.ARTIFACTS.load_json(case.run / 'peer-report.json')
            self.assertEqual(case.finish()['peer_status'], 'ok')
            self.assertEqual(case.host.ARTIFACTS.load_json(case.run / 'peer-report.json'), before)
        finally:
            child.wait(timeout=5)

    @skipUnless(sys.platform.startswith('linux'), 'Linux PID namespaces')
    def test_foreign_namespace_is_unknown_before_pid_lookup(self):
        host = fixtures.load_host()
        safety = host.CONFIG.SAFETY
        identity = safety.process_identity(os.getpid())
        identity['pid_namespace'] = 'pid:[0]'
        with mock.patch.object(safety, 'process_identity', side_effect=AssertionError('foreign PID lookup')):
            self.assertEqual(safety.process_identity_state(identity), 'unknown')
        identity.pop('pid_namespace')
        with self.assertRaises(ValueError):
            safety.validate_process_identity(identity)

    @skipUnless(sys.platform.startswith('linux'), 'Linux PID namespaces')
    def test_live_launcher_cannot_be_recovered_from_another_pid_namespace(self):
        if not shutil.which('bwrap'):
            self.skipTest('bwrap unavailable')
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'XDG_STATE_HOME': tmp + '/state'}):
            root = Path(tmp) / 'workspace'
            root.mkdir()
            guard = host.REGISTRY.acquire_workspace_guard(root, 'live', 'codex', Path(tmp) / 'runs' / 'live')
            before = guard.read_bytes()
            code = '''import sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import registry
root = Path(sys.argv[2])
entry = registry.read_workspace_guard(root, 'live')[1]
assert registry.CONFIG.SAFETY.process_identity_state(entry['launcher_identity']) == 'unknown'
try:
    registry.recover_workspace_guard(root, 'live')
except SystemExit:
    pass
else:
    raise AssertionError('recovered a live foreign-namespace launcher')
'''
            args = ['bwrap', '--die-with-parent', '--unshare-pid', '--ro-bind', '/', '/',
                    '--bind', tmp, tmp, '--proc', '/proc', '--', sys.executable, '-c', code,
                    str(fixtures.HOST_RUNTIME.parent), str(root)]
            completed = subprocess.run(args, capture_output=True, text=True, timeout=15)
            if completed.returncode and 'bwrap:' in completed.stderr:
                self.skipTest('bwrap PID namespaces unavailable: ' + completed.stderr.strip())
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(guard.read_bytes(), before)
