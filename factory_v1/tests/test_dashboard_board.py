"""Focused board/query-boundary checks; no live GitHub, server or workers."""
import copy
import json
import subprocess
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from factory_v1 import dashboard_board as board


REPOSITORY = 'example/product'
PROJECT = 'PVT_example'
OBSERVED = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)


def connection(nodes, more=False, cursor=None):
    return {'nodes': nodes, 'pageInfo': {'hasNextPage': more, 'endCursor': cursor}}


def linked(ids=None, more=False, cursor=None):
    return {'repository': {'nameWithOwner': REPOSITORY,
                           'projectsV2': connection([{'id': value} for value in
                                                    (ids if ids is not None else [PROJECT])], more, cursor)}}


def items(values, more=False, cursor=None):
    return {'node': {'id': PROJECT, 'title': '<Board>',
                     'url': 'https://github.com/users/example/projects/2',
                     'items': connection(values, more, cursor)}}


def issue(number, status: str | None = 'Todo', repository=REPOSITORY, blockers=()):
    return {'id': f'item{number}', 'fieldValueByName': {'name': status} if status else None,
            'content': {'__typename': 'Issue', 'number': number, 'title': f'<script>ticket {number}</script>',
                        'url': f'https://github.com/{repository}/issues/{number}',
                        'repository': {'nameWithOwner': repository},
                        'blockedBy': connection(list(blockers))}}


def dependency(number, state='OPEN', repository=REPOSITORY):
    return {'number': number, 'url': f'https://github.com/{repository}/issues/{number}',
            'state': state, 'repository': {'nameWithOwner': repository}}


class DashboardBoardTests(unittest.TestCase):
    def test_scoped_paginated_projection_and_actual_renderer(self):
        values = [issue(1), issue(2, 'In Progress'), issue(3, 'Blocked'), issue(4, 'Done'),
                  issue(5, 'Review'), issue(6, None),
                  issue(7, 'In Progress', blockers=[dependency(90), dependency(91, 'CLOSED')]),
                  issue(8, 'Done', blockers=[dependency(90)]),
                  issue(9, 'Review', blockers=[dependency(1, repository='other/private')]),
                  issue(10, repository='other/private'),
                  {'id': 'draft', 'content': {'__typename': 'DraftIssue'}},
                  {'id': 'deleted', 'content': None}]
        values[0]['content']['body'] = 'SECRET_BODY /private/host/path'
        values[0]['content']['title'] += ' ghp_credentialcanary'
        read = Mock(side_effect=[linked(['PVT_other'], True, 'linked2'), linked(),
                                 items(values[:6], True, 'items2'), items(values[6:])])
        clock = Mock(return_value=OBSERVED)
        observation = board.read_board(REPOSITORY, PROJECT,
                                       transport=SimpleNamespace(query=read), now=clock)
        self.assertEqual(observation['status'], 'available')
        self.assertEqual(observation['observed_at'], OBSERVED.isoformat())
        self.assertEqual(read.call_count, 4)
        self.assertEqual(read.call_args_list[1].args[1]['cursor'], 'linked2')
        self.assertEqual(read.call_args_list[3].args[1]['cursor'], 'items2')
        tickets = {ticket['number']: ticket for ticket in observation['tickets']}
        self.assertEqual(list(tickets), list(range(1, 10)))
        self.assertEqual([ticket['column'] for ticket in tickets.values()],
                         ['Todo', 'In Progress', 'Blocked', 'Done', 'Unknown', 'Unknown',
                          'Blocked', 'Done', 'Blocked'])
        self.assertEqual(tickets[7]['source_status'], 'In Progress')
        self.assertTrue(tickets[7]['derived_blocked'])
        self.assertEqual(tickets[7]['blockers'], [{'number': 90, 'url':
                                                 'https://github.com/example/product/issues/90'}])
        self.assertFalse(tickets[8]['derived_blocked'])
        self.assertEqual(tickets[9]['blockers'], [])
        rendered = board.render_board(observation)
        for column in board.COLUMNS:
            self.assertIn(f'<h3>{column}</h3>', rendered)
        self.assertIn('href="https://github.com/example/product/issues/7"', rendered)
        self.assertIn('href="https://github.com/example/product/issues/90"', rendered)
        self.assertIn('Source status: Review', rendered)
        self.assertIn('Blocked (derived)', rendered)
        self.assertIn('&lt;script&gt;ticket 1&lt;/script&gt;', rendered)
        self.assertIn('&lt;Board&gt;', rendered)
        self.assertNotIn('<script>', rendered)
        serialized = json.dumps(observation) + rendered
        for private in ('SECRET_BODY', '/private/host', 'credentialcanary', 'other/private'):
            self.assertNotIn(private, serialized)
        retained = copy.deepcopy(observation)
        retained['status'] = 'unavailable'
        retained['tickets'][0]['url'] = 'javascript:alert(1)'
        stale_html = board.render_board(retained)
        self.assertIn('stale', stale_html)
        self.assertIn(OBSERVED.isoformat(), stale_html)
        self.assertNotIn('javascript:', stale_html)
        empty = board.read_board(REPOSITORY, PROJECT,
                                 transport=SimpleNamespace(query=Mock(side_effect=[linked(), items([])])), now=clock)
        self.assertIn('No tickets', board.render_board(empty))
        self.assertEqual(clock.call_count, 2)

    def test_fail_closed_source_freshness_bounds_and_read_only_transport(self):
        wrong_repo = linked()
        wrong_repo['repository']['nameWithOwner'] = 'other/private'
        wrong_project = items([])
        wrong_project['node']['id'] = 'PVT_other'
        truncated = issue(1)
        truncated['content']['blockedBy']['pageInfo']['hasNextPage'] = True
        bad_url = issue(1)
        bad_url['content']['url'] = 'https://evil.example/issues/1'
        cases = {
            'unlinked': [linked(['PVT_other'])],
            'ambiguous': [linked([PROJECT, PROJECT])],
            'repo_identity': [wrong_repo],
            'project_identity': [linked(), wrong_project],
            'blocker_truncation': [linked(), items([truncated])],
            'issue_identity': [linked(), items([bad_url])],
            'duplicate': [linked(), items([issue(1), issue(1)])],
            'cursor_cycle': [linked([], True, 'same'), linked([], True, 'same')],
            'page_bound': [linked([], True, str(i)) for i in range(board.MAX_PAGES)],
            'source_failure': [board.BoardUnavailable('bearer SECRET /private/path')],
        }
        for name, responses in cases.items():
            with self.subTest(name=name):
                clock = Mock(return_value=OBSERVED)
                read = Mock(side_effect=responses)
                observation = board.read_board(REPOSITORY, PROJECT,
                                               transport=SimpleNamespace(query=read), now=clock)
                self.assertEqual(observation['status'], 'unavailable')
                self.assertIsNone(observation['observed_at'])
                self.assertEqual(observation['tickets'], [])
                clock.assert_not_called()
                self.assertNotIn('SECRET', json.dumps(observation))
                self.assertNotIn('/private', board.render_board(observation))
                self.assertNotIn('No tickets', board.render_board(observation))
        transport = board.HostGitHubReadTransport(REPOSITORY, PROJECT)
        with patch.object(board.subprocess, 'run') as run:
            refusals = [
                ('mutation { deleteProjectV2(input:{projectId:"x"}) { clientMutationId } }', {}),
                ('query { viewer { login } }', {}),
                (board.LINKED_PROJECTS, {'owner': 'other', 'name': 'private', 'cursor': None}),
                (board.PROJECT_ITEMS, {'project': 'PVT_other', 'cursor': None}),
                (board.PROJECT_ITEMS, {'project': PROJECT, 'cursor': None, 'url': 'https://evil.example'}),
            ]
            for document, variables in refusals:
                with self.subTest(document=document, variables=variables):
                    with self.assertRaises(board.BoardUnavailable):
                        transport.query(document, variables)
            run.assert_not_called()
            variables = {'project': PROJECT, 'cursor': None}
            run.return_value = SimpleNamespace(stdout=json.dumps({'data': items([])}))
            self.assertEqual(transport.query(board.PROJECT_ITEMS, variables), items([]))
            args, kwargs = run.call_args
            self.assertEqual(args[0], ['gh', 'api', '--hostname', 'github.com', '--method',
                                       'POST', 'graphql', '--input', '-'])
            self.assertNotIn('shell', kwargs)
            self.assertEqual(kwargs['timeout'], 8)
            self.assertEqual(json.loads(kwargs['input']), {'query': board.PROJECT_ITEMS, 'variables': variables})
            for response in ('{"errors":[{"message":"SECRET"}],"data":{}}', 'SECRET not json'):
                run.return_value = SimpleNamespace(stdout=response)
                with self.assertRaisesRegex(board.BoardUnavailable, '^GitHub board read failed.$'):
                    transport.query(board.PROJECT_ITEMS, variables)
            run.side_effect = subprocess.TimeoutExpired('SECRET', 8)
            with self.assertRaises(board.BoardUnavailable):
                transport.query(board.PROJECT_ITEMS, variables)
            run.side_effect = None
            transport.calls = board.MAX_PAGES * 2
            run.reset_mock()
            with self.assertRaises(board.BoardUnavailable):
                transport.query(board.PROJECT_ITEMS, variables)
            run.assert_not_called()
            for repository, project in [('https://evil.example/x', PROJECT), (REPOSITORY, None)]:
                observation = board.read_board(repository, project)
                self.assertEqual(observation['status'], 'unavailable')
            run.assert_not_called()
