"""Combined public checkpoints from explicit records; no worker/stage journeys."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from factory_v1 import assignments, controls, effect_resolution, merge, planning, readiness_gate, tickets
from factory_v1.repositories import RepositoryError
from factory_v1.tests import test_readiness as readiness_fixtures, test_readiness_effects as effect_fixtures


class CombinedReadinessTests(readiness_fixtures.SupervisedFixture, unittest.TestCase):
    setUp = effect_fixtures.OperationIntegrationTests.setUp
    file = readiness_fixtures.CoreReadinessTests.file
    save = readiness_fixtures.CoreReadinessTests.save
    read = readiness_fixtures.CoreReadinessTests.read
    record = readiness_fixtures.CoreReadinessTests.record


    def reopen_fixture(self):
        row = self.record(self.config['slots'][0], 'complete')
        row['handoff'].update(repository='owner/product', repository_id=7)
        config = dict(operator_id='operator', repository='owner/product', repository_id=7,
                      api_base='https://api.github.com', base='main', branch='candidate',
                      candidate_assignment=row['assignment_id'], issue=1, pr_number=2,
                      head='c'*40, baseline='a'*40)
        row['merge'] = dict(status='incomplete', authority=config, close_allowed=False,
                            failure={'code': 'checks_failed'}, integrated_evidence={'integrated': 'd'*40})
        self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(row), row['assignment_id'])); self.db.commit()
        observed = dict(number=1, id=10, node_id='I1', repository_url='https://api.github.com/repos/owner/product', body='Exact issue', state='closed')
        repo = dict(id=7, full_name='owner/product', default_branch='main')
        pr = dict(number=2, head=dict(ref='candidate', sha=config['head'], repo=repo),
                  base=dict(ref='main', repo=repo), merged=True, state='closed', merge_commit_sha='d'*40)
        records = {'': repo, '/pulls/2': pr, '/issues/1': observed}
        api = Mock()
        api.read.side_effect = lambda path: deepcopy(records[path])
        self.health['sha'] = 'b'*40
        return row, config, observed, records, api

    def test_integrated_corrective_reopen_keeps_exact_scope_and_last_read_guards(self):
        row, config, observed, records, api = self.reopen_fixture()
        restore = self.snapshot()
        baseline = deepcopy((row, config, observed, records))
        original = self.readers.execution_support.side_effect
        source = Path(self.config['slots'][0]['skills'][0]['path']); raw = source.read_bytes()
        merge.reopen(api, row, config, observed, database=self.db)
        api.issue_state.assert_called_once_with('open', observed)
        restore()
        resolution = effect_resolution.bound(self.db, self.item, 'merge-reopen', row['assignment_id'])
        for operation, selection, bound in (('effect', None, None),
                ('work', {'merge_reopen': row['assignment_id']}, resolution),
                ('effect', None, resolution), ('effect', {'merge_reopen': 'unrelated'}, resolution)):
            with self.subTest(operation=operation, selection=selection), self.assertRaises(RepositoryError):
                readiness_gate.check(self.db, 'p', 'i', operation=operation, selection=selection, resolution=bound)
        for fault in ('issue', 'repository', 'unmerged', 'config', 'hold', 'pause', 'metadata-hold', 'final-policy'):
            with self.subTest(fault=fault):
                restore()
                row, config, observed, records = deepcopy(baseline)
                api.reset_mock()
                chosen = {**config, 'issue': 99} if fault == 'config' else config
                if fault == 'issue': records['/issues/1']['id'] = 999
                if fault == 'repository': records['']['id'] = 999
                if fault == 'unmerged': records['/pulls/2']['merged'] = False
                def hold():
                    self.item['restart_reconciliation'] = dict(status='blocked', observations={},
                        blockers=[dict(boundary='model', code='operation_uncertain')]); self.save()
                if fault == 'hold': hold()
                if fault == 'pause': controls.request(self.db, 'p', 'i', 'operator', 'pause', 'direct-component')
                def drift(*args):
                    result = original(*args)
                    hold()
                    return result
                if fault == 'metadata-hold': self.readers.execution_support.side_effect = drift
                def read(path):
                    result = deepcopy(records[path])
                    if fault == 'final-policy' and path == '/issues/1': source.write_bytes(raw + b'changed at final issue read')
                    return result
                api.read.side_effect = read
                try:
                    with self.assertRaises(RepositoryError) as caught:
                        merge.reopen(api, row, chosen, observed, database=self.db)
                    api.issue_state.assert_not_called()
                    if fault == 'final-policy': self.assertEqual(caught.exception.code, 'readiness_inputs_changed')
                    if fault == 'metadata-hold':
                        self.assertEqual(assignments.current(self.db, 'p', 'i', 'operator', require_active=False)['restart_reconciliation']['blockers'][0]['boundary'], 'model')
                finally: restore()


class BranchReopenReadinessTests(readiness_fixtures.SupervisedFixture, unittest.TestCase):
    setUp = effect_fixtures.OperationIntegrationTests.setUp
    file = readiness_fixtures.CoreReadinessTests.file
    save = readiness_fixtures.CoreReadinessTests.save
    read = readiness_fixtures.CoreReadinessTests.read
    record = readiness_fixtures.CoreReadinessTests.record

    def branch_fixture(self):
        import hashlib
        row, config, observed, records, api = CombinedReadinessTests.reopen_fixture(self)
        self.health['sha'] = 'a' * 40
        row.pop('merge')
        row['handoff']['stage'] = 'simplify'
        row['handoff']['issue']['body_sha256'] = hashlib.sha256(observed['body'].encode()).hexdigest()
        row['handoff_digest'] = assignments.digest(row['handoff'])
        config.update(execution_reference='LOCAL qualified prerequisites, not live execution',
            executions=[{'local': n} for n in range(4)], standards_assignment='1' * 64,
            spec_assignment='2' * 64, required_checks=[{'context': 'quality', 'app_id': 99}],
            protection_sha256='f' * 64, recovery=None, bearer='LOCAL-NONSECRET')
        path = self.file('branch-merge.json', config)
        records['/pulls/2'].update(merged=False, state='open')
        records['/pulls/2'].pop('merge_commit_sha')
        records['/git/ref/heads/candidate'] = {'ref': 'refs/heads/candidate',
                                             'object': {'type': 'commit', 'sha': config['head']}}
        row['merge_refusal'] = {'code': 'not_integrated', 'candidate': config['head'], 'revision': row['revision'],
            'correction': {'authority': {k: v for k, v in config.items() if k != 'bearer'},
                'config_path': str(path), 'binding': effect_resolution.branch_reopen_binding(row),
                'issue': deepcopy(observed), 'pr': deepcopy(records['/pulls/2'])}}
        self.db.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                        (json.dumps(row), row['assignment_id'])); self.db.commit()
        return row, config, observed, records, api

    def test_branch_corrective_reopen_grants_only_exact_current_operation(self):
        row, config, observed, records, api = self.branch_fixture()
        restore = self.snapshot()
        baseline = deepcopy((row, config, observed, records))
        source = Path(self.config['slots'][0]['skills'][0]['path']); raw = source.read_bytes()
        resolution = effect_resolution.bound(self.db, self.item, 'branch-reopen', row['assignment_id'])
        self.assertEqual(effect_resolution.permitted(self.db, 'p', 'i', resolution), set())
        selection = {'merge_reopen': row['assignment_id']}
        with readiness_gate.admission(self.db, 'p', 'i', operation='effect', resolution=resolution, selection=selection):
            readiness_gate.check(self.db, 'p', 'i', operation='effect', resolution=resolution, selection=selection)
        merge.reopen(api, row, config, observed, database=self.db)
        api.issue_state.assert_called_once_with('open', observed)
        self.assertNotIn('merge', assignments.rows(self.db)[0])
        restore()
        for operation, chosen in (('work', selection), ('effect', None), ('effect', {'merge_reopen': 'other'})):
            with self.subTest(operation=operation, selection=chosen), self.assertRaises(RepositoryError):
                readiness_gate.check(self.db, 'p', 'i', operation=operation, selection=chosen, resolution=resolution)
        for fault in ('health', 'permission', 'ownership', 'policy', 'hold'):
            with self.subTest(fault=fault):
                restore()
                row, config, observed, records = deepcopy(baseline)
                api.reset_mock()
                if fault == 'health': self.health['sha'] = 'b'*40
                if fault == 'permission': self.write_authority['operations'].remove('projects')
                if fault == 'ownership':
                    changed = deepcopy(row); changed['runtime']['status'] = 'running'
                    changed['merge_refusal']['correction']['binding'] = effect_resolution.branch_reopen_binding(changed)
                    self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(changed),)); self.db.commit()
                def read(path):
                    result = deepcopy(records[path])
                    if path == '/issues/1':
                        if fault == 'policy': source.write_bytes(raw + b'changed at final read')
                        if fault == 'hold':
                            self.item['restart_reconciliation'] = {'status': 'blocked', 'observations': {},
                                'blockers': [{'boundary': 'model', 'code': 'operation_uncertain'}]}; self.save()
                    return result
                api.read.side_effect = read
                try:
                    with self.assertRaises(RepositoryError): merge.reopen(api, row, config, observed, database=self.db)
                    api.issue_state.assert_not_called()
                finally: restore()
