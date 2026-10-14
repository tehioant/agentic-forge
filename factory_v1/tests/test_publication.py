"""Direct publication component checks; legacy helpers are not discovered."""
import base64
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from factory_v1 import publication
from factory_v1.repositories import GitHub, RepositoryError
from factory_v1.sandbox_source import manifest
from factory_v1.tests import test_assignments


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.assignments = test_assignments.AssignmentTests()
        self.assignments.setUp()
        self.addCleanup(self.assignments.doCleanups)
        self.fixture = self.assignments.fixture
        self.root = self.fixture.root
        self.assignment = self.assignments.ok(self.assignments.command())
        self.artifacts = self.root / 'run-artifacts'
        self.artifacts.mkdir(mode=0o700)
        self.source = self.artifacts / 'workspace'
        self.baseline = self.artifacts / 'inputs/baseline'
        for directory in (self.source, self.baseline):
            directory.mkdir(parents=True)
            (directory / 'factory_v1').mkdir()
            (directory / 'factory_v1/behavior.py').write_text('old behavior\n')
            (directory / 'test_behavior.py').write_text('old tests\n')
        (self.source / 'factory_v1/behavior.py').write_text('new behavior\n')
        self.refresh_source()
        (self.artifacts / 'initial-source.json').write_text(json.dumps({'baseline': manifest(self.baseline)}))
        self.assignment['runtime'] = {'status': 'complete', 'run_id': 'fixture-run', 'container_removed': True,
                                      'artifacts': str(self.artifacts)}
        self.assignment['result_disposition'] = {'isolated_execution': True, 'trusted_execution': False,
                                                 'advance_allowed': False, 'close_allowed': False}
        # Fixture-only trusted-host injection. This is not a worker isolation receipt.
        self.calls = []
        self.commits = {}
        self.commit_readback_overrides = {}
        self.refs = {'main': 'a' * 40}
        self.prs = []
        self.drop = None
        self.repo = {'id': 123, 'full_name': 'example/product', 'default_branch': 'main'}
        self.issue = copy.deepcopy(self.assignments.tracker.issues[10])
        self.entries = []
        for path, item in manifest(self.baseline).items():
            self.entries.append({'path': path, 'mode': '100644', 'type': 'blob',
                                 'sha': publication.object_sha('blob', (self.baseline / path).read_bytes())})
        subtree = publication.tree_sha([dict(e, path='behavior.py') for e in self.entries if e['path'].startswith('factory_v1/')])
        self.entries.extend([{'path': 'factory_v1', 'mode': '040000', 'type': 'tree', 'sha': subtree},
                             {'path': 'opaque', 'mode': '160000', 'type': 'commit', 'sha': 'e' * 40},
                             {'path': 'link', 'mode': '120000', 'type': 'blob', 'sha': 'f' * 40}])
        self.tree = publication.tree_sha([e for e in self.entries if '/' not in e['path']])
        self.commits['a' * 40] = {'sha': 'a' * 40, 'tree': {'sha': self.tree}}
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                case.calls.append(('GET', self.path, None))
                path = self.path.removeprefix('/repos/example/product')
                value = None
                if path == '':
                    value = case.repo
                elif path == '/issues/10':
                    value = case.issue
                elif path.startswith('/git/commits/'):
                    value = copy.deepcopy(case.commits.get(path.split('/')[-1]))
                    if isinstance(value, dict) and 'message' in value:
                        value.update(case.commit_readback_overrides)
                elif path == '/git/trees/' + case.tree + '?recursive=1':
                    value = {'sha': case.tree, 'tree': case.entries, 'truncated': False}
                elif path.startswith('/git/ref/heads/'):
                    branch = path.removeprefix('/git/ref/heads/')
                    if branch in case.refs:
                        value = {'ref': 'refs/heads/' + branch, 'object': {'type': 'commit', 'sha': case.refs[branch]}}
                elif path.startswith('/pulls?'):
                    value = case.read_prs()
                elif path == '/pulls/20' and case.prs:
                    value = case.read_prs()[0]
                elif path == '/issues/20' and case.prs:
                    value = {'number': 20, 'state': 'open', 'body': case.prs[0]['body'], 'pull_request': {},
                             'repository_url': 'https://api.github.com/repos/example/product'}
                self.reply(404 if value is None else 200, {} if value is None else value)

            def do_POST(self):
                self.mutate()

            def do_PATCH(self):
                self.mutate()

            def mutate(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.calls.append((self.command, self.path, body))
                # Verify intent exists at the actual HTTP side-effect boundary.
                with closing(sqlite3.connect(case.fixture.state)) as database:
                    stored = json.loads(database.execute('SELECT payload FROM role_assignments').fetchone()[0])
                    assert stored['publication']['status'] == 'pending'
                    assert 'bearer' not in stored['publication']['approval']
                path = self.path.removeprefix('/repos/example/product')
                if path == '/git/blobs':
                    value = {'sha': publication.object_sha('blob', base64.b64decode(body['content']))}
                elif path == '/git/trees':
                    value = {'sha': case.plan['tree']}
                    assert body['base_tree'] == case.tree
                elif path == '/git/commits':
                    raw = f"tree {body['tree']}\nparent {body['parents'][0]}\nauthor Factory Controller <factory-controller@users.noreply.github.com> 1767225600 +0000\ncommitter Factory Controller <factory-controller@users.noreply.github.com> 1767225600 +0000\n\n{body['message']}".encode()
                    actual = subprocess.run(['/usr/bin/git', 'hash-object', '--stdin', '-t', 'commit'], input=raw, capture_output=True, check=True).stdout.decode().strip()
                    assert body['message'].endswith('\n')
                    value = {'sha': actual, 'tree': {'sha': body['tree']}, 'parents': [{'sha': body['parents'][0]}], 'message': body['message'][:-1]}
                    case.commits[actual] = value
                elif path == '/git/refs':
                    case.refs[body['ref'].removeprefix('refs/heads/')] = body['sha']
                    value = {}
                elif path.startswith('/git/refs/heads/'):
                    assert body['force'] is False
                    case.refs[path.removeprefix('/git/refs/heads/')] = body['sha']
                    value = {}
                elif path == '/pulls':
                    assert not case.prs
                    case.prs.append(dict(body, number=20, state='open', merged=False))
                    value = case.read_prs()[0]
                else:
                    self.reply(403, {})
                    return
                if case.drop == path:
                    case.drop = None
                    self.close_connection = True
                    return
                self.reply(201, value)

            def reply(self, status, value):
                self.send_response(status)
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.assignment['handoff']['tracker']['api_base'] = self.base
        self.assignment['handoff_digest'] = publication.assignments.digest(self.assignment['handoff'])
        with closing(sqlite3.connect(self.fixture.state)) as database, database:
            item = json.loads(database.execute('SELECT payload FROM iterations').fetchone()[0])
            item['repository_onboarding']['api_base'] = self.base
            database.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
        self.persist_assignment()
        self.config = {'operator_id': '42', 'reference': 'fixture authenticated operator approval',
                       'execution_reference': 'fixture trusted host checked execution; NOT live evidence',
                       'binding': publication.binding(self.assignment), 'manifest': manifest(self.source),
                       'paths': ['factory_v1/behavior.py'], 'policy_paths': [], 'repository': 'example/product',
                       'repository_id': 123, 'branch': 'factory/issue-10', 'base': 'main', 'issue': 10,
                       'expected_head': 'a' * 40, 'candidate': 'b' * 40, 'commit_date': '2026-01-01T00:00:00Z',
                       'api_base': self.base, 'bearer': 'HOST-ONLY-SECRET-FIXTURE'}
        self.config_path = self.root / 'private-publication.json'
        self.write_config()
        result = self.cli('--plan-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.plan = json.loads(result.stdout)
        self.config['candidate'] = self.plan['candidate']
        self.write_config()
        self.calls.clear()

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def refresh_source(self):
        (self.artifacts / 'source-artifacts.json').write_text(json.dumps(manifest(self.source)))

    def read_prs(self):
        result = copy.deepcopy(self.prs)
        for pr in result:
            branch = pr['head']
            base = pr['base']
            pr['head'] = {'ref': branch, 'sha': self.refs[branch], 'repo': self.repo}
            pr['base'] = {'ref': base, 'sha': self.refs[base], 'repo': self.repo}
        return result

    def persist_assignment(self):
        with closing(sqlite3.connect(self.fixture.state)) as database, database:
            database.execute('UPDATE role_assignments SET payload=?', (json.dumps(self.assignment),))

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)

    def cli(self, *extra):
        return self.fixture.cli('publish-candidate', '--project', 'product', '--iteration', 'm1',
                                '--assignment', self.assignment['assignment_id'], '--publication-config', str(self.config_path), *extra)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(self.config['bearer'], result.stdout + result.stderr)
        return json.loads(result.stdout)

    def writes(self):
        return [c for c in self.calls if c[0] != 'GET']

    def deny(self, code=None):
        result = self.cli()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        if code:
            self.assertEqual(json.loads(result.stderr)['error'], code)
        self.assertNotIn(self.config['bearer'], result.stdout + result.stderr)
        self.assertEqual(self.writes(), [])

    def stored(self):
        with closing(sqlite3.connect(self.fixture.state)) as database:
            return json.loads(database.execute('SELECT payload FROM role_assignments').fetchone()[0])










































# Legacy PublicationTests has helper methods only; focused tests use explicit state below.
import datetime
import inspect


def git_object(kind, data):
    """Independent Git wire-object oracle: real bytes, no Git process."""
    return hashlib.sha1(kind.encode() + b' ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def git_tree(entries):
    ordered = sorted(entries, key=lambda entry: entry['path'].encode() + (b'/' if entry['type'] == 'tree' else b''))
    data = b''.join(entry['mode'].lstrip('0').encode() + b' ' + entry['path'].encode() + b'\0' + bytes.fromhex(entry['sha']) for entry in ordered)
    return git_object('tree', data)


class PublicationComponentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / 'state.sqlite'
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir(mode=0o700)
        self.source = self.artifacts / 'workspace'
        self.baseline = self.artifacts / 'inputs/baseline'
        for folder in (self.source, self.baseline):
            (folder / 'factory_v1').mkdir(parents=True)
            (folder / 'factory_v1/behavior.py').write_bytes(b'old behavior\n')
            (folder / 'test_behavior.py').write_bytes(b'old tests\n')
        (self.source / 'factory_v1/behavior.py').write_bytes(b'new behavior\n')
        self.refresh_source()
        (self.artifacts / 'initial-source.json').write_text(json.dumps({'baseline': manifest(self.baseline)}))
        self.repo = {'id': 123, 'full_name': 'example/product', 'default_branch': 'main'}
        self.issue = {'id': 10, 'node_id': 'ISSUE_10', 'number': 10, 'state': 'open',
                      'body': 'Pinned work body', 'repository_url': 'https://api.github.com/repos/example/product'}
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
                     'status': 'active', 'execution_allowed': False,
                     'approval': {'operator_id': '42', 'reference': 'approved'},
                     'origin': {'platform': 'discord', 'chat_id': '123', 'thread_id': '123', 'parent_chat_id': '456', 'scope_id': '789'},
                     'handoff': {'commit': 'a' * 40},
                     'ticket_work': {'assignment_id': 'synthesis-1', 'input_digest': 'inputs-1'},
                     'repository_onboarding': {'api_base': 'http://127.0.0.1:8765', 'metadata': {'id': 123}}}
        revision = {'spec': self.item['handoff'], 'ticket_assignment': 'synthesis-1', 'ticket_inputs': 'inputs-1'}
        handoff = {'stage': 'implementation', 'profile': {'role': 'implementation', 'name': 'factory-worker', 'home': str(self.root / 'profile')},
                   'workspace': str(self.root / 'assigned'), 'repository': 'example/product', 'repository_id': 123,
                   'issue': {k: self.issue[k] for k in ('id', 'node_id', 'number')} | {'body_sha256': hashlib.sha256(self.issue['body'].encode()).hexdigest()},
                   'spec_commit': 'a' * 40, 'baseline': 'a' * 40, 'candidate': None,
                   'capabilities': ['write_workspace'], 'skills': [], 'tracker': {'api_base': 'http://127.0.0.1:8765'}}
        self.assignment = {'assignment_id': 'd' * 64, 'claim_id': 'claim-1', 'revision': revision,
                           'ticket_scope': {'project': 'product', 'iteration': 'm1', 'repository': 'example/product', 'issue_number': 10},
                           'handoff': handoff, 'handoff_digest': publication.assignments.digest(handoff),
                           'runtime': {'status': 'complete', 'run_id': 'run-1', 'container_removed': True, 'artifacts': str(self.artifacts)},
                           'result_disposition': {'isolated_execution': True, 'trusted_execution': False, 'advance_allowed': False, 'close_allowed': False}}
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
            db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(self.item)))
            db.execute('CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT)')
            db.execute('INSERT INTO role_assignments VALUES (?,?)', (self.assignment['assignment_id'], json.dumps(self.assignment)))
        Path(str(self.state) + '.onboarding.lock').touch(mode=0o600)
        self.entries = [{'path': name, 'mode': '100644', 'type': 'blob', 'sha': git_object('blob', (self.baseline / name).read_bytes())} for name in manifest(self.baseline)]
        subtree = git_tree([dict(e, path='behavior.py') for e in self.entries if e['path'].startswith('factory_v1/')])
        self.entries.extend([{'path': 'factory_v1', 'mode': '040000', 'type': 'tree', 'sha': subtree},
                             {'path': 'opaque', 'mode': '160000', 'type': 'commit', 'sha': 'e' * 40},
                             {'path': 'link', 'mode': '120000', 'type': 'blob', 'sha': 'f' * 40}])
        self.tree = git_tree([e for e in self.entries if '/' not in e['path']])
        self.commits = {'a' * 40: {'sha': 'a' * 40, 'tree': {'sha': self.tree}}}
        self.refs = {'main': 'a' * 40}
        self.prs = []
        self.calls = []
        self.drop = None
        self.overrides = {}
        self.config = {'operator_id': '42', 'reference': 'explicit host operator approval',
                       'execution_reference': 'component-only host authorization; NOT live worker proof',
                       'binding': publication.binding(self.assignment), 'manifest': manifest(self.source),
                       'paths': ['factory_v1/behavior.py'], 'policy_paths': [], 'repository': 'example/product',
                       'repository_id': 123, 'branch': 'factory/issue-10', 'base': 'main', 'issue': 10,
                       'expected_head': 'a' * 40, 'candidate': 'b' * 40, 'commit_date': '2026-01-01T00:00:00Z',
                       'api_base': 'http://127.0.0.1:8765', 'bearer': 'HOST-ONLY-SECRET-FIXTURE'}
        self.config_path = self.root / 'publication.json'
        self.write_config()
        for target in ('socket.socket', 'subprocess.Popen'):
            patcher = mock.patch(target, side_effect=AssertionError('external creation denied'))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(publication.GitHub, 'request', side_effect=self.external)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.plan = self.publish(plan_only=True)
        self.config['candidate'] = self.plan['candidate']
        self.write_config()
        self.calls.clear()

    def refresh_source(self):
        (self.artifacts / 'source-artifacts.json').write_text(json.dumps(manifest(self.source)))

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)

    def persist(self):
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(self.assignment), self.assignment['assignment_id']))
            db.execute('UPDATE iterations SET payload=?', (json.dumps(self.item),))

    def stored(self):
        with closing(sqlite3.connect(self.state)) as db:
            return json.loads(db.execute('SELECT payload FROM role_assignments WHERE assignment_id=?', (self.assignment['assignment_id'],)).fetchone()[0])

    def publish(self, plan_only=False):
        with closing(sqlite3.connect(self.state)) as db:
            return publication.publish(db, 'product', 'm1', '42', self.assignment['assignment_id'], self.config_path, plan_only)

    def read_prs(self):
        result = copy.deepcopy(self.prs)
        for pr in result:
            for side in ('head', 'base'):
                branch = pr[side]
                pr[side] = {'ref': branch, 'sha': self.refs[branch], 'repo': copy.deepcopy(self.repo)}
        return result

    def external(self, path, payload=None, method=None):
        method = method or ('GET' if payload is None else 'POST')
        suffix = path.removeprefix('/repos/example/product')
        self.calls.append((method, suffix, copy.deepcopy(payload)))
        if method == 'GET':
            if suffix == '':
                return copy.deepcopy(self.repo)
            if suffix == '/issues/10':
                return copy.deepcopy(self.issue)
            if suffix.startswith('/git/commits/'):
                value = copy.deepcopy(self.commits.get(suffix.split('/')[-1]))
                if value and 'message' in value:
                    value.update(self.overrides)
                return value
            if suffix.startswith('/git/trees/'):
                return {'sha': self.tree, 'tree': copy.deepcopy(self.entries), 'truncated': False}
            if suffix.startswith('/git/ref/heads/'):
                branch = suffix.removeprefix('/git/ref/heads/')
                return None if branch not in self.refs else {'ref': 'refs/heads/' + branch, 'object': {'type': 'commit', 'sha': self.refs[branch]}}
            if suffix.startswith('/pulls?'):
                return self.read_prs()
            if suffix == '/pulls/20':
                return self.read_prs()[0]
            if suffix == '/issues/20':
                return {'number': 20, 'body': self.prs[0]['body'], 'state': 'open', 'pull_request': {},
                        'repository_url': 'https://api.github.com/repos/example/product'}
            raise AssertionError('Unexpected read ' + suffix)
        assert isinstance(payload, dict)
        journal = self.stored()['publication']
        self.assertEqual(journal['status'], 'pending')
        self.assertNotIn('bearer', journal['approval'])
        if suffix == '/git/blobs':
            result = {'sha': git_object('blob', base64.b64decode(payload['content']))}
        elif suffix == '/git/trees':
            self.assertEqual(payload['base_tree'], self.tree)
            result = {'sha': self.plan['tree']}
        elif suffix == '/git/commits':
            date = datetime.datetime.strptime(payload['author']['date'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
            identity = 'Factory Controller <factory-controller@users.noreply.github.com>'
            raw = f"tree {payload['tree']}\nparent {payload['parents'][0]}\nauthor {identity} {int(date.timestamp())} +0000\ncommitter {identity} {int(date.timestamp())} +0000\n\n{payload['message']}".encode()
            self.assertTrue(payload['message'].endswith('\n'))
            sha = git_object('commit', raw)
            result = {'sha': sha, 'tree': {'sha': payload['tree']}, 'parents': [{'sha': payload['parents'][0]}], 'message': payload['message'][:-1]}
            self.commits[sha] = copy.deepcopy(result)
        elif suffix == '/git/refs':
            self.refs[payload['ref'].removeprefix('refs/heads/')] = payload['sha']
            result = {}
        elif suffix.startswith('/git/refs/heads/'):
            self.assertIs(payload['force'], False)
            self.refs[suffix.removeprefix('/git/refs/heads/')] = payload['sha']
            result = {}
        elif suffix == '/pulls':
            self.assertEqual(self.prs, [])
            self.prs.append(dict(payload, number=20, state='open', merged=False))
            result = self.read_prs()[0]
        else:
            raise AssertionError('Unexpected write ' + suffix)
        if self.drop == suffix:
            self.drop = None
            raise RepositoryError('github_unavailable', 'simulated lost response')
        return result

    def writes(self):
        return [call for call in self.calls if call[0] != 'GET']

    def refuse(self, code=None):
        before = len(self.writes())
        with self.assertRaises((RepositoryError, ValueError, TypeError, KeyError, AttributeError)) as raised:
            self.publish()
        if code:
            self.assertIsInstance(raised.exception, RepositoryError)
            self.assertEqual(getattr(raised.exception, 'code', None), code)
        self.assertNotIn(self.config['bearer'], str(raised.exception))
        self.assertEqual(len(self.writes()), before)

    def test_happy_publication_durable_exact_commit_pr_and_no_delivery_authority(self):
        result = self.publish()
        self.assertEqual(result['status'], 'published')
        self.assertFalse(result['close_allowed'])
        self.assertFalse(result['merge_allowed'])
        self.assertEqual(result['candidate'], self.plan['candidate'])
        self.assertEqual(self.refs['main'], 'a' * 40)
        self.assertEqual(self.issue['state'], 'open')
        self.assertIn('Refs #10', self.prs[0]['body'])
        self.assertIn('factory-v1-publication:' + self.assignment['assignment_id'], self.prs[0]['body'])
        self.assertNotIn('Closes', self.prs[0]['body'])
        self.assertNotIn(self.config['bearer'], json.dumps(self.stored()))
        self.assertFalse(self.stored()['result_disposition']['trusted_execution'])
        self.assertFalse(self.stored()['result_disposition']['advance_allowed'])
        writes = self.writes()
        self.assertEqual(self.publish(), result)
        self.assertEqual(self.writes(), writes)
        tree = next(c[2] for c in writes if c[1] == '/git/trees')
        self.assertEqual([entry['path'] for entry in tree['tree']], ['factory_v1/behavior.py'])
        commit = next(c[2] for c in writes if c[1] == '/git/commits')
        identity = {'name': 'Factory Controller', 'email': 'factory-controller@users.noreply.github.com', 'date': '2026-01-01T00:00:00Z'}
        self.assertEqual(commit, {'message': 'Factory candidate: Refs #10\n', 'tree': self.plan['tree'], 'parents': ['a' * 40], 'author': identity, 'committer': identity})

    def test_exact_git_wire_hashes_preserve_terminal_lf_and_opaque_entries(self):
        subtree = git_tree([{'path': 'behavior.py', 'mode': '100644', 'type': 'blob', 'sha': git_object('blob', b'new behavior\n')}])
        expected = git_tree([dict(e, sha=subtree) if e['path'] == 'factory_v1' else e for e in self.entries if '/' not in e['path']])
        self.assertEqual(self.plan['tree'], expected)
        self.assertEqual(publication.object_sha('blob', b'new behavior\n'), git_object('blob', b'new behavior\n'))
        message = 'Factory candidate: Refs #10'
        raw = f"tree {expected}\nparent {'a' * 40}\nauthor Factory Controller <factory-controller@users.noreply.github.com> 1767225600 +0000\ncommitter Factory Controller <factory-controller@users.noreply.github.com> 1767225600 +0000\n\n{message}\n".encode()
        self.assertEqual(self.plan['candidate'], git_object('commit', raw))
        self.assertNotEqual(self.plan['candidate'], git_object('commit', raw[:-1]))
        result = self.publish()
        for read_message in (message, message + '\n'):
            self.commits[result['candidate']]['message'] = read_message
            self.assertEqual(self.publish(), result)

    def test_commit_readback_matrix_retains_intent_and_prevents_ref_pr(self):
        message = self.plan['message']
        cases = [('message', value) for value in ('drift', ' ' + message, message + ' ', message + '\t',
                 message + '\r\n', message + '\n\n', message + '\n ', message + '\n\t', '\n' + message, None, [], {}, 123, True)]
        cases += [('sha', 'c' * 40), ('tree', {'sha': 'c' * 40}), ('parents', []),
                  ('parents', [{'sha': 'c' * 40}]), ('parents', [{'sha': 'a' * 40}] * 2)]
        journal = None
        for field, value in cases:
            self.calls.clear()
            self.overrides = {field: value}
            with self.assertRaises(RepositoryError) as raised:
                self.publish()
            self.assertEqual(raised.exception.code, 'github_mismatch')
            self.assertEqual(str(raised.exception), 'Exact commit readback failed.')
            self.assertEqual(self.prs, [])
            self.assertNotIn(self.config['branch'], self.refs)
            self.assertEqual(self.refs['main'], 'a' * 40)
            self.assertEqual(self.issue['state'], 'open')
            self.assertEqual(self.stored()['publication']['status'], 'pending')
            if journal is None:
                journal = self.stored()['publication']
            self.assertEqual(self.stored()['publication'], journal)
            self.assertEqual([c[1] for c in self.writes()], ['/git/blobs', '/git/trees', '/git/commits'])

    def test_lost_ref_or_pr_response_reconciles_without_second_effect(self):
        # Each subcase starts with the same tiny state, not a prior worker/controller chain.
        initial_state = self.state.read_bytes()
        for suffix in ('/git/refs', '/pulls'):
            self.state.write_bytes(initial_state)
            self.refs = {'main': 'a' * 40}
            self.prs = []
            self.calls.clear()
            self.drop = suffix
            with self.assertRaises(RepositoryError):
                self.publish()
            self.assertEqual(self.stored()['publication']['status'], 'pending')
            self.assertEqual(self.publish()['status'], 'published')
            self.assertEqual(sum(c[1] == suffix for c in self.writes()), 1)
            self.assertEqual(len(self.prs), 1)

    def test_second_candidate_uses_same_pr_nonforce_update(self):
        first = self.publish()
        (self.source / 'factory_v1/behavior.py').write_bytes(b'second candidate\n')
        self.refresh_source()
        self.config['manifest'] = manifest(self.source)
        self.config['expected_head'] = first['candidate']
        self.write_config()
        self.plan = self.publish(plan_only=True)
        self.config['candidate'] = self.plan['candidate']
        self.write_config()
        self.publish()
        patches = [c for c in self.writes() if c[0] == 'PATCH']
        self.assertEqual(patches, [('PATCH', '/git/refs/heads/factory/issue-10', {'sha': self.plan['candidate'], 'force': False})])
        self.assertEqual(len(self.prs), 1)
        self.assertEqual(self.stored()['publication']['pr_number'], 20)

    def test_concurrent_publishers_share_one_durable_intent_and_pr(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.publish) for _ in range(2)]
            results = [future.result(timeout=3) for future in futures]
        self.assertEqual(results[0], results[1])
        self.assertEqual(sum(c[1] == '/pulls' for c in self.writes()), 1)

    def test_private_config_and_all_worker_mount_exclusions(self):
        before = self.state.read_bytes()
        self.config_path.chmod(0o644)
        self.refuse('approval_required')
        self.assertEqual(self.calls, [])
        self.config_path.chmod(0o600)
        canonical = self.config_path
        for scope in (self.artifacts, self.artifacts / 'scratch', self.source,
                      Path(self.assignment['handoff']['workspace']), Path(self.assignment['handoff']['profile']['home'])):
            scope.mkdir(exist_ok=True)
            self.config_path = scope / 'publication.json'
            self.write_config()
            self.refuse('approval_required')
            self.assertEqual(self.calls, [])
            self.assertEqual(self.state.read_bytes(), before)
            self.config_path.unlink()
            self.config_path = canonical
            alias = scope / 'authorization-hardlink.json'
            os.link(canonical, alias)
            try:
                self.refuse('approval_required')
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state.read_bytes(), before)
            finally:
                alias.unlink()
        self.assertEqual(canonical.stat().st_nlink, 1)

    def test_authorization_and_protected_scope_aliases_refuse_before_reads(self):
        outside = self.root / 'outside'
        outside.mkdir()
        canonical = self.config_path
        file_link = self.root / 'authorization-alias.json'
        file_link.symlink_to(canonical)
        directory_link = self.root / 'root-alias'
        directory_link.symlink_to(self.root, target_is_directory=True)
        before = self.state.read_bytes()
        for alias in (file_link, directory_link / canonical.name, outside / '..' / canonical.name):
            self.config_path = alias
            self.refuse('unsafe_path')
            self.assertEqual(self.calls, [])
            self.assertEqual(self.state.read_bytes(), before)
        self.config_path = canonical
        for name in ('workspace', 'profile', 'artifacts'):
            original = copy.deepcopy(self.assignment)
            scope = Path(original['handoff']['workspace'] if name == 'workspace' else original['handoff']['profile']['home'] if name == 'profile' else original['runtime']['artifacts'])
            scope.mkdir(exist_ok=True)
            link = self.root / (name + '-alias')
            link.symlink_to(scope, target_is_directory=True)
            for alias in (outside / '..' / scope.name, link):
                if name == 'workspace':
                    self.assignment['handoff']['workspace'] = str(alias)
                elif name == 'profile':
                    self.assignment['handoff']['profile']['home'] = str(alias)
                else:
                    self.assignment['runtime']['artifacts'] = str(alias)
                self.assignment['handoff_digest'] = publication.assignments.digest(self.assignment['handoff'])
                self.config['binding'] = publication.binding(self.assignment)
                self.persist()
                self.write_config()
                self.refuse('unsafe_path')
                self.assertEqual(self.calls, [])
            self.assignment = original
            self.config['binding'] = publication.binding(original)
        self.persist()
        self.write_config()
        self.assertEqual(self.publish()['status'], 'published')

    def test_scope_repository_branch_issue_candidate_and_binding_pin_refusals(self):
        original = copy.deepcopy(self.config)
        before = self.state.read_bytes()
        for field, value in (('repository', 'other/product'), ('repository_id', 999), ('branch', 'main'),
                             ('branch', '../escape'), ('issue', 11), ('candidate', 'c' * 40), ('paths', ['other.py']),
                             ('base', 'other'), ('api_base', 'http://127.0.0.1:8766')):
            self.config = copy.deepcopy(original)
            self.config[field] = value
            self.write_config()
            self.refuse()
            self.assertEqual(self.state.read_bytes(), before)
        for field in ('handoff_digest', 'claim_id', 'baseline', 'spec_commit', 'stage', 'profile', 'revision'):
            self.config = copy.deepcopy(original)
            self.config['binding'][field] = 'wrong'
            self.write_config()
            self.refuse('scope_mismatch')
            self.assertEqual(self.state.read_bytes(), before)

    def test_worker_reports_review_roles_execution_and_profile_pins_are_not_authority(self):
        original_assignment = copy.deepcopy(self.assignment)
        original_config = copy.deepcopy(self.config)
        for changes, code in (({'status': 'failed'}, 'execution_required'), ({'container_removed': False}, 'execution_required'),
                              ({'run_id': 'wrong'}, 'scope_mismatch')):
            self.assignment = copy.deepcopy(original_assignment)
            self.assignment['runtime'].update(changes)
            self.assignment['submitted_result'] = {'status': 'done', 'trusted_execution': True}
            if code == 'execution_required':
                self.config['binding'] = publication.binding(self.assignment)
                self.write_config()
            else:
                self.config = copy.deepcopy(original_config)
                self.write_config()
            self.persist()
            self.refuse(code)
        self.assignment = copy.deepcopy(original_assignment)
        for stage, name, role in (('review-spec', 'factory-worker', 'review'), ('implementation', 'default', 'implementation')):
            self.assignment['handoff']['stage'] = stage
            self.assignment['handoff']['profile'].update(name=name, role=role)
            self.assignment['handoff_digest'] = publication.assignments.digest(self.assignment['handoff'])
            self.config['binding'] = publication.binding(self.assignment)
            self.persist()
            self.write_config()
            self.refuse('scope_mismatch')
        self.assignment = original_assignment
        self.config = original_config
        self.persist()
        self.config['execution_reference'] = ''
        self.write_config()
        self.refuse('approval_required')
        self.config['execution_reference'] = 'component authenticated host attestation'
        self.write_config()
        self.assertEqual(self.publish()['status'], 'published')
        self.assertFalse(self.stored()['result_disposition']['trusted_execution'])

    def test_pause_stop_stale_assignment_and_competing_claim_refuse(self):
        for status in ('paused', 'stopped'):
            self.item['status'] = status
            self.persist()
            self.refuse('iteration_paused')
        self.item['status'] = 'active'
        self.item['ticket_work']['input_digest'] = 'obsolete'
        self.persist()
        self.refuse('stale_assignment')
        self.item['ticket_work']['input_digest'] = 'inputs-1'
        self.persist()
        (self.artifacts / 'stop').touch()
        self.refuse('cancelled')
        (self.artifacts / 'stop').unlink()
        other = copy.deepcopy(self.assignment)
        other['assignment_id'] = 'c' * 64
        other['ticket_scope']['issue_number'] = 11
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('INSERT INTO role_assignments VALUES (?,?)', (other['assignment_id'], json.dumps(other)))
        self.refuse('claim_conflict')
        self.assertEqual(self.calls, [])

    def test_manifest_bytes_modes_and_symlink_refuse_before_remote_writes(self):
        path = self.source / 'factory_v1/behavior.py'
        path.chmod(0o755)
        self.refuse('manifest_mismatch')
        path.chmod(0o644)
        path.write_bytes(b'replacement\n')
        self.refuse('manifest_mismatch')
        path.unlink()
        path.symlink_to(self.baseline / 'factory_v1/behavior.py')
        self.refuse('unsafe_path')

    def test_policy_gates_are_held_but_product_behavior_and_tests_are_publishable(self):
        original = copy.deepcopy(self.config)
        for name in ('lefthook.yml', '.mise.toml', '.betterleaks.toml', '.githooks/pre-commit',
                     'scripts/security/check.sh', 'tools/gate.py', '.github/workflows/check.yml', 'AGENTS.md'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'reviewed policy change\n')
            self.refresh_source()
            self.config = copy.deepcopy(original)
            self.config['manifest'] = manifest(self.source)
            self.config['paths'].append(name)
            self.write_config()
            self.refuse('policy_held')
            if name == 'AGENTS.md':
                self.config['policy_paths'] = ['AGENTS.md']
                self.write_config()
                self.assertEqual(self.publish(plan_only=True)['status'], 'plan_only')
            path.unlink()
        for name in ('factory_v1/sandbox_policy.py', 'factory_v1/tests/test_security.py', 'test_behavior.py', 'scripts/build_product.py'):
            self.assertFalse(publication.policy_path(name))
        (self.source / 'test_behavior.py').write_bytes(b'new requirement tests\n')
        self.refresh_source()
        self.config = original
        self.config['manifest'] = manifest(self.source)
        self.config['paths'].append('test_behavior.py')
        self.write_config()
        self.plan = self.publish(plan_only=True)
        self.config['candidate'] = self.plan['candidate']
        self.write_config()
        self.assertEqual(self.publish()['status'], 'published')

    def test_secret_suid_source_and_json_bounds_are_refused(self):
        path = self.source / 'factory_v1/behavior.py'
        for data, mode in (('ghp_' + 'a' * 30, 0o644), ('ordinary code', 0o4644)):
            path.write_text(data)
            path.chmod(mode)
            self.refresh_source()
            self.config['manifest'] = manifest(self.source)
            self.write_config()
            self.refuse('source_blocked')
        path.chmod(0o644)
        with path.open('wb') as stream:
            stream.truncate(100 * 1024 * 1024 + 1)
        self.refuse('source_blocked')
        path.write_bytes(b'new behavior\n')
        self.config['manifest'] = manifest(self.source)
        self.write_config()
        (self.artifacts / 'source-artifacts.json').write_bytes(b' ' * (8 * 1024 * 1024 + 1))
        self.refuse('invalid_publication')

    def test_remote_repository_body_issue_base_branch_and_pr_refusals(self):
        for target, key, value, code in ((self.repo, 'id', 999, 'github_mismatch'),
                                        (self.issue, 'state', 'closed', 'scope_mismatch'),
                                        (self.issue, 'body', 'stale', 'scope_mismatch'),
                                        (self.refs, 'main', 'c' * 40, 'scope_mismatch')):
            old = target[key]
            target[key] = value
            self.refuse(code)
            target[key] = old
        self.refs[self.config['branch']] = 'c' * 40
        self.refuse('publication_conflict')
        del self.refs[self.config['branch']]
        self.publish()
        self.calls.clear()
        for field, value in (('body', 'Closes #10'), ('base', 'other'), ('state', 'closed')):
            old = self.prs[0][field]
            self.prs[0][field] = value
            if field == 'base':
                self.refs['other'] = 'a' * 40
            self.refuse('github_mismatch')
            self.prs[0][field] = old
        original = self.read_prs()
        for side, value in (('head', None), ('base', []), ('head', {'ref': self.config['branch'], 'repo': None}),
                            ('base', dict(original[0]['base'], sha='c' * 40))):
            observed = copy.deepcopy(original)
            observed[0][side] = value
            with mock.patch.object(self, 'read_prs', return_value=observed):
                self.refuse()

    def test_pending_approval_journal_and_published_identity_cannot_be_rewritten(self):
        self.drop = '/git/refs'
        with self.assertRaises(RepositoryError):
            self.publish()
        self.calls.clear()
        self.config['reference'] = 'replacement approval'
        self.write_config()
        self.refuse('publication_conflict')
        self.config['reference'] = 'explicit host operator approval'
        self.write_config()
        result = self.publish()
        self.calls.clear()
        saved = self.stored()
        for field, value in (('candidate', 'c' * 40), ('status', 'unknown'), ('pr_number', 21)):
            changed = copy.deepcopy(saved)
            changed['publication'][field] = value
            with closing(sqlite3.connect(self.state)) as db, db:
                db.execute('UPDATE role_assignments SET payload=?', (json.dumps(changed),))
            before = self.state.read_bytes()
            self.refuse('publication_conflict')
            self.assertEqual(self.state.read_bytes(), before)
        self.assignment = saved
        self.persist()
        for head in (None, self.config['expected_head']):
            if head is None:
                del self.refs[self.config['branch']]
            else:
                self.refs[self.config['branch']] = head
            self.refuse('publication_conflict')
        self.refs[self.config['branch']] = result['candidate']
        for field, value in (('tree', None), ('parents', [None]), ('message', 'drift')):
            old = self.commits[result['candidate']][field]
            self.commits[result['candidate']][field] = value
            self.refuse()
            self.commits[result['candidate']][field] = old

    def test_add_delete_scope_and_unsupported_entry_replacement(self):
        (self.source / 'link').write_bytes(b'not a link\n')
        self.refresh_source()
        self.config['manifest'] = manifest(self.source)
        self.config['paths'].append('link')
        self.write_config()
        self.refuse('scope_mismatch')
        (self.source / 'link').unlink()
        (self.source / 'factory_v1/behavior.py').unlink()
        (self.source / 'new_behavior.py').write_bytes(b'new file\n')
        self.refresh_source()
        self.config['manifest'] = manifest(self.source)
        self.config['paths'] = ['factory_v1/behavior.py', 'new_behavior.py']
        self.write_config()
        self.plan = self.publish(plan_only=True)
        self.config['candidate'] = self.plan['candidate']
        self.write_config()
        self.publish()
        self.assertIsNone(next(e for e in self.plan['changes'] if e['path'] == 'factory_v1/behavior.py')['sha'])
        self.assertEqual(next(e for e in self.plan['changes'] if e['path'] == 'new_behavior.py')['mode'], '100644')

    def test_adapter_denies_arbitrary_operation_and_nonapproved_payload(self):
        github = mock.Mock(spec=GitHub)
        api = publication.PublicationGitHub(github, self.config)
        for method, path, body in (('POST', '/repos/other/product/pulls', {}),
                                   ('PATCH', '/repos/example/product/git/refs/heads/main', {'force': True}),
                                   ('PUT', '/repos/example/product/pulls/20/merge', {}), ('GET', 'https://evil.invalid/', None)):
            with self.assertRaises(RepositoryError):
                api.request(path, body, method)
        for operation in (lambda: api.write('/actions/secrets', {}), lambda: api.read('/actions/secrets')):
            with self.assertRaises(RepositoryError):
                operation()
        api.approve(self.plan, {'factory_v1/behavior.py': b'new behavior\n'}, {'title': 'approved'})
        with self.assertRaises(RepositoryError):
            api.write('/git/refs/heads/' + self.config['branch'], {'sha': self.plan['candidate'], 'force': True}, 'PATCH')
        github.request.assert_not_called()

    def test_malformed_configuration_persistent_json_and_invalid_database_no_effect(self):
        before = self.state.read_bytes()
        original = copy.deepcopy(self.config)
        for field, value in (('api_base', 123), ('api_base', {}), ('commit_date', None), ('paths', [None]),
                             ('paths', [{}]), ('paths', ['x'] * 201), ('policy_paths', [{}]), ('manifest', []), ('binding', [])):
            self.config = copy.deepcopy(original)
            self.config[field] = value
            self.write_config()
            self.refuse()
            self.assertEqual(self.state.read_bytes(), before)
        self.config = original
        for data in (b'{', b'\xff', b'{"operator_id":"42","operator_id":"42"}', b' ' * (8 * 1024 * 1024 + 1)):
            self.config_path.write_bytes(data)
            self.refuse()
            self.assertEqual(self.state.read_bytes(), before)
        self.write_config()
        original_payload = json.dumps(self.assignment)
        for payload in ('{', '[]', '{"assignment_id":null}', '{"assignment_id":"x","assignment_id":"y"}'):
            with closing(sqlite3.connect(self.state)) as db, db:
                db.execute('UPDATE role_assignments SET payload=?', (payload,))
            snapshot = self.state.read_bytes()
            self.refuse()
            self.assertEqual(self.state.read_bytes(), snapshot)
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('UPDATE role_assignments SET payload=?', (original_payload,))
            db.execute('UPDATE iterations SET payload=?', ('[]',))
        snapshot = self.state.read_bytes()
        self.refuse()
        self.assertEqual(self.state.read_bytes(), snapshot)
        self.assertEqual(self.writes(), [])
        # Read/write URI admission cannot create absent targets. Ephemeral targets are wrapper-only.
        missing = self.root / 'missing.sqlite'
        with self.assertRaises(sqlite3.Error):
            sqlite3.connect(missing.as_uri() + '?mode=rw', uri=True)
        self.assertFalse(missing.exists())


    def test_nonpersistent_missing_corrupt_and_empty_state_refuse_without_creation(self):
        import sys
        from factory_v1 import __main__ as cli
        from factory_v1.errors import IntakeError
        before = set(self.root.iterdir())
        for state in ('', ':memory:'):
            with mock.patch.object(sys, 'argv', ['factory', '--state', state, '--operator-id', '42',
                'publish-candidate', '--project', 'product', '--iteration', 'm1', '--assignment', self.assignment['assignment_id'],
                '--publication-config', str(self.config_path)]), self.assertRaises(IntakeError) as raised:
                cli.main()
            self.assertEqual(raised.exception.code, 'invalid_state')
            self.assertEqual(set(self.root.iterdir()), before)
        missing = self.root / 'missing.sqlite'
        with self.assertRaises(sqlite3.Error):
            sqlite3.connect(missing.as_uri() + '?mode=rw', uri=True)
        self.assertFalse(missing.exists())
        for name, raw in (('empty.sqlite', b''), ('corrupt.sqlite', b'not a SQLite database')):
            path = self.root / name
            path.write_bytes(raw)
            Path(str(path) + '.onboarding.lock').touch(mode=0o600)
            before = set(self.root.iterdir())
            with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True)) as db, self.assertRaises(sqlite3.Error):
                publication.publish(db, 'product', 'm1', '42', self.assignment['assignment_id'], self.config_path)
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(set(self.root.iterdir()), before)
        self.assertEqual(self.calls, [])

    def test_exact_commit_guard_sensitivity(self):
        value = {'sha': self.plan['candidate'], 'tree': {'sha': self.plan['tree']},
                 'parents': [{'sha': self.config['expected_head']}], 'message': 'drift'}
        api = mock.Mock()
        api.read.return_value = value
        with self.assertRaises(RepositoryError) as raised:
            publication.verify_commit(api, self.plan, self.config)
        self.assertEqual(str(raised.exception), 'Exact commit readback failed.')
        source = inspect.getsource(publication.verify_commit)
        source = source.replace("value.get('message') in (plan['message'], plan['message'] + '\\n')", 'True')
        namespace = dict(vars(publication))
        exec(compile(source, '<commit-message-guard-probe>', 'exec'), namespace)
        namespace['verify_commit'](api, self.plan, self.config)
