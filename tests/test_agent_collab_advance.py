"""Observable guided-workflow behavior and the existing evidence boundaries."""
import json
import time
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
        self.assertNotIn('evidence_markdown', output)
        self.assertFalse((self.run / 'host-result.json').exists())
        self.assertEqual(self.advance(), output)
        path = Path(action['input_path'])
        self.host.write_json(path, {'summary': 'Completed independent analysis.', 'claims': []})
        args = self.host.build_parser().parse_args(action['command'][2:])
        self.assertEqual(args.first_pass, path)
        reviewed = self.advance('--first-pass', path)
        self.assertIn('Completed independent analysis.', reviewed['evidence_markdown'])
        self.assertIn('Peer Report', reviewed['evidence_markdown'])

    def test_adjudication_and_synthesis_resume_and_complete_idempotently(self):
        first = self.advance()
        self.assertEqual(first['next_action']['stage'], 'adjudicator')
        self.assertEqual(self.advance()['review_dir'], first['review_dir'])
        old_draft = self.case.author(first)
        ready = self.advance('--adjudicator', self.advisory())
        self.assertEqual(ready['next_action']['stage'], 'synthesis')
        self.assertNotEqual(first['review_dir'], ready['review_dir'])
        self.assertIn('Actual independent advisory report.', ready['evidence_markdown'])
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
        self.assertIn('advisory_pending', ready['evidence_markdown'])

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
        self.assertIn('Additional evidence.', refreshed['evidence_markdown'])
        self.assertEqual(draft.read_bytes(), before)
        self.assertTrue(self.case.guard.exists())

    def test_render_avoids_matrix_duplicates_but_still_binds_the_matrix(self):
        ready = self.advance()
        text = ready['evidence_markdown']
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
        self.assertNotIn('evidence_markdown', result)
        self.assertTrue(self.case.guard.exists())

    def test_provider_wait_does_not_hold_the_run_operation_lock(self):
        def wait(*args):
            with self.host.REGISTRY.run_operation_lock(self.run):
                return 'report_ready'
        with mock.patch.object(self.host, 'wait_for_peer_report', side_effect=wait):
            self.assertIn('evidence_markdown', self.advance())

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
        with mock.patch.object(self.host.CONFIG.SAFETY, 'process_group_alive', return_value=True):
            with self.assertRaises((SystemExit, ValueError)):
                self.advance()
        self.assertTrue(self.case.guard.exists())
