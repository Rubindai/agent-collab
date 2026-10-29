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
            with mock.patch.object(host.REGISTRY, 'workspace_guard_path', return_value=root / 'guard.json'):
                host.REGISTRY.acquire_workspace_guard(root, 'reuse', 'codex', root / 'run')
                owner = host.REGISTRY.read_workspace_guard(root, 'reuse')[1]
                host.REGISTRY.release_workspace_guard(root, 'reuse', owner=owner)
                host.REGISTRY.acquire_workspace_guard(root, 'reuse', 'codex', root / 'run')
                before = (root / 'guard.json').read_bytes()
                for action in (
                    lambda: host.REGISTRY.release_workspace_guard(root, 'reuse', owner=owner),
                    lambda: host.REGISTRY.update_workspace_guard(root, 'reuse', owner=owner,
                        phase='peer_running', peer_identity=fixtures.valid_process_identity()),
                    lambda: host.REGISTRY.require_workspace_guard_owner(root, 'reuse', owner=owner),
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
            with (mock.patch.object(host.REGISTRY, 'workspace_guard_path', return_value=guard),
                  mock.patch.object(host, 'wait_for_peer_report', return_value='report_ready'),
                  mock.patch.object(host, 'record_host_workspace_mutation', return_value=None),
                  mock.patch.object(host.CONFIG.SAFETY, 'process_group_alive', return_value=False),
                  redirect_stdout(io.StringIO())):
                lifecycle.LifecycleAuditTests().prepare_run(host, root, run, guard)
                common = [str(run), '--repo-root', str(root)]
                host.finish(host.build_parser().parse_args(['finish', *common]))
                host.write_json(run / 'host-synthesis.json', fixtures.valid_host_synthesis(run_dir=run))
                args = host.build_parser().parse_args(['complete', *common])
                host.complete(args)
                host.REGISTRY.acquire_workspace_guard(root, run.name, 'claude', root / 'elsewhere' / run.name)
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
            with (mock.patch.object(host.REGISTRY, 'workspace_guard_path', return_value=guard),
                  mock.patch.object(host, 'process_alive', return_value=False),
                  mock.patch.object(host.CONFIG.SAFETY, 'process_group_alive', return_value=False),
                  mock.patch.object(host, 'record_cancel_workspace_mutation', return_value=None),
                  redirect_stdout(io.StringIO())):
                lifecycle.LifecycleAuditTests().prepare_run(host, root, run, guard)
                (run / 'peer-report.json').write_text('')
                args = host.build_parser().parse_args(['cancel', str(run), '--repo-root', str(root)])
                host.cancel(args)
                self.assertFalse(guard.exists())
                host.REGISTRY.acquire_workspace_guard(root, run.name, 'codex', root / 'other' / run.name)
                before = guard.read_bytes()
                with self.assertRaisesRegex(SystemExit, 'owner mismatch'):
                    host.cancel(args)
                self.assertEqual(guard.read_bytes(), before)

    def test_capacity_is_reserved_before_admission(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(host.REGISTRY, 'workspace_guard_path', return_value=root / 'guard.json'):
                host.REGISTRY.acquire_workspace_guard(root, 'one', 'codex', root / 'one')
                entries = host.REGISTRY.read_workspace_guards(root)[1]
                owner = entries['one']
                budget = host.REGISTRY.registry_capacity_bytes(entries)
                identity = fixtures.valid_process_identity()
                identity['start_time'] = 'x' * 3700
                with mock.patch.object(host.REGISTRY, 'WORKSPACE_GUARD_MAX_BYTES', budget):
                    with self.assertRaisesRegex(SystemExit, 'no capacity'):
                        host.REGISTRY.acquire_workspace_guard(root, 'two', 'claude', root / 'two')
                    for phase in ('peer_running', 'ready_for_synthesis'):
                        host.REGISTRY.update_workspace_guard(root, 'one', owner=owner, phase=phase, peer_identity=identity)
                    self.assertLess((root / 'guard.json').stat().st_size, budget)
                    host.REGISTRY.release_workspace_guard(root, 'one', owner=owner)
                self.assertFalse((root / 'guard.json').exists())

    def test_start_validation_does_not_probe_other_launchers(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(host.REGISTRY, 'workspace_guard_path', return_value=root / 'guard.json'):
                host.REGISTRY.acquire_workspace_guard(root, 'one', 'codex', root / 'one')
                with mock.patch.object(host.CONFIG.SAFETY, 'process_identity_state', side_effect=AssertionError('unrelated probe')):
                    host.REGISTRY.require_workspace_guard_available(root)
                    host.REGISTRY.acquire_workspace_guard(root, 'two', 'claude', root / 'two')
                for state in ('unknown', 'matching'):
                    with mock.patch.object(host.CONFIG.SAFETY, 'process_identity_state', return_value=state):
                        with self.assertRaisesRegex(SystemExit, 'proven dead'):
                            host.REGISTRY.recover_workspace_guard(root, 'one')
                with mock.patch.object(host.CONFIG.SAFETY, 'process_identity_state', return_value='dead_or_replaced'):
                    host.REGISTRY.recover_workspace_guard(root, 'one')
                self.assertEqual(set(host.REGISTRY.read_workspace_guards(root)[1]), {'two'})

    def test_independent_processes_keep_all_registry_entries(self):
        host = fixtures.load_host()
        code = '''import importlib.util, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).parent))
spec = importlib.util.spec_from_file_location('host', sys.argv[1])
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
r = Path(sys.argv[2]); h.REGISTRY.workspace_guard_path = lambda _: r / 'guard.json'
h.REGISTRY.acquire_workspace_guard(r, sys.argv[3], 'codex', r / sys.argv[3])
owner = h.REGISTRY.read_workspace_guard(r, sys.argv[3])[1]
h.REGISTRY.update_workspace_guard(r, sys.argv[3], owner=owner, phase='peer_running', peer_identity=h.CONFIG.SAFETY.process_identity(__import__('os').getpid()))
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processes = [subprocess.Popen([sys.executable, '-c', code, str(fixtures.HOST_RUNTIME), tmp, f'run-{i}'],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(8)]
            for process in processes:
                out, err = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, out + err)
            with mock.patch.object(host.REGISTRY, 'workspace_guard_path', return_value=root / 'guard.json'):
                self.assertEqual(len(host.REGISTRY.read_workspace_guards(root)[1]), 8)

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
        report = host.ARTIFACTS.default_adjudicator_report(fixtures.request(), fixtures.valid_report())
        report['summary'] = 'A reviewer approved this.'
        with self.assertRaisesRegex(host.ARTIFACTS.ArtifactValidationError, 'unmodified runtime placeholder'):
            host.ARTIFACTS.validate_adjudicator_report(report, fixtures.request())


class CompactWorkflowTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = fixtures.load_host()
        self.run = self.root / 'runs' / fixtures.request()['run_id']
        self.guard = self.root / 'guard.json'
        for patch in (
            mock.patch.object(self.host.REGISTRY, 'workspace_guard_path', return_value=self.guard),
            mock.patch.object(self.host, 'wait_for_peer_report', return_value='report_ready'),
            mock.patch.object(self.host, 'record_host_workspace_mutation', return_value=None),
            mock.patch.object(self.host.CONFIG.SAFETY, 'process_group_alive', return_value=False),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        lifecycle.LifecycleAuditTests().prepare_run(self.host, self.root, self.run, self.guard)

    def invoke(self, command, *options):
        output = io.StringIO()
        args = self.host.build_parser().parse_args([command, str(self.run), '--repo-root', str(self.root), *map(str, options)])
        with redirect_stdout(output):
            getattr(self.host, command)(args)
        return output.getvalue()

    def finish(self, *options):
        return json.loads(self.invoke('finish', *options))

    def author(self, result):
        path = Path(result['synthesis_input'])
        payload = {'summary':'Reviewed the captured evidence.', 'verdict':'pass', 'claims':[], 'unresolved_risks':[]}
        self.host.write_json(path, payload)
        return path

    def test_semantic_inputs_complete_without_manual_metadata(self):
        first = self.root / 'first.json'
        first.write_text(json.dumps({'summary':'Independent host pass.', 'claims':[]}))
        (self.run / 'host-first-pass.json').unlink()
        output = self.finish('--first-pass', first)
        authored = self.author(output)
        second = self.finish()
        self.assertEqual(output['synthesis_input'], second['synthesis_input'])
        self.assertEqual(self.host.ARTIFACTS.load_json(authored)['verdict'], 'pass')
        rendered = self.invoke('result', '--format', 'markdown', '--review', output['review_dir'])
        self.assertIn('Independent host pass.', rendered)
        self.assertIn('Peer Report', rendered)
        self.invoke('complete', '--synthesis', authored)
        synthesis = self.host.ARTIFACTS.load_json(self.run / 'host-synthesis.json')
        self.assertEqual(synthesis['run_id'], self.run.name)
        self.assertEqual(synthesis['claim_matrix_sha256'], output['claim_matrix_sha256'])
        self.assertTrue(synthesis['final_answer_ready'])
        self.assertFalse(self.guard.exists())
        self.invoke('complete', '--synthesis', authored)

    def test_changed_report_invalidates_old_draft_without_overwriting_it(self):
        previous = self.finish()
        authored = self.author(previous)
        original = authored.read_bytes()
        report = self.host.ARTIFACTS.load_json(self.run / 'peer-report.json')
        report['summary'] = 'New evidence.'
        self.host.write_json(self.run / 'peer-report.json', report)
        for command, flags in [('result', ('--format','markdown','--review',previous['review_dir'])),
                               ('complete', ('--synthesis', authored))]:
            with self.assertRaisesRegex(ValueError, 'stale review'):
                self.invoke(command, *flags)
        self.assertTrue(self.guard.exists())
        self.assertFalse((self.run / 'host-synthesis.json').exists())
        new = self.finish()
        self.assertNotEqual(new['review_dir'], previous['review_dir'])
        self.assertEqual(authored.read_bytes(), original)
        with self.assertRaisesRegex(ValueError, 'stale review'):
            self.invoke('complete', '--synthesis', authored)
        self.invoke('complete', '--synthesis', self.author(new))

    def test_wrong_run_and_modified_receipt_are_rejected(self):
        result = self.finish()
        authored = self.author(result)
        receipt = authored.parent / 'context.json'
        data = self.host.ARTIFACTS.load_json(receipt)
        data['owner_token'] = '0' * 32
        self.host.write_json(receipt, data)
        with self.assertRaisesRegex(ValueError, 'receipt was modified'):
            self.invoke('complete', '--synthesis', authored)
        self.assertTrue(self.guard.exists())
        copied = self.root / 'synthesis.json'
        copied.write_bytes(authored.read_bytes())
        with self.assertRaisesRegex(ValueError, 'direct, non-symlink'):
            self.invoke('complete', '--synthesis', copied)

    def test_invalid_helper_input_does_not_partially_write_first_pass(self):
        original = (self.run / 'host-first-pass.json').read_bytes()
        first = self.root / 'first.json'
        first.write_text(json.dumps({'summary':'Changed', 'claims':[]}))
        helpers = self.root / 'helpers.json'
        helpers.write_text(json.dumps({'reports':[{'name':str(i),'summary':'x','claims':[]} for i in range(9)]}))
        with self.assertRaisesRegex(ValueError, 'per-run limit'):
            self.finish('--first-pass', first, '--helpers', helpers)
        self.assertEqual((self.run / 'host-first-pass.json').read_bytes(), original)
        self.assertFalse((self.run / 'helper-reports.json').exists())

    def test_adjudicator_input_rebuilds_revision_and_preserves_judgments(self):
        first = self.finish()
        path = self.root / 'adjudicator.json'
        payload = {'status':'ok', 'summary':'Independent adjudication.', 'false_positives':[],
                   'claims_needing_verification':[], 'recommended_verdict':'pass', 'claims':[]}
        path.write_text(json.dumps(payload))
        second = self.finish('--adjudicator', path)
        self.assertNotEqual(first['review_dir'], second['review_dir'])
        saved = self.host.ARTIFACTS.load_json(self.run / 'adjudicator-report.json')
        self.assertEqual(saved['summary'], payload['summary'])
        self.invoke('complete', '--synthesis', self.author(second))
