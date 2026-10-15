"""Direct engineering seams; legacy sibling fixture helpers are not discovered."""
import copy
from contextlib import contextmanager
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from factory_v1 import assignments, review, simplification
from factory_v1.repositories import GitHub, RepositoryError
from factory_v1.tests import test_assignments, test_simplification



@contextmanager
def component_case(cls):
    case = cls(methodName='runTest')
    try:
        case.setUp()
        yield case
    finally:
        case.doCleanups()


@contextmanager
def candidate_case():
    case = ReviewComponentTests(methodName='runTest')
    try:
        directory = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        case.addCleanup(directory.cleanup)
        case.root = Path(directory.name)
        case.database = sqlite3.connect(case.root / 'state.sqlite')
        case.addCleanup(case.database.close)
        case.database.executescript('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT);'
                                   'CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT);')
        case.database.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps({
            'project_id': 'product', 'iteration_id': 'm1', 'status': 'active', 'execution_allowed': False,
            'approval': {'operator_id': '42', 'reference': 'local review fixture'}})))
        root = case.root / 'candidate'
        root.mkdir(mode=0o700)
        case.e = test_simplification.LocalStageEvidence(root)
        case.candidate = case.e.assignment
        case.candidate['simplification_result'] = {
            'contract': simplification.CONTRACT, 'outcome': 'no-op',
            'candidate_sha256': case.e.runtime['candidate_sha256'],
            'input_candidate_sha256': case.e.contract['input_candidate_sha256'], 'changed_paths': [],
            'checks_verified': True, 'behavior_preservation_claim': 'preserved',
            'native_execution_trusted': False, 'approval': False, 'advance_allowed': False,
            'review_required': ['Standards', 'Spec'], 'findings': [],
            'reason': 'native_execution_unattested_and_independent_review_required'}
        case.candidate['submitted_result'] = case.e.result(case.e.report())
        case.candidate['handoff'].update(repository='example/product', repository_id=123,
            workspace=str(case.root / 'workspace'), standards=[], profile={'name': 'candidate', 'home': str(case.root / 'candidate-home')})
        case.e.seal_handoff()
        case.store(case.candidate)
        case.pins = review.pins(case.candidate, case.e.initial, case.e.baseline)
        yield case
    finally:
        case.doCleanups()

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.seam = test_simplification.SimplificationTests()
        self.seam.setUp()
        self.addCleanup(self.seam.doCleanups)
        self.case = self.seam.case
        self.assignment = self.case.assignment
        grant = self.assignment.fixture.cli('spend-grant', '--project', 'product', '--iteration', 'm1',
            '--provider', 'openai-codex', '--model', 'gpt-6.1-sol', '--operation', 'responses',
            '--kind', 'allowance', '--ceiling', '100', '--expires', str(int(time.time()) + 600),
            '--reference', 'Labeled bounded review-test fixture allowance, not new live spending')
        self.assignment.ok(grant)
        self.implemented = self.seam.implementation()
        prepared = self.assignment.ok(self.seam.prepare(self.seam.request(self.implemented)))
        self.candidate = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(self.candidate['runtime']['status'], 'complete', self.candidate)


    def command(self, name, request, config=None, dry=False):
        path = self.case.root / ('review-request-' + name + '.json')
        path.write_text(json.dumps(request))
        args = [name, '--project', 'product', '--iteration', 'm1', '--request', str(path),
                '--api-base', self.assignment.fixture.base]
        if config:
            args += ['--review-config', str(config)]
        if dry:
            args += ['--dry-run']
        return self.assignment.fixture.cli(*args)


    def request(self, stage, suffix='', candidate=None):
        config = self.assignment.configuration(stage=stage)
        config['profile'].update(name='axis-' + stage + suffix, home=str(self.case.root / ('axis-home-' + stage + suffix)))
        return {'candidate_assignment': (candidate or self.candidate)['assignment_id'], 'axis': stage,
                'profile': config['profile'], 'skills': config['skills'], 'claim_id': 'axis-' + stage + suffix}


    def axis(self, stage, mode='', suffix='', candidate=None):
        prepared = self.assignment.ok(self.command('prepare-review', self.request(stage, suffix, candidate)))
        self.case.mode(mode)
        result = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(result['runtime']['status'], 'complete', result)
        return result


    def pair(self, spec_mode='', suffix='', candidate=None):
        standards = self.axis('review-standards', suffix=suffix, candidate=candidate)
        spec = self.axis('review-spec', spec_mode, suffix, candidate)
        return standards, spec


    def pair_request(self, axes):
        return {'standards_assignment': axes[0]['assignment_id'], 'spec_assignment': axes[1]['assignment_id']}


    def authorization(self, axes, policy=None):
        path = self.case.root / 'private-review-authorization.json'
        config = {'operator_id': '42', 'execution_reference': 'LABELED simulated host verification, NOT live execution',
                  'axes': [review.execution_binding(axis) for axis in axes], 'policy_decision': policy}
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        return path


    def assert_launcher_refusals(self):
        for mode in ('review-write', 'bad-load'):
            with self.subTest(mode=mode):
                prepared = self.assignment.ok(self.command('prepare-review', self.request('review-spec', '-' + mode)))
                self.case.mode(mode)
                failed = self.assignment.ok(self.case.launch(prepared))
                self.assertEqual(failed['runtime']['status'], 'failed', failed)
                self.assertEqual(failed['runtime']['error'], 'invalid_result')
                observed = self.assignment.ok(self.assignment.inspect(prepared))
                self.assertNotIn('review_result', observed)
                self.assertTrue((Path(failed['runtime']['artifacts']) / 'scratch/result.json').exists())

class ReviewValidatorTests(unittest.TestCase):
    def test_exact_axis_pin_schema_findings_stuckness_and_readonly_evidence(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as directory:
            e = test_simplification.LocalStageEvidence(Path(directory))
            pins = {'tree_sha256': assignments.digest(e.initial), 'baseline_sha256': assignments.digest(e.baseline)}
            contract = {'pins': pins, 'axis': 'Spec', 'verification_commands': e.contract['verification_commands'],
                        'result_fields': ['contract', 'axis', 'adaptation', 'pins', 'verdict', 'findings', 'test_assessment', 'stuckness'],
                        'verdicts': ['pass', 'reject', 'stuck']}
            e.assignment['handoff']['review'] = contract
            report = {'contract': review.CONTRACT, 'axis': 'Spec', 'adaptation': e.assignment['handoff']['adaptation'],
                      'pins': pins, 'verdict': 'pass', 'findings': [], 'test_assessment': 'LABELED requirement-based test assessment',
                      'stuckness': None}
            def validate(value, status='done'):
                return review.validate_result(e.assignment, e.result(value, 'review-result', status))
            def reject(value, status='done'):
                with self.assertRaises(RepositoryError) as raised:
                    validate(value, status)
                self.assertEqual(raised.exception.code, 'invalid_result')
            accepted = validate(report)
            self.assertFalse(accepted['native_execution_trusted'])
            self.assertFalse(accepted['advance_allowed'])
            finding = {'id': 'R1', 'kind': 'missing', 'path': 'hello.py:1', 'requirement': 'Required greeting',
                       'evidence': 'LABELED omission', 'correction': 'Implement greeting'}
            for kind in review.KINDS:
                rejected = validate({**report, 'verdict': 'reject', 'findings': [{**finding, 'kind': kind}]})
                self.assertEqual(rejected['findings'][0]['kind'], kind)
            stuckness = {k: 'LABELED attempted investigation' for k in ('attempts', 'evidence', 'uncertainty', 'recommendation')}
            stuck = validate({**report, 'verdict': 'stuck', 'stuckness': stuckness}, 'stuck')
            self.assertEqual(stuck['stuckness'], stuckness)
            for change in ({'axis': 'combined'}, {'pins': {**pins, 'tree_sha256': '0' * 64}},
                           {'adaptation': 'unreviewed'}, {'findings': [finding]}, {'approval': True},
                           {'verdict': 'reject', 'findings': []}, {'verdict': 'reject', 'findings': [finding, finding]},
                           {'verdict': 'stuck', 'stuckness': {}}, {'test_assessment': ''}):
                with self.subTest(change=change):
                    reject({**report, **change})
            for raw in ('{not JSON', '{"verdict":"pass","verdict":"reject"}',
                        '{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}'):
                reject(raw)
            reject(report, 'stuck')
            (e.root / 'workspace/hello.py').write_text('forged read-only mutation')
            e.refresh_checks()  # Even internally consistent new checks cannot repin the axis.
            reject(report)
            (e.root / 'workspace/hello.py').write_text('candidate\n')
            e.refresh_checks()
            (e.root / 'inputs/baseline/hello.py').write_text('baseline drift')
            reject(report)


from unittest.mock import Mock, patch


class ReviewComponentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.database = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(self.database.close)
        self.database.executescript('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT);'
                                   'CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT);')
        self.database.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps({
            'project_id': 'product', 'iteration_id': 'm1', 'status': 'active', 'execution_allowed': False,
            'approval': {'operator_id': '42', 'reference': 'local review fixture'}})))
        root = self.root / 'candidate'
        root.mkdir(mode=0o700)
        self.e = test_simplification.LocalStageEvidence(root)
        self.candidate = self.e.assignment
        self.candidate['simplification_result'] = {
            'contract': simplification.CONTRACT, 'outcome': 'no-op',
            'candidate_sha256': self.e.runtime['candidate_sha256'],
            'input_candidate_sha256': self.e.contract['input_candidate_sha256'], 'changed_paths': [],
            'checks_verified': True, 'behavior_preservation_claim': 'preserved',
            'native_execution_trusted': False, 'approval': False, 'advance_allowed': False,
            'review_required': ['Standards', 'Spec'], 'findings': [],
            'reason': 'native_execution_unattested_and_independent_review_required'}
        self.candidate['submitted_result'] = self.e.result(self.e.report())
        self.candidate['handoff'].update(repository='example/product', repository_id=123,
            workspace=str(self.root / 'workspace'), standards=[], profile={'name': 'candidate', 'home': str(self.root / 'candidate-home')})
        self.e.seal_handoff()
        self.store(self.candidate)
        self.pins = review.pins(self.candidate, self.e.initial, self.e.baseline)
        self.axes = [self.axis('review-standards', '1'), self.axis('review-spec', '2')]
        self.request = {'standards_assignment': self.axes[0]['assignment_id'], 'spec_assignment': self.axes[1]['assignment_id']}
        self.github = Mock(base='https://api.github.com')
        # Already-admitted record inputs: tracker/profile/spending revalidation is outside
        # report, pairing, adverse-evidence and host-authorization invariants under test.
        self.preflight = self.enterContext(patch.object(assignments, 'prepare', return_value={'dry_run': True}))

    def store(self, row):
        self.database.execute('INSERT OR REPLACE INTO role_assignments VALUES (?,?)', (row['assignment_id'], json.dumps(row)))
        self.database.commit()

    def axis(self, stage, identity):
        root = self.root / ('axis-' + identity)
        root.mkdir(mode=0o700)
        e = test_simplification.LocalStageEvidence(root)
        contract = {'axis': review.AXES[stage], 'pins': copy.deepcopy(self.pins),
            'verification_commands': e.contract['verification_commands'], 'policy_paths': [], 'test_changes': [],
            'result_fields': ['contract', 'axis', 'adaptation', 'pins', 'verdict', 'findings', 'test_assessment', 'stuckness'],
            'verdicts': ['pass', 'reject', 'stuck']}
        handoff: dict[str, object] = {key: None for key in assignments.REQUEST_FIELDS}
        handoff.update(copy.deepcopy(self.candidate['handoff']))
        handoff.update(stage=stage, skills=[], review=contract,
            profile={'name': 'axis-' + identity, 'home': str(self.root / ('home-' + identity))},
            adaptation='LABELED local read-only adaptation', capabilities=['read_workspace', 'scratch', 'model'])
        row = {'assignment_id': identity * 64, 'claim_id': 'axis-' + identity, 'handoff': handoff,
            'handoff_digest': assignments.digest(handoff), 'runtime': e.runtime,
            'ticket_scope': copy.deepcopy(self.candidate['ticket_scope']), 'revision': copy.deepcopy(self.candidate['revision']),
            'result_disposition': {'checks_verified': True}}
        report = {'contract': review.CONTRACT, 'axis': contract['axis'], 'pins': contract['pins'],
            'adaptation': handoff['adaptation'], 'verdict': 'pass', 'findings': [],
            'test_assessment': 'LABELED independent requirement assessment', 'stuckness': None}
        result = e.result(report, 'review-result')
        result.update(assignment_id=row['assignment_id'], claim_id=row['claim_id'], handoff_digest=row['handoff_digest'])
        row['submitted_result'] = result
        row['review_result'] = self.admitted_review(row)
        for group, names in (('scratch', ['loads.json', 'events.jsonl', 'conversation.json', 'probes.json']),
                            ('inputs', ['worker.py', 'sandbox_relay.py', 'assignment.json', 'handoff.json']),
                            ('', ['container-inspection.json', 'launch-command.json', 'source-artifacts.json'])):
            for filename in names:
                path = root / group / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('LABELED hash input; no executed worker\n')
        (root / 'scratch/result.json').write_text(json.dumps(result))
        (root / 'model-evidence').mkdir()
        (root / 'model-evidence/response-local.json').write_text('LABELED hash input; no inference\n')
        self.store(row)
        return row

    def adverse(self, axis, verdict='reject', kind='missing'):
        report = json.loads(axis['submitted_result']['artifacts'][0]['content'])
        if verdict == 'stuck':
            report.update(verdict='stuck', stuckness={k: 'Local retained investigation' for k in ('attempts', 'evidence', 'uncertainty', 'recommendation')})
            axis['submitted_result']['status'] = 'stuck'
        else:
            report.update(verdict='reject', findings=[{'id': 'R1', 'kind': kind, 'path': 'hello.py',
                'requirement': 'Greeting required', 'evidence': 'Local omission', 'correction': 'Restore greeting'}])
        axis['submitted_result']['artifacts'] = [simplification.artifact('review-result', json.dumps(report))]
        axis['review_result'] = self.admitted_review(axis)
        (Path(axis['runtime']['artifacts']) / 'scratch/result.json').write_text(json.dumps(axis['submitted_result']))
        self.store(axis)
        with self.database:
            review.record_hold(self.database, axis)
        return axis

    def admitted_review(self, axis):
        artifact = axis['submitted_result']['artifacts'][0]
        return {**json.loads(artifact['content']), 'report_sha256': artifact['sha256'],
                'native_execution_trusted': False, 'advance_allowed': False, 'close_allowed': False}

    def authorization(self, decision=None):
        path = self.root / 'authorization.json'
        path.write_text(json.dumps({'operator_id': '42', 'execution_reference': 'LABELED local host authorization input',
            'axes': [review.execution_binding(a) for a in self.axes], 'policy_decision': decision}))
        path.chmod(0o600)
        return path

    def evaluate(self, config=None):
        return review.evaluate(self.database, 'product', 'm1', '42', self.request, self.github, config)

    def test_review_host_authority_cases(self):
        cases = [('absent', None), ('durable', None), ('public', 'approval_required'),
                 ('pin', 'execution_required'), ('mounted', 'approval_required'), ('saved-drift', 'execution_required')]
        for mode, code in cases:
            with self.subTest(mode=mode), component_case(ReviewComponentTests) as case:
                path = None if mode == 'absent' else case.authorization()
                if mode == 'public':
                    path.chmod(0o644)
                elif mode == 'pin':
                    data = json.loads(path.read_text())
                    data['axes'][0]['pins']['spec_commit'] = 'f' * 40
                    path.write_text(json.dumps(data))
                elif mode == 'mounted':
                    mounted = Path(case.axes[0]['runtime']['artifacts']) / 'scratch/authority.json'
                    mounted.write_bytes(path.read_bytes())
                    mounted.chmod(0o600)
                    path = mounted
                elif mode == 'saved-drift':
                    grant = json.loads(path.read_text())
                    for axis in case.axes:
                        axis['review_authorization'] = copy.deepcopy(grant)
                        case.store(axis)
                    (Path(case.axes[0]['runtime']['artifacts']) / 'scratch/conversation.json').write_text('drift')
                    path = None
                if code:
                    with self.assertRaises(RepositoryError) as refusal:
                        case.evaluate(path)
                    self.assertEqual(refusal.exception.code, code)
                else:
                    feedback = case.evaluate(path)
                    self.assertEqual(feedback['status'], 'execution-unverified' if mode == 'absent' else 'accepted')
                    if mode == 'durable':
                        self.assertTrue(feedback['advance_allowed'])
                        self.assertFalse(feedback['merge_allowed'])
                        self.assertFalse(feedback['close_allowed'])
                        self.assertEqual(case.evaluate(), feedback)
                        with closing(sqlite3.connect(case.root / 'state.sqlite')) as restarted:
                            self.assertEqual(assignments.inspect(restarted, 'product', 'm1', '42', case.axes[0]['assignment_id'])['review_authorization']['axes'],
                                [review.execution_binding(a) for a in case.axes])


    def test_lone_adverse_axis_holds_old_and_later_passing_pairs(self):
        self.evaluate(self.authorization())
        rejected = self.adverse(self.axis('review-spec', '3'))
        for _ in range(2):
            feedback = self.evaluate()
            self.assertEqual(feedback['status'], 'corrections')
            self.assertEqual(feedback['adverse_reviews'][0]['assignment_id'], rejected['assignment_id'])
            self.assertFalse(feedback['advance_allowed'])
        later = [self.axis('review-standards', '4'), self.axis('review-spec', '5')]
        self.axes = later
        self.request = {'standards_assignment': later[0]['assignment_id'], 'spec_assignment': later[1]['assignment_id']}
        self.assertEqual(self.evaluate(self.authorization())['status'], 'corrections')
        saved = assignments.inspect(self.database, 'product', 'm1', '42', self.candidate['assignment_id'])
        self.assertIn(rejected['assignment_id'], saved['review_holds'])
        self.database.execute('DELETE FROM role_assignments WHERE assignment_id=?', (rejected['assignment_id'],))
        self.database.commit()
        with self.assertRaises(RepositoryError) as refusal:
            self.evaluate()
        self.assertEqual(refusal.exception.code, 'stale_review')

    def test_all_completed_reject_stuck_policy_and_correction_debug_remain_distinct(self):
        self.adverse(self.axis('review-spec', '3'))
        self.adverse(self.axis('review-standards', '4'), 'stuck')
        self.assertEqual(self.evaluate()['status'], 'diagnosis')
        policy = self.adverse(self.axis('review-spec', '5'), kind='gate-weakening')
        feedback = self.evaluate()
        self.assertEqual(feedback['status'], 'operator-decision')
        self.assertEqual(len(feedback['adverse_reviews']), 3)
        self.assertEqual(feedback['policy_findings'], ['Spec:R1'])
        exact = {'reference': 'Local explicit decision', 'paths': [], 'findings': ['Spec:R1']}
        self.assertEqual(self.evaluate(self.authorization(exact))['status'], 'diagnosis')
        self.assertFalse(self.evaluate()['advance_allowed'])
        child = {'assignment_id': '6' * 64, 'ticket_scope': copy.deepcopy(self.candidate['ticket_scope']),
                 'handoff': {'corrections': {'pins': self.pins}},
                 'runtime': {'status': 'complete'}, 'submitted_result': {'status': 'stuck', 'work': ['Retained failed correction']}}
        self.store(child)
        feedback = self.evaluate()
        self.assertEqual(feedback['debug_evidence'][0]['result'], child['submitted_result'])

    def test_stuck_corrective_child_routes_diagnosis_and_refuses_fresh_corrections(self):
        self.adverse(self.axes[1])  # Ordinary rejecting pair; no stuck reviewer masks the child.
        feedback = self.evaluate()
        self.assertEqual(feedback['status'], 'corrections')
        self.assertFalse(feedback['advance_allowed'])
        request = {**self.request, 'profile': {'name': 'fresh-correction', 'home': str(self.root / 'fresh-home')},
                   'skills': [], 'claim_id': 'fresh-correction'}
        review.prepare_corrections(self.database, 'product', 'm1', '42', request, self.github, dry_run=True)
        self.assertEqual(self.preflight.call_args.args[4]['stage'], 'corrections')
        child = {'assignment_id': '6' * 64, 'ticket_scope': copy.deepcopy(self.candidate['ticket_scope']),
                 'handoff': {'corrections': {'pins': self.pins}},
                 'runtime': {'status': 'complete'},
                 'submitted_result': {'status': 'stuck', 'work': ['Retained failed correction']}}
        self.store(child)
        before = assignments.rows(self.database)
        feedback = self.evaluate()
        self.assertEqual(feedback['status'], 'diagnosis')
        self.assertFalse(feedback['advance_allowed'])
        self.assertEqual(feedback['debug_evidence'], [{'assignment_id': child['assignment_id'],
            'runtime': child['runtime'], 'result': child['submitted_result']}])
        self.preflight.reset_mock()
        with self.assertRaises(RepositoryError) as refusal:
            review.prepare_corrections(self.database, 'product', 'm1', '42', request, self.github)
        self.assertEqual(refusal.exception.code, 'corrections_held')
        self.assertTrue(all(call.args[4]['stage'] in review.AXES for call in self.preflight.call_args_list))
        self.assertEqual(assignments.rows(self.database), before)
        self.assertEqual(self.evaluate()['status'], 'diagnosis')

    def test_prepare_rejects_malformed_requests_before_candidate_or_assignment_admission(self):
        # Complete the minimal record for the wrapper's bounded-request assembly.
        self.candidate['handoff'].update(claim_id=self.candidate['claim_id'], preceding=[],
            capabilities=['read_workspace', 'scratch', 'model', 'write_workspace'],
            result_contract=assignments.CONTRACT)
        self.e.seal_handoff()
        self.store(self.candidate)
        request = {'candidate_assignment': self.candidate['assignment_id'], 'axis': 'review-spec',
                   'profile': {'name': 'fresh-spec', 'home': str(self.root / 'fresh-spec-home')},
                   'skills': [], 'claim_id': 'fresh-spec'}
        before = assignments.rows(self.database)
        invalid = [None, [], {}, {**request, 'approval': True}]
        invalid += [{**request, 'candidate_assignment': value} for value in (None, 7, '', 'a' * 63, 'g' * 64)]
        invalid += [{**request, 'axis': value} for value in (None, [], 7, 'combined')]
        admitted = review.candidate(self.database, 'product', 'm1', '42', self.candidate['assignment_id'])
        with patch.object(review, 'candidate', return_value=admitted) as candidate_reader:
            for value in invalid:
                with self.subTest(request=value):
                    candidate_reader.reset_mock()
                    self.preflight.reset_mock()
                    with self.assertRaises(RepositoryError) as refusal:
                        review.prepare(self.database, 'product', 'm1', '42', value, self.github)
                    self.assertEqual(refusal.exception.code, 'invalid_review')
                    candidate_reader.assert_not_called()
                    self.preflight.assert_not_called()
                    self.assertEqual(assignments.rows(self.database), before)
        prior, root, source, baseline, checks = review.candidate(self.database, 'product', 'm1', '42', self.candidate['assignment_id'])
        expected = {key: prior['handoff'][key] for key in assignments.REQUEST_FIELDS}
        expected.update(stage=request['axis'], profile=request['profile'], skills=request['skills'],
            claim_id=request['claim_id'], capabilities=['read_workspace', 'scratch', 'model'],
            preceding=review.preceding(prior, root, source, baseline, checks))
        self.preflight.reset_mock()
        self.assertEqual(review.prepare(self.database, 'product', 'm1', '42', request, self.github, dry_run=True), {'dry_run': True})
        self.preflight.assert_called_once_with(self.database, 'product', 'm1', '42', expected, self.github, True)
        self.assertEqual(assignments.rows(self.database), before)

    def test_review_exact_policy_decision_cases(self):
        workflow = '.github/workflows/check.yml'
        cases = [('security', None, 'operator-decision'),
            ('security', {'reference': 'Local unrelated axis grant', 'paths': [], 'findings': ['Standards:R1']}, 'operator-decision'),
            ('security', {'reference': 'Local unrelated finding grant', 'paths': [], 'findings': ['Spec:R2']}, 'operator-decision'),
            ('security', {'reference': 'Local excess path grant', 'paths': ['hello.py'], 'findings': ['Spec:R1']}, 'operator-decision'),
            ('security', {'reference': 'Local explicit security decision', 'paths': [], 'findings': ['Spec:R1']}, 'corrections'),
            ('workflow', None, 'operator-decision'),
            ('workflow', {'reference': 'Local unrelated decision', 'paths': [], 'findings': []}, 'operator-decision'),
            ('workflow', {'reference': 'Local exact decision', 'paths': [workflow], 'findings': []}, 'accepted')]
        for family, decision, status in cases:
            with self.subTest(family=family, decision=decision), component_case(ReviewComponentTests) as case:
                if family == 'security':
                    case.adverse(case.axes[1], kind='security-weakening')
                else:
                    for axis in case.axes:
                        axis['handoff']['review'].update(policy_paths=[workflow], test_changes=['test_hello.py'])
                        axis['handoff_digest'] = assignments.digest(axis['handoff'])
                        axis['submitted_result']['handoff_digest'] = axis['handoff_digest']
                        (Path(axis['runtime']['artifacts']) / 'scratch/result.json').write_text(json.dumps(axis['submitted_result']))
                        case.store(axis)
                feedback = case.evaluate(case.authorization(decision))
                self.assertEqual(feedback['status'], status)
                if family == 'security':
                    self.assertEqual(feedback['policy_findings'], ['Spec:R1'])
                    self.assertEqual(feedback['findings'][0]['kind'], 'security-weakening')
                    self.assertFalse(feedback['advance_allowed'])
                    self.assertFalse(feedback['merge_allowed'])
                    self.assertFalse(feedback['close_allowed'])
                    if status == 'corrections':
                        self.assertEqual(case.evaluate(), feedback)
                    if decision is None:
                        self.assertEqual(case.evaluate()['status'], 'operator-decision')
                else:
                    self.assertEqual(case.axes[1]['handoff']['review']['test_changes'], ['test_hello.py'])

    def test_handoff_refuses_each_changed_scope_or_preceding_with_valid_candidate(self):
        prior, root, source, baseline, checks = review.candidate(self.database, 'product', 'm1', '42', self.candidate['assignment_id'])
        request = copy.deepcopy(prior['handoff'])
        request.update(stage='review-spec', preceding=review.preceding(prior, root, source, baseline, checks))
        contract = review.handoff(self.database, 'product', 'm1', '42', request)
        self.assertEqual(contract['pins'], self.pins)
        before = assignments.rows(self.database)
        changes = {'repository': 'example/other', 'repository_id': 456, 'workspace': str(self.root / 'other'),
                   'issue': {**request['issue'], 'body_sha256': 'f' * 64}, 'spec_commit': 'f' * 40,
                   'baseline': 'f' * 40, 'candidate': 'f' * 40,
                   'standards': [simplification.artifact('AGENTS.md', 'Unrelated standards')]}
        changed_preceding = copy.deepcopy(request['preceding'])
        changed_preceding[-1] = simplification.artifact('implementation-diff', 'Unrelated diff')
        changes['preceding'] = changed_preceding  # Keep the authentic candidate identity/evidence.
        for field, value in changes.items():
            with self.subTest(field=field):
                altered = {**request, field: value}
                with self.assertRaises(RepositoryError) as refusal:
                    review.handoff(self.database, 'product', 'm1', '42', altered)
                self.assertEqual(refusal.exception.code, 'scope_mismatch')
                self.assertEqual(assignments.rows(self.database), before)
        self.assertEqual(review.handoff(self.database, 'product', 'm1', '42', request), contract)

    def test_review_candidate_admission_cases(self):
        cases = [('valid', None), ('raw-newline', 'candidate_unverified'), ('baseline', 'candidate_unverified'),
                 ('command', 'candidate_unverified'), ('outcome', 'candidate_unverified'),
                 ('checks', 'candidate_unverified'), ('findings', 'candidate_unverified'), ('correction', 'stale_review')]
        for mode, code in cases:
            with self.subTest(mode=mode), candidate_case() as case:
                receipt = case.e.root / 'controller-checks.json'
                raw = receipt.read_bytes()
                row = copy.deepcopy(case.candidate)
                if mode == 'raw-newline':
                    receipt.write_bytes(raw + b'\n')
                    self.assertEqual(json.loads(receipt.read_bytes()), json.loads(raw))
                elif mode == 'baseline':
                    (case.e.root / 'inputs/baseline/hello.py').write_text('changed baseline\n')
                    changed = review.manifest(case.e.root / 'inputs/baseline')
                    (case.e.root / 'initial-source.json').write_text(json.dumps({'workspace': case.e.initial, 'baseline': changed}))
                    self.assertEqual(simplification.check_receipt(case.e.root, row['runtime'])[0], case.e.initial)
                elif mode == 'command':
                    changed = json.loads(raw)
                    changed['records'][0]['command'] = 'python -m unittest unrelated_tests -v'
                    receipt.write_text(json.dumps(changed))
                    row['runtime']['checks_sha256'] = hashlib.sha256(receipt.read_bytes()).hexdigest()
                    self.assertEqual(simplification.check_receipt(case.e.root, row['runtime'])[2], changed)
                elif mode in ('outcome', 'checks', 'findings'):
                    changes = {'outcome': {'outcome': 'findings'}, 'checks': {'checks_verified': False},
                               'findings': {'findings': ['unresolved']}}
                    row['simplification_result'].update(changes[mode])
                elif mode == 'correction':
                    case.store({'assignment_id': '6' * 64, 'ticket_scope': copy.deepcopy(row['ticket_scope']),
                        'handoff': {'corrections': {'pins': case.pins}},
                        'runtime': {'status': 'complete'}, 'submitted_result': {'status': 'done'}})
                case.store(row)
                if code:
                    with self.assertRaises(RepositoryError) as refusal:
                        review.candidate(case.database, 'product', 'm1', '42', row['assignment_id'])
                    self.assertEqual(refusal.exception.code, code)
                else:
                    admitted = review.candidate(case.database, 'product', 'm1', '42', row['assignment_id'])
                    self.assertEqual(admitted[2:4], (case.e.initial, case.e.baseline))

    def test_pair_independence_checks_and_resealed_pin_matrix(self):
        for field, value, code in [('status', 'failed', 'review_incomplete'), ('container_removed', False, 'review_incomplete')]:
            axis = copy.deepcopy(self.axes[1])
            axis['runtime'][field] = value
            self.store(axis)
            with self.subTest(field=field):
                with self.assertRaises(RepositoryError) as refusal:
                    self.evaluate()
                self.assertEqual(refusal.exception.code, code)
        self.store(self.axes[1])
        axis = copy.deepcopy(self.axes[1])
        axis['handoff']['review']['pins']['spec_commit'] = 'f' * 40
        axis['handoff_digest'] = assignments.digest(axis['handoff'])
        report = json.loads(axis['submitted_result']['artifacts'][0]['content'])
        report['pins'] = axis['handoff']['review']['pins']
        axis['submitted_result'].update(handoff_digest=axis['handoff_digest'])
        axis['submitted_result']['artifacts'] = [simplification.artifact('review-result', json.dumps(report))]
        axis['review_result'] = self.admitted_review(axis)
        self.store(axis)
        with self.assertRaises(RepositoryError) as refusal:
            self.evaluate()
        self.assertEqual(refusal.exception.code, 'stale_review')


    def test_real_staging_and_policy_test_path_derivation(self):
        e = self.e
        (e.root / 'workspace/.github/workflows').mkdir(parents=True)
        (e.root / 'workspace/.github/workflows/check.yml').write_text('local policy')
        (e.root / 'workspace/test_hello.py').write_text('local test')
        e.refresh_checks()
        self.candidate['simplification_result']['candidate_sha256'] = e.runtime['candidate_sha256']
        self.store(self.candidate)
        prior, root, source, baseline, checks = review.candidate(self.database, 'product', 'm1', '42', self.candidate['assignment_id'])
        request = copy.deepcopy(prior['handoff'])
        request.update(stage='review-spec', preceding=review.preceding(prior, root, source, baseline, checks))
        contract = review.handoff(self.database, 'product', 'm1', '42', request)
        self.assertEqual(contract['policy_paths'], ['.github/workflows/check.yml'])
        self.assertEqual(contract['test_changes'], ['test_hello.py'])
        destination = self.root / 'staged'
        destination.mkdir()
        (destination / 'workspace').mkdir()
        pins = {'baseline': baseline}
        review.copy_candidate(self.database, 'product', 'm1', '42', {'handoff': {'review': contract}}, destination, pins)
        self.assertEqual(review.manifest(destination / 'workspace'), source)

    def test_shared_execution_fingerprints_refuse_unbounded_symlink_or_missing_response(self):
        from factory_v1 import merge
        axis = self.axes[0]
        binding = review.execution_binding(axis)
        merged_binding = merge.execution_binding(axis)
        self.assertEqual(binding['evidence'], merged_binding['evidence'])
        self.assertEqual(binding['model_evidence'], merged_binding['model_evidence'])
        root = Path(axis['runtime']['artifacts'])
        response = root / 'model-evidence/response-local.json'
        retained_response = response.with_suffix('.retained')
        response.rename(retained_response)
        with self.assertRaises(RepositoryError) as refusal:
            review.execution_binding(axis)
        self.assertEqual(refusal.exception.code, 'execution_required')
        retained_response.rename(response)
        transcript = root / 'scratch/conversation.json'
        raw = transcript.read_bytes()
        with transcript.open('wb') as stream:
            stream.truncate(16_000_001)
        with self.assertRaises(RepositoryError) as refusal:
            review.execution_binding(axis)
        self.assertEqual(refusal.exception.code, 'execution_required')
        transcript.write_bytes(raw)
        retained = transcript.with_suffix('.retained')
        transcript.rename(retained)
        transcript.symlink_to(retained)
        with self.assertRaises(RepositoryError) as refusal:
            review.execution_binding(axis)
        self.assertEqual(refusal.exception.code, 'unsafe_path')

    def test_exact_correction_findings_keep_scope_and_require_both_fresh_axes(self):
        self.adverse(self.axes[1])
        feedback = self.evaluate()
        self.assertEqual(feedback['status'], 'corrections')
        request = copy.deepcopy(self.axes[0]['handoff'])
        request['preceding'] = [simplification.artifact('candidate', request['candidate']),
            simplification.artifact('findings', simplification.handoff_json({'reviews': self.request, 'feedback': feedback}))]
        contract = review.corrections_handoff(self.database, 'product', 'm1', '42', request, self.github)
        self.assertEqual(contract['findings'], feedback['findings'])
        self.assertEqual(contract['rerun_required'], ['implementation-checks', 'simplify', 'Standards', 'Spec'])
        self.assertFalse(contract['advance_allowed'])
        request['baseline'] = 'f' * 40
        with self.assertRaises(RepositoryError) as refusal:
            review.corrections_handoff(self.database, 'product', 'm1', '42', request, self.github)
        self.assertEqual(refusal.exception.code, 'scope_mismatch')


    def test_pause_and_stopped_axis_never_authorize_acceptance(self):
        from factory_v1 import controls
        controls.request(self.database, 'product', 'm1', '42', 'pause', 'pause before authorization')
        with self.assertRaises(RepositoryError) as refusal:
            self.evaluate(self.authorization())
        self.assertEqual(refusal.exception.code, 'iteration_paused')
        self.assertNotIn('review_authorization', assignments.rows(self.database)[1])
        (Path(self.axes[0]['runtime']['artifacts']) / 'stop').touch()
        with self.assertRaises(RepositoryError) as refusal:
            assignments.require_result_active(self.axes[0])
        self.assertEqual(refusal.exception.code, 'cancelled')


if __name__ == "__main__":
    unittest.main()
