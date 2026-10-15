"""Restart/recovery integration at public guards with explicit persisted records only."""
import copy
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from factory_v1 import assignments, controls, recovery, restart, sandbox, tickets
from factory_v1.repositories import RepositoryError
from factory_v1.tests import test_restart


class RestartRecoveryTests(unittest.TestCase):
    setUp = test_restart.RestartTests.setUp
    save_item = test_restart.RestartTests.save_item
    stored = test_restart.RestartTests.stored
    assignment = test_restart.RestartTests.assignment

    def registration(self):
        self.item['repository_onboarding'] = {'status': 'verified', 'metadata': {'id': 123}}
        self.item['delivery_monitor'] = {'config': 'SIMULATED host configuration'}
        self.pin = {'number': 20, 'id': 200, 'node_id': 'I20', 'body_sha256': assignments.digest('issue')}
        self.record = {'status': 'registered', 'assignment': None, 'parked': [],
            'config': {'incident_id': 'incident', 'evidence_id': 'e' * 64, 'issue': self.pin,
                       'paths': ['app.py'], 'removable_assignments': []},
            'evidence': {'main': 'a' * 40}, 'removed': {}, 'removable': {}}
        self.item['main_recoveries'] = {'incident': self.record}
        self.item['main_incident'] = {'incident_id': 'incident', 'recovery_issue': 20,
                                    'recovery_assignment': assignments.digest(self.record['config'])}
        self.save_item()
        return {'stage': 'repair', 'issue': self.pin, 'baseline': 'a' * 40, 'candidate': None,
                'claim_id': 'fresh', 'profile': {'name': 'fresh', 'home': '/fresh'},
                'preceding': recovery.preceding(self.record)}

    def observations(self, status='failed'):
        stack = ExitStack()
        stack.enter_context(patch.object(restart, 'tracker', return_value=None))
        stack.enter_context(patch.object(restart.delivery, 'sample', return_value={'evidence': {'status': status, 'main': 'a' * 40}}))
        stack.enter_context(patch.object(restart.delivery, 'monitor_state', return_value={'config': {'api_base': self.github.base}}))
        stack.enter_context(patch.object(restart.delivery, 'validate_persisted'))
        stack.enter_context(patch.object(restart.delivery, 'persisted_evidence', return_value=self.record['evidence']))
        stack.enter_context(patch.object(recovery, 'exact_issue', return_value={'state': 'open'}))
        stack.enter_context(patch('factory_v1.requirements.revalidate_inputs', return_value={}))
        return stack

    def test_registered_repair_readback_is_ready_without_ordinary_dispatch_or_incident_release(self):
        self.registration()
        incident = copy.deepcopy(self.item['main_incident'])
        for status in ('healthy', 'failed', 'unknown'):
            with self.subTest(status=status), self.observations(status):
                result = restart.reconcile(self.db, 'p', 'i', 'operator', self.github)
                self.assertEqual(result['status'], 'ready')
                self.assertFalse(result['dispatch_allowed'])
                self.assertEqual(self.stored()['main_incident'], incident)
                with self.assertRaises(RepositoryError):
                    restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github)

    def test_exact_repair_can_pass_public_prepare_boundary_after_failed_health(self):
        request = self.registration()
        # Stop at spending after real restart and recovery handoff admission, before
        # any downstream frontier/persistence. No launch or preparatory workflow.
        request.update(repository='owner/repo', repository_id=123, spec_commit='c' * 40, skills=[],
                       workspace='/fixture', standards=[], capabilities=[], result_contract=assignments.CONTRACT)
        request['profile'].update(provider='fixture', model='fixture', operation='fixture')
        item = self.stored()
        item.update(handoff={'commit': 'c' * 40}, repository_onboarding={'status': 'verified', 'metadata': {'id': 123}})
        self.item = item
        self.save_item()
        for status in ('healthy', 'failed', 'unknown'):
            with self.subTest(status=status), self.observations(status), patch.object(assignments, 'validate', return_value=[]), \
                 patch.object(assignments, 'admission_preview', side_effect=RuntimeError('REACHED spending')):
                with self.assertRaisesRegex(RuntimeError, 'REACHED spending'):
                    recovery.prepare(self.db, 'p', 'i', 'operator', request, self.github)
        self.assertEqual(assignments.rows(self.db), [])

    def test_ordinary_different_and_stale_repair_authority_cannot_pass_dispatch(self):
        request = self.registration()
        cases = [request | {'stage': 'implementation'}, request | {'issue': self.pin | {'number': 21}},
                 request | {'baseline': 'b' * 40}, request | {'preceding': []}]
        for value in cases:
            with self.subTest(value=value), self.observations(), self.assertRaises(RepositoryError):
                restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github, request=value)
        self.assertEqual(assignments.rows(self.db), [])

    def test_fresh_incident_requirements_and_runtime_failures_refuse_exact_repair(self):
        request = self.registration()
        for target in ('factory_v1.delivery.validate_persisted', 'factory_v1.requirements.revalidate_inputs',
                       'factory_v1.recovery.exact_issue'):
            with self.subTest(target=target), self.observations(), \
                 patch(target, side_effect=RepositoryError('scope_mismatch', 'drift')), self.assertRaises(RepositoryError):
                restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github, request=request)
        with self.observations(), patch.object(restart.delivery, 'sample', return_value={'evidence': {'status': 'failed', 'main': 'b' * 40}}), self.assertRaises(RepositoryError):
            restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github, request=request)
        row = self.assignment()
        self.record['parked'] = [row['assignment_id']]
        self.save_item()
        with self.observations(), patch.object(restart, 'worker', side_effect=RepositoryError('worker_uncertain', 'not absent')), self.assertRaises(RepositoryError):
            restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github, request=request)

    def test_parked_failed_writer_releases_ownership_but_never_its_results(self):
        self.registration()
        row = self.assignment('failed')
        row['runtime']['container_removed'] = True
        row['recovery_disposition'] = 'incident'
        self.record['parked'] = [row['assignment_id']]
        self.save_item()
        self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),))
        self.db.commit()
        self.assertEqual(restart.ownership(self.db, self.stored())['assignments'], [])
        request = {'stage': 'repair', 'claim_id': 'fresh', 'profile': {'name': 'fresh', 'home': '/fresh'}}
        assignments.claims(self.db, request, row['ticket_scope'], 'd' * 64)
        with self.assertRaises(RepositoryError):
            assignments.require_result_active(row)

    def test_public_launch_revalidates_exact_repair_before_runtime_access(self):
        request = self.registration()
        row = self.assignment()
        self.record['assignment'] = row['assignment_id']
        self.item['main_incident']['recovery_assignment'] = row['assignment_id']
        self.save_item()
        row['claim_id'] = request['claim_id']
        row['handoff'] = request | {'recovery': recovery.contract(self.record)}
        row.pop('runtime')
        row['ticket_scope']['issue_number'] = 20
        self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),))
        self.db.commit()
        with self.observations(), patch.object(sandbox, 'load_config', return_value={}), \
             patch.object(restart, 'scoped', return_value=[]), \
             patch.object(assignments, 'inspect', return_value=row), \
             patch.object(sandbox, 'original', return_value=request), \
             patch.object(sandbox, 'Docker', side_effect=RuntimeError('REACHED runtime boundary')):
            with self.assertRaisesRegex(RuntimeError, 'REACHED runtime boundary'):
                sandbox.launch(self.state, 'p', 'i', 'operator', row['assignment_id'], '/fixture', self.github)

    def test_unregistered_failed_unknown_health_never_admits_ordinary_dispatch(self):
        self.registration()
        self.item.pop('main_incident')
        self.item.pop('main_recoveries')
        self.save_item()
        for status in ('healthy', 'failed', 'unknown'):
            with self.subTest(status=status), self.observations(status):
                if status == 'healthy':
                    restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github)
                else:
                    with self.assertRaises(RepositoryError):
                        restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github)

    def test_resume_restores_control_not_ordinary_health_authority(self):
        self.registration()
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'explicit pause')
        with self.observations():
            result = controls.resume(self.db, 'p', 'i', 'operator', 'explicit resume', self.github)
        self.assertEqual(result['action'], 'resume')
        self.assertFalse(result['dispatch_allowed'])
        self.assertIsNotNone(self.stored()['main_incident'])

    def test_registration_change_during_final_tracker_read_is_not_dispatch_authority(self):
        request = self.registration()
        reads = []
        def tracker(db, item, api):
            reads.append(True)
            if len(reads) == 2:
                item['main_incident']['recovery_assignment'] = 'stale'
            return None
        with self.observations(), patch.object(restart, 'tracker', side_effect=tracker), self.assertRaises(RepositoryError):
            restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github, request=request)
        self.assertEqual(self.stored()['restart_reconciliation']['status'], 'blocked')

    def test_registered_reservation_only_reaches_its_own_frontier_boundary(self):
        self.registration()
        # The first frontier after restart is the reservation's own eligibility
        # guard, not a synthesis or worker precursor.
        with self.observations(), patch.object(tickets, 'frontier', side_effect=RuntimeError('REACHED reservation frontier')):
            with self.assertRaisesRegex(RuntimeError, 'REACHED reservation frontier'):
                tickets.reserve(self.db, self.stored(), self.github, 20)
            with self.assertRaises(RepositoryError):
                tickets.reserve(self.db, self.stored(), self.github, 10)
        self.assertNotIn('reservation', self.stored())

    def test_restart_tracker_preserves_repair_priority_over_parked_native_active_work(self):
        self.registration()
        self.item['ticket_work'] = {'publication_complete': True, 'frontier': {
            'foreign_active': [], 'active': [10, 20], 'items': []}}
        self.save_item()
        with patch.object(restart.repositories, 'reconcile'), \
             patch.object(tickets, 'frontier', side_effect=lambda db, item, api: item):
            self.assertEqual(restart.tracker(self.db, self.stored(), restart.Readback(self.github, 'owner/repo'))['active'], [10, 20])

    def test_scoped_descendant_and_diagnosis_launch_guard_rejects_stale_root_authority(self):
        request = self.registration()
        root = self.assignment()
        root.pop('runtime')
        root['handoff'] = request | {'recovery': recovery.contract(self.record)}
        root['ticket_scope']['issue_number'] = 20
        self.record['assignment'] = root['assignment_id']
        self.item['main_incident']['recovery_assignment'] = root['assignment_id']
        self.save_item()
        for stage in ('simplify', 'diagnosis'):
            descendant = copy.deepcopy(root)
            descendant['assignment_id'] = 'd' * 64
            descendant['handoff']['stage'] = stage
            descendant['handoff']['simplification'] = {'implementation_assignment': root['assignment_id']}
            descendant['handoff']['diagnosis'] = {'pins': {'stuck_assignment': root['assignment_id']}}
            for stale in (False, True):
                stored = copy.deepcopy(root)
                if stale:
                    stored['handoff']['recovery']['authority_sha256'] = '0' * 64
                self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(stored),))
                self.db.commit()
                with self.subTest(stage=stage, stale=stale), self.observations(), \
                     patch.object(restart, 'scoped', return_value=[]), \
                     patch.object(assignments, 'inspect', side_effect=lambda db, p, i, op, ident: descendant if ident == descendant['assignment_id'] else stored):
                    if stale:
                        with self.assertRaises(RepositoryError):
                            restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github, assignment_id=descendant['assignment_id'])
                    else:
                        restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github, assignment_id=descendant['assignment_id'])

    def test_parked_claim_still_requires_truthful_pending_effect_readback(self):
        self.registration()
        row = self.assignment()
        row.pop('runtime')
        row['publication'] = {'status': 'pending', 'approval': {'operator_id': 'operator'}}
        self.record['parked'] = [row['assignment_id']]
        self.save_item()
        self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),))
        self.db.commit()
        with self.observations(), patch.object(restart, 'candidate', side_effect=RepositoryError('publication_uncertain', 'unknown')):
            result = restart.reconcile(self.db, 'p', 'i', 'operator', self.github)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn({'boundary': row['assignment_id'] + ':publication', 'code': 'publication_uncertain'}, result['blockers'])
        self.assertEqual(assignments.rows(self.db)[0]['publication'], row['publication'])
