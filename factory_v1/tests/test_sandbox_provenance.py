"""Provenance/admission components; no worker, controller CLI or container executes."""
import json
import signal
import sqlite3
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import assignments, sandbox, sandbox_checks
from factory_v1.repositories import RepositoryError
from factory_v1.sandbox_policy import load_config
from factory_v1.tests import test_sandbox


class CheckFixture:
    def __init__(self, case):
        self.launcher = test_sandbox.LauncherFixture(case)
        self.root = self.launcher.root / 'check-run'
        self.root.mkdir(mode=0o700)
        (self.root / 'workspace').mkdir()
        (self.root / 'workspace/hello.py').write_text('labeled candidate\n')
        self.config = load_config(self.launcher.config)
        self.config['verification_commands'] = ['python -m unittest selected_component']
        self.runtime = {'run_id': 'fixture-run', 'container': 'fixture-container'}
        self.mounts = {'/workspace': (str(self.root / 'workspace'), False),
                       '/scratch': (str(self.root / 'checks/scratch'), True),
                       '/inputs': (str(self.root / 'checks/inputs'), False)}
        self.docker = Mock()
        self.docker.image.return_value = 'fixture-image-id'
        self.docker.inspect.side_effect = [test_sandbox.container_inspection(self.config, self.runtime['container'], self.mounts),
                                          {'State': {'Running': False, 'ExitCode': 0}}]
        self.records = [{'command': self.config['verification_commands'][0], 'exit_code': 0,
                         'stdout': 'labeled output', 'stderr': '', 'output_truncated': False}]
        self.report = {'run_id': self.runtime['run_id'], 'records': self.records}
        self.boundary: Callable | None = None
        def call(*args, **kwargs):
            if self.boundary:
                self.boundary(args[0])
            return json.dumps(self.report).encode() if args[0] == 'logs' else b''
        self.docker.call.side_effect = call

    def verify(self, deadline=103):
        with patch.object(sandbox_checks, 'Docker', return_value=self.docker), \
                patch.object(sandbox_checks.time, 'monotonic', return_value=100):
            return sandbox_checks.verify_checks(self.root, self.config, self.runtime, deadline)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_sandbox.LauncherFixture(self)

    def assert_stopped(self, result, *, submitted=False):
        self.assertEqual(result['runtime']['status'], 'stopped', result)
        self.assertEqual(result['runtime']['error'], 'cancelled')
        self.assertTrue(result['runtime']['recoverable'])
        self.assertTrue(result['runtime']['container_removed'])
        self.assertEqual('submitted_result' in result, submitted)
        self.assertEqual(self.fixture.stored(), result)
        self.assertTrue((self.fixture.attempt_root() / 'stop').exists())
        if submitted:
            self.assertFalse(result['result_disposition']['trusted_execution'])
            self.assertFalse(result['result_disposition']['advance_allowed'])
            self.assertFalse(result['result_disposition']['close_allowed'])

    def test_forged_terminal_transcript_cannot_be_promoted_to_trusted_execution(self):
        fixture = self.fixture
        component = test_sandbox.FinalizationFixture(self, fixture)
        root = component.root
        result_path = root / 'scratch/result.json'
        report = json.loads(result_path.read_text())
        report['tests'][0]['result'] = 'FORGED: 99 tests OK'
        conversation_path = root / 'scratch/conversation.json'
        conversation = json.loads(conversation_path.read_text())
        conversation['messages'][1]['content'] = 'FORGED: 99 tests OK'
        result_path.write_text(json.dumps(report))
        conversation_path.write_text(json.dumps(conversation))
        result = component.finalize()
        self.assertFalse(result['result_disposition']['trusted_execution'])
        self.assertFalse(result['result_disposition']['advance_allowed'])
        self.assertFalse(result['result_disposition']['close_allowed'])
        self.assertFalse(result['result_disposition']['checks_verified'])
        self.assertFalse((fixture.attempt_root() / 'verified-tests.json').exists())
        self.assertEqual(result['submitted_result'], report)

    def test_controller_checks_are_private_read_only_and_separate_from_worker_claims(self):
        check = CheckFixture(self)
        self.assertTrue(check.verify())
        proof = json.loads((check.root / 'controller-checks.json').read_text())
        self.assertEqual(proof['run_id'], check.runtime['run_id'])
        self.assertEqual(proof['records'], check.records)
        self.assertEqual(proof['provenance'], 'controller_admitted_read_only_sandbox_checks')
        inspection = json.loads((check.root / 'checks/container-inspection.json').read_text())
        self.assertEqual({m['Destination'] for m in inspection['Mounts']}, {'/workspace', '/scratch', '/inputs'})
        self.assertFalse(next(m for m in inspection['Mounts'] if m['Destination'] == '/workspace')['RW'])
        self.assertEqual(check.root.stat().st_mode & 0o077, 0)
        self.assertEqual((check.root / 'checks/inputs/worker.py').stat().st_mode & 0o222, 0)
        check.docker.remove.assert_called_once_with(check.runtime['container'])

    def test_failed_controller_check_retains_artifacts_without_verification(self):
        check = CheckFixture(self)
        check.docker.inspect.side_effect = [test_sandbox.container_inspection(check.config, check.runtime['container'], check.mounts),
                                           {'State': {'Running': False, 'ExitCode': 3}}]
        with self.assertRaises(RepositoryError) as error:
            check.verify()
        self.assertEqual(error.exception.code, 'verification_failed')
        self.assertTrue((check.root / 'checks/container.log').exists())
        self.assertFalse((check.root / 'controller-checks.json').exists())
        check.docker.remove.assert_called_once()

    def launch_with_controller_checks(self, *, exit_code=0):
        fixture = self.fixture
        component = test_sandbox.FinalizationFixture(self, fixture)
        component.config['verification_commands'] = ['python -m unittest selected_component']
        docker = Mock()
        docker.image.return_value = 'fixture-image-id'
        mounts = {'/workspace': (str(component.root / 'workspace'), False),
                  '/scratch': (str(component.root / 'checks/scratch'), True),
                  '/inputs': (str(component.root / 'checks/inputs'), False)}
        docker.inspect.side_effect = [test_sandbox.container_inspection(component.config, component.runtime['container'], mounts),
                                      {'State': {'Running': False, 'ExitCode': exit_code}}]
        records = [{'command': component.config['verification_commands'][0], 'exit_code': exit_code,
                    'stdout': 'labeled controller output', 'stderr': '', 'output_truncated': False}]
        docker.call.side_effect = lambda *args, **kwargs: (json.dumps({'run_id': component.runtime['run_id'], 'records': records}).encode()
                                                         if args[0] == 'logs' else b'')
        verifier = Mock(wraps=sandbox_checks.verify_checks)
        store = Mock(wraps=assignments.store_result)
        def setup(mocks):
            mocks.enter_context(patch.object(sandbox_checks, 'Docker', return_value=docker))
            mocks.enter_context(patch.object(sandbox_checks.time, 'monotonic', return_value=100))
            mocks.enter_context(patch.object(sandbox, 'verify_checks', verifier))
            mocks.enter_context(patch.object(assignments, 'store_result', store))
        if exit_code:
            with self.assertRaises(RepositoryError) as error:
                component.finalize(setup=setup)
            self.assertEqual(error.exception.code, 'verification_failed')
            result = fixture.stored()
        else:
            result = component.finalize(setup=setup)
        verifier.assert_called_once()
        docker.remove.assert_called_once_with(result['runtime']['container'])
        self.assertEqual([call.args[0] for call in docker.call.call_args_list], ['create', 'start', 'logs'] if exit_code else
                         ['create', 'start', 'logs', 'logs'])
        return result, store, records

    def test_launch_successful_controller_checks_are_wired_once_and_remain_untrusted(self):
        result, store, records = self.launch_with_controller_checks()
        root = self.fixture.attempt_root()
        self.assertEqual(result['runtime']['status'], 'running')  # Admission does not finish ownership.
        self.assertEqual(self.fixture.stored(), result)
        self.assertTrue(result['result_disposition']['checks_verified'])
        self.assertTrue(result['result_disposition']['isolated_execution'])
        for flag in ('trusted_execution', 'advance_allowed', 'close_allowed'):
            self.assertFalse(result['result_disposition'][flag])
        proof = json.loads((root / 'controller-checks.json').read_text())
        self.assertEqual(proof['run_id'], result['runtime']['run_id'])
        self.assertEqual(proof['records'], records)
        self.assertEqual(proof['provenance'], 'controller_admitted_read_only_sandbox_checks')
        self.assertEqual(result['runtime']['checks_sha256'], test_sandbox.hashlib.sha256((root / 'controller-checks.json').read_bytes()).hexdigest())
        self.assertEqual((root / 'worker-container.log').read_bytes(), b'labeled mock container log')
        self.assertNotEqual((root / 'worker-container.log').read_bytes(), (root / 'checks/container.log').read_bytes())
        inspection = json.loads((root / 'checks/container-inspection.json').read_text())
        self.assertFalse(next(m for m in inspection['Mounts'] if m['Destination'] == '/workspace')['RW'])
        store.assert_called_once()

    def test_launch_failed_real_controller_checks_retain_scratch_without_store_result(self):
        result, store, _ = self.launch_with_controller_checks(exit_code=3)
        root = self.fixture.attempt_root()
        self.assertEqual(result['runtime']['status'], 'running')
        self.assertEqual(self.fixture.stored(), result)
        self.assertNotIn('submitted_result', result)
        self.assertNotIn('result_disposition', result)
        self.assertNotIn('checks_sha256', result['runtime'])
        self.assertTrue((root / 'scratch/result.json').exists())
        self.assertTrue((root / 'checks/container.log').exists())
        self.assertTrue((root / 'worker-container.log').exists())
        self.assertFalse((root / 'controller-checks.json').exists())
        store.assert_not_called()

    def cancelled_checks(self, boundary):
        check = CheckFixture(self)
        def interrupt(command):
            if command == boundary:
                (check.root / 'stop').touch()
        check.boundary = interrupt
        with self.assertRaises(RepositoryError) as error:
            check.verify()
        self.assertEqual(error.exception.code, 'cancelled')
        self.assertFalse((check.root / 'controller-checks.json').exists())
        check.docker.remove.assert_called_once()

    def test_stop_during_controller_checks_removes_the_reused_container(self):
        self.cancelled_checks('start')

    def test_cancellation_at_check_output_cannot_accept_verification(self):
        self.cancelled_checks('logs')

    def test_check_container_disappearing_after_cancellation_is_structured(self):
        check = CheckFixture(self)
        calls = []
        def inspect(name):
            calls.append(name)
            if len(calls) == 1:
                return test_sandbox.container_inspection(check.config, name, check.mounts)
            (check.root / 'stop').touch()
            return None
        check.docker.inspect.side_effect = inspect
        with self.assertRaises(RepositoryError) as error:
            check.verify()
        self.assertEqual(error.exception.code, 'cancelled')
        self.assertFalse((check.root / 'controller-checks.json').exists())
        check.docker.remove.assert_called_once()

    def test_controller_checks_share_original_attempt_deadline(self):
        check = CheckFixture(self)
        self.assertTrue(check.verify(deadline=103))
        request = json.loads((check.root / 'checks/inputs/checks.json').read_text())
        self.assertEqual(request['seconds'], 3)
        argv = json.loads((check.root / 'checks/launch-command.json').read_text())
        self.assertIn('--network', argv)
        expired = CheckFixture(self)
        with self.assertRaises(RepositoryError) as error:
            expired.verify(deadline=100)
        self.assertEqual(error.exception.code, 'time_limit')
        expired.docker.call.assert_not_called()
        self.assertFalse((expired.root / 'controller-checks.json').exists())

    def test_report_identity_bounds_and_candidate_mutation_fail_closed(self):
        for defect in ('identity', 'command', 'exit', 'truncated', 'stdout', 'candidate', 'mode'):
            with self.subTest(defect=defect):
                check = CheckFixture(self)
                if defect == 'identity':
                    check.report['run_id'] = 'wrong-run'
                elif defect == 'command':
                    check.records[0]['command'] = 'weakened check'
                elif defect == 'exit':
                    check.records[0]['exit_code'] = False
                elif defect == 'truncated':
                    check.records[0]['output_truncated'] = True
                elif defect == 'stdout':
                    check.records[0]['stdout'] = 'x' * 65537
                else:
                    def change(command):
                        if command == 'logs':
                            path = check.root / 'workspace/hello.py'
                            if defect == 'candidate':
                                path.write_text('mutated candidate')
                            else:
                                path.chmod(0o755)
                    check.boundary = change
                with self.assertRaises(RepositoryError) as error:
                    check.verify()
                self.assertEqual(error.exception.code, 'invalid_result' if defect in {'candidate', 'mode'} else 'verification_failed')
                self.assertFalse((check.root / 'controller-checks.json').exists())
                check.docker.remove.assert_called_once()

    def signal_during_checks(self, number):
        component = test_sandbox.FinalizationFixture(self, self.fixture)
        check = CheckFixture(self)
        check.root = component.root
        check.runtime = component.runtime
        check.mounts = {'/workspace': (str(check.root / 'workspace'), False),
                        '/scratch': (str(check.root / 'checks/scratch'), True),
                        '/inputs': (str(check.root / 'checks/inputs'), False)}
        check.docker.inspect.side_effect = [test_sandbox.container_inspection(check.config, check.runtime['container'], check.mounts)]
        check.boundary = lambda command: component.cancel(number, None) if command == 'start' else None
        with patch.object(sandbox_checks, 'Docker', return_value=check.docker), \
                patch.object(sandbox_checks.time, 'monotonic', return_value=100), \
                self.assertRaises(RepositoryError) as error:
            sandbox.verify_checks(component.root, check.config, component.runtime, 103)
        self.assertEqual(error.exception.code, 'cancelled')
        self.assertTrue(component.stop.is_set())
        self.assertTrue((component.root / 'stop').exists())
        self.assertNotIn('submitted_result', self.fixture.stored())
        self.assertFalse((component.root / 'controller-checks.json').exists())
        check.docker.remove.assert_called_once()

    def test_sigterm_handler_during_controller_checks_cancels_launch(self):
        self.signal_during_checks(signal.SIGTERM)

    def test_sigint_handler_during_controller_checks_cancels_launch(self):
        self.signal_during_checks(signal.SIGINT)

    def test_cancellation_after_worker_evidence_cannot_submit_result(self):
        fixture = self.fixture
        component = test_sandbox.FinalizationFixture(self, fixture)
        original = sandbox.check_worker_evidence
        injected = []
        def interrupted(*args):
            result = original(*args)
            component.cancel(signal.SIGTERM, None)
            injected.append(True)
            return result
        with self.assertRaises(RepositoryError) as error:
            component.finalize(setup=lambda mocks: mocks.enter_context(
                patch.object(sandbox, 'check_worker_evidence', side_effect=interrupted)))
        self.assertEqual(error.exception.code, 'cancelled')
        self.assertTrue(injected)
        self.assertNotIn('submitted_result', fixture.stored())

    def signal_during_result_revalidation(self, number):
        fixture = self.fixture
        component = test_sandbox.FinalizationFixture(self, fixture)
        injected = []
        def pause(db):
            component.cancel(number, None)
            injected.append(True)
        original = assignments.store_result
        def store(*args, **kwargs):
            fixture.preparing = pause
            try:
                return original(*args, **kwargs)
            finally:
                fixture.preparing = None
        with self.assertRaises(RepositoryError) as error:
            component.finalize(setup=lambda mocks: mocks.enter_context(
                patch.object(assignments, 'store_result', side_effect=store)))
        self.assertEqual(error.exception.code, 'cancelled')
        self.assertTrue(injected)
        self.assertNotIn('submitted_result', fixture.stored())

    def test_sigterm_handler_during_final_result_revalidation_is_not_admitted(self):
        self.signal_during_result_revalidation(signal.SIGTERM)

    def test_sigint_handler_during_final_result_revalidation_is_not_admitted(self):
        self.signal_during_result_revalidation(signal.SIGINT)

    def test_signal_after_result_commit_cannot_report_complete(self):
        fixture = self.fixture
        component = test_sandbox.FinalizationFixture(self, fixture)
        original = assignments.store_result
        committed = []
        def interrupted(*args, **kwargs):
            result = original(*args, **kwargs)
            committed.append(fixture.stored()['submitted_result'])
            component.cancel(signal.SIGTERM, None)
            return result
        with self.assertRaises(RepositoryError) as error:
            component.finalize(setup=lambda mocks: mocks.enter_context(
                patch.object(assignments, 'store_result', side_effect=interrupted)))
        self.assertEqual(error.exception.code, 'cancelled')
        self.assertEqual(committed, [component.result])
        result = fixture.stored()
        self.assertEqual(result['submitted_result'], component.result)
        for flag in ('trusted_execution', 'advance_allowed', 'close_allowed'):
            self.assertFalse(result['result_disposition'][flag])
        self.assertNotEqual(result['runtime']['status'], 'complete')

    def test_signal_before_result_commit_rolls_back_submission(self):
        fixture = self.fixture
        component = test_sandbox.FinalizationFixture(self, fixture)
        connect = sqlite3.connect
        updated = []
        class CancelledCommit(sqlite3.Connection):
            def execute(connection, sql, parameters=()):
                cursor = super().execute(sql, parameters)
                if not updated and sql.startswith('UPDATE role_assignments') and 'submitted_result' in str(parameters):
                    component.cancel(signal.SIGTERM, None)
                    updated.append(True)
                return cursor
        def controlled_connect(*args, **kwargs):
            return connect(*args, **kwargs, factory=CancelledCommit)
        with self.assertRaises(RepositoryError) as error:
            component.finalize(setup=lambda mocks: mocks.enter_context(
                patch.object(sqlite3, 'connect', side_effect=controlled_connect)))
        self.assertEqual(error.exception.code, 'cancelled')
        self.assertTrue(updated)
        self.assertNotIn('submitted_result', fixture.stored())
        with connect(fixture.state) as fresh:
            self.assertNotIn('submitted_result', json.loads(fresh.execute('SELECT payload FROM role_assignments').fetchone()[0]))

    def test_signal_during_final_cleanup_cannot_report_complete(self):
        fixture = self.fixture
        component = test_sandbox.FinalizationFixture(self, fixture)
        assignment = fixture.stored()
        assignment['submitted_result'] = component.result
        assignment['result_disposition'] = {'trusted_execution': False, 'advance_allowed': False, 'close_allowed': False}
        with fixture.db:
            fixture.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(assignment),))
        fixture.docker.remove.side_effect = lambda name: component.cancel(signal.SIGTERM, None)
        self.assert_stopped(component.cleanup(), submitted=True)
        fixture.helper.join.assert_called_once_with(timeout=1)
        fixture.guard.wait.assert_called_once_with(timeout=35)

    def test_launcher_does_not_release_ownership_when_broker_join_is_unconfirmed(self):
        fixture = self.fixture
        component = test_sandbox.FinalizationFixture(self, fixture)
        fixture.helper.is_alive.return_value = True
        fixture.helper.is_alive.side_effect = None
        component.cancel(signal.SIGTERM, None)
        result = component.cleanup()
        self.assertEqual(result['runtime']['status'], 'unconfirmed', result)
        self.assertEqual(result['runtime']['error'], 'stop_unconfirmed')
        self.assertFalse(result['runtime']['container_removed'])
        capability = Path(json.loads((fixture.attempt_root() / 'capability-path.json').read_text()))
        self.assertTrue(capability.exists())
        fixture.helper.join.assert_called_once_with(timeout=1)
        self.assertEqual(fixture.stored(), result)

    def test_malformed_operator_checks_are_refused_before_claim(self):
        fixture = self.fixture
        for commands in ('echo unsafe', [None], [''], ['x\0y'], ['x' * 1025], ['echo ok'] * 9):
            config = test_sandbox.launcher_config(fixture.root)
            config['verification_commands'] = commands
            fixture.config.write_text(json.dumps(config))
            with self.subTest(commands=commands), self.assertRaises(RepositoryError) as error:
                load_config(fixture.config)
            self.assertEqual(error.exception.code, 'invalid_launcher')
            self.assertNotIn('runtime', fixture.stored())
        self.assertEqual(list(fixture.artifacts.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
