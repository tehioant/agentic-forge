"""Public stage/launcher regressions; Docker/model doubles are NOT live skill evidence."""
import copy
import json
import sqlite3
import unittest
from pathlib import Path

from factory_v1 import assignments
from factory_v1.tests import test_assignments, test_sandbox


class SimplificationTests(unittest.TestCase):
    def setUp(self):
        self.case = test_sandbox.SandboxTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.assignment = self.case.assignment
        self.set_checks(['python -m unittest discover -v'])
        self.prepared = self.assignment.ok(self.assignment.command())

    def set_checks(self, commands):
        config = json.loads(self.case.config.read_text())
        config['verification_commands'] = commands
        self.case.config.write_text(json.dumps(config))

    def implementation(self):
        result = self.assignment.ok(self.case.launch(self.prepared))
        self.assertEqual(result['runtime']['status'], 'complete', result)
        return result

    def request(self, prior, suffix='default'):
        configuration = self.assignment.configuration(stage='simplify')
        configuration['profile'].update(name='simplifier-' + suffix,
            home=str(self.case.root / ('simplifier-home-' + suffix)))
        return {'implementation_assignment': prior['assignment_id'],
                'profile': configuration['profile'], 'skills': configuration['skills'], 'claim_id': 'simplify-' + suffix}

    def prepare(self, request, dry_run=False):
        path = self.case.root / 'simplification-request.json'
        path.write_text(json.dumps(request))
        args = ['prepare-simplification', '--project', 'product', '--iteration', 'm1',
                '--request', str(path), '--api-base', self.assignment.fixture.base]
        if dry_run:
            args.append('--dry-run')
        return self.assignment.fixture.cli(*args)

    def test_preparation_requires_controller_tested_implementation_not_text_or_worker_assertion(self):
        request = self.request(self.prepared)
        before = self.assignment.fixture.state.read_bytes()
        self.assignment.refused(self.prepare(request), 'implementation_unverified')
        self.assertEqual(self.assignment.fixture.state.read_bytes(), before)
        claimed = self.assignment.result(self.prepared)
        self.assignment.ok(self.assignment.command('assignment-result', claimed, self.prepared['assignment_id']))
        self.assignment.refused(self.prepare(request), 'implementation_unverified')
        generic = self.assignment.configuration(stage='simplify')
        generic['preceding'].append(test_assignments.artifact('implementation-diff', 'arbitrary prose'))
        self.assignment.refused(self.assignment.command(request=generic), 'implementation_unverified')
        self.assertEqual(self.case.model_calls, [])

    def test_no_checks_cannot_prepare_simplification(self):
        self.set_checks([])
        prior = self.implementation()
        self.assignment.refused(self.prepare(self.request(prior)), 'implementation_unverified')

    def test_exact_handoff_dry_run_replay_and_no_op_use_sanitized_implementation_not_host_head(self):
        prior = self.implementation()
        request = self.request(prior)
        before = self.assignment.fixture.state.read_bytes()
        dry = self.assignment.ok(self.prepare(request, dry_run=True))
        self.assertEqual(self.assignment.fixture.state.read_bytes(), before)
        prepared = self.assignment.ok(self.prepare(request))
        self.assertEqual(prepared['handoff_digest'], dry['handoff_digest'])
        self.assertEqual(self.assignment.ok(self.prepare(request))['assignment_id'], prepared['assignment_id'])
        handoff = prepared['handoff']
        self.assertEqual(handoff['issue_body'], prior['handoff']['issue_body'])
        for field in ('specification', 'standards', 'baseline'):
            self.assertEqual(handoff[field], prior['handoff'][field])
        self.assertEqual(handoff['skill_entry_points'], ['simplify-code'])
        self.assertEqual(handoff['skills'][0]['instructions'], Path(request['skills'][0]['path']).read_text())
        self.assertEqual(handoff['simplification']['scope'], ['hello.py'])
        evidence = json.loads(next(a['content'] for a in handoff['preceding'] if a['name'] == 'implementation-evidence'))
        self.assertEqual(evidence['checks']['candidate'], json.loads((Path(prior['runtime']['artifacts']) / 'source-artifacts.json').read_text()))
        diff = json.loads(next(a['content'] for a in handoff['preceding'] if a['name'] == 'implementation-diff'))
        self.assertIn('candidate fixture', diff[0]['diff'])
        result = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(result['runtime']['status'], 'complete', result)
        stage = result['simplification_result']
        self.assertEqual(stage['outcome'], 'no-op')
        self.assertEqual(stage['input_candidate_sha256'], stage['candidate_sha256'])
        self.assertEqual(stage['candidate_sha256'], prior['runtime']['candidate_sha256'])
        self.assertTrue(stage['checks_verified'])
        self.assertFalse(stage['approval'])
        self.assertFalse(stage['advance_allowed'])
        self.assertFalse(stage['native_execution_trusted'])
        root = Path(result['runtime']['artifacts'])
        self.assertEqual((root / 'workspace/hello.py').read_text(), 'print("candidate fixture")\n')
        self.assertEqual((root / 'inputs/baseline/hello.py').read_text(), 'print("baseline")\n')
        self.assertEqual((self.case.source / 'hello.py').read_text(), 'print("baseline")\n')
        self.assertEqual(self.assignment.ok(self.assignment.inspect(prepared))['simplification_result'], stage)
        self.assertFalse((self.case.bin / 'container.json').exists())
        self.assignment.assert_no_external_writes()

    def test_cleanup_pins_result_and_findings_are_retained_without_approval(self):
        prior = self.implementation()
        for mode in ('cleanup', 'findings'):
            with self.subTest(mode=mode):
                prepared = self.assignment.ok(self.prepare(self.request(prior, mode)))
                self.case.mode(mode)
                result = self.assignment.ok(self.case.launch(prepared))
                self.assertEqual(result['runtime']['status'], 'complete', result)
                stage = result['simplification_result']
                self.assertEqual(stage['outcome'], mode)
                self.assertEqual(stage['candidate_sha256'], result['runtime']['candidate_sha256'])
                self.assertEqual(stage['changed_paths'], ['hello.py'] if mode == 'cleanup' else [])
                self.assertEqual(bool(stage['findings']), mode == 'findings')
                self.assertFalse(stage['approval'])
                self.assertFalse(result['result_disposition']['advance_allowed'])
                self.assertEqual(self.assignment.ok(self.assignment.inspect(prepared))['simplification_result'], stage)

    def assert_failed_modes(self, modes):
        prior = self.implementation()
        for mode in modes:
            with self.subTest(mode=mode):
                prepared = self.assignment.ok(self.prepare(self.request(prior, mode)))
                self.case.mode(mode)
                result = self.assignment.ok(self.case.launch(prepared))
                self.assertEqual(result['runtime']['status'], 'failed', result)
                self.assertEqual(result['runtime']['error'], 'scope_violation' if mode == 'scope-violation' else 'invalid_result')
                observed = self.assignment.ok(self.assignment.inspect(prepared))
                self.assertNotIn('submitted_result', observed)
                self.assertNotIn('simplification_result', observed)
                self.assertFalse((self.case.bin / 'container.json').exists())
                self.assertTrue((Path(result['runtime']['artifacts']) / 'scratch/result.json').is_file())

    def test_wrong_pins_malformed_results_and_missing_four_angle_work_are_refused(self):
        self.assert_failed_modes(('wrong-revision', 'wrong-input', 'wrong-adaptation', 'missing-angle', 'malformed-stage', 'duplicate-stage', 'nested-stage'))

    def test_scope_behavior_changes_false_no_op_and_applied_findings_are_refused(self):
        self.assert_failed_modes(('scope-violation', 'behavior-not-preserved', 'false-no-op', 'applied-finding', 'false-approval'))

    def test_failing_resulting_candidate_checks_retain_raw_evidence_but_no_stage_result(self):
        prior = self.implementation()
        prepared = self.assignment.ok(self.prepare(self.request(prior)))
        self.case.mode('checks-fail')
        result = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(result['runtime']['error'], 'verification_failed')
        observed = self.assignment.ok(self.assignment.inspect(prepared))
        self.assertNotIn('submitted_result', observed)
        self.assertNotIn('simplification_result', observed)
        self.assertTrue((Path(result['runtime']['artifacts']) / 'scratch/result.json').is_file())

    def test_absent_or_changed_verification_commands_refuse_before_new_attempt(self):
        prior = self.implementation()
        prepared = self.assignment.ok(self.prepare(self.request(prior)))
        for commands in ([], ['true'], ['python -m unittest discover -v', 'true']):
            with self.subTest(commands=commands):
                self.set_checks(commands)
                self.assignment.refused(self.case.launch(prepared), 'verification_required')
                self.assertNotIn('runtime', self.assignment.ok(self.assignment.inspect(prepared)))
        self.assertEqual(len(self.case.model_calls), 1)

    def test_source_and_check_receipt_drift_refuse_preparation_or_launch(self):
        prior = self.implementation()
        prepared = self.assignment.ok(self.prepare(self.request(prior)))
        root = Path(prior['runtime']['artifacts'])
        receipt = root / 'controller-checks.json'
        raw = receipt.read_text()
        receipt.write_text(raw + '\n')
        self.assignment.refused(self.prepare(self.request(prior, 'drift')), 'implementation_unverified')
        receipt.write_text(raw)
        (root / 'workspace/hello.py').write_text('print("drift")\n')
        self.assignment.refused(self.case.launch(prepared), 'implementation_unverified')
        self.assertNotIn('runtime', self.assignment.ok(self.assignment.inspect(prepared)))
        self.assertEqual(len(self.case.model_calls), 1)

    def test_failed_implementation_cannot_start_simplification(self):
        self.case.mode('checks-fail')
        prior = self.assignment.ok(self.case.launch(self.prepared))
        self.assertEqual(prior['runtime']['status'], 'failed')
        self.assignment.refused(self.prepare(self.request(prior)), 'implementation_unverified')

    def test_empty_ticket_diff_accepts_evidence_backed_no_op(self):
        self.case.mode('implementation-no-op')
        prior = self.implementation()
        prepared = self.assignment.ok(self.prepare(self.request(prior)))
        self.assertEqual(prepared['handoff']['simplification']['scope'], [])
        self.case.mode('')
        result = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(result['runtime']['status'], 'complete', result)
        self.assertEqual(result['simplification_result']['outcome'], 'no-op')
        self.assertEqual(result['simplification_result']['changed_paths'], [])

    def test_skill_drift_wrong_pin_and_reused_profiles_refuse_without_new_model_calls(self):
        prior = self.implementation()
        request = self.request(prior)
        wrong_pin = copy.deepcopy(request)
        wrong_pin['skills'][0]['sha256'] = '0' * 64
        self.assignment.refused(self.prepare(wrong_pin), 'skill_blocked')
        reused = copy.deepcopy(request)
        reused['profile']['home'] = prior['handoff']['profile']['home']
        self.assignment.refused(self.prepare(reused), 'profile_claim_conflict')
        prepared = self.assignment.ok(self.prepare(request))
        skill = Path(request['skills'][0]['path'])
        skill.write_text(skill.read_text() + '\nUnreviewed instruction drift.\n')
        self.assignment.refused(self.case.launch(prepared), 'skill_blocked')
        self.assertNotIn('runtime', self.assignment.ok(self.assignment.inspect(prepared)))
        self.assertEqual(len(self.case.model_calls), 1)

    def test_caller_result_without_whole_process_launch_cannot_create_candidate(self):
        prior = self.implementation()
        prepared = self.assignment.ok(self.prepare(self.request(prior)))
        report = self.assignment.result(prepared)
        self.assignment.refused(self.assignment.command('assignment-result', report, prepared['assignment_id']), 'invalid_result')
        observed = self.assignment.ok(self.assignment.inspect(prepared))
        self.assertNotIn('submitted_result', observed)
        self.assertNotIn('simplification_result', observed)
        self.assertEqual(len(self.case.model_calls), 1)

    def test_failed_malformed_attempt_cannot_accept_caller_replacement(self):
        prior = self.implementation()
        prepared = self.assignment.ok(self.prepare(self.request(prior, 'failed-replacement')))
        self.case.mode('malformed-stage')
        failed = self.assignment.ok(self.case.launch(prepared))
        self.assertEqual(failed['runtime']['status'], 'failed')
        root = Path(failed['runtime']['artifacts'])
        original = (root / 'scratch/result.json').read_bytes()
        replacement = json.loads(original)
        contract = prepared['handoff']['simplification']
        checks = json.loads((root / 'controller-checks.json').read_text())
        report = {'contract': contract['contract'], 'adaptation': prepared['handoff']['adaptation'],
                  'input_candidate_sha256': contract['input_candidate_sha256'],
                  'candidate_sha256': assignments.digest(checks['candidate']), 'outcome': 'no-op',
                  'behavior_preservation': 'preserved',
                  'angles': {angle: 'Caller replacement, not executed work' for angle in contract['angles']},
                  'findings': []}
        replacement['artifacts'] = [test_assignments.artifact('simplification-result', json.dumps(report))
                                    if artifact['name'] == 'simplification-result' else artifact
                                    for artifact in replacement['artifacts']]
        before = self.assignment.fixture.state.read_bytes()
        self.assignment.refused(self.assignment.command('assignment-result', replacement, prepared['assignment_id']), 'invalid_result')
        self.assertEqual(self.assignment.fixture.state.read_bytes(), before)
        observed = self.assignment.ok(self.assignment.inspect(prepared))
        self.assertEqual(observed['runtime'], failed['runtime'])
        self.assertNotIn('submitted_result', observed)
        self.assertNotIn('simplification_result', observed)
        self.assertEqual((root / 'scratch/result.json').read_bytes(), original)

    def test_only_exact_completed_launcher_result_can_replay(self):
        prior = self.implementation()
        prepared = self.assignment.ok(self.prepare(self.request(prior, 'completed-replay')))
        completed = self.assignment.ok(self.case.launch(prepared))
        accepted = completed['submitted_result']
        replay = self.assignment.ok(self.assignment.command('assignment-result', accepted, prepared['assignment_id']))
        self.assertEqual(replay['simplification_result'], completed['simplification_result'])
        for change in ('work', 'sandbox_run_id'):
            altered = copy.deepcopy(accepted)
            altered[change] = ['Caller replacement'] if change == 'work' else accepted['run_id']
            before = self.assignment.fixture.state.read_bytes()
            self.assignment.refused(self.assignment.command('assignment-result', altered, prepared['assignment_id']), 'invalid_result')
            self.assertEqual(self.assignment.fixture.state.read_bytes(), before)
        for status in ('running', 'failed', 'unconfirmed', 'stopped'):
            with self.subTest(injected_runtime_status=status):
                injected = copy.deepcopy(completed)
                injected['runtime'].update(status=status, container_removed=status != 'unconfirmed')
                database = sqlite3.connect(self.assignment.fixture.state)
                try:
                    with database:
                        database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                                         (json.dumps(injected), prepared['assignment_id']))
                finally:
                    database.close()
                before = self.assignment.fixture.state.read_bytes()
                self.assignment.refused(self.assignment.command('assignment-result', accepted, prepared['assignment_id']), 'invalid_result')
                self.assertEqual(self.assignment.fixture.state.read_bytes(), before)
                observed = self.assignment.ok(self.assignment.inspect(prepared))
                self.assertEqual(observed['runtime'], injected['runtime'])
                self.assertEqual(observed['submitted_result'], accepted)

    def test_pause_keeps_prepared_stage_but_refuses_launch(self):
        prior = self.implementation()
        prepared = self.assignment.ok(self.prepare(self.request(prior)))
        self.assignment.mutate_iteration(lambda item: item.update(status='paused'))
        self.assignment.refused(self.case.launch(prepared), 'iteration_paused')
        self.assertNotIn('runtime', self.assignment.ok(self.assignment.inspect(prepared)))
        self.assertEqual(len(self.case.model_calls), 1)

    def test_stop_during_simplification_checks_never_accepts_candidate(self):
        import time
        prior = self.implementation()
        prepared = self.assignment.ok(self.prepare(self.request(prior)))
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
            self.fail('Simplification checks did not start')
        self.assignment.ok(self.assignment.command('stop-assignment', assignment=prepared['assignment_id']))
        output, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, error)
        result = json.loads(output)
        self.assertEqual(result['runtime']['status'], 'stopped', result)
        self.assertNotIn('simplification_result', result)
        self.assertNotIn('submitted_result', result)
        self.assertFalse((self.case.bin / 'container.json').exists())
        observed = self.assignment.ok(self.assignment.inspect(prepared))
        self.assertEqual(observed['runtime'], result['runtime'])
        self.assertNotIn('simplification_result', observed)

    def test_generic_prepare_cannot_substitute_requirements_standards_or_diff(self):
        prior = self.implementation()
        prepared = self.assignment.ok(self.prepare(self.request(prior), dry_run=True))
        generic = {key: value for key, value in prepared['handoff'].items() if key in self.assignment.request}
        generic['skills'] = [{key: value for key, value in skill.items() if key != 'instructions'} for skill in generic['skills']]
        for field in ('candidate', 'baseline', 'standards', 'preceding'):
            request = copy.deepcopy(generic)
            if field in ('candidate', 'baseline'):
                request[field] = 'f' * 40
            elif field == 'standards':
                request[field] = [test_assignments.artifact('AGENTS.md', 'Weakened standards')]
            else:
                request[field][-1] = test_assignments.artifact('implementation-diff', 'Wrong diff')
            self.assignment.refused(self.assignment.command(request=request), 'scope_mismatch')


if __name__ == '__main__':
    unittest.main()
