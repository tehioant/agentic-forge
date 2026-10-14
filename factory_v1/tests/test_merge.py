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
        from factory_v1 import recovery, simplification
        self.builder['handoff']['stage'] = 'repair'
        incident = {'status': 'active', 'incident_id': 'incident-1', 'recovery_assignment': self.builder['assignment_id'], 'recovery_issue': 10}
        self.item['main_incident'] = incident
        self.save_item()
        with self.assertRaises(RepositoryError) as refusal: self.lifecycle()
        self.assertEqual(refusal.exception.code, 'main_frozen')
        # Labeled prequalified host registration/native-chain input; no repair worker is run.
        record = {'config': {'operator_id': '42', 'reference': 'LABELED registered authority fixture',
                    'incident_id': 'incident-1', 'issue': self.candidate['handoff']['issue'],
                    'evidence_id': 'e' * 64, 'paths': ['app.py'], 'removable_assignments': []},
                  'evidence': {'main': self.config['baseline']}, 'assignment': self.builder['assignment_id'],
                  'status': 'repair', 'parked': [], 'removed': {}, 'removable': {}, 'clearance': None}
        self.item['main_recoveries'] = {'incident-1': record}
        self.save_item()
        scope = recovery.contract(record)
        self.builder['handoff'].update(recovery=scope, adaptation='LABELED repair adaptation')
        self.builder['runtime'] = {'artifacts': str(self.root)}
        (self.root / 'workspace').mkdir()
        (self.root / 'inputs/baseline').mkdir(parents=True)
        (self.root / 'scratch').mkdir()
        (self.root / 'workspace/app.py').write_text('fixed')
        (self.root / 'inputs/baseline/app.py').write_text('broken')
        command, output = 'SIMULATED diagnostic', 'SIMULATED red output, not native execution'
        tests = [{'command': command, 'result': output}]
        (self.root / 'scratch/conversation.json').write_text(json.dumps({'messages': [
            {'role': 'assistant', 'tool_calls': [{'id': 'local', 'function': {'name': 'terminal', 'arguments': json.dumps({'command': command})}}]},
            {'role': 'tool', 'tool_call_id': 'local', 'content': output}]}))
        diagnosis = {'contract': 'factory-stuck-diagnosis-v1', 'pins': scope, 'adaptation': self.builder['handoff']['adaptation'],
            'feedback_loop': {'command': command, 'result': output, 'symptom': 'local regression'}, 'commands': tests,
            'hypotheses': [{'hypothesis': 'literal', 'prediction': 'fix restores behavior', 'evidence': output}],
            'attempted_repairs': ['bounded fix'], 'findings': ['literal differs'], 'cause_or_uncertainty': 'literal',
            'directions': [{'id': 'forward', 'direction': 'fix', 'tradeoff': 'retains behavior'}], 'recommendation': 'forward', 'blockers': []}
        report = {'contract': recovery.CONTRACT, 'scope': scope, 'mode': 'roll-forward', 'rationale': 'bounded fix',
                  'diagnosis': diagnosis, 'removed_assignments': [], 'regression': 'focused regression', 'cleanup': 'no probes'}
        self.builder['submitted_result'] = {'status': 'done', 'tests': tests,
            'artifacts': [simplification.artifact('recovery-report', json.dumps(report))]}
        self.config['recovery'] = {'incident_id': 'incident-1', 'implementation_assignment': self.builder['assignment_id'],
                                   'issue': 10, 'removed_assignments': []}
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

    def test_concrete_protection_http_failures_keep_preflight_immutable_and_refusal_durable(self):
        original = self.external_request.side_effect
        for status in (403, 404):
            with self.subTest(http_status=status):
                self.store(self.candidate)
                self.calls.clear()
                def inaccessible(path, body=None, method=None):
                    if path.endswith('/branches/main/protection'):
                        self.calls.append(('GET', '/branches/main/protection', None))
                        if status == 403:
                            raise RepositoryError('github_blocked', 'LOCAL simulated HTTP 403', status)
                        # GitHub.request translates GET HTTP 404 to an absent value.
                        return None
                    return original(path, body, method)
                self.external_request.side_effect = inaccessible
                before = list(self.db.iterdump())
                with self.assertRaises(RepositoryError) as refusal:
                    self.lifecycle('merge-preflight')
                self.assertEqual(refusal.exception.code, 'protection_required')
                self.assertEqual(list(self.db.iterdump()), before)
                with self.assertRaises(RepositoryError) as refusal:
                    self.lifecycle()
                self.assertEqual(refusal.exception.code, 'protection_required')
                self.assertEqual(self.stored()['merge_refusal']['code'], 'protection_required')
                self.assertNotIn('merge', self.stored())
                self.assertEqual(self.mutations(), [])
                self.assertEqual(self.work_issue['state'], 'open')
        self.external_request.side_effect = original

    def test_green_candidate_cannot_hide_failed_or_unknown_baseline_checks(self):
        for mode in ('failed', 'missing', 'unknown'):
            with self.subTest(main_checks=mode):
                self.store(self.candidate)
                self.refs['main'] = self.config['baseline']
                self.pr.update(state='open', merged=False)
                self.calls.clear()
                records = self.good_checks(self.config['baseline'])
                if mode == 'missing':
                    records = {'total_count': 0, 'check_runs': []}
                else:
                    records['check_runs'][0]['conclusion'] = 'failure' if mode == 'failed' else None
                self.run_records[self.config['baseline']] = records
                # The real baseline predicate, not a supplied main_incident, refuses.
                self.assertTrue(merge.checks(self.api, self.config, self.config['head']))
                with self.assertRaises(RepositoryError) as refusal:
                    self.lifecycle()
                self.assertEqual(refusal.exception.code, 'main_frozen')
                self.assertIn(('GET', '/commits/' + self.config['baseline'] +
                               '/check-runs?per_page=100&filter=latest', None), self.calls)
                self.assertEqual(self.stored()['merge_refusal']['code'], 'main_frozen')
                self.assertNotIn('merge', self.stored())
                self.assertEqual(self.mutations(), [])
                self.assertNotIn('main_incident', assignments.current(self.db, 'product', 'm1', '42'))

    def test_concurrent_locked_controllers_share_one_merge_intent_and_put(self):
        import fcntl
        from factory_v1.repositories import state_lock
        state = self.root / 'state.sqlite'
        started = threading.Barrier(2, timeout=3)
        local = threading.local()
        def stored():
            db = getattr(local, 'database', self.db)
            return json.loads(db.execute('SELECT payload FROM role_assignments WHERE assignment_id=?',
                                        (self.candidate['assignment_id'],)).fetchone()[0])
        def controller():
            with closing(sqlite3.connect(state, timeout=3)) as db:
                local.database = db
                started.wait()  # Never wait/join while holding the shared controller lock.
                with state_lock(db):
                    # A missing shared lock must fail deterministically, not by race luck.
                    with open(str(state) + '.onboarding.lock', 'a') as contender:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return merge.lifecycle(db, 'product', 'm1', '42', self.config_path, 'merge-candidate')
        with patch.object(self, 'stored', side_effect=stored):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(controller) for _ in range(2)]
                results = [future.result(timeout=5) for future in futures]
        self.assertEqual([r['status'] for r in results], ['merged', 'merged'])
        self.assertEqual(results[0]['integrated_evidence'], results[1]['integrated_evidence'])
        self.assertEqual(len(self.mutations('PUT')), 1)
        self.assertEqual(self.mutations('PATCH'), [])
        with closing(sqlite3.connect(state)) as restarted:
            journal = merge.inspect_merge(restarted, 'product', 'm1', '42', self.candidate['assignment_id'])['merge']
        self.assertEqual(journal['status'], 'merged')
        self.assertEqual(journal['integrated_evidence'], results[0]['integrated_evidence'])

    def test_material_spec_or_obsolete_assignment_reopens_retaining_integrated_receipts(self):
        authority = {k: v for k, v in self.config.items() if k != 'bearer'}
        evidence = {'candidate': self.config['head'], 'integrated': self.integrated,
                    'tree': self.plan['tree'], 'main': self.integrated, 'membership': 'identical',
                    'parents': [self.config['baseline'], self.config['head']]}
        prior = {'status': 'delivered', 'authority': authority, 'integrated_evidence': evidence,
                 'branch_checks': merge.checks(self.api, self.config, self.config['head']),
                 'protection': merge.protection(self.api, self.config),
                 'delivery_checks': merge.checks(self.api, self.config, self.integrated),
                 'failure': None, 'close_allowed': True, 'iteration_complete': False, 'deployment_verified': False}
        original_item = copy.deepcopy(self.item)
        self.pr.update(merged=True, state='closed', merge_commit_sha=self.integrated)
        self.refs['main'] = self.integrated
        # Only unrelated execution prerequisites are admitted; revision inspection is real.
        def revision_checked_chain(db, project, iteration, operator, config, path):
            return assignments.inspect(db, project, iteration, operator, config['candidate_assignment']), self.builder, self.root
        with patch.object(merge, 'chain', side_effect=revision_checked_chain):
            for mode, code in (('material-spec', 'stale_assignment'), ('obsolete', 'requirements_held')):
                with self.subTest(requirements=mode):
                    self.item = copy.deepcopy(original_item)
                    candidate = copy.deepcopy(self.candidate)
                    candidate['merge'] = copy.deepcopy(prior)
                    if mode == 'material-spec':
                        self.item['handoff']['commit'] = 'f' * 40
                    else:
                        candidate['requirement_disposition'] = {'change_id': 'material-revision', 'status': 'obsolete'}
                    self.save_item()
                    self.store(candidate)
                    self.work_issue['state'] = 'closed'
                    self.calls.clear()
                    with self.assertRaises(RepositoryError) as refusal:
                        self.lifecycle('verify-delivery')
                    self.assertEqual(refusal.exception.code, code)
                    retained = self.stored()['merge']
                    self.assertEqual(retained['status'], 'incomplete')
                    self.assertFalse(retained['close_allowed'])
                    self.assertEqual(retained['failure']['code'], code)
                    self.assertEqual(retained['integrated_evidence'], prior['integrated_evidence'])
                    self.assertEqual(retained['delivery_checks'], prior['delivery_checks'])
                    self.assertEqual(self.work_issue['state'], 'open')
                    self.assertEqual(self.mutations('PUT'), [])
                    self.assertEqual(len(self.mutations('PATCH')), 1)
                    with closing(sqlite3.connect(self.root / 'state.sqlite')) as restarted:
                        inspected = merge.inspect_merge(restarted, 'product', 'm1', '42', self.candidate['assignment_id'])
                    self.assertEqual(inspected['merge'], retained)
                    if mode == 'material-spec':
                        self.assertNotEqual(inspected['revision']['spec'], inspected['current_spec'])
                    else:
                        self.assertEqual(inspected['requirement_disposition'], candidate['requirement_disposition'])

    def test_missing_or_failed_integrated_checks_reopen_without_another_merge(self):
        self.lifecycle()
        for mode, code in (('missing', 'checks_unknown'), ('failed', 'checks_failed'), ('unknown', 'checks_failed')):
            with self.subTest(integrated_checks=mode):
                records = self.good_checks(self.integrated)
                if mode == 'missing':
                    records = {'total_count': 0, 'check_runs': []}
                else:
                    records['check_runs'][0]['conclusion'] = 'failure' if mode == 'failed' else None
                self.run_records[self.integrated] = records
                self.work_issue['state'] = 'closed'
                with self.assertRaises(RepositoryError) as refusal:
                    self.lifecycle('verify-delivery')
                self.assertEqual(refusal.exception.code, code)
                self.assertEqual(self.work_issue['state'], 'open')
                journal = self.stored()['merge']
                self.assertEqual(journal['status'], 'incomplete')
                self.assertEqual(journal['failure']['code'], code)
                self.assertFalse(journal['close_allowed'])
                self.assertEqual(journal['integrated_evidence']['integrated'], self.integrated)
                self.assertEqual(journal['delivery_checks'], [])
                self.assertEqual(len(self.mutations('PUT')), 1)

    def test_wrong_repo_missing_branch_checks_and_unbound_recovery_refuse_without_intent(self):
        config = copy.deepcopy(self.config)
        for mode, code in (('repository', 'scope_mismatch'), ('missing-check', 'checks_unknown'),
                           ('unknown-check', 'checks_failed'), ('boolean-recovery', 'scope_mismatch'),
                           ('ordinary-recovery', 'main_frozen')):
            with self.subTest(mode=mode):
                self.config = copy.deepcopy(config)
                self.repo['id'] = 123
                self.item.pop('main_incident', None)
                self.run_records[self.config['head']] = self.good_checks(self.config['head'])
                self.store(self.candidate)
                self.calls.clear()
                if mode == 'repository':
                    self.repo['id'] = 456
                elif mode == 'missing-check':
                    self.run_records[self.config['head']] = {'total_count': 0, 'check_runs': []}
                elif mode == 'unknown-check':
                    self.run_records[self.config['head']]['check_runs'][0]['conclusion'] = None
                else:
                    self.item['main_incident'] = {'status': 'active', 'incident_id': 'incident-1',
                        'recovery_assignment': self.builder['assignment_id'], 'recovery_issue': 10}
                    self.config['recovery'] = True if mode == 'boolean-recovery' else {
                        'incident_id': 'incident-1', 'implementation_assignment': self.builder['assignment_id'], 'issue': 10,
                        'removed_assignments': []}
                self.save_item()
                self.write_config()
                with self.assertRaises(RepositoryError) as refusal:
                    self.lifecycle()
                self.assertEqual(refusal.exception.code, code)
                self.assertEqual(self.mutations(), [])
                self.assertNotIn('merge', self.stored())

    def test_changed_or_resealed_weak_protection_and_malformed_check_counts_refuse(self):
        original = copy.deepcopy(self.classic)
        cases = [('strict', False), ('enforce_admins', False), ('allow_force_pushes', True), ('allow_deletions', True)]
        for field, value in cases:
            with self.subTest(protection=field):
                self.classic = copy.deepcopy(original)
                self.config['protection_sha256'] = assignments.digest({'rules': self.rules, 'protection': original})
                if field == 'strict':
                    self.classic['required_status_checks']['strict'] = value
                else:
                    self.classic[field]['enabled'] = value
                with self.assertRaises(RepositoryError) as refusal:
                    merge.protection(self.api, self.config)
                self.assertEqual(refusal.exception.code, 'stale_protection')
                self.config['protection_sha256'] = assignments.digest({'rules': self.rules, 'protection': self.classic})
                with self.assertRaises(RepositoryError) as refusal:
                    merge.protection(self.api, self.config)
                self.assertEqual(refusal.exception.code, 'protection_required')
        for count in (True, 0, 2, 100):
            with self.subTest(total_count=count):
                self.run_records[self.config['head']] = {**self.good_checks(self.config['head']), 'total_count': count}
                with self.assertRaises(RepositoryError) as refusal:
                    merge.checks(self.api, self.config, self.config['head'])
                self.assertEqual(refusal.exception.code, 'checks_unknown')
        self.assertEqual(self.mutations(), [])

    def test_late_config_or_protection_drift_blocks_durable_intent(self):
        for mode, code in (('config', 'stale_admission'), ('protection', 'stale_protection')):
            with self.subTest(mode=mode):
                self.store(self.candidate)
                self.calls.clear()
                self.protection_reads = 0
                self.classic['required_status_checks']['strict'] = True
                self.config['execution_reference'] = 'LOCAL unchanged authority'
                self.write_config()
                def change(reads):
                    if reads != 2:
                        return
                    if mode == 'config':
                        self.config['execution_reference'] = 'LOCAL replaced authority'
                        self.write_config()
                    else:
                        self.classic['required_status_checks']['strict'] = False
                self.boundary_change = change
                with self.assertRaises(RepositoryError) as refusal:
                    self.lifecycle()
                self.assertEqual(refusal.exception.code, code)
                self.assertNotIn('merge', self.stored())
                self.assertEqual(self.mutations(), [])

    def test_paused_pending_intent_remains_uncertain_on_fresh_connection(self):
        self.merge_effect = False
        self.drop_merge = True
        with self.assertRaises(RepositoryError) as refusal:
            self.lifecycle()
        self.assertEqual(refusal.exception.code, 'merge_uncertain')
        controls.request(self.db, 'product', 'm1', '42', 'pause', 'retain uncertain intent on restart')
        with closing(sqlite3.connect(self.root / 'state.sqlite')) as restarted:
            with self.assertRaises(RepositoryError) as refusal:
                merge.lifecycle(restarted, 'product', 'm1', '42', self.config_path, 'merge-candidate')
            self.assertEqual(refusal.exception.code, 'merge_uncertain')
            inspected = merge.inspect_merge(restarted, 'product', 'm1', '42', self.candidate['assignment_id'])
        self.assertEqual(inspected['merge']['status'], 'pending')
        self.assertFalse(inspected['merge']['close_allowed'])
        self.assertEqual(len(self.mutations('PUT')), 1)
        self.assertEqual(self.work_issue['state'], 'open')


class ChainAdmissionTests(unittest.TestCase):
    """Retained local declarations, NOT native/model execution or worker acceptance."""

    def setUp(self):
        from factory_v1.tests.test_assignments import PINS, SOURCE_ROOT
        directory = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(self.db.close)
        (self.root / 'state.sqlite').chmod(0o600)
        self.db.executescript('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT);'
                             'CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT);')
        item = {'status': 'active', 'execution_allowed': False,
            'approval': {'operator_id': '42', 'reference': 'Local declared chain prerequisite'},
            'handoff': {'commit': 'c' * 40}, 'ticket_work': {'input_digest': 'd' * 64,
                'inputs': {'handoff': {'commit': 'c' * 40}, 'documents': {'spec': {'sha256': 'e' * 64}}}}}
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(item)))
        self.db.commit()
        self.selected = {}
        for name in ('implement', 'simplify-code', 'code-review', 'code-review/references/structured-text-contracts.md'):
            suffix = name if name.endswith('.md') else name + '/SKILL.md'
            path = self.root / 'selected' / suffix
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((SOURCE_ROOT / suffix).read_bytes())
            source = '/home/ops/.hermes/skills/' + ('software-development/' if name == 'simplify-code' else '') + suffix
            self.selected[name] = {'name': name, 'source': source, 'path': str(path), 'sha256': PINS[name],
                'dependencies': ['code-review/references/structured-text-contracts.md'] if name == 'code-review' else [],
                'instructions': path.read_text()}
        self.rows = []
        for index, stage in enumerate(('implementation', 'simplify', 'review-standards', 'review-spec'), 1):
            root = self.root / stage
            root.mkdir(mode=0o700)
            for folder in ('scratch', 'inputs/baseline', 'workspace', 'model-evidence'):
                (root / folder).mkdir(parents=True)
            (root / 'inputs/baseline/hello.py').write_text('print("baseline")\n')
            (root / 'workspace/hello.py').write_text('print("candidate")\n')
            source, baseline = manifest(root / 'workspace'), manifest(root / 'inputs/baseline')
            run = 'declared-local-run-' + str(index)
            receipt = {'run_id': run, 'candidate': source,
                'provenance': 'controller_admitted_read_only_sandbox_checks',
                'fixture_label': 'LOCAL declaration, no checks process or native worker ran',
                'records': [{'command': 'python -m unittest local_greeting', 'exit_code': 0,
                    'stdout': 'LOCAL declared passing prerequisite', 'stderr': '', 'output_truncated': False}]}
            (root / 'controller-checks.json').write_text(json.dumps(receipt))
            (root / 'initial-source.json').write_text(json.dumps({'workspace': source, 'baseline': baseline}))
            for relative in ('scratch/result.json', 'scratch/loads.json', 'scratch/events.jsonl',
                             'scratch/conversation.json', 'scratch/probes.json', 'inputs/worker.py',
                             'inputs/sandbox_relay.py', 'inputs/assignment.json', 'inputs/handoff.json',
                             'container-inspection.json', 'launch-command.json', 'source-artifacts.json',
                             'model-evidence/response-local.json'):
                (root / relative).write_text('LOCAL retained declaration, NOT execution: ' + relative + '\n')
            names = ['implement'] if stage == 'implementation' else ['simplify-code'] if stage == 'simplify' else [
                'code-review', 'code-review/references/structured-text-contracts.md']
            h = {'stage': stage, 'profile': {'name': stage + '-local', 'home': str(self.root / ('profile-' + stage)),
                    'role': assignments.ROLES[stage], 'provider': assignments.SUBSCRIPTION_SCOPE[0],
                    'model': assignments.SUBSCRIPTION_SCOPE[1], 'operation': assignments.SUBSCRIPTION_SCOPE[2], 'reasoning': 'low'},
                'repository': 'example/product', 'repository_id': 123, 'workspace': str(root / 'workspace'),
                'issue': {'number': 10, 'id': 110, 'node_id': 'ISSUE10', 'body_sha256': 'f' * 64},
                'spec_commit': 'c' * 40, 'baseline': 'a' * 40, 'candidate': 'b' * 40,
                'standards': [merge.simplification.artifact('AGENTS.md', 'Local fixture standards')],
                'preceding': [], 'skills': [copy.deepcopy(self.selected[name]) for name in names],
                'capabilities': ['read_workspace', 'scratch', 'model'] + (['write_workspace'] if index <= 2 else []),
                'result_contract': assignments.CONTRACT, 'claim_id': 'declared-claim-' + str(index),
                'adaptation': merge.role_skills.ADAPTATIONS[stage], 'input_loads': {'fixture:requirements': 'e' * 64}}
            row = {'assignment_id': str(index) * 64, 'claim_id': h['claim_id'], 'handoff': h,
                'ticket_scope': {'project': 'product', 'iteration': 'm1', 'issue_number': 10},
                'revision': requirements.authority(item, 10),
                'runtime': {'status': 'complete', 'container_removed': True, 'artifacts': str(root), 'run_id': run,
                    'candidate_sha256': assignments.digest(source), 'baseline_sha256': assignments.digest(baseline),
                    'checks_sha256': hashlib.sha256((root / 'controller-checks.json').read_bytes()).hexdigest()},
                'result_disposition': {'checks_verified': True}}
            row['submitted_result'] = {'assignment_id': row['assignment_id'], 'claim_id': row['claim_id'],
                'run_id': run, 'status': 'done', 'work': [{'reference': 'LOCAL declared stage work, NOT native execution'}],
                'loads': [{'source': key, 'sha256': value, 'tool_reference': 'LOCAL declared load'}
                    for key, value in ({s['source']: s['sha256'] for s in h['skills']} | h['input_loads']).items()],
                'artifacts': [], 'tests': []}
            self.rows.append(row)
        self.builder, self.candidate, *self.axes = self.rows
        simplification = merge.simplification
        root = Path(self.builder['runtime']['artifacts'])
        self.seal(self.builder)
        preceding = simplification.preceding(self.builder, root, manifest(root / 'workspace'),
            manifest(root / 'inputs/baseline'), json.loads((root / 'controller-checks.json').read_text()))
        h = self.candidate['handoff']
        h['preceding'] = preceding
        digest = self.candidate['runtime']['candidate_sha256']
        report = {'contract': simplification.CONTRACT, 'adaptation': h['adaptation'],
            'input_candidate_sha256': digest, 'candidate_sha256': digest, 'outcome': 'no-op',
            'behavior_preservation': 'preserved', 'angles': {a: 'LOCAL declared assessment' for a in simplification.ANGLES}, 'findings': []}
        h['simplification'] = {'implementation_assignment': self.builder['assignment_id'],
            'input_candidate_sha256': digest, 'verification_commands': ['python -m unittest local_greeting'],
            'scope': ['hello.py'], 'result_fields': list(report)}
        self.candidate['submitted_result']['artifacts'] = [simplification.artifact('simplification-result', json.dumps(report))]
        self.seal(self.candidate)
        self.candidate['simplification_result'] = simplification.validate_result(self.candidate, self.candidate['submitted_result'])
        root = Path(self.candidate['runtime']['artifacts'])
        self.pins = merge.review.pins(self.candidate, manifest(root / 'workspace'), manifest(root / 'inputs/baseline'))
        for axis in self.axes:
            h = axis['handoff']
            h['preceding'] = merge.review.preceding(self.candidate, root, manifest(root / 'workspace'),
                manifest(root / 'inputs/baseline'), json.loads((root / 'controller-checks.json').read_text()))
            report = {'contract': merge.review.CONTRACT, 'axis': merge.review.AXES[h['stage']],
                'adaptation': h['adaptation'], 'pins': copy.deepcopy(self.pins), 'verdict': 'pass', 'findings': [],
                'test_assessment': 'LOCAL declared assessment, NOT native reviewer work', 'stuckness': None}
            h['review'] = {'pins': copy.deepcopy(self.pins), 'axis': report['axis'], 'policy_paths': [],
                'verification_commands': ['python -m unittest local_greeting'], 'result_fields': list(report),
                'verdicts': ['pass', 'reject', 'stuck']}
            axis['submitted_result']['artifacts'] = [simplification.artifact('review-result', json.dumps(report))]
            self.seal(axis)
            axis['review_result'] = merge.review.validate_result(axis, axis['submitted_result'])
        saved = {'operator_id': '42', 'execution_reference': 'LOCAL declared approved prerequisites, NOT attestation',
            'axes': [merge.review.execution_binding(a) for a in self.axes], 'policy_decision': None}
        for axis in self.axes:
            axis['review_authorization'] = copy.deepcopy(saved)
        for row in self.rows:
            self.store(row)
        self.config = {'candidate_assignment': self.candidate['assignment_id'],
            'standards_assignment': self.axes[0]['assignment_id'], 'spec_assignment': self.axes[1]['assignment_id'],
            'repository': 'example/product', 'repository_id': 123, 'issue': 10, 'baseline': 'a' * 40,
            'executions': [merge.execution_binding(a) for a in self.rows]}
        self.config_path = self.root / 'controller-merge.json'
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)
        # Adverse-candidate holds have their own component tests. Do not let that
        # earlier scan mask the immutable chain-pins comparison being tested here.
        self.enterContext(patch.object(merge.review, 'adverse_reviews', return_value=[]))

    def seal(self, row):
        row['handoff_digest'] = assignments.digest(row['handoff'])
        row['submitted_result']['handoff_digest'] = row['handoff_digest']

    def store(self, row):
        self.db.execute('INSERT OR REPLACE INTO role_assignments VALUES (?,?)', (row['assignment_id'], json.dumps(row)))
        self.db.commit()

    def admit(self):
        return merge.chain(self.db, 'product', 'm1', '42', self.config, self.config_path)

    def reseal_axis_authority(self, axes):
        """Isolate semantic axis guards, not the frozen-authorization regressions."""
        saved = copy.deepcopy(self.axes[0]['review_authorization'])
        saved['axes'] = [merge.review.execution_binding(a) for a in axes]
        for axis in axes:
            axis['review_authorization'] = copy.deepcopy(saved)
            self.store(axis)
        self.config['executions'] = [merge.execution_binding(a) for a in [self.builder, self.candidate, *axes]]

    def assert_refused(self, code):
        before = list(self.db.iterdump())
        with self.assertRaises(RepositoryError) as refusal:
            self.admit()
        self.assertEqual(refusal.exception.code, code)
        self.assertEqual(list(self.db.iterdump()), before)

    def test_valid_chain_reaches_both_actual_approved_binding_comparisons(self):
        with patch.object(merge, 'require', wraps=merge.require) as guards:
            prior, builder, root = self.admit()
        self.assertEqual(prior, self.candidate)
        self.assertEqual(builder, self.builder)
        self.assertEqual(root, Path(self.candidate['runtime']['artifacts']))
        messages = {call.args[1]: call.args[0] for call in guards.call_args_list}
        self.assertIs(messages['Independent review execution authorization missing or stale.'], True)
        self.assertIs(messages['Host-resolved implement, simplify-code and both native review executions required.'], True)

    def test_forged_execution_declarations_never_qualify(self):
        self.config['executions'] = [{'role': 'implementation', 'status': 'done'}] * 4
        self.assert_refused('execution_required')

    def test_each_host_execution_binding_field_is_frozen(self):
        approved = copy.deepcopy(self.config['executions'])
        for index, execution in enumerate(approved):
            for field in execution:
                with self.subTest(stage=self.rows[index]['handoff']['stage'], field=field):
                    self.config['executions'] = copy.deepcopy(approved)
                    self.config['executions'][index][field] = {'forged': 'LOCAL changed approved field'}
                    self.assert_refused('execution_required')
        self.config['executions'] = approved

    def test_each_assignment_candidate_check_and_scope_binding_is_frozen(self):
        approved = copy.deepcopy(self.config['executions'])
        for index, execution in enumerate(approved):
            for field in execution['binding']:
                with self.subTest(stage=self.rows[index]['handoff']['stage'], field=field):
                    self.config['executions'] = copy.deepcopy(approved)
                    self.config['executions'][index]['binding'][field] = 'LOCAL forged binding'
                    self.assert_refused('execution_required')
            for field in ('run_id', 'candidate_sha256', 'baseline_sha256', 'checks_sha256'):
                with self.subTest(stage=self.rows[index]['handoff']['stage'], runtime=field):
                    self.config['executions'] = copy.deepcopy(approved)
                    self.config['executions'][index]['binding']['execution'][field] = 'LOCAL forged runtime pin'
                    self.assert_refused('execution_required')
        self.config['executions'] = approved

    def test_both_review_authorizations_freeze_source_response_loads_and_work(self):
        approved = copy.deepcopy(self.axes[0]['review_authorization'])
        for index, binding in enumerate(approved['axes']):
            for field in binding:
                with self.subTest(axis=index, field=field):
                    forged = copy.deepcopy(approved)
                    forged['axes'][index][field] = {'forged': 'LOCAL changed approved reviewer field'}
                    for axis in self.axes:
                        axis['review_authorization'] = forged
                        self.store(axis)
                    self.assert_refused('execution_required')
        for axis in self.axes:
            axis['review_authorization'] = copy.deepcopy(approved)
            self.store(axis)

    def test_changed_reviewer_load_references_and_work_cannot_reuse_approval(self):
        for original in self.axes:
            for field in ('loads', 'work'):
                with self.subTest(axis=original['handoff']['stage'], field=field):
                    axis = copy.deepcopy(original)
                    if field == 'loads':
                        axis['submitted_result']['loads'][0]['tool_reference'] = 'LOCAL changed load reference'
                    else:
                        axis['submitted_result']['work'] = [{'reference': 'LOCAL changed work'}]
                    self.store(axis)
                    approved = copy.deepcopy(self.config['executions'])
                    rows = [axis if row['assignment_id'] == axis['assignment_id'] else row for row in self.rows]
                    self.config['executions'] = [merge.execution_binding(row) for row in rows]
                    try:
                        self.assert_refused('execution_required')
                    finally:
                        self.config['executions'] = approved
                        self.store(original)

    def test_changed_builder_and_other_stage_transcripts_or_responses_are_rejected(self):
        for row in self.rows:
            for relative in ('scratch/conversation.json', 'model-evidence/response-local.json'):
                path = Path(row['runtime']['artifacts']) / relative
                raw = path.read_bytes()
                with self.subTest(stage=row['handoff']['stage'], artifact=relative):
                    approved = copy.deepcopy(self.config['executions'])
                    path.write_bytes(raw + b'\n')
                    if row in self.axes:
                        self.config['executions'] = [merge.execution_binding(a) for a in self.rows]
                    try:
                        self.assert_refused('execution_required')
                    finally:
                        self.config['executions'] = approved
                        path.write_bytes(raw)

    def test_reused_review_axis_cannot_supply_two_independent_passes(self):
        self.config['spec_assignment'] = self.config['standards_assignment']
        self.reseal_axis_authority([self.axes[0], self.axes[0]])
        self.assert_refused('stale_review')

    def test_reused_profiles_or_artifacts_cannot_supply_fresh_axes(self):
        original = copy.deepcopy(self.axes)
        for field in ('name', 'home', 'artifacts'):
            with self.subTest(field=field):
                standards, spec = copy.deepcopy(original)
                if field == 'artifacts':
                    spec['runtime'] = copy.deepcopy(standards['runtime'])
                    spec['submitted_result']['run_id'] = spec['runtime']['run_id']
                else:
                    spec['handoff']['profile'][field] = standards['handoff']['profile'][field]
                    self.seal(spec)
                self.reseal_axis_authority([standards, spec])
                self.assert_refused('stale_review')
        for axis in original:
            self.store(axis)

    def test_changed_selected_source_bytes_do_not_qualify(self):
        for skill in self.selected.values():
            path = Path(skill['path'])
            raw = path.read_bytes()
            with self.subTest(source=skill['source']):
                path.write_bytes(raw + b'\nchanged')
                try:
                    self.assert_refused('skill_blocked')
                finally:
                    path.write_bytes(raw)

    def test_each_review_pin_must_equal_the_immutable_candidate(self):
        for index, original in enumerate(self.axes):
            for field in self.pins:
                with self.subTest(axis=original['handoff']['stage'], pin=field):
                    axes = copy.deepcopy(self.axes)
                    axis = axes[index]
                    root = Path(axis['runtime']['artifacts'])
                    changed_path = None
                    raw_source = b''
                    raw_receipt = (root / 'controller-checks.json').read_bytes()
                    if field in ('tree_sha256', 'baseline_sha256'):
                        folder = 'workspace' if field == 'tree_sha256' else 'inputs/baseline'
                        changed_path = root / folder / 'hello.py'
                        raw_source = changed_path.read_bytes()
                        changed_path.write_bytes(raw_source + b'\n')
                        changed_manifest = manifest(root / folder)
                        pin = assignments.digest(changed_manifest)
                        runtime_field = 'candidate_sha256' if field == 'tree_sha256' else 'baseline_sha256'
                        axis['runtime'][runtime_field] = pin
                        if field == 'tree_sha256':
                            receipt = json.loads(raw_receipt)
                            receipt['candidate'] = changed_manifest
                            (root / 'controller-checks.json').write_text(json.dumps(receipt))
                            axis['runtime']['checks_sha256'] = hashlib.sha256((root / 'controller-checks.json').read_bytes()).hexdigest()
                    else:
                        pin = '0' * len(str(self.pins[field]))
                    axis['handoff']['review']['pins'][field] = pin
                    artifact = axis['submitted_result']['artifacts'][0]
                    report = json.loads(artifact['content'])
                    report['pins'] = copy.deepcopy(axis['handoff']['review']['pins'])
                    axis['submitted_result']['artifacts'][0] = merge.simplification.artifact('review-result', json.dumps(report))
                    self.seal(axis)
                    try:
                        axis['review_result'] = merge.review.validate_result(axis, axis['submitted_result'])
                        self.reseal_axis_authority(axes)
                        self.assert_refused('stale_review')
                    finally:
                        if changed_path is not None:
                            changed_path.write_bytes(raw_source)
                        (root / 'controller-checks.json').write_bytes(raw_receipt)
                        for restored in self.axes:
                            self.store(restored)

    def test_rejected_spec_with_valid_resealed_authority_cannot_pass_real_chain(self):
        self.admit()  # Identical bytes, checks and independent bindings qualify first.
        axes = copy.deepcopy(self.axes)
        spec = axes[1]
        report = json.loads(spec['submitted_result']['artifacts'][0]['content'])
        report.update(verdict='reject', findings=[{'id': 'requirement-gap', 'kind': 'missing',
            'path': 'hello.py', 'requirement': 'Greeting must meet the pinned work issue',
            'evidence': 'LOCAL semantic rejection despite the declared passing checks',
            'correction': 'Correct the greeting and obtain both fresh axes'}])
        spec['submitted_result']['artifacts'] = [merge.simplification.artifact('review-result', json.dumps(report))]
        self.seal(spec)
        spec['review_result'] = merge.review.validate_result(spec, spec['submitted_result'])
        self.reseal_axis_authority(axes)
        before = list(self.db.iterdump())
        with self.assertRaises(RepositoryError) as refusal:
            self.admit()
        self.assertEqual(refusal.exception.code, 'stale_review')
        self.assertEqual(str(refusal.exception), 'Exact candidate requires both independent passes.')
        self.assertEqual(list(self.db.iterdump()), before)
        self.assertEqual(spec['review_result']['verdict'], 'reject')
        self.assertTrue(spec['result_disposition']['checks_verified'])

    def test_private_artifact_envelope_and_oversized_model_evidence_fail_real_chain(self):
        self.admit()
        root = Path(self.builder['runtime']['artifacts'])
        root.chmod(0o755)
        try:
            self.assert_refused('execution_required')
        finally:
            root.chmod(0o700)
        response = root / 'model-evidence/response-local.json'
        raw = response.read_bytes()
        with response.open('wb') as stream:
            stream.truncate(16_000_001)
        try:
            self.assert_refused('execution_required')
        finally:
            response.write_bytes(raw)


class MappedCandidateTests(unittest.TestCase):
    """Real Git object assembly from local byte/mode pins; metadata IO only is mocked."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / 'workspace'
        self.baseline = self.root / 'inputs/baseline'
        for folder in (self.source, self.baseline):
            folder.mkdir(parents=True)
            (folder / 'hello.py').write_bytes(b'print("baseline")\n')
            (folder / 'AGENTS.md').write_bytes(b'Unchanged operator policy\n')
            for path in folder.iterdir():
                path.chmod(0o644)
        (self.source / 'hello.py').write_bytes(b'print("candidate")\n')
        self.assignment = {'assignment_id': 'a' * 64, 'claim_id': 'local-mapping', 'handoff_digest': 'd' * 64,
            'revision': {'spec': {'commit': 'c' * 40}},
            'ticket_scope': {'project': 'product', 'iteration': 'm1', 'issue_number': 10},
            'handoff': {'stage': 'simplify', 'profile': {'name': 'local-mapping'}, 'spec_commit': 'c' * 40,
                        'baseline': 'a' * 40, 'candidate': 'b' * 40, 'issue': {'number': 10}}}
        self.config = {'repository': 'example/product', 'repository_id': 123, 'base': 'main',
            'branch': 'factory/10', 'issue': 10, 'pr_number': 20, 'head': 'b' * 40, 'baseline': 'a' * 40,
            'api_base': 'https://api.github.com', 'bearer': 'LOCAL metadata-only nonsecret'}
        self.approval = {k: self.config[k] for k in ('repository', 'repository_id', 'base', 'branch', 'issue', 'api_base')}
        self.approval.update(binding=publication.binding(self.assignment), expected_head=self.config['baseline'],
                             paths=['hello.py'], policy_paths=[], commit_date='2026-01-01T00:00:00Z')
        self.reseal_source()
        baseline = manifest(self.baseline)
        (self.root / 'initial-source.json').write_text(json.dumps({'baseline': baseline}))
        self.entries = [{'path': path, 'type': 'blob', 'mode': '100644',
                         'sha': publication.object_sha('blob', (self.baseline / path).read_bytes())} for path in baseline]
        self.base_tree = publication.tree_sha(self.entries)
        self.commits = {self.config['baseline']: {'sha': self.config['baseline'], 'tree': {'sha': self.base_tree}}}
        self.calls = []
        self.api = merge.MergeGitHub(self.config)
        self.api.github.request = Mock(side_effect=self.external)
        self.plan, _ = publication.assemble(self.api, self.approval, self.assignment, self.root)
        self.config['head'] = self.plan['candidate']
        self.commits[self.config['head']] = {'sha': self.config['head'], 'tree': {'sha': self.plan['tree']},
            'parents': [{'sha': self.config['baseline']}], 'message': self.plan['message']}
        self.assignment['publication'] = {'status': 'published', 'pr_number': 20,
                                         'candidate': self.config['head'], 'approval': self.approval}
        self.calls.clear()

    def reseal_source(self):
        self.approval['manifest'] = manifest(self.source)
        (self.root / 'source-artifacts.json').write_text(json.dumps(self.approval['manifest']))

    def external(self, path, body=None, method=None):
        self.assertIsNone(body)
        self.assertIn(method, (None, 'GET'))
        self.calls.append(path)
        suffix = path.removeprefix('/repos/example/product')
        if suffix.startswith('/git/commits/'):
            return copy.deepcopy(self.commits[suffix.split('/')[-1]])
        if suffix == '/git/trees/' + self.base_tree + '?recursive=1':
            return {'sha': self.base_tree, 'tree': copy.deepcopy(self.entries), 'truncated': False}
        self.fail('Unexpected mapping read: ' + suffix)

    def mapped(self):
        return merge.mapped_candidate(self.api, self.config, self.assignment, self.root)

    def test_reviewed_bytes_and_modes_cannot_map_to_the_old_git_head(self):
        self.assertEqual(self.mapped(), self.plan)
        file = self.source / 'hello.py'
        original = file.read_bytes()
        for mode in ('bytes', 'executable'):
            with self.subTest(drift=mode):
                file.write_bytes(original)
                file.chmod(0o644)
                self.reseal_source()
                if mode == 'bytes':
                    file.write_bytes(original + b'# changed after review\n')
                else:
                    file.chmod(0o755)
                with self.assertRaises(RepositoryError) as refusal:
                    self.mapped()
                self.assertEqual(refusal.exception.code, 'manifest_mismatch')
                # Reseal manifest comparisons to reach actual content/mode -> Git HEAD equality.
                self.reseal_source()
                changed, _ = publication.assemble(self.api, self.approval, self.assignment, self.root)
                self.commits[changed['candidate']] = {'sha': changed['candidate'], 'tree': {'sha': changed['tree']},
                    'parents': [{'sha': self.config['baseline']}], 'message': changed['message']}
                self.assertNotEqual(changed['tree'], self.plan['tree'])
                self.assertNotEqual(changed['candidate'], self.config['head'])
                with self.assertRaises(RepositoryError) as refusal:
                    self.mapped()
                self.assertEqual(refusal.exception.code, 'stale_head')
                self.assertEqual(str(refusal.exception), 'Reviewed byte/mode tree differs from Git head.')

    def test_unchanged_policy_is_preserved_and_changed_policy_needs_explicit_authority(self):
        mapped = self.mapped()
        policy = next(e for e in self.entries if e['path'] == 'AGENTS.md')
        expected = publication.tree_sha([policy, {'path': 'hello.py', 'type': 'blob', 'mode': '100644',
            'sha': publication.object_sha('blob', (self.source / 'hello.py').read_bytes())}])
        self.assertEqual(mapped['tree'], expected)
        self.assertEqual([e['path'] for e in mapped['changes']], ['hello.py'])
        self.assertEqual(manifest(self.source)['AGENTS.md'], manifest(self.baseline)['AGENTS.md'])
        file = self.source / 'AGENTS.md'
        file.write_bytes(file.read_bytes() + b'Unapproved policy change\n')
        self.reseal_source()
        self.approval['paths'] = ['hello.py', 'AGENTS.md']
        with self.assertRaises(RepositoryError) as refusal:
            publication.assemble(self.api, self.approval, self.assignment, self.root)
        self.assertEqual(refusal.exception.code, 'policy_held')
        with self.assertRaises(RepositoryError) as refusal:
            self.mapped()
        self.assertEqual(refusal.exception.code, 'policy_held')

    def test_exact_published_git_commit_tree_and_parent_cannot_be_replaced(self):
        original = copy.deepcopy(self.commits[self.config['head']])
        for field, value in (('tree', {'sha': '0' * 40}), ('parents', [{'sha': '0' * 40}]), ('message', 'unreviewed')):
            with self.subTest(field=field):
                self.commits[self.config['head']] = {**original, field: value}
                with self.assertRaises(RepositoryError) as refusal:
                    self.mapped()
                self.assertEqual(refusal.exception.code, 'github_mismatch')
                self.assertEqual(str(refusal.exception), 'Exact commit readback failed.')


if __name__ == "__main__":
    unittest.main()
