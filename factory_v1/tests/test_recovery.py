"""Small recovery components; external tracker/health/execution seams are mocked."""

import json
import sqlite3
import unittest
from unittest.mock import patch

from factory_v1 import assignments, delivery
from factory_v1.repositories import save_iteration


class RecoveryFixture(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT, PRIMARY KEY(project_id, iteration_id))')
        self.item = {'project_id': 'p', 'iteration_id': 'i', 'repository': 'example/product',
                     'approval': {'operator_id': '42', 'reference': 'test'},
                     'correlation': {'project_id': 'p', 'iteration_id': 'i', 'control_run_id': 'run', 'evidence_id': 'evidence', 'stage': 'delivery'},
                     'origin': {'platform': 'discord', 'chat_id': '123', 'thread_id': '123', 'parent_chat_id': '456', 'scope_id': '789'}}
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(self.item)))
        self.db.commit()
        self.config = {'operator_id': '42', 'repository': 'example/product', 'repository_id': 7,
                       'api_base': 'https://api.github.com', 'issue': 18,
                       'required_checks': [{'context': 'quality', 'app_id': 99}],
                       'required_workflows': [{'id': 11, 'event': 'push'}], 'environment': 'test', 'timeout': 2,
                       'contract': {'reference': 'test-only', 'revision': 'a' * 40,
                                    'health_url': 'https://example.test/health', 'acceptance': {'journey': True}}}

    def evidence(self, healthy):
        config = self.config
        expected = delivery._expected_health(config, 'a' * 40, 21)
        return {'repository': 'example/product', 'repository_id': 7, 'main': 'a' * 40,
                'checks': [{'id': 31, 'name': 'quality', 'app_id': 99, 'head_sha': 'a' * 40,
                            'status': 'completed', 'conclusion': 'success'}],
                'workflows': [{'id': 41, 'workflow_id': 11, 'head_sha': 'a' * 40, 'head_branch': 'main',
                               'event': 'push', 'status': 'completed', 'conclusion': 'success'}],
                'deployment': {'id': 21, 'sha': 'a' * 40, 'ref': 'main', 'environment': 'test',
                               'status': {'id': 51, 'state': 'success'}},
                'health': {'expected': expected, 'matched': healthy, 'response_sha256': assignments.digest(expected)},
                'status': 'healthy' if healthy else 'failed', 'failure': None if healthy else 'health_failed'}

    def sample(self, healthy):
        with patch.object(delivery, 'observe', return_value=self.evidence(healthy)):
            return delivery.sample(self.db, 'p', 'i', '42', self.config)

    def stored(self):
        return delivery.context(self.db, 'p', 'i', '42')

class RecoveryObservationTests(RecoveryFixture):
    def test_healthy_sample_cannot_clear_registered_repair_before_merge_and_reopening(self):
        self.sample(False)
        item = self.stored()
        item['main_incident'].update(recovery_assignment='b' * 64, recovery_issue=20)
        with self.db:
            save_iteration(self.db, item)
            self.db.execute('UPDATE delivery_incidents SET payload=?', (json.dumps(item['main_incident']),))
        observed = self.sample(True)
        self.assertIsNotNone(observed['incident'], 'Healthy polling must not bypass registered repair delivery/reopen proof')
        self.assertEqual(observed['incident']['latest_evidence'], assignments.digest(self.evidence(True)))
        delivery.inspect(self.db, 'p', 'i', '42')

    def test_unregistered_transient_incident_still_clears_on_verified_health(self):
        self.sample(False)
        self.assertIsNone(self.sample(True)['incident'])
