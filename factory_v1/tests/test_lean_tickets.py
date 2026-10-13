"""Lean admission regressions: retained live body, otherwise labeled local fixtures."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from factory_v1 import tickets
from factory_v1.tests import test_tickets, test_bugs, test_assignments

ROOT = Path(__file__).resolve().parents[2]


def lean_body(inputs, blockers='None (can start immediately).'):
    parent = inputs['handoff']['issue']['number']
    url = inputs['documents']['milestone.md']['url']
    return (f'## Change\n\nReaders reopen the exact retained French note.\n\n'
            f'## Done\n\n- The public CLI reopens café unchanged.\n\n'
            f'## Context\n\nParent #{parent}; first milestone only. Stories: US-1.\n\n'
            f'[Pinned milestone]({url})\n\n'
            f'## Constraint\n\nNo unrelated changes.\n\n## Blocked by\n\n{blockers}\n\n'
            '<!-- factory-ticket:lean-fixture:work -->\n')


class LeanContractTests(unittest.TestCase):
    def setUp(self):
        self.issue = json.loads((ROOT / 'factory_v1/tests/fixtures/issue21.json').read_text())
        commit = 'cccb21e35cc989a1ecd5a3c88f0689a694d77d9c'
        self.inputs = {'handoff': {'commit': commit, 'issue': {'number': 3}}, 'documents': {}}
        for role, name in [('milestone.md', 'fresh-agentic-factory.md'),
                           ('provenance.md', 'fresh-agentic-factory-interview-decisions.md')]:
            content = (ROOT / 'docs/specs' / name).read_text()
            self.inputs['documents'][role] = {'content': content,
                'url': f'https://github.com/tehioant/agentic-forge/blob/{commit}/docs/specs/{name}',
                'sha256': hashlib.sha256(content.encode()).hexdigest()}

    def test_original_live_issue21_admits_without_fabricated_progress_or_body_rewrite(self):
        before = copy.deepcopy((self.issue, self.inputs))
        self.assertEqual(hashlib.sha256(self.issue['body'].encode()).hexdigest(), self.issue['body_sha256'])
        self.assertEqual(self.inputs['documents']['milestone.md']['sha256'],
                         'fd80a9650f507fb2791b6820ab16369d6c0f619eb7241c0c0bdd09a4cb480c8c')
        self.assertEqual(tickets.external_contract(self.issue, self.inputs), 'contracted')
        self.assertNotIn('Status', tickets.sections(self.issue['body']) or {})
        self.assertEqual((self.issue, self.inputs), before)

    def test_lean_rejects_missing_substance_ambiguous_or_unpinned_scope(self):
        body = self.issue['body']
        parts = tickets.sections(body)
        assert parts is not None
        defects = [
            {'title': ' '},
            {'body': body.replace(parts['Change'], ' ')},
            {'body': body.replace(parts['Done'], ' ')},
            {'body': body.replace('- Public pause', 'Public pause')},
            {'body': body.replace('US-36', 'US-999')},
            {'body': body.replace('US-36', 'US-01')},
            {'body': body.replace('Stories: US-35, US-36.', 'Stories: US-35, US-35.')},
            {'body': body.replace('Stories: US-35, US-36.', '')},
            {'body': body.replace('Parent #3;', 'Parent #4;')},
            {'body': body.replace('Parent #3;', 'Parent #03;')},
            {'body': body.replace('Parent #3;', '')},
            {'body': body.replace('Stories:', 'Stories: US-35. Stories:')},
            {'body': body.replace('Parent #3;', 'Parent #3; Parent #4;')},
            {'body': body.replace('cccb21e35cc989a1ecd5a3c88f0689a694d77d9c', 'a' * 40)},
            {'body': body + '\n## Done \t\n\n- A conflicting criterion.\n'},
            {'body': body + '\n## Acceptance criteria\n\n- [ ] Conflicting canonical fields.\n'},
            {'body': body + '\n## Status\n\nunknown\n'},
            {'body': body + '\n## Status\n\nready\ndeferred\n'},
            {'body': body.replace('## Blocked by', '## Unknown blockers')},
        ]
        for defect in defects:
            with self.subTest(defect=defect):
                self.assertFalse(tickets.external_contract({**self.issue, **defect}, self.inputs))
        for change in ('missing-parent', 'missing-document', 'unrelated-milestone'):
            inputs = copy.deepcopy(self.inputs)
            if change == 'missing-parent':
                inputs.pop('handoff')
            elif change == 'missing-document':
                inputs['documents'].pop('provenance.md')
            else:
                inputs['documents']['milestone.md']['content'] = '## User Stories\n1. Unrelated story.\n'
            with self.subTest(inputs=change):
                self.assertFalse(tickets.external_contract(self.issue, inputs))

    def test_normalized_sections_retain_spans_and_refuse_duplicates(self):
        body = self.issue['body'].replace('## ', '## \t').replace('\n', '\r\n')
        self.assertEqual(tickets.external_contract({**self.issue, 'body': body}, self.inputs), 'contracted')
        spans = tickets.section_spans(body)
        parts = tickets.sections(body)
        assert spans is not None and parts is not None
        self.assertEqual(body[slice(*spans['Blocked by'])].strip(), parts['Blocked by'])
        self.assertTrue(body[spans['Blocked by'][1]:].startswith('<!-- fresh-factory-m1-ticket:'))
        self.assertIsNone(tickets.sections(body + '\r\n##\tBlocked  by \t\r\n\r\nNone.\r\n'))


class LeanFrontierTests(unittest.TestCase):
    def setUp(self):
        self.case = test_tickets.TicketTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.item = self.case.publish()
        self.inputs = self.item['ticket_work']['inputs']
        self.case.issues[12]['body'] = lean_body(self.inputs)

    def test_public_frontier_and_reservation_use_board_progress_not_done_heading(self):
        self.case.members[12]['fields']['STATUS'] = 'Deferred'
        frontier = self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']
        row = next(r for r in frontier['items'] if r['number'] == 12)
        self.assertTrue(row['contract_valid'])
        self.assertEqual(row['status'], 'deferred')
        self.assertFalse(row['admissible'])
        self.assertEqual(self.case.control('reserve-ticket', issue=12).returncode, 2)
        self.case.members[12]['fields']['STATUS'] = 'Ready'
        body = self.case.issues[12]['body']
        reserved = self.case.ok(self.case.control('reserve-ticket', issue=12))
        self.assertEqual(reserved['reservation']['status'], 'reserved')
        self.assertEqual(self.case.members[12]['fields']['STATUS'], 'Active')
        self.assertEqual(self.case.issues[12]['body'], body)
        self.assertEqual(self.case.issues[12]['state'], 'open')
        self.assertFalse(reserved['execution_allowed'])

    def test_public_refusals_preserve_independent_frontier_and_make_no_claim_or_write(self):
        body = self.case.issues[12]['body']
        variants = [body.replace('US-1.', 'US-999.'), body + '\n## Status\n\ndeferred\n',
                    body + '\n## Status\n\nunknown\n', body + '\n## Bug scope\n\nrecovery\n',
                    body.replace('## Done', '## Done\n\n- criterion\n\n## Done'),
                    body.replace('## Blocked by', '## \tBlocked  by').replace('None (can start immediately).', '- #10')]
        for variant in variants:
            with self.subTest(body=variant):
                self.case.issues[12]['body'] = variant
                members = copy.deepcopy(self.case.members)
                self.case.fixture.calls.clear()
                result = self.case.control('reserve-ticket', issue=12)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(json.loads(result.stderr)['error'], {'ticket_ineligible', 'github_mismatch'})
                self.assertNotIn('reservation', self.case.inspect())
                self.assertEqual(self.case.members, members)
                self.assertFalse(any(c[0] == 'POST' and c[1] == '/graphql' and
                                     c[2]['query'].lstrip().startswith('mutation ') for c in self.case.fixture.calls))
        self.case.issues[12]['body'] = body + '\n## Status \t\n\ndeferred\n'
        self.assertEqual(self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'], [10])
        self.assertEqual(self.case.ok(self.case.control('reserve-ticket', issue=10))['reservation']['status'], 'reserved')


class LeanAffectedMutationTests(unittest.TestCase):
    def test_public_lean_affected_crlf_body_update_and_lost_response_replay(self):
        case = test_bugs.BugTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        body = lean_body(case.case.inspect()['ticket_work']['inputs'])
        body = body.replace('## Blocked by', '## \tBlocked  by \t').replace('\n', '\r\n')
        case.assert_normalized_affected_publication(body, body.replace('None (can start immediately).', '- #13'), 'body')


class LeanAssignmentTests(unittest.TestCase):
    def test_public_prepare_keeps_exact_body_pin_and_refuses_later_valid_body_drift(self):
        case = test_assignments.AssignmentTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.tracker.issues[12]['body'] = lean_body(case.item['ticket_work']['inputs'])
        request = case.configuration(number=12)
        before = copy.deepcopy(case.tracker.issues)
        prepared = case.ok(case.command(request=request))
        self.assertEqual(prepared['handoff']['issue_body'], before[12]['body'])
        self.assertEqual(prepared['handoff']['issue']['body_sha256'], request['issue']['body_sha256'])
        case.assert_no_external_writes()
        case.tracker.issues[12]['body'] = before[12]['body'].replace('No unrelated changes.', 'No unrelated changes or publication.')
        case.refused(case.command(request=request), 'scope_mismatch')
        case.assert_no_external_writes()
        self.assertEqual(case.tracker.issues[12]['title'], before[12]['title'])
        self.assertEqual(case.tracker.members[12]['fields']['STATUS'], 'Ready')
        case.tracker.issues[12]['body'] = before[12]['body']
        self.assertEqual(case.ok(case.command(request=request))['assignment_id'], prepared['assignment_id'])


if __name__ == '__main__':
    unittest.main()
