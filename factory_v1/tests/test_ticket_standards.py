"""Bounded semantic and deep-graph regressions at real ticket validation seams."""
import copy

from factory_v1 import repositories, tickets
from factory_v1.tests import test_lean_tickets


class StandardsTests(test_lean_tickets.LeanControllerCase):
    def ticket(self):
        return {'key': 'notes', 'title': 'Retain notes', 'desired_behavior': 'Reopen café unchanged.',
                'references': [{'spec': self.inputs['documents']['milestone.md']['url'], 'requirement': 'US-1'}],
                'acceptance_criteria': ['Readers reopen the exact retained note.'],
                'blockers': [], 'status': 'ready', 'iteration': 'm1', 'triage_label': 'ready-for-agent'}

    def validate(self, batch):
        return tickets.validate_batch(batch, self.inputs, 'm1', 'ready-for-agent')

    def test_generated_oversized_requirement_is_safe_refusal(self):
        ticket, before = self.ticket(), self.stored()
        ticket['references'][0]['requirement'] = 'US-' + '9' * 5000
        with self.assertRaises(repositories.RepositoryError) as error:
            self.validate([ticket])
        self.assertTrue(str(error.exception))
        self.assertEqual(self.stored(), before)
        self.assert_no_external_writes()

    def test_authoritative_oversized_requirement_preserves_independent_frontier(self):
        self.issues[12]['body'] = self.canonical_body.replace('US-1', 'US-' + '9' * 5000)
        snapshot = self.frontier()
        self.assertEqual(snapshot['eligible'], [10])
        self.assertFalse(next(r for r in snapshot['items'] if r['number'] == 12)['contract_valid'])
        self.assert_no_external_writes()
        before = self.stored()
        with self.assertRaises(repositories.RepositoryError) as error:
            self.reserve(12)
        self.assertEqual(error.exception.code, 'ticket_ineligible')
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.members[12]['fieldValues']['nodes'][0]['optionId'], 'Ready')
        self.assertEqual(self.reserve(10)['reservation']['status'], 'reserved')

    def test_authoritative_oversized_textual_blocker_is_safe_refusal(self):
        before = self.stored()
        original = self.canonical_body
        self.issues[12]['body'] = original.replace('None (can start immediately).', '- #' + '9' * 5000)
        self.assertNotEqual(self.issues[12]['body'], original)
        with self.assertRaises(repositories.RepositoryError) as error:
            self.frontier()
        self.assertEqual(error.exception.code, 'github_mismatch')
        self.assertEqual(self.stored(), before)
        self.assert_no_external_writes()
        self.issues[12]['body'] = original
        self.assertEqual(self.frontier()['eligible'], [10, 12])

    def test_deep_generated_graph_and_guards(self):
        template = self.ticket()
        batch = [{**copy.deepcopy(template), 'key': 'chain-' + str(n),
                  'blockers': ['chain-' + str(n + 1)] if n < 1099 else []} for n in range(1100)]
        before = self.stored()
        for blocker in ('chain-0', 'absent'):
            bad = copy.deepcopy(batch)
            bad[-1]['blockers'] = [blocker]
            with self.subTest(blocker=blocker), self.assertRaises(repositories.RepositoryError):
                self.validate(bad)
            self.assertEqual(self.stored(), before)
        ordered = self.validate(batch)
        self.assertEqual([t['key'] for t in ordered], ['chain-' + str(n) for n in reversed(range(1100))])
        self.assertEqual(self.stored(), before)
        self.assert_no_external_writes()

    def test_deep_authoritative_graph_cycle_and_unknown_guards(self):
        template = copy.deepcopy(self.issues[12])
        for n in range(20, 1120):
            issue = copy.deepcopy(template)
            issue.update(number=n, id=n * 100, node_id='I' + str(n),
                         html_url='https://github.com/example/product/issues/' + str(n), body=self.canonical_body)
            if n < 1119:
                issue['body'] = issue['body'].replace('None (can start immediately).', '- #' + str(n + 1))
            self.dependencies[n] = [n + 1] if n < 1119 else []
            self.issues[n] = issue
            member = copy.deepcopy(self.members[12])
            member.update(id='ITEM' + str(n), content={'id': 'I' + str(n), 'number': n,
                                                      'repository': {'nameWithOwner': 'example/product'}})
            self.members[n] = member
        snapshot = self.frontier()
        self.assertEqual(snapshot['eligible'], [10, 12, 1119])
        self.assertEqual(len(snapshot['items']), 1102)
        before = self.stored()
        self.dependencies[1119] = [20]
        self.issues[1119]['body'] = self.canonical_body.replace('None (can start immediately).', '- #20')
        with self.assertRaises(repositories.RepositoryError) as error:
            self.frontier()
        self.assertEqual(error.exception.code, 'github_mismatch')
        self.assertEqual(self.stored(), before)
        self.assert_no_external_writes()
        self.issues[2000] = {**template, 'number': 2000, 'id': 200000, 'node_id': 'I2000',
                             'html_url': 'https://github.com/example/product/issues/2000', 'body': self.canonical_body}
        self.dependencies[1119] = [2000]
        self.issues[1119]['body'] = self.canonical_body.replace('None (can start immediately).', '- #2000')
        self.assertEqual(self.frontier()['eligible'], [10, 12])
        self.assertEqual(self.reserve(12)['reservation']['status'], 'reserved')
