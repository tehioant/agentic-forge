"""Lean contracts and controller seams; tracker/model boundaries are local mocks."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_v1 import assignments, bugs, repositories, tickets

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
        snapshot = json.loads((ROOT / 'factory_v1/tests/fixtures/lean_original_spec.json').read_text())
        commit = 'cccb21e35cc989a1ecd5a3c88f0689a694d77d9c'
        self.assertEqual(snapshot['commit'], commit)
        self.inputs = {'handoff': {'commit': commit, 'issue': {'number': 3}}, 'documents': {}}
        for role, name in [('milestone.md', 'fresh-agentic-factory.md'),
                           ('provenance.md', 'fresh-agentic-factory-interview-decisions.md')]:
            content = snapshot['documents'][name]
            self.inputs['documents'][role] = {'content': content,
                'url': f'https://github.com/tehioant/agentic-forge/blob/{commit}/docs/specs/{name}',
                'sha256': hashlib.sha256(content.encode()).hexdigest()}

    def test_original_live_issue21_admits_without_fabricated_progress_or_body_rewrite(self):
        before = copy.deepcopy((self.issue, self.inputs))
        self.assertEqual(hashlib.sha256(self.issue['body'].encode()).hexdigest(), self.issue['body_sha256'])
        self.assertEqual(self.inputs['documents']['milestone.md']['sha256'],
                         'fd80a9650f507fb2791b6820ab16369d6c0f619eb7241c0c0bdd09a4cb480c8c')
        self.assertEqual(self.inputs['documents']['provenance.md']['sha256'],
                         '8c5129e02dcf289dcc146efe5b3ef401ea5b3cb0d37bdbd2f37672f071d93625')
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


class LeanControllerCase(unittest.TestCase):
    """Explicit admitted state, real SQLite/admission, mocked tracker I/O only."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(self.database.close)
        self.database.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
        commit = 'a' * 40
        handoff = {'commit': commit, 'issue': {'number': 3}}
        self.inputs = {'handoff': handoff, 'documents': {}}
        for name, content in [('milestone.md', '## User Stories\n1. Readers reopen retained notes.\n'),
                              ('provenance.md', 'Operator chose retained French notes.\n')]:
            self.inputs['documents'][name] = {
                'url': f'https://github.com/example/product/blob/{commit}/docs/{name}',
                'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest()}
        statuses = {role: role.title() for role in ('ready', 'active', 'done', 'blocked', 'deferred')}
        self.board = {'project_id': 'BOARD', 'status': {'id': 'STATUS', 'options': {v: v for v in statuses.values()}},
                      'scope': {'id': 'SCOPE', 'options': {'m1': 'M1'}}}
        source = ROOT / 'factory_v1/ticket_skills/to-tickets.md'
        descriptor = {'name': 'to-tickets', 'path': str(source),
                      'sha256': '5c9fba69845c2519b9b35b9af42ae5142c21f8ca15ac2123dc2722002c8058ae'}
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
                     'status': 'active', 'execution_allowed': False, 'stage': 'ticket_frontier_ready',
                     'correlation': {'stage': 'ticket_frontier_ready'}, 'handoff': handoff,
                     'approval': {'operator_id': '42', 'reference': 'local-test-approval'},
                     'repository_onboarding': {'status': 'verified', 'metadata': {'id': 123}},
                     'ticket_work': {'assignment_id': 'lean-tickets', 'input_digest': tickets.digest(self.inputs),
                                     'inputs': self.inputs, 'board': self.board, 'publication_complete': True,
                                     'request': {'skill': descriptor, 'tracker': {
                                         'project_id': 'BOARD', 'status_field': 'Status', 'scope_field': 'Iteration',
                                         'statuses': statuses, 'triage_label': 'ready-for-agent'}}}}
        ticket = {'key': 'notes', 'title': 'Retain notes', 'desired_behavior': 'Reopen café unchanged.',
                  'references': [{'spec': self.inputs['documents']['milestone.md']['url'], 'requirement': 'US-1'}],
                  'acceptance_criteria': ['Readers reopen the exact retained note.'],
                  'blockers': [], 'status': 'ready', 'triage_label': 'ready-for-agent'}
        self.canonical_body = tickets.body(self.item, ticket, {})
        self.issues = {n: {'number': n, 'id': n * 100, 'node_id': f'I{n}', 'state': 'open',
                           'state_reason': None, 'title': 'Retain notes', 'labels': [{'name': 'ready-for-agent'}],
                           'repository_url': 'https://api.github.com/repos/example/product',
                           'html_url': f'https://github.com/example/product/issues/{n}',
                           'body': lean_body(self.inputs)} for n in (10, 12, 13)}
        self.members = {n: {'id': f'ITEM{n}',
                            'content': {'id': f'I{n}', 'number': n, 'repository': {'nameWithOwner': 'example/product'}},
                            'fieldValues': {'nodes': [{'field': {'id': 'STATUS'}, 'optionId': 'Ready'},
                                                      {'field': {'id': 'SCOPE'}, 'optionId': 'M1'}],
                                            'pageInfo': {'hasNextPage': False}}} for n in (10, 12)}
        self.dependencies = {n: [] for n in self.issues}
        self.mutations = []
        self.drop_patch = False
        self.github = Mock(base='https://api.github.com')
        self.github.request.side_effect = self.tracker_request
        self.database.execute('INSERT INTO iterations VALUES (?, ?, ?)', ('product', 'm1', json.dumps(self.item)))
        self.database.commit()
        for target, name, options in [
                (tickets, 'pinned_inputs', {'return_value': self.inputs}),
                (tickets.gh, 'resolve_board', {'return_value': copy.deepcopy(self.board)}),
                (tickets.gh, 'project_items', {'side_effect': lambda *_: copy.deepcopy(list(self.members.values()))}),
                (tickets.gh, 'set_field', {'side_effect': self.set_field})]:
            mock = patch.object(target, name, **options)
            started = mock.start()
            self.addCleanup(mock.stop)
            if name == 'set_field':
                self.field_write = started

    def tracker_request(self, path, payload=None, method=None):
        prefix = '/repos/example/product/issues/'
        self.assertTrue(path.startswith(prefix), path)
        number = int(path[len(prefix):].split('/')[0].split('?')[0])
        if '/dependencies/blocked_by' in path:
            if payload is None:
                return copy.deepcopy([self.issues[n] for n in self.dependencies[number]])
            self.assertIsNone(method)
            self.assertEqual(set(payload), {'issue_id'})
            blocker = next(n for n, issue in self.issues.items() if issue['id'] == payload['issue_id'])
            self.mutations.append(('POST', path, copy.deepcopy(payload)))
            self.dependencies[number].append(blocker)
            return {}
        self.assertEqual(path, prefix + str(number))
        if payload is None:
            self.assertIn(method, (None, 'GET'))
            return copy.deepcopy(self.issues[number])
        self.assertEqual(method, 'PATCH')
        self.assertEqual(set(payload), {'body'})
        self.mutations.append(('PATCH', path, copy.deepcopy(payload)))
        self.issues[number]['body'] = payload['body']
        if self.drop_patch:
            self.drop_patch = False
            raise repositories.RepositoryError('github_unavailable', 'Local lost PATCH response')
        return copy.deepcopy(self.issues[number])

    def set_field(self, github, project, member, field, option):
        self.assertIs(github, self.github)
        self.assertEqual((project, field), ('BOARD', 'STATUS'))
        target = next(m for m in self.members.values() if m['id'] == member)
        next(v for v in target['fieldValues']['nodes'] if v['field']['id'] == field)['optionId'] = option

    def status(self, number, option):
        self.members[number]['fieldValues']['nodes'][0]['optionId'] = option

    def stored(self):
        return json.loads(self.database.execute('SELECT payload FROM iterations').fetchone()[0])

    def persist(self):
        repositories.save_iteration(self.database, self.item)
        self.database.commit()

    def frontier(self):
        return tickets.frontier(self.database, self.stored(), self.github)['ticket_work']['frontier']

    def reserve(self, number):
        return tickets.reserve(self.database, self.stored(), self.github, number)

    def assert_no_external_writes(self):
        self.assertEqual(self.mutations, [])
        self.field_write.assert_not_called()


class LeanFrontierTests(LeanControllerCase):
    def test_frontier_and_reservation_use_board_progress_not_done_heading(self):
        self.status(12, 'Deferred')
        row = next(r for r in self.frontier()['items'] if r['number'] == 12)
        self.assertTrue(row['contract_valid'])
        self.assertEqual(row['status'], 'deferred')
        self.assertFalse(row['admissible'])
        with self.assertRaises(repositories.RepositoryError) as caught:
            self.reserve(12)
        self.assertEqual(caught.exception.code, 'ticket_ineligible')
        self.assertNotIn('reservation', self.stored())
        self.assert_no_external_writes()
        self.status(12, 'Ready')
        before = copy.deepcopy(self.issues)
        reserved = self.reserve(12)
        self.assertEqual(reserved['reservation']['status'], 'reserved')
        self.assertEqual(self.members[12]['fieldValues']['nodes'][0]['optionId'], 'Active')
        self.assertEqual(self.issues, before)
        self.assertEqual(self.issues[12]['state'], 'open')
        self.assertFalse(reserved['execution_allowed'])
        self.assertEqual(self.stored()['reservation'], reserved['reservation'])
        self.field_write.assert_called_once_with(self.github, 'BOARD', 'ITEM12', 'STATUS', 'Active')
        self.assertEqual(self.mutations, [])

    def assert_refusals(self, variants):
        for variant in variants:
            with self.subTest(body=variant):
                self.issues[12]['body'] = variant
                # Keep native edges consistent so each refusal exercises its intended guard.
                self.dependencies[12] = [10] if '- #10' in variant else []
                frontier = self.frontier()
                self.assertEqual(frontier['eligible'], [10])
                members = copy.deepcopy(self.members)
                with self.assertRaises(repositories.RepositoryError) as caught:
                    self.reserve(12)
                self.assertEqual(caught.exception.code, 'ticket_ineligible')
                self.assertNotIn('reservation', self.stored())
                self.assertEqual(self.members, members)
                self.assertEqual(self.issues[12]['body'], variant)
                self.assert_no_external_writes()
                self.assertEqual(self.stored()['ticket_work']['frontier']['eligible'], [10])

    def test_refusals_preserve_independent_frontier_and_make_no_claim_or_write(self):
        body = lean_body(self.inputs)
        self.assert_refusals([
            body.replace('US-1.', 'US-999.'), body + '\n## Status\n\ndeferred\n',
            body + '\n## Status\n\nunknown\n', body + '\n## Bug scope\n\nrecovery\n',
            body.replace('## Done', '## Done\n\n- criterion\n\n## Done'),
            body.replace('## Blocked by', '## \tBlocked  by').replace('None (can start immediately).', '- #10')])
        self.issues[12]['body'] = body + '\n## Status \t\n\ndeferred\n'
        self.dependencies[12] = []
        self.assertEqual(self.frontier()['eligible'], [10])
        self.assertEqual(self.reserve(10)['reservation']['status'], 'reserved')

    def test_native_textual_blocker_disagreement_refuses_without_claim_or_write(self):
        self.issues[12]['body'] = lean_body(self.inputs, '- #10').replace('## Blocked by', '## \tBlocked  by')
        before = self.stored()
        members = copy.deepcopy(self.members)
        body = self.issues[12]['body']
        with self.assertRaises(repositories.RepositoryError) as caught:
            self.reserve(12)
        self.assertEqual(caught.exception.code, 'github_mismatch')
        self.assertIn('Native/textual blockers disagree', str(caught.exception))
        self.database.rollback()
        self.assertEqual(self.stored(), before)
        self.assertEqual(self.members, members)
        self.assertEqual(self.issues[12]['body'], body)
        self.assert_no_external_writes()

    def test_mixed_mechanical_scope_fields_refuse_without_claim_or_write(self):
        body = lean_body(self.inputs)
        self.assert_refusals([
            body + f'\n## Parent\n\n{parent}\n' for parent in
            ('#999', '#' + str(self.inputs['handoff']['issue']['number']), '')] + [
            body + f'\n## {heading}\n\n{value}\n' for heading, value in
            [('Iteration', 'other-milestone'), ('Triage', 'other-label')]])

    def test_closing_atx_control_headings_cannot_evade_holds_or_duplicates(self):
        body = lean_body(self.inputs)
        self.assert_refusals([
            body + f'\n## {heading}\n\n{value}\n' for heading, value in
            [('Status ##', 'blocked'), ('Status \t### \t', 'deferred'),
             ('Done ##', '- Conflicting criterion.'), ('Context ###', 'Parent #999; Stories: US-1.'),
             ('Status ##', 'unknown'), ('Bug scope ##', 'recovery')]] + [
            body + '\n## Status\n\nready\n\n## Status ##\n\nblocked\n',
            self.canonical_body.replace('## Status\n', '## Status ##\n').replace(
                'ready (GitHub Projects is authoritative for current progress).', 'blocked')])


class LeanAffectedMutationTests(LeanControllerCase):
    def assert_lost_patch_replay(self, body):
        self.issues[12]['body'] = body
        original = copy.deepcopy(self.issues)
        self.assertTrue(tickets.external_contract(self.issues[12], self.inputs))
        work = {'inputs': self.inputs, 'bug': {'affected': [12]},
                'candidates': [bugs.observe(self.item, self.github, self.board, copy.deepcopy(self.issues[12]))],
                'board': self.board}
        self.item['affected_test_work'] = work
        self.persist()
        expected = body.replace('None (can start immediately).', '- #13')
        self.drop_patch = True
        with self.assertRaises(repositories.RepositoryError) as caught:
            bugs.affected_edges(self.database, self.item, work, self.github, self.issues[13])
        self.assertEqual(caught.exception.code, 'github_unavailable')
        self.assertEqual(self.issues[12]['body'].encode(), expected.encode())
        self.assertEqual(self.dependencies[12], [])
        persisted = self.stored()['affected_test_work']
        self.assertEqual(persisted['affected_intents'], {'12': {'before': body, 'body': expected}})
        # Restart from the durable intent, not the in-memory record that saw the failure.
        self.item = self.stored()
        work = self.item['affected_test_work']
        bugs.affected_edges(self.database, self.item, work, self.github, self.issues[13])
        bugs.verify_affected(self.item, work, self.github, self.issues[13])
        self.assertEqual(self.dependencies[12], [13])
        self.assertEqual([method for method, _, _ in self.mutations], ['PATCH', 'POST'])
        self.assertEqual(self.issues[12], {**original[12], 'body': expected})
        self.assertEqual(self.issues[10], original[10])
        self.assertEqual(self.issues[13], original[13])
        self.assertEqual(self.stored()['affected_test_work']['edge_intents'], [[12, 1300]])
        writes = copy.deepcopy(self.mutations)
        self.item = self.stored()
        bugs.affected_edges(self.database, self.item, self.item['affected_test_work'], self.github, self.issues[13])
        bugs.verify_affected(self.item, self.item['affected_test_work'], self.github, self.issues[13])
        self.assertEqual(self.mutations, writes)
        self.field_write.assert_not_called()
        # Each matrix row starts from the same small state rather than a new worker journey.
        self.dependencies[12] = []
        self.mutations.clear()

    def test_terminal_comment_tail_survives_crlf_lost_patch_replay(self):
        variants = [('lean', '\n\n \t\n'), ('lean', '\n<!-- unrelated footer -->\n\n \t'),
                    ('lean', '<!-- another footer -->\t\n\n<!-- final footer -->\n'),
                    ('canonical', '\n<!-- unrelated footer -->\n\n \t')]
        for contract, tail in variants:
            with self.subTest(contract=contract, tail=tail):
                body = lean_body(self.inputs) if contract == 'lean' else self.canonical_body
                self.assert_lost_patch_replay((body + tail).replace('\n', '\r\n'))

    def test_footer_spelling_neighbors_survive_lost_patch_replay(self):
        for tail in ('\n  <!-- neighbor -->\n', '\n<!--neighbor-->\n'):
            with self.subTest(tail=tail):
                body = lean_body(self.inputs) + tail
                body = body.replace('## Blocked by', '## \tBlocked  by \t###\t').replace('\n', '\r\n')
                self.assert_lost_patch_replay(body)
                after = self.issues[12]['body']
                self.assertIn('<!-- factory-ticket:lean-fixture:work -->', after)
                self.assertTrue(after.endswith(tail.replace('\n', '\r\n')))

    def test_lean_affected_crlf_body_update_and_lost_response_replay(self):
        body = lean_body(self.inputs)
        body = body.replace('## Blocked by', '## \tBlocked  by \t').replace('\n', '\r\n')
        self.assert_lost_patch_replay(body)


class LeanAssignmentTests(LeanControllerCase):
    def test_prepare_keeps_exact_body_pin_and_refuses_later_valid_body_drift(self):
        source = ROOT / 'factory_v1/tests/role_skill_snapshots/implement/SKILL.md'
        skill = {'name': 'implement', 'source': '/home/ops/.hermes/skills/implement/SKILL.md',
                 'path': str(source), 'dependencies': [],
                 'sha256': '136a8987143241a0229952a56d9c41042646073769fcad466d808bd5e40bb4f0'}
        standards = 'Fixture standards: meaningful tests; no direct publication.'
        issue = self.issues[12]
        request = {'stage': 'implementation', 'repository': 'example/product', 'repository_id': 123,
                   'workspace': str(self.root / 'workspace'),
                   'issue': {k: issue[k] for k in ('number', 'id', 'node_id')} |
                            {'body_sha256': hashlib.sha256(issue['body'].encode()).hexdigest()},
                   'spec_commit': self.item['handoff']['commit'], 'baseline': 'a' * 40, 'candidate': None,
                   'standards': [{'name': 'AGENTS.md', 'content': standards,
                                  'sha256': hashlib.sha256(standards.encode()).hexdigest()}], 'preceding': [],
                   'profile': {'name': 'factory-implementation', 'home': str(self.root / 'profile'),
                               'role': 'implementation', 'provider': 'openai-codex', 'model': 'gpt-6.1-sol',
                               'operation': 'responses', 'reasoning': 'high'},
                   'skills': [skill], 'capabilities': ['read_workspace', 'scratch', 'model', 'write_workspace'],
                   'result_contract': 'factory-bounded-result-v1', 'claim_id': 'lean-body-pin'}
        before = copy.deepcopy(self.issues)
        iteration_before = self.stored()
        policy = {'scope': ['product', 'm1', 'openai-codex', 'gpt-6.1-sol', 'responses']}
        with patch.object(assignments, 'admission_preview', return_value=policy):
            def prepare():
                return assignments.prepare(self.database, 'product', 'm1', '42', request, self.github)
            prepared = prepare()
            self.assertEqual(prepared['handoff']['issue_body'], before[12]['body'])
            self.assertEqual(prepared['handoff']['issue']['body_sha256'], request['issue']['body_sha256'])
            self.assertEqual(prepared['handoff']['input_loads'][issue['html_url']], request['issue']['body_sha256'])
            persisted = assignments.rows(self.database)
            self.assertEqual(len(persisted), 1)
            self.assertEqual(persisted[0]['handoff'], prepared['handoff'])
            self.assert_no_external_writes()
            issue['body'] = before[12]['body'].replace('No unrelated changes.', 'No unrelated changes or publication.')
            self.assertEqual(tickets.external_contract(issue, self.inputs), 'contracted')
            with self.assertRaises(repositories.RepositoryError) as caught:
                prepare()
            self.assertEqual(caught.exception.code, 'scope_mismatch')
            self.assertEqual(assignments.rows(self.database), persisted)
            self.assert_no_external_writes()
            self.assertEqual(issue['title'], before[12]['title'])
            self.assertEqual(self.members[12]['fieldValues']['nodes'][0]['optionId'], 'Ready')
            issue['body'] = before[12]['body']
            self.assertEqual(prepare()['assignment_id'], prepared['assignment_id'])
        self.assertEqual(self.stored(), iteration_before)
        self.assertEqual(self.issues, before)
        self.assertEqual(assignments.rows(self.database), persisted)
        self.assert_no_external_writes()


if __name__ == '__main__':
    unittest.main()
