"""Standards regressions through the public CLI and fixture HTTP tracker."""
import copy
import json
import unittest

from factory_v1.tests import test_tickets

hash_json = test_tickets.hash_json


class StandardsTests(unittest.TestCase):
    setUp = test_tickets.TicketTests.setUp
    control = test_tickets.TicketTests.control
    ok = test_tickets.TicketTests.ok
    inspect = test_tickets.TicketTests.inspect
    completion = test_tickets.TicketTests.completion
    publish = test_tickets.TicketTests.publish

    def test_generated_oversized_requirement_is_safe_refusal(self):
        request = self.completion()
        before = self.inspect()
        request['tickets'][0]['references'][0]['requirement'] = 'US-' + '9' * 5000
        request['execution']['result_digest'] = hash_json(request['tickets'])
        result = self.control('complete-tickets', request)
        self.assertEqual(result.returncode, 2, result.stderr)
        error = json.loads(result.stderr)
        self.assertTrue(error['message'])
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.inspect(), before)
        self.assertEqual(self.issues, {})

    def test_authoritative_oversized_requirement_preserves_independent_frontier(self):
        self.publish()
        self.issues[12]['body'] = self.issues[12]['body'].replace('US-1', 'US-' + '9' * 5000)
        snapshot = self.ok(self.control('frontier'))['ticket_work']['frontier']
        self.assertEqual(snapshot['eligible'], [10])
        self.assertFalse(next(r for r in snapshot['items'] if r['number'] == 12)['contract_valid'])
        result = self.control('reserve-ticket', issue=12)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], 'ticket_ineligible')
        self.assertEqual(self.members[12]['fields']['STATUS'], 'Ready')
        self.assertEqual(self.ok(self.control('reserve-ticket', issue=10))['reservation']['status'], 'reserved')

    def test_authoritative_oversized_textual_blocker_is_safe_refusal(self):
        self.publish()
        before = self.inspect()
        original = self.issues[12]['body']
        self.issues[12]['body'] = original.replace('None (can start immediately).', '- #' + '9' * 5000)
        result = self.control('frontier')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], 'github_mismatch')
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(self.inspect(), before)
        self.issues[12]['body'] = original
        self.assertEqual(self.ok(self.control('frontier'))['ticket_work']['frontier']['eligible'], [10, 12])

    def test_deep_generated_graph_and_guards(self):
        request = self.completion()
        template = request['tickets'][2]
        batch = []
        for n in range(1100):
            ticket = copy.deepcopy(template)
            ticket.update(key='chain-' + str(n), blockers=['chain-' + str(n + 1)] if n < 1099 else [])
            batch.append(ticket)
        self.boards = []  # Validate synthesis without publishing 1100 fixture issues.
        before = self.inspect()
        for blocker in ('chain-0', 'absent'):
            bad = copy.deepcopy(request)
            bad['tickets'] = copy.deepcopy(batch)
            bad['tickets'][-1]['blockers'] = [blocker]
            bad['execution']['result_digest'] = hash_json(bad['tickets'])
            result = self.control('complete-tickets', bad)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertTrue(json.loads(result.stderr)['message'])
            self.assertEqual(self.inspect(), before)
        request['tickets'] = batch
        request['execution']['result_digest'] = hash_json(batch)
        work = self.ok(self.control('complete-tickets', request))['ticket_work']
        self.assertEqual(work['status'], 'blocked')
        self.assertEqual(work['blocker']['code'], 'projects_blocked')
        self.assertEqual([t['key'] for t in work['tickets']], ['chain-' + str(n) for n in reversed(range(1100))])
        self.assertEqual(self.issues, {})

    def test_deep_authoritative_graph_cycle_and_unknown_guards(self):
        self.publish()
        template = copy.deepcopy(self.issues[12])
        for n in range(20, 1120):
            issue = copy.deepcopy(template)
            issue.update(number=n, id=1000 + n, node_id='I' + str(n), html_url='https://github.com/example/product/issues/' + str(n))
            if n < 1119:
                issue['body'] = issue['body'].replace('None (can start immediately).', '- #' + str(n + 1))
                self.deps[n] = [n + 1]
            self.issues[n] = issue
            self.members[n] = {'id': 'ITEM' + str(n), 'fields': {'STATUS': 'Ready', 'SCOPE': 'M1'}}
        snapshot = self.ok(self.control('frontier'))['ticket_work']['frontier']
        self.assertEqual(snapshot['eligible'], [10, 12, 1119])
        self.assertEqual(len(snapshot['items']), 1103)
        before = self.inspect()
        self.deps[1119] = [20]
        self.issues[1119]['body'] = template['body'].replace('None (can start immediately).', '- #20')
        result = self.control('frontier')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], 'github_mismatch')
        self.assertEqual(self.inspect(), before)
        self.issues[2000] = {**template, 'number': 2000, 'id': 3000, 'node_id': 'I2000', 'html_url': 'https://github.com/example/product/issues/2000'}
        self.deps[1119] = [2000]
        self.issues[1119]['body'] = template['body'].replace('None (can start immediately).', '- #2000')
        self.assertEqual(self.ok(self.control('frontier'))['ticket_work']['frontier']['eligible'], [10, 12])
        self.assertEqual(self.ok(self.control('reserve-ticket', issue=12))['reservation']['status'], 'reserved')
