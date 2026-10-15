"""Narrow restart components over persisted rows and independent readbacks."""
import copy
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch
from contextlib import closing, redirect_stdout
from factory_v1 import assignments, controls, restart
from factory_v1.repositories import RepositoryError


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = str(Path(self.directory.name) / 'state.db')
        self.db = sqlite3.connect(self.state)
        self.addCleanup(self.db.close)
        self.item = {'project_id': 'p', 'iteration_id': 'i', 'repository': 'owner/repo',
                     'approval': {'operator_id': 'operator', 'reference': 'approved'},
                     'status': 'active', 'execution_allowed': False}
        self.db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
        self.db.execute('CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT)')
        self.save_item()
        self.github = Mock(base='https://api.github.com')
        self.repo = {'id': 123, 'full_name': 'owner/repo', 'name': 'repo',
                     'owner': {'login': 'owner'}, 'private': True}
        self.github.request.return_value = self.repo
        for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket',
                       'factory_v1.repositories.GitHub.request', 'factory_v1.messaging.HermesTransport.send'):
            self.enterContext(patch(target, side_effect=AssertionError('Unexpected external execution: ' + target)))

    def save_item(self):
        self.db.execute('DELETE FROM iterations')
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(self.item)))
        self.db.commit()

    def stored(self):
        return json.loads(self.db.execute('SELECT payload FROM iterations').fetchone()[0])

    def assignment(self, status='running'):
        root = Path(self.directory.name)
        artifacts = root / 'artifacts'
        artifacts.mkdir(mode=0o700, exist_ok=True)
        (artifacts / 'worker-report.json').write_text('{"status":"done","tests":"green"}')
        source = root / 'source'
        (source / '.git').mkdir(parents=True, exist_ok=True)
        (source / '.git/HEAD').write_text('a' * 40 + '\n')
        handoff = {'stage': 'implementation', 'profile': {'name': 'writer', 'home': str(root / 'profile')},
                   'workspace': str(source), 'baseline': 'a' * 40, 'candidate': None,
                   'repository': 'owner/repo', 'repository_id': 123,
                   'issue': {'number': 10, 'id': 100, 'node_id': 'I10',
                             'body_sha256': hashlib.sha256(b'Pinned issue').hexdigest()}}
        self.row = {'assignment_id': 'c' * 64, 'claim_id': 'claim', 'handoff': handoff,
                    'handoff_digest': assignments.digest(handoff),
                    'ticket_scope': {'project': 'p', 'iteration': 'i', 'repository': 'owner/repo',
                                     'repository_id': 123, 'issue_number': 10, 'issue_id': 100},
                    'revision': {'spec': {}, 'ticket_assignment': None, 'ticket_inputs': None},
                    'runtime': {'run_id': 'run-1', 'container': 'factory-run-1', 'controller_pid': 111,
                                'controller_start': 'original-owner', 'artifacts': str(artifacts), 'status': status}}
        self.db.execute('INSERT OR REPLACE INTO role_assignments VALUES (?,?)', (self.row['assignment_id'], json.dumps(self.row)))
        self.db.commit()
        return self.row

    def observed_restart(self, *, recover=None):
        self.issue_observation = {'number': 10, 'id': 100, 'node_id': 'I10', 'body': 'Pinned issue'}
        with patch.object(restart, 'process_identity', return_value=None), \
             patch.object(restart.Docker, 'inspect', return_value=None), \
             patch('factory_v1.requirements.revalidate_inputs', return_value={}), \
             patch.object(restart.tickets.gh, 'read_issue', return_value=self.issue_observation):
            self.github.request.side_effect = lambda path, *_: {'sha': 'a' * 40} if '/git/commits/' in path else self.repo
            return restart.reconcile(self.db, 'p', 'i', 'operator', self.github, recover=recover)

    def reset_state(self, item):
        self.db.rollback()
        self.db.execute('DELETE FROM role_assignments')
        self.item = copy.deepcopy(item)
        self.save_item()
        _, path = controls.paths(self.db, 'p', 'i')
        assert path is not None
        path.unlink(missing_ok=True)
        self.github.request.reset_mock(side_effect=True)
        self.github.request.return_value = self.repo

    def test_fresh_reconciliation_and_durable_pause_matrix(self):
        baseline = copy.deepcopy(self.item)
        for label, failure, code in [
            ('unreconciled', None, 'reconciliation_required'),
            ('repository-unavailable', RepositoryError('github_unavailable', 'unavailable'), 'github_unavailable'),
            ('controller-loss', SystemExit('SIMULATED controller loss at read-return'), 'reconciliation_interrupted'),
            ('lost-worker', None, 'reconciliation_required'),
            ('reconciled', None, None),
        ]:
            with self.subTest(case=label):
                self.reset_state(baseline)
                if label not in ('lost-worker', 'controller-loss'):
                    controls.request(self.db, 'p', 'i', 'operator', 'pause', 'intentional')
                self.github.request.side_effect = failure
                if label == 'lost-worker':
                    self.assignment()
                if label == 'controller-loss':
                    with self.assertRaises(SystemExit):
                        restart.reconcile(self.db, 'p', 'i', 'operator', self.github)
                elif label == 'reconciled':
                    with closing(sqlite3.connect(self.state)) as fresh:
                        result = restart.reconcile(fresh, 'p', 'i', 'operator', self.github)
                    self.assertEqual(result['status'], 'ready')
                    self.assertFalse(result['dispatch_allowed'])
                else:
                    with patch.object(restart, 'process_identity', return_value=None):
                        seam = controls.require_active if label == 'lost-worker' else controls.resume
                        args = (self.db, 'p', 'i') if label == 'lost-worker' else (
                            self.db, 'p', 'i', 'operator', 'explicit', *([self.github] if failure else []))
                        with self.assertRaises(RepositoryError) as raised:
                            seam(*args)
                    self.assertEqual(raised.exception.code, 'reconciliation_required')
                if label in ('repository-unavailable', 'controller-loss'):
                    boundary = 'tracker' if label == 'repository-unavailable' else 'restart'
                    self.assertEqual(self.stored()['restart_reconciliation']['blockers'], [{'boundary': boundary, 'code': code}])
                if label not in ('lost-worker', 'controller-loss'):
                    self.assertTrue(controls.blocked(self.db, 'p', 'i'))
                if label == 'controller-loss':
                    with closing(sqlite3.connect(self.state)) as fresh, self.assertRaises(RepositoryError):
                        controls.require_active(fresh, 'p', 'i')
                if label == 'reconciled':
                    self.assertTrue(controls.resume(self.db, 'p', 'i', 'operator', 'explicit', self.github)['dispatch_allowed'])
                    self.assertEqual(self.stored()['status'], 'active')

    def test_repository_effect_adopt_never_recreate_matrix(self):
        baseline = copy.deepcopy(self.item)
        for present in (True, False):
            with self.subTest(present=present):
                self.reset_state(baseline)
                self.item['repository_intent'] = {'mode': 'new', 'marker': 'approved marker'}
                self.item['repository_onboarding'] = {'status': 'pending', 'api_base': self.github.base}
                self.save_item()
                self.repo['description'] = 'approved marker'
                self.github.request.side_effect = lambda path, *_: {'login': 'owner'} if path == '/user' else (self.repo if present else None)
                result = restart.reconcile(self.db, 'p', 'i', 'operator', self.github)
                if present:
                    self.assertEqual(result['status'], 'ready')
                    self.assertEqual(self.stored()['repository_onboarding']['metadata']['id'], 123)
                else:
                    self.assertEqual(result['blockers'], [{'boundary': 'tracker', 'code': 'creation_uncertain'}])
                    self.assertEqual(self.stored()['repository_onboarding']['status'], 'pending')
                self.assertTrue(all(all(value is None for value in call.args[1:]) for call in self.github.request.call_args_list))

    def test_worker_recovery_retains_budget_and_claim_identity_matrix(self):
        baseline = copy.deepcopy(self.item)
        for attempt, released in ((None, False), ('another-run', False), ('run-1', True)):
            with self.subTest(attempt=attempt):
                self.reset_state(baseline)
                row = self.assignment()
                retained = (Path(row['runtime']['artifacts']) / 'worker-report.json').read_bytes()
                self.item['budget'] = {'attempts': 2, 'deadline': 54321}
                self.save_item()
                recovery = (row['assignment_id'], attempt, 'explicit infrastructure continuation') if attempt else None
                result = self.observed_restart(recover=recovery)
                self.assertEqual(result['status'], 'blocked' if attempt == 'another-run' else 'ready')
                saved = assignments.rows(self.db)[0]
                self.assertEqual(restart.released_claim(saved), released)
                self.assertEqual(saved['runtime']['run_id'], 'run-1')
                self.assertEqual(self.stored()['budget'], self.item['budget'])
                self.assertNotIn('result_disposition', saved)
                self.assertNotIn('submitted_result', saved)
                self.assertEqual((Path(row['runtime']['artifacts']) / 'worker-report.json').read_bytes(), retained)
                if attempt is None:
                    self.assertEqual(saved['runtime']['status'], 'stopped')
                    self.assertTrue(saved['runtime']['recoverable'])
                if released:
                    self.assertEqual(assignments.ticket_claims(self.db, {'issue_number': 11}), [])
                    with self.assertRaises(RepositoryError):
                        assignments.require_result_active(saved)
                    request = {'stage': 'implementation', 'claim_id': 'fresh-claim', 'profile': copy.deepcopy(row['handoff']['profile'])}
                    assignments.claims(self.db, request, row['ticket_scope'], 'd' * 64)
                    fresh = copy.deepcopy(row)
                    fresh.update(assignment_id='d' * 64, claim_id='fresh-claim')
                    fresh.pop('runtime')
                    self.db.execute('INSERT INTO role_assignments VALUES (?,?)', (fresh['assignment_id'], json.dumps(fresh)))
                    self.db.commit()
                    with self.assertRaises(RepositoryError):
                        assignments.claims(self.db, request | {'claim_id': 'third-claim'}, row['ticket_scope'], 'e' * 64)

    def test_resume_last_read_drift_and_pause_matrix(self):
        self.assignment()
        self.item['delivery_monitor'] = {'SIMULATED': 'configured health'}
        self.save_item()
        def health(*_):
            self.issue_observation['body'] = 'Concurrent requirement edit'
            return {'evidence': {'status': 'healthy'}}
        with patch.object(restart.delivery, 'sample', side_effect=health):
            result = self.observed_restart()
        self.assertEqual(result['status'], 'blocked')
        self.assertIn({'boundary': self.row['assignment_id'] + ':final-requirements', 'code': 'scope_mismatch'}, result['blockers'])
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'initial pause')
        def revalidate(*_):
            controls.request(self.db, 'p', 'i', 'operator', 'emergency-stop', 'later stop')
            return {'status': 'ready'}
        with patch.object(restart, 'reconcile', side_effect=revalidate), self.assertRaises(RepositoryError) as raised:
            controls.resume(self.db, 'p', 'i', 'operator', 'resume original pause', self.github)
        self.assertEqual(raised.exception.code, 'control_conflict')
        saved = controls.read(self.db, 'p', 'i')
        assert saved is not None
        self.assertEqual(saved['action'], 'emergency-stop')

    def test_worker_scope_liveness_and_process_identity_matrix(self):
        row = self.assignment()
        row['ticket_scope']['repository_id'] = 999
        self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),))
        self.db.commit()
        with patch.object(restart.Docker, 'inspect') as inspect:
            result = restart.reconcile(self.db, 'p', 'i', 'operator', self.github, recover=(row['assignment_id'], 'run-1', 'explicit'))
        self.assertEqual(result['status'], 'blocked')
        inspect.assert_not_called()
        self.assertFalse(restart.released_claim(assignments.rows(self.db)[0]))
        for observation in ({'Name': '/factory-run-1', 'State': {'Running': True}},
                            {'Name': '/wrong-worker', 'State': {'Running': False}},
                            {'Name': '/factory-run-1', 'State': {'Running': 'unknown'}}):
            with self.subTest(observation=observation), patch.object(restart, 'process_identity', return_value=None), \
                 patch.object(restart.Docker, 'inspect', return_value=observation), self.assertRaises(RepositoryError):
                restart.worker(copy.deepcopy(row))
        from factory_v1 import sandbox_guard
        stat = '111 (name with spaces) ' + ' '.join(['R'] + ['0'] * 18 + ['12345'])
        identities = []
        for boot in ('boot-one', 'boot-two'):
            with patch.object(Path, 'read_text', side_effect=[stat, boot + '\n']):
                identities.append(sandbox_guard.process_identity(111))
        first, second = identities
        self.assertEqual(first, 'boot-one:12345')
        self.assertNotEqual(first, second)
        self.assertTrue(sandbox_guard.same_process(first, '12345'), 'Legacy matching owner stays conservatively live')
        self.assertFalse(sandbox_guard.same_process(second, first), 'Exact new owners cannot cross a boot boundary')
        self.assertFalse(sandbox_guard.same_process(None, '12345'))
        with patch.object(Path, 'read_text', side_effect=[stat.replace(') R', ') Z'), 'boot-one\n']):
            self.assertIsNone(sandbox_guard.process_identity(111))

    def test_workspace_pin_formats_matrix(self):
        row = self.assignment()
        git = Path(row['handoff']['workspace']) / '.git'
        api = Mock()
        api.read.return_value = {'sha': 'a' * 40}
        self.assertEqual(restart.workspace(row, api)['head'], 'a' * 40)
        (git / 'HEAD').write_text('ref: refs/heads/topic\n')
        (git / 'refs/heads').mkdir(parents=True)
        (git / 'refs/heads/topic').write_text('a' * 40 + '\n')
        self.assertEqual(restart.workspace(row, api)['branch'], 'refs/heads/topic')
        (git / 'refs/heads/topic').unlink()
        (git / 'packed-refs').write_text('a' * 40 + ' refs/heads/topic\n')
        self.assertEqual(restart.workspace(row, api)['head'], 'a' * 40)
        (git / 'packed-refs').write_text('b' * 40 + ' refs/heads/topic\n')
        with self.assertRaises(RepositoryError):
            restart.workspace(row, api)

    def test_uncertain_health_and_attention_hold_matrix(self):
        baseline = copy.deepcopy(self.item)
        for boundary, observations, code in [
            ('attention', [{'state': 'uncertain'}], 'attention_uncertain'),
            ('delivery', {'evidence': {'status': 'unknown'}}, 'main_frozen'),
        ]:
            with self.subTest(boundary=boundary):
                self.reset_state(baseline)
                if boundary == 'delivery':
                    self.item['delivery_monitor'] = {'config': 'SIMULATED configured contract'}
                    self.save_item()
                target = restart.attention if boundary == 'attention' else restart.delivery
                with patch.object(target, 'records' if boundary == 'attention' else 'sample', return_value=observations) as observed:
                    result = restart.reconcile(self.db, 'p', 'i', 'operator', self.github)
                self.assertEqual(result['blockers'], [{'boundary': boundary, 'code': code}])
                if boundary == 'delivery':
                    observed.assert_called_once_with(self.db, 'p', 'i', 'operator')
                else:
                    with self.assertRaises(RepositoryError):
                        controls.require_active(self.db, 'p', 'i')
        self.reset_state(baseline)
        self.assignment()
        with patch.object(restart, 'reconcile', return_value={'status': 'blocked'}) as read, self.assertRaises(RepositoryError):
            restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github)
        read.assert_called_once()
        self.reset_state(baseline)
        for record in ({'status': 'ready'}, {'status': 'ready', 'observations': {}, 'blockers': [{'code': 'unknown'}]}, []):
            self.item['restart_reconciliation'] = record
            self.save_item()
            with self.subTest(record=record), self.assertRaises(RepositoryError):
                controls.require_active(self.db, 'p', 'i')

    def test_profile_and_scope_ownership_matrix(self):
        row = self.assignment()
        for change in ('profile', 'ticket'):
            other = copy.deepcopy(row)
            other['assignment_id'] = 'd' * 64
            if change == 'ticket':
                other['handoff']['profile'] = {'name': 'other', 'home': '/other-profile'}
                other['ticket_scope']['issue_number'] = 11
            self.db.execute('INSERT OR REPLACE INTO role_assignments VALUES (?,?)', (other['assignment_id'], json.dumps(other)))
            self.db.commit()
            with self.subTest(change=change), self.assertRaises(RepositoryError):
                restart.ownership(self.db, self.item)

    def test_public_scope_bound_restart_and_admission_matrix(self):
        from factory_v1.__main__ import main
        from factory_v1 import sandbox, tickets
        argv = ['factory', '--state', self.state, '--operator-id', 'operator', 'reconcile-restart',
                '--project', 'p', '--iteration', 'i', '--api-base', self.github.base]
        output = io.StringIO()
        with patch('sys.argv', argv), patch('factory_v1.repositories.GitHub', return_value=self.github), redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue())['status'], 'ready')
        self.assertEqual(self.stored()['restart_reconciliation']['status'], 'ready')
        with patch.object(sandbox, 'load_config', return_value={}), \
             patch.object(restart, 'before_dispatch', side_effect=RepositoryError('reconciliation_required', 'unknown')) as admission, \
             patch.object(sandbox, 'Docker') as docker, self.assertRaises(RepositoryError):
            sandbox.launch(self.state, 'p', 'i', 'operator', 'assignment', '/SIMULATED-config', self.github)
        admission.assert_called_once_with(ANY, 'p', 'i', 'operator', self.github, force=True, assignment_id='assignment')
        docker.assert_not_called()
        with patch.object(restart, 'before_dispatch', side_effect=RepositoryError('reconciliation_required', 'unknown')) as admission:
            for label in ('reserve', 'prepare'):
                with self.subTest(boundary=label), patch.object(assignments, 'validate', return_value=[]), self.assertRaises(RepositoryError):
                    if label == 'reserve':
                        tickets.reserve(self.db, self.item, self.github, 10)
                    else:
                        assignments.prepare(self.db, 'p', 'i', 'operator', {'stage': 'implementation', 'skills': []}, self.github)
        self.assertEqual(admission.call_count, 2)
        self.assertEqual(assignments.rows(self.db), [])
        self.assertNotIn('reservation', self.stored())
