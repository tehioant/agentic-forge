"""Direct engineering seams; legacy sibling fixture helpers are not discovered."""
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
                        'container_removed': True, 'baseline_sha256': assignments.digest(self.baseline),
                        'controller_pid': 2147483647, 'controller_start': 'LABELED exited fixture owner'}
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
            'ticket_scope': {'project': 'product', 'iteration': 'm1', 'issue_number': 10},
            'revision': {'spec': {}, 'ticket_assignment': None, 'ticket_inputs': None},
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
                changed_raw = json.dumps({**e.receipt, **change})
                (e.root / 'controller-checks.json').write_text(changed_raw)
                e.runtime['checks_sha256'] = hashlib.sha256(changed_raw.encode()).hexdigest()
                with self.assertRaises(RepositoryError) as raised:
                    simplification.check_receipt(e.root, e.runtime)
                self.assertEqual(raised.exception.code, 'implementation_unverified')
        e.runtime['checks_sha256'] = hashlib.sha256(raw).hexdigest()
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
        e.assignment['handoff']['simplification'] = e.contract = {
            **e.contract, 'input_candidate_sha256': assignments.digest(e.initial), 'scope': []}
        self.assertEqual(simplification.validate_result(e.assignment, e.result(e.report()))['changed_paths'], [])
        (e.root / 'stop').touch()
        with self.assertRaises(RepositoryError) as raised:
            inspect()
        self.assertEqual(raised.exception.code, 'cancelled')


from unittest.mock import patch


class SimplificationComponentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        evidence_root = self.root / 'evidence'
        evidence_root.mkdir(mode=0o700)
        self.e = LocalStageEvidence(evidence_root)
        self.e.assignment['handoff'].update(stage='implementation', repository='example/product', repository_id=123,
            workspace=str(evidence_root / 'workspace'), standards=[])
        self.e.seal_handoff()
        self.db = sqlite3.connect(self.root / 'state.sqlite')
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT);'
                             'CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT);')
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps({
            'project_id': 'product', 'iteration_id': 'm1', 'status': 'active', 'execution_allowed': False,
            'approval': {'operator_id': '42', 'reference': 'local fixture'}})))
        self.db.execute('INSERT INTO role_assignments VALUES (?,?)',
                        (self.e.assignment['assignment_id'], json.dumps(self.e.assignment)))
        self.db.commit()

    def test_real_staging_copies_verified_candidate_and_keeps_baseline(self):
        e = self.e
        root, source, baseline = e.root, e.initial, e.baseline
        contract = {**e.contract, 'implementation_assignment': e.assignment['assignment_id']}
        destination = self.root / 'staged'
        destination.mkdir()
        (destination / 'workspace').mkdir()
        (destination / 'workspace/host-head.py').write_text('Must not be staged')
        pins = {'baseline': baseline}
        row = {'handoff': {'simplification': contract}}
        simplification.copy_candidate(self.db, 'product', 'm1', '42', row, destination, pins)
        self.assertEqual(manifest(destination / 'workspace'), source)
        self.assertEqual(pins['workspace'], source)
        self.assertEqual(manifest(root / 'inputs/baseline'), baseline)
        self.assertFalse((destination / 'workspace/host-head.py').exists())
        with self.assertRaises(RepositoryError) as refusal:
            simplification.copy_candidate(self.db, 'product', 'm1', '42', row, destination, {'baseline': {}})
        self.assertEqual(refusal.exception.code, 'scope_mismatch')

    def test_resealed_receipt_semantics_not_only_raw_checksum(self):
        e = self.e
        for change in ({'records': []}, {'provenance': 'worker-assertion'}, {'run_id': 'wrong'},
            {'records': [{**e.receipt['records'][0], 'exit_code': 1}]},
            {'records': [{**e.receipt['records'][0], 'exit_code': True}]},
            {'records': [{**e.receipt['records'][0], 'output_truncated': True}]}):
            raw = json.dumps({**e.receipt, **change})
            (e.root / 'controller-checks.json').write_text(raw)
            e.runtime['checks_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
            with self.subTest(change=change):
                with self.assertRaises(RepositoryError) as refusal:
                    simplification.check_receipt(e.root, e.runtime)
                self.assertEqual(refusal.exception.code, 'implementation_unverified')
        e.refresh_checks()
        self.assertEqual(simplification.check_receipt(e.root, e.runtime)[0], e.initial)

    def test_stage_result_only_accepts_launcher_correlation_or_exact_completed_replay(self):
        e = self.e
        e.assignment['handoff'].update(stage='simplify', skills=[], input_loads={},
            profile={'name': 'local-simplifier', 'home': str(self.root / 'profile')}, preceding=[],
            capabilities=['read_workspace', 'scratch', 'model', 'write_workspace'],
            result_contract=assignments.CONTRACT, claim_id=e.assignment['claim_id'])
        e.seal_handoff()
        result = e.result(e.report())
        result.update(assignment_id=e.assignment['assignment_id'], handoff_digest=e.assignment['handoff_digest'],
                      claim_id=e.assignment['claim_id'], tests=[{'command': 'local check', 'result': 'Local input'}])
        result['artifacts'].append(simplification.artifact('stage-evidence', 'Local validator input'))
        e.assignment['submitted_result'] = result
        self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(e.assignment),))
        self.db.commit()
        # Eligibility is an already-admitted prerequisite; stage/result guards remain real.
        with patch.object(assignments, 'prepare', return_value={'dry_run': True}):
            stored = assignments.store_result(self.db, 'product', 'm1', '42', e.assignment['assignment_id'], result, None)
            self.assertEqual(stored['simplification_result']['outcome'], 'no-op')
            self.assertFalse(stored['result_disposition']['trusted_execution'])
            self.assertFalse(stored['result_disposition']['advance_allowed'])
            replacement = {**result, 'work': ['Caller replacement, not executed work']}
            with self.assertRaises(RepositoryError) as refusal:
                assignments.store_result(self.db, 'product', 'm1', '42', e.assignment['assignment_id'], replacement, None)
            self.assertEqual(refusal.exception.code, 'invalid_result')
            for status in ('failed', 'running', 'unconfirmed', 'stopped'):
                row = copy.deepcopy(stored)
                row['runtime']['status'] = status
                self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),))
                self.db.commit()
                with self.subTest(status=status):
                    with self.assertRaises(RepositoryError) as refusal:
                        assignments.store_result(self.db, 'product', 'm1', '42', e.assignment['assignment_id'], result, None)
                    self.assertEqual(refusal.exception.code, 'invalid_result')
                self.assertEqual(assignments.rows(self.db)[0]['submitted_result'], result)

    def test_controller_receipt_stop_and_sidecar_pause_refuse_without_staging(self):
        from factory_v1 import controls
        e = self.e
        controls.request(self.db, 'product', 'm1', '42', 'pause', 'local stage pause')
        # Inspection remains available while paused; preparation is separately gated.
        self.assertEqual(simplification.implementation(self.db, 'product', 'm1', '42', e.assignment['assignment_id'])[2], e.initial)
        with self.assertRaises(RepositoryError) as refusal:
            assignments.current(self.db, 'product', 'm1', '42')
        self.assertEqual(refusal.exception.code, 'iteration_paused')
        (e.root / 'stop').touch()
        with self.assertRaises(RepositoryError) as refusal:
            simplification.implementation(self.db, 'product', 'm1', '42', e.assignment['assignment_id'])
        self.assertEqual(refusal.exception.code, 'cancelled')


if __name__ == "__main__":
    unittest.main()
