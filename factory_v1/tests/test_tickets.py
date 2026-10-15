"""Small ticket validators and durable publication/admission components; no CLI or transport."""
import base64
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from factory_v1 import controls, repositories, tickets

REAL_PINNED_INPUTS = tickets.pinned_inputs


def conn(nodes):
    return {'nodes': nodes, 'pageInfo': {'hasNextPage': False, 'endCursor': None}}


def hash_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class FrontierFixture:
    """Explicit state for one direct function, not a preparatory lifecycle."""
    def __init__(self, case, native=False):
        directory = tempfile.TemporaryDirectory()
        case.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / 'state.sqlite'
        self.database = sqlite3.connect(self.state)
        case.addCleanup(self.database.close)
        self.database.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
        self.database.execute('CREATE TABLE role_assignments (assignment_id TEXT, payload TEXT)')
        self.github = Mock(base='https://api.github.com')
        source = Path(__file__).resolve().parents[1] / 'ticket_skills/to-tickets.md'
        descriptor = {'name': 'to-tickets', 'path': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
        statuses = {'ready': 'Ready', 'active': 'Active', 'blocked': 'Blocked', 'deferred': 'Deferred', 'done': 'Done'}
        self.board = {'project_id': 'P1', 'status': {'id': 'STATUS', 'options': {n: n for n in statuses.values()}},
                      'scope': {'id': 'SCOPE', 'options': {'m1': 'M1', 'm2': 'M2'}}}
        if native:
            statuses = {'ready': 'Todo', 'active': 'In Progress', 'done': 'Done'}
            self.board = {'project_id': 'P1', 'status': {'id': 'STATUS', 'options': {n: n for n in statuses.values()}},
                          'milestone': {'id': 501, 'number': 1, 'title': 'm1'}}
        self.start_request = {'skill': descriptor, 'tracker': {'project_id': 'P1', 'status_field': 'Status',
                              'triage_label': 'agent-triage', 'statuses': statuses}}
        url = 'https://github.com/example/product/blob/' + 'a' * 40 + '/docs/milestone.md'
        self.inputs = {'handoff': {'issue': {'number': 7}}, 'documents': {
            'milestone.md': {'url': url, 'sha256': hashlib.sha256(b'## User Stories\n1. Retain notes.\n').hexdigest(),
                             'content': '## User Stories\n1. Retain notes.\n'},
            'vision.md': {'url': url.replace('milestone', 'vision'), 'sha256': hashlib.sha256(b'Team sharing').hexdigest(),
                          'content': 'Team sharing'}}}
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
                     'status': 'active', 'stage': 'ticket_frontier_ready', 'execution_allowed': False,
                     'correlation': {'stage': 'ticket_frontier_ready', 'worker_run_id': None},
                     'origin': {'platform': 'discord', 'chat_id': '1'},
                     'approval': {'operator_id': '42', 'reference': 'fixture-direction'},
                     'repository_onboarding': {'status': 'verified', 'api_base': self.github.base, 'metadata': {'id': 123}},
                     'handoff': self.inputs['handoff']}
        self.work = {'assignment_id': 'ticket-fixture', 'request': self.start_request,
                     'skill': tickets.skill(descriptor), 'inputs': self.inputs, 'input_digest': tickets.digest(self.inputs),
                     'adaptation': tickets.ADAPTATION, 'board': self.board, 'status': 'published', 'publication_complete': True,
                     'published': {}}
        self.item['ticket_work'] = self.work
        self.issues, self.members, self.deps = {}, {}, {}
        for number, blockers in ((10, []), (11, [10]), (12, [])):
            ticket = self.ticket('note-' + str(number), blockers=blockers)
            self.issues[number] = self.issue(number, tickets.body(self.item, ticket, {10: 10}))
            fields = {'STATUS': statuses['ready']}
            if not native:
                fields['SCOPE'] = 'M1'
            self.members[number] = {'id': 'ITEM' + str(number), 'fields': fields}
            self.deps[number] = blockers
        self.work['frontier'] = {'authority': 'github', 'items': [
            {'number': n, 'id': i['id'], 'node_id': i['node_id'], 'membership_id': self.members[n]['id'],
             'status': 'ready', 'issue_state': 'open', 'state_reason': None, 'blockers': self.deps[n],
             'contract_valid': True, 'held': False, 'admissible': not self.deps[n]} for n, i in self.issues.items()],
            'eligible': [10, 12], 'foreign_active': [], 'active': []}
        self.database.execute('INSERT INTO iterations VALUES (?, ?, ?)', ('product', 'm1', json.dumps(self.item)))
        self.database.commit()
        self.state.chmod(0o600)
        stack = ExitStack()
        case.addCleanup(stack.close)
        self.stack = stack
        stack.enter_context(patch.object(tickets, 'pinned_inputs', side_effect=lambda *_: copy.deepcopy(self.inputs)))
        stack.enter_context(patch.object(tickets, 'verified_board', side_effect=lambda *_: copy.deepcopy(self.board)))
        stack.enter_context(patch.object(tickets.gh, 'project_items', side_effect=lambda *_: self.project_items()))
        stack.enter_context(patch.object(tickets.gh, 'read_issue', side_effect=lambda _g, _r, n: copy.deepcopy(self.issues[n])))
        stack.enter_context(patch.object(tickets.gh, 'dependencies', side_effect=lambda _g, _r, n: [copy.deepcopy(self.issues[b]) for b in self.deps.get(n, [])]))
        stack.enter_context(patch.object(tickets.gh, 'membership', side_effect=self.membership))
        self.set_field = stack.enter_context(patch.object(tickets.gh, 'set_field', side_effect=self.field))
        self.add_item = stack.enter_context(patch.object(tickets.gh, 'add_item', side_effect=self.member))
        self.add_dependency = stack.enter_context(patch.object(tickets.gh, 'add_dependency', side_effect=self.edge))
        stack.enter_context(patch.object(tickets.gh, 'rest_pages', side_effect=lambda *_: copy.deepcopy(list(self.issues.values()))))
        self.github.request.side_effect = self.request

    def ticket(self, key='note', blockers=None):
        return {'key': key, 'title': 'Retain a note', 'desired_behavior': 'Persist the exact note.',
                'references': [{'spec': self.inputs['documents']['milestone.md']['url'], 'requirement': 'US-1'}],
                'acceptance_criteria': ['Reopening retains café.'], 'blockers': blockers or [], 'status': 'ready',
                'iteration': 'm1', 'triage_label': 'agent-triage'}

    def evidence(self, result, work=None):
        work = work or self.work
        sources = {work['skill']['path']: work['skill']['sha256'],
                   **{d['url']: d['sha256'] for d in work['inputs']['documents'].values()}, **work.get('load_sources', {})}
        return {'run_id': 'fixture-run', 'decomposition': 'One bounded observable note slice.', 'result_digest': tickets.digest(result),
                'loads': [{'source': source, 'sha256': pin, 'tool_reference': 'fixture-read'} for source, pin in sources.items()]}

    def issue(self, number, body):
        milestone = copy.deepcopy(self.board.get('milestone'))
        if milestone:
            milestone['state'] = 'open'
        return {'number': number, 'id': 1000 + number, 'node_id': 'I' + str(number), 'title': 'Retain a note', 'body': body,
                'state': 'open', 'state_reason': None, 'labels': [{'name': 'agent-triage'}],
                'repository_url': 'https://api.github.com/repos/example/product',
                'html_url': 'https://github.com/example/product/issues/' + str(number), 'milestone': milestone}

    def project_items(self):
        return [{'id': m['id'], 'content': {'id': self.issues[n]['node_id'], 'number': n, 'repository': {'nameWithOwner': 'example/product'}},
                 'fieldValues': conn([{'field': {'id': k}, 'optionId': v} for k, v in m['fields'].items()])} for n, m in self.members.items()]

    def membership(self, _github, _board, _repo, issue):
        m = self.members.get(issue['number'])
        return None if m is None else {'id': m['id'], 'status': m['fields'].get('STATUS'), 'scope': m['fields'].get('SCOPE')}

    def field(self, _github, _project, item, field, option):
        next(m for m in self.members.values() if m['id'] == item)['fields'][field] = option

    def member(self, _github, _project, node):
        n = next(n for n, i in self.issues.items() if i['node_id'] == node)
        self.members[n] = {'id': 'ITEM' + str(n), 'fields': {}}

    def edge(self, _github, _repo, number, blocker):
        self.deps.setdefault(number, []).append(next(n for n, i in self.issues.items() if i['id'] == blocker))

    def request(self, path, body=None, method=None):
        if method == 'PATCH':
            n = int(path.rsplit('/', 1)[1]); self.issues[n].update(body)
            return copy.deepcopy(self.issues[n])
        if body is not None and path == '/repos/example/product/issues':
            n = max(self.issues, default=9) + 1
            self.issues[n] = self.issue(n, body['body']); self.issues[n]['title'] = body['title']
            return copy.deepcopy(self.issues[n])
        raise AssertionError(('unexpected external boundary', path, body, method))

    def persist(self):
        repositories.save_iteration(self.database, self.item)
        self.database.commit()

    def stored(self):
        with closing(sqlite3.connect(self.state)) as database:
            return json.loads(database.execute('SELECT payload FROM iterations').fetchone()[0])

    def pause(self):
        controls.request(self.database, 'product', 'm1', '42', 'pause', 'fixture-pause')

    def publication(self):
        self.work.pop('publication_complete', None)
        self.work.update(status='publication_pending', tickets=[self.ticket()], execution=self.evidence([self.ticket()]))
        self.issues.clear(); self.members.clear(); self.deps.clear()
        self.persist()


class TicketTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FrontierFixture(self)
        self.database, self.item, self.github = self.fixture.database, self.fixture.item, self.fixture.github
        self.issues, self.members, self.deps = self.fixture.issues, self.fixture.members, self.fixture.deps
        self.start_request = self.fixture.start_request

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def control(self, command, request=None, issue=None):
        """Compatibility facade for remaining fixture consumers; never a CLI."""
        functions = {'synthesize-tickets': tickets.synthesize, 'complete-tickets': tickets.complete_synthesis,
                     'publish-tickets': tickets.publish, 'frontier': tickets.frontier, 'reserve-ticket': tickets.reserve}
        try:
            args = (self.database, self.inspect(), self.github)
            if command in {'synthesize-tickets', 'complete-tickets'}:
                args = (self.database, self.inspect(), request, self.github)
            elif command == 'reserve-ticket':
                args += (issue,)
            value = functions[command](*args)
            return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr='')
        except repositories.RepositoryError as error:
            self.database.rollback()
            return SimpleNamespace(returncode=2, stdout='', stderr=json.dumps({'error': error.code, 'message': str(error)}))

    def publish(self):
        return self.inspect()

    def inspect(self):
        return self.fixture.stored()

    def completion(self):
        batch = [self.fixture.ticket('reopen', ['create']), self.fixture.ticket('create'), self.fixture.ticket('independent')]
        work = self.fixture.work
        return {'assignment_id': work['assignment_id'], 'input_digest': work['input_digest'], 'adaptation': tickets.ADAPTATION,
                'tickets': batch, 'execution': self.fixture.evidence(batch)}

    def test_batch_orders_blockers_and_rejects_invalid_scope_and_cycles(self):
        batch = self.completion()['tickets']
        self.assertEqual([t['key'] for t in tickets.validate_batch(batch, self.fixture.inputs, 'm1', 'agent-triage')], ['create', 'reopen', 'independent'])
        for defect in ('unknown', 'cycle', 'duplicate', 'future', 'deferred', 'reference', 'criteria', 'label', 'requirement'):
            bad = copy.deepcopy(batch)
            if defect == 'unknown': bad[0]['blockers'] = ['absent']
            if defect == 'cycle': bad[1]['blockers'] = ['reopen']
            if defect == 'duplicate': bad[1]['key'] = 'reopen'
            if defect == 'future': bad[0]['iteration'] = 'm2'
            if defect == 'deferred': bad[0]['status'] = 'deferred'
            if defect == 'reference': bad[0]['references'][0]['spec'] = 'https://other/spec'
            if defect == 'criteria': bad[0]['acceptance_criteria'] = []
            if defect == 'label': bad[0]['triage_label'] = 'other'
            if defect == 'requirement': bad[0]['references'][0]['requirement'] = 'US-99'
            with self.subTest(defect=defect), self.assertRaises(repositories.RepositoryError):
                tickets.validate_batch(bad, self.fixture.inputs, 'm1', 'agent-triage')

    def test_execution_requires_complete_actual_loads_and_result(self):
        request = self.completion(); work = self.fixture.work
        self.assertEqual(tickets.validate_execution(work, request['execution'], request['tickets']), request['execution'])
        for field in ('source', 'sha256', 'tool_reference'):
            for value in ([], {}, None, 42, ''):
                bad = copy.deepcopy(request['execution']); bad['loads'][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(repositories.RepositoryError) as caught:
                    tickets.validate_execution(work, bad, request['tickets'])
                self.assertEqual(caught.exception.code, 'execution_evidence_required')
        for field, value in (('loads', []), ('result_digest', '0' * 64), ('decomposition', ''), ('run_id', '')):
            with self.subTest(field=field), self.assertRaises(repositories.RepositoryError):
                tickets.validate_execution(work, {**request['execution'], field: value}, request['tickets'])

    def ticket_case(self, label):
        from contextlib import contextmanager
        @contextmanager
        def isolated():
            fixture = self.fixture
            saved = {name: getattr(fixture, name) for name in ('item', 'work', 'issues', 'members', 'deps')}
            original_db = self.database; original_state = fixture.state
            data = copy.deepcopy(saved)
            fixture.state = fixture.root / (label + '.sqlite')
            self.database = fixture.database = sqlite3.connect(fixture.state); original_db.backup(self.database)
            for name, value in data.items(): setattr(fixture, name, value)
            self.item, self.issues, self.members, self.deps = fixture.item, fixture.issues, fixture.members, fixture.deps
            mocks = (fixture.set_field, fixture.add_item, fixture.add_dependency, self.github.request)
            settings = [(m, m.side_effect, m.return_value) for m in mocks]
            for m in mocks: m.reset_mock()
            try:
                yield
            finally:
                self.database.close(); self.database = fixture.database = original_db; fixture.state = original_state
                for name, value in saved.items(): setattr(fixture, name, value)
                self.item, self.issues, self.members, self.deps = fixture.item, fixture.issues, fixture.members, fixture.deps
                for m, effect, value in settings: m.side_effect = effect; m.return_value = value; m.reset_mock()
        return isolated()

    def test_synthesis_completion_replay(self):
        request = self.completion()
        for field, value in (('assignment_id', 'old'), ('input_digest', '0' * 64), ('adaptation', 'other'), ('inputs', None), ('replay', None)):
            with self.subTest(field=field), self.ticket_case(field):
                before = self.inspect()
                if field == 'replay':
                    self.fixture.work['completion_digest'] = tickets.digest(request); self.fixture.persist()
                    with patch.object(tickets, 'publish', side_effect=lambda _d, i, _g: i):
                        result = tickets.complete_synthesis(self.database, self.item, request, self.github)
                    self.assertEqual(result['ticket_work']['status'], 'published'); self.assertTrue(result['ticket_work']['publication_complete'])
                    self.assertEqual(self.inspect(), result)
                else:
                    with patch.object(tickets, 'publish') as publish:
                        if field == 'inputs':
                            with patch.object(tickets, 'pinned_inputs', return_value={}):
                                with self.assertRaises(repositories.RepositoryError): tickets.complete_synthesis(self.database, self.item, request, self.github)
                        else:
                            with self.assertRaises(repositories.RepositoryError): tickets.complete_synthesis(self.database, self.item, {**request, field: value}, self.github)
                        self.database.rollback(); self.assertEqual(self.inspect(), before); publish.assert_not_called()



    def test_external_contract_requires_substance_current_refs_and_unambiguous_sections(self):
        good = self.issues[10]
        self.assertEqual(tickets.external_contract(good, self.fixture.inputs), 'ready')
        for body in (good['body'].replace('Persist the exact note.', ''), good['body'].replace('US-1', 'US-99'),
                     good['body'].replace('Reopening retains café.', ''), good['body'].replace('## Status', '## Missing'),
                     good['body'] + '\n## What to build\nConflicting prose.\n'):
            with self.subTest(body=body):
                self.assertFalse(tickets.external_contract({**good, 'body': body}, self.fixture.inputs))
        self.assertFalse(tickets.external_contract({**good, 'title': ' '}, self.fixture.inputs))

    def test_selected_skill_bytes_are_revalidated_without_repinning(self):
        path = self.fixture.root / 'to-tickets.md'; path.write_bytes(Path(self.start_request['skill']['path']).read_bytes())
        descriptor = {**self.start_request['skill'], 'path': str(path)}
        self.assertEqual(tickets.skill(descriptor)['sha256'], descriptor['sha256'])
        path.write_text('Changed instructions')
        with self.assertRaises(repositories.RepositoryError) as caught:
            tickets.skill(descriptor)
        self.assertEqual(caught.exception.code, 'skill_blocked')

    def test_single_ticket_publication_retains_exact_body_and_no_dispatch(self):
        self.fixture.publication()
        result = tickets.publish(self.database, self.item, self.github)
        self.assertTrue(result['ticket_work']['publication_complete'])
        self.assertEqual(result['ticket_work']['frontier']['eligible'], [10])
        self.assertEqual(self.issues[10]['body'], tickets.body(self.item, self.fixture.ticket(), {}))
        self.assertEqual(self.members[10]['fields'], {'STATUS': 'Ready', 'SCOPE': 'M1'})
        self.assertFalse(result['execution_allowed']); self.assertIsNone(result['correlation']['worker_run_id'])
        self.assertEqual(self.inspect(), result)
        writes = self.github.request.call_count
        tickets.publish(self.database, result, self.github)
        self.assertEqual(self.github.request.call_count, writes)

    def test_ticket_effect_intent_reconciliation(self):
        for effect in ('unknown-issue', 'lost-issue', 'dependency', 'membership'):
            with self.subTest(effect=effect), self.ticket_case(effect):
                self.fixture.publication()
                if effect in ('dependency', 'membership'):
                    ticket = self.fixture.ticket(blockers=['blocker'])
                    self.fixture.work.update(tickets=[ticket], published={'blocker': {'number': 10, 'id': 1010, 'node_id': 'I10'}})
                    self.issues[10] = self.fixture.issue(10, 'external blocker')
                    self.issues[11] = self.fixture.issue(11, tickets.body(self.item, ticket, {'blocker': 10}))
                    self.fixture.work['intents'] = {'note': {'dependencies': [1010]} if effect == 'dependency' else {'membership': True}}
                    self.deps[11] = [] if effect == 'dependency' else [10]
                elif effect == 'unknown-issue': self.fixture.work['intents'] = {'note': {'issue': {'title': 'Retain a note'}}}
                else:
                    def create(path, body, method=None):
                        self.assertIn('issue', self.inspect()['ticket_work']['intents']['note'])
                        self.fixture.request(path, body, method)
                        raise repositories.RepositoryError('github_unavailable', 'lost acknowledgement')
                    self.github.request.side_effect = create
                self.fixture.persist()
                blocked = tickets.publish(self.database, self.item, self.github)
                if effect == 'lost-issue':
                    self.assertEqual(blocked['ticket_work']['status'], 'blocked'); self.assertEqual(len(self.issues), 1)
                else:
                    self.assertEqual(blocked['ticket_work']['blocker']['code'], 'publication_uncertain')
                    self.github.request.assert_not_called()
                if effect in ('dependency', 'membership'):
                    self.assertFalse(blocked['ticket_work'].get('publication_complete'))
                    self.fixture.add_dependency.assert_not_called(); self.fixture.add_item.assert_not_called()
                    self.assertEqual(self.inspect(), blocked)
                else:
                    if effect == 'unknown-issue': self.issues[10] = self.fixture.issue(10, tickets.body(self.item, self.fixture.ticket(), {}))
                    self.github.request.reset_mock(); self.github.request.side_effect = self.fixture.request
                    recovered = tickets.publish(self.database, blocked, self.github)
                    self.assertTrue(recovered['ticket_work']['publication_complete'])
                    self.github.request.assert_not_called(); self.assertEqual(self.inspect(), recovered)



    def test_issue_publication_identity_contract(self):
        for defect, code in (('board', 'projects_blocked'), ('identity', 'publication_mismatch')):
            with self.subTest(defect=defect), self.ticket_case(defect):
                self.fixture.publication()
                if defect == 'identity':
                    self.issues[10] = self.fixture.issue(10, tickets.body(self.item, self.fixture.ticket(), {}))
                    self.fixture.work['intents'] = {'note': {'identity': {'number': 10, 'id': 999, 'node_id': 'I10'}}}; self.fixture.persist()
                    result = tickets.publish(self.database, self.item, self.github)
                else:
                    with patch.object(tickets, 'verified_board', side_effect=repositories.RepositoryError('projects_blocked', 'board absent')):
                        result = tickets.publish(self.database, self.item, self.github)
                self.assertEqual(result['ticket_work']['blocker']['code'], code)
                self.assertEqual(self.inspect(), result)
                if defect == 'board':
                    self.assertTrue(result['ticket_work']['execution']); self.assertNotIn('intents', result['ticket_work']); self.github.request.assert_not_called()
                else:
                    self.assertFalse(result['ticket_work'].get('publication_complete')); self.fixture.add_item.assert_not_called()



    def test_reservation_exact_replay(self):
        for lost in (False, True):
            with self.subTest(lost=lost), self.ticket_case('lost' if lost else 'normal'):
                if lost:
                    def write(*args):
                        self.assertEqual(self.inspect()['reservation']['status'], 'pending'); self.fixture.field(*args)
                        raise repositories.RepositoryError('github_unavailable', 'lost status response')
                    self.fixture.set_field.side_effect = write
                    with self.assertRaises(repositories.RepositoryError): tickets.reserve(self.database, self.item, self.github, 10)
                    pending = self.inspect()['reservation']; self.database.rollback()
                    self.fixture.set_field.reset_mock(); self.fixture.set_field.side_effect = self.fixture.field
                result = tickets.reserve(self.database, self.item, self.github, 10)
                self.assertEqual(result['reservation']['status'], 'reserved'); self.assertEqual(self.inspect(), result)
                if lost:
                    self.assertEqual(result['reservation']['run_id'], pending['run_id']); self.fixture.set_field.assert_not_called()
                else:
                    replay = tickets.reserve(self.database, result, self.github, 10)
                    self.assertEqual(replay['reservation'], result['reservation'])
                    with self.assertRaises(repositories.RepositoryError) as caught: tickets.reserve(self.database, replay, self.github, 12)
                    self.assertEqual(caught.exception.code, 'reservation_conflict'); self.database.rollback()
                    self.assertEqual(self.inspect()['reservation'], result['reservation'])



    def test_reservation_fresh_frontier(self):
        for defect in ('partial', 'foreign', 'new-ticket', 'late-pause'):
            with self.subTest(defect=defect), self.ticket_case(defect):
                number = 10; expected = 'reservation_conflict'
                if defect == 'partial': self.fixture.work['publication_complete'] = False; self.fixture.persist(); expected = 'publication_pending'
                elif defect == 'foreign': self.members[12]['fields'].update(SCOPE='M2', STATUS='Active')
                elif defect == 'new-ticket':
                    self.issues[20] = self.fixture.issue(20, self.issues[12]['body'])
                    self.members[20] = {'id': 'ITEM20', 'fields': {'STATUS': 'Ready', 'SCOPE': 'M1'}}
                    result = tickets.frontier(self.database, self.item, self.github)
                    self.assertEqual(result['ticket_work']['frontier']['eligible'], [10, 12, 20])
                    self.issues[20]['labels'] = []; number = 20; expected = 'ticket_ineligible'
                else:
                    def write(*args): self.fixture.field(*args); self.fixture.pause()
                    self.fixture.set_field.side_effect = write; expected = 'iteration_paused'
                with self.assertRaises(repositories.RepositoryError) as caught: tickets.reserve(self.database, self.item, self.github, number)
                self.assertEqual(caught.exception.code, expected); self.database.rollback()
                if defect == 'late-pause':
                    self.assertEqual(self.inspect()['reservation']['status'], 'pending')
                    self.assertTrue(controls.blocked(self.database, 'product', 'm1')); self.assertEqual(self.members[10]['fields']['STATUS'], 'Active')
                else:
                    self.assertNotIn('reservation', self.inspect()); self.fixture.set_field.assert_not_called()
                if defect == 'new-ticket':
                    reserved = tickets.reserve(self.database, self.item, self.github, 10)
                    self.issues[10]['body'] = self.issues[10]['body'].replace('None (can start immediately).', '- #12'); self.deps[10] = [12]
                    with self.assertRaises(repositories.RepositoryError) as caught: tickets.reserve(self.database, self.item, self.github, 10)
                    self.assertEqual(caught.exception.code, 'ticket_ineligible'); self.database.rollback()
                    self.assertEqual(self.inspect()['reservation'], reserved['reservation']); self.assertEqual(self.members[10]['fields']['STATUS'], 'Active')

    def test_real_pinned_inputs_reject_endpoint_repository_and_raw_document_drift(self):
        handoff = {'repository': 'example/product', 'api_base': self.github.base, 'commit': 'a' * 40,
                   'issue': {'number': 7, 'id': 700}, 'documents': {}}
        responses = {'/repos/example/product': {'id': 123, 'full_name': 'example/product', 'name': 'product',
                     'owner': {'login': 'example'}, 'private': True}, '/repos/example/product/git/commits/' + 'a' * 40: {'sha': 'a' * 40}}
        for name, document in self.fixture.inputs['documents'].items():
            raw = document['content'].encode(); path = 'docs/' + name
            blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            handoff['documents'][name] = {'path': path, 'blob': blob, 'url': document['url']}
            responses['/repos/example/product/contents/' + path + '?ref=' + 'a' * 40] = {
                'path': path, 'type': 'file', 'encoding': 'base64', 'sha': blob, 'content': base64.b64encode(raw).decode()}
        self.item.update(handoff=handoff, planning={'status': 'complete'})
        self.fixture.issues[7] = {**self.fixture.issue(7, self.fixture.inputs['documents']['milestone.md']['content']), 'id': 700}
        self.github.request.side_effect = lambda path: copy.deepcopy(responses[path])
        read = REAL_PINNED_INPUTS(self.database, self.item, self.github); self.database.rollback()
        self.assertEqual(read['documents']['milestone.md']['content'], self.fixture.inputs['documents']['milestone.md']['content'])
        metadata = responses['/repos/example/product/contents/docs/milestone.md?ref=' + 'a' * 40]
        metadata['content'] = base64.b64encode(b'changed bytes').decode()
        with self.assertRaises(repositories.RepositoryError) as caught: REAL_PINNED_INPUTS(self.database, self.item, self.github)
        self.assertEqual(caught.exception.code, 'publication_mismatch'); self.database.rollback()
        responses['/repos/example/product']['id'] = 999
        with self.assertRaises(repositories.RepositoryError) as caught: REAL_PINNED_INPUTS(self.database, self.item, self.github)
        self.assertEqual(caught.exception.code, 'repository_mismatch'); self.database.rollback()
        self.github.base += '/other'; self.github.request.reset_mock()
        with self.assertRaises(repositories.RepositoryError) as caught: REAL_PINNED_INPUTS(self.database, self.item, self.github)
        self.assertEqual(caught.exception.code, 'capability_conflict'); self.github.request.assert_not_called()
