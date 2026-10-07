"""Public CLI, real loopback HTTP and fresh-process restart; ALL external evidence SIMULATED."""
import base64
import copy
import json
import sqlite3
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

    def test_merge_then_integrated_checks_close_and_restart_inspects_exact_evidence(self):
        merged = self.ok(self.cli())
        self.assertEqual(merged['status'], 'merged')
        self.assertEqual(merged['integrated_evidence']['integrated'], self.merge_sha)
        self.assertFalse(merged['close_allowed'])
        self.assertEqual(self.assignment.tracker.issues[10]['state'], 'open')
        self.pass_checks(self.merge_sha)
        delivered = self.ok(self.cli('verify-delivery'))
        self.assertEqual(delivered['status'], 'delivered')
        self.assertEqual({c['head_sha'] for c in delivered['delivery_checks']}, {self.merge_sha})
        self.assertEqual(self.assignment.tracker.issues[10]['state'], 'closed')
        self.assertFalse(delivered['iteration_complete'])
        self.assertFalse(delivered['deployment_verified'])
        writes = self.writes()
        self.assertEqual(self.ok(self.cli()), delivered)
        self.assertEqual(self.writes(), writes)
        inspected = self.assignment.ok(self.assignment.inspect(self.candidate))
        self.assertEqual(inspected['merge'], self.stored()['merge'])

    def test_pinned_spec_issue_changed_at_final_protection_read_never_merges(self):
        spec = self.review.seam.case.assignment.tracker.planning
        def change():
            spec.published[spec.issue_path]['body'] += '\nBoundary requirement change'
        self.boundary_change = change
        self.refused('publication_mismatch')
        self.assertEqual(self.writes('PUT'), [])
        self.assertNotIn('merge', self.stored())
        self.refused('publication_mismatch')

    def test_old_approved_pair_cannot_override_later_completed_rejection(self):
        later = self.review.pair('review-reject', '-later')
        feedback = self.assignment.ok(self.review.command('review-candidate', self.review.pair_request(later)))
        self.assertEqual(feedback['status'], 'corrections')
        for _ in range(2):
            self.refused('stale_review')
        self.assertEqual(self.writes('PUT'), [])
        self.assertFalse(self.stored()['merge_refusal'].get('close_allowed', False))

    def test_pinned_document_changed_at_final_protection_read_never_merges(self):
        spec = self.assignment.tracker.planning
        def change():
            path = next(p for p in spec.published if '/contents/' in p)
            spec.published[path]['content'] = base64.b64encode(b'Changed pinned bytes').decode()
        self.boundary_change = change
        self.refused('publication_mismatch')
        self.assertEqual(self.writes('PUT'), [])

    def test_lone_completed_rejecting_axis_blocks_old_approved_merge_without_aggregation(self):
        self.review.axis('review-spec', 'review-reject', '-lone')
        self.refused('stale_review')
        self.assertEqual(self.writes('PUT'), [])

    def test_merge_autoclosure_reopens_immediately_including_lost_responses(self):
        self.auto_close = True
        self.drop_merge = True
        self.drop_close = True
        result = self.ok(self.cli())
        self.assertEqual(result['status'], 'merged')
        self.assertFalse(result['close_allowed'])
        self.assertEqual(result['delivery_checks'], [])
        self.assertEqual(self.assignment.tracker.issues[10]['state'], 'open')
        self.assertEqual(len(self.writes('PUT')), 1)
        self.assertEqual(len(self.writes('PATCH')), 1)
        self.ok(self.cli())
        self.assertEqual(len(self.writes('PUT')), 1)
        self.assertEqual(len(self.writes('PATCH')), 1)

    def test_requirements_changed_during_merge_autoclosure_are_reopened_not_delivered(self):
        self.auto_close = True
        def change():
            self.assignment.tracker.issues[10]['body'] += '\nChanged during integration'
        self.after_merge = change
        self.refused('stale_requirements')
        self.assertEqual(self.assignment.tracker.issues[10]['state'], 'open')
        self.assertFalse(self.stored()['merge']['close_allowed'])
        self.assertEqual(self.stored()['merge']['status'], 'incomplete')

    def test_issue_identity_changed_during_merge_refuses_without_unsafe_state_write(self):
        self.auto_close = True
        def change():
            self.assignment.tracker.issues[10]['node_id'] = 'REPLACED'
        self.after_merge = change
        self.refused('scope_mismatch')
        self.assertEqual(self.writes('PATCH'), [])
        self.assertFalse(self.stored()['merge']['close_allowed'])
        self.assertEqual(self.stored()['merge']['failure']['code'], 'scope_mismatch')

    def test_branch_only_and_premature_manual_closure_are_reopened_not_delivered(self):
        self.assignment.tracker.issues[10]['state'] = 'closed'
        self.refused('not_integrated', 'verify-delivery')
        self.assertEqual(self.assignment.tracker.issues[10]['state'], 'open')
        self.assertEqual(self.writes('PUT'), [])
        self.assertNotIn('merge', self.stored())

    def test_branch_only_checks_after_merge_reopen_auto_closure_then_integrated_checks_deliver(self):
        self.ok(self.cli())
        self.assignment.tracker.issues[10]['state'] = 'closed'
        self.refused('checks_unknown', 'verify-delivery')
        self.assertEqual(self.assignment.tracker.issues[10]['state'], 'open')
        self.assertEqual(self.stored()['merge']['status'], 'incomplete')
        self.check_runs[self.merge_sha] = copy.deepcopy(self.check_runs[self.config['head']])
        self.refused('checks_stale', 'verify-delivery')
        self.pass_checks(self.merge_sha)
        self.assertEqual(self.ok(self.cli('verify-delivery'))['status'], 'delivered')
        self.assertEqual(len(self.writes('PUT')), 1)

    def test_concrete_protection_403_and_absent_protection_fail_closed(self):
        before = self.fixture.state.read_bytes()
        self.protection_http = 403
        self.refused('protection_required', 'merge-preflight')
        self.assertEqual(self.fixture.state.read_bytes(), before)
        self.refused('protection_required')
        self.assertEqual(self.stored()['merge_refusal']['code'], 'protection_required')
        self.assertEqual(self.writes(), [])
        self.protection_http = 404
        self.refused('protection_required')
        self.assertEqual(self.writes(), [])

    def test_stale_head_wrong_scope_stale_baseline_failed_missing_unknown_checks(self):
        head = self.config['head']
        self.refs[self.config['branch']] = 'd' * 40
        self.refused('stale_head')
        self.refs[self.config['branch']] = head
        self.repo['id'] = 456
        self.refused('repository_mismatch')
        self.repo['id'] = 123
        self.refs['main'] = 'd' * 40
        self.refused('stale_baseline')
        self.refs['main'] = self.base_sha
        self.check_runs[head][0]['conclusion'] = 'failure'
        self.refused('checks_failed')
        self.check_runs[head][0]['conclusion'] = None
        self.refused('checks_failed')
        self.pass_checks(head)
        self.check_runs[head][0]['app']['id'] = 88
        self.refused('checks_unknown')
        self.assertEqual(self.writes(), [])

    def test_changed_protection_weak_gates_and_unsupported_rules_never_admit(self):
        self.protection['required_status_checks']['strict'] = False
        self.refused('stale_protection')
        self.config['protection_sha256'] = assignments.digest({'rules': self.rules, 'protection': self.protection})
        self.write_config()
        self.refused('protection_required')
        self.protection['required_status_checks']['strict'] = True
        self.rules = [{'type': 'merge_queue'}]
        self.config['protection_sha256'] = assignments.digest({'rules': self.rules, 'protection': self.protection})
        self.write_config()
        self.refused('protection_required')
        self.assertEqual(self.writes(), [])

    def test_native_chain_axis_source_and_transcript_changes_do_not_qualify(self):
        original = copy.deepcopy(self.config)
        self.config['executions'] = [{'role': 'implementation', 'status': 'done'}] * 4
        self.write_config()
        self.refused('execution_required')
        self.config = copy.deepcopy(original)
        self.write_config()
        transcript = Path(self.builder['runtime']['artifacts']) / 'scratch/conversation.json'
        raw = transcript.read_bytes()
        transcript.write_bytes(raw + b'\n')
        self.refused('execution_required')
        transcript.write_bytes(raw)
        self.config['spec_assignment'] = self.config['standards_assignment']
        self.write_config()
        self.refused('stale_review')
        self.config = copy.deepcopy(original)
        self.write_config()
        skill = Path(self.builder['handoff']['skills'][0]['path'])
        skill.write_bytes(skill.read_bytes() + b'\nchanged')
        self.refused('skill_blocked')
        self.assertEqual(self.writes(), [])

    def test_pause_and_requirement_revisions_survive_restart_and_reopen_obsolete_closure(self):
        self.assignment.mutate_iteration(lambda i: i.update(status='paused'))
        self.refused('iteration_paused')
        self.refused('iteration_paused')
        self.assignment.mutate_iteration(lambda i: i.update(status='active'))
        self.assignment.tracker.issues[10]['body'] += '\nChanged requirements'
        self.refused('stale_requirements')
        self.assignment.tracker.issues[10]['state'] = 'closed'
        self.refused('stale_requirements', 'verify-delivery')
        self.assertEqual(self.assignment.tracker.issues[10]['state'], 'open')
        self.assertEqual(self.writes('PUT'), [])

    def test_ordinary_main_incident_cannot_use_caller_boolean_or_unbound_recovery(self):
        self.assignment.mutate_iteration(lambda i: i.update(main_incident={'status': 'active', 'incident_id': 'incident-1',
                                                                        'recovery_assignment': self.builder['assignment_id'], 'recovery_issue': 10}))
        self.refused('main_frozen')
        self.config['recovery'] = True
        self.write_config()
        self.refused('scope_mismatch')
        self.config['recovery'] = {'incident_id': 'incident-1', 'implementation_assignment': self.builder['assignment_id'], 'issue': 10}
        self.write_config()
        self.refused('main_frozen')
        self.assertEqual(self.writes(), [])

    def test_lost_merge_response_readback_and_uncertain_open_outcome_never_resend(self):
        self.drop_merge = True
        self.assertEqual(self.ok(self.cli())['status'], 'merged')
        self.ok(self.cli())
        self.assertEqual(len(self.writes('PUT')), 1)

    def test_open_uncertain_merge_stays_durable_even_after_pause_and_restart(self):
        self.omit_merge = True
        self.drop_merge = True
        self.refused('merge_uncertain')
        self.assertEqual(self.stored()['merge']['status'], 'pending')
        self.assignment.mutate_iteration(lambda i: i.update(status='paused'))
        self.refused('merge_uncertain')
        self.assertEqual(len(self.writes('PUT')), 1)
        self.assertEqual(self.assignment.tracker.issues[10]['state'], 'open')

    def test_concurrent_controllers_do_not_double_merge(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.cli(), range(2)))
        for result in results:
            self.assertEqual(self.ok(result)['status'], 'merged')
        self.assertEqual(len(self.writes('PUT')), 1)

    def test_integrated_tree_membership_and_failed_checks_hold_delivery(self):
        self.ok(self.cli())
        self.pass_checks(self.merge_sha)
        self.check_runs[self.merge_sha][0]['conclusion'] = 'failure'
        self.refused('checks_failed', 'verify-delivery')
        self.pass_checks(self.merge_sha)
        self.membership = 'diverged'
        self.refused('not_integrated', 'verify-delivery')
        self.membership = 'identical'
        self.commits[self.merge_sha]['tree']['sha'] = 'e' * 40
        self.refused('integration_mismatch', 'verify-delivery')
        self.assertEqual(self.assignment.tracker.issues[10]['state'], 'open')

    def test_reviewed_bytes_modes_and_policy_unchanged_mapping_required(self):
        file = self.source / 'hello.py'
        file.chmod(0o755)
        self.refused('implementation_unverified')
        self.assertEqual(self.writes(), [])

    def test_rejected_requirement_axis_cannot_merge_despite_green_tests(self):
        rejected = self.review.axis('review-spec', mode='review-reject', suffix='-rejected')
        axes = (self.axes[0], rejected)
        feedback = self.assignment.ok(self.review.command('review-candidate', self.review.pair_request(axes),
                                                          self.review.authorization(axes)))
        self.assertEqual(feedback['status'], 'corrections')
        self.config.update(spec_assignment=rejected['assignment_id'],
                           executions=[merge.execution_binding(a) for a in [self.builder, self.candidate, *axes]])
        self.write_config()
        self.refused('stale_review')
        self.assertEqual(self.writes(), [])

    def test_healthy_candidate_does_not_hide_failed_or_unknown_main_checks(self):
        self.check_runs[self.base_sha][0]['conclusion'] = 'failure'
        self.refused('main_frozen')
        self.check_runs[self.base_sha] = []
        self.refused('main_frozen')
        self.assertEqual(self.writes(), [])

    def test_exact_incident_repair_uses_same_checks_and_independent_reviews(self):
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
        recovery = {'incident_id': 'main-failure-1', 'implementation_assignment': builder['assignment_id'], 'issue': 10}
        self.assignment.mutate_iteration(lambda i: i.update(main_incident={'status': 'active', 'incident_id': recovery['incident_id'],
            'recovery_assignment': builder['assignment_id'], 'recovery_issue': 10}))
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

    def test_lost_issue_closure_response_reconciles_without_duplicate_merge_or_closure(self):
        self.ok(self.cli())
        self.pass_checks(self.merge_sha)
        self.drop_close = True
        self.assertEqual(self.ok(self.cli('verify-delivery'))['status'], 'delivered')
        self.assertEqual(self.assignment.tracker.issues[10]['state'], 'closed')
        self.drop_close = False
        self.assertEqual(self.ok(self.cli('verify-delivery'))['status'], 'delivered')
        self.assertEqual(len(self.writes('PUT')), 1)
        self.assertEqual(len(self.writes('PATCH')), 1)

    def test_material_spec_revision_and_obsolete_requirements_reopen_integrated_work(self):
        self.ok(self.cli())
        self.pass_checks(self.merge_sha)
        self.ok(self.cli('verify-delivery'))
        self.assignment.mutate_iteration(lambda i: i['handoff'].update(commit='f' * 40))
        self.refused('stale_assignment', 'verify-delivery')
        self.assertEqual(self.assignment.tracker.issues[10]['state'], 'open')
        self.assertEqual(self.stored()['merge']['status'], 'incomplete')
        inspected = self.ok(self.fixture.cli('inspect-merge', '--project', 'product', '--iteration', 'm1',
                                            '--assignment', self.candidate['assignment_id']))
        self.assertEqual(inspected['merge']['failure']['code'], 'stale_assignment')
        self.assertNotEqual(inspected['revision']['spec'], inspected['current_spec'])

    def test_private_config_and_controller_state_cannot_be_worker_mounts(self):
        self.config_path.chmod(0o644)
        self.refused('execution_required')
        self.config_path.chmod(0o600)
        mounted = self.artifacts / 'scratch/merge.json'
        mounted.write_bytes(self.config_path.read_bytes())
        mounted.chmod(0o600)
        self.config_path = mounted
        self.refused('execution_required')
        self.assertEqual(self.writes(), [])
