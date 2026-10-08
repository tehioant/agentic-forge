"""Representative public review lifecycles plus local validator/controller matrices; NOT live evidence."""
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


    def test_two_fresh_readonly_axes_keep_pins_separate_and_fail_closed_without_host_authority(self):
        axes = self.pair()
        for axis, label in zip(axes, ('Standards', 'Spec')):
            handoff = axis['handoff']
            self.assertEqual(handoff['review']['axis'], label)
            self.assertEqual(handoff['skill_entry_points'], ['code-review'])
            self.assertEqual(handoff['skills'][0]['instructions'], Path(handoff['skills'][0]['path']).read_text())
            self.assertEqual(handoff['capabilities'], ['read_workspace', 'scratch', 'model'])
            self.assertEqual(handoff['review']['pins']['tree_sha256'], self.candidate['runtime']['candidate_sha256'])
            self.assertFalse(axis['review_result']['native_execution_trusted'])
            root = Path(axis['runtime']['artifacts'])
            inspection = json.loads((root / 'container-inspection.json').read_text())
            self.assertFalse(next(m['RW'] for m in inspection['Mounts'] if m['Destination'] == '/workspace'))
            self.assertTrue(next(m['RW'] for m in inspection['Mounts'] if m['Destination'] == '/scratch'))
            self.assertEqual((root / 'workspace/hello.py').read_text(), 'print("candidate fixture")\n')
            self.assertEqual(handoff['issue_body'], self.implemented['handoff']['issue_body'])
            self.assertNotIn('implementation_result', ''.join(a['content'] for a in handoff['preceding']))
        result = self.assignment.ok(self.command('review-candidate', self.pair_request(axes)))
        self.assertEqual(result['status'], 'execution-unverified')
        self.assertFalse(result['advance_allowed'])
        self.assertEqual(set(result['axes']), {'Standards', 'Spec'})
        self.assignment.assert_no_external_writes()
        config = self.authorization(axes)
        accepted = self.assignment.ok(self.command('review-candidate', self.pair_request(axes), config))
        self.assertEqual(accepted['status'], 'accepted')
        self.assertTrue(accepted['advance_allowed'])
        self.assertFalse(accepted['merge_allowed'])
        self.assertFalse(accepted['close_allowed'])
        self.assertEqual(self.assignment.ok(self.command('review-candidate', self.pair_request(axes))), accepted)
        self.assert_launcher_refusals()
        transcript = Path(axes[0]['runtime']['artifacts']) / 'scratch/conversation.json'
        transcript.write_text(transcript.read_text() + '\n')
        self.assignment.refused(self.command('review-candidate', self.pair_request(axes)), 'execution_required')


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

    def test_rejection_routes_exact_findings_to_fresh_implement_then_invalidates_both_axes(self):
        axes = self.pair('review-reject')
        feedback = self.assignment.ok(self.command('review-candidate', self.pair_request(axes)))
        self.assertEqual(feedback['status'], 'corrections')
        self.assertEqual(feedback['axes']['Standards']['verdict'], 'pass')
        self.assertEqual(feedback['axes']['Spec']['verdict'], 'reject')
        config = self.assignment.configuration(stage='corrections')
        request = {**self.pair_request(axes), 'profile': config['profile'], 'skills': config['skills'], 'claim_id': 'fix-1'}
        before = self.assignment.fixture.state.read_bytes()
        self.assignment.ok(self.command('prepare-corrections', request, dry=True))
        self.assertEqual(self.assignment.fixture.state.read_bytes(), before)
        prepared = self.assignment.ok(self.command('prepare-corrections', request))
        self.assertEqual(prepared['handoff']['skill_entry_points'], ['implement'])
        self.assertEqual(prepared['handoff']['corrections']['findings'], feedback['findings'])
        self.assertEqual(self.assignment.ok(self.command('prepare-corrections', request))['assignment_id'], prepared['assignment_id'])
        self.case.mode('')
        corrected = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(corrected['runtime']['status'], 'complete', corrected)
        self.assignment.refused(self.command('review-candidate', self.pair_request(axes)), 'stale_review')
        simplified = self.assignment.ok(self.seam.prepare(self.seam.request(corrected, 'corrected')))
        next_candidate = self.assignment.ok(self.case.launch(simplified))
        self.assertEqual(next_candidate['runtime']['status'], 'complete', next_candidate)
        new_axes = self.pair(suffix='-corrected', candidate=next_candidate)
        accepted = self.assignment.ok(self.command('review-candidate', self.pair_request(new_axes), self.authorization(new_axes)))
        self.assertEqual(accepted['status'], 'accepted')


    def test_stuck_corrective_attempt_keeps_agent_evidence_for_debug_and_never_satisfies_review(self):
        axes = self.pair('review-reject')
        config = self.assignment.configuration(stage='corrections')
        request = {**self.pair_request(axes), 'profile': config['profile'], 'skills': config['skills'], 'claim_id': 'stuck-fix'}
        prepared = self.assignment.ok(self.command('prepare-corrections', request))
        self.seam.set_checks(['true'])
        self.assignment.refused(self.case.launch(prepared), 'verification_required')
        self.seam.set_checks(['python -m unittest discover -v'])
        self.case.mode('correction-stuck')
        stuck = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(stuck['runtime']['status'], 'complete', stuck)
        feedback = self.assignment.ok(self.command('review-candidate', self.pair_request(axes)))
        self.assertEqual(feedback['status'], 'diagnosis')
        self.assertEqual(feedback['debug_evidence'][0]['result'], stuck['submitted_result'])
        self.assertFalse(feedback['advance_allowed'])
        self.assignment.refused(self.command('prepare-corrections', request), 'corrections_held')


    def test_pause_and_stop_during_review_checks_preserve_raw_attempt_without_acceptance(self):
        prepared = self.assignment.ok(self.command('prepare-review', self.request('review-spec')))
        self.case.mode('checks-hang')
        process = self.case.started(prepared)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            observed = self.assignment.ok(self.assignment.inspect(prepared))
            runtime = observed.get('runtime')
            if runtime and (Path(runtime['artifacts']) / 'checks/container-inspection.json').exists():
                break
            self.assertIsNone(process.poll())
            time.sleep(.02)
        else:
            self.fail('Review checks did not start')
        self.assignment.mutate_iteration(lambda item: item.update(status='paused'))
        self.assignment.ok(self.assignment.command('stop-assignment', assignment=prepared['assignment_id']))
        output, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, error)
        stopped = json.loads(output)
        self.assertEqual(stopped['runtime']['status'], 'stopped')
        self.assertNotIn('submitted_result', stopped)
        self.assertNotIn('review_result', stopped)
        self.assertFalse((self.case.bin / 'container.json').exists())
        self.assertEqual(self.assignment.ok(self.assignment.inspect(prepared))['runtime'], stopped['runtime'])


    def test_preparation_dry_replay_pause_skill_drift_missing_checks_and_caller_assertion(self):
        request = self.request('review-spec')
        before = self.assignment.fixture.state.read_bytes()
        dry = self.assignment.ok(self.command('prepare-review', request, dry=True))
        self.assertEqual(self.assignment.fixture.state.read_bytes(), before)
        prepared = self.assignment.ok(self.command('prepare-review', request))
        self.assertEqual(dry['assignment_id'], prepared['assignment_id'])
        self.assignment.refused(self.assignment.command('assignment-result', self.assignment.result(prepared), prepared['assignment_id']), 'invalid_result')
        self.seam.set_checks(['true'])
        self.assignment.refused(self.case.launch(prepared), 'verification_required')
        self.seam.set_checks(['python -m unittest discover -v'])
        self.assignment.mutate_iteration(lambda item: item.update(status='paused'))
        self.assignment.refused(self.case.launch(prepared), 'iteration_paused')
        self.assignment.mutate_iteration(lambda item: item.update(status='active'))
        standards_request = self.request('review-standards')
        standards = self.assignment.ok(self.command('prepare-review', standards_request))
        reused = self.request('review-spec', '-reused')
        reused['profile']['home'] = standards['handoff']['profile']['home']
        self.assignment.refused(self.command('prepare-review', reused), 'profile_claim_conflict')
        before = self.assignment.fixture.state.read_bytes()
        self.assignment.refused(self.command('prepare-review', {**request, 'extra': True}), 'invalid_review')
        self.assertEqual(before, self.assignment.fixture.state.read_bytes())
        arbitrary = self.assignment.configuration(stage='review-spec')
        self.assignment.refused(self.assignment.command(request=arbitrary), 'implementation_unverified')
        generic = {key: prepared['handoff'][key] for key in self.assignment.request}
        generic['skills'] = [{k: v for k, v in s.items() if k != 'instructions'} for s in generic['skills']]
        generic['candidate'] = 'f' * 40
        self.assignment.refused(self.assignment.command(request=generic), 'scope_mismatch')
        skill = Path(request['skills'][0]['path'])
        skill.write_text(skill.read_text() + '\nUnreviewed change')
        self.assignment.refused(self.case.launch(prepared), 'skill_blocked')
        self.assertNotIn('runtime', self.assignment.ok(self.assignment.inspect(prepared)))



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


class ReviewControllerTests(unittest.TestCase):
    """Real controller/SQLite/tracker seam; local result inputs launch zero workers."""
    def setUp(self):
        self.case = test_assignments.AssignmentTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.database = sqlite3.connect(self.case.fixture.state)
        self.addCleanup(self.database.close)
        self.github = GitHub(self.case.fixture.base, 'fixture', 10)
        prepared = assignments.prepare(self.database, 'product', 'm1', '42', self.case.request, self.github)
        prior, _ = self.local_completed(prepared, 'implementation')
        config = self.case.configuration(stage='simplify')
        candidate = simplification.prepare(self.database, 'product', 'm1', '42',
            {'implementation_assignment': prior['assignment_id'], 'profile': config['profile'],
             'skills': config['skills'], 'claim_id': 'local-simplifier'}, self.github)
        self.candidate, self.evidence = self.local_completed(candidate, 'simplification')
        self.axes = []
        for stage in ('review-standards', 'review-spec'):
            config = self.case.configuration(stage=stage)
            axis = review.prepare(self.database, 'product', 'm1', '42',
                {'candidate_assignment': self.candidate['assignment_id'], 'axis': stage, 'profile': config['profile'],
                 'skills': config['skills'], 'claim_id': 'local-' + stage}, self.github)
            completed, _ = self.local_completed(axis, stage)
            self.axes.append(completed)
        self.request = {'standards_assignment': self.axes[0]['assignment_id'], 'spec_assignment': self.axes[1]['assignment_id']}

    def store(self, row):
        self.database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                              (json.dumps(row), row['assignment_id']))
        self.database.commit()

    def local_completed(self, prepared, name, source=None):
        root = self.case.root / ('local-' + name)
        root.mkdir(mode=0o700)
        e = test_simplification.LocalStageEvidence(root)
        if source:
            import shutil
            shutil.rmtree(root / 'workspace')
            shutil.copytree(source, root / 'workspace')
            e.refresh_checks()
        row = copy.deepcopy(prepared)
        row['runtime'] = e.runtime
        row['result_disposition'] = {'checks_verified': True}
        if name == 'implementation':
            result = {'status': 'done', 'run_id': row['runtime']['run_id']}
        elif row['handoff']['stage'] == 'simplify':
            e.assignment = row
            e.contract = row['handoff']['simplification']
            result = e.result(e.report())
            row['simplification_result'] = simplification.validate_result(row, result)
        else:
            contract = row['handoff']['review']
            result = e.result({'contract': review.CONTRACT, 'axis': contract['axis'],
                'adaptation': row['handoff']['adaptation'], 'pins': contract['pins'], 'verdict': 'pass', 'findings': [],
                'test_assessment': 'LABELED local controller input, not native execution', 'stuckness': None}, 'review-result')
            row['review_result'] = review.validate_result(row, result)
        result.update(loads=[], work=['LABELED local controller input; NOT executed work'])
        row['submitted_result'] = result
        # Binding tests hash deliberately labeled local files, not native attestation.
        for group, names in (('scratch', ['result.json', 'loads.json', 'events.jsonl', 'conversation.json', 'probes.json']),
                             ('inputs', ['worker.py', 'sandbox_relay.py', 'assignment.json', 'handoff.json']),
                             ('', ['container-inspection.json', 'launch-command.json', 'source-artifacts.json'])):
            for filename in names:
                path = root / group / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('LABELED local execution-binding input; no worker ran\n')
        (root / 'scratch/result.json').write_text(json.dumps(result))
        (root / 'model-evidence').mkdir()
        (root / 'model-evidence/response-local.json').write_text('LABELED local response hash input; no inference\n')
        self.store(row)
        return row, e

    def authorization(self, decision=None):
        path = self.case.root / 'local-host-authorization.json'
        path.write_text(json.dumps({'operator_id': '42', 'execution_reference': 'LABELED local authorization input; NOT live evidence',
                                   'axes': [review.execution_binding(axis) for axis in self.axes], 'policy_decision': decision}))
        path.chmod(0o600)
        return path

    def evaluate(self, config=None):
        return review.evaluate(self.database, 'product', 'm1', '42', self.request, self.github, config)

    def test_authorization_policy_findings_and_stuckness_are_not_green_shortcuts(self):
        self.assertEqual(self.evaluate()['status'], 'execution-unverified')
        path = self.authorization()
        path.chmod(0o644)
        with self.assertRaises(RepositoryError) as raised:
            self.evaluate(path)
        self.assertEqual(raised.exception.code, 'approval_required')
        path.chmod(0o600)
        config = json.loads(path.read_text())
        config['axes'][0]['pins']['spec_commit'] = 'f' * 40
        path.write_text(json.dumps(config))
        with self.assertRaises(RepositoryError) as raised:
            self.evaluate(path)
        self.assertEqual(raised.exception.code, 'execution_required')
        path = self.authorization()
        mounted = Path(self.axes[0]['runtime']['artifacts']) / 'scratch/authorization.json'
        mounted.write_bytes(path.read_bytes())
        mounted.chmod(0o600)
        with self.assertRaises(RepositoryError) as raised:
            self.evaluate(mounted)
        self.assertEqual(raised.exception.code, 'approval_required')
        accepted = self.evaluate(path)
        self.assertEqual(accepted['status'], 'accepted')
        self.assertTrue(accepted['advance_allowed'])
        self.assertFalse(accepted['merge_allowed'])
        self.assertFalse(accepted['close_allowed'])
        self.assertEqual(self.evaluate(), accepted)  # Durable saved authorization, no worker retry.
        with closing(sqlite3.connect(self.case.fixture.state)) as restarted:
            saved = assignments.inspect(restarted, 'product', 'm1', '42', self.axes[0]['assignment_id'])
        assert saved is not None
        self.assertEqual(saved['review_authorization']['axes'], [review.execution_binding(a) for a in self.axes])
        axis = self.axes[1]
        original = copy.deepcopy(axis)
        for kind in ('gate-weakening', 'security-weakening', 'test-weakening', 'stuck'):
            with self.subTest(kind=kind):
                axis = copy.deepcopy(original)
                artifact = axis['submitted_result']['artifacts'][0]
                report = json.loads(artifact['content'])
                if kind == 'stuck':
                    axis['submitted_result']['status'] = 'stuck'
                    report.update(verdict='stuck', stuckness={k: 'LABELED retained investigation' for k in
                                  ('attempts', 'evidence', 'uncertainty', 'recommendation')})
                else:
                    report.update(verdict='reject', findings=[{'id': 'R1', 'kind': kind, 'path': 'hello.py',
                        'requirement': 'Required meaningful checks', 'evidence': 'LABELED omitted protection', 'correction': 'Restore protection'}])
                axis['submitted_result']['artifacts'] = [simplification.artifact('review-result', json.dumps(report))]
                axis['review_result'] = review.validate_result(axis, axis['submitted_result'])
                (Path(axis['runtime']['artifacts']) / 'scratch/result.json').write_text(json.dumps(axis['submitted_result']))
                self.axes[1] = axis
                self.store(axis)
                feedback = self.evaluate(self.authorization())
                if kind == 'stuck':
                    expected = 'diagnosis'
                elif kind == 'test-weakening':
                    expected = 'corrections'
                else:
                    expected = 'operator-decision'
                self.assertEqual(feedback['status'], expected)
                self.assertFalse(feedback['advance_allowed'])
                if kind in ('gate-weakening', 'security-weakening'):
                    wrong = {'reference': 'LABELED wrong scope', 'paths': [], 'findings': []}
                    self.assertEqual(self.evaluate(self.authorization(wrong))['status'], 'operator-decision')
                    exact = {'reference': 'LABELED exact operator decision, not acceptance', 'paths': [], 'findings': ['Spec:R1']}
                    self.assertEqual(self.evaluate(self.authorization(exact))['status'], 'corrections')
                    self.assertFalse(self.evaluate()['advance_allowed'])
        self.case.assert_no_external_writes()

    def test_candidate_scope_policy_test_changes_claims_and_private_receipt_pins(self):
        candidate = self.candidate
        e = self.evidence
        identity = candidate['assignment_id']
        def read():
            return review.candidate(self.database, 'product', 'm1', '42', identity)
        self.assertEqual(read()[2], e.initial)
        profile = self.case.configuration(stage='review-spec')
        request = {'candidate_assignment': identity, 'axis': 'review-spec', 'profile': profile['profile'],
                   'skills': profile['skills'], 'claim_id': 'local-input-matrix'}
        before = self.case.fixture.state.read_bytes()
        for altered in (None, [], {}, {**request, 'axis': ['review-spec']},
                        {**request, 'candidate_assignment': 'BAD'}, {**request, 'extra': True}):
            with self.subTest(request=altered), self.assertRaises(RepositoryError) as raised:
                review.prepare(self.database, 'product', 'm1', '42', altered, self.github)
            self.assertEqual(raised.exception.code, 'invalid_review')
            self.assertEqual(self.case.fixture.state.read_bytes(), before)
        config = self.case.configuration(stage='simplify')
        config['profile'].update(name='second-candidate', home=str(self.case.root / 'second-candidate-home'))
        prepared = simplification.prepare(self.database, 'product', 'm1', '42',
            {'implementation_assignment': candidate['handoff']['simplification']['implementation_assignment'],
             'profile': config['profile'], 'skills': config['skills'], 'claim_id': 'second-candidate'}, self.github)
        second, _ = self.local_completed(prepared, 'second-candidate')
        config = self.case.configuration(stage='review-spec')
        config['profile'].update(name='second-spec', home=str(self.case.root / 'second-spec-home'))
        prepared = review.prepare(self.database, 'product', 'm1', '42',
            {'candidate_assignment': second['assignment_id'], 'axis': 'review-spec', 'profile': config['profile'],
             'skills': config['skills'], 'claim_id': 'second-spec'}, self.github)
        second_spec, _ = self.local_completed(prepared, 'second-spec')
        mixed = {'standards_assignment': self.axes[0]['assignment_id'], 'spec_assignment': second_spec['assignment_id']}
        with self.assertRaises(RepositoryError) as raised:
            review.pair(self.database, 'product', 'm1', '42', mixed, self.github)
        self.assertEqual(raised.exception.code, 'stale_review')
        for change in ({'outcome': 'findings'}, {'checks_verified': False}, {'findings': ['Unresolved']}, {'outcome': 'unknown'}):
            with self.subTest(change=change):
                altered = copy.deepcopy(candidate)
                altered['simplification_result'].update(change)
                self.store(altered)
                with self.assertRaises(RepositoryError) as raised:
                    read()
                self.assertEqual(raised.exception.code, 'candidate_unverified')
        self.store(candidate)
        receipt = e.root / 'controller-checks.json'
        raw = receipt.read_bytes()
        receipt.write_bytes(raw + b'\n')
        with self.assertRaises(RepositoryError) as raised:
            read()
        self.assertEqual(raised.exception.code, 'candidate_unverified')
        receipt.write_bytes(raw)
        baseline = e.root / 'inputs/baseline/hello.py'
        raw_baseline = baseline.read_bytes()
        baseline.write_text('baseline drift')
        with self.assertRaises(RepositoryError) as raised:
            read()
        self.assertEqual(raised.exception.code, 'candidate_unverified')
        baseline.write_bytes(raw_baseline)
        generic = {key: self.axes[1]['handoff'][key] for key in assignments.REQUEST_FIELDS}
        self.assertEqual(review.handoff(self.database, 'product', 'm1', '42', generic)['test_changes'], [])
        for field in ('candidate', 'baseline', 'spec_commit', 'standards'):
            altered = copy.deepcopy(generic)
            altered[field] = ['Weakened policy'] if field == 'standards' else 'f' * 40
            with self.assertRaises(RepositoryError) as raised:
                review.handoff(self.database, 'product', 'm1', '42', altered)
            self.assertEqual(raised.exception.code, 'scope_mismatch')
        simplify_request = {key: copy.deepcopy(candidate['handoff'][key]) for key in assignments.REQUEST_FIELDS}
        for field in ('candidate', 'baseline', 'standards', 'preceding'):
            altered = copy.deepcopy(simplify_request)
            if field in ('candidate', 'baseline'):
                altered[field] = 'f' * 40
            elif field == 'standards':
                altered[field] = [simplification.artifact('AGENTS.md', 'Weakened standards')]
            else:
                altered[field][-1] = simplification.artifact('implementation-diff', 'Wrong diff')
            with self.subTest(simplification_scope=field), self.assertRaises(RepositoryError) as raised:
                simplification.handoff(self.database, 'product', 'm1', '42', altered)
            self.assertEqual(raised.exception.code, 'scope_mismatch')
        # The controller derives path policy from actual local tree deltas, not caller assertions.
        (e.root / 'workspace/.github/workflows').mkdir(parents=True)
        (e.root / 'workspace/.github/workflows/checks.yml').write_text('LABELED local policy-path input')
        (e.root / 'workspace/test_hello.py').write_text('LABELED legitimate test-path input')
        e.refresh_checks()
        candidate['simplification_result'].update(candidate_sha256=e.runtime['candidate_sha256'])
        self.store(candidate)
        _, root, source, before, checks = read()
        generic['preceding'] = review.preceding(candidate, root, source, before, checks)
        contract = review.handoff(self.database, 'product', 'm1', '42', generic)
        self.assertEqual(contract['policy_paths'], ['.github/workflows/checks.yml'])
        self.assertEqual(contract['test_changes'], ['test_hello.py'])
        # Each policy case is a local tree/controller transition, not another worker chain.
        for policy in (True, False):
            with self.subTest(policy_path=policy):
                if not policy:
                    (e.root / 'workspace/.github/workflows/checks.yml').unlink()
                    e.refresh_checks()
                    candidate['simplification_result']['candidate_sha256'] = e.runtime['candidate_sha256']
                    self.store(candidate)
                axes = []
                for stage in ('review-standards', 'review-spec'):
                    suffix = stage + str(policy)
                    config = self.case.configuration(stage=stage)
                    config['profile'].update(name='paths-' + suffix, home=str(self.case.root / ('paths-home-' + suffix)))
                    prepared = review.prepare(self.database, 'product', 'm1', '42',
                        {'candidate_assignment': identity, 'axis': stage, 'profile': config['profile'],
                         'skills': config['skills'], 'claim_id': 'paths-' + suffix}, self.github)
                    row, _ = self.local_completed(prepared, suffix, e.root / 'workspace')
                    axes.append(row)
                self.axes = axes
                self.request = {'standards_assignment': axes[0]['assignment_id'], 'spec_assignment': axes[1]['assignment_id']}
                feedback = self.evaluate(self.authorization())
                self.assertEqual(feedback['status'], 'operator-decision' if policy else 'accepted')
                self.assertEqual(axes[1]['handoff']['review']['test_changes'], ['test_hello.py'])
                if policy:
                    wrong = {'reference': 'LABELED wrong policy scope', 'paths': [], 'findings': []}
                    self.assertEqual(self.evaluate(self.authorization(wrong))['status'], 'operator-decision')
                    exact = {'reference': 'LABELED exact policy scope', 'paths': ['.github/workflows/checks.yml'], 'findings': []}
                    self.assertEqual(self.evaluate(self.authorization(exact))['status'], 'accepted')
                self.assertTrue(feedback['axes']['Spec']['test_assessment'])
        self.case.assert_no_external_writes()


if __name__ == '__main__':
    unittest.main()
