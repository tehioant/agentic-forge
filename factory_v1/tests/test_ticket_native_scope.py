"""Native scope/board/frontier checks at their real admission and metadata seams."""
import copy
import unittest
from unittest.mock import Mock, patch

from factory_v1 import repositories, ticket_github as gh, tickets
from factory_v1.tests import test_tickets

REAL_PROJECT_ITEMS = gh.project_items
REAL_DEPENDENCIES = gh.dependencies


class NativeScopeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_tickets.FrontierFixture(self, native=True)
        self.case = self.fixture
        self.database, self.item, self.github = self.fixture.database, self.fixture.item, self.fixture.github
        self.milestones = [{**self.fixture.board['milestone'], 'state': 'open'}]

    def frontier(self):
        return tickets.frontier(self.database, self.fixture.stored(), self.github)['ticket_work']['frontier']

    def test_native_scope_frontier_and_progress_1(self):
        original = copy.deepcopy(self.fixture.issues[10])
        for status, scope in (('blocked', True), ('deferred', True), ('ready', False), ('ready', True)):
            with self.subTest(status=status, scope=scope):
                self.fixture.issues[10] = copy.deepcopy(original)
                self.fixture.issues[10]['body'] = original['body'].replace('ready (GitHub Projects is authoritative for current progress).', status)
                if not scope:
                    self.fixture.issues[10]['milestone'] = {'id': 502, 'number': 2, 'title': 'm2', 'state': 'open'}
                self.assertEqual(self.frontier()['eligible'], [10, 12] if status == 'ready' and scope else [12])
                if status != 'ready' or not scope:
                    if scope:
                        with self.assertRaises(repositories.RepositoryError) as caught:
                            tickets.reserve(self.database, self.item, self.github, 10)
                        self.assertEqual(caught.exception.code, 'ticket_ineligible')
                        self.database.rollback(); self.assertNotIn('reservation', self.fixture.stored())
                    continue
                reserved = tickets.reserve(self.database, self.item, self.github, 10)
                self.assertEqual(self.fixture.members[10]['fields'], {'STATUS': 'In Progress'})
                self.assertEqual(reserved['reservation']['status'], 'reserved')
                self.assertEqual(self.fixture.stored(), reserved); self.assertFalse(reserved['execution_allowed'])
                self.assertIsNone(reserved['correlation']['worker_run_id'])
                with self.assertRaises(repositories.RepositoryError):
                    tickets.reserve(self.database, reserved, self.github, 12)
                self.database.rollback(); self.assertEqual(self.fixture.stored()['reservation'], reserved['reservation'])


    def test_completed_blocker_requires_closed_completed_done_current_scope(self):
        for state, reason, status, scope, expected in (
            ('open', None, 'Done', True, False), ('closed', 'not_planned', 'Done', True, False),
            ('closed', 'completed', 'Todo', True, False), ('closed', 'completed', 'Done', True, True),
            ('closed', 'completed', 'Done', False, False)):
            with self.subTest(state=state, reason=reason, status=status, scope=scope):
                self.fixture.issues[10].update(state=state, state_reason=reason,
                    milestone=self.milestones[0] if scope else {'id': 502, 'number': 2, 'title': 'm2', 'state': 'open'})
                self.fixture.members[10]['fields']['STATUS'] = status
                self.assertEqual(11 in self.frontier()['eligible'], expected)

    def test_native_blocker_graph_identity(self):
        before = self.fixture.stored()
        original = copy.deepcopy(self.fixture.issues[10])
        dependency = {**original, 'id': 999, 'node_id': 'OTHER'}
        for fault in ('textual-disagreement', 'cycle', 'identity-in-scope', 'identity-outside-scope'):
            with self.subTest(fault=fault):
                self.fixture.issues[10] = copy.deepcopy(original)
                if fault == 'textual-disagreement': self.fixture.deps[10] = [12]
                if fault == 'cycle':
                    self.fixture.deps[10] = [11]
                    self.fixture.issues[10]['body'] = original['body'].replace('None (can start immediately).', '- #11')
                if fault == 'identity-outside-scope': self.fixture.issues[10]['milestone'] = None
                with patch.object(gh, 'rest_pages', return_value=[dependency]), self.assertRaises(repositories.RepositoryError) as caught:
                    if fault.startswith('identity'):
                        REAL_DEPENDENCIES(self.github, 'example/product', 11)
                    else:
                        self.frontier()
                self.assertEqual(caught.exception.code, 'github_mismatch')
                self.database.rollback(); self.assertEqual(self.fixture.stored(), before)
                self.assertEqual(self.fixture.members[11]['fields']['STATUS'], 'Todo')
                self.assertNotIn('reservation', self.fixture.stored())


    def test_native_board_and_connection_contract_1(self):
        github = Mock()
        config = {'project_id': None, 'status_field': 'Status', 'statuses': {'ready': 'Todo', 'active': 'In Progress', 'done': 'Done'}, 'triage_label': 'agent-triage'}
        fields = [{'id': 'STATUS', 'name': 'Status', 'options': [{'id': name, 'name': name} for name in ('Todo', 'In Progress', 'Done')]}]
        boards = [{'id': 'P1', 'closed': False}, {'id': 'P2', 'closed': False}]
        rows = [('milestone', values) for values in ([], self.milestones * 2, [{**self.milestones[0], 'state': 'closed'}], [{**self.milestones[0], 'id': True}])]
        rows += [('fields', values) for values in ([], fields * 2, [{**fields[0], 'options': fields[0]['options'][:-1]}])]
        rows += [('milestone-access', None), ('board-access', None)]
        for boundary, values in rows:
            with self.subTest(boundary=boundary, values=values):
                with patch.object(gh, 'rest_pages', return_value=values) as rest, \
                        patch.object(gh, 'linked_projects', return_value=boards[:1]) as linked, \
                        patch.object(gh, 'project_fields', return_value=values):
                    if boundary == 'milestone-access': rest.side_effect = repositories.RepositoryError('github_blocked', 'no access')
                    if boundary == 'board-access': linked.side_effect = repositories.RepositoryError('projects_blocked', 'inaccessible')
                    with self.assertRaises(repositories.RepositoryError):
                        if boundary.startswith('milestone'): gh.resolve_milestone(github, 'example/product', 'm1')
                        else: gh.resolve_board(github, 'example/product', config)
        for field, value in (('id', 999), ('number', 2), ('title', 'renamed')):
            issue = {**self.fixture.issues[10], 'milestone': {**self.milestones[0], field: value}}
            with self.subTest(field=field), self.assertRaises(repositories.RepositoryError): gh.issue_scope(issue, self.fixture.board)
        with patch.object(gh, 'linked_projects', return_value=boards), patch.object(gh, 'project_fields', return_value=fields):
            with self.assertRaises(repositories.RepositoryError): gh.resolve_board(github, 'example/product', config)
            self.assertEqual(gh.resolve_board(github, 'example/product', {**config, 'project_id': 'P1'})['project_id'], 'P1')
            with self.assertRaises(repositories.RepositoryError): gh.resolve_board(github, 'example/product', {**config, 'project_id': 'absent'})
        github.request.assert_not_called()


    def test_connection_pagination_and_project_shape_validation(self):
        fetch = Mock(side_effect=[{'nodes': [1], 'pageInfo': {'hasNextPage': True, 'endCursor': 'next'}}, test_tickets.conn([2])])
        self.assertEqual(gh.pages(fetch), [1, 2]); self.assertEqual([c.args for c in fetch.call_args_list], [(None,), ('next',)])
        fetch = Mock(return_value={'nodes': [], 'pageInfo': {'hasNextPage': True, 'endCursor': 'repeat'}})
        with self.assertRaises(repositories.RepositoryError): gh.pages(fetch)
        good = self.fixture.project_items()[0]
        for changes in ({'fieldValues': test_tickets.conn([None])}, {'fieldValues': test_tickets.conn([{'field': None, 'optionId': 'Todo'}])},
                        {'fieldValues': test_tickets.conn([{'field': {'id': []}, 'optionId': 'Todo'}])},
                        {'fieldValues': test_tickets.conn([{'field': {'id': 'STATUS'}, 'optionId': []}])},
                        {'content': {'id': 'I10', 'number': [], 'repository': {'nameWithOwner': 'example/product'}}},
                        {'content': {'id': 'I10', 'number': 10, 'repository': []}}):
            with self.subTest(changes=changes), patch.object(gh, 'graph', return_value={'node': {'id': 'P1', 'items': test_tickets.conn([{**good, **changes}])}}):
                with self.assertRaises(repositories.RepositoryError) as caught: REAL_PROJECT_ITEMS(self.github, 'P1')
                self.assertEqual(caught.exception.code, 'github_mismatch')
        for field, value in (('state', []), ('labels', [{'name': []}]), ('state_reason', {})):
            with self.subTest(field=field), self.assertRaises(repositories.RepositoryError): gh.issue_identity({**self.fixture.issues[10], field: value}, 'example/product')

    def test_native_single_issue_publication_and_lost_membership_receipt(self):
        self.fixture.publication()
        def lost(*args):
            self.assertTrue(self.fixture.stored()['ticket_work']['intents']['note']['membership'])
            self.fixture.member(*args); raise repositories.RepositoryError('github_unavailable', 'lost board response')
        self.fixture.add_item.side_effect = lost
        blocked = tickets.publish(self.database, self.item, self.github)
        self.assertEqual(blocked['ticket_work']['status'], 'blocked'); self.assertEqual(len(self.fixture.issues), 1)
        self.fixture.add_item.reset_mock(); self.fixture.add_item.side_effect = self.fixture.member
        result = tickets.publish(self.database, blocked, self.github)
        self.assertTrue(result['ticket_work']['publication_complete']); self.fixture.add_item.assert_not_called()
        self.assertEqual(self.fixture.members[10]['fields'], {'STATUS': 'Todo'})
        self.assertEqual(self.fixture.issues[10]['milestone']['id'], 501); self.assertEqual(self.fixture.stored(), result)
