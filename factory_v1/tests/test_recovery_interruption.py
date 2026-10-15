"""Direct recovery interruption components; no services or worker journeys."""
import contextlib
import copy
import io
import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import __main__ as cli, assignments, controls, delivery, merge, recovery, simplification, tickets
from factory_v1.repositories import RepositoryError, state_lock
from factory_v1.tests.test_recovery_components import RecoveryComponentFixture


class RecoveryInterruptionTests(RecoveryComponentFixture):
    def pause(self, action='pause'):
        return controls.request(self.db, 'p', 'i', '42', action, 'SIMULATED interruption')

    def refused(self, function, *args):
        with self.assertRaises(RepositoryError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, 'iteration_paused')

    def test_registration_pause_after_final_issue_read_leaves_no_authority_or_parked_claim(self):
        original = self.stored()
        def read(*args):
            self.pause()
            return copy.deepcopy(self.issue)
        self.reader.side_effect = read
        self.refused(self.register)
        self.assertEqual(self.stored(), original)
        self.assertEqual(assignments.rows(self.db), [])
        self.github.request.assert_not_called()

    def test_pause_after_final_frontier_read_admits_no_repair_assignment(self):
        self.seed_registered()
        before = self.stored()
        def read(*args):
            self.pause()
            return copy.deepcopy(self.issue)
        self.reader.side_effect = read
        self.refused(self.prepare)
        self.assertEqual(self.stored(), before)
        self.assertEqual(assignments.rows(self.db), [])

    def test_pause_on_recovery_result_revalidation_admits_no_result(self):
        prepared = self.seed_assignment()
        handoff = prepared['handoff']
        loads = {s['source']: s['sha256'] for s in handoff['skills']} | handoff['input_loads']
        result = {'assignment_id': prepared['assignment_id'], 'handoff_digest': prepared['handoff_digest'],
                  'claim_id': prepared['claim_id'], 'run_id': 'fixture-run', 'status': 'stuck',
                  'loads': [{'source': source, 'sha256': pin, 'tool_reference': 'SIMULATED read'} for source, pin in loads.items()],
                  'work': ['SIMULATED untrusted result'], 'artifacts': [simplification.artifact('stage-evidence', 'fixture')],
                  'tests': [{'command': 'SIMULATED command', 'result': 'SIMULATED no native execution'}]}
        def read(*args):
            self.pause()
            return copy.deepcopy(self.issue)
        self.reader.side_effect = read
        with self.preparation_seams():
            self.refused(assignments.store_result, self.db, 'p', 'i', '42', prepared['assignment_id'], result, self.github)
        self.assertNotIn('submitted_result', assignments.rows(self.db)[0])

    def test_pause_after_last_clearance_context_read_keeps_incident_and_parked_ownership(self):
        self.seed_registered()
        config = {'recovery': {'incident_id': self.authorization['incident_id'], 'implementation_assignment': 'x'},
                  'head': 'b' * 40, 'api_base': self.github.base, 'required_checks': self.config['required_checks']}
        journal = {'status': 'delivered', 'integrated_evidence': {'integrated': 'a' * 40, 'candidate': 'b' * 40}, 'delivery_checks': ['bound-pass']}
        samples = []
        def sample(*args):
            samples.append(True)
            return {'evidence': self.evidence(True)}
        def frontier(db, item, gh):
            item['ticket_work']['frontier'] = {'items': [], 'eligible': [], 'active': [], 'foreign_active': []}
            return item
        current = assignments.current
        def last_context(*args, **kwargs):
            item = current(*args, **kwargs)
            if len(samples) == 2:
                self.pause()
            return item
        with contextlib.ExitStack() as stack:
            for target, value in [('load_config', config), ('controller_paths', None), ('lifecycle', journal), ('chain', ({}, {}, self.root))]:
                stack.enter_context(patch.object(merge, target, return_value=value))
            stack.enter_context(patch.object(recovery, 'merge_boundary', return_value={'removed_assignments': []}))
            stack.enter_context(patch.object(tickets, 'frontier', side_effect=frontier))
            stack.enter_context(patch.object(delivery, 'sample', side_effect=sample))
            stack.enter_context(patch.object(assignments, 'current', side_effect=last_context))
            self.refused(recovery.verify, self.db, 'p', 'i', '42', self.config_file, self.github)
        self.assertIsNotNone(self.stored()['main_incident'])
        self.assertEqual(recovery.active(self.stored())['status'], 'registered')

    def test_pause_at_last_recovery_merge_boundary_prevents_intent_and_put(self):
        assignment, report, result, artifacts = self.completed_report()
        config = {'recovery': {'incident_id': self.authorization['incident_id'],
                  'implementation_assignment': assignment['assignment_id'], 'issue': 20, 'removed_assignments': []},
                  'candidate_assignment': assignment['assignment_id'], 'repository': 'example/product',
                  'repository_id': 7, 'api_base': self.github.base, 'issue': 20, 'head': 'b' * 40, 'baseline': 'a' * 40,
                  'base': 'main', 'branch': 'repair', 'standards_assignment': 's', 'spec_assignment': 's2', 'pr_number': 1}
        api = Mock(github=self.github)
        api.prefix = '/repos/example/product'
        api.read.side_effect = lambda path: {'ref': 'refs/heads/' + ('main' if path.endswith('main') else 'repair'),
            'object': {'type': 'commit', 'sha': config['baseline'] if path.endswith('main') else config['head']}}
        pr = {'base': {'sha': 'a' * 40}, 'state': 'open', 'merged': False, 'draft': False,
              'mergeable': True, 'mergeable_state': 'clean'}
        boundaries = []
        boundary = recovery.merge_boundary
        def final_boundary(*args):
            admitted = boundary(*args)
            boundaries.append(admitted)
            if len(boundaries) == 3:
                self.pause()
            return admitted
        with contextlib.ExitStack() as stack:
            for name, value in [('load_config', config), ('controller_paths', None), ('MergeGitHub', api),
                                ('chain', (assignment, assignment, artifacts)), ('issue', self.issue),
                                ('mapped_candidate', {}), ('target', pr), ('protection', {}), ('checks', []), ('require_publication', None)]:
                stack.enter_context(patch.object(merge, name, return_value=value))
            stack.enter_context(patch.object(merge.review, 'evaluate', return_value={'status': 'accepted'}))
            stack.enter_context(patch.object(delivery, 'mutation_boundary', side_effect=lambda *a: self.stored()))
            stack.enter_context(patch.object(recovery, 'merge_boundary', side_effect=final_boundary))
            self.refused(merge.lifecycle, self.db, 'p', 'i', '42', self.config_file, 'merge-candidate')
        self.assertEqual(len(boundaries), 3)
        api.request.assert_not_called()
        self.assertNotIn('merge', assignments.rows(self.db)[0])

    def test_pause_and_emergency_stop_public_dispatch_ignore_controller_lock(self):
        state = self.root / 'state.sqlite'
        for action in ('pause', 'emergency-stop'):
            argv = ['factory_v1', '--state', str(state), '--operator-id', '42', action,
                    '--project', 'p', '--iteration', 'i', '--reference', 'SIMULATED control']
            with state_lock(self.db), patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()) as out:
                cli.main()
            self.assertFalse(json.loads(out.getvalue())['dispatch_allowed'])
            self.refused(self.register)
            self.assertNotIn('main_recoveries', self.stored())
        self.github.request.assert_not_called()

    def test_pause_after_reopen_retains_receipt_but_admits_no_status_intent(self):
        prior, issue = self.delivered_ticket()
        self.seed_registered()
        field, member = self.tracker_for_removed(issue)
        write = self.github.request.side_effect
        def reopen(*args):
            write(*args)
            self.pause()
        self.github.request.side_effect = reopen
        item = self.stored()
        self.refused(recovery.reopen_removed, self.db, item, recovery.active(item),
                     {'removed_assignments': [prior['assignment_id']]}, self.github)
        receipt = recovery.active(self.stored())['removed'][prior['assignment_id']]
        self.assertTrue(receipt['open_intent'])
        self.assertEqual(receipt['issue_readback']['state'], 'open')
        self.assertFalse(receipt['status_intent'])
        self.assertEqual(member['status'], 'D')
        field.assert_not_called()
        self.assertEqual(self.github.request.call_count, 1)

    def test_pause_after_status_effect_keeps_membership_dependency_and_delivery_receipts(self):
        prior, issue = self.delivered_ticket()
        self.seed_registered()
        field, member = self.tracker_for_removed(issue)
        effect = field.side_effect
        def status(*args):
            effect(*args)
            self.pause()
        field.side_effect = status
        item = self.stored()
        self.refused(recovery.reopen_removed, self.db, item, recovery.active(item),
                     {'removed_assignments': [prior['assignment_id']]}, self.github)
        receipt = recovery.active(self.stored())['removed'][prior['assignment_id']]
        self.assertEqual(receipt['membership']['status'], 'T')
        self.assertEqual(receipt['dependencies'], [])
        self.assertEqual(assignments.rows(self.db)[0]['merge']['failure']['code'], 'delivery_removed')
        self.assertIsNotNone(self.stored()['main_incident'])
        self.assertEqual(field.call_count, 1)

    def test_pause_during_membership_read_prevents_reopen_intent_and_write(self):
        prior, issue = self.delivered_ticket()
        self.seed_registered()
        def membership(*args):
            self.pause()
            return {'id': 'MEM10', 'status': 'D'}
        with patch('factory_v1.tickets.gh.membership', side_effect=membership):
            item = self.stored()
            self.refused(recovery.reopen_removed, self.db, item, recovery.active(item),
                         {'removed_assignments': [prior['assignment_id']]}, self.github)
        self.assertEqual(recovery.active(self.stored())['removed'], {})
        self.github.request.assert_not_called()
