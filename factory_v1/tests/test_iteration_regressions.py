"""Issue 24 correction regressions: SQLite components, mocked external reads only."""
import copy
import json
import unittest
from unittest.mock import patch

from factory_v1 import assignments, attention, delivery, diagnosis, iteration, planning, requirements, tickets
from factory_v1.repositories import RepositoryError, save_iteration
from factory_v1.tests import test_iteration as fixtures


def fixture(owner, cls):
    value = cls(methodName='runTest')
    value.setUp()
    owner.addCleanup(value.doCleanups)
    return value


class CompletionRegressions(unittest.TestCase):
    def test_scope_and_raw_issue_drift_refuse_without_report_or_attention(self):
        for body in ('## Bug scope\ncurrent\n', '## Bug scope\nrecovery\n', '## Bug scope\ndeferred\n## Detail\nChanged\n'):
            with self.subTest(body=body):
                f = fixture(self, fixtures.IterationComponents)
                row = fixtures.work_row(2)
                row.update(status='deferred', held=True, issue_state='open')
                f.observed['items'].append(row)
                issue = {'body': '## Bug scope\ndeferred\n', 'state': 'open'}
                f.mocks['tickets.gh.read_issue'].side_effect = lambda api, repo, n: copy.deepcopy(issue) if n == 2 else {'body': 'accepted', 'state': 'closed'}
                def observe(config):
                    issue['body'] = body
                    return f.health
                f.mocks['delivery.observe'].side_effect = observe
                with self.assertRaises(RepositoryError):
                    f.complete()
                self.assertNotIn('completion', f.stored())
                self.assertEqual(attention.records(f.db, 'product', 'm1', f.item), [])


class DeliveryContinuationRegressions(unittest.TestCase):
    def test_failed_unknown_and_later_old_samples_reach_new_mutation_boundary(self):
        from factory_v1.tests.test_delivery import configuration, DeliveryTarget
        config = configuration('http://127.0.0.1:9876')
        for status in ('failed', 'unknown', 'healthy'):
            with self.subTest(status=status):
                f = fixture(self, fixtures.ContinuationComponents)
                target = DeliveryTarget(config)
                with patch.object(delivery, 'http_json', side_effect=lambda url, timeout: target.value('/health')):
                    healthy = delivery.observe(config, api=type('API', (), {'read': lambda self, suffix: target.value(suffix)})())
                self.assertEqual(healthy['status'], 'healthy')
                failed = dict(healthy, status=status, failure=None if status == 'healthy' else 'delivery_unknown')
                with patch.object(delivery, 'observe', return_value=failed):
                    delivery.sample(f.db, 'product', 'm1', '42', config)
                    before = delivery.context(f.db, 'product', 'm1', '42')
                    started = f.start()
                    self.assertEqual(started.get('delivery_monitor'), before['delivery_monitor'])
                    self.assertEqual(started.get('main_incident'), before.get('main_incident'))
                    boundary = delivery.mutation_boundary(f.db, 'product', 'm2', '42')
                    self.assertEqual(boundary.get('main_incident'), before.get('main_incident'))
                    self.assertEqual(attention.records(f.db, 'product', 'm2', started), [])
                later = dict(healthy, status='unknown', failure='delivery_unknown')
                with patch.object(delivery, 'observe', return_value=later):
                    delivery.sample(f.db, 'product', 'm1', '42')
                current = delivery.inspect(f.db, 'product', 'm2', '42')
                self.assertEqual(current['monitor']['latest']['status'], 'unknown')
                self.assertIsNotNone(current['incident'])
                with patch.object(delivery, 'observe', return_value=healthy):
                    delivery.sample(f.db, 'product', 'm1', '42')
                self.assertIsNone(delivery.inspect(f.db, 'product', 'm2', '42')['incident'])
                self.assertEqual(f.start()['delivery_monitor']['latest']['status'], 'healthy')
