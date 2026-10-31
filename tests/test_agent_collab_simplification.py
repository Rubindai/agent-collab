"""Regression coverage for concurrent ownership and the compact host workflow."""
import io
import json
import subprocess
import sys
import tempfile
from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest import TestCase, mock

import test_agent_collab_runtime as fixtures
import test_agent_collab_lifecycle_audit as lifecycle

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class SettingsProjectionTests(TestCase):
    def test_mode_selection_keeps_priority_and_word_boundaries(self):
        host = fixtures.load_host()
        for target, brief, expected in (
            ('crash', 'design a fix', 'debug'),
            ('crash', 'plan the fix', 'plan'),
            ('architecture', 'research current docs', 'design'),
            ('audit', 'research current docs', 'review'),
            ('provider behavior', 'research current docs', 'research'),
            ('relationship', 'explain the plantation layout', 'review'),
            ('PATCH', 'inspect behavior', 'review'),
        ):
            with self.subTest(target=target, brief=brief):
                self.assertEqual(host.classify_mode(target, brief), expected)

    def test_environment_roundtrip_preserves_explicit_choices(self):
        config = fixtures.load_host().CONFIG
        for enabled in (True, False):
            settings = {**config.SETTING_DEFAULTS,
                'safe_mode': enabled, 'local_subagents_allowed': enabled,
                'online_research': enabled, 'adjudicator_enabled': enabled,
                'max_local_subagents': 0, 'agent_timeout_seconds': 900.5,
                'codex_effort': 'high', 'claude_effort': 'max',
                'codex_config': ['notice="雪"'], 'workspace_mode': 'direct'}
            environment = config.settings_to_env(settings)
            raw = {key: environment[name] for key, name in config.SETTING_ENV_KEYS.items()}
            self.assertEqual(config.normalize_settings(raw, 'roundtrip'), settings)
            self.assertIn('雪', environment['CODEX_AGENT_COLLAB_CONFIG'])

    def test_setup_projection_preserves_false_zero_and_timeout_error_order(self):
        config = fixtures.load_host().CONFIG
        self.assertEqual(config.settings_from_args(Namespace(
            safe_mode=False, max_local_subagents=0, timeout_seconds=900,
            agent_timeout_seconds=3600, unknown='ignored')),
            {'safe_mode': False, 'max_local_subagents': 0, 'agent_timeout_seconds': 900})
        with self.assertRaisesRegex(ValueError, 'expected boolean'):
            config.settings_from_args(Namespace(safe_mode='invalid', timeout_seconds='invalid'))


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
                  mock.patch.object(host.LIFECYCLE, 'wait_for_peer_report', return_value='report_ready'),
                  mock.patch.object(host.LIFECYCLE, 'record_host_workspace_mutation', return_value=None),
                  mock.patch.object(host.CONFIG.SAFETY, 'process_group_alive', return_value=False),
                  redirect_stdout(io.StringIO())):
                lifecycle.LifecycleAuditTests().prepare_run(host, root, run, guard)
                common = [str(run), '--repo-root', str(root)]
                host.LIFECYCLE.finish(host.build_parser().parse_args(['finish', *common]))
                draft = fixtures.author_synthesis_input(host, run)
                args = host.build_parser().parse_args(['complete', *common, '--synthesis', str(draft)])
                host.LIFECYCLE.complete(args)
                host.REGISTRY.acquire_workspace_guard(root, run.name, 'claude', root / 'elsewhere' / run.name)
                before = guard.read_bytes()
                with self.assertRaisesRegex(SystemExit, 'owner mismatch'):
                    host.LIFECYCLE.complete(args)
                self.assertEqual(guard.read_bytes(), before)

    def test_old_cancellation_cannot_release_a_reused_id(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / 'runs' / fixtures.request()['run_id']
            guard = root / 'guard.json'
            with (mock.patch.object(host.REGISTRY, 'workspace_guard_path', return_value=guard),
                  mock.patch.object(host.PROCESS_TREE, 'process_alive', return_value=False),
                  mock.patch.object(host.CONFIG.SAFETY, 'process_group_alive', return_value=False),
                  mock.patch.object(host.LIFECYCLE, 'record_cancel_workspace_mutation', return_value=None),
                  redirect_stdout(io.StringIO())):
                lifecycle.LifecycleAuditTests().prepare_run(host, root, run, guard)
                (run / 'peer-report.json').write_text('')
                args = host.build_parser().parse_args(['cancel', str(run), '--repo-root', str(root)])
                host.LIFECYCLE.cancel(args)
                self.assertFalse(guard.exists())
                host.REGISTRY.acquire_workspace_guard(root, run.name, 'codex', root / 'other' / run.name)
                before = guard.read_bytes()
                with self.assertRaisesRegex(SystemExit, 'owner mismatch'):
                    host.LIFECYCLE.cancel(args)
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

    def test_wrongly_typed_registry_entry_is_named_and_preserved(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'guard.json'
            with mock.patch.object(host.REGISTRY, 'workspace_guard_path', return_value=path):
                host.REGISTRY.acquire_workspace_guard(root, 'named-entry', 'codex', root / 'run')
                original = host.ARTIFACTS.load_json(path)
                for field in ('host', 'phase'):
                    value = json.loads(json.dumps(original))
                    value['runs']['named-entry'][field] = []
                    host.CONFIG.STRICT_JSON.write(path, value)
                    before = path.read_bytes()
                    with self.assertRaisesRegex(SystemExit, 'named-entry'):
                        host.REGISTRY.read_workspace_guards(root)
                    self.assertEqual(path.read_bytes(), before)

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
        self.capture_mutation = self.host.LIFECYCLE.record_host_workspace_mutation
        self.run = self.root / 'runs' / fixtures.request()['run_id']
        self.guard = self.root / 'guard.json'
        for patch in (
            mock.patch.object(self.host.REGISTRY, 'workspace_guard_path', return_value=self.guard),
            mock.patch.object(self.host.LIFECYCLE, 'wait_for_peer_report', return_value='report_ready'),
            mock.patch.object(self.host.LIFECYCLE, 'record_host_workspace_mutation', return_value=None),
            mock.patch.object(self.host.CONFIG.SAFETY, 'process_group_alive', return_value=False),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        lifecycle.LifecycleAuditTests().prepare_run(self.host, self.root, self.run, self.guard)

    def invoke(self, command, *options):
        output = io.StringIO()
        args = self.host.build_parser().parse_args([command, str(self.run), '--repo-root', str(self.root), *map(str, options)])
        with redirect_stdout(output):
            args.func(args)
        return output.getvalue()

    def finish(self, *options):
        return json.loads(self.invoke('finish', *options))

    def author(self, result):
        path = Path(result['synthesis_input'])
        payload = {'summary':'Reviewed the captured evidence.', 'verdict':'pass', 'claims':[], 'unresolved_risks':[]}
        self.host.CONFIG.STRICT_JSON.write(path, payload)
        return path

    def test_missing_peer_report_keeps_summary_and_diagnostic_distinct(self):
        (self.run / 'peer-report.json').unlink()
        self.finish()
        report = self.host.ARTIFACTS.load_json(self.run / 'peer-report.json')
        self.assertEqual(report['summary'], 'Peer process produced no report before finish timeout.')
        self.assertEqual(report['error'], {'kind': 'peer_no_output',
            'message': 'Peer report missing or empty.', 'details': None})
        self.assertEqual(report['limitations'], ['Peer report missing or empty.'])

    def test_semantic_inputs_complete_without_manual_metadata(self):
        first = self.root / 'first.json'
        first.write_text(json.dumps({'summary':'Independent host pass.', 'claims':[]}))
        (self.run / 'host-first-pass.json').unlink()
        (self.run / 'host-inputs.json').unlink()
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
        self.host.CONFIG.STRICT_JSON.write(self.run / 'peer-report.json', report)
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

    def test_workspace_drift_requires_a_new_reviewed_draft(self):
        result = self.finish()
        authored = self.author(result)
        changed = self.root / 'workspace.txt'
        changed.write_text('before')
        self.host.LIFECYCLE.run_snapshot(self.root, self.run / 'before.snapshot', ignored_paths=[self.run])
        changed.write_text('after')
        mutation = self.capture_mutation(self.root, self.run, fixtures.request())
        self.assertIsNotNone(mutation)
        with mock.patch.object(self.host.LIFECYCLE, 'record_host_workspace_mutation', return_value=mutation):
            with self.assertRaisesRegex(SystemExit, 'mutation evidence changed'):
                self.invoke('complete', '--synthesis', authored)
            self.assertFalse((self.run / 'host-synthesis.json').exists())
            self.assertTrue(self.guard.exists())
            revision = self.finish()
            self.assertNotEqual(result['review_dir'], revision['review_dir'])
            with self.assertRaisesRegex(ValueError, 'stale review'):
                self.invoke('complete', '--synthesis', authored)
            self.invoke('complete', '--synthesis', self.author(revision))

    def test_wrong_run_and_modified_receipt_are_rejected(self):
        result = self.finish()
        authored = self.author(result)
        receipt = authored.parent / 'context.json'
        data = self.host.ARTIFACTS.load_json(receipt)
        data['owner_token'] = '0' * 32
        self.host.CONFIG.STRICT_JSON.write(receipt, data)
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


class ProviderProjectionTests(TestCase):
    def test_projection_preserves_report_structure_and_rejects_new_constraints(self):
        import copy
        module = fixtures.load_module(fixtures.RUNTIME_ROOT / 'scripts' / 'provider_schema.py', 'provider_schema_test')
        schema = json.loads(fixtures.CANONICAL_PEER_SCHEMA.read_text())
        projected = module.provider_schema(schema)
        self.assertEqual(projected, json.loads(fixtures.PEER_SCHEMA.read_text()))
        self.assertEqual(projected['required'], schema['required'])
        self.assertEqual(set(projected['properties']), set(schema['properties']))
        for mutate in (
            lambda value: value['properties']['summary'].update({'maxLength':10}),
            lambda value: value['properties']['target'].update({'minLength':2}),
            lambda value: value['allOf'].append({'required':['invented']}),
        ):
            changed = copy.deepcopy(schema)
            mutate(changed)
            with self.assertRaisesRegex(ValueError, 'unreviewed'):
                module.provider_schema(changed)

    def test_check_does_not_rewrite_the_schema(self):
        before = fixtures.PEER_SCHEMA.stat().st_mtime_ns
        result = subprocess.run([sys.executable, str(fixtures.RUNTIME_ROOT / 'scripts' / 'provider_schema.py'), '--check'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fixtures.PEER_SCHEMA.stat().st_mtime_ns, before)

    def test_package_sync_uses_the_selected_python_for_generation(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / 'selected-python'
            log = Path(tmp) / 'calls.jsonl'
            wrapper.write_text('#!' + sys.executable + '\nimport json,os,sys\n'
                + 'with open(' + repr(str(log)) + ', "a") as out: out.write(json.dumps(sys.argv[1:])+"\\n")\n'
                + 'os.execv(' + repr(sys.executable) + ', [' + repr(sys.executable) + ', *sys.argv[1:]])\n')
            wrapper.chmod(0o700)
            result = subprocess.run([str(fixtures.REPO_ROOT / 'scripts/sync-packages.sh'), '--check'],
                capture_output=True, text=True, env={**os.environ, 'PYTHON':str(wrapper)})
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertTrue(any('provider_schema.py' in command[0] and '--check' in command for command in calls))
