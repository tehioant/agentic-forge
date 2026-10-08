"""Small public lifecycle smokes and local real-validator matrices; NOT live execution evidence."""
import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from factory_v1 import assignments, simplification
from factory_v1.repositories import RepositoryError
from factory_v1.sandbox_source import manifest
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
        accepted = result['submitted_result']
        replay = self.assignment.ok(self.assignment.command('assignment-result', accepted, prepared['assignment_id']))
        self.assertEqual(replay['simplification_result'], result['simplification_result'])
        for change in ('work', 'sandbox_run_id'):
            altered = copy.deepcopy(accepted)
            altered[change] = ['Caller replacement'] if change == 'work' else accepted['run_id']
            before = self.assignment.fixture.state.read_bytes()
            self.assignment.refused(self.assignment.command('assignment-result', altered, prepared['assignment_id']), 'invalid_result')
            self.assertEqual(self.assignment.fixture.state.read_bytes(), before)
        for status in ('running', 'failed', 'unconfirmed', 'stopped'):
            with self.subTest(injected_runtime_status=status):
                injected = copy.deepcopy(result)
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
        for mode, error in (('scope-violation', 'scope_violation'), ('checks-fail', 'verification_failed')):
            with self.subTest(mode=mode):
                prepared = self.assignment.ok(self.prepare(self.request(prior, mode)))
                self.case.mode(mode)
                rejected = self.assignment.ok(self.case.launch(prepared))
                self.assertEqual(rejected['runtime']['status'], 'failed', rejected)
                self.assertEqual(rejected['runtime']['error'], error)
                observed = self.assignment.ok(self.assignment.inspect(prepared))
                self.assertNotIn('submitted_result', observed)
                self.assertNotIn('simplification_result', observed)
                self.assertFalse((self.case.bin / 'container.json').exists())
                self.assertTrue((Path(rejected['runtime']['artifacts']) / 'scratch/result.json').is_file())


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


    def test_prelaunch_admission_scope_and_receipts_fail_closed_without_workers(self):
        prior = self.implementation()
        request = self.request(prior)
        wrong_pin = copy.deepcopy(request)
        wrong_pin['skills'][0]['sha256'] = '0' * 64
        self.assignment.refused(self.prepare(wrong_pin), 'skill_blocked')
        reused = copy.deepcopy(request)
        reused['profile']['home'] = prior['handoff']['profile']['home']
        self.assignment.refused(self.prepare(reused), 'profile_claim_conflict')
        prepared = self.assignment.ok(self.prepare(request))
        self.assignment.refused(self.assignment.command('assignment-result', self.assignment.result(prepared),
                                                       prepared['assignment_id']), 'invalid_result')
        for commands in ([], ['true'], ['python -m unittest discover -v', 'true']):
            with self.subTest(commands=commands):
                self.set_checks(commands)
                self.assignment.refused(self.case.launch(prepared), 'verification_required')
        self.set_checks(['python -m unittest discover -v'])
        self.assignment.mutate_iteration(lambda item: item.update(status='paused'))
        self.assignment.refused(self.case.launch(prepared), 'iteration_paused')
        self.assignment.mutate_iteration(lambda item: item.update(status='active'))
        generic = {key: value for key, value in prepared['handoff'].items() if key in self.assignment.request}
        generic['skills'] = [{key: value for key, value in skill.items() if key != 'instructions'} for skill in generic['skills']]
        generic['candidate'] = 'f' * 40
        self.assignment.refused(self.assignment.command(request=generic), 'scope_mismatch')
        root = Path(prior['runtime']['artifacts'])
        receipt = root / 'controller-checks.json'
        raw = receipt.read_bytes()
        receipt.write_bytes(raw + b'\n')
        self.assignment.refused(self.prepare(self.request(prior, 'drift')), 'implementation_unverified')
        receipt.write_bytes(raw)
        workspace = root / 'workspace/hello.py'
        original = workspace.read_bytes()
        workspace.write_text('print("drift")\n')
        self.assignment.refused(self.case.launch(prepared), 'implementation_unverified')
        workspace.write_bytes(original)
        skill = Path(request['skills'][0]['path'])
        skill.write_text(skill.read_text() + '\nUnreviewed instruction drift.\n')
        self.assignment.refused(self.case.launch(prepared), 'skill_blocked')
        self.assertNotIn('runtime', self.assignment.ok(self.assignment.inspect(prepared)))
        self.assertEqual(len(self.case.model_calls), 1)


class LocalStageEvidence:
    """Explicit local validator input, never a launcher/native execution receipt."""
    def __init__(self, root):
        self.root = root
        (root / 'workspace').mkdir()
        (root / 'inputs/baseline').mkdir(parents=True)
        (root / 'workspace/hello.py').write_text('candidate\n')
        (root / 'inputs/baseline/hello.py').write_text('baseline\n')
        self.initial = manifest(root / 'workspace')
        self.baseline = manifest(root / 'inputs/baseline')
        (root / 'initial-source.json').write_text(json.dumps({'workspace': self.initial, 'baseline': self.baseline}))
        self.runtime = {'artifacts': str(root), 'run_id': 'local-validator-input', 'status': 'complete',
                        'container_removed': True, 'baseline_sha256': assignments.digest(self.baseline)}
        self.refresh_checks()
        self.contract = {'contract': simplification.CONTRACT, 'input_candidate_sha256': assignments.digest(self.initial),
                         'scope': ['hello.py'], 'verification_commands': ['python -m unittest discover -v'],
                         'result_fields': ['contract', 'adaptation', 'input_candidate_sha256', 'candidate_sha256',
                                           'outcome', 'behavior_preservation', 'angles', 'findings']}
        self.assignment = {'assignment_id': 'a' * 64, 'claim_id': 'local-input',
            'runtime': self.runtime, 'handoff': {'stage': 'simplify', 'simplification': self.contract,
            'adaptation': 'LABELED local validator adaptation', 'skills': [],
            'baseline': 'a' * 40, 'candidate': 'b' * 40, 'spec_commit': 'c' * 40,
            'issue': {'body_sha256': 'd' * 64}},
            'ticket_scope': {'project': 'product', 'iteration': 'm1'},
            'revision': {'spec': None, 'ticket_assignment': None, 'ticket_inputs': None},
            'result_disposition': {'checks_verified': True}, 'submitted_result': {'status': 'done', 'run_id': self.runtime['run_id']}}
        self.seal_handoff()

    def seal_handoff(self):
        self.assignment['handoff_digest'] = assignments.digest(self.assignment['handoff'])

    def refresh_checks(self):
        source = manifest(self.root / 'workspace')
        self.receipt = {'run_id': self.runtime['run_id'], 'candidate': source,
                        'provenance': 'controller_admitted_read_only_sandbox_checks',
                        'records': [{'command': 'python -m unittest discover -v', 'exit_code': 0,
                                     'stdout': 'LABELED local validator input; no execution claimed',
                                     'stderr': '', 'output_truncated': False}]}
        raw = json.dumps(self.receipt)
        (self.root / 'controller-checks.json').write_text(raw)
        self.runtime.update(candidate_sha256=assignments.digest(source), checks_sha256=hashlib.sha256(raw.encode()).hexdigest())
        return source

    def report(self, **changes):
        return {'contract': simplification.CONTRACT, 'adaptation': self.assignment['handoff']['adaptation'],
                'input_candidate_sha256': self.contract['input_candidate_sha256'],
                'candidate_sha256': self.runtime['candidate_sha256'], 'outcome': 'no-op',
                'behavior_preservation': 'preserved',
                'angles': {angle: 'LABELED local work input' for angle in simplification.ANGLES},
                'findings': [], **changes}

    def result(self, report, name='simplification-result', status='done'):
        content = report if isinstance(report, str) else json.dumps(report)
        return {'run_id': self.runtime['run_id'], 'status': status,
                'loads': [], 'work': ['LABELED local validator input; no worker execution'],
                'artifacts': [simplification.artifact(name, content)]}


class SimplificationValidatorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(directory.cleanup)
        self.evidence = LocalStageEvidence(Path(directory.name))

    def reject(self, report, code='invalid_result', **changes):
        with self.assertRaises(RepositoryError) as raised:
            simplification.validate_result(self.evidence.assignment, {**self.evidence.result(report), **changes})
        self.assertEqual(raised.exception.code, code)

    def test_result_pins_json_four_angles_and_behavior_matrix(self):
        e = self.evidence
        self.assertEqual(simplification.validate_result(e.assignment, e.result(e.report()))['outcome'], 'no-op')
        for change in ({'candidate_sha256': '0' * 64}, {'input_candidate_sha256': '0' * 64},
                       {'adaptation': 'unreviewed'}, {'angles': {'reuse': 'only one angle'}},
                       {'behavior_preservation': 'not-established'}, {'approval': True}):
            with self.subTest(change=change):
                self.reject(e.report(**change))
        for raw in ('{not JSON', '{"outcome":"cleanup","outcome":"no-op"}',
                    '{"nested":' + '[' * 2000 + '0' + ']' * 2000 + '}'):
            with self.subTest(raw=raw[:40]):
                self.reject(raw)
        self.reject(e.report(), run_id='uncorrelated')
        self.reject(e.report(), artifacts=[])
        finding = {'kind': 'correctness', 'path': 'hello.py', 'evidence': 'LABELED risk', 'proposal': 'Investigate first'}
        stage = simplification.validate_result(e.assignment, e.result(e.report(outcome='findings', findings=[finding])))
        self.assertEqual(stage['findings'], [finding])
        self.assertFalse(stage['approval'])
        self.assertFalse(stage['advance_allowed'])
        self.assertFalse(stage['native_execution_trusted'])
        self.reject(e.report(findings=[finding]))  # False approval hiding findings.
        e.contract['scope'] = []  # Empty ticket diff accepts unchanged no-op.
        self.assertEqual(simplification.validate_result(e.assignment, e.result(e.report()))['changed_paths'], [])
        e.contract['scope'] = ['hello.py']
        (e.root / 'workspace/hello.py').write_text('cleanup\n')
        e.refresh_checks()
        cleanup = simplification.validate_result(e.assignment, e.result(e.report(outcome='cleanup')))
        self.assertEqual(cleanup['changed_paths'], ['hello.py'])
        self.assertEqual(cleanup['candidate_sha256'], e.runtime['candidate_sha256'])
        self.assertFalse(cleanup['approval'])
        self.reject(e.report())  # False no-op changed the candidate.
        self.reject(e.report(outcome='findings', findings=[finding]))  # Applied risky proposal.
        (e.root / 'workspace/outside.py').write_text('outside scope\n')
        e.refresh_checks()
        self.reject(e.report(outcome='cleanup'), 'scope_violation')

    def test_private_receipt_implementation_status_and_drift_matrix(self):
        e = self.evidence
        e.assignment['handoff'].update(stage='implementation', repository='example/product', repository_id=123,
                                        workspace=str(e.root / 'workspace'), standards=[])
        e.seal_handoff()
        database = sqlite3.connect(e.root / 'local.sqlite')
        self.addCleanup(database.close)
        database.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
        database.execute('CREATE TABLE role_assignments (assignment_id TEXT, payload TEXT)')
        item = {'approval': {'operator_id': '42', 'reference': 'LABELED local input'}, 'execution_allowed': False, 'status': 'active'}
        database.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(item)))
        def stored(value):
            database.execute('DELETE FROM role_assignments')
            database.execute('INSERT INTO role_assignments VALUES (?,?)', (value['assignment_id'], json.dumps(value)))
            database.commit()
        def inspect():
            return simplification.implementation(database, 'product', 'm1', '42', e.assignment['assignment_id'])
        stored(e.assignment)
        self.assertEqual(inspect()[2], e.initial)
        prior, root, source, baseline, receipt = inspect()
        request = copy.deepcopy(prior['handoff'])
        request['preceding'] = simplification.preceding(prior, root, source, baseline, receipt)
        self.assertEqual(simplification.handoff(database, 'product', 'm1', '42', request)['scope'], ['hello.py'])
        for field in ('repository', 'repository_id', 'issue', 'spec_commit', 'baseline', 'candidate', 'standards', 'preceding'):
            with self.subTest(scope_field=field):
                altered = copy.deepcopy(request)
                if field == 'standards':
                    altered[field] = [simplification.artifact('AGENTS.md', 'Weakened standards')]
                elif field == 'preceding':
                    # Keep correlating evidence, but change its diff rather than removing it.
                    altered[field][-1] = simplification.artifact('implementation-diff', 'Wrong diff')
                else:
                    altered[field] = 'unrelated'
                with self.assertRaises(RepositoryError) as raised:
                    simplification.handoff(database, 'product', 'm1', '42', altered)
                self.assertEqual(raised.exception.code, 'scope_mismatch')
        for path, value in (('runtime.status', 'failed'), ('runtime.status', 'running'),
                            ('runtime.container_removed', False), ('submitted_result.status', 'stuck'),
                            ('result_disposition.checks_verified', False)):
            with self.subTest(path=path, value=value):
                altered = copy.deepcopy(e.assignment)
                group, field = path.split('.')
                altered[group][field] = value
                stored(altered)
                with self.assertRaises(RepositoryError) as raised:
                    inspect()
                self.assertEqual(raised.exception.code, 'implementation_unverified')
        stored(e.assignment)
        raw = (e.root / 'controller-checks.json').read_bytes()
        for change in ({'records': []}, {'provenance': 'worker-assertion'}, {'run_id': 'wrong'},
                       {'candidate': {}}, {'records': [{**e.receipt['records'][0], 'exit_code': 1}]},
                       {'records': [{**e.receipt['records'][0], 'exit_code': True}]},
                       {'records': [{**e.receipt['records'][0], 'output_truncated': True}]}):
            with self.subTest(change=change):
                (e.root / 'controller-checks.json').write_text(json.dumps({**e.receipt, **change}))
                with self.assertRaises(RepositoryError) as raised:
                    simplification.check_receipt(e.root, e.runtime)
                self.assertEqual(raised.exception.code, 'implementation_unverified')
        (e.root / 'controller-checks.json').write_bytes(raw + b'\n')
        with self.assertRaises(RepositoryError):
            inspect()
        (e.root / 'controller-checks.json').write_bytes(raw)
        for relative in ('workspace/hello.py', 'inputs/baseline/hello.py'):
            path = e.root / relative
            original = path.read_bytes()
            path.write_text('drift\n')
            with self.assertRaises(RepositoryError) as raised:
                inspect()
            self.assertEqual(raised.exception.code, 'implementation_unverified')
            path.write_bytes(original)
        # A truly empty implementation diff yields an empty controller-derived scope.
        (e.root / 'inputs/baseline/hello.py').write_bytes((e.root / 'workspace/hello.py').read_bytes())
        e.baseline = manifest(e.root / 'inputs/baseline')
        e.runtime['baseline_sha256'] = assignments.digest(e.baseline)
        (e.root / 'initial-source.json').write_text(json.dumps({'workspace': e.initial, 'baseline': e.baseline}))
        stored(e.assignment)
        prior, root, source, baseline, receipt = inspect()
        request = copy.deepcopy(prior['handoff'])
        request['preceding'] = simplification.preceding(prior, root, source, baseline, receipt)
        empty = simplification.handoff(database, 'product', 'm1', '42', request)
        self.assertEqual(empty['scope'], [])
        e.assignment['handoff']['simplification'] = e.contract = empty
        self.assertEqual(simplification.validate_result(e.assignment, e.result(e.report()))['changed_paths'], [])
        (e.root / 'stop').touch()
        with self.assertRaises(RepositoryError) as raised:
            inspect()
        self.assertEqual(raised.exception.code, 'cancelled')


if __name__ == '__main__':
    unittest.main()
