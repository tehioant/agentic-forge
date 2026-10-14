"""Direct engineering seams; legacy sibling fixture helpers are not discovered."""
import base64
import copy
import json
import os
import sqlite3
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from factory_v1 import assignments, merge, publication
from factory_v1.sandbox_source import manifest
from factory_v1.tests import test_review


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.review = test_review.ReviewTests()
        self.review.setUp()
        self.addCleanup(self.review.doCleanups)
        self.assignment = self.review.assignment
        self.fixture = self.assignment.fixture
        self.root = self.fixture.root
        self.axes = self.review.pair()
        self.assignment.ok(self.review.command('review-candidate', self.review.pair_request(self.axes), self.review.authorization(self.axes)))
        self.candidate = self.assignment.ok(self.assignment.inspect(self.review.candidate))
        self.builder = self.review.implemented
        self.artifacts = Path(self.candidate['runtime']['artifacts'])
        self.baseline = self.artifacts / 'inputs/baseline'
        self.source = self.artifacts / 'workspace'
        self.entries = [{'path': path, 'type': 'blob', 'mode': '100755' if info['executable'] else '100644',
                         'sha': publication.object_sha('blob', (self.baseline / path).read_bytes())}
                        for path, info in manifest(self.baseline).items()]
        self.baseline_tree = publication.tree_sha(self.entries)
        self.base_sha = self.candidate['handoff']['baseline']
        self.commits = {self.base_sha: {'sha': self.base_sha, 'tree': {'sha': self.baseline_tree}}}
        self.refs = {'main': self.base_sha}
        self.repo = dict(self.fixture.repo, default_branch='main')
        self.pr = None
        self.calls = []
        self.rules = []
        self.protection = {'required_status_checks': {'strict': True, 'checks': [{'context': 'quality', 'app_id': 99},
                                                                               {'context': 'security', 'app_id': 99}]},
                           'enforce_admins': {'enabled': True}, 'allow_force_pushes': {'enabled': False},
                           'allow_deletions': {'enabled': False}}
        self.check_runs = {}
        self.protection_http = 200
        self.drop_merge = False
        self.omit_merge = False
        self.drop_close = False
        self.auto_close = False
        self.after_merge = None
        self.boundary_change = None
        self.before_main_commit = None
        self.before_close_readback = None
        self.membership = 'identical'
        self.merge_sha = 'c' * 40
        parent = self.fixture.server.RequestHandlerClass
        case = self

        class Handler(parent):
            def do_GET(self):
                suffix = self.path.removeprefix('/repos/example/product')
                value = None
                if suffix == '':
                    value = case.repo
                elif suffix.startswith('/git/commits/'):
                    if (case.before_main_commit and suffix == '/git/commits/' + case.refs['main'] and
                            case.before_main_commit()):
                        self.close_connection = True
                        return
                    value = case.commits.get(suffix.split('/')[-1])
                    if value is None:
                        super().do_GET()
                        return
                elif suffix.startswith('/git/trees/'):
                    value = {'sha': case.baseline_tree, 'tree': case.entries, 'truncated': False}
                elif suffix.startswith('/git/ref/heads/'):
                    branch = suffix.removeprefix('/git/ref/heads/')
                    if branch in case.refs:
                        value = {'ref': 'refs/heads/' + branch, 'object': {'type': 'commit', 'sha': case.refs[branch]}}
                elif suffix.startswith('/pulls?'):
                    value = [case.read_pr()] if case.pr else []
                elif suffix == '/pulls/20':
                    value = case.read_pr()
                elif suffix == '/issues/20':
                    value = {'number': 20, 'state': 'open', 'body': case.pr['body'], 'pull_request': {},
                             'repository_url': 'https://api.github.com/repos/example/product'}
                elif suffix in {'/rules/branches/main', '/branches/main/protection'}:
                    case.calls.append(('GET', self.path, None))
                    if suffix == '/branches/main/protection' and case.boundary_change:
                        reads = sum(c[:2] == ('GET', self.path) for c in case.calls)
                        if reads == 2:
                            case.boundary_change()
                    if case.protection_http != 200:
                        self.reply(case.protection_http, {'message': 'Upgrade to GitHub Pro or make this repository public to enable this feature.'})
                    else:
                        self.reply(200, case.rules if suffix.startswith('/rules') else case.protection)
                    return
                elif suffix.endswith('/check-runs?per_page=100&filter=latest'):
                    sha = suffix.split('/')[2]
                    runs = case.check_runs.get(sha, [])
                    value = {'total_count': len(runs), 'check_runs': runs}
                elif suffix.startswith('/compare/'):
                    sha = suffix.split('/')[2].split('...')[0]
                    value = {'status': case.membership, 'base_commit': {'sha': sha}, 'merge_base_commit': {'sha': sha}}
                else:
                    super().do_GET()
                    return
                case.calls.append(('GET', self.path, None))
                self.reply(404 if value is None else 200, {} if value is None else value)

            def do_POST(self):
                if self.path not in {'/repos/example/product/git/blobs', '/repos/example/product/git/trees',
                                     '/repos/example/product/git/commits', '/repos/example/product/git/refs', '/repos/example/product/pulls'}:
                    super().do_POST()
                    return
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.calls.append(('POST', self.path, body))
                suffix = self.path.removeprefix('/repos/example/product')
                if suffix == '/git/blobs':
                    value = {'sha': publication.object_sha('blob', base64.b64decode(body['content']))}
                elif suffix == '/git/trees':
                    value = {'sha': case.plan['tree']}
                elif suffix == '/git/commits':
                    value = {'sha': case.plan['candidate'], 'tree': {'sha': body['tree']},
                             'parents': [{'sha': sha} for sha in body['parents']], 'message': body['message'].rstrip('\n')}
                    case.commits[value['sha']] = value
                elif suffix == '/git/refs':
                    case.refs[body['ref'].removeprefix('refs/heads/')] = body['sha']
                    value = {}
                else:
                    case.pr = dict(body, number=20, state='open', merged=False, mergeable=True, mergeable_state='clean')
                    value = case.read_pr()
                self.reply(201, value)

            def do_PUT(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.calls.append(('PUT', self.path, body))
                if self.path != '/repos/example/product/pulls/20/merge':
                    self.reply(403, {})
                    return
                assert body == {'sha': case.config['head'], 'merge_method': 'merge'}
                stored = case.stored()['merge']
                assert stored['status'] == 'pending' and stored['integrated_evidence'] is None
                assert 'bearer' not in stored['authority']
                if not case.omit_merge:
                    case.pr.update(state='closed', merged=True, merge_commit_sha=case.merge_sha)
                    case.commits[case.merge_sha] = {'sha': case.merge_sha, 'tree': {'sha': case.plan['tree']},
                                                  'parents': [{'sha': case.base_sha}, {'sha': case.config['head']}]}
                    case.refs['main'] = case.merge_sha
                    if case.auto_close:
                        case.assignment.tracker.issues[10]['state'] = 'closed'
                    if case.after_merge:
                        case.after_merge()
                if case.drop_merge:
                    self.close_connection = True
                    return
                self.reply(200, {'merged': not case.omit_merge, 'sha': case.merge_sha})

            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.calls.append(('PATCH', self.path, body))
                assert self.path == '/repos/example/product/issues/10'
                if body['state'] == 'closed':
                    stored = case.stored()['merge']
                    assert stored['integrated_evidence']['integrated'] == case.merge_sha
                    assert stored['delivery_checks'] and stored['close_allowed'] is True
                elif case.auto_close:
                    stored = case.stored()['merge']
                    assert stored['close_allowed'] is False
                    assert stored['failure'] or not stored['delivery_checks']
                case.assignment.tracker.issues[10].update(body)
                if body['state'] == 'closed' and case.before_close_readback and case.before_close_readback():
                    self.close_connection = True
                    return
                if case.drop_close:
                    self.close_connection = True
                    return
                self.reply(200, case.assignment.tracker.issues[10])

        self.fixture.server.RequestHandlerClass = Handler
        self.publish_config = {'operator_id': '42', 'reference': 'SIMULATED scoped publication authority',
            'execution_reference': 'SIMULATED host verification, NOT live worker provenance',
            'binding': publication.binding(self.candidate), 'manifest': manifest(self.source),
            'paths': ['hello.py'], 'policy_paths': [], 'repository': 'example/product', 'repository_id': 123,
            'branch': 'factory/issue-10', 'base': 'main', 'issue': 10, 'expected_head': self.base_sha,
            'candidate': 'b' * 40, 'commit_date': '2026-01-01T00:00:00Z', 'api_base': self.fixture.base,
            'bearer': 'SIMULATED-HOST-ONLY-CREDENTIAL'}
        self.publication_path = self.root / 'publication.json'
        self.publication_path.write_text(json.dumps(self.publish_config))
        self.publication_path.chmod(0o600)
        self.plan = self.assignment.ok(self.publish('--plan-only'))
        self.publish_config['candidate'] = self.plan['candidate']
        self.publication_path.write_text(json.dumps(self.publish_config))
        self.assignment.ok(self.publish())
        # Publication deliberately stays draft; independent PR readiness is a prerequisite.
        self.pr['draft'] = False
        self.candidate = self.assignment.ok(self.assignment.inspect(self.candidate))
        self.config = {'operator_id': '42', 'execution_reference': 'LABELED simulated independent host verification, NOT live acceptance',
            'executions': [merge.execution_binding(a) for a in [self.builder, self.candidate, *self.axes]],
            'candidate_assignment': self.candidate['assignment_id'], **self.review.pair_request(self.axes),
            'repository': 'example/product', 'repository_id': 123, 'base': 'main', 'branch': 'factory/issue-10',
            'pr_number': 20, 'head': self.plan['candidate'], 'baseline': self.base_sha, 'issue': 10,
            'api_base': self.fixture.base, 'bearer': 'SIMULATED-HOST-ONLY-CREDENTIAL',
            'required_checks': copy.deepcopy(self.protection['required_status_checks']['checks']),
            'protection_sha256': assignments.digest({'rules': self.rules, 'protection': self.protection}), 'recovery': None}
        self.config_path = self.root / 'private-merge.json'
        self.write_config()
        self.pass_checks(self.config['head'])
        self.pass_checks(self.base_sha)
        self.calls.clear()

    def publish(self, *extra):
        return self.fixture.cli('publish-candidate', '--project', 'product', '--iteration', 'm1',
                                '--assignment', self.candidate['assignment_id'], '--publication-config', str(self.publication_path), *extra)

    def read_pr(self):
        if self.pr is None:
            return None
        value = copy.deepcopy(self.pr)
        for side in ('base', 'head'):
            value[side] = {'ref': self.pr[side], 'sha': self.refs[self.pr[side]], 'repo': copy.deepcopy(self.repo)}
        return value

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)

    def pass_checks(self, sha):
        self.check_runs[sha] = [{'id': i + 1, 'name': c['context'], 'app': {'id': c['app_id']},
                                 'head_sha': sha, 'status': 'completed', 'conclusion': 'success'}
                                for i, c in enumerate(self.config['required_checks'])]

    def cli(self, action='merge-candidate'):
        return self.fixture.cli(action, '--project', 'product', '--iteration', 'm1', '--merge-config', str(self.config_path))

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(self.config['bearer'], result.stdout + result.stderr)
        return json.loads(result.stdout)

    def refused(self, code, action='merge-candidate'):
        result = self.cli(action)
        self.assignment.refused(result, code)
        self.assertNotIn(self.config['bearer'], result.stdout + result.stderr)
        return result

    def stored(self):
        with closing(sqlite3.connect(self.fixture.state)) as db:
            return json.loads(db.execute('SELECT payload FROM role_assignments WHERE assignment_id=?',
                                        (self.candidate['assignment_id'],)).fetchone()[0])

    def write_assignment(self, assignment):
        with closing(sqlite3.connect(self.fixture.state)) as db, db:
            db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                       (json.dumps(assignment), assignment['assignment_id']))

    def writes(self, method=None):
        return [c for c in self.calls if c[0] == method] if method else [c for c in self.calls if c[0] != 'GET']

    def install_delivery_observer(self):
        from factory_v1.tests.test_delivery import DeliveryTarget, configuration
        config = configuration(self.fixture.base, repository_id=123, issue=10)
        config['required_checks'] = copy.deepcopy(self.config['required_checks'])
        target = DeliveryTarget(config, self.base_sha)
        target.attach(self.fixture.server, extra_only=True)
        path = self.root / 'private-delivery.json'
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        result = self.fixture.cli('observe-delivery', '--project', 'product', '--iteration', 'm1', '--delivery-config', str(path))
        observed = self.ok(result)
        self.assertEqual(observed['evidence']['status'], 'healthy')
        return target, path


    def assert_incident_repair_uses_same_checks_and_independent_reviews(self):
        request = self.assignment.configuration(stage='repair')
        request.update(baseline=self.base_sha, claim_id='incident-repair')
        request['profile'].update(name='incident-repair', home=str(self.root / 'incident-repair-profile'))
        prepared = self.assignment.ok(self.assignment.command(request=request))
        builder = self.assignment.ok(self.review.case.launch(prepared))
        self.assertEqual(builder['runtime']['status'], 'complete', builder)
        simplified = self.assignment.ok(self.review.seam.prepare(self.review.seam.request(builder, 'repair')))
        candidate = self.assignment.ok(self.review.case.launch(simplified))
        axes = self.review.pair(suffix='-repair', candidate=candidate)
        self.assignment.ok(self.review.command('review-candidate', self.review.pair_request(axes), self.review.authorization(axes)))
        self.candidate = candidate
        self.artifacts = Path(candidate['runtime']['artifacts'])
        self.source = self.artifacts / 'workspace'
        self.publish_config.update(binding=publication.binding(candidate), manifest=manifest(self.source),
                                   branch='factory/repair-10', candidate='b' * 40)
        self.publication_path.write_text(json.dumps(self.publish_config))
        self.plan = self.assignment.ok(self.publish('--plan-only'))
        self.publish_config['candidate'] = self.plan['candidate']
        self.publication_path.write_text(json.dumps(self.publish_config))
        self.pr = None
        self.assignment.ok(self.publish())
        self.pr['draft'] = False
        self.candidate = self.assignment.ok(self.assignment.inspect(candidate))
        with closing(sqlite3.connect(self.fixture.state)) as db, db:
            item = json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])
            incident = item.get('main_incident') or {'status': 'active', 'incident_id': 'main-failure-1'}
            incident.update(recovery_assignment=builder['assignment_id'], recovery_issue=10)
            recovery = {'incident_id': incident['incident_id'], 'implementation_assignment': builder['assignment_id'], 'issue': 10}
            item['main_incident'] = incident
            db.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
            if 'delivery_monitor' in item:
                # SIMULATED future repair-controller authorization; both pointers update atomically.
                db.execute('UPDATE delivery_incidents SET payload=? WHERE id=?', (json.dumps(incident), incident['incident_id']))
        self.config.update(candidate_assignment=self.candidate['assignment_id'], **self.review.pair_request(axes),
            executions=[merge.execution_binding(a) for a in [builder, self.candidate, *axes]],
            branch='factory/repair-10', head=self.plan['candidate'], recovery=recovery)
        self.write_config()
        self.pass_checks(self.config['head'])
        self.check_runs[self.base_sha] = []
        self.calls.clear()
        self.assertEqual(self.ok(self.cli())['status'], 'merged')
        self.pass_checks(self.merge_sha)
        self.assertEqual(self.ok(self.cli('verify-delivery'))['status'], 'delivered')
        self.assertEqual(len(self.writes('PUT')), 1)
        # This operation cannot clear the incident or claim healthy deployment.
        item = self.assignment.tracker.inspect()
        self.assertEqual(item['main_incident']['status'], 'active')

import hashlib
import tempfile
from unittest.mock import Mock, patch
from factory_v1 import controls, delivery, requirements
from factory_v1.repositories import RepositoryError


class MergeComponentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(self.db.close)
        (self.root / 'state.sqlite').chmod(0o600)
        self.db.executescript('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT);'
                             'CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT);')
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'status': 'active', 'execution_allowed': False,
            'approval': {'operator_id': '42', 'reference': 'Local merge fixture'}, 'repository': 'example/product',
            'repository_onboarding': {'metadata': {'id': 123}, 'api_base': 'https://api.github.com'},
            'handoff': {'commit': 'c' * 40}, 'ticket_work': {'input_digest': 'd' * 64,
                'inputs': {'handoff': {'commit': 'c' * 40}, 'documents': {'spec': {'sha256': 'e' * 64}}}}}
        self.save_item()
        self.body = 'Local required greeting'
        self.candidate = {'assignment_id': 'a' * 64, 'claim_id': 'local-candidate',
            'handoff': {'stage': 'simplify', 'repository': 'example/product', 'repository_id': 123,
                'workspace': str(self.root / 'worker'), 'profile': {'name': 'local', 'home': str(self.root / 'profile')},
                'issue': {'number': 10, 'id': 110, 'node_id': 'ISSUE10', 'body_sha256': hashlib.sha256(self.body.encode()).hexdigest()},
                'skills': []}, 'ticket_scope': {'project': 'product', 'iteration': 'm1', 'issue_number': 10},
            'revision': requirements.authority(self.item, 10)}
        self.candidate['handoff_digest'] = assignments.digest(self.candidate['handoff'])
        self.builder = {'assignment_id': 'b' * 64, 'handoff': {'stage': 'implementation'}}
        self.store(self.candidate)
        self.classic = {'required_status_checks': {'strict': True, 'checks': [{'context': 'quality', 'app_id': 99}]},
            'enforce_admins': {'enabled': True}, 'allow_force_pushes': {'enabled': False}, 'allow_deletions': {'enabled': False}}
        self.rules = []
        self.config = {'operator_id': '42', 'execution_reference': 'LABELED prequalified host input, not native execution',
            'executions': [{'local': n} for n in range(4)], 'candidate_assignment': 'a' * 64,
            'standards_assignment': '1' * 64, 'spec_assignment': '2' * 64,
            'repository': 'example/product', 'repository_id': 123, 'base': 'main', 'branch': 'factory/10',
            'pr_number': 20, 'head': 'b' * 40, 'baseline': 'a' * 40, 'issue': 10,
            'api_base': 'https://api.github.com', 'bearer': 'LABELED-LOCAL-NONSECRET',
            'required_checks': copy.deepcopy(self.classic['required_status_checks']['checks']),
            'protection_sha256': assignments.digest({'rules': self.rules, 'protection': self.classic}), 'recovery': None}
        self.config_path = self.root / 'merge.json'
        self.write_config()
        self.plan = {'tree': 'e' * 40, 'base_tree': 'f' * 40}
        self.integrated = 'c' * 40
        self.refs = {'main': self.config['baseline'], self.config['branch']: self.config['head']}
        repo = {'id': 123, 'full_name': 'example/product', 'default_branch': 'main'}
        self.repo = repo
        self.pr = {'number': 20, 'state': 'open', 'merged': False, 'draft': False,
            'mergeable': True, 'mergeable_state': 'clean',
            'head': {'ref': self.config['branch'], 'sha': self.config['head'], 'repo': repo},
            'base': {'ref': 'main', 'sha': self.config['baseline'], 'repo': repo}}
        self.work_issue = {**self.candidate['handoff']['issue'], 'body': self.body, 'state': 'open',
                           'repository_url': 'https://api.github.com/repos/example/product'}
        self.commits = {self.integrated: {'sha': self.integrated, 'tree': {'sha': self.plan['tree']},
            'parents': [{'sha': self.config['baseline']}, {'sha': self.config['head']}]}}
        self.run_records = {sha: self.good_checks(sha) for sha in (self.config['head'], self.config['baseline'], self.integrated)}
        self.calls = []
        self.protection_reads = 0
        self.boundary_change = None
        self.merge_effect = True
        self.drop_merge = False
        self.drop_state = False
        self.auto_close = False
        self.after_merge = None
        self.membership = 'identical'
        self.remote_inputs = copy.deepcopy(self.item['ticket_work']['inputs'])
        self.api = merge.MergeGitHub(self.config)
        self.external_request = Mock(side_effect=self.external)
        self.api.github.request = self.external_request
        self.enterContext(patch.object(merge, 'MergeGitHub', return_value=self.api))
        # Native chain authority and Git byte/mode assembly are prerequisite seams.
        # This component tests final admission/journals/receipts, not whole stage execution.
        self.enterContext(patch.object(merge, 'chain', side_effect=lambda *a: (self.stored(), self.builder, self.root)))
        self.enterContext(patch.object(merge, 'mapped_candidate', return_value=self.plan))
        self.review = self.enterContext(patch.object(merge.review, 'evaluate', return_value={'status': 'accepted'}))
        self.enterContext(patch.object(delivery, 'mutation_boundary', side_effect=lambda *a: assignments.current(*a)))
        # External pinned-document loader only; real scoped requirement equality remains.
        self.enterContext(patch.object(merge.tickets, 'pinned_inputs', side_effect=lambda *a: copy.deepcopy(self.remote_inputs)))

    def save_item(self):
        self.db.execute('DELETE FROM iterations')
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(self.item)))
        self.db.commit()

    def store(self, row):
        self.db.execute('INSERT OR REPLACE INTO role_assignments VALUES (?,?)', (row['assignment_id'], json.dumps(row)))
        self.db.commit()

    def stored(self):
        return json.loads(self.db.execute('SELECT payload FROM role_assignments WHERE assignment_id=?', ('a' * 64,)).fetchone()[0])

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)

    def good_checks(self, sha):
        return {'total_count': 1, 'check_runs': [{'id': 1, 'name': 'quality', 'app': {'id': 99},
            'head_sha': sha, 'status': 'completed', 'conclusion': 'success'}]}

    def external(self, path, body=None, method=None):
        method = method or ('POST' if body is not None else 'GET')
        suffix = path.removeprefix('/repos/example/product')
        self.calls.append((method, suffix, copy.deepcopy(body)))
        if method == 'PUT':
            journal = self.stored()['merge']
            self.assertEqual(journal['status'], 'pending')
            self.assertIsNone(journal['integrated_evidence'])
            self.assertNotIn('bearer', journal['authority'])
            if self.merge_effect:
                self.pr.update(merged=True, state='closed', merge_commit_sha=self.integrated)
                self.refs['main'] = self.integrated
                if self.auto_close: self.work_issue['state'] = 'closed'
                if self.after_merge: self.after_merge()
            if self.drop_merge: raise RepositoryError('transport_error', 'Local lost response')
            return {'merged': self.merge_effect}
        if method == 'PATCH':
            journal = self.stored().get('merge')
            assert body is not None
            if body['state'] == 'closed':
                self.assertTrue(journal['close_allowed'])
                self.assertTrue(journal['delivery_checks'])
            elif journal:
                self.assertFalse(journal['close_allowed'])
            self.work_issue.update(body)
            if self.drop_state: raise RepositoryError('transport_error', 'Local lost state response')
            return copy.deepcopy(self.work_issue)
        if suffix == '': value = self.repo
        elif suffix == '/issues/10': value = self.work_issue
        elif suffix == '/pulls/20': value = self.pr
        elif suffix == '/rules/branches/main': value = self.rules
        elif suffix == '/branches/main/protection':
            value = copy.deepcopy(self.classic)
            self.protection_reads += 1
            if self.boundary_change: self.boundary_change(self.protection_reads)
        elif suffix.startswith('/git/ref/heads/'):
            branch = suffix.removeprefix('/git/ref/heads/')
            value = {'ref': 'refs/heads/' + branch, 'object': {'type': 'commit', 'sha': self.refs[branch]}}
        elif suffix.startswith('/commits/'):
            value = self.run_records.get(suffix.split('/')[2], {'total_count': 0, 'check_runs': []})
        elif suffix.startswith('/git/commits/'): value = self.commits[suffix.split('/')[-1]]
        elif suffix.startswith('/compare/'):
            sha = suffix.split('/')[-1].split('...')[0]
            value = {'status': self.membership, 'base_commit': {'sha': sha}, 'merge_base_commit': {'sha': sha}}
        else: raise AssertionError('Unplanned external route ' + suffix)
        return copy.deepcopy(value)

    def lifecycle(self, action='merge-candidate'):
        return merge.lifecycle(self.db, 'product', 'm1', '42', self.config_path, action)

    def mutations(self, method=None):
        return [c for c in self.calls if c[0] == method] if method else [c for c in self.calls if c[0] != 'GET']

    def test_intent_precedes_effect_and_lost_merge_autoclose_reopens_truthfully(self):
        self.auto_close = self.drop_merge = self.drop_state = True
        journal = self.lifecycle()
        self.assertEqual(journal['status'], 'merged')
        self.assertFalse(journal['close_allowed'])
        self.assertEqual(journal['delivery_checks'], [])
        self.assertEqual(self.work_issue['state'], 'open')
        self.assertEqual(len(self.mutations('PUT')), 1)
        self.assertEqual(len(self.mutations('PATCH')), 1)
        self.assertEqual(self.lifecycle()['status'], 'merged')
        self.assertEqual(len(self.mutations('PUT')), 1)
        self.assertEqual(len(self.mutations('PATCH')), 1)
        with closing(sqlite3.connect(self.root / 'state.sqlite')) as restarted:
            self.assertEqual(merge.inspect_merge(restarted, 'product', 'm1', '42', 'a' * 64)['merge']['integrated_evidence']['integrated'], self.integrated)

    def test_pending_uncertain_intent_never_resends_even_while_paused(self):
        self.merge_effect = False
        self.drop_merge = True
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle()
        self.assertEqual(refusal.exception.code, 'merge_uncertain')
        controls.request(self.db, 'product', 'm1', '42', 'pause', 'pause pending merge')
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle()
        self.assertEqual(refusal.exception.code, 'merge_uncertain')
        self.assertEqual(self.stored()['merge']['status'], 'pending')
        self.assertEqual(len(self.mutations('PUT')), 1)

    def test_late_pause_after_last_protection_read_blocks_intent_and_put(self):
        def change(reads):
            if reads == 3:
                controls.request(self.db, 'product', 'm1', '42', 'pause', 'pause final admission read')
        self.boundary_change = change
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle()
        self.assertEqual(refusal.exception.code, 'iteration_paused')
        self.assertEqual(self.protection_reads, 3)
        self.assertEqual(self.mutations(), [])
        self.assertNotIn('merge', self.stored())

    def test_final_publication_document_drift_blocks_intent_and_put(self):
        def change(reads):
            if reads == 3: self.remote_inputs['documents']['spec']['sha256'] = '0' * 64
        self.boundary_change = change
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle()
        self.assertEqual(refusal.exception.code, 'stale_requirements')
        self.assertEqual(self.protection_reads, 3)
        self.assertEqual(self.mutations(), [])
        self.assertNotIn('merge', self.stored())

    def test_final_issue_identity_or_body_drift_refuses_unsafe_writes(self):
        for field, value, code in [('node_id', 'REPLACED', 'scope_mismatch'),
                                   ('body', self.body + ' changed', 'stale_admission')]:
            self.protection_reads = 0
            self.calls.clear()
            self.work_issue.update(node_id='ISSUE10', body=self.body)
            self.store(self.candidate)
            def change(reads):
                if reads == 3:
                    self.work_issue[field] = value
            self.boundary_change = change
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    self.lifecycle()
                self.assertEqual(refusal.exception.code, code)
            self.assertEqual(self.mutations(), [])
            self.assertNotIn('merge', self.stored())

    def test_final_review_admission_cannot_use_earlier_pass(self):
        self.review.side_effect = [{'status': 'accepted'}, {'status': 'corrections'}]
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle()
        self.assertEqual(refusal.exception.code, 'stale_review')
        self.assertEqual(self.mutations(), [])
        self.assertNotIn('merge', self.stored())

    def test_delivery_incident_at_final_sample_freezes_ordinary_merge(self):
        calls = 0
        def boundary(*args):
            nonlocal calls
            calls += 1
            item = assignments.current(*args)
            if calls == 2:
                item['main_incident'] = {'status': 'active', 'incident_id': 'new-incident'}
            return item
        with patch.object(delivery, 'mutation_boundary', side_effect=boundary):
            with self.assertRaises(RepositoryError) as refusal: self.lifecycle()
        self.assertEqual(refusal.exception.code, 'main_frozen')
        self.assertEqual(self.mutations(), [])
        self.assertNotIn('merge', self.stored())

    def test_exact_repair_scope_keeps_checks_reviews_and_incident(self):
        self.builder['handoff']['stage'] = 'repair'
        incident = {'status': 'active', 'incident_id': 'incident-1', 'recovery_assignment': self.builder['assignment_id'], 'recovery_issue': 10}
        self.item['main_incident'] = incident
        self.save_item()
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle()
        self.assertEqual(refusal.exception.code, 'main_frozen')
        self.config['recovery'] = {'incident_id': 'incident-1', 'implementation_assignment': self.builder['assignment_id'], 'issue': 10}
        self.write_config()
        self.run_records[self.config['baseline']] = {'total_count': 0, 'check_runs': []}
        self.assertEqual(self.lifecycle()['status'], 'merged')
        self.assertGreaterEqual(self.review.call_count, 2)
        self.assertEqual(assignments.current(self.db, 'product', 'm1', '42')['main_incident'], incident)
        self.assertEqual(self.lifecycle('verify-delivery')['status'], 'delivered')
        self.assertEqual(assignments.current(self.db, 'product', 'm1', '42')['main_incident'], incident)

    def test_integrated_checks_only_delivery_and_lost_closure_readback(self):
        self.lifecycle()
        self.work_issue['state'] = 'closed'
        self.run_records[self.integrated] = self.good_checks(self.config['head'])
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle('verify-delivery')
        self.assertEqual(refusal.exception.code, 'checks_stale')
        self.assertEqual(self.work_issue['state'], 'open')
        self.assertEqual(self.stored()['merge']['status'], 'incomplete')
        self.run_records[self.integrated] = self.good_checks(self.integrated)
        self.drop_state = True
        delivered = self.lifecycle('verify-delivery')
        self.assertEqual(delivered['status'], 'delivered')
        self.assertEqual(self.work_issue['state'], 'closed')
        self.assertEqual({r['head_sha'] for r in delivered['delivery_checks']}, {self.integrated})
        self.assertFalse(delivered['iteration_complete'])
        self.assertFalse(delivered['deployment_verified'])
        writes = self.mutations()
        self.assertEqual(self.lifecycle('verify-delivery'), delivered)
        self.assertEqual(self.mutations(), writes)

    def test_prior_delivery_receipts_survive_unknown_main_drift_without_reopening(self):
        self.lifecycle()
        delivered = self.lifecycle('verify-delivery')
        later = 'd' * 40
        self.refs['main'] = later
        self.membership = 'ahead'
        self.commits[later] = {'sha': later, 'tree': {'sha': '0' * 40}}
        writes = self.mutations()
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle('verify-delivery')
        self.assertEqual(refusal.exception.code, 'delivery_stale')
        retained = self.stored()['merge']
        self.assertEqual(retained['status'], 'revalidation-required')
        self.assertFalse(retained['close_allowed'])
        self.assertEqual(retained['delivery_checks'], delivered['delivery_checks'])
        self.assertEqual(retained['integrated_evidence'], delivered['integrated_evidence'])
        self.assertEqual(self.work_issue['state'], 'closed')
        self.assertEqual(self.mutations(), writes)
        self.commits[later]['tree']['sha'] = self.plan['base_tree']
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle('verify-delivery')
        self.assertEqual(refusal.exception.code, 'delivery_removed')
        self.assertEqual(self.work_issue['state'], 'open')
        self.assertEqual(self.stored()['merge']['status'], 'incomplete')

    def test_reopened_delivery_checkpoint_keeps_prior_receipts_before_close_response(self):
        self.lifecycle()
        delivered = self.lifecycle('verify-delivery')
        self.work_issue['state'] = 'open'
        original = self.external_request.side_effect
        def read_return(path, body=None, method=None):
            if method == 'PATCH' and body['state'] == 'closed':
                retained = self.stored()['merge']
                self.assertEqual(retained['status'], 'delivered')
                self.assertEqual(retained['delivery_checks'], delivered['delivery_checks'])
                self.assertEqual(retained['integrated_evidence'], delivered['integrated_evidence'])
            return original(path, body, method)
        self.external_request.side_effect = read_return
        self.assertEqual(self.lifecycle('verify-delivery')['status'], 'delivered')
        self.assertEqual(len(self.mutations('PUT')), 1)

    def test_checks_and_resealed_protection_semantic_matrix(self):
        self.assertTrue(merge.protection(self.api, self.config))
        for field, value in [('strict', False), ('checks', [])]:
            old = copy.deepcopy(self.classic)
            self.classic['required_status_checks'][field] = value
            self.config['protection_sha256'] = assignments.digest({'rules': self.rules, 'protection': self.classic})
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    merge.protection(self.api, self.config)
                self.assertEqual(refusal.exception.code, 'protection_required')
            self.classic = old
        self.rules = [{'type': 'merge_queue'}]
        self.config['protection_sha256'] = assignments.digest({'rules': self.rules, 'protection': self.classic})
        with self.assertRaises(RepositoryError) as refusal: merge.protection(self.api, self.config)
        self.assertEqual(refusal.exception.code, 'protection_required')
        for field, value, code in [('head_sha', '0' * 40, 'checks_stale'), ('conclusion', 'failure', 'checks_failed'),
                                   ('status', 'queued', 'checks_failed'), ('app', {'id': 88}, 'checks_unknown')]:
            records = self.good_checks(self.config['head'])
            records['check_runs'][0][field] = value
            self.run_records[self.config['head']] = records
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    merge.checks(self.api, self.config, self.config['head'])
                self.assertEqual(refusal.exception.code, code)
        self.assertEqual(self.mutations(), [])

    def test_controller_config_private_scope_and_fixed_intent(self):
        self.config_path.chmod(0o644)
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle()
        self.assertEqual(refusal.exception.code, 'execution_required')
        self.config_path.chmod(0o600)
        worker = self.root / 'worker'
        worker.mkdir()
        mounted = worker / 'merge.json'
        mounted.write_bytes(self.config_path.read_bytes())
        mounted.chmod(0o600)
        with self.assertRaises(RepositoryError) as refusal: merge.controller_paths(self.db, mounted)
        self.assertEqual(refusal.exception.code, 'execution_required')
        self.lifecycle()
        self.config['execution_reference'] = 'Replacement authority'
        self.write_config()
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle()
        self.assertEqual(refusal.exception.code, 'merge_conflict')
        self.assertEqual(len(self.mutations('PUT')), 1)

    def test_actual_merge_capability_denies_unscoped_write_and_branch_only_closure(self):
        with self.assertRaises(RepositoryError) as refusal:
            self.api.request(self.api.prefix + '/issues/11', {'state': 'closed'}, 'PATCH')
        self.assertEqual(refusal.exception.code, 'capability_blocked')
        self.work_issue['state'] = 'closed'
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle('verify-delivery')
        self.assertEqual(refusal.exception.code, 'not_integrated')
        self.assertEqual(self.work_issue['state'], 'open')
        self.assertEqual(self.mutations('PUT'), [])

    def test_changed_requirements_during_autoclosure_reopen_with_incomplete_receipt(self):
        self.auto_close = True
        self.after_merge = lambda: self.work_issue.update(body=self.body + ' revised during integration')
        with self.assertRaises(RepositoryError) as refusal:
            self.lifecycle()
        self.assertEqual(refusal.exception.code, 'stale_requirements')
        self.assertEqual(self.work_issue['state'], 'open')
        journal = self.stored()['merge']
        self.assertEqual(journal['status'], 'incomplete')
        self.assertFalse(journal['close_allowed'])
        self.assertEqual(journal['integrated_evidence']['integrated'], self.integrated)
        self.assertEqual(len(self.mutations('PUT')), 1)
        self.assertEqual(len(self.mutations('PATCH')), 1)

    def test_real_integrated_tree_parents_and_membership_are_not_branch_proof(self):
        self.pr.update(merged=True, state='closed', merge_commit_sha=self.integrated)
        self.refs['main'] = self.integrated
        self.assertEqual(merge.merged_evidence(self.api, self.config, self.pr, self.plan)['integrated'], self.integrated)
        original = copy.deepcopy(self.commits[self.integrated])
        for change in ({'tree': {'sha': '0' * 40}}, {'parents': [{'sha': self.config['head']}]}):
            self.commits[self.integrated] = {**original, **change}
            with self.subTest(change=change):
                with self.assertRaises(RepositoryError) as refusal:
                    merge.merged_evidence(self.api, self.config, self.pr, self.plan)
                self.assertEqual(refusal.exception.code, 'integration_mismatch')
        self.commits[self.integrated] = original
        self.membership = 'diverged'
        with self.assertRaises(RepositoryError) as refusal:
            merge.merged_evidence(self.api, self.config, self.pr, self.plan)
        self.assertEqual(refusal.exception.code, 'not_integrated')
        self.assertEqual(self.mutations(), [])

    def test_final_branch_main_and_check_drift_never_admit_intent(self):
        for mode, code in [('main', 'stale_baseline'), ('branch', 'stale_admission'), ('check', 'checks_failed')]:
            self.protection_reads = 0
            self.calls.clear()
            self.refs.update(main=self.config['baseline'])
            self.refs[self.config['branch']] = self.config['head']
            self.run_records[self.config['head']] = self.good_checks(self.config['head'])
            self.store(self.candidate)
            def change(reads):
                if reads != 3:
                    return
                if mode == 'main':
                    self.refs['main'] = 'd' * 40
                elif mode == 'branch':
                    self.refs[self.config['branch']] = 'd' * 40
                else:
                    self.run_records[self.config['head']]['check_runs'][0]['conclusion'] = 'failure'
            self.boundary_change = change
            with self.subTest(mode=mode):
                with self.assertRaises(RepositoryError) as refusal:
                    self.lifecycle()
                self.assertEqual(refusal.exception.code, code)
            self.assertEqual(self.mutations(), [])
            self.assertNotIn('merge', self.stored())

    def test_identity_changed_after_merge_persists_hold_without_wrong_issue_write(self):
        self.auto_close = True
        self.after_merge = lambda: self.work_issue.update(node_id='REPLACED')
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle()
        self.assertEqual(refusal.exception.code, 'scope_mismatch')
        self.assertEqual(self.mutations('PATCH'), [])
        journal = self.stored()['merge']
        self.assertFalse(journal['close_allowed'])
        self.assertEqual(journal['status'], 'incomplete')


if __name__ == "__main__":
    unittest.main()
