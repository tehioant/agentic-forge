"""MOCK ONLY: public subprocess lifecycle on a three-state board and native milestones."""
import copy
import json
import threading
import subprocess
import sys
import os
import unittest
from pathlib import Path

from factory_v1.tests import test_tickets


class NativeScopeTests(unittest.TestCase):
    def setUp(self):
        self.case = test_tickets.TicketTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.case.fields = [{'id': 'STATUS', 'name': 'Status', 'options': [
            {'id': name, 'name': name} for name in ('Todo', 'In Progress', 'Done')]}]
        self.case.start_request['tracker'] = {
            'project_id': None, 'status_field': 'Status',
            'statuses': {'ready': 'Todo', 'active': 'In Progress', 'done': 'Done'},
            'triage_label': 'agent-triage'}
        self.milestones = [{'id': 501, 'number': 1, 'title': 'm1', 'state': 'open'}]
        self.scope_inaccessible = False
        self.crash_kind = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.posted_milestone = None
        parent = self.case.fixture.server.RequestHandlerClass
        test = self

        class Handler(parent):
            def do_GET(self):
                if self.path.startswith('/repos/example/product/milestones?'):
                    test.case.fixture.calls.append(('GET', self.path))
                    self.reply(403 if test.scope_inaccessible else 200, test.milestones)
                    return
                super().do_GET()

            def do_POST(self):
                if self.path == '/repos/example/product/issues':
                    import io
                    raw = self.rfile.read(int(self.headers['Content-Length']))
                    test.posted_milestone = json.loads(raw).get('milestone')
                    self.rfile = io.BytesIO(raw)
                super().do_POST()
                if self.path == '/repos/example/product/issues' and test.case.issues:
                    issue = test.case.issues[max(test.case.issues)]
                    issue['milestone'] = copy.deepcopy(next((m for m in test.milestones
                                                           if m['number'] == test.posted_milestone), None))

            def reply(self, status, value):
                if self.path == '/repos/example/product/issues' and isinstance(value, dict) and 'number' in value:
                    value['milestone'] = copy.deepcopy(next((m for m in test.milestones
                                                           if m['number'] == test.posted_milestone), None))
                kind = None
                if self.path == '/repos/example/product/issues':
                    kind = 'issue'
                if self.path == '/graphql' and isinstance(value, dict):
                    data = value.get('data', {})
                    if 'addProjectV2ItemById' in data:
                        kind = 'membership'
                    if 'updateProjectV2ItemFieldValue' in data:
                        kind = 'field'
                if kind and test.crash_kind == kind:
                    test.crash_kind = None
                    test.entered.set()
                    test.release.wait(timeout=10)
                    self.close_connection = True
                    return
                super().reply(status, value)
        self.case.fixture.server.RequestHandlerClass = Handler

    def test_three_state_publication_frontier_and_one_reservation(self):
        item = self.case.publish()
        self.assertEqual(item['ticket_work']['status'], 'published')
        self.assertEqual(self.case.deps, {11: [10]})
        self.assertEqual(set(self.case.members), {10, 11, 12})
        self.assertTrue(all(m['fields'] == {'STATUS': 'Todo'} for m in self.case.members.values()))
        self.assertTrue(all(i['milestone']['id'] == 501 for i in self.case.issues.values()))
        self.assertEqual(item['ticket_work']['frontier']['eligible'], [10, 12])
        self.assertEqual(self.case.control('reserve-ticket', issue=11).returncode, 2)
        reserved = self.case.ok(self.case.control('reserve-ticket', issue=10))
        self.assertEqual(self.case.members[10]['fields'], {'STATUS': 'In Progress'})
        self.assertEqual(self.case.control('reserve-ticket', issue=12).returncode, 2)
        self.assertEqual(self.case.ok(self.case.control('reserve-ticket', issue=10))['reservation'], reserved['reservation'])
        self.assertEqual(self.case.ok(self.case.control('publish-tickets'))['ticket_work']['frontier']['active'], [10])
        self.assertEqual(len(self.case.issues), 3)
        self.assertFalse(reserved['execution_allowed'])
        self.assertIsNone(reserved['correlation']['worker_run_id'])

    def test_blocked_deferred_foreign_work_does_not_hide_independent_frontier(self):
        self.case.publish()
        ready = 'ready (GitHub Projects is authoritative for current progress).'
        self.case.issues[10]['body'] = self.case.issues[10]['body'].replace(ready, 'blocked')
        self.assertEqual(self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'], [12])
        self.assertEqual(self.case.control('reserve-ticket', issue=10).returncode, 2)
        self.case.issues[10]['body'] = self.case.issues[10]['body'].replace('## Status\n\nblocked', '## Status\n\ndeferred')
        self.assertEqual(self.case.control('reserve-ticket', issue=10).returncode, 2)
        self.case.issues[10]['milestone'] = {'id': 502, 'number': 2, 'title': 'm2', 'state': 'open'}
        self.assertEqual(self.case.control('reserve-ticket', issue=10).returncode, 2)
        self.assertEqual(self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'], [12])
        self.assertEqual(self.case.ok(self.case.control('reserve-ticket', issue=12))['reservation']['status'], 'reserved')

    def test_done_requires_closed_completed_and_current_native_scope(self):
        self.case.publish()
        self.case.members[10]['fields']['STATUS'] = 'Done'
        self.assertNotIn(11, self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'])
        self.case.issues[10].update(state='closed', state_reason='completed')
        self.assertIn(11, self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'])
        self.case.issues[10]['milestone']['id'] = 999
        self.assertEqual(self.case.control('frontier').returncode, 2)

    def test_missing_inaccessible_ambiguous_scope_blocks_before_issue_writes(self):
        request = self.case.completion()
        original = copy.deepcopy(self.milestones)
        for defect in ('missing', 'ambiguous', 'inaccessible'):
            with self.subTest(defect=defect):
                self.milestones = [] if defect == 'missing' else original + ([dict(original[0], id=502, number=2)] if defect == 'ambiguous' else [])
                self.scope_inaccessible = defect == 'inaccessible'
                item = self.case.ok(self.case.control('complete-tickets', request))
                self.assertEqual(item['ticket_work']['status'], 'blocked')
                self.assertEqual(self.case.issues, {})
        self.milestones = original
        self.scope_inaccessible = False
        self.assertEqual(self.case.ok(self.case.control('publish-tickets'))['ticket_work']['status'], 'published')
        self.case.issues[12]['milestone'] = None
        self.assertEqual(self.case.control('frontier').returncode, 2)

    def test_missing_ambiguous_inaccessible_board_preserves_synthesis(self):
        self.case.test_missing_ambiguous_inaccessible_or_changed_board_is_durable_blocker()

    def test_lost_acknowledgements_reconcile_exact_mock_targets(self):
        self.case.test_lost_issue_response_reconciles_without_duplicate()
        self.assertTrue(all(i['milestone']['id'] == 501 for i in self.case.issues.values()))

    def test_membership_dependency_and_progress_lost_acknowledgements(self):
        self.case.test_lost_native_dependency_membership_and_field_responses_reconcile()
        self.assertTrue(all(m['fields'] == {'STATUS': 'Todo'} for m in self.case.members.values()))

    def test_crash_after_board_write_reconciles_persisted_intent(self):
        request = self.case.completion()
        path = self.case.fixture.root / 'crash.json'
        path.write_text(json.dumps(request))
        self.crash_kind = 'membership'
        command = [sys.executable, '-m', 'factory_v1', '--state', str(self.case.fixture.state), '--operator-id', '42',
                   'complete-tickets', '--project', 'product', '--iteration', 'm1',
                   '--api-base', self.case.fixture.base, '--request', str(path)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        try:
            self.assertTrue(self.entered.wait(timeout=5))
            process.kill()
            process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            self.release.set()
        self.assertEqual(len(self.case.members), 1)
        self.assertEqual(self.case.ok(self.case.control('publish-tickets'))['ticket_work']['status'], 'published')
        self.assertEqual(len(self.case.members), 3)
        self.assertEqual(len(self.case.issues), 3)

    def test_crash_after_issue_write_reconciles(self):
        self.case.test_process_death_after_issue_write_reconciles_committed_intent()

    def test_reservation_lost_response_is_durable(self):
        self.case.test_reservation_lost_response_is_durable_and_reconciles()
