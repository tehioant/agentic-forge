"""Direct engineering seams; legacy sibling fixture helpers are not discovered."""
import copy
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
        self.candidate['simplification_result'] = simplification.validate_result(self.candidate, self.e.result(self.e.report()))
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
        row['review_result'] = review.validate_result(row, result)
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
        axis['review_result'] = review.validate_result(axis, axis['submitted_result'])
        (Path(axis['runtime']['artifacts']) / 'scratch/result.json').write_text(json.dumps(axis['submitted_result']))
        self.store(axis)
        with self.database:
            review.record_hold(self.database, axis)
        return axis

    def authorization(self, decision=None):
        path = self.root / 'authorization.json'
        path.write_text(json.dumps({'operator_id': '42', 'execution_reference': 'LABELED local host authorization input',
            'axes': [review.execution_binding(a) for a in self.axes], 'policy_decision': decision}))
        path.chmod(0o600)
        return path

    def evaluate(self, config=None):
        return review.evaluate(self.database, 'product', 'm1', '42', self.request, self.github, config)

    def test_pair_authority_replay_and_private_exact_binding(self):
        self.assertEqual(self.evaluate()['status'], 'execution-unverified')
        path = self.authorization()
        accepted = self.evaluate(path)
        self.assertTrue(accepted['advance_allowed'])
        self.assertFalse(accepted['merge_allowed'])
        self.assertFalse(accepted['close_allowed'])
        self.assertEqual(self.evaluate(), accepted)
        with closing(sqlite3.connect(self.root / 'state.sqlite')) as restarted:
            self.assertEqual(assignments.inspect(restarted, 'product', 'm1', '42', self.axes[0]['assignment_id'])['review_authorization']['axes'],
                             [review.execution_binding(a) for a in self.axes])
        (Path(self.axes[0]['runtime']['artifacts']) / 'scratch/conversation.json').write_text('drift')
        with self.assertRaises(RepositoryError) as refusal:
            self.evaluate()
        self.assertEqual(refusal.exception.code, 'execution_required')

    def test_authorization_mode_mount_and_pin_drift_refuse(self):
        path = self.authorization()
        path.chmod(0o644)
        with self.assertRaises(RepositoryError) as refusal:
            self.evaluate(path)
        self.assertEqual(refusal.exception.code, 'approval_required')
        path.chmod(0o600)
        data = json.loads(path.read_text())
        data['axes'][0]['pins']['spec_commit'] = 'f' * 40
        path.write_text(json.dumps(data))
        with self.assertRaises(RepositoryError) as refusal:
            self.evaluate(path)
        self.assertEqual(refusal.exception.code, 'execution_required')
        mounted = Path(self.axes[0]['runtime']['artifacts']) / 'scratch/authority.json'
        mounted.write_bytes(self.authorization().read_bytes())
        mounted.chmod(0o600)
        with self.assertRaises(RepositoryError) as refusal:
            self.evaluate(mounted)
        self.assertEqual(refusal.exception.code, 'approval_required')

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
        child = {'assignment_id': '6' * 64, 'handoff': {'corrections': {'pins': self.pins}},
                 'runtime': {'status': 'complete'}, 'submitted_result': {'status': 'stuck', 'work': ['Retained failed correction']}}
        self.store(child)
        feedback = self.evaluate()
        self.assertEqual(feedback['debug_evidence'][0]['result'], child['submitted_result'])

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
        axis['review_result'] = review.validate_result(axis, axis['submitted_result'])
        self.store(axis)
        with self.assertRaises(RepositoryError) as refusal:
            self.evaluate()
        self.assertEqual(refusal.exception.code, 'stale_review')

    def test_candidate_receipts_supersession_and_independent_holds(self):
        self.assertEqual(review.candidate(self.database, 'product', 'm1', '42', self.candidate['assignment_id'])[2], self.e.initial)
        for change in ({'outcome': 'findings'}, {'checks_verified': False}, {'findings': ['unresolved']}):
            row = copy.deepcopy(self.candidate)
            row['simplification_result'].update(change)
            self.store(row)
            with self.subTest(change=change):
                with self.assertRaises(RepositoryError) as refusal:
                    review.candidate(self.database, 'product', 'm1', '42', row['assignment_id'])
                self.assertEqual(refusal.exception.code, 'candidate_unverified')
        self.store(self.candidate)
        child = {'assignment_id': '6' * 64, 'handoff': {'corrections': {'pins': self.pins}},
                 'runtime': {'status': 'complete'}, 'submitted_result': {'status': 'done'}}
        self.store(child)
        with self.assertRaises(RepositoryError) as refusal:
            review.candidate(self.database, 'product', 'm1', '42', self.candidate['assignment_id'])
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

    def test_exact_policy_path_direction_does_not_hide_test_changes(self):
        for axis in self.axes:
            axis['handoff']['review'].update(policy_paths=['.github/workflows/check.yml'], test_changes=['test_hello.py'])
            axis['handoff_digest'] = assignments.digest(axis['handoff'])
            axis['submitted_result']['handoff_digest'] = axis['handoff_digest']
            (Path(axis['runtime']['artifacts']) / 'scratch/result.json').write_text(json.dumps(axis['submitted_result']))
            self.store(axis)
        self.assertEqual(self.evaluate(self.authorization())['status'], 'operator-decision')
        wrong = {'reference': 'Local unrelated decision', 'paths': [], 'findings': []}
        self.assertEqual(self.evaluate(self.authorization(wrong))['status'], 'operator-decision')
        exact = {**wrong, 'paths': ['.github/workflows/check.yml']}
        self.assertEqual(self.evaluate(self.authorization(exact))['status'], 'accepted')
        self.assertEqual(self.axes[1]['handoff']['review']['test_changes'], ['test_hello.py'])

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
