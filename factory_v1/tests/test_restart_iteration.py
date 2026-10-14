"""Restart/current-main integration: direct retained rows, no worker journeys."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from factory_v1 import assignments, attention, controls, delivery, restart
from factory_v1.repositories import RepositoryError, save_iteration
from factory_v1.tests import test_iteration as iteration_fixtures
from factory_v1.tests import test_restart as restart_fixtures
from factory_v1.tests import test_restart_effects as effect_fixtures
from factory_v1.tests.test_delivery import DeliveryTarget, configuration
from factory_v1.tests.test_iteration_regressions import fixture


class RestartIterationTests(unittest.TestCase):
    def test_nonactive_read_preserves_terminal_status_and_active_admission_refuses(self):
        f = fixture(self, restart_fixtures.RestartTests)
        for status in ('waiting-for-operator', 'paused', 'failed', 'stopped'):
            with self.subTest(status=status):
                f.item['status'] = status
                f.save_item()
                self.assertEqual(assignments.current(f.db, 'p', 'i', 'operator', require_active=False), f.item)
                with self.assertRaises(RepositoryError):
                    assignments.current(f.db, 'p', 'i', 'operator')
                self.assertEqual(f.stored(), f.item)

    def test_completed_predecessor_claims_do_not_conflict_with_current_ownership(self):
        f = fixture(self, restart_fixtures.RestartTests)
        old = f.assignment('complete')
        old['runtime']['container_removed'] = True
        report = {'delivered': [{'issue': {'number': 10, 'id': 100}}]}
        f.item.update(status='waiting-for-operator', completion={'report_id': assignments.digest(report), 'report': report})
        f.save_item()
        fresh = copy.deepcopy(old)
        fresh['assignment_id'] = 'd' * 64
        fresh['claim_id'] = 'next-ticket'
        fresh['ticket_scope'].update(iteration='next', issue_number=11, issue_id=110)
        fresh.pop('runtime')
        for row in (old, fresh):
            f.db.execute('INSERT OR REPLACE INTO role_assignments VALUES (?,?)', (row['assignment_id'], json.dumps(row)))
        f.db.commit()
        self.assertEqual(assignments.ticket_claims(f.db, fresh['ticket_scope']), [fresh])
        self.assertEqual(restart.ownership(f.db, f.item), {'assignments': [fresh['assignment_id']], 'profiles': ['writer']})
        old['runtime']['container_removed'] = False
        f.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(old), old['assignment_id']))
        f.db.commit()
        with self.assertRaises(RepositoryError):
            restart.ownership(f.db, f.item)

    def test_restart_retains_delivered_receipts_without_granting_new_delivery(self):
        f = fixture(self, effect_fixtures.CandidateRestartTests)
        f.merged()  # Pure retained journal/ref/check dictionaries.
        restart.integration(f.assignment, f.api)
        journal = f.assignment['merge']
        journal.update(status='delivered', close_allowed=True, delivery_checks=['historical-integrated-checks'])
        before = copy.deepcopy(journal)
        restart.integration(f.assignment, f.api)
        self.assertEqual(journal, before)
        checks = f.values['/commits/' + 'f' * 40 + '/check-runs?per_page=100&filter=latest']
        checks['check_runs'][0]['conclusion'] = 'failure'
        with self.assertRaises(RepositoryError):
            restart.integration(f.assignment, f.api)
        self.assertEqual(journal, before)
        f.api.write.assert_not_called()
        f.api.issue_state.assert_not_called()

    def test_restart_health_routes_to_successor_without_rewriting_predecessor_pointers(self):
        f = fixture(self, iteration_fixtures.ContinuationComponents)
        config = configuration('http://127.0.0.1:9876')
        target = DeliveryTarget(config)
        with patch.object(delivery, 'http_json', side_effect=lambda *_: target.value('/health')):
            healthy = delivery.observe(config, api=Mock(read=target.value))
        with patch.object(delivery, 'observe', return_value=healthy):
            delivery.sample(f.db, 'product', 'm1', '42', config)
        previous = delivery.context(f.db, 'product', 'm1', '42')
        successor = copy.deepcopy(previous)
        successor.update(iteration_id='m2', status='active', previous_iteration={'iteration_id': 'm1'})
        successor['correlation']['iteration_id'] = 'm2'
        successor.pop('completion')
        delivery.inherit_monitor(f.db, previous, successor, '42')
        f.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm2', json.dumps(successor)))
        previous['next_iteration'] = {'iteration_id': 'm2'}
        save_iteration(f.db, previous)
        f.db.commit()
        unknown = dict(healthy, status='unknown', failure='delivery_unknown')
        with patch.object(restart, 'tracker', return_value={}), patch.object(delivery, 'observe', return_value=unknown):
            result = restart.reconcile(f.db, 'product', 'm1', '42', Mock(base=config['api_base']))
        self.assertIn({'boundary': 'delivery', 'code': 'main_frozen'}, result['blockers'])
        old = delivery.context(f.db, 'product', 'm1', '42')
        new = delivery.context(f.db, 'product', 'm2', '42')
        self.assertEqual(old['delivery_monitor'], previous['delivery_monitor'])
        self.assertNotIn('main_incident', old)
        self.assertEqual(new['delivery_monitor']['latest']['status'], 'unknown')
        self.assertIsNotNone(new['main_incident'])
        delivery.inspect(f.db, 'product', 'm1', '42')
        delivery.inspect(f.db, 'product', 'm2', '42')
        self.assertEqual(len(attention.records(f.db, 'product', 'm2', new)), 1)

    def test_superseded_assignment_is_not_revalidated_against_new_authority(self):
        f = fixture(self, restart_fixtures.RestartTests)
        old = f.assignment('complete')
        old['runtime']['container_removed'] = True
        old['requirement_disposition'] = {'change_id': 'revision'}
        f.item['requirement_changes'] = {'revision': {'kind': 'material', 'status': 'resumed', 'affected': [10], 'blocks_verified': True}}
        f.item['requirement_authority'] = {'10': {'spec': {'commit': 'e' * 40}, 'ticket_assignment': 'new', 'ticket_inputs': 'f' * 64}}
        f.save_item()
        f.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(old),))
        f.db.commit()
        with patch('factory_v1.requirements.revalidate_inputs', side_effect=AssertionError('Obsolete authority must stay historical')):
            result = f.observed_restart()
        self.assertEqual(result['status'], 'ready', result['blockers'])
        self.assertEqual(assignments.rows(f.db)[0]['revision'], old['revision'])
        self.assertEqual(f.stored()['requirement_authority'], f.item['requirement_authority'])
        self.assertEqual(restart.ownership(f.db, f.item)['assignments'], [])

    def test_resume_preserves_delivery_owned_incident_snapshot(self):
        f = fixture(self, restart_fixtures.RestartTests)
        incident = {'status': 'active', 'incident_id': 'repair-incident', 'recovery_assignment': 'c' * 64, 'recovery_issue': 10}
        f.item.update(delivery_monitor={'SIMULATED': 'registered observer'}, main_incident=incident)
        f.save_item()
        controls.request(f.db, 'p', 'i', 'operator', 'pause', 'intentional')
        with patch.object(restart, 'tracker', return_value={}), \
             patch.object(delivery, 'sample', return_value={'evidence': {'status': 'healthy'}}):
            controls.resume(f.db, 'p', 'i', 'operator', 'explicit', f.github)
        self.assertEqual(f.stored()['main_incident'], incident)
        self.assertFalse(controls.blocked(f.db, 'p', 'i'))
        self.assertEqual(f.stored()['status'], 'active')
