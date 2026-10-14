"""Narrow restart components. All durable input rows are simulated.

No worker/controller journey: each test starts at one persisted boundary;
network, Docker, Git and process observation seams are mocked.
"""
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
            patcher = patch(target, side_effect=AssertionError('Unexpected external execution: ' + target))
            patcher.start()
            self.addCleanup(patcher.stop)

    def save_item(self):
        self.db.execute('DELETE FROM iterations')
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(self.item)))
        self.db.commit()

    def test_resume_without_fresh_external_reconciliation_is_refused(self):
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'pause')
        with self.assertRaises(RepositoryError) as raised:
            controls.resume(self.db, 'p', 'i', 'operator', 'resume')
        self.assertEqual(raised.exception.code, 'reconciliation_required')
        self.assertTrue(controls.blocked(self.db, 'p', 'i'))

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
        # Requirements/tracker data are mocked at their independent read seams;
        # worker, ownership, persistence and pause guards remain production code.
        self.issue_observation = {'number': 10, 'id': 100, 'node_id': 'I10', 'body': 'Pinned issue'}
        with patch.object(restart, 'process_identity', return_value=None), \
             patch.object(restart.Docker, 'inspect', return_value=None), \
             patch('factory_v1.requirements.revalidate_inputs', return_value={}), \
             patch.object(restart.tickets.gh, 'read_issue', return_value=self.issue_observation):
            self.github.request.side_effect = lambda path, *_: {'sha': 'a' * 40} if '/git/commits/' in path else self.repo
            return restart.reconcile(self.db, 'p', 'i', 'operator', self.github, recover=recover)

    def test_pause_survives_new_connection_and_reconciliation_never_resumes(self):
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'intentional')
        with closing(sqlite3.connect(self.state)) as db:
            result = restart.reconcile(db, 'p', 'i', 'operator', self.github)
        self.assertEqual(result['status'], 'ready')
        self.assertFalse(result['dispatch_allowed'])
        self.assertTrue(controls.blocked(self.db, 'p', 'i'))
        resumed = controls.resume(self.db, 'p', 'i', 'operator', 'explicit', self.github)
        self.assertTrue(resumed['dispatch_allowed'])
        self.assertEqual(self.stored()['status'], 'active')

    def test_failed_repository_read_is_durable_and_prevents_resume(self):
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'intentional')
        self.github.request.side_effect = RepositoryError('github_unavailable', 'unavailable')
        with self.assertRaises(RepositoryError):
            controls.resume(self.db, 'p', 'i', 'operator', 'explicit', self.github)
        self.assertEqual(self.stored()['restart_reconciliation']['blockers'], [{'boundary': 'tracker', 'code': 'github_unavailable'}])
        self.assertTrue(controls.blocked(self.db, 'p', 'i'))

    def test_controller_loss_during_observation_leaves_durable_admission_hold(self):
        self.github.request.side_effect = SystemExit('SIMULATED controller loss at read-return')
        with self.assertRaises(SystemExit):
            restart.reconcile(self.db, 'p', 'i', 'operator', self.github)
        with closing(sqlite3.connect(self.state)) as fresh, self.assertRaises(RepositoryError):
            controls.require_active(fresh, 'p', 'i')
        self.assertEqual(self.stored()['restart_reconciliation']['blockers'],
                         [{'boundary': 'restart', 'code': 'reconciliation_interrupted'}])

    def test_repository_lost_response_adopts_exact_effect_without_creation(self):
        self.item['repository_intent'] = {'mode': 'new', 'marker': 'approved marker'}
        self.item['repository_onboarding'] = {'status': 'pending', 'api_base': self.github.base}
        self.save_item()
        self.repo['description'] = 'approved marker'
        self.github.request.side_effect = lambda path, *_: {'login': 'owner'} if path == '/user' else self.repo
        result = restart.reconcile(self.db, 'p', 'i', 'operator', self.github)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(self.stored()['repository_onboarding']['metadata']['id'], 123)
        self.assertTrue(all(all(value is None for value in call.args[1:]) for call in self.github.request.call_args_list))

    def test_repository_absent_before_effect_does_not_repeat_creation(self):
        self.item['repository_intent'] = {'mode': 'new', 'marker': 'approved marker'}
        self.item['repository_onboarding'] = {'status': 'pending', 'api_base': self.github.base}
        self.save_item()
        self.github.request.side_effect = lambda path, *_: {'login': 'owner'} if path == '/user' else None
        result = restart.reconcile(self.db, 'p', 'i', 'operator', self.github)
        self.assertEqual(result['blockers'], [{'boundary': 'tracker', 'code': 'creation_uncertain'}])
        self.assertEqual(self.stored()['repository_onboarding']['status'], 'pending')

    def test_lost_worker_keeps_artifacts_and_does_not_trust_done_report(self):
        row = self.assignment()
        retained = (Path(row['runtime']['artifacts']) / 'worker-report.json').read_bytes()
        result = self.observed_restart()
        self.assertEqual(result['status'], 'ready')
        saved = assignments.rows(self.db)[0]
        self.assertEqual(saved['runtime']['status'], 'stopped')
        self.assertTrue(saved['runtime']['recoverable'])
        self.assertNotIn('result_disposition', saved)
        self.assertNotIn('submitted_result', saved)
        self.assertFalse(restart.released_claim(saved))
        self.assertEqual((Path(row['runtime']['artifacts']) / 'worker-report.json').read_bytes(), retained)

    def test_exact_recovery_retires_claim_but_preserves_attempt_and_budget(self):
        row = self.assignment()
        self.item['budget'] = {'attempts': 2, 'deadline': 54321}
        self.save_item()
        result = self.observed_restart(recover=(row['assignment_id'], 'run-1', 'explicit infrastructure continuation'))
        self.assertEqual(result['status'], 'ready')
        saved = assignments.rows(self.db)[0]
        self.assertTrue(restart.released_claim(saved))
        self.assertEqual(saved['runtime']['run_id'], 'run-1')
        self.assertEqual(self.stored()['budget'], self.item['budget'])
        self.assertEqual(assignments.ticket_claims(self.db, {'issue_number': 11}), [])
        with self.assertRaises(RepositoryError):
            assignments.require_result_active(saved)

    def test_wrong_attempt_cannot_release_a_claim(self):
        row = self.assignment()
        result = self.observed_restart(recover=(row['assignment_id'], 'another-run', 'explicit'))
        self.assertEqual(result['status'], 'blocked')
        self.assertFalse(restart.released_claim(assignments.rows(self.db)[0]))

    def test_explicit_recovery_allows_fresh_claim_not_old_result_or_second_owner(self):
        row = self.assignment()
        self.observed_restart(recover=(row['assignment_id'], 'run-1', 'explicit recovery'))
        request = {'stage': 'implementation', 'claim_id': 'fresh-claim',
                   'profile': copy.deepcopy(row['handoff']['profile'])}
        assignments.claims(self.db, request, row['ticket_scope'], 'd' * 64)
        # Persisted fresh ownership is a direct simulated input, not a worker run.
        fresh = copy.deepcopy(row)
        fresh['assignment_id'] = 'd' * 64
        fresh['claim_id'] = 'fresh-claim'
        fresh.pop('runtime')
        self.db.execute('INSERT INTO role_assignments VALUES (?,?)', (fresh['assignment_id'], json.dumps(fresh)))
        self.db.commit()
        with self.assertRaises(RepositoryError):
            assignments.claims(self.db, {**request, 'claim_id': 'third-claim'}, row['ticket_scope'], 'e' * 64)

    def test_issue_drift_during_health_is_refused_at_final_resume_boundary(self):
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

    def test_wrong_repository_scope_never_inspects_or_releases_worker(self):
        row = self.assignment()
        row['ticket_scope']['repository_id'] = 999
        self.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),))
        self.db.commit()
        with patch.object(restart.Docker, 'inspect') as inspect:
            result = restart.reconcile(self.db, 'p', 'i', 'operator', self.github,
                                       recover=(row['assignment_id'], 'run-1', 'explicit'))
        self.assertEqual(result['status'], 'blocked')
        inspect.assert_not_called()
        self.assertFalse(restart.released_claim(assignments.rows(self.db)[0]))

    def test_live_or_unknown_worker_never_becomes_recoverable(self):
        row = self.assignment()
        for observation in ({'Name': '/factory-run-1', 'State': {'Running': True}},
                            {'Name': '/wrong-worker', 'State': {'Running': False}},
                            {'Name': '/factory-run-1', 'State': {'Running': 'unknown'}}):
            with self.subTest(observation=observation), patch.object(restart, 'process_identity', return_value=None), \
                 patch.object(restart.Docker, 'inspect', return_value=observation), self.assertRaises(RepositoryError):
                restart.worker(copy.deepcopy(row))

    def test_lost_controller_blocks_local_admission_even_without_prior_report(self):
        self.assignment()
        with patch.object(restart, 'process_identity', return_value=None), self.assertRaises(RepositoryError) as raised:
            controls.require_active(self.db, 'p', 'i')
        self.assertEqual(raised.exception.code, 'reconciliation_required')

    def test_workspace_detached_loose_packed_and_drift_matrix(self):
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

    def test_uncertain_attention_never_sends_or_resumes(self):
        with patch.object(restart.attention, 'records', return_value=[{'state': 'uncertain'}]):
            result = restart.reconcile(self.db, 'p', 'i', 'operator', self.github)
        self.assertEqual(result['blockers'], [{'boundary': 'attention', 'code': 'attention_uncertain'}])
        with self.assertRaises(RepositoryError):
            controls.require_active(self.db, 'p', 'i')

    def test_configured_unknown_health_is_resampled_and_holds_dispatch(self):
        self.item['delivery_monitor'] = {'config': 'SIMULATED configured contract'}
        self.save_item()
        with patch.object(restart.delivery, 'sample', return_value={'evidence': {'status': 'unknown'}}) as sample:
            result = restart.reconcile(self.db, 'p', 'i', 'operator', self.github)
        sample.assert_called_once_with(self.db, 'p', 'i', 'operator')
        self.assertEqual(result['blockers'], [{'boundary': 'delivery', 'code': 'main_frozen'}])

    def test_duplicate_profiles_and_cross_scope_claims_are_refused(self):
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

    def test_pre_dispatch_refreshes_real_restart_state_and_refuses_unknown(self):
        self.assignment()
        with patch.object(restart, 'reconcile', return_value={'status': 'blocked'}) as read, \
             self.assertRaises(RepositoryError):
            restart.before_dispatch(self.db, 'p', 'i', 'operator', self.github)
        read.assert_called_once()

    def test_malformed_ready_receipts_do_not_authorize_admission(self):
        for record in ({'status': 'ready'}, {'status': 'ready', 'observations': {}, 'blockers': [{'code': 'unknown'}]}, []):
            self.item['restart_reconciliation'] = record
            self.save_item()
            with self.subTest(record=record), self.assertRaises(RepositoryError):
                controls.require_active(self.db, 'p', 'i')

    def test_public_reconciliation_route_is_in_process_and_scope_bound(self):
        from factory_v1.__main__ import main
        argv = ['factory', '--state', self.state, '--operator-id', 'operator', 'reconcile-restart',
                '--project', 'p', '--iteration', 'i', '--api-base', self.github.base]
        output = io.StringIO()
        with patch('sys.argv', argv), patch('factory_v1.repositories.GitHub', return_value=self.github), redirect_stdout(output):
            main()
        self.assertEqual(json.loads(output.getvalue())['status'], 'ready')
        self.assertEqual(self.stored()['restart_reconciliation']['status'], 'ready')

    def test_public_launch_refuses_restart_before_docker_or_model_access(self):
        from factory_v1 import sandbox
        with patch.object(sandbox, 'load_config', return_value={}), \
             patch.object(restart, 'before_dispatch', side_effect=RepositoryError('reconciliation_required', 'unknown')) as admission, \
             patch.object(sandbox, 'Docker') as docker, self.assertRaises(RepositoryError):
            sandbox.launch(self.state, 'p', 'i', 'operator', 'assignment', '/SIMULATED-config', self.github)
        admission.assert_called_once_with(ANY, 'p', 'i', 'operator', self.github, force=True, assignment_id='assignment')
        docker.assert_not_called()

    def test_public_prepare_and_reserve_use_restart_boundary_before_claims(self):
        from factory_v1 import tickets
        with patch.object(restart, 'before_dispatch', side_effect=RepositoryError('reconciliation_required', 'unknown')) as admission:
            with self.assertRaises(RepositoryError):
                tickets.reserve(self.db, self.item, self.github, 10)
            with patch.object(assignments, 'validate', return_value=[]), self.assertRaises(RepositoryError):
                assignments.prepare(self.db, 'p', 'i', 'operator', {'stage': 'implementation', 'skills': []}, self.github)
        self.assertEqual(admission.call_count, 2)
        self.assertEqual(assignments.rows(self.db), [])
        self.assertNotIn('reservation', self.stored())

    def test_pause_arriving_during_resume_readback_is_not_overwritten(self):
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

    def test_process_identity_includes_boot_and_does_not_admit_zombies(self):
        from factory_v1 import sandbox_guard
        stat = '111 (name with spaces) ' + ' '.join(['R'] + ['0'] * 18 + ['12345'])
        with patch.object(Path, 'read_text', side_effect=[stat, 'boot-one\n']):
            first = sandbox_guard.process_identity(111)
        with patch.object(Path, 'read_text', side_effect=[stat, 'boot-two\n']):
            second = sandbox_guard.process_identity(111)
        self.assertEqual(first, 'boot-one:12345')
        self.assertNotEqual(first, second)
        self.assertTrue(sandbox_guard.same_process(first, '12345'), 'Legacy matching owner stays conservatively live')
        self.assertFalse(sandbox_guard.same_process(second, first), 'Exact new owners cannot cross a boot boundary')
        self.assertFalse(sandbox_guard.same_process(None, '12345'))
        with patch.object(Path, 'read_text', side_effect=[stat.replace(') R', ') Z'), 'boot-one\n']):
            self.assertIsNone(sandbox_guard.process_identity(111))
