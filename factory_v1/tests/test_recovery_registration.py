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
        (self.root / 'state.db').chmod(0o600)
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
        item = self.stored()
        item['restart_reconciliation'] = {'status': 'blocked',
            'observations': {'tracker': None, 'attention': [], 'ownership': None},
            'blockers': [{'boundary': 'delivery', 'code': 'main_frozen'}]}
        self.save(item)
        return self.stored()

    def refused(self, code):
        before, rows = self.stored(), assignments.rows(self.db)
        with self.assertRaises(RepositoryError) as caught:
            self.register()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.stored(), before)
        self.assertEqual(assignments.rows(self.db), rows)
        self.github.request.assert_not_called()

    def test_registration_requires_fresh_reconciliation_matrix(self):
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

    def test_authority_monitor_and_incident_scope_matrix(self):
        original = self.blocked_health()
        authority_cases = [({'reference': ''}, 'invalid_recovery'), ({'evidence_id': '0' * 64}, 'recovery_conflict'),
                           ({'incident_id': 'other'}, 'recovery_held'), ({'issue': self.authorization['issue'] | {'id': 999}}, 'scope_mismatch'),
                           ({'removable_assignments': ['f' * 64]}, 'scope_mismatch'), ({'operator_id': 'wrong'}, 'invalid_recovery')]
        cases = [('authority-' + str(index), self.authorization | change, {}, 0o600, self.config['api_base'], code)
                 for index, (change, code) in enumerate(authority_cases)]
        cases += [
            ('missing-paths', {k: v for k, v in self.authorization.items() if k != 'paths'}, {}, 0o600, self.config['api_base'], 'invalid_recovery'),
            ('public-authority', self.authorization, {}, 0o644, self.config['api_base'], 'execution_required'),
            ('missing-monitor', self.authorization, {'delivery_monitor': None}, 0o600, self.config['api_base'], 'recovery_held'),
            ('monitor-digest', self.authorization, {'delivery_monitor': original['delivery_monitor'] | {'config_sha256': '0' * 64}}, 0o600, self.config['api_base'], 'invalid_delivery'),
            ('incident-evidence', self.authorization, {'main_incident': original['main_incident'] | {'latest_evidence': '0' * 64}}, 0o600, self.config['api_base'], 'invalid_delivery'),
            ('api-scope', self.authorization, {}, 0o600, 'https://wrong.test', 'scope_mismatch'),
        ]
        for label, authority, item_change, mode, api_base, code in cases:
            with self.subTest(case=label):
                self.save(original | copy.deepcopy(item_change))
                self.write_authority(authority)
                self.config_file.chmod(mode)
                self.github.base = api_base
                self.refused(code)

    def test_pending_effect_and_worker_absence_matrix(self):
        original = self.blocked_health()
        for blocker in [{'boundary': 'tracker', 'code': 'publication_uncertain'}, {'boundary': 'model', 'code': 'operation_uncertain'},
                        {'boundary': 'worker', 'code': 'worker_uncertain'}, {'boundary': 'restart', 'code': 'reconciliation_interrupted'}]:
            for blockers in ([blocker], [*original['restart_reconciliation']['blockers'], blocker]):
                self.save(original | {'restart_reconciliation': original['restart_reconciliation'] | {'blockers': blockers}})
                self.refused('reconciliation_required')
        self.save(original | {'restart_reconciliation': {'status': 'blocked'}}); self.refused('invalid_restart')

        self.save(original)
        for runtime in ({'status': 'running', 'container_removed': True}, {'status': 'failed', 'container_removed': False},
                        {'status': 'stopped', 'container_removed': None}):
            row = {'assignment_id': 'f' * 64, 'ticket_scope': {'project': 'p', 'iteration': 'i'},
                   'handoff': {'workspace': str(self.root / 'worker'), 'profile': {'home': str(self.root / 'home')}},
                   'runtime': runtime | {'artifacts': str(self.root / 'artifacts')}}
            with self.db: self.db.execute('INSERT OR REPLACE INTO role_assignments VALUES (?,?)', (row['assignment_id'], json.dumps(row)))
            with self.subTest(runtime=runtime): self.refused('run_conflict')

    def test_control_revision_and_last_read_drift_matrix(self):
        original = self.blocked_health()
        for label, change, code in [
            ('sidecar', {}, 'iteration_paused'),
            ('status', {'status': 'paused'}, 'iteration_paused'),
            ('revision', {'requirement_changes': {'revision': {'kind': 'material', 'status': 'held', 'affected': [20]}}}, 'requirements_held'),
        ]:
            with self.subTest(before_read=label):
                self.save(original | change)
                if label == 'sidecar':
                    controls.request(self.db, 'p', 'i', 'operator', 'pause', 'SIMULATED interruption')
                self.refused(code)
                if label == 'sidecar':
                    _, path = controls.paths(self.db, 'p', 'i')
                    assert path is not None
                    path.unlink()
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
