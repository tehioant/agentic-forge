"""Recovery/completion integration components; no worker or external service."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from factory_v1 import assignments, attention, delivery, recovery
from factory_v1.repositories import RepositoryError, save_iteration
from factory_v1.tests import test_iteration as fixtures
from factory_v1.tests import test_recovery as recovery_fixtures
from factory_v1.tests.test_iteration_regressions import fixture, seed_monitor


class RecoveryIterationTests(unittest.TestCase):
    def setUp(self):
        for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket'):
            blocker = patch(target, side_effect=AssertionError('Unexpected external boundary: ' + target))
            blocker.start()
            self.addCleanup(blocker.stop)

    def monitor(self, f):
        source = fixture(self, recovery_fixtures.RecoveryFixture)
        f.item = seed_monitor(f, source.config, source.evidence(False))
        return source

    def test_pending_recovery_blocks_next_and_completion_matrix(self):
        f = fixture(self, fixtures.ContinuationComponents)
        self.monitor(f)
        baseline = delivery.context(f.db, 'product', 'm1', '42')
        for pointer in (True, False):
            with self.subTest(incident_pointer=pointer):
                item = copy.deepcopy(baseline)
                item['main_recoveries'] = {item['main_incident']['incident_id']: {'status': 'registered'}}
                if pointer:
                    incident = item['main_incident']
                    incident.update(recovery_assignment='c' * 64, recovery_issue=20)
                    with f.db:
                        f.db.execute('UPDATE delivery_incidents SET payload=?', (json.dumps(incident),))
                else:
                    item.pop('main_incident')
                with f.db:
                    save_iteration(f.db, item)
                before = copy.deepcopy(item)
                with self.assertRaises(RepositoryError) as caught:
                    f.start()
                self.assertEqual(caught.exception.code, 'recovery_held')
                self.assertEqual(delivery.context(f.db, 'product', 'm1', '42'), before)
                self.assertEqual(f.db.execute('SELECT COUNT(*) FROM iterations').fetchone()[0], 1)
                self.assertEqual(attention.records(f.db, 'product', 'm2'), [])
                successor = {'project_id': 'product', 'iteration_id': 'm2'}
                with self.assertRaises(RepositoryError) as caught:
                    delivery.inherit_monitor(f.db, item, successor, '42')
                self.assertEqual(caught.exception.code, 'recovery_held')
                self.assertEqual(successor, {'project_id': 'product', 'iteration_id': 'm2'})
        completed = fixture(self, fixtures.IterationComponents)
        completed.item['main_recoveries'] = {'incident': {'status': 'registered'}}
        completed.store()
        with self.assertRaises(RepositoryError):
            completed.complete()
        self.assertNotIn('completion', completed.stored())
        completed.mocks['delivery.observe'].assert_not_called()


    def test_predecessor_poll_preserves_authority_matrix(self):
        f = fixture(self, fixtures.ContinuationComponents)
        source = self.monitor(f)
        old = delivery.context(f.db, 'product', 'm1', '42')
        started = seed_monitor(f, source.config, source.evidence(False), f.pending_interview(), old['main_incident'], notify=False)
        pin = {'number': 20, 'id': 200, 'node_id': 'I20', 'body_sha256': 'd' * 64}
        config = {'operator_id': '42', 'reference': 'SIMULATED host scope', 'incident_id': started['main_incident']['incident_id'], 'evidence_id': started['main_incident']['latest_evidence'], 'issue': pin, 'paths': ['app.py'], 'removable_assignments': []}
        registered = {'config': config, 'evidence': source.evidence(False), 'assignment': None, 'status': 'registered', 'parked': [], 'removable': {}}
        started['main_recoveries'] = {config['incident_id']: registered}
        started['main_incident'].update(recovery_assignment=assignments.digest(config), recovery_issue=20)
        with f.db:
            save_iteration(f.db, started)
            f.db.execute('UPDATE delivery_incidents SET payload=? WHERE iteration=?', (json.dumps(started['main_incident']), 'm2'))
        with patch.object(delivery, 'observe', return_value=source.evidence(True)):
            sampled = delivery.sample(f.db, 'product', 'm1', '42')
            boundary = delivery.mutation_boundary(f.db, 'product', 'm2', '42')
        self.assertEqual(recovery.active(boundary), registered)
        self.assertEqual(sampled['incident']['recovery_issue'], 20)
        with self.assertRaises(RepositoryError) as caught:
            recovery.admission(boundary, 'implementation', 1)
        self.assertEqual(caught.exception.code, 'main_frozen')
        self.assertEqual(delivery.context(f.db, 'product', 'm1', '42'), dict(old, next_iteration={'iteration_id': 'm2', 'request': f.request}))
        self.assertEqual(len(attention.records(f.db, 'product', 'm1', old)), 1)
        self.assertEqual(attention.records(f.db, 'product', 'm2', boundary), [])


    def test_released_claim_not_worker_absence_matrix(self):
        f = fixture(self, fixtures.IterationComponents)
        f.item['main_recoveries'] = {'incident': {'status': 'cleared'}}
        f.store()
        for status in ('complete', 'failed', 'stopped'):
            for removed in (False, None):
                with self.subTest(status=status, removed=removed):
                    assignment = {'assignment_id': 'repair', 'ticket_scope': {'project': 'product', 'iteration': 'm1'}, 'handoff': {'recovery': {'incident_id': 'incident'}}, 'runtime': {'status': status, 'container_removed': removed}}
                    self.assertTrue(recovery.released_claim(f.db, assignment))
                    with patch.object(assignments, 'rows', return_value=[assignment]), self.assertRaises(RepositoryError):
                        f.complete()
                    self.assertNotIn('completion', f.stored())


if __name__ == '__main__':
    unittest.main()
