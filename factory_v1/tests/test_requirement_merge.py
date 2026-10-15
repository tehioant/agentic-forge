"""Revision-sensitive review/merge admission seams, not a merge-controller journey."""
import copy
import hashlib
import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from factory_v1 import assignments, delivery, merge, repositories, requirements, review, role_skills, sandbox, sandbox_source, simplification, tickets
from factory_v1.tests import test_simplification, test_tickets


def record_change(case, fixture, tracker):
    """Compatibility helper: invoke only the real scoped revision function."""
    item = fixture.stored()
    return requirements.record_change(fixture.database, 'product', 'm1', '42', {
        'change_id': 'changed-requirement', 'kind': 'material', 'direction': {
            'operator_id': '42', 'origin': item['origin'], 'reference': 'fixture-direction', 'content': 'Durably retain notes.'},
        'requirements': ['US-1'], 'issues': [10], 'old_revision': {'10': requirements.authority(item, 10)}}, fixture.github)


def candidate_record(fixture):
    handoff = {'stage': 'simplify', 'skills': [], 'issue': requirements.issue_pin(fixture.issues[10])}
    return {'assignment_id': 'candidate', 'ticket_scope': {'project': 'product', 'iteration': 'm1', 'issue_number': 10},
            'revision': requirements.authority(fixture.item, 10), 'handoff': handoff, 'handoff_digest': assignments.digest(handoff),
            'assignment_ready': True, 'result_disposition': {'requirements_valid': True}}


class RequirementReviewTests(unittest.TestCase):
    def test_material_revision_before_review_refuses_old_candidate_on_durable_reload(self):
        fixture = test_tickets.FrontierFixture(self); old = candidate_record(fixture)
        fixture.database.execute('INSERT INTO role_assignments VALUES (?,?)', (old['assignment_id'], json.dumps(old))); fixture.database.commit()
        fixture.item['requirement_changes'] = {'change': {'kind': 'material', 'status': 'held', 'affected': [10]}}; fixture.persist()
        before = fixture.stored()
        with self.assertRaises(repositories.RepositoryError) as caught: review.candidate(fixture.database, 'product', 'm1', '42', 'candidate')
        self.assertEqual(caught.exception.code, 'requirements_held'); self.assertEqual(fixture.stored(), before)
        fixture.github.request.assert_not_called(); self.assertEqual(assignments.rows(fixture.database), [old])


class RequirementMergeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_tickets.FrontierFixture(self)
        self.database, self.item, self.github = self.fixture.database, self.fixture.item, self.fixture.github
        self.item['handoff']['commit'] = 'a' * 40; self.fixture.persist()
        self.request = {'stage': 'implementation', 'repository': 'example/product', 'repository_id': 123,
                        'workspace': str(self.fixture.root / 'workspace'), 'issue': requirements.issue_pin(self.fixture.issues[10]),
                        'spec_commit': 'a' * 40, 'baseline': 'b' * 40, 'candidate': None, 'standards': [], 'preceding': [], 'skills': [],
                        'capabilities': ['read_workspace'], 'result_contract': assignments.CONTRACT, 'claim_id': 'fresh',
                        'profile': {'name': 'fixture', 'home': str(self.fixture.root / 'home'), 'provider': 'fixture', 'model': 'fixture', 'operation': 'chat'}}
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(assignments, 'validate', return_value=[]))
        stack.enter_context(patch.object(assignments, 'admission_preview', return_value={'scope': 'fixture'}))
        self.api = Mock(github=self.github)
        classic = {'required_status_checks': {'strict': True, 'checks': [{'context': 'unit', 'app_id': 1}]},
                   'enforce_admins': {'enabled': True}, 'allow_force_pushes': {'enabled': False}, 'allow_deletions': {'enabled': False}}
        self.classic = classic
        self.config = {'base': 'main', 'required_checks': classic['required_status_checks']['checks'],
                       'protection_sha256': assignments.digest({'rules': [], 'protection': classic})}
        self.api.read.side_effect = lambda path: [] if path == '/rules/branches/main' else copy.deepcopy(classic)

    def test_material_revision_after_approval_refuses_result_and_merge_inspection_retains_history(self):
        old = candidate_record(self.fixture); old['requirement_disposition'] = {'change_id': 'change', 'status': 'obsolete'}
        old['assignment_ready'] = False; old['result_disposition']['requirements_valid'] = False
        old['merge_refusal'] = {'code': 'requirements_held'}
        self.database.execute('INSERT INTO role_assignments VALUES (?,?)', ('candidate', json.dumps(old))); self.database.commit()
        for _ in range(2):
            with self.assertRaises(repositories.RepositoryError) as caught: assignments.require_result_active(assignments.rows(self.database)[0])
            self.assertEqual(caught.exception.code, 'requirements_held')
            readback = merge.inspect_merge(self.database, 'product', 'm1', '42', 'candidate')
            self.assertEqual(readback['requirement_disposition'], old['requirement_disposition']); self.assertEqual(readback['refusal']['code'], 'requirements_held')
        self.assertEqual(assignments.rows(self.database), [old]); self.api.request.assert_not_called()

    def test_independent_candidate_retains_exact_revision_evidence_when_other_issue_held(self):
        old = candidate_record(self.fixture)
        self.database.execute('INSERT INTO role_assignments VALUES (?,?)', ('candidate', json.dumps(old))); self.database.commit()
        self.item['requirement_changes'] = {'change': {'kind': 'material', 'status': 'held', 'affected': [12]}}; self.fixture.persist()
        self.assertEqual(assignments.inspect(self.database, 'product', 'm1', '42', 'candidate'), old)
        self.assertEqual(merge.inspect_merge(self.database, 'product', 'm1', '42', 'candidate')['current_revision'], old['revision'])
        self.assertEqual(assignments.rows(self.database), [old]); self.api.request.assert_not_called()

    def test_minor_prose_cannot_revalidate_changed_controller_check_bytes(self):
        root = self.fixture.root / 'artifacts'; (root / 'workspace').mkdir(parents=True); (root / 'inputs/baseline').mkdir(parents=True); (root / 'scratch').mkdir()
        (root / 'controller-checks.json').write_text('{"exit":0}\n'); result = {'fixture': 'result'}
        (root / 'scratch/result.json').write_text(json.dumps(result))
        old = candidate_record(self.fixture)
        old.update(runtime={'status': 'complete', 'artifacts': str(root), 'candidate_sha256': assignments.digest(sandbox_source.manifest(root / 'workspace')),
                            'baseline_sha256': assignments.digest(sandbox_source.manifest(root / 'inputs/baseline')), 'checks_sha256': hashlib.sha256((root / 'controller-checks.json').read_bytes()).hexdigest()},
                   submitted_result=result, result_disposition={'checks_verified': True})
        self.database.execute('INSERT INTO role_assignments VALUES (?,?)', ('candidate', json.dumps(old))); self.database.commit()
        with patch.object(sandbox, 'original', return_value={}):
            checked = requirements.validity(self.database, self.item, self.github, {10})
            self.assertEqual(checked[0]['checks'], ['issue', 'requirements', 'skills', 'candidate'])
            (root / 'controller-checks.json').write_text('{"exit":0}\n\n')
            with self.assertRaises(repositories.RepositoryError) as caught: requirements.validity(self.database, self.item, self.github, {10})
        self.assertEqual(caught.exception.code, 'stale_assignment'); self.assertEqual(assignments.rows(self.database), [old])

    def final_merge_fixture(self, fault=lambda: None):
        """Preadmitted input records; real final review -> prepare -> frontier.

        Byte writers below do not execute precursor preparation or validation.
        Tracker state is mutable, while pinned specification document bytes stay exact.
        """
        root = self.fixture.root
        evidence_root = root / 'candidate'; evidence_root.mkdir(mode=0o700)
        evidence = test_simplification.LocalStageEvidence(evidence_root)
        candidate = evidence.assignment
        candidate['handoff'].update(copy.deepcopy(self.request), stage='simplify', candidate='c' * 40,
                                    claim_id=candidate['claim_id'])
        candidate['revision'] = requirements.authority(self.item, 10)
        candidate['ticket_scope'] = {'project': 'product', 'iteration': 'm1', 'repository': 'example/product',
                                    'repository_id': 123, 'issue_number': 10, 'issue_id': self.request['issue']['id'],
                                    'workspace': self.request['workspace']}
        evidence.seal_handoff()
        candidate['submitted_result'] = evidence.result(evidence.report())
        candidate['simplification_result'] = {
            'contract': simplification.CONTRACT, 'outcome': 'no-op',
            'candidate_sha256': evidence.runtime['candidate_sha256'],
            'input_candidate_sha256': evidence.contract['input_candidate_sha256'], 'changed_paths': [],
            'checks_verified': True, 'behavior_preservation_claim': 'preserved',
            'native_execution_trusted': False, 'approval': False, 'advance_allowed': False,
            'review_required': ['Standards', 'Spec'], 'findings': [],
            'reason': 'native_execution_unattested_and_independent_review_required'}
        self.store(candidate)
        source, baseline = evidence.initial, evidence.baseline
        preceding = review.preceding(candidate, evidence_root, source, baseline, evidence.receipt)
        axes = []
        contracts = {}
        for stage in ('review-standards', 'review-spec'):
            request = {key: copy.deepcopy(candidate['handoff'][key]) for key in assignments.REQUEST_FIELDS}
            request.update(stage=stage, claim_id=stage, preceding=preceding,
                           profile={**self.request['profile'], 'name': stage, 'home': str(root / stage)})
            contract = {'contract': review.CONTRACT, 'axis': review.AXES[stage],
                        'pins': review.pins(candidate, source, baseline),
                        'verification_commands': ['python -m unittest discover -v'],
                        'policy_paths': [], 'test_changes': [],
                        'result_fields': ['contract', 'axis', 'adaptation', 'pins', 'verdict',
                                          'findings', 'test_assessment', 'stuckness'],
                        'finding_fields': sorted(review.FINDING_FIELDS), 'finding_kinds': sorted(review.KINDS),
                        'verdicts': ['pass', 'reject', 'stuck'], 'native_execution_trusted': False}
            contracts[stage] = contract
            input_loads = {self.fixture.issues[10]['html_url']: request['issue']['body_sha256'],
                           **{d['url']: d['sha256'] for d in self.fixture.inputs['documents'].values()},
                           **{'handoff:preceding:' + a['name']: a['sha256'] for a in preceding}}
            handoff = {**request, 'review': contract, 'adaptation': role_skills.ADAPTATIONS[stage],
                       'issue_body': self.fixture.issues[10]['body'], 'specification': self.fixture.inputs,
                       'stage_rules': role_skills.STAGE_RULES[stage],
                       'support_policy': {k: role_skills.SUPPORT_POLICY[k] for k in ('repository-inputs', 'required')},
                       'input_loads': input_loads, 'skill_entry_points': list(role_skills.STAGES[stage]),
                       'tracker': {'api_base': self.github.base, 'board': self.fixture.board,
                                   'issue_url': self.fixture.issues[10]['html_url']},
                       'fresh_context': True, 'inherited_memory': False, 'inherited_sessions': False,
                       'credentials': [], 'model_policy': {'scope': 'fixture', 'reservation': 1, 'recheck_at_call': True},
                       'result_requirements': {'identity': ['assignment_id', 'handoff_digest', 'claim_id', 'run_id'],
                           'required_evidence': ['actual-selected-instruction-loads', 'observable-skill-guided-work',
                                                 'artifacts', 'tests', 'trusted-whole-process-execution'],
                           'advancement': 'disabled-without-trusted-whole-process-evidence'}}
            axis = {'assignment_id': assignments.digest({'request': request, 'scope': candidate['ticket_scope']}), 'claim_id': stage,
                    'ticket_scope': copy.deepcopy(candidate['ticket_scope']), 'revision': copy.deepcopy(candidate['revision']),
                    'handoff': handoff, 'handoff_digest': assignments.digest(handoff), 'assignment_ready': True}
            axis_root = root / (stage + '-evidence'); axis_root.mkdir(mode=0o700)
            local = test_simplification.LocalStageEvidence(axis_root)
            local.runtime['run_id'] = stage + '-input'
            local.refresh_checks()
            axis['runtime'] = local.runtime
            report = {'contract': review.CONTRACT, 'axis': review.AXES[stage], 'pins': contract['pins'],
                      'adaptation': handoff['adaptation'], 'verdict': 'pass', 'findings': [],
                      'test_assessment': 'SIMULATED independent assessment', 'stuckness': None}
            result = local.result(report, 'review-result')
            result.update(assignment_id=axis['assignment_id'], claim_id=axis['claim_id'], handoff_digest=axis['handoff_digest'])
            axis.update(submitted_result=result, result_disposition={'checks_verified': True})
            axis['review_result'] = {**report, 'report_sha256': result['artifacts'][0]['sha256'],
                                     'native_execution_trusted': False, 'advance_allowed': False, 'close_allowed': False}
            # Hash inputs are labeled fixtures, not claims of native execution.
            for relative in ('scratch/loads.json', 'scratch/events.jsonl', 'scratch/conversation.json', 'scratch/probes.json',
                             'inputs/worker.py', 'inputs/sandbox_relay.py', 'inputs/assignment.json', 'inputs/handoff.json',
                             'container-inspection.json', 'launch-command.json', 'source-artifacts.json',
                             'model-evidence/response-local.json'):
                path = axis_root / relative; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('SIMULATED evidence input; no execution\n')
            (axis_root / 'scratch/result.json').write_text(json.dumps(result))
            self.store(axis); axes.append(axis)
        authorization = {'operator_id': '42', 'execution_reference': 'SIMULATED host verification input',
                         'axes': [review.execution_binding(axis) for axis in axes], 'policy_decision': None}
        for axis in axes:
            axis['review_authorization'] = authorization; self.store(axis)
        self.config.update(operator_id='42', execution_reference='SIMULATED merge authority',
                           executions=[{'fixture': n} for n in range(4)], candidate_assignment=candidate['assignment_id'],
                           standards_assignment=axes[0]['assignment_id'], spec_assignment=axes[1]['assignment_id'],
                           repository='example/product', repository_id=123, branch='factory/10', pr_number=20,
                           head='c' * 40, baseline='b' * 40, issue=10, api_base=self.github.base,
                           bearer='SIMULATED-NONSECRET', recovery=None)
        self.config_path = root / 'merge.json'; self.config_path.write_text(json.dumps(self.config)); self.config_path.chmod(0o600)
        self.api = merge.MergeGitHub(self.config)
        self.api.github = self.github
        self.protection_reads = 0; self.frontier_reads = []; self.fault_injected = False
        repo = {'id': 123, 'full_name': 'example/product', 'default_branch': 'main'}
        self.pr = {'number': 20, 'state': 'open', 'merged': False, 'draft': False, 'mergeable': True, 'mergeable_state': 'clean',
                   'base': {'ref': 'main', 'sha': self.config['baseline'], 'repo': repo},
                   'head': {'ref': 'factory/10', 'sha': self.config['head'], 'repo': repo}}
        tracker_request = self.fixture.request
        def external(path, body=None, method=None):
            suffix = path.removeprefix('/repos/example/product')
            if method == 'PUT':
                self.assertEqual(self.stored_candidate()['merge']['status'], 'pending')
                # Stop at the effect boundary: no fabricated merge or delivery journey.
                return {'merged': False}
            if suffix == '': return copy.deepcopy(repo)
            if suffix == '/issues/10': return copy.deepcopy(self.fixture.issues[10])
            if suffix == '/pulls/20': return copy.deepcopy(self.pr)
            if suffix == '/rules/branches/main': return []
            if suffix == '/branches/main/protection':
                self.protection_reads += 1
                if self.protection_reads == 3:
                    fault()
                    self.fault_injected = True
                return copy.deepcopy(self.classic)
            if suffix.startswith('/git/ref/heads/'):
                branch = suffix.removeprefix('/git/ref/heads/')
                return {'ref': 'refs/heads/' + branch, 'object': {'type': 'commit', 'sha': self.config['baseline' if branch == 'main' else 'head']}}
            if suffix.startswith('/commits/'):
                return {'total_count': 1, 'check_runs': [{'id': 1, 'name': 'unit', 'app': {'id': 1},
                        'head_sha': suffix.split('/')[2], 'status': 'completed', 'conclusion': 'success'}]}
            return tracker_request(path, body, method)
        self.github.request.side_effect = external
        self.enterContext(patch.object(merge, 'MergeGitHub', return_value=self.api))
        self.enterContext(patch.object(merge, 'chain', side_effect=lambda *a: (self.stored_candidate(), {}, evidence_root)))
        self.enterContext(patch.object(merge, 'mapped_candidate', return_value={'tree': 'd' * 40}))
        self.enterContext(patch.object(delivery, 'mutation_boundary', side_effect=lambda *a: assignments.current(*a)))
        # Only precursor contract metadata is supplied. Final pair validation,
        # approved execution-binding comparison and prepare/frontier stay real.
        self.enterContext(patch.object(review, 'handoff', side_effect=lambda _d, _p, _i, _o, request:
                                      copy.deepcopy(contracts[request['stage']])))
        # A transparent observation of the real external tracker read records timing;
        # evaluate/prepare/frontier themselves are never replaced.
        project_items = self.fixture.project_items
        def observe():
            self.frontier_reads.append(self.protection_reads)
            return project_items()
        self.enterContext(patch.object(tickets.gh, 'project_items', side_effect=lambda *a: observe()))
        self.candidate_id = candidate['assignment_id']
        self.github.request.reset_mock()

    def store(self, row):
        self.database.execute('DELETE FROM role_assignments WHERE assignment_id=?', (row['assignment_id'],))
        self.database.execute('INSERT INTO role_assignments VALUES (?,?)', (row['assignment_id'], json.dumps(row)))
        self.database.commit()

    def stored_candidate(self):
        return next(row for row in assignments.rows(self.database) if row['assignment_id'] == self.config['candidate_assignment'])

    def run_final_merge(self):
        return merge.lifecycle(self.database, 'product', 'm1', '42', self.config_path, 'merge-candidate')

    def assert_protection_read_drift_refused(self, fault, code):
        self.final_merge_fixture(fault)
        before = self.fixture.stored(); rows = assignments.rows(self.database)
        documents = copy.deepcopy(self.fixture.inputs['documents'])
        body = self.fixture.issues[10]['body']
        with self.assertRaises(repositories.RepositoryError) as caught: self.run_final_merge()
        stored = self.stored_candidate()
        self.assertFalse('merge' in stored, 'Final frontier refusal must not persist merge intent')
        self.assertEqual([call for call in self.github.request.call_args_list if call.args[1] is not None], [])
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(self.fault_injected)
        self.assertIn(0, self.frontier_reads)  # Initially valid at actual merge admission.
        if code != 'stale_admission':
            self.assertIn(3, self.frontier_reads)  # Re-read by the FINAL review checkpoint.
            self.assertEqual(self.fixture.issues[10]['body'], body)
        self.assertEqual(self.fixture.inputs['documents'], documents)
        self.assertEqual(self.fixture.stored(), before)
        self.assertEqual(stored.pop('merge_refusal')['code'], code)
        self.assertEqual([stored if row['assignment_id'] == self.candidate_id else row for row in assignments.rows(self.database)], rows)

    def test_unchanged_final_frontier_reaches_intent_and_single_put(self):
        self.final_merge_fixture()
        with self.assertRaises(repositories.RepositoryError) as caught: self.run_final_merge()
        self.assertEqual(caught.exception.code, 'merge_uncertain')
        self.assertTrue(self.fault_injected)
        self.assertIn(0, self.frontier_reads)
        self.assertIn(3, self.frontier_reads)
        self.assertEqual(self.stored_candidate()['merge']['status'], 'pending')
        puts = [call for call in self.github.request.call_args_list if call.args[2:] == ('PUT',)]
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0].args, ('/repos/example/product/pulls/20/merge', {'sha': self.config['head'], 'merge_method': 'merge'}, 'PUT'))

    def test_native_blocker_drift_at_protection_read_refuses_merge(self):
        self.assert_protection_read_drift_refused(lambda: self.fixture.deps.update({10: [12]}), 'github_mismatch')

    def test_completed_blocker_reopened_at_last_protection_read_refuses_merge(self):
        # Declare the blocker before pinning the candidate. Only its completion
        # changes late; no issue-10 byte drift can mask the frontier invariant.
        self.fixture.deps[10] = [12]
        self.fixture.issues[10]['body'] = self.fixture.issues[10]['body'].replace('None (can start immediately).', '- #12')
        self.request['issue'] = requirements.issue_pin(self.fixture.issues[10])
        self.fixture.issues[12].update(state='closed', state_reason='completed')
        self.fixture.members[12]['fields']['STATUS'] = 'Done'
        def fault():
            self.fixture.issues[12].update(state='open', state_reason=None)
            self.fixture.members[12]['fields']['STATUS'] = 'Ready'
        self.assert_protection_read_drift_refused(fault, 'ticket_ineligible')

    def test_board_scope_drift_at_protection_read_refuses_admission(self):
        self.assert_protection_read_drift_refused(lambda: self.fixture.members[10]['fields'].update(SCOPE='M2'), 'ticket_ineligible')

    def test_board_progress_drift_at_last_protection_read_refuses_admission(self):
        self.assert_protection_read_drift_refused(lambda: self.fixture.members[10]['fields'].update(STATUS='Blocked'), 'ticket_ineligible')

    def test_exact_ticket_body_drift_at_protection_read_refuses_admission(self):
        self.assert_protection_read_drift_refused(lambda: self.fixture.issues[10].update(body=self.fixture.issues[10]['body'] + '\nNew requirement.'), 'stale_admission')

    def test_last_publication_guard_rejects_remote_doc_drift_without_mutation(self):
        merge.require_publication(self.database, self.item, self.api, 10)
        before = self.fixture.stored()
        changed = copy.deepcopy(self.fixture.inputs); changed['documents']['milestone.md']['content'] += 'Remote edit'
        with patch.object(tickets, 'pinned_inputs', return_value=changed):
            with self.assertRaises(repositories.RepositoryError) as caught: merge.require_publication(self.database, self.item, self.api, 10)
        self.assertEqual(caught.exception.code, 'stale_requirements'); self.assertEqual(self.fixture.stored(), before)
        self.assertEqual(assignments.rows(self.database), []); self.api.request.assert_not_called()
