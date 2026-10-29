"""Regression coverage for concurrent ownership and the compact host workflow."""
import io
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import TestCase, mock

import test_agent_collab_runtime as fixtures
import test_agent_collab_lifecycle_audit as lifecycle

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class RegistryOwnershipTests(TestCase):
    def test_reused_id_rejects_old_owner_even_at_the_same_path(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(host, 'workspace_guard_path', return_value=root / 'guard.json'):
                host.acquire_workspace_guard(root, 'reuse', 'codex', root / 'run')
                owner = host.read_workspace_guard(root, 'reuse')[1]
                host.release_workspace_guard(root, 'reuse', owner=owner)
                host.acquire_workspace_guard(root, 'reuse', 'codex', root / 'run')
                before = (root / 'guard.json').read_bytes()
                for action in (
                    lambda: host.release_workspace_guard(root, 'reuse', owner=owner),
                    lambda: host.update_workspace_guard(root, 'reuse', owner=owner,
                        phase='peer_running', peer_identity=fixtures.valid_process_identity()),
                    lambda: host.require_workspace_guard_owner(root, 'reuse', owner=owner),
                ):
                    with self.assertRaisesRegex(SystemExit, 'owner mismatch'):
                        action()
                    self.assertEqual((root / 'guard.json').read_bytes(), before)

    def test_old_completed_run_cannot_release_reused_id_in_another_root(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / 'runs' / fixtures.request()['run_id']
            guard = root / 'guard.json'
            with (mock.patch.object(host, 'workspace_guard_path', return_value=guard),
                  mock.patch.object(host, 'wait_for_peer_report', return_value='report_ready'),
                  mock.patch.object(host, 'record_host_workspace_mutation', return_value=None),
                  mock.patch.object(host.SAFETY, 'process_group_alive', return_value=False),
                  redirect_stdout(io.StringIO())):
                lifecycle.LifecycleAuditTests().prepare_run(host, root, run, guard)
                common = [str(run), '--repo-root', str(root)]
                host.finish(host.build_parser().parse_args(['finish', *common]))
                host.write_json(run / 'host-synthesis.json', fixtures.valid_host_synthesis(run_dir=run))
                args = host.build_parser().parse_args(['complete', *common])
                host.complete(args)
                host.acquire_workspace_guard(root, run.name, 'claude', root / 'elsewhere' / run.name)
                before = guard.read_bytes()
                with self.assertRaisesRegex(SystemExit, 'owner mismatch'):
                    host.complete(args)
                self.assertEqual(guard.read_bytes(), before)

    def test_old_cancellation_cannot_release_a_reused_id(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / 'runs' / fixtures.request()['run_id']
            guard = root / 'guard.json'
            with (mock.patch.object(host, 'workspace_guard_path', return_value=guard),
                  mock.patch.object(host, 'process_alive', return_value=False),
                  mock.patch.object(host.SAFETY, 'process_group_alive', return_value=False),
                  mock.patch.object(host, 'record_cancel_workspace_mutation', return_value=None),
                  redirect_stdout(io.StringIO())):
                lifecycle.LifecycleAuditTests().prepare_run(host, root, run, guard)
                (run / 'peer-report.json').write_text('')
                args = host.build_parser().parse_args(['cancel', str(run), '--repo-root', str(root)])
                host.cancel(args)
                self.assertFalse(guard.exists())
                host.acquire_workspace_guard(root, run.name, 'codex', root / 'other' / run.name)
                before = guard.read_bytes()
                with self.assertRaisesRegex(SystemExit, 'owner mismatch'):
                    host.cancel(args)
                self.assertEqual(guard.read_bytes(), before)

    def test_capacity_is_reserved_before_admission(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(host, 'workspace_guard_path', return_value=root / 'guard.json'):
                host.acquire_workspace_guard(root, 'one', 'codex', root / 'one')
                entries = host.read_workspace_guards(root)[1]
                owner = entries['one']
                budget = host.registry_capacity_bytes(entries)
                identity = fixtures.valid_process_identity()
                identity['start_time'] = 'x' * 3700
                with mock.patch.object(host, 'WORKSPACE_GUARD_MAX_BYTES', budget):
                    with self.assertRaisesRegex(SystemExit, 'no capacity'):
                        host.acquire_workspace_guard(root, 'two', 'claude', root / 'two')
                    for phase in ('peer_running', 'ready_for_synthesis'):
                        host.update_workspace_guard(root, 'one', owner=owner, phase=phase, peer_identity=identity)
                    self.assertLess((root / 'guard.json').stat().st_size, budget)
                    host.release_workspace_guard(root, 'one', owner=owner)
                self.assertFalse((root / 'guard.json').exists())

    def test_start_validation_does_not_probe_other_launchers(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(host, 'workspace_guard_path', return_value=root / 'guard.json'):
                host.acquire_workspace_guard(root, 'one', 'codex', root / 'one')
                with mock.patch.object(host.SAFETY, 'process_identity_state', side_effect=AssertionError('unrelated probe')):
                    host.require_workspace_guard_available(root)
                    host.acquire_workspace_guard(root, 'two', 'claude', root / 'two')
                for state in ('unknown', 'matching'):
                    with mock.patch.object(host.SAFETY, 'process_identity_state', return_value=state):
                        with self.assertRaisesRegex(SystemExit, 'proven dead'):
                            host.recover_workspace_guard(root, 'one')
                with mock.patch.object(host.SAFETY, 'process_identity_state', return_value='dead_or_replaced'):
                    host.recover_workspace_guard(root, 'one')
                self.assertEqual(set(host.read_workspace_guards(root)[1]), {'two'})

    def test_independent_processes_keep_all_registry_entries(self):
        host = fixtures.load_host()
        code = '''import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('host', sys.argv[1])
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
r = Path(sys.argv[2]); h.workspace_guard_path = lambda _: r / 'guard.json'
h.acquire_workspace_guard(r, sys.argv[3], 'codex', r / sys.argv[3])
owner = h.read_workspace_guard(r, sys.argv[3])[1]
h.update_workspace_guard(r, sys.argv[3], owner=owner, phase='peer_running', peer_identity=h.SAFETY.process_identity(__import__('os').getpid()))
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processes = [subprocess.Popen([sys.executable, '-c', code, str(fixtures.HOST_RUNTIME), tmp, f'run-{i}'],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(8)]
            for process in processes:
                out, err = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, out + err)
            with mock.patch.object(host, 'workspace_guard_path', return_value=root / 'guard.json'):
                self.assertEqual(len(host.read_workspace_guards(root)[1]), 8)

    def test_malformed_cli_artifact_is_actionable_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / 'run'
            run.mkdir()
            (run / 'host-request.json').write_text('{broken')
            result = subprocess.run([sys.executable, str(fixtures.HOST_RUNTIME), 'finish', str(run)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('host-request.json', result.stderr)
            self.assertNotIn('Traceback', result.stderr)

    def test_pending_adjudication_cannot_contain_authored_judgments(self):
        host = fixtures.load_host()
        report = host.default_adjudicator_report(fixtures.request(), fixtures.valid_report())
        report['summary'] = 'A reviewer approved this.'
        with self.assertRaisesRegex(host.ArtifactValidationError, 'unmodified runtime placeholder'):
            host.validate_adjudicator_report(report, fixtures.request())
