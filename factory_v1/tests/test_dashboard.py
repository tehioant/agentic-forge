"""Direct reader/render/router components; no server, controller or worker lifecycle."""
from copy import deepcopy
from datetime import datetime, timezone
from email.message import Message
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_v1 import dashboard


class DashboardTests(unittest.TestCase):
    def test_scoped_snapshot_is_sanitized_consistent_and_read_only(self):
        now = datetime(2026, 10, 17, tzinfo=timezone.utc)
        project = {'project_id': 'selected', 'iteration_id': 'iteration-1', 'repository': 'owner/product',
                   'planning': {'assignment_id': 'plan-1', 'instructions': 'private-canary'},
                   'ticket_work': {'assignment_id': 'tickets-1'},
                   'bug_work': {'bug-key': {'assignment_id': 'bug-1'}},
                   'origin': {'token': 'private-canary'}, 'idea': 'private-canary'}
        prepared = {'assignment_id': 'assignment-1', 'status': 'prepared',
                    'ticket_scope': {'project': 'selected', 'iteration': 'iteration-1',
                                     'repository': 'owner/product', 'issue_number': 61},
                    'handoff': {'stage': 'implementation', 'profile': {'role': 'implementation',
                                                                         'home': '/private-canary'},
                                'issue': {'number': 61}, 'instructions': 'private-canary'},
                    'submitted_result': {'transcript': 'private-canary'}}
        attempt = deepcopy(prepared)
        attempt['assignment_id'] = 'assignment-2'
        attempt['runtime'] = {'run_id': 'run-1', 'status': 'complete', 'container_removed': True,
                              'artifacts': '/private-canary'}
        attempt['submitted_result'] = {'assignment_id': 'assignment-2', 'run_id': 'run-1', 'status': 'done'}
        attempt['result_disposition'] = {'structurally_valid': True, 'isolated_execution': True}
        blocked = deepcopy(attempt)
        blocked['assignment_id'] = 'assignment-4'
        blocked['submitted_result'].update(assignment_id='assignment-4', status='blocked')
        unknown = {'assignment_id': 'assignment-3', 'ticket_scope': {
            'project': 'selected', 'iteration': 'iteration-1', 'repository': 'owner/product'}}
        foreign = deepcopy(prepared)
        foreign['assignment_id'] = 'foreign'
        foreign['ticket_scope']['project'] = 'other'
        foreign['handoff']['profile']['role'] = 'foreign-canary'
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'records.sqlite'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT);'
                                 'CREATE TABLE role_assignments(assignment_id TEXT, payload TEXT);')
                db.execute('INSERT INTO iterations VALUES(?,?,?)', ('selected', 'iteration-1', json.dumps(project)))
                db.execute('INSERT INTO iterations VALUES(?,?,?)', ('other', 'foreign-iteration', 'invalid private-canary'))
                db.executemany('INSERT INTO role_assignments VALUES(?,?)',
                               [(a['assignment_id'], json.dumps(a)) for a in (prepared, attempt, unknown, foreign, blocked)])
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            original_connect = sqlite3.connect
            statements = []
            test = self

            class ReadConnection(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    statements.append(sql)
                    result = super().execute(sql, parameters)
                    if sql == 'BEGIN':
                        test.assertEqual(super().execute('PRAGMA query_only').fetchone()[0], 1)
                        with test.assertRaises(sqlite3.OperationalError):
                            super().execute('CREATE TABLE forbidden(value)')
                        test.assertTrue(self.in_transaction)
                    return result

            def connect(filename, **kwargs):
                self.assertTrue(filename.endswith('?mode=ro'))
                self.assertTrue(kwargs['uri'])
                return original_connect(filename, factory=ReadConnection, **kwargs)

            with patch.object(dashboard.sqlite3, 'connect', side_effect=connect) as boundary:
                data = dashboard.snapshot(path, 'selected', now=now)
                boundary.assert_called_once()
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            self.assertEqual(set(p.name for p in Path(root).iterdir()), {'records.sqlite'})
            self.assertEqual(statements.count('BEGIN'), 1)
            self.assertEqual(data['project'], {'id': 'selected', 'repositories': ['owner/product']})
            self.assertEqual(data['sources']['forge'], {'status': 'ok', 'fetched_at': now.isoformat(),
                                                       'observed_at': None, 'error': None})
            self.assertEqual(set(data), {'version', 'project', 'sources', 'workers', 'board', 'fragments'})
            self.assertEqual(data['sources']['github']['status'], 'unconfigured')
            workers = {r['id']: r for r in data['workers']}
            self.assertEqual(set(workers), {'plan-1', 'tickets-1', 'bug-1', 'assignment-1', 'assignment-2', 'assignment-3', 'assignment-4'})
            self.assertEqual(workers['assignment-2']['status']['state'], 'done')
            self.assertEqual(workers['assignment-4']['status']['state'], 'waiting')
            self.assertEqual(workers['assignment-1']['status']['state'], 'waiting')
            self.assertEqual(workers['plan-1']['status']['label'], 'Record (activity unknown)')
            view = dashboard.render_page(data)
            self.assertIn('Done (recorded assignment)', view)
            self.assertIn('Waiting (blocked result)', view)
            self.assertIn('Worker reported a blocked outcome.', view)
            self.assertIn('assignment-2', view)
            self.assertIn(data['fragments']['forge'], view)
            self.assertEqual(workers['assignment-1']['kind'], 'prepared_assignment')
            self.assertEqual(workers['assignment-2']['kind'], 'worker_attempt')
            self.assertEqual(workers['assignment-2']['run_id'], 'run-1')
            self.assertEqual(workers['plan-1']['kind'], 'planning_record')
            self.assertEqual(workers['tickets-1']['stage'], 'to-tickets')
            self.assertEqual(workers['bug-1']['kind'], 'synthesis_record')
            self.assertEqual(workers['assignment-1']['issue_url'], 'https://github.com/owner/product/issues/61')
            self.assertEqual(workers['assignment-1']['role'], 'implementation')
            self.assertEqual(workers['assignment-1']['stage'], 'implementation')
            self.assertIsNone(workers['assignment-3']['task'])
            self.assertIsNone(workers['assignment-3']['role'])
            self.assertIsNone(workers['assignment-3']['stage'])
            expected_keys = {'id', 'iteration_id', 'kind', 'role', 'stage', 'issue_number', 'issue_url', 'task', 'run_id', 'status'}
            self.assertTrue(all(set(r) == expected_keys for r in workers.values()))
            self.assertNotIn('canary', json.dumps(data))
            self.assertTrue(all('runtime' not in worker for worker in workers.values()))
            board = {'status': 'available', 'observed_at': '2026-10-16T00:00:00+00:00', 'error': None,
                     'project': {'id': 'PVT_selected', 'title': '<unsafe> board', 'repository': 'owner/product',
                                 'url': 'https://github.com/users/owner/projects/1'},
                     'tickets': [{'number': 61, 'title': '<img> ticket', 'url': 'https://github.com/owner/product/issues/61',
                                  'column': 'Todo', 'source_status': 'Todo', 'derived_blocked': False, 'blockers': []}]}
            with patch.object(dashboard, 'read_board', return_value=board) as github:
                combined = dashboard.snapshot(path, 'selected', now=now, repository='owner/product', github_project='PVT_selected')
                github.assert_called_once_with('owner/product', 'PVT_selected')
                self.assertEqual(combined['sources']['github']['scope'], 'recorded_repository_match')
                self.assertEqual(combined['sources']['github']['observed_at'], board['observed_at'])
                self.assertIsNone(combined['sources']['forge']['observed_at'])
                self.assertEqual(combined['board']['tickets'][0]['column'], 'Todo')
                view = dashboard.render_page(combined)
                self.assertLess(view.index('Recorded workers'), view.index('GitHub Projects tickets'))
                self.assertIn('&lt;img&gt; ticket', view)
                self.assertNotIn('<img>', view)
                github.reset_mock()
                mismatch = dashboard.snapshot(path, 'selected', repository='other/repo', github_project='PVT_selected')
                self.assertEqual(mismatch['sources']['github']['scope'], 'mismatch')
                github.assert_not_called()
                missing_forge = dashboard.snapshot(Path(root) / 'absent.sqlite', 'selected', repository='owner/product', github_project='PVT_selected')
                self.assertEqual(missing_forge['sources']['forge']['status'], 'unavailable')
                self.assertEqual(missing_forge['sources']['github']['status'], 'ok')
                self.assertIn('scope unverified', missing_forge['fragments']['github'])
                github.return_value = {'status': 'unavailable', 'observed_at': None, 'project': None, 'tickets': [], 'error': 'GitHub board observation unavailable.'}
                github_failed = dashboard.snapshot(path, 'selected', repository='owner/product', github_project='PVT_selected')
                self.assertEqual(github_failed['sources']['forge']['status'], 'ok')
                self.assertEqual(github_failed['sources']['github']['status'], 'unavailable')
                self.assertIn('Done (recorded assignment)', github_failed['fragments']['forge'])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            # Independent corrupt/scope-drift inputs, not lifecycle precursors.
            bad_iteration = dict(project, project_id='other')
            bad_assignment = deepcopy(prepared)
            bad_assignment['ticket_scope']['repository'] = 'other/repository'
            bad_issue = deepcopy(prepared)
            bad_issue['handoff']['issue']['number'] = 99
            for name, iteration, assignment in (
                ('indexed-payload-project-mismatch', bad_iteration, prepared),
                ('repository-scope-mismatch', project, bad_assignment),
                ('issue-scope-mismatch', project, bad_issue),
                ('assignment-id-mismatch', project, dict(prepared, assignment_id='changed')),
                ('corrupt-json', project, None),
            ):
                with self.subTest(name=name), sqlite3.connect(path) as db:
                    db.execute('UPDATE iterations SET payload=? WHERE project_id=?', (json.dumps(iteration), 'selected'))
                    db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                               (json.dumps(assignment) if assignment else '{', 'assignment-1'))
                    db.commit()
                    result = dashboard.snapshot(path, 'selected', now=now)
                    self.assertEqual(result['sources']['forge']['status'], 'unavailable')
                    self.assertEqual(result['workers'], [])
                    self.assertNotIn('canary', json.dumps(result))
            with sqlite3.connect(path) as db:
                db.executescript('DROP TABLE role_assignments; DELETE FROM iterations;')
            empty = dashboard.snapshot(path, 'selected', now=now)
            self.assertEqual(empty['sources']['forge']['status'], 'ok')
            self.assertEqual(empty['workers'], [])
            missing = Path(root) / 'missing.sqlite'
            self.assertEqual(dashboard.snapshot(missing, 'selected')['sources']['forge']['status'], 'unavailable')
            self.assertFalse(missing.exists())
            self.assertEqual(set(p.name for p in Path(root).iterdir()), {'records.sqlite'})

    def test_actual_page_routes_empty_error_and_local_http_boundary(self):
        data = {'version': 1, 'project': {'id': 'selected', 'repositories': ['owner/product']},
                'sources': {'forge': {'status': 'ok', 'fetched_at': '2026-10-17T00:00:00+00:00',
                                      'observed_at': None, 'error': None},
                            'github': {'status': 'unconfigured', 'fetched_at': None, 'observed_at': None, 'error': None, 'scope': 'unverified'}},
                'workers': [], 'board': {'status': 'unconfigured', 'tickets': []}}
        application = dashboard.Dashboard(Path('/not-read-by-this-test'), 'selected')
        with patch.object(dashboard, 'snapshot', return_value=data) as read:
            html = application.route('GET', '/')
            self.assertEqual(html.status, 200)
            self.assertIn(b'<h1>selected</h1>', html.body)
            self.assertIn(b'No recorded assignments', html.body)
            self.assertEqual(json.loads(application.route('GET', '/api/snapshot').body), data)
            self.assertEqual(read.call_count, 2)
            for method, path, status in [('POST', '/', 405), ('DELETE', '/api/snapshot', 405),
                                         ('GET', '/logs', 404), ('GET', '/api/snapshot?project=other', 404)]:
                with self.subTest(method=method, path=path):
                    read.reset_mock()
                    self.assertEqual(application.route(method, path).status, status)
                    read.assert_not_called()
        for kind, label in dashboard.KINDS.items():
            with self.subTest(kind=kind):
                record = {'id': '<script>private()</script>', 'iteration_id': 'old-iteration', 'kind': kind,
                          'role': 'implementation', 'task': 'Issue #61', 'stage': None, 'issue_number': 61,
                          'issue_url': 'https://github.com/owner/product/issues/61', 'run_id': None,
                          'status': {'state': 'waiting', 'label': 'Waiting (blocked result)', 'reason': 'Worker reported a blocked outcome.',
                                     'blocker': {'number': 7, 'url': 'https://github.com/owner/product/issues/7'}}}
                view = dashboard.render_page(dict(data, workers=[record]))
                self.assertIn(label, view)
                self.assertIn('href="https://github.com/owner/product/issues/61"', view)
                self.assertIn('old-iteration', view)
                self.assertIn('Unknown', view)
                self.assertNotIn('<script>', view)
                self.assertIn('&lt;script&gt;', view)
                self.assertIn('Blocked by', view)
                self.assertIn('href="https://github.com/owner/product/issues/7"', view)
        unavailable = deepcopy(data)
        unavailable['sources']['forge']['status'] = 'unavailable'
        view = dashboard.render_page(unavailable)
        self.assertIn('Forge source unavailable', view)
        self.assertNotIn('No recorded assignments', view)
        self.assertIn('Source observation time: Unknown', view)
        args = dashboard.parser().parse_args(['--database', '/explicit/records.sqlite', '--project', 'selected'])
        self.assertEqual(args.port, 8787)
        self.assertEqual(args.project, 'selected')
        for options in ({'repository': 'owner/product'}, {'github_project': 'PVT_selected'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                dashboard.Dashboard(Path('/unused'), 'selected', **options)
        selected = dashboard.Dashboard(Path('/unused'), 'selected', repository='owner/product', github_project='PVT_selected')
        with patch.object(dashboard, 'snapshot', return_value=data) as read:
            selected.route('GET', '/api/snapshot')
            read.assert_called_once_with(Path('/unused'), 'selected', repository='owner/product', github_project='PVT_selected')
        # Exercise HTTP headers and local-origin refusal without constructing a socket/server.
        handler_type = dashboard.handler_for(application, 8787)
        for host, site, status in [('127.0.0.1:8787', 'same-origin', 200),
                                    ('localhost:8787', 'none', 200),
                                    ('attacker.example:8787', 'same-origin', 403),
                                    ('127.0.0.1:8787', 'cross-site', 403)]:
            with self.subTest(host=host, site=site):
                handler = object.__new__(handler_type)
                handler.server = Mock(server_port=8787)
                handler.headers = Message()
                handler.headers['Host'] = host
                handler.headers['Sec-Fetch-Site'] = site
                handler.command, handler.path, handler.wfile = 'GET', '/api/snapshot', BytesIO()
                handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
                with patch.object(application, 'route', return_value=dashboard.Response(200, b'{}', 'application/json')) as route:
                    handler.respond()
                    handler.send_response.assert_called_once_with(status)
                    handler.send_header.assert_any_call('Cache-Control', 'no-store')
                    handler.send_header.assert_any_call('X-Content-Type-Options', 'nosniff')
                    self.assertEqual(route.call_count, int(status == 200))
