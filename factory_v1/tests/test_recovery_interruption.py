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

    def test_registration_final_read_pause_matrix(self):
        original = self.stored()
        def read(*args):
            self.pause()
            return copy.deepcopy(self.issue)
        self.reader.side_effect = read
        self.refused(self.register)
        self.assertEqual(self.stored(), original)
        self.assertEqual(assignments.rows(self.db), [])
        self.github.request.assert_not_called()

    def test_prepare_and_result_final_read_pause_matrix(self):
        prepared = self.seed_assignment()
        handoff = prepared['handoff']
        loads = {s['source']: s['sha256'] for s in handoff['skills']} | handoff['input_loads']
        result = {'assignment_id': prepared['assignment_id'], 'handoff_digest': prepared['handoff_digest'],
                  'claim_id': prepared['claim_id'], 'run_id': 'fixture-run', 'status': 'stuck',
                  'loads': [{'source': source, 'sha256': pin, 'tool_reference': 'SIMULATED read'} for source, pin in loads.items()],
                  'work': ['SIMULATED untrusted result'], 'artifacts': [simplification.artifact('stage-evidence', 'fixture')],
                  'tests': [{'command': 'SIMULATED command', 'result': 'SIMULATED no native execution'}]}
        interruptions = []
        def read(*args):
            self.pause()
            interruptions.append(True)
            return copy.deepcopy(self.issue)
        self.reader.side_effect = read
        baseline = self.stored()
        with self.db:
            self.db.execute('DELETE FROM role_assignments')
        for label, seam, args in [
            ('prepare', self.prepare, ()),
            ('result', assignments.store_result, (self.db, 'p', 'i', '42', prepared['assignment_id'], result, self.github)),
        ]:
            with self.subTest(boundary=label):
                _, control_path = controls.paths(self.db, 'p', 'i')
                assert control_path is not None
                control_path.unlink(missing_ok=True)
                item = copy.deepcopy(baseline)
                if label == 'prepare':
                    record = recovery.active(item)
                    assert record is not None
                    record.update(status='registered', assignment=None)
                    item['main_incident']['recovery_assignment'] = assignments.digest(self.authorization)
                with self.db:
                    self.db.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
                    self.db.execute('UPDATE delivery_incidents SET payload=?', (json.dumps(item['main_incident']),))
                    if label == 'result':
                        self.db.execute('INSERT INTO role_assignments VALUES (?,?)', (prepared['assignment_id'], json.dumps(prepared)))
                before = self.stored()
                prior_interruptions = len(interruptions)
                with self.preparation_seams():
                    self.refused(seam, *args)
                self.assertGreater(len(interruptions), prior_interruptions)
                self.assertEqual(self.stored(), before)
                rows = assignments.rows(self.db)
                if label == 'prepare':
                    self.assertEqual(rows, [])
                else:
                    self.assertNotIn('submitted_result', rows[0])

    def test_clearance_final_read_pause_matrix(self):
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

    def test_merge_final_boundary_pause_matrix(self):
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

    def test_public_stop_ignores_controller_lock_matrix(self):
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

    def test_reopen_posteffect_pause_matrix(self):
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

    def test_status_posteffect_pause_matrix(self):
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

    def test_membership_preintent_pause_matrix(self):
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
