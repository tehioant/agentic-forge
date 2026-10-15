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

    def test_real_nested_checkpoint_with_terminal_slots_and_zero_allowance(self):
        for slot in self.config['slots']:
            self.record(slot, 'complete')
        self.db.execute('UPDATE model_grants SET remaining=0'); self.db.commit()
        self.item['ticket_work']['status'] = 'published'
        tickets.effect_checkpoint(self.db, self.item)
        self.assertEqual(json.loads(self.db.execute('SELECT payload FROM iterations').fetchone()[0])['ticket_work']['status'], 'published')
        before = self.db.execute('SELECT payload FROM iterations').fetchone()[0]
        with self.assertRaises(RepositoryError):
            planning.checkpoint(self.db, self.item)
        self.assertEqual(self.db.execute('SELECT payload FROM iterations').fetchone()[0], before)

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

    def test_public_corrective_reopen_failed_health_and_exact_scope_refusals(self):
        row, config, observed, records, api = self.reopen_fixture()
        merge.reopen(api, row, config, observed, database=self.db)
        api.issue_state.assert_called_once_with('open', observed)
        with self.assertRaises(RepositoryError):
            readiness_gate.check(self.db, 'p', 'i', operation='effect')
        resolution = effect_resolution.bound(self.db, self.item, 'merge-reopen', row['assignment_id'])
        for operation, selection in (('work', {'merge_reopen': row['assignment_id']}), ('effect', None),
                                     ('effect', {'merge_reopen': 'unrelated'})):
            with self.subTest(operation=operation, selection=selection), self.assertRaises(RepositoryError):
                readiness_gate.check(self.db, 'p', 'i', operation=operation, selection=selection, resolution=resolution)
        for mutation in ('issue', 'repository', 'unmerged', 'config', 'hold', 'pause'):
            with self.subTest(mutation=mutation):
                api.issue_state.reset_mock()
                old = deepcopy(records)
                if mutation == 'issue': records['/issues/1']['id'] = 999
                if mutation == 'repository': records['']['id'] = 999
                if mutation == 'unmerged': records['/pulls/2']['merged'] = False
                chosen = dict(config, issue=99) if mutation == 'config' else config
                if mutation == 'hold':
                    self.item['restart_reconciliation'] = dict(status='blocked', observations={}, blockers=[dict(boundary='model', code='operation_uncertain')]); self.save()
                if mutation == 'pause': controls.request(self.db, 'p', 'i', 'operator', 'pause', 'direct-component')
                with self.assertRaises(RepositoryError):
                    merge.reopen(api, row, chosen, observed, database=self.db)
                api.issue_state.assert_not_called()
                records.clear(); records.update(old)
                self.item.pop('restart_reconciliation', None); self.save()

    def test_public_corrective_reopen_last_read_preserves_independent_hold(self):
        row, config, observed, records, api = self.reopen_fixture()
        original = self.readers.execution_support.side_effect
        def drift(*args):
            result = original(*args)
            self.item['restart_reconciliation'] = dict(status='blocked', observations={}, blockers=[dict(boundary='model', code='operation_uncertain')])
            self.save()
            return result
        self.readers.execution_support.side_effect = drift
        with self.assertRaises(RepositoryError):
            merge.reopen(api, row, config, observed, database=self.db)
        api.issue_state.assert_not_called()
        self.assertEqual(assignments.current(self.db, 'p', 'i', 'operator', require_active=False)['restart_reconciliation']['blockers'][0]['boundary'], 'model')

    def test_public_corrective_reopen_final_tracker_read_rechecks_policy(self):
        row, config, observed, records, api = self.reopen_fixture()
        source = Path(self.config['slots'][0]['skills'][0]['path'])
        raw = source.read_bytes()
        def read(path):
            result = deepcopy(records[path])
            if path == '/issues/1':
                source.write_bytes(raw + b'changed at final issue read')
            return result
        api.read.side_effect = read
        try:
            with self.assertRaises(RepositoryError) as caught:
                merge.reopen(api, row, config, observed, database=self.db)
            self.assertEqual(caught.exception.code, 'readiness_inputs_changed')
            api.issue_state.assert_not_called()
        finally:
            source.write_bytes(raw)


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

    def test_branch_public_reopen_real_nested_readiness_grants_no_other_operation(self):
        row, config, observed, records, api = self.branch_fixture()
        resolution = effect_resolution.bound(self.db, self.item, 'branch-reopen', row['assignment_id'])
        self.assertEqual(effect_resolution.permitted(self.db, 'p', 'i', resolution), set())
        selection = {'merge_reopen': row['assignment_id']}
        with readiness_gate.admission(self.db, 'p', 'i', operation='effect', resolution=resolution, selection=selection):
            readiness_gate.check(self.db, 'p', 'i', operation='effect', resolution=resolution, selection=selection)
        merge.reopen(api, row, config, observed, database=self.db)
        api.issue_state.assert_called_once_with('open', observed)
        for operation, chosen in (('work', selection), ('effect', None), ('effect', {'merge_reopen': 'other'})):
            with self.subTest(operation=operation, selection=chosen), self.assertRaises(RepositoryError):
                readiness_gate.check(self.db, 'p', 'i', operation=operation, selection=chosen, resolution=resolution)
        self.assertNotIn('merge', assignments.rows(self.db)[0])

    def test_branch_public_reopen_never_bypasses_health_permission_or_ownership(self):
        row, config, observed, records, api = self.branch_fixture()
        original = self.readers.execution_support.side_effect
        for fault in ('health', 'permission', 'ownership'):
            with self.subTest(fault=fault):
                if fault == 'health': self.health['sha'] = 'b' * 40
                elif fault == 'permission': self.write_authority['operations'].remove('projects')
                else:
                    changed = deepcopy(row)
                    changed['runtime']['status'] = 'running'
                    changed['merge_refusal']['correction']['binding'] = effect_resolution.branch_reopen_binding(changed)
                    self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(changed),)); self.db.commit()
                with self.assertRaises(RepositoryError): merge.reopen(api, row, config, observed, database=self.db)
                api.issue_state.assert_not_called()
                self.health['sha'] = 'a' * 40
                if fault == 'permission': self.write_authority['operations'].append('projects')
        self.readers.execution_support.side_effect = original

    def test_branch_final_tracker_read_keeps_current_local_policy_and_independent_holds(self):
        row, config, observed, records, api = self.branch_fixture()
        source = Path(self.config['slots'][0]['skills'][0]['path'])
        raw = source.read_bytes()
        for fault in ('policy', 'hold'):
            def read(path):
                result = deepcopy(records[path])
                if path == '/issues/1':
                    if fault == 'policy': source.write_bytes(raw + b'changed at final read')
                    else:
                        self.item['restart_reconciliation'] = {'status': 'blocked', 'observations': {},
                            'blockers': [{'boundary': 'model', 'code': 'operation_uncertain'}]}
                        self.save()
                return result
            api.read.side_effect = read
            try:
                with self.subTest(fault=fault), self.assertRaises(RepositoryError):
                    merge.reopen(api, row, config, observed, database=self.db)
                api.issue_state.assert_not_called()
            finally:
                source.write_bytes(raw)
