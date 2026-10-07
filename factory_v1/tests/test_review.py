"""Public fresh-process review lifecycle. All Docker/model/tracker evidence is labeled simulation."""
import copy
import json
import time
import unittest
from pathlib import Path

from factory_v1 import review
from factory_v1.tests import test_simplification


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

    def test_review_axes_require_distinct_top_level_profiles_and_homes(self):
        standards = self.axis('review-standards')
        request = self.request('review-spec')
        request['profile']['home'] = standards['handoff']['profile']['home']
        self.assignment.refused(self.command('prepare-review', request), 'profile_claim_conflict')

    def test_host_verified_acceptance_restarts_exactly_but_changed_execution_evidence_refuses(self):
        axes = self.pair()
        config = self.authorization(axes)
        accepted = self.assignment.ok(self.command('review-candidate', self.pair_request(axes), config))
        self.assertEqual(accepted['status'], 'accepted')
        self.assertTrue(accepted['advance_allowed'])
        self.assertFalse(accepted['merge_allowed'])
        self.assertFalse(accepted['close_allowed'])
        self.assertEqual(self.assignment.ok(self.command('review-candidate', self.pair_request(axes))), accepted)
        transcript = Path(axes[0]['runtime']['artifacts']) / 'scratch/conversation.json'
        transcript.write_text(transcript.read_text() + '\n')
        self.assignment.refused(self.command('review-candidate', self.pair_request(axes)), 'execution_required')

    def test_host_authorization_rejects_public_permissions_wrong_scope_and_wrong_pins(self):
        axes = self.pair()
        request = self.pair_request(axes)
        path = self.authorization(axes)
        path.chmod(0o644)
        self.assignment.refused(self.command('review-candidate', request, path), 'approval_required')
        path.chmod(0o600)
        raw = path.read_text()
        config = json.loads(raw)
        config['axes'][0]['pins']['spec_commit'] = 'f' * 40
        path.write_text(json.dumps(config))
        self.assignment.refused(self.command('review-candidate', request, path), 'execution_required')
        mounted = Path(axes[0]['runtime']['artifacts']) / 'scratch/authorization.json'
        mounted.write_text(raw)
        mounted.chmod(0o600)
        self.assignment.refused(self.command('review-candidate', request, mounted), 'approval_required')

    def test_rejection_routes_exact_findings_to_fresh_implement_then_invalidates_both_axes(self):
        approved = self.pair(suffix='-original')
        self.assertEqual(self.assignment.ok(self.command('review-candidate', self.pair_request(approved),
                                                       self.authorization(approved)))['status'], 'accepted')
        axes = self.pair('review-reject')
        feedback = self.assignment.ok(self.command('review-candidate', self.pair_request(axes)))
        self.assertEqual(feedback['status'], 'corrections')
        self.assertEqual(feedback['axes']['Standards']['verdict'], 'pass')
        self.assertEqual(feedback['axes']['Spec']['verdict'], 'reject')
        stale = self.assignment.ok(self.command('review-candidate', self.pair_request(approved)))
        self.assertEqual(stale['status'], 'corrections')
        self.assertFalse(stale['advance_allowed'])
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

    def test_gate_security_and_test_weakening_are_not_green_shortcuts(self):
        for mode in ('review-gate', 'review-security', 'review-test'):
            candidate = self.candidate_with_mode('builder-' + mode)
            axes = self.pair(mode, '-' + mode, candidate=candidate)
            request = self.pair_request(axes)
            config = self.authorization(axes)
            feedback = self.assignment.ok(self.command('review-candidate', request, config))
            self.assertEqual(feedback['status'], 'corrections' if mode == 'review-test' else 'operator-decision')
            self.assertFalse(feedback['advance_allowed'])
            if mode != 'review-test':
                policy = {'reference': 'Fixture explicit operator decision, not gate bypass', 'paths': [], 'findings': ['Spec:R1']}
                result = self.assignment.ok(self.command('review-candidate', request, self.authorization(axes, policy)))
                self.assertEqual(result['status'], 'corrections')
                self.assertFalse(result['advance_allowed'])

    def test_stuck_review_preserves_debug_evidence_without_retry_based_acceptance(self):
        axes = self.pair('review-stuck')
        result = self.assignment.ok(self.command('review-candidate', self.pair_request(axes)))
        self.assertEqual(result['status'], 'diagnosis')
        self.assertEqual(set(result['axes']['Spec']['stuckness']), {'attempts', 'evidence', 'uncertainty', 'recommendation'})
        self.assertFalse(result['advance_allowed'])

    def test_lone_rejection_persists_candidate_hold_and_later_passes_cannot_reset_it(self):
        approved = self.pair()
        self.assignment.ok(self.command('review-candidate', self.pair_request(approved), self.authorization(approved)))
        rejected = self.axis('review-spec', 'review-reject', '-lone')
        held = self.assignment.ok(self.assignment.inspect(self.candidate))
        self.assertEqual(held['review_holds'][rejected['assignment_id']]['verdict'], 'reject')
        for _ in range(2):
            result = self.assignment.ok(self.command('review-candidate', self.pair_request(approved)))
            self.assertEqual(result['status'], 'corrections')
            self.assertEqual(result['adverse_reviews'][0]['assignment_id'], rejected['assignment_id'])
            self.assertFalse(result['advance_allowed'])
        later_passes = self.pair(suffix='-retry')
        result = self.assignment.ok(self.command('review-candidate', self.pair_request(later_passes),
                                                self.authorization(later_passes)))
        self.assertEqual(result['status'], 'corrections')
        self.assertEqual(self.assignment.ok(self.assignment.inspect(self.candidate))['review_holds'], held['review_holds'])

    def test_all_completed_adverse_axes_remain_correlated_in_current_feedback(self):
        approved = self.pair()
        self.assignment.ok(self.command('review-candidate', self.pair_request(approved), self.authorization(approved)))
        rejected = self.axis('review-spec', 'review-reject', '-reject')
        stuck = self.axis('review-standards', 'review-stuck', '-stuck')
        result = self.assignment.ok(self.command('review-candidate', self.pair_request(approved)))
        self.assertEqual(result['status'], 'diagnosis')
        self.assertEqual({a['assignment_id'] for a in result['adverse_reviews']},
                         {rejected['assignment_id'], stuck['assignment_id']})
        self.assertEqual(result['findings'][0]['kind'], 'missing')
        self.assertFalse(result['advance_allowed'])
        policy = self.axis('review-spec', 'review-gate', '-policy')
        result = self.assignment.ok(self.command('review-candidate', self.pair_request(approved)))
        self.assertEqual(result['status'], 'operator-decision')
        self.assertEqual(result['policy_findings'], ['Spec:R1'])
        self.assertEqual(len(result['adverse_reviews']), 3)
        self.assertIn(policy['assignment_id'], self.assignment.ok(self.assignment.inspect(self.candidate))['review_holds'])

    def test_malformed_wrong_axis_scope_pins_and_false_pass_never_admit(self):
        for mode in ('review-wrong-axis', 'review-wrong-tree', 'review-wrong-adaptation', 'review-false-pass',
                     'review-malformed', 'review-duplicate', 'review-duplicate-finding', 'review-write', 'bad-load'):
            with self.subTest(mode=mode):
                prepared = self.assignment.ok(self.command('prepare-review', self.request('review-spec', '-' + mode)))
                self.case.mode(mode)
                result = self.assignment.ok(self.case.launch(prepared))
                self.assertEqual(result['runtime']['status'], 'failed', result)
                self.assertEqual(result['runtime']['error'], 'invalid_result')
                self.assertNotIn('review_result', self.assignment.ok(self.assignment.inspect(prepared)))
                self.assertTrue((Path(result['runtime']['artifacts']) / 'scratch/result.json').exists())

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
        skill = Path(request['skills'][0]['path'])
        skill.write_text(skill.read_text() + '\nUnreviewed change')
        self.assignment.refused(self.case.launch(prepared), 'skill_blocked')
        self.assertNotIn('runtime', self.assignment.ok(self.assignment.inspect(prepared)))

    def candidate_with_mode(self, mode):
        config = copy.deepcopy(self.assignment.request)
        config['profile'].update(name='builder-' + mode, home=str(self.case.root / ('builder-home-' + mode)))
        config['claim_id'] = 'builder-' + mode
        self.case.mode(mode)
        prepared = self.assignment.ok(self.assignment.command(request=config))
        implemented = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(implemented['runtime']['status'], 'complete', implemented)
        self.case.mode('')
        prepared = self.assignment.ok(self.seam.prepare(self.seam.request(implemented, mode)))
        return self.assignment.ok(self.case.launch(prepared))

    def test_policy_paths_hold_claimed_passes_but_legitimate_test_changes_are_requirement_reviewed(self):
        for mode in ('implementation-policy', 'implementation-test'):
            candidate = self.candidate_with_mode(mode)
            axes = self.pair(suffix='-' + mode, candidate=candidate)
            request = self.pair_request(axes)
            result = self.assignment.ok(self.command('review-candidate', request, self.authorization(axes)))
            if mode == 'implementation-policy':
                self.assertEqual(result['status'], 'operator-decision')
                self.assertFalse(result['advance_allowed'])
                path = '.github/workflows/checks.yml'
                bad = {'reference': 'Fixture wrong operator scope', 'paths': [], 'findings': []}
                self.assertEqual(self.assignment.ok(self.command('review-candidate', request, self.authorization(axes, bad)))['status'], 'operator-decision')
                exact = {'reference': 'Fixture explicit exact policy decision', 'paths': [path], 'findings': []}
                self.assertEqual(self.assignment.ok(self.command('review-candidate', request, self.authorization(axes, exact)))['status'], 'accepted')
            else:
                self.assertEqual(result['status'], 'accepted')
                self.assertEqual(axes[1]['handoff']['review']['test_changes'], ['test_hello.py'])
                self.assertTrue(result['axes']['Spec']['test_assessment'])

    def test_axes_from_distinct_candidates_never_mix_and_private_receipt_tree_baseline_drift_refuses(self):
        first_axes = self.pair()
        second = self.candidate_with_mode('implementation-test')
        second_spec = self.axis('review-spec', suffix='-different', candidate=second)
        self.assignment.refused(self.command('review-candidate', self.pair_request((first_axes[0], second_spec))), 'stale_review')
        root = Path(self.candidate['runtime']['artifacts'])
        receipt = root / 'controller-checks.json'
        raw = receipt.read_bytes()
        receipt.write_bytes(raw + b'\n')
        self.assignment.refused(self.command('review-candidate', self.pair_request(first_axes)), 'candidate_unverified')
        receipt.write_bytes(raw)
        (root / 'inputs/baseline/hello.py').write_text('baseline drift')
        self.assignment.refused(self.command('review-candidate', self.pair_request(first_axes)), 'candidate_unverified')

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

    def test_public_malformed_scope_refuses_without_new_assignments(self):
        request = self.request('review-spec')
        before = self.assignment.fixture.state.read_bytes()
        for altered in (None, [], {}, {**request, 'axis': ['review-spec']}, {**request, 'candidate_assignment': 'BAD'},
                        {**request, 'extra': True}):
            self.assignment.refused(self.command('prepare-review', altered), 'invalid_review')
            self.assertEqual(before, self.assignment.fixture.state.read_bytes())
        prepared = self.assignment.ok(self.command('prepare-review', request))
        generic = {key: prepared['handoff'][key] for key in self.assignment.request}
        generic['skills'] = [{k: v for k, v in s.items() if k != 'instructions'} for s in generic['skills']]
        for key in ('candidate', 'baseline', 'spec_commit'):
            wrong = copy.deepcopy(generic)
            wrong[key] = 'f' * 40
            self.assignment.refused(self.assignment.command(request=wrong), 'scope_mismatch')

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

    def test_generic_review_and_corrections_cannot_inject_prose_or_substitute_original_scope(self):
        arbitrary = self.assignment.configuration(stage='review-spec')
        self.assignment.refused(self.assignment.command(request=arbitrary), 'implementation_unverified')
        prepared = self.assignment.ok(self.command('prepare-review', self.request('review-spec')))
        generic = {key: prepared['handoff'][key] for key in self.assignment.request}
        generic['skills'] = [{k: v for k, v in s.items() if k != 'instructions'} for s in generic['skills']]
        generic['standards'][0]['content'] = 'weak policy'
        import hashlib
        generic['standards'][0]['sha256'] = hashlib.sha256(b'weak policy').hexdigest()
        self.assignment.refused(self.assignment.command(request=generic), 'scope_mismatch')


if __name__ == '__main__':
    unittest.main()
