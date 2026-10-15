"""Restart/current-main integration: direct retained rows, no worker journeys."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from factory_v1 import assignments, attention, controls, delivery, restart
from factory_v1.repositories import RepositoryError, save_iteration
from factory_v1.tests import test_iteration as iteration_fixtures
from factory_v1.tests import test_restart as restart_fixtures
from factory_v1.tests import test_restart_recovery as recovery_fixtures
from factory_v1.tests import test_restart_effects as effect_fixtures
from factory_v1.tests.test_delivery import DeliveryTarget, configuration
from factory_v1.tests.test_iteration_regressions import fixture, seed_monitor


class RestartIterationTests(unittest.TestCase):
    def test_terminal_predecessor_and_current_ownership_matrix(self):
        f = fixture(self, restart_fixtures.RestartTests)
        for status in ('waiting-for-operator', 'paused', 'failed', 'stopped'):
            with self.subTest(status=status):
                f.item['status'] = status
                f.save_item()
                self.assertEqual(assignments.current(f.db, 'p', 'i', 'operator', require_active=False), f.item)
                with self.assertRaises(RepositoryError):
                    assignments.current(f.db, 'p', 'i', 'operator')
                self.assertEqual(f.stored(), f.item)
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
        f.db.execute('DELETE FROM role_assignments')
        f.db.commit()
        old['runtime']['container_removed'] = True
        old['requirement_disposition'] = {'change_id': 'revision'}
        f.item.update(status='active')
        f.item.pop('completion')
        f.item['requirement_changes'] = {'revision': {'kind': 'material', 'status': 'resumed', 'affected': [10], 'blocks_verified': True}}
        f.item['requirement_authority'] = {'10': {'spec': {'commit': 'e' * 40}, 'ticket_assignment': 'new', 'ticket_inputs': 'f' * 64}}
        f.save_item()
        f.db.execute('INSERT INTO role_assignments VALUES (?,?)', (old['assignment_id'], json.dumps(old)))
        f.db.commit()
        with patch('factory_v1.requirements.revalidate_inputs', side_effect=AssertionError('Obsolete authority must stay historical')):
            result = f.observed_restart()
        self.assertEqual(result['status'], 'ready', result['blockers'])
        self.assertEqual(assignments.rows(f.db)[0]['revision'], old['revision'])
        self.assertEqual(f.stored()['requirement_authority'], f.item['requirement_authority'])
        self.assertEqual(restart.ownership(f.db, f.item)['assignments'], [])



    def test_retained_delivery_and_monitor_scope_matrix(self):
        f = fixture(self, iteration_fixtures.ContinuationComponents)
        config = configuration('http://127.0.0.1:9876')
        from factory_v1.tests.test_recovery import RecoveryFixture
        source = fixture(self, RecoveryFixture)
        source.config = config
        healthy = source.evidence(True)
        seed_monitor(f, config, healthy)
        previous = delivery.context(f.db, 'product', 'm1', '42')
        successor = copy.deepcopy(previous)
        successor.update(iteration_id='m2', status='active', previous_iteration={'iteration_id': 'm1'})
        successor['correlation']['iteration_id'] = 'm2'
        successor.pop('completion')
        f.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm2', json.dumps(successor)))
        seed_monitor(f, config, healthy, successor)
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
