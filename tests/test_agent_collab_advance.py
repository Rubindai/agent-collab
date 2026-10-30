"""Observable guided-workflow behavior and the existing evidence boundaries."""
import json
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase, mock

import test_agent_collab_runtime as fixtures
import test_agent_collab_simplification as compact

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class AdvanceWorkflowTests(TestCase):
    def setUp(self):
        self.case = compact.CompactWorkflowTests('test_semantic_inputs_complete_without_manual_metadata')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.host, self.run = self.case.host, self.case.run

    def advance(self, *options):
        return json.loads(self.case.invoke('advance', *options))

    def evidence(self, output):
        return Path(output['evidence_file']).read_text()

    def advisory(self):
        path = self.case.root / 'advisory.json'
        self.host.write_json(path, {'status': 'ok', 'summary': 'Actual independent advisory report.',
            'false_positives': [], 'claims_needing_verification': [], 'recommended_verdict': 'pass', 'claims': []})
        return path

    def remove_first_pass(self):
        (self.run / 'host-first-pass.json').unlink()
        (self.run / 'host-inputs.json').unlink()

    def test_initial_resume_requests_first_pass_without_exposing_peer_output(self):
        self.remove_first_pass()
        original = self.host.ARTIFACTS.load_json

        def private_read(path, *args, **kwargs):
            self.assertNotIn(Path(path).name, ('peer-report.json', 'peer.raw.json', 'peer-normalization.json'))
            return original(path, *args, **kwargs)

        with mock.patch.object(self.host.ARTIFACTS, 'load_json', side_effect=private_read):
            output = self.advance()
        action = output['next_action']
        self.assertEqual(action['stage'], 'first_pass')
        self.assertNotIn('evidence_file', output)
        self.assertFalse((self.run / 'host-result.json').exists())
        self.assertEqual(self.advance(), output)
        path = Path(action['input_path'])
        self.host.write_json(path, {'summary': 'Completed independent analysis.', 'claims': []})
        args = self.host.build_parser().parse_args(action['command'][2:])
        self.assertEqual(args.first_pass, path)
        reviewed = self.advance('--first-pass', path)
        self.assertIn('Completed independent analysis.', self.evidence(reviewed))
        self.assertIn('Peer Report', self.evidence(reviewed))

    def test_adjudication_and_synthesis_resume_and_complete_idempotently(self):
        first = self.advance()
        self.assertEqual(first['next_action']['stage'], 'adjudicator')
        self.assertEqual(self.advance()['review_dir'], first['review_dir'])
        old_draft = self.case.author(first)
        ready = self.advance('--adjudicator', self.advisory())
        self.assertEqual(ready['next_action']['stage'], 'synthesis')
        self.assertNotEqual(first['review_dir'], ready['review_dir'])
        self.assertIn('Actual independent advisory report.', self.evidence(ready))
        with self.assertRaisesRegex(ValueError, 'stale review'):
            self.advance('--synthesis', old_draft)
        draft = self.case.author(ready)
        self.assertEqual(self.advance()['synthesis_input'], str(draft))
        self.assertEqual(json.loads(draft.read_text())['verdict'], 'pass')
        done = self.advance('--synthesis', draft)
        self.assertIsNone(done['next_action'])
        self.assertEqual(done['status'], 'completed')
        self.assertFalse(self.case.guard.exists())
        self.assertEqual(self.advance()['status'], 'already_complete')
        self.assertEqual(self.advance('--synthesis', draft)['status'], 'already_complete')

    def test_disabled_adjudication_goes_directly_to_synthesis(self):
        request = self.host.require_run_request(self.run)
        request['adjudicator_enabled'] = False
        self.host.write_json(self.run / 'host-request.json', request)
        self.host.write_json(self.run / 'peer-process.json',
            fixtures.valid_peer_process(self.run, self.case.root, self.case.guard))
        ready = self.advance()
        self.assertEqual(ready['next_action']['stage'], 'synthesis')
        with self.assertRaisesRegex(ValueError, 'enabled adjudication'):
            self.advance('--adjudicator', self.advisory())
        self.advance('--synthesis', self.case.author(ready))

    def test_unavailable_advisory_remains_absent_when_limitation_is_synthesized(self):
        ready = self.advance()
        draft = self.case.author(ready)
        value = json.loads(draft.read_text())
        value['unresolved_risks'] = ['Native advisory helper unavailable in this host.']
        self.host.write_json(draft, value)
        self.advance('--synthesis', draft)
        self.assertEqual(json.loads((self.run / 'adjudicator-report.json').read_text())['status'], 'advisory_pending')
        self.assertEqual(json.loads((self.run / 'host-synthesis.json').read_text())['unresolved_risks'], value['unresolved_risks'])

    def test_helpers_disabled_does_not_request_an_impossible_advisory(self):
        request = fixtures.request(local_subagents_allowed=False, max_local_subagents=0)
        self.host.write_json(self.run / 'host-request.json', request)
        self.host.write_json(self.run / 'peer-process.json',
            fixtures.valid_peer_process(self.run, self.case.root, self.case.guard))
        ready = self.advance()
        self.assertEqual(ready['next_action']['stage'], 'synthesis')
        self.assertFalse(ready['host_policy']['local_subagents_allowed'])
        self.assertIn('advisory_pending', self.evidence(ready))

    def test_admitted_helpers_and_advisory_share_the_finite_budget(self):
        request = fixtures.request(max_local_subagents=1)
        self.host.write_json(self.run / 'host-request.json', request)
        self.host.write_json(self.run / 'peer-process.json',
            fixtures.valid_peer_process(self.run, self.case.root, self.case.guard))
        self.remove_first_pass()
        first = self.case.root / 'first.json'
        helpers = self.case.root / 'helpers.json'
        self.host.write_json(first, {'summary': 'Independent host pass.', 'claims': []})
        self.host.write_json(helpers, {'reports': [{'name': 'reviewer', 'summary': 'Independent helper.', 'claims': []}]})
        ready = self.advance('--first-pass', first, '--helpers', helpers)
        self.assertEqual(ready['next_action']['stage'], 'synthesis')
        self.assertIn('Independent helper.', self.evidence(ready))
        with self.assertRaisesRegex(ValueError, 'per-run limit 1'):
            self.advance('--adjudicator', self.advisory())
        self.assertEqual(json.loads((self.run / 'adjudicator-report.json').read_text())['status'], 'advisory_pending')

    def test_failed_peer_still_offers_configured_advisory_for_its_evidence(self):
        report = self.host.CONFIG.load_peer_runtime().failure('peer_failed', 'Fixture failure.',
            self.host.require_run_request(self.run), None)
        self.host.write_json(self.run / 'peer-report.json', report)
        self.assertEqual(self.advance()['next_action']['stage'], 'adjudicator')

    def test_out_of_order_or_combined_judgments_do_not_mutate_evidence(self):
        original = (self.run / 'host-inputs.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'independent reports'):
            self.advance('--adjudicator', self.advisory())
        self.assertEqual((self.run / 'host-inputs.json').read_bytes(), original)
        ready = self.advance()
        with self.assertRaisesRegex(ValueError, 'separately'):
            self.advance('--adjudicator', self.advisory(), '--synthesis', self.case.author(ready))
        self.assertEqual((self.run / 'host-inputs.json').read_bytes(), original)
        self.assertTrue(self.case.guard.exists())
        self.assertFalse((self.run / 'host-synthesis.json').exists())

    def test_peer_changes_reject_old_synthesis_and_render_a_new_revision(self):
        ready = self.advance()
        draft = self.case.author(ready)
        before = draft.read_bytes()
        peer = json.loads((self.run / 'peer-report.json').read_text())
        peer['summary'] = 'Additional evidence.'
        self.host.write_json(self.run / 'peer-report.json', peer)
        with self.assertRaisesRegex(ValueError, 'stale review'):
            self.advance('--synthesis', draft)
        refreshed = self.advance()
        self.assertNotEqual(ready['review_dir'], refreshed['review_dir'])
        self.assertIn('Additional evidence.', self.evidence(refreshed))
        self.assertEqual(draft.read_bytes(), before)
        self.assertTrue(self.case.guard.exists())

    def test_render_avoids_matrix_duplicates_but_still_binds_the_matrix(self):
        ready = self.advance()
        text = self.evidence(ready)
        self.assertEqual(text.count('Host claim'), 1)
        receipt = json.loads((Path(ready['review_dir']) / 'context.json').read_text())
        self.assertIsNotNone(receipt['evidence_sha256']['claim-matrix.json'])
        self.assertIsNotNone(receipt['evidence_sha256']['host-inputs.json'])
        matrix_path = self.run / 'claim-matrix.json'
        matrix = json.loads(matrix_path.read_text())
        matrix['summary'] = 'Unreviewed matrix change.'
        self.host.write_json(matrix_path, matrix)
        with self.assertRaisesRegex(ValueError, 'stale review'):
            self.advance('--synthesis', self.case.author(ready))
        self.assertTrue(self.case.guard.exists())

    def test_large_render_is_a_private_bound_file_and_control_output_stays_small(self):
        report = json.loads((self.run / 'peer-report.json').read_text())
        report['claims'] = [{'claim': f'Candidate {i}: ' + 'detail ' * 60,
            'status': 'plausible_unverified', 'evidence': 'source evidence ' * 60} for i in range(30)]
        self.host.write_json(self.run / 'peer-report.json', report)
        raw = self.case.invoke('advance')
        output = json.loads(raw)
        self.assertLess(len(raw), 5000)
        self.assertEqual(next(iter(output)), 'next_action')
        evidence = Path(output['evidence_file'])
        data = evidence.read_bytes()
        self.assertGreater(len(data), 30000)
        self.assertEqual(output['evidence_bytes'], len(data))
        self.assertNotIn(b'\r\n', data)
        self.assertEqual(evidence.stat().st_mode & 0o777, 0o600)
        receipt = json.loads((evidence.parent / 'context.json').read_text())
        self.assertEqual(receipt['rendered_sha256'], hashlib.sha256(data).hexdigest())
        self.assertIn('Candidate 29', data.decode())
        evidence.write_text('Altered display')
        with self.assertRaisesRegex(ValueError, 'rendered review changed'):
            self.advance('--synthesis', self.case.author(output))
        self.assertTrue(self.case.guard.exists())

    def test_rendered_file_symlinks_are_rejected_without_overwriting_the_target(self):
        ready = self.advance()
        evidence = Path(ready['evidence_file'])
        target = self.case.root / 'target'
        target.write_text('untouched')
        evidence.unlink()
        evidence.symlink_to(target)
        with self.assertRaisesRegex(OSError, 'symlink'):
            self.advance()
        self.assertEqual(target.read_text(), 'untouched')

    def test_unsubmitted_synthesis_is_never_inferred_and_first_pass_stays_immutable(self):
        ready = self.advance()
        self.case.author(ready)
        self.assertIsNotNone(self.advance()['next_action'])
        self.assertFalse((self.run / 'host-synthesis.json').exists())
        changed = self.case.root / 'changed.json'
        self.host.write_json(changed, {'summary': 'Changed after peer exposure.', 'claims': []})
        with self.assertRaisesRegex(ValueError, 'first pass is immutable'):
            self.advance('--first-pass', changed)
        self.assertTrue(self.case.guard.exists())

    def test_waiting_holds_guard_and_never_exposes_partial_report(self):
        process = json.loads((self.run / 'peer-process.json').read_text())
        process['started_at_epoch'] = time.time()
        process['started_at'] = datetime.fromtimestamp(process['started_at_epoch'], timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.host.write_json(self.run / 'peer-process.json', process)
        with mock.patch.object(self.host, 'wait_for_peer_report', return_value='timeout'):
            result = self.advance('--timeout-seconds', 60)
        self.assertEqual(result['next_action']['stage'], 'waiting')
        self.assertNotIn('evidence_file', result)
        self.assertTrue(self.case.guard.exists())

    def test_default_guided_wait_is_bounded_and_main_returns_success_for_waiting(self):
        process = json.loads((self.run / 'peer-process.json').read_text())
        process['started_at_epoch'] = time.time()
        process['started_at'] = datetime.fromtimestamp(process['started_at_epoch'], timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        self.host.write_json(self.run / 'peer-process.json', process)
        wait, source = self.host.resolve_finish_wait_timeout(None, process, guided=True)
        duration, deadline = self.host.remaining_finish_wait_seconds(process, wait, now=process['started_at_epoch'] + 10)
        self.assertEqual((duration, source), (60, 'guided_default'))
        self.assertLess(process['started_at_epoch'] + 10 + duration, deadline)
        output = io.StringIO()
        with mock.patch.object(self.host, 'wait_for_peer_report', return_value='timeout') as waiting, redirect_stdout(output):
            code = self.host.main(['advance', str(self.run), '--repo-root', str(self.case.root)])
        self.assertEqual(code, 0)
        self.assertLessEqual(waiting.call_args.args[2], 60)
        result = json.loads(output.getvalue())
        self.assertEqual(result['next_action']['stage'], 'waiting')
        self.assertEqual(result['finish_timeout_source'], 'guided_default')
        # Bounded waits never extend the absolute deadline.
        duration, same_deadline = self.host.remaining_finish_wait_seconds(process, wait, now=deadline - 2)
        self.assertEqual((duration, same_deadline), (2, deadline))

    def test_provider_wait_does_not_hold_the_run_operation_lock(self):
        def wait(*args):
            with self.host.REGISTRY.run_operation_lock(self.run):
                return 'report_ready'
        with mock.patch.object(self.host, 'wait_for_peer_report', side_effect=wait):
            self.assertIn('evidence_file', self.advance())

    def test_terminal_runs_reject_new_evidence_and_cancellation_never_reactivates(self):
        ready = self.advance()
        self.advance('--synthesis', self.case.author(ready))
        with self.assertRaisesRegex(ValueError, 'terminal run'):
            self.advance('--adjudicator', self.advisory())
        # Even incomplete cancellation evidence must route to cancellation
        # validation rather than silently return a completed or active stage.
        self.host.write_json(self.run / 'cancel-result.json', {})
        with self.assertRaisesRegex(ValueError, 'cancel RUN'):
            self.advance()

    def test_unknown_process_identity_cannot_release_a_guard(self):
        with mock.patch.object(self.host.CONFIG.SAFETY, 'process_identity_state', return_value='unknown'):
            with self.assertRaises((SystemExit, ValueError)):
                self.advance()
        self.assertTrue(self.case.guard.exists())


class GuidedCliSmokeTests(TestCase):
    def test_execute_returned_argv_through_real_cli_with_a_finished_fixture_peer(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, run = base / 'workspace', base / 'runs/cli-smoke'
            repo.mkdir(); run.mkdir(parents=True)
            (repo / 'source.txt').write_text('fixture input\n')
            request = fixtures.request(run_id='cli-smoke', adjudicator_enabled=False)
            host.write_json(run / 'host-request.json', request)
            guard = host.REGISTRY.acquire_workspace_guard(repo, 'cli-smoke', 'codex', run)
            # A real, reaped wrapper identity keeps the test off provider networks.
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(.3)'], start_new_session=True)
            try:
                identity = host.CONFIG.SAFETY.process_identity(child.pid)
                started = time.time()
                process = fixtures.valid_peer_process(run, repo, guard, pid=child.pid, pgid=child.pid,
                    process_identity=identity, started_at_epoch=started,
                    started_at=datetime.fromtimestamp(started, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
                host.write_json(run / 'peer-process.json', process)
                host.REGISTRY.update_workspace_guard(repo, 'cli-smoke', owner=process, phase='peer_running', peer_identity=identity)
                host.run_snapshot(repo, run / 'before.snapshot', ignored_paths=[run])
            finally:
                child.wait(timeout=10)
            host.write_json(run / 'provider-process.json', fixtures.valid_provider_process('cli-smoke',
                pid=child.pid, pgid=child.pid, process_identity=identity))
            host.write_json(run / 'peer-report.json', fixtures.valid_report(run_id='cli-smoke'))
            action = host.next_action(run, repo, 'first_pass')
            host.write_json(Path(action['input_path']), {'summary': 'Independent fixture analysis.', 'claims': []})
            completed = subprocess.run(action['command'], cwd=base, capture_output=True, text=True, timeout=15)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            ready = json.loads(completed.stdout)
            self.assertEqual(ready['next_action']['stage'], 'synthesis')
            self.assertIn('Independent fixture analysis.', Path(ready['evidence_file']).read_text())
            host.write_json(Path(ready['synthesis_input']), {'summary': 'Verified fixture evidence.', 'verdict': 'pass',
                'claims': [], 'unresolved_risks': ['Synthetic provider report; no provider executed.']})
            closed = subprocess.run(ready['next_action']['command'], cwd=base, capture_output=True, text=True, timeout=15)
            self.assertEqual(closed.returncode, 0, closed.stderr)
            self.assertIsNone(json.loads(closed.stdout)['next_action'])
            self.assertIsNone(host.REGISTRY.read_workspace_guard(repo, 'cli-smoke')[1])
