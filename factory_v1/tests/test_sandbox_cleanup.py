"""Reviewer regressions: actual native lifecycle code, injected external failures only.
No Docker/model acceptance claims, source mutation, commits or permission changes.
"""
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import sandbox, sandbox_guard
from factory_v1.repositories import RepositoryError
from factory_v1.sandbox_policy import Docker


class CleanupErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cleanup-probe-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state.sqlite'
        self.database = sqlite3.connect(self.state)
        self.addCleanup(self.database.close)
        self.database.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
        self.database.execute('CREATE TABLE role_assignments (assignment_id TEXT, payload TEXT)')
        item = {'project_id': 'p', 'iteration_id': 'i',
                'approval': {'operator_id': 42, 'reference': 'LABELED_REVIEW_FIXTURE'},
                'execution_allowed': False, 'status': 'active'}
        self.database.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(item)))
        self.assignment = {'assignment_id': 'a', 'ticket_scope': {'project': 'p', 'iteration': 'i'},
                           'runtime': {'run_id': 'r', 'status': 'running', 'container': 'fixture',
                                       'artifacts': str(self.root)}}
        self.database.execute('INSERT INTO role_assignments VALUES (?,?)', ('a', json.dumps(self.assignment)))
        self.database.commit()

    def test_docker_exec_errors_structured_matrix(self):
        with patch('factory_v1.sandbox_policy.shutil.which', return_value='/fixture/docker'):
            docker = Docker()
        for operation, args, kwargs, error in (
            (docker.call, ('rm', '-f', 'fixture'), {}, FileNotFoundError('injected executable vanished')),
            (docker.inspect, ('fixture',), {'optional': True}, PermissionError('injected exec refusal')),
        ):
            with self.subTest(operation=operation.__name__), patch('factory_v1.sandbox_policy.subprocess.run', side_effect=error):
                with self.assertRaises(RepositoryError):
                    operation(*args, **kwargs)


    def test_stop_cleanup_optional_errors_still_remove_matrix(self):
        from copy import deepcopy
        from factory_v1 import controls
        baseline = deepcopy(self.assignment)
        cases = [('emergency', defect) for defect in ('kill', 'inspect', 'logs', 'artifact', 'remove')]
        cases += [('assignment', defect) for defect in ('logs', 'artifact')]
        for adapter, defect in cases:
            with self.subTest(adapter=adapter, defect=defect), ExitStack() as stack:
                assignment = deepcopy(baseline)
                assignment['runtime'].update(controller_pid=123, controller_start='LABELED absent process')
                with self.database:
                    self.database.execute('UPDATE role_assignments SET payload=?', (json.dumps(assignment),))
                _, marker = controls.paths(self.database, 'p', 'i')
                if marker is not None:
                    marker.unlink(missing_ok=True)
                for name in ('stop', 'container.log', 'container-log-error.json'):
                    (self.root / name).unlink(missing_ok=True)
                docker = Mock()
                calls = []
                def call(command, *args, **kwargs):
                    calls.append(command)
                    if command == defect:
                        raise RepositoryError('container_unavailable', 'LABELED injected Docker failure')
                    return b'labeled fixture logs'
                docker.call.side_effect = call
                docker.inspect.return_value = {'State': {'Running': False}}
                if defect == 'inspect':
                    docker.inspect.side_effect = RepositoryError('container_unavailable', 'LABELED inspect failure')
                if defect == 'remove':
                    docker.remove.side_effect = RepositoryError('stop_unconfirmed', 'LABELED removal failure')
                if defect == 'artifact':
                    stack.enter_context(patch.object(Path, 'write_bytes', side_effect=PermissionError('injected log write refusal')))
                stack.enter_context(patch('factory_v1.sandbox_guard.process_identity', return_value=None))
                stack.enter_context(patch('factory_v1.sandbox_policy.Docker', return_value=docker))
                stack.enter_context(patch('factory_v1.sandbox.Docker', return_value=docker))
                if adapter == 'emergency':
                    result = controls.request(self.database, 'p', 'i', 42, 'emergency-stop', 'LABELED cleanup failure matrix')
                    outcome = result['cleanup']['a']
                else:
                    result = sandbox.stop_assignment(self.database, 'p', 'i', 42, 'a')
                    self.assertIsNotNone(result)
                    outcome = result['runtime']
                    self.assertEqual(outcome['status'], 'stopped')
                    if defect == 'artifact':
                        self.assertEqual(json.loads((self.root / 'container-log-error.json').read_text())['type'], 'PermissionError')
                self.assertTrue((self.root / 'stop').exists())
                docker.remove.assert_called_once_with('fixture')
                self.assertEqual(outcome['container_removed'], defect != 'remove')
                if adapter == 'emergency':
                    self.assertIn('kill', calls)
                    self.assertEqual(calls[0], 'kill')
                    self.assertEqual(outcome['error'], 'stop_unconfirmed' if defect in {'kill', 'inspect', 'remove'} else None)
                    if defect in {'kill', 'inspect'}:
                        self.assertNotIn('logs', calls, 'Do not defer fallback removal when halt is unconfirmed')



    def test_guard_cleanup_optional_errors_still_remove_matrix(self):
        path = self.root / 'guard.json'
        path.write_text(json.dumps({'seconds': 1, 'controller_pid': 123, 'controller_start': 'fixture',
                                   'container': 'fixture', 'capability': str(self.root / 'absent')}))
        (self.root / 'guard-finish').touch()
        for failure in ('logs', 'artifact'):
            with self.subTest(failure=failure), ExitStack() as stack:
                for name in ('container.log', 'guard-result.json'):
                    (self.root / name).unlink(missing_ok=True)
                docker = Mock()
                docker.call.return_value = b'labeled fixture logs'
                if failure == 'logs':
                    docker.call.side_effect = RepositoryError('container_unavailable', 'injected log timeout')
                else:
                    stack.enter_context(patch.object(Path, 'write_bytes', side_effect=PermissionError('injected log write refusal')))
                stack.enter_context(patch('sys.argv', ['guard', str(path)]))
                stack.enter_context(patch('factory_v1.sandbox_guard.Docker', return_value=docker))
                sandbox_guard.main()
                docker.remove.assert_called_once_with('fixture')
                self.assertEqual(json.loads((self.root / 'guard-result.json').read_text())['status'], 'stopped')

    def test_launch_cleanup_unconfirmed_matrix(self):
        import threading
        for failure in (FileNotFoundError('injected Docker disappearance during cleanup'),
                        RepositoryError('stop_unconfirmed', 'injected uncertain removal')):
            with self.subTest(failure=type(failure).__name__):
                with self.database:
                    self.database.execute('UPDATE role_assignments SET payload=?', (json.dumps(self.assignment),))
                docker = Mock()
                docker.remove.side_effect = failure
                runtime = self.assignment['runtime']
                result = sandbox.cleanup_attempt(str(self.state), 'p', 'i', 42, 'a', 'r', self.root,
                    runtime, threading.Event(), docker, None, None, [], None, {},
                    'failed', 'source_blocked', str(self.root / 'config.json'))
                docker.remove.assert_called_once_with('fixture')
                self.assertEqual(result['runtime']['status'], 'unconfirmed')
                self.assertEqual(result['runtime']['error'], 'stop_unconfirmed')
                self.assertFalse(result['runtime']['container_removed'])




if __name__ == '__main__':
    unittest.main(verbosity=2)
