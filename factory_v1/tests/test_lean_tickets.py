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

    def test_single_line_comment_tail_matrix_preserves_normalized_spans(self):
        prefix, marker = self.issue['body'].split('<!--', 1)
        marker = '<!--' + marker
        comments = [('<!--' + left + 'neighbor' + right + '-->')
                    for left in ('', ' ') for right in ('', ' ')] + ['<!---->', '<!-- -->']
        for newline in ('\n', '\r\n'):
            for heading in ('## Blocked by', '## \tBlocked  by \t###\t'):
                content = prefix.replace('## Blocked by', heading).replace('\n', newline)
                expected = tickets.sections(content)
                assert expected is not None
                for indent in ('', '  ', '   ', '\t'):
                    for comment in comments:
                        neighbor = indent + comment
                        for sequence in (neighbor, marker + newline + neighbor,
                                         marker + newline * 2 + neighbor + newline + '\t<!---->'):
                            for ending in ('', '\t', ' \t', newline, newline * 2,
                                           '\t' + newline * 2 + ' \t'):
                                tail = sequence + ending
                                body = content + tail
                                with self.subTest(newline=newline, heading=heading, tail=tail):
                                    spans = tickets.section_spans(body)
                                    self.assertIsNotNone(spans)
                                    assert spans is not None
                                    start, end = spans['Blocked by']
                                    self.assertEqual(end, len(content))
                                    self.assertEqual(body[end:], tail)
                                    self.assertEqual(body[start:end].strip(), expected['Blocked by'])
                                    self.assertEqual(tickets.sections(body), expected)
                                    self.assertEqual(tickets.external_contract(
                                        {**self.issue, 'body': body}, self.inputs), 'contracted')
                                    target = body[:start] + newline + '- #13' + newline * 2 + body[end:]
                                    self.assertEqual(target[:start].encode(), body[:start].encode())
                                    self.assertTrue(target.encode().endswith(tail.encode()))
                                    target_parts = tickets.sections(target)
                                    assert target_parts is not None
                                    self.assertEqual(target_parts['Blocked by'], '- #13')

    def test_nonterminal_inline_and_multiline_comments_are_not_footer_tails(self):
        prefix = '## Blocked by\n\nNone.\n\n'
        for tail in ('<!-- neighbor -->\nVisible prose\n', 'Text <!-- neighbor -->\n',
                     '<!-- first\nsecond -->\n', '<!-- unclosed\n', ' \t\n\n'):
            for newline in ('\n', '\r\n'):
                body = (prefix + tail).replace('\n', newline)
                with self.subTest(body=body):
                    spans = tickets.section_spans(body)
                    assert spans is not None
                    self.assertEqual(spans['Blocked by'][1], len(body))

    def test_closing_atx_headings_share_contract_names_and_source_spans(self):
        body = self.issue['body']
        parts = tickets.sections(body)
        assert parts is not None
        for name in parts:
            body = body.replace('## ' + name + '\n', '## \t' + name + ' \t### \t\n')
        body = body.replace('\n', '\r\n')
        self.assertEqual(tickets.external_contract({**self.issue, 'body': body}, self.inputs), 'contracted')
        self.assertEqual(tickets.sections(body), {name: value.replace('\n', '\r\n') for name, value in parts.items()})
        spans = tickets.section_spans(body)
        assert spans is not None
        self.assertEqual(body[slice(*spans['Blocked by'])].strip(), parts['Blocked by'].replace('\n', '\r\n'))
        self.assertIsNone(tickets.sections(body + '\r\n## Done ##\r\n\r\n- Duplicate.\r\n'))
        self.assertEqual(tickets.sections('## Literal#\n\nx\n'), {'Literal#': 'x'})


class LeanFrontierTests(unittest.TestCase):
    def setUp(self):
        self.case = test_tickets.TicketTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.item = self.case.publish()
        self.inputs = self.item['ticket_work']['inputs']
        self.canonical_body = self.case.issues[12]['body']
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

    def assert_public_refusals(self, variants):
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
                self.assertEqual(self.case.issues[12]['body'], variant)
                self.assertFalse(any(c[0] == 'PATCH' or c[0] == 'POST' and
                                     (c[1] != '/graphql' or c[2]['query'].lstrip().startswith('mutation '))
                                     for c in self.case.fixture.calls))
                frontier = self.case.inspect()['ticket_work']['frontier']
                self.assertEqual(frontier['eligible'], [10])

    def test_public_refusals_preserve_independent_frontier_and_make_no_claim_or_write(self):
        body = lean_body(self.inputs)
        self.assert_public_refusals([
            body.replace('US-1.', 'US-999.'), body + '\n## Status\n\ndeferred\n',
            body + '\n## Status\n\nunknown\n', body + '\n## Bug scope\n\nrecovery\n',
            body.replace('## Done', '## Done\n\n- criterion\n\n## Done'),
            body.replace('## Blocked by', '## \tBlocked  by').replace('None (can start immediately).', '- #10')])
        self.case.issues[12]['body'] = body + '\n## Status \t\n\ndeferred\n'
        self.assertEqual(self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'], [10])
        self.assertEqual(self.case.ok(self.case.control('reserve-ticket', issue=10))['reservation']['status'], 'reserved')

    def test_public_mixed_mechanical_scope_fields_refuse_without_claim_or_write(self):
        body = lean_body(self.inputs)
        self.assert_public_refusals([
            body + f'\n## Parent\n\n{parent}\n' for parent in
            ('#999', '#' + str(self.inputs['handoff']['issue']['number']), '')] + [
            body + f'\n## {heading}\n\n{value}\n' for heading, value in
            [('Iteration', 'other-milestone'), ('Triage', 'other-label')]])

    def test_public_closing_atx_control_headings_cannot_evade_holds_or_duplicates(self):
        body = lean_body(self.inputs)
        self.assert_public_refusals([
            body + f'\n## {heading}\n\n{value}\n' for heading, value in
            [('Status ##', 'blocked'), ('Status \t### \t', 'deferred'),
             ('Done ##', '- Conflicting criterion.'), ('Context ###', 'Parent #999; Stories: US-1.'),
             ('Status ##', 'unknown'), ('Bug scope ##', 'recovery')]] + [
            body + '\n## Status\n\nready\n\n## Status ##\n\nblocked\n',
            self.canonical_body.replace('## Status\n', '## Status ##\n').replace(
                'ready (GitHub Projects is authoritative for current progress).', 'blocked')])


class LeanAffectedMutationTests(unittest.TestCase):
    def test_public_terminal_comment_tail_survives_crlf_lost_patch_replay(self):
        variants = [('lean', '\n\n \t\n'), ('lean', '\n<!-- unrelated footer -->\n\n \t'),
                    ('lean', '<!-- another footer -->\t\n\n<!-- final footer -->\n'),
                    ('canonical', '\n<!-- unrelated footer -->\n\n \t')]
        for contract, tail in variants:
            with self.subTest(contract=contract, tail=tail):
                case = test_bugs.BugTests()
                case.setUp()
                try:
                    body = case.case.issues[10]['body']
                    if contract == 'lean':
                        body = lean_body(case.case.inspect()['ticket_work']['inputs'])
                    body = (body + tail).replace('\n', '\r\n')
                    case.assert_normalized_affected_publication(
                        body, body.replace('None (can start immediately).', '- #13'), 'body')
                finally:
                    case.doCleanups()

    def test_public_footer_spelling_neighbors_survive_lost_patch_replay(self):
        for tail in ('\n  <!-- neighbor -->\n', '\n<!--neighbor-->\n'):
            with self.subTest(tail=tail):
                case = test_bugs.BugTests()
                case.setUp()
                try:
                    body = lean_body(case.case.inspect()['ticket_work']['inputs']) + tail
                    body = body.replace('## Blocked by', '## \tBlocked  by \t###\t').replace('\n', '\r\n')
                    case.assert_normalized_affected_publication(
                        body, body.replace('None (can start immediately).', '- #13'), 'body')
                    after = case.case.issues[10]['body']
                    self.assertEqual(after.encode(), body.replace('None (can start immediately).', '- #13').encode())
                    self.assertIn('<!-- factory-ticket:lean-fixture:work -->', after)
                    self.assertTrue(after.endswith(tail.replace('\n', '\r\n')))
                    patches = [c for c in case.case.fixture.calls if c[0] == 'PATCH']
                    self.assertEqual(len(patches), 1)
                    call_count = len(case.case.fixture.calls)
                    work = case.work(case.case.inspect())
                    self.assertTrue(case.work(case.retry(work['assignment_id']))['publication_complete'])
                    self.assertFalse(any(c[0] == 'PATCH' or c[0] == 'POST' and
                                         (c[1] != '/graphql' or c[2]['query'].lstrip().startswith('mutation '))
                                         for c in case.case.fixture.calls[call_count:]))
                finally:
                    case.doCleanups()

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
