"""Direct host-registration records and read-return guards; no lifecycle precursors."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import assignments, controls, delivery, recovery, repositories, requirements, restart, tickets
from factory_v1.repositories import RepositoryError


class RecoveryRegistrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.db = sqlite3.connect(self.root / 'state.db')
        self.addCleanup(self.db.close)
        self.db.executescript('''
            CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT);
            CREATE TABLE delivery_observations (project TEXT, iteration TEXT, id TEXT, payload TEXT);
            CREATE TABLE delivery_incidents (project TEXT, iteration TEXT, id TEXT, payload TEXT);
            CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT);
        ''')
        self.config = {'operator_id': 'operator', 'repository': 'owner/repo', 'repository_id': 7,
            'api_base': 'https://api.github.com', 'issue': 18,
            'required_checks': [{'context': 'quality', 'app_id': 99}],
            'required_workflows': [{'id': 11, 'event': 'push'}], 'environment': 'test', 'timeout': 2,
            'contract': {'reference': 'SIMULATED', 'revision': 'a' * 40,
                'health_url': 'https://example.test/health', 'acceptance': {'journey': True}}}
        self.evidence = {'repository': 'owner/repo', 'repository_id': 7, 'main': 'a' * 40,
            'checks': [], 'workflows': [], 'deployment': None, 'health': None,
            'status': 'failed', 'failure': 'SIMULATED unavailable delivery'}
        self.issue = {'number': 20, 'id': 200, 'node_id': 'I20', 'state': 'open', 'body': 'repair requirements'}
        pin = {k: self.issue[k] for k in ('number', 'id', 'node_id')} | {'body_sha256': hashlib.sha256(self.issue['body'].encode()).hexdigest()}
        self.authorization = {'operator_id': 'operator', 'reference': 'SIMULATED independently verified host scope',
            'incident_id': 'incident', 'evidence_id': assignments.digest(self.evidence), 'issue': pin,
            'paths': ['app.py'], 'removable_assignments': []}
        self.item = {'project_id': 'p', 'iteration_id': 'i', 'repository': 'owner/repo',
            'approval': {'operator_id': 'operator', 'reference': 'approved'}, 'status': 'active', 'execution_allowed': False,
            'origin': {'platform': 'discord', 'chat_id': '123', 'thread_id': '123', 'parent_chat_id': '456', 'scope_id': '789'},
            'delivery_monitor': {'config': self.config, 'config_sha256': assignments.digest(self.config),
                'latest': {'evidence_id': self.authorization['evidence_id'], 'status': 'failed', 'main': 'a' * 40}, 'sequence': 1},
            'main_incident': {'incident_id': 'incident', 'status': 'active', 'monitor_sha256': assignments.digest(self.config),
                'first_evidence': self.authorization['evidence_id'], 'latest_evidence': self.authorization['evidence_id'],
                'recovery_issue': None, 'recovery_assignment': None}}
        self.save(self.item)
        with self.db:
            self.db.execute('INSERT INTO delivery_observations VALUES (?,?,?,?)', ('p', 'i', self.authorization['evidence_id'], json.dumps(self.evidence)))
            self.db.execute('INSERT INTO delivery_incidents VALUES (?,?,?,?)', ('p', 'i', 'incident', json.dumps(self.item['main_incident'])))
        self.config_file = self.root / 'recovery.json'
        self.write_authority()
        self.github = Mock(base=self.config['api_base'])
        self.github.request.side_effect = AssertionError('Unexpected external call')
        self.reader = self.enterContext(patch.object(tickets.gh, 'read_issue', side_effect=lambda *a: copy.deepcopy(self.issue)))
        self.enterContext(patch.object(restart, 'tracker', return_value=None))
        self.enterContext(patch.object(delivery, 'sample', side_effect=lambda *a: {'evidence': copy.deepcopy(self.evidence)}))
        self.enterContext(patch.object(requirements, 'revalidate_inputs', return_value={}))
        for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket'):
            self.enterContext(patch(target, side_effect=AssertionError('Unexpected boundary: ' + target)))

    def save(self, item):
        with self.db:
            self.db.execute('DELETE FROM iterations')
            self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(item)))

    def stored(self):
        return json.loads(self.db.execute('SELECT payload FROM iterations').fetchone()[0])

    def write_authority(self, value=None):
        self.config_file.write_text(json.dumps(self.authorization if value is None else value))
        self.config_file.chmod(0o600)

    def reconcile(self):
        return restart.reconcile(self.db, 'p', 'i', 'operator', self.github)

    def register(self):
        return recovery.register(self.db, 'p', 'i', 'operator', self.config_file, self.github)

    def blocked_health(self):
        result = self.reconcile()
        self.assertEqual(result['blockers'], [{'boundary': 'delivery', 'code': 'main_frozen'}])
        self.assertEqual(result['status'], 'blocked')
        return self.stored()

    def refused(self, code):
        before, rows = self.stored(), assignments.rows(self.db)
        with self.assertRaises(RepositoryError) as caught:
            self.register()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.stored(), before)
        self.assertEqual(assignments.rows(self.db), rows)
        self.github.request.assert_not_called()

    def test_unregistered_failure_registers_then_requires_fresh_reconciliation_without_clearing_freeze(self):
        for status in ('failed', 'unknown'):
            self.evidence['status'] = status
            identity = assignments.digest(self.evidence)
            item = copy.deepcopy(self.item)
            item['delivery_monitor']['latest'].update(status=status, evidence_id=identity)
            item['main_incident'].update(first_evidence=identity, latest_evidence=identity)
            self.authorization['evidence_id'] = identity
            with self.db:
                self.db.execute('INSERT INTO delivery_observations VALUES (?,?,?,?)', ('p', 'i', identity, json.dumps(self.evidence)))
                self.db.execute('UPDATE delivery_incidents SET payload=?', (json.dumps(item['main_incident']),))
            self.save(item); self.write_authority()
            before = self.blocked_health()
            with self.subTest(status=status):
                record = self.register()
                saved = self.stored()
                self.assertEqual(saved['restart_reconciliation'], before['restart_reconciliation'])
                self.assertEqual(record['config'], self.authorization)
                self.assertEqual(record['evidence'], self.evidence)
                with self.assertRaises(RepositoryError) as caught: controls.require_active(self.db, 'p', 'i')
                self.assertEqual(caught.exception.code, 'reconciliation_required')
                result = self.reconcile()
                self.assertEqual(result['status'], 'ready')
                self.assertFalse(result['dispatch_allowed'])
                self.assertEqual(result['observations']['final-recovery'], recovery.contract(record))
                self.assertEqual(self.stored()['main_incident'], saved['main_incident'])
                with self.assertRaises(RepositoryError) as caught: restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github)
                self.assertEqual(caught.exception.code, 'main_frozen')
                self.assertEqual(assignments.rows(self.db), [])

    def test_missing_partial_stale_and_wrong_scope_authority_reaches_real_contract_guards(self):
        self.blocked_health()
        for mutation, code in [({'reference': ''}, 'invalid_recovery'), ({'evidence_id': '0' * 64}, 'recovery_conflict'),
                               ({'incident_id': 'other'}, 'recovery_held'), ({'issue': self.authorization['issue'] | {'id': 999}}, 'scope_mismatch'),
                               ({'removable_assignments': ['f' * 64]}, 'scope_mismatch'), ({'operator_id': 'wrong'}, 'invalid_recovery')]:
            with self.subTest(mutation=mutation): self.write_authority(self.authorization | mutation); self.refused(code)
        self.write_authority({k: v for k, v in self.authorization.items() if k != 'paths'}); self.refused('invalid_recovery')
        self.write_authority(); self.config_file.chmod(0o644); self.refused('execution_required')

    def test_monitor_and_persisted_incident_guards_remain_authoritative(self):
        original = self.blocked_health()
        for mutation, code in [({'delivery_monitor': None}, 'recovery_held'),
                               ({'delivery_monitor': original['delivery_monitor'] | {'config_sha256': '0' * 64}}, 'invalid_delivery'),
                               ({'main_incident': original['main_incident'] | {'latest_evidence': '0' * 64}}, 'invalid_delivery')]:
            self.save(original | mutation); self.refused(code)
        self.save(original); self.github.base = 'https://wrong.test'; self.refused('scope_mismatch')

    def test_pause_and_affected_revision_holds_refuse_registration(self):
        original = self.blocked_health()
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'SIMULATED interruption'); self.refused('iteration_paused')
        _, path = controls.paths(self.db, 'p', 'i'); path.unlink()
        self.save(original | {'status': 'paused'}); self.refused('iteration_paused')
        self.save(original | {'requirement_changes': {'revision': {'kind': 'material', 'status': 'held', 'affected': [20]}}}); self.refused('requirements_held')

    def test_unknown_other_effects_and_malformed_receipts_cannot_use_health_registration_lane(self):
        original = self.blocked_health()
        for blocker in [{'boundary': 'tracker', 'code': 'publication_uncertain'}, {'boundary': 'model', 'code': 'operation_uncertain'},
                        {'boundary': 'worker', 'code': 'worker_uncertain'}, {'boundary': 'restart', 'code': 'reconciliation_interrupted'}]:
            for blockers in ([blocker], [*original['restart_reconciliation']['blockers'], blocker]):
                self.save(original | {'restart_reconciliation': original['restart_reconciliation'] | {'blockers': blockers}})
                self.refused('reconciliation_required')
        self.save(original | {'restart_reconciliation': {'status': 'blocked'}}); self.refused('invalid_restart')

    def test_worker_absence_guard_is_reached_before_registration_parks_ownership(self):
        self.blocked_health()
        for runtime in ({'status': 'running', 'container_removed': True}, {'status': 'failed', 'container_removed': False},
                        {'status': 'stopped', 'container_removed': None}):
            row = {'assignment_id': 'f' * 64, 'ticket_scope': {'project': 'p', 'iteration': 'i'},
                   'handoff': {'workspace': str(self.root / 'worker'), 'profile': {'home': str(self.root / 'home')}},
                   'runtime': runtime | {'artifacts': str(self.root / 'artifacts')}}
            with self.db: self.db.execute('INSERT OR REPLACE INTO role_assignments VALUES (?,?)', (row['assignment_id'], json.dumps(row)))
            with self.subTest(runtime=runtime): self.refused('run_conflict')

    def test_pause_and_restart_or_revision_drift_at_last_issue_read_admit_no_registration(self):
        original = self.blocked_health()
        for drift, code in [('pause', 'iteration_paused'), ('restart', 'reconciliation_required'), ('revision', 'requirements_held')]:
            self.save(original)
            def read(*args):
                if drift == 'pause': controls.request(self.db, 'p', 'i', 'operator', 'pause', 'SIMULATED last read')
                else:
                    item = self.stored()
                    if drift == 'restart': item['restart_reconciliation']['blockers'].append({'boundary': 'tracker', 'code': 'publication_uncertain'})
                    else: item['requirement_changes'] = {'revision': {'kind': 'material', 'status': 'held', 'affected': [20]}}
                    self.save(item)
                return copy.deepcopy(self.issue)
            self.reader.side_effect = read
            with self.subTest(drift=drift), self.assertRaises(RepositoryError) as caught: self.register()
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn('main_recoveries', self.stored())
            self.assertIsNone(self.stored()['main_incident']['recovery_issue'])
            if drift == 'pause':
                _, path = controls.paths(self.db, 'p', 'i'); path.unlink()
