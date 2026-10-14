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
