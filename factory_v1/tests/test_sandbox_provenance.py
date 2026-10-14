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
        def forge(root):
            result_path = root / 'scratch/result.json'
            result = json.loads(result_path.read_text())
            result['tests'][0]['result'] = 'FORGED: 99 tests OK'
            conversation_path = root / 'scratch/conversation.json'
            conversation = json.loads(conversation_path.read_text())
            conversation['messages'][1]['content'] = 'FORGED: 99 tests OK'
            result_path.write_text(json.dumps(result))
            conversation_path.write_text(json.dumps(conversation))
        fixture.worker_boundary = forge
        result = fixture.launch()
        self.assertEqual(result['runtime']['status'], 'complete', result)
        self.assertFalse(result['result_disposition']['trusted_execution'])
        self.assertFalse(result['result_disposition']['advance_allowed'])
        self.assertFalse(result['result_disposition']['close_allowed'])
        self.assertFalse(result['result_disposition']['checks_verified'])
        self.assertFalse((fixture.attempt_root() / 'verified-tests.json').exists())

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
        config = test_sandbox.launcher_config(fixture.root)
        config['verification_commands'] = ['python -m unittest selected_component']
        fixture.config.write_text(json.dumps(config))
        docker = Mock()
        docker.image.return_value = 'fixture-image-id'
        inspections = []
        def inspect(name):
            root = fixture.attempt_root()
            mounts = {'/workspace': (str(root / 'workspace'), False),
                      '/scratch': (str(root / 'checks/scratch'), True),
                      '/inputs': (str(root / 'checks/inputs'), False)}
            observed = test_sandbox.container_inspection(config, name, mounts, exit_code=exit_code)
            inspections.append(observed)
            return observed
        docker.inspect.side_effect = inspect
        records = [{'command': config['verification_commands'][0], 'exit_code': exit_code,
                    'stdout': 'labeled controller output', 'stderr': '', 'output_truncated': False}]
        def call(*args, **kwargs):
            if args[0] == 'logs':
                return json.dumps({'run_id': fixture.stored()['runtime']['run_id'], 'records': records}).encode()
            return b''
        docker.call.side_effect = call
        verifier = Mock(wraps=sandbox_checks.verify_checks)
        store = Mock(wraps=assignments.store_result)
        def setup(mocks):
            mocks.enter_context(patch.object(sandbox_checks, 'Docker', return_value=docker))
            mocks.enter_context(patch.object(sandbox_checks.time, 'monotonic', return_value=100))
            mocks.enter_context(patch.object(sandbox, 'verify_checks', verifier))
            mocks.enter_context(patch.object(assignments, 'store_result', store))
        result = fixture.launch(setup=setup)
        verifier.assert_called_once()
        self.assertEqual(len(inspections), 2)
        docker.remove.assert_called_once_with(result['runtime']['container'])
        self.assertEqual([call.args[0] for call in docker.call.call_args_list], ['create', 'start', 'logs'] if exit_code else
                         ['create', 'start', 'logs', 'logs'])
        return result, store, records

    def test_launch_successful_controller_checks_are_wired_once_and_remain_untrusted(self):
        result, store, records = self.launch_with_controller_checks()
        root = self.fixture.attempt_root()
        self.assertEqual(result['runtime']['status'], 'complete')
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
        self.assertEqual(result['runtime']['status'], 'failed')
        self.assertEqual(result['runtime']['error'], 'verification_failed')
        self.assertTrue(result['runtime']['recoverable'])
        self.assertTrue(result['runtime']['container_removed'])
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
        fixture = self.fixture
        def cancel_checks(root, config, runtime, deadline):
            fixture.cancel(number)
            raise RepositoryError('cancelled', 'labeled check boundary interrupted')
        self.assert_stopped(fixture.launch(setup=lambda mocks: mocks.enter_context(
            patch.object(sandbox, 'verify_checks', side_effect=cancel_checks))))

    def test_sigterm_handler_during_controller_checks_cancels_launch(self):
        self.signal_during_checks(signal.SIGTERM)

    def test_sigint_handler_during_controller_checks_cancels_launch(self):
        self.signal_during_checks(signal.SIGINT)

    def test_cancellation_after_worker_evidence_cannot_submit_result(self):
        fixture = self.fixture
        original = sandbox.check_worker_evidence
        def interrupted(*args):
            result = original(*args)
            fixture.cancel()
            return result
        self.assert_stopped(fixture.launch(setup=lambda mocks: mocks.enter_context(
            patch.object(sandbox, 'check_worker_evidence', side_effect=interrupted))))

    def signal_during_result_revalidation(self, number):
        fixture = self.fixture
        original = assignments.store_result
        def store(*args, **kwargs):
            fixture.preparing = lambda db: fixture.cancel(number)
            try:
                return original(*args, **kwargs)
            finally:
                fixture.preparing = None
        self.assert_stopped(fixture.launch(setup=lambda mocks: mocks.enter_context(
            patch.object(assignments, 'store_result', side_effect=store))))

    def test_sigterm_handler_during_final_result_revalidation_is_not_admitted(self):
        self.signal_during_result_revalidation(signal.SIGTERM)

    def test_sigint_handler_during_final_result_revalidation_is_not_admitted(self):
        self.signal_during_result_revalidation(signal.SIGINT)

    def test_signal_after_result_commit_cannot_report_complete(self):
        fixture = self.fixture
        original = assignments.store_result
        def interrupted(*args, **kwargs):
            result = original(*args, **kwargs)
            fixture.cancel()
            return result
        self.assert_stopped(fixture.launch(setup=lambda mocks: mocks.enter_context(
            patch.object(assignments, 'store_result', side_effect=interrupted))), submitted=True)

    def test_signal_before_result_commit_rolls_back_submission(self):
        fixture = self.fixture
        connect = sqlite3.connect
        updated = []
        class CancelledCommit(sqlite3.Connection):
            def execute(connection, sql, parameters=()):
                cursor = super().execute(sql, parameters)
                if not updated and sql.startswith('UPDATE role_assignments') and 'submitted_result' in str(parameters):
                    fixture.cancel()
                    updated.append(True)
                return cursor
        def controlled_connect(*args, **kwargs):
            return connect(*args, **kwargs, factory=CancelledCommit)
        self.assert_stopped(fixture.launch(setup=lambda mocks: mocks.enter_context(
            patch.object(sqlite3, 'connect', side_effect=controlled_connect))))
        self.assertTrue(updated)

    def test_signal_during_final_cleanup_cannot_report_complete(self):
        fixture = self.fixture
        def interrupted(name):
            if (fixture.attempt_root() / 'worker-reported-tests.json').exists():
                fixture.cancel()
        fixture.docker.remove.side_effect = interrupted
        self.assert_stopped(fixture.launch(), submitted=True)

    def test_launcher_does_not_release_ownership_when_broker_join_is_unconfirmed(self):
        fixture = self.fixture
        fixture.helper.is_alive.return_value = True
        fixture.helper.is_alive.side_effect = None
        result = fixture.launch()
        self.assertEqual(result['runtime']['status'], 'unconfirmed', result)
        self.assertEqual(result['runtime']['error'], 'stop_unconfirmed')
        self.assertFalse(result['runtime']['container_removed'])
        capability = Path(json.loads((fixture.attempt_root() / 'capability-path.json').read_text()))
        self.addCleanup(test_sandbox.shutil.rmtree, capability, ignore_errors=True)
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
