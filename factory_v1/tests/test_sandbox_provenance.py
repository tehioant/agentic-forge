"""Provenance/admission components; no worker, controller CLI or container executes."""
import json
import signal
import sqlite3
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import assignments, sandbox, sandbox_checks, controls
from factory_v1.repositories import RepositoryError
from factory_v1.sandbox_policy import load_config
from factory_v1.tests import test_sandbox


class CheckFixture:
    def __init__(self, case):
        self.launcher = test_sandbox.LauncherFixture(case)
        self.root = self.launcher.artifacts / 'check-run'
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
            return sandbox_checks.verify_checks(self.root, self.config, self.runtime, deadline,
                admit=lambda: controls.admission(self.launcher.db, 'product', 'm1', operation='effect'))


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


    def test_controller_check_proof_admission_cases(self):
        for defect in ('valid', 'process', 'identity', 'command', 'exit', 'truncated', 'stdout', 'candidate', 'mode'):
            with self.subTest(defect=defect):
                check = CheckFixture(self)
                if defect == 'process':
                    check.docker.inspect.side_effect = [test_sandbox.container_inspection(check.config, check.runtime['container'], check.mounts),
                                                       {'State': {'Running': False, 'ExitCode': 3}}]
                elif defect == 'identity':
                    check.report['run_id'] = 'wrong-run'
                elif defect == 'command':
                    check.records[0]['command'] = 'weakened check'
                elif defect == 'exit':
                    check.records[0]['exit_code'] = False
                elif defect == 'truncated':
                    check.records[0]['output_truncated'] = True
                elif defect == 'stdout':
                    check.records[0]['stdout'] = 'x' * 65537
                elif defect in {'candidate', 'mode'}:
                    def change(command):
                        if command == 'logs':
                            path = check.root / 'workspace/hello.py'
                            if defect == 'candidate':
                                path.write_text('mutated candidate')
                            else:
                                path.chmod(0o755)
                    check.boundary = change
                if defect == 'valid':
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
                else:
                    with self.assertRaises(RepositoryError) as error:
                        check.verify()
                    self.assertEqual(error.exception.code, 'invalid_result' if defect in {'candidate', 'mode'} else 'verification_failed')
                    self.assertFalse((check.root / 'controller-checks.json').exists())
                    if defect == 'process':
                        self.assertTrue((check.root / 'checks/container.log').exists())
                check.docker.remove.assert_called_once_with(check.runtime['container'])


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

    def test_finalization_controller_check_wiring_cases(self):
        for exit_code in (0, 3, 'pause'):
            with self.subTest(exit_code=exit_code):
                fixture = self.fixture if exit_code == 0 else test_sandbox.LauncherFixture(self)
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
                    if exit_code == 'pause':
                        evidence = sandbox.check_worker_evidence
                        def paused_evidence(*args):
                            result = evidence(*args)
                            controls.request(fixture.db, 'product', 'm1', '42', 'pause', 'labeled last evidence read pause')
                            return result
                        mocks.enter_context(patch.object(sandbox, 'check_worker_evidence', side_effect=paused_evidence))
                if exit_code:
                    with self.assertRaises(RepositoryError) as error:
                        component.finalize(setup=setup)
                    self.assertEqual(error.exception.code, 'iteration_paused' if exit_code == 'pause' else 'verification_failed')
                    result = fixture.stored()
                else:
                    result = component.finalize(setup=setup)
                root = component.root
                self.assertEqual(result['runtime']['status'], 'running')
                self.assertEqual(fixture.stored(), result)
                verifier.assert_called_once()
                docker.remove.assert_called_once_with(result['runtime']['container'])
                self.assertEqual([call.args[0] for call in docker.call.call_args_list],
                                 ['logs'] if exit_code == 'pause' else ['create', 'start', 'logs'] if exit_code else ['create', 'start', 'logs', 'logs'])
                if exit_code:
                    self.assertNotIn('submitted_result', result)
                    self.assertNotIn('result_disposition', result)
                    self.assertNotIn('checks_sha256', result['runtime'])
                    if exit_code == 'pause':
                        self.assertNotIn('create', [call.args[0] for call in docker.call.call_args_list])
                        self.assertNotIn('start', [call.args[0] for call in docker.call.call_args_list])
                    for name in ('scratch/result.json', 'checks/container.log', 'worker-container.log'):
                        self.assertTrue((root / name).exists())
                    self.assertFalse((root / 'controller-checks.json').exists())
                    store.assert_not_called()
                else:
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

    def test_controller_check_cancellation_checkpoints(self):
        rows = (('pause-before-create', None), ('pause-before-start', None), ('pause-wait', None), ('pause-receipt', None),
                ('start', None), ('logs', None), ('disappear', None),
                ('start', signal.SIGTERM), ('start', signal.SIGINT))
        for boundary, number in rows:
            with self.subTest(boundary=boundary, signal=number):
                check = CheckFixture(self)
                if boundary.startswith('pause-'):
                    injected = []
                    def pause():
                        if not injected:
                            controls.request(check.launcher.db, 'product', 'm1', '42', 'pause', 'labeled independent-check interruption')
                            injected.append(True)
                    inspections = []
                    def inspect(name):
                        inspections.append(name)
                        if len(inspections) == 1:
                            if boundary == 'pause-before-start':
                                pause()
                            return test_sandbox.container_inspection(check.config, name, check.mounts)
                        if boundary == 'pause-wait':
                            pause()
                            return {'State': {'Running': True}}
                        return {'State': {'Running': False, 'ExitCode': 0}}
                    check.docker.inspect.side_effect = inspect
                    check.boundary = lambda command: pause() if command == 'logs' and boundary == 'pause-receipt' else None
                    if boundary == 'pause-before-create':
                        pause()
                    with patch.object(sandbox_checks.time, 'sleep'), self.assertRaises(RepositoryError) as error:
                        check.verify()
                    self.assertEqual(error.exception.code, 'iteration_paused')
                    self.assertTrue(injected)
                    operations = [call.args[0] for call in check.docker.call.call_args_list]
                    if boundary == 'pause-before-create':
                        self.assertNotIn('create', operations)
                    if boundary in {'pause-before-create', 'pause-before-start'}:
                        self.assertNotIn('start', operations)
                    self.assertFalse((check.root / 'controller-checks.json').exists())
                    check.docker.remove.assert_called_once()
                    continue
                component = None
                if number is not None:
                    fixture = self.fixture if number == signal.SIGTERM else test_sandbox.LauncherFixture(self)
                    component = test_sandbox.FinalizationFixture(self, fixture)
                    check.root, check.runtime = component.root, component.runtime
                    check.config['artifacts_root'] = component.config['artifacts_root']
                    check.mounts = {'/workspace': (str(check.root / 'workspace'), False),
                                    '/scratch': (str(check.root / 'checks/scratch'), True),
                                    '/inputs': (str(check.root / 'checks/inputs'), False)}
                calls, injected = [], []
                def interrupt(command):
                    if command == boundary:
                        if component:
                            component.cancel(number, None)
                        else:
                            (check.root / 'stop').touch()
                        injected.append(True)
                def inspect(name):
                    calls.append(name)
                    if len(calls) == 1:
                        return test_sandbox.container_inspection(check.config, name, check.mounts)
                    if boundary == 'disappear':
                        (check.root / 'stop').touch()
                        injected.append(True)
                        return None
                    return {'State': {'Running': False, 'ExitCode': 0}}
                check.boundary = interrupt
                check.docker.inspect.side_effect = inspect
                with self.assertRaises(RepositoryError) as error:
                    check.verify()
                self.assertEqual(error.exception.code, 'cancelled')
                self.assertTrue(injected)
                self.assertFalse((check.root / 'controller-checks.json').exists())
                check.docker.remove.assert_called_once()
                if component:
                    self.assertTrue(component.stop.is_set())
                    self.assertTrue((component.root / 'stop').exists())
                    self.assertNotIn('submitted_result', fixture.stored())



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


    def signal_during_checks(self, number):
        component = test_sandbox.FinalizationFixture(self, self.fixture)
        check = CheckFixture(self)
        check.root = component.root
        check.runtime = component.runtime
        check.config['artifacts_root'] = component.config['artifacts_root']
        check.mounts = {'/workspace': (str(check.root / 'workspace'), False),
                        '/scratch': (str(check.root / 'checks/scratch'), True),
                        '/inputs': (str(check.root / 'checks/inputs'), False)}
        check.docker.inspect.side_effect = [test_sandbox.container_inspection(check.config, check.runtime['container'], check.mounts)]
        check.boundary = lambda command: component.cancel(number, None) if command == 'start' else None
        with patch.object(sandbox_checks, 'Docker', return_value=check.docker), \
                patch.object(sandbox_checks.time, 'monotonic', return_value=100), \
                self.assertRaises(RepositoryError) as error:
            sandbox.verify_checks(component.root, check.config, component.runtime, 103,
                admit=lambda: controls.admission(self.fixture.db, 'product', 'm1', operation='effect'))
        self.assertEqual(error.exception.code, 'cancelled')
        self.assertTrue(component.stop.is_set())
        self.assertTrue((component.root / 'stop').exists())
        self.assertNotIn('submitted_result', self.fixture.stored())
        self.assertFalse((component.root / 'controller-checks.json').exists())
        check.docker.remove.assert_called_once()



    def test_final_result_precommit_cancellation_cases(self):
        rows = (('evidence', signal.SIGTERM), ('revalidation', signal.SIGTERM), ('revalidation', signal.SIGINT))
        for boundary, number in rows:
            with self.subTest(boundary=boundary, signal=number):
                fixture = self.fixture if boundary == 'evidence' else test_sandbox.LauncherFixture(self)
                component = test_sandbox.FinalizationFixture(self, fixture)
                injected = []
                evidence, store = sandbox.check_worker_evidence, assignments.store_result
                def cancel(db=None):
                    component.cancel(number, None)
                    injected.append(True)
                def interrupted_evidence(*args):
                    result = evidence(*args)
                    cancel()
                    return result
                def interrupted_store(*args, **kwargs):
                    fixture.preparing = cancel
                    try:
                        return store(*args, **kwargs)
                    finally:
                        fixture.preparing = None
                def setup(mocks):
                    if boundary == 'evidence':
                        mocks.enter_context(patch.object(sandbox, 'check_worker_evidence', side_effect=interrupted_evidence))
                    else:
                        mocks.enter_context(patch.object(assignments, 'store_result', side_effect=interrupted_store))
                with self.assertRaises(RepositoryError) as error:
                    component.finalize(setup=setup)
                self.assertEqual(error.exception.code, 'cancelled')
                self.assertTrue(injected)
                self.assertNotIn('submitted_result', fixture.stored())
                with sqlite3.connect(fixture.state) as fresh:
                    self.assertNotIn('submitted_result', json.loads(fresh.execute('SELECT payload FROM role_assignments').fetchone()[0]))

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



    def test_final_result_commit_cancellation_cases(self):
        for after_commit in (False, True):
            with self.subTest(after_commit=after_commit):
                fixture = test_sandbox.LauncherFixture(self) if after_commit else self.fixture
                component = test_sandbox.FinalizationFixture(self, fixture)
                connect, store = sqlite3.connect, assignments.store_result
                injected, committed = [], []
                class CancelledCommit(sqlite3.Connection):
                    def execute(connection, sql, parameters=()):
                        cursor = super().execute(sql, parameters)
                        if not injected and sql.startswith('UPDATE role_assignments') and 'submitted_result' in str(parameters):
                            component.cancel(signal.SIGTERM, None)
                            injected.append(True)
                        return cursor
                def controlled_connect(*args, **kwargs):
                    return connect(*args, **kwargs, factory=CancelledCommit)
                def interrupted(*args, **kwargs):
                    result = store(*args, **kwargs)
                    committed.append(fixture.stored()['submitted_result'])
                    component.cancel(signal.SIGTERM, None)
                    injected.append(True)
                    return result
                def setup(mocks):
                    if after_commit:
                        mocks.enter_context(patch.object(assignments, 'store_result', side_effect=interrupted))
                    else:
                        mocks.enter_context(patch.object(sqlite3, 'connect', side_effect=controlled_connect))
                with self.assertRaises(RepositoryError) as error:
                    component.finalize(setup=setup)
                self.assertEqual(error.exception.code, 'cancelled')
                self.assertTrue(injected)
                result = fixture.stored()
                self.assertEqual('submitted_result' in result, after_commit)
                with connect(fixture.state) as fresh:
                    self.assertEqual('submitted_result' in json.loads(fresh.execute('SELECT payload FROM role_assignments').fetchone()[0]), after_commit)
                if after_commit:
                    self.assertEqual(committed, [component.result])
                    self.assertEqual(result['submitted_result'], component.result)
                    for flag in ('trusted_execution', 'advance_allowed', 'close_allowed'):
                        self.assertFalse(result['result_disposition'][flag])
                    self.assertNotEqual(result['runtime']['status'], 'complete')






if __name__ == '__main__':
    unittest.main()
