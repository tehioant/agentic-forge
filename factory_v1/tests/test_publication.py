"""Public CLI + real loopback HTTP + durable restart; NOT live GitHub/worker proof."""
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
                    value = case.commits.get(path.split('/')[-1])
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
                    value = {'sha': actual, 'tree': {'sha': body['tree']}, 'parents': [{'sha': body['parents'][0]}], 'message': body['message']}
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

    def test_happy_restart_published_never_done_and_no_token_leak(self):
        result = self.ok(self.cli())
        self.assertEqual(result['status'], 'published')
        self.assertFalse(result['close_allowed'])
        self.assertFalse(result['merge_allowed'])
        self.assertEqual(self.refs['main'], 'a' * 40)
        self.assertEqual(self.issue['state'], 'open')
        self.assertIn('Refs #10', self.prs[0]['body'])
        self.assertIn('factory-v1-publication:' + self.assignment['assignment_id'], self.prs[0]['body'])
        self.assertNotIn('Closes', self.prs[0]['body'])
        self.assertNotIn(self.config['bearer'], json.dumps(self.stored()))
        self.assertFalse(self.stored()['result_disposition']['trusted_execution'])
        before = self.writes()
        self.assertEqual(result, self.ok(self.cli()))
        self.assertEqual(before, self.writes())
        payload = next(c[2] for c in before if c[1].endswith('/git/trees'))
        self.assertEqual([c['path'] for c in payload['tree']], ['factory_v1/behavior.py'])

    def test_lost_ref_response_reconciles_same_target_after_restart(self):
        self.drop = '/git/refs'
        self.assertEqual(self.cli().returncode, 2)
        self.assertEqual(self.stored()['publication']['status'], 'pending')
        self.ok(self.cli())
        self.assertEqual(sum(c[1].endswith('/git/refs') for c in self.writes()), 1)

    def test_lost_pr_response_reconciles_same_target_after_restart(self):
        self.drop = '/pulls'
        self.assertEqual(self.cli().returncode, 2)
        self.ok(self.cli())
        self.assertEqual(sum(c[1].endswith('/pulls') for c in self.writes()), 1)
        self.assertEqual(len(self.prs), 1)

    def test_update_same_branch_pr_is_nonforce(self):
        first = self.ok(self.cli())
        (self.source / 'factory_v1/behavior.py').write_text('second candidate\n')
        self.refresh_source()
        self.config['manifest'] = manifest(self.source)
        self.config['expected_head'] = first['candidate']
        self.write_config()
        self.plan = self.ok(self.cli('--plan-only'))
        self.config['candidate'] = self.plan['candidate']
        self.write_config()
        self.ok(self.cli())
        patches = [c for c in self.writes() if c[0] == 'PATCH']
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0][2], {'sha': self.plan['candidate'], 'force': False})
        self.assertEqual(len(self.prs), 1)
        self.assertEqual(self.stored()['publication']['pr_number'], 20)

    def test_concurrent_publications_serialize_one_intent(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.cli(), range(2)))
        for result in results:
            self.ok(result)
        self.assertEqual(sum(c[1].endswith('/pulls') for c in self.writes()), 1)

    def test_private_config_missing_or_worker_mounted_refused(self):
        self.config_path.chmod(0o644)
        self.deny('approval_required')
        self.config_path = self.source / 'publication.json'
        self.write_config()
        self.deny('approval_required')

    def test_artifact_authorization_dotdot_alias_refused_before_http_or_state(self):
        outside = self.root / 'outside'
        outside.mkdir()
        self.config_path = self.artifacts / 'publication.json'
        self.write_config()
        before = self.fixture.state.read_bytes()
        self.deny('approval_required')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.fixture.state.read_bytes(), before)
        self.config_path = outside / '..' / self.artifacts.name / 'publication.json'
        self.deny('unsafe_path')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.fixture.state.read_bytes(), before)

    def test_authorization_in_each_excluded_scope_refused_before_http_or_state(self):
        outside = self.root / 'outside'
        outside.mkdir()
        scopes = (Path(self.assignment['handoff']['workspace']),
                  Path(self.assignment['handoff']['profile']['home']), self.artifacts)
        for scope in scopes:
            scope.mkdir(exist_ok=True)
            self.config_path = scope / 'publication.json'
            self.write_config()
            for path, code in ((self.config_path, 'approval_required'),
                               (outside / '..' / scope.name / 'publication.json', 'unsafe_path')):
                with self.subTest(scope=scope.name, path=str(path)):
                    self.config_path = path
                    before = self.fixture.state.read_bytes()
                    self.deny(code)
                    self.assertEqual(self.calls, [])
                    self.assertEqual(self.fixture.state.read_bytes(), before)

    def test_authorization_hardlinks_in_each_excluded_scope_refused_before_http_or_state(self):
        scopes = (self.artifacts, self.artifacts / 'scratch',
                  Path(self.assignment['handoff']['workspace']),
                  Path(self.assignment['handoff']['profile']['home']))
        for scope in scopes:
            scope.mkdir(exist_ok=True)
            alias = scope / 'authorization-hardlink.json'
            os.link(self.config_path, alias)
            try:
                self.assertTrue(alias.samefile(self.config_path))
                self.assertEqual(self.config_path.stat().st_nlink, 2)
                for extra in (('--plan-only',), ()):
                    with self.subTest(scope=str(scope), extra=extra):
                        self.calls.clear()
                        before = self.fixture.state.read_bytes()
                        result = self.cli(*extra)
                        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                        self.assertEqual(json.loads(result.stderr)['error'], 'approval_required')
                        self.assertEqual(result.stdout, '')
                        self.assertNotIn(self.config['bearer'], result.stdout + result.stderr)
                        self.assertEqual(self.calls, [])
                        self.assertEqual(self.fixture.state.read_bytes(), before)
            finally:
                alias.unlink()
        self.assertEqual(self.config_path.stat().st_nlink, 1)
        self.assertEqual(self.ok(self.cli('--plan-only'))['status'], 'plan_only')
        self.assertEqual(self.ok(self.cli())['status'], 'published')

    def test_protected_scope_aliases_refused_before_http_or_state(self):
        outside = self.root / 'outside'
        outside.mkdir()
        original = copy.deepcopy(self.assignment)
        for name in ('workspace', 'profile', 'artifacts'):
            scope = Path(original['handoff']['workspace'] if name == 'workspace' else
                         original['handoff']['profile']['home'] if name == 'profile' else
                         original['runtime']['artifacts'])
            scope.mkdir(exist_ok=True)
            link = self.root / (name + '-alias')
            link.symlink_to(scope, target_is_directory=True)
            for alias in (outside / '..' / scope.name, link):
                with self.subTest(scope=name, alias=str(alias)):
                    self.assignment = copy.deepcopy(original)
                    if name == 'workspace':
                        self.assignment['handoff']['workspace'] = str(alias)
                    elif name == 'profile':
                        self.assignment['handoff']['profile']['home'] = str(alias)
                    else:
                        self.assignment['runtime']['artifacts'] = str(alias)
                    self.assignment['handoff_digest'] = publication.assignments.digest(self.assignment['handoff'])
                    self.config['binding'] = publication.binding(self.assignment)
                    self.persist_assignment()
                    self.config_path = scope / 'publication.json'
                    self.write_config()
                    before = self.fixture.state.read_bytes()
                    self.deny('unsafe_path')
                    self.assertEqual(self.calls, [])
                    self.assertEqual(self.fixture.state.read_bytes(), before)

    def test_private_outside_authorization_valid_but_aliases_refused(self):
        outside = self.root / 'assigned-workspace-outside'
        outside.mkdir()
        self.config_path = outside / 'publication.json'
        self.write_config()
        canonical = self.config_path
        file_link = self.root / 'authorization-alias.json'
        file_link.symlink_to(canonical)
        directory_link = self.root / 'outside-alias'
        directory_link.symlink_to(outside, target_is_directory=True)
        for alias in (file_link, directory_link / canonical.name,
                      outside / '..' / outside.name / canonical.name):
            with self.subTest(alias=str(alias)):
                self.config_path = alias
                before = self.fixture.state.read_bytes()
                self.deny('unsafe_path')
                self.assertEqual(self.calls, [])
                self.assertEqual(self.fixture.state.read_bytes(), before)
        self.config_path = canonical
        self.assertEqual(self.ok(self.cli())['status'], 'published')
        self.assertEqual(self.stored()['publication']['status'], 'published')

    def test_scope_role_repository_branch_issue_pin_denials_before_writes(self):
        original = copy.deepcopy(self.config)
        for field, value in [('repository', 'other/product'), ('repository_id', 999), ('branch', 'main'),
                             ('branch', '../escape'), ('issue', 11), ('candidate', 'c' * 40), ('paths', ['other.py']),
                             ('base', 'other'), ('api_base', 'http://127.0.0.1:1')]:
            with self.subTest(field=field, value=value):
                self.config = copy.deepcopy(original)
                self.config[field] = value
                self.write_config()
                self.deny()
        self.config = original
        for field in ('handoff_digest', 'claim_id', 'baseline', 'spec_commit', 'stage', 'profile', 'revision'):
            with self.subTest(binding=field):
                self.config = copy.deepcopy(original)
                self.config['binding'][field] = 'wrong'
                self.write_config()
                self.deny('scope_mismatch')

    def test_worker_report_is_not_trusted_execution(self):
        self.assignment['runtime']['status'] = 'failed'
        self.assignment['submitted_result'] = {'status': 'done', 'trusted_execution': True}
        self.config['binding'] = publication.binding(self.assignment)
        self.write_config()
        self.persist_assignment()
        self.deny('execution_required')

    def test_review_role_is_readonly_even_with_private_approval(self):
        self.assignment['handoff']['stage'] = 'review-spec'
        self.assignment['handoff']['profile']['role'] = 'review'
        self.assignment['handoff_digest'] = publication.assignments.digest(self.assignment['handoff'])
        self.config['binding'] = publication.binding(self.assignment)
        self.persist_assignment()
        self.write_config()
        self.deny('scope_mismatch')

    def test_paused_stopped_stale_and_competing_scope_denied(self):
        for status in ('paused', 'stopped'):
            with closing(sqlite3.connect(self.fixture.state)) as database, database:
                item = json.loads(database.execute('SELECT payload FROM iterations').fetchone()[0])
                item['status'] = status
                database.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
            self.deny('iteration_paused')
        with closing(sqlite3.connect(self.fixture.state)) as database, database:
            item['status'] = 'active'
            item['ticket_work']['input_digest'] = 'obsolete'
            database.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
        self.deny('stale_assignment')

    def test_stop_marker_refused(self):
        (self.artifacts / 'stop').touch()
        self.deny('cancelled')

    def test_manifest_bytes_modes_and_links_denied(self):
        path = self.source / 'factory_v1/behavior.py'
        path.chmod(0o755)
        self.deny('manifest_mismatch')
        path.chmod(0o644)
        path.write_text('replacement\n')
        self.deny('manifest_mismatch')
        path.unlink()
        path.symlink_to(self.baseline / 'factory_v1/behavior.py')
        self.deny('unsafe_path')

    def test_policy_held_but_implementation_tests_are_reviewable(self):
        (self.source / '.github').mkdir()
        (self.source / '.github/workflows').mkdir()
        (self.source / '.github/workflows/check.yml').write_text('checks: disabled\n')
        self.refresh_source()
        self.config['manifest'] = manifest(self.source)
        self.config['paths'].append('.github/workflows/check.yml')
        self.write_config()
        self.deny('policy_held')
        self.assertFalse(publication.policy_path('factory_v1/sandbox.py'))
        self.assertFalse(publication.policy_path('factory_v1/tests/test_security.py'))

    def test_explicit_operator_policy_path_can_be_planned(self):
        (self.source / 'AGENTS.md').write_text('Reviewed explicit operator standards change\n')
        self.refresh_source()
        self.config['manifest'] = manifest(self.source)
        self.config['paths'].append('AGENTS.md')
        self.config['policy_paths'] = ['AGENTS.md']
        self.write_config()
        self.plan = self.ok(self.cli('--plan-only'))
        self.config['candidate'] = self.plan['candidate']
        self.write_config()
        self.ok(self.cli())

    def test_secret_and_suid_detection(self):
        path = self.source / 'factory_v1/behavior.py'
        for data, mode in [('ghp_' + 'a' * 30, 0o644), ('ordinary code', 0o4644)]:
            path.write_text(data)
            path.chmod(mode)
            self.refresh_source()
            self.config['manifest'] = manifest(self.source)
            self.write_config()
            self.deny('source_blocked')

    def test_remote_repository_issue_base_and_branch_replacement_refused(self):
        self.repo['id'] = 999
        self.deny('github_mismatch')
        self.repo['id'] = 123
        self.issue['state'] = 'closed'
        self.deny('scope_mismatch')
        self.issue['state'] = 'open'
        old_body = self.issue['body']
        self.issue['body'] = 'stale'
        self.deny('scope_mismatch')
        self.issue['body'] = old_body
        self.refs['main'] = 'c' * 40
        self.deny('scope_mismatch')
        self.refs['main'] = 'a' * 40
        self.refs[self.config['branch']] = 'c' * 40
        self.deny('publication_conflict')

    def test_pr_mismatch_refused_before_new_mutations(self):
        self.ok(self.cli())
        self.calls.clear()
        for field, value in [('body', 'Closes #10'), ('base', 'other'), ('state', 'closed')]:
            old = self.prs[0][field]
            self.prs[0][field] = value
            if field == 'base':
                self.refs['other'] = 'a' * 40
            self.deny('github_mismatch')
            self.prs[0][field] = old

    def test_pending_approval_cannot_be_replaced(self):
        self.drop = '/git/refs'
        self.assertEqual(self.cli().returncode, 2)
        self.calls.clear()
        self.config['reference'] = 'different approval'
        self.write_config()
        self.deny('publication_conflict')

    def test_git_independently_verifies_tree_with_untouched_unsupported_entries(self):
        with tempfile.TemporaryDirectory(prefix='publication-git-') as directory:
            subprocess.run(['/usr/bin/git', 'init', '--bare', '--template=', directory], capture_output=True, check=True)
            def tree(entries):
                data = ''.join(f"{e['mode']} {e['type']} {e['sha']}\t{e['path']}\n" for e in entries).encode()
                return subprocess.run(['/usr/bin/git', '--git-dir=' + directory, 'mktree', '--missing'],
                                      input=data, capture_output=True, check=True).stdout.decode().strip()
            subtree = tree([{'path': 'behavior.py', 'mode': '100644', 'type': 'blob',
                             'sha': publication.object_sha('blob', b'new behavior\n')}])
            root_entries = [dict(e, sha=subtree) if e['path'] == 'factory_v1' else e
                            for e in self.entries if '/' not in e['path']]
            self.assertEqual(self.plan['tree'], tree(root_entries))
        self.ok(self.cli())

    def test_deletion_and_addition_are_exact_scope(self):
        (self.source / 'factory_v1/behavior.py').unlink()
        (self.source / 'new_behavior.py').write_text('new file\n')
        self.refresh_source()
        self.config['manifest'] = manifest(self.source)
        self.config['paths'] = ['factory_v1/behavior.py', 'new_behavior.py']
        self.write_config()
        self.plan = self.ok(self.cli('--plan-only'))
        self.config['candidate'] = self.plan['candidate']
        self.write_config()
        self.ok(self.cli())
        changes = self.plan['changes']
        self.assertIsNone(next(c for c in changes if c['path'] == 'factory_v1/behavior.py')['sha'])
        self.assertEqual(next(c for c in changes if c['path'] == 'new_behavior.py')['mode'], '100644')

    def test_addition_cannot_replace_opaque_tree_or_link(self):
        (self.source / 'link').write_text('not a link\n')
        self.refresh_source()
        self.config['manifest'] = manifest(self.source)
        self.config['paths'].append('link')
        self.write_config()
        self.deny('scope_mismatch')

    def test_conflicting_persisted_ticket_claim_is_not_stolen(self):
        other = copy.deepcopy(self.assignment)
        other['assignment_id'] = 'c' * 64
        other['ticket_scope']['issue_number'] = 11
        with closing(sqlite3.connect(self.fixture.state)) as database, database:
            database.execute('INSERT INTO role_assignments VALUES (?,?)', (other['assignment_id'], json.dumps(other)))
        self.deny('claim_conflict')

    def test_profile_denial_and_execution_pin_denial(self):
        self.config['binding']['execution']['run_id'] = 'different-run'
        self.write_config()
        self.deny('scope_mismatch')
        self.assignment['handoff']['profile']['name'] = 'default'
        self.assignment['handoff_digest'] = publication.assignments.digest(self.assignment['handoff'])
        self.config['binding'] = publication.binding(self.assignment)
        self.persist_assignment()
        self.write_config()
        self.deny('scope_mismatch')

    def test_adapter_denies_arbitrary_operations_and_payload_before_network(self):
        api = publication.PublicationGitHub(GitHub(self.base, 'unused', 1), self.config)
        for method, path, body in [('POST', '/repos/other/product/pulls', {}),
                                   ('PATCH', '/repos/example/product/git/refs/heads/main', {'force': True}),
                                   ('PUT', '/repos/example/product/pulls/20/merge', {}),
                                   ('GET', 'https://evil.invalid/', None)]:
            with self.assertRaises(RepositoryError):
                api.request(path, body, method)
        with self.assertRaises(RepositoryError):
            api.write('/actions/secrets', {})
        with self.assertRaises(RepositoryError):
            api.read('/actions/secrets')
        self.assertEqual(self.calls, [])

    def test_gate_configurations_and_tooling_require_operator_path(self):
        original = copy.deepcopy(self.config)
        for name in ('lefthook.yml', '.mise.toml', '.betterleaks.toml',
                     '.githooks/pre-commit', 'scripts/security/check.sh', 'tools/gate.py'):
            with self.subTest(path=name):
                path = self.source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('gate configuration fixture\n')
                self.refresh_source()
                self.config = copy.deepcopy(original)
                self.config['manifest'] = manifest(self.source)
                self.config['paths'].append(name)
                self.write_config()
                self.deny('policy_held')
                path.unlink()
        for name in ('factory_v1/sandbox_policy.py', 'factory_v1/tests/test_security.py',
                     'test_behavior.py', 'scripts/build_product.py'):
            self.assertFalse(publication.policy_path(name), name)

    def test_legitimate_behavior_and_test_delta_can_publish(self):
        (self.source / 'test_behavior.py').write_text('new requirement tests\n')
        self.refresh_source()
        self.config['manifest'] = manifest(self.source)
        self.config['paths'].append('test_behavior.py')
        self.write_config()
        self.plan = self.ok(self.cli('--plan-only'))
        self.config['candidate'] = self.plan['candidate']
        self.write_config()
        self.ok(self.cli())

    def test_malformed_config_fields_and_json_refuse_without_state_changes(self):
        before = self.fixture.state.read_bytes()
        original = copy.deepcopy(self.config)
        for field, value in (('api_base', 123), ('api_base', {}), ('commit_date', None),
                             ('paths', [None]), ('paths', [{}]), ('paths', ['x'] * 201),
                             ('policy_paths', [{}]), ('manifest', []), ('binding', [])):
            with self.subTest(field=field, value=value):
                self.config = copy.deepcopy(original)
                self.config[field] = value
                self.write_config()
                self.deny()
                self.assertEqual(self.fixture.state.read_bytes(), before)
        self.config = original
        for data in (b'{', b'\xff', b'{"operator_id":"42","operator_id":"42"}',
                     b' ' * (8 * 1024 * 1024 + 1)):
            self.config_path.write_bytes(data)
            self.deny()
            self.assertEqual(self.fixture.state.read_bytes(), before)

    def test_invalid_state_never_creates_sqlite_or_lock(self):
        import sys
        missing = self.root / 'missing.sqlite'
        corrupt = self.root / 'corrupt.sqlite'
        corrupt.write_bytes(b'not a SQLite database')
        empty = self.root / 'empty.sqlite'
        empty.touch()
        before = set(self.root.iterdir())
        for state in ('', ':memory:', str(missing), str(corrupt), str(empty)):
            with self.subTest(state=state):
                result = subprocess.run([sys.executable, '-m', 'factory_v1', '--state', state,
                    '--operator-id', '42', 'publish-candidate', '--project', 'product',
                    '--iteration', 'm1', '--assignment', self.assignment['assignment_id'],
                    '--publication-config', str(self.config_path)], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn('error', json.loads(result.stderr))
                self.assertEqual(result.stdout, '')
                self.assertEqual(set(self.root.iterdir()), before)
        self.assertEqual(corrupt.read_bytes(), b'not a SQLite database')
        self.assertEqual(empty.stat().st_size, 0)
        self.assertEqual(self.calls, [])

    def test_malformed_persistent_json_and_shapes_refuse_without_mutations(self):
        original = json.dumps(self.assignment)
        for payload in ('{', '[]', '{"assignment_id":null}',
                        '{"assignment_id":"x","assignment_id":"y"}'):
            with self.subTest(payload=payload):
                with closing(sqlite3.connect(self.fixture.state)) as database, database:
                    database.execute('UPDATE role_assignments SET payload=?', (payload,))
                before = self.fixture.state.read_bytes()
                self.deny()
                self.assertEqual(self.fixture.state.read_bytes(), before)
        with closing(sqlite3.connect(self.fixture.state)) as database, database:
            database.execute('UPDATE role_assignments SET payload=?', (original,))
            database.execute('UPDATE iterations SET payload=?', ('[]',))
        before = self.fixture.state.read_bytes()
        self.deny()
        self.assertEqual(self.fixture.state.read_bytes(), before)

    def test_source_and_json_bounds_refuse_before_remote_writes(self):
        path = self.source / 'factory_v1/behavior.py'
        with path.open('wb') as stream:
            stream.truncate(100 * 1024 * 1024 + 1)
        self.deny('source_blocked')
        path.write_text('new behavior\n')
        metadata = self.artifacts / 'source-artifacts.json'
        metadata.write_bytes(b' ' * (8 * 1024 * 1024 + 1))
        self.deny('invalid_publication')

    def test_complete_runtime_flags_do_not_replace_private_execution_attestation(self):
        self.assignment['submitted_result'] = {'status': 'done', 'execution_reference': 'worker assertion',
                                               'trusted_execution': True}
        self.persist_assignment()
        self.config['execution_reference'] = ''
        self.write_config()
        self.deny('approval_required')
        self.config['execution_reference'] = 'fixture independently authenticated host attestation'
        self.write_config()
        self.ok(self.cli())
        self.assertFalse(self.stored()['result_disposition']['trusted_execution'])
        self.assertFalse(self.stored()['result_disposition']['advance_allowed'])

    def test_journal_candidate_and_pr_identity_drift_are_held_without_rewrite(self):
        self.ok(self.cli())
        self.calls.clear()
        saved = self.stored()
        for field, value in (('candidate', 'c' * 40), ('status', 'unknown'), ('pr_number', 21)):
            with self.subTest(field=field):
                changed = copy.deepcopy(saved)
                changed['publication'][field] = value
                with closing(sqlite3.connect(self.fixture.state)) as database, database:
                    database.execute('UPDATE role_assignments SET payload=?', (json.dumps(changed),))
                before = self.fixture.state.read_bytes()
                self.deny('publication_conflict')
                self.assertEqual(self.fixture.state.read_bytes(), before)

    def test_published_branch_and_commit_drift_are_not_replayed(self):
        result = self.ok(self.cli())
        self.calls.clear()
        before = self.fixture.state.read_bytes()
        for head in (None, self.config['expected_head']):
            if head is None:
                del self.refs[self.config['branch']]
            else:
                self.refs[self.config['branch']] = head
            self.deny('publication_conflict')
            self.assertEqual(self.fixture.state.read_bytes(), before)
        self.refs[self.config['branch']] = result['candidate']
        commit = self.commits[result['candidate']]
        for field, value in (('tree', None), ('parents', [None]), ('message', 'drift')):
            old = commit[field]
            commit[field] = value
            self.deny()
            self.assertEqual(self.fixture.state.read_bytes(), before)
            commit[field] = old

    def test_pr_nested_malformed_metadata_and_base_sha_drift_are_refused(self):
        self.ok(self.cli())
        self.calls.clear()
        original = self.read_prs()
        before = self.fixture.state.read_bytes()
        for side, value in (('head', None), ('base', []),
                            ('head', {'ref': self.config['branch'], 'repo': None}),
                            ('base', dict(original[0]['base'], sha='c' * 40))):
            observed = copy.deepcopy(original)
            observed[0][side] = value
            with mock.patch.object(self, 'read_prs', return_value=observed):
                self.deny()
                self.assertEqual(self.fixture.state.read_bytes(), before)
