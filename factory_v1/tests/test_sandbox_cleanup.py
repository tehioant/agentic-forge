"""Reviewer regressions: actual native lifecycle code, injected external failures only.
No Docker/model acceptance claims, source mutation, commits or permission changes.
"""
import json
import os
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
        for host, context, admitted in (('unix:///var/run/docker.sock', 'default', True),
                                         ('tcp://remote:2375', 'default', False),
                                         ('ssh://remote', 'default', False),
                                         ('unix:///run/user/1000/docker.sock', 'remote', False)):
            with self.subTest(host=host, context=context), patch.dict(os.environ, {'DOCKER_HOST': host, 'DOCKER_CONTEXT': context}), \
                    patch('factory_v1.sandbox_policy.shutil.which', return_value='/fixture/docker'):
                if admitted:
                    self.assertEqual(Docker().env['DOCKER_HOST'], host)
                    self.assertNotIn('DOCKER_CONTEXT', Docker().env)
                else:
                    with self.assertRaises(RepositoryError) as refusal:
                        Docker()
                    self.assertEqual(refusal.exception.code, 'storage_unbounded')
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
        cases += [(adapter, defect) for adapter in ('assignment', 'reconcile')
                  for defect in ('logs', 'artifact', 'marker', 'root', 'remove', 'marker-remove')]
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
                if defect in {'remove', 'marker-remove'}:
                    docker.remove.side_effect = RepositoryError('stop_unconfirmed', 'LABELED removal failure')
                if defect == 'artifact':
                    stack.enter_context(patch.object(Path, 'write_bytes', side_effect=PermissionError('injected log write refusal')))
                if defect in {'marker', 'marker-remove'}:
                    touch = Path.touch
                    def marker_touch(path, *args, **kwargs):
                        if path == self.root / 'stop':
                            raise OSError(28, 'labeled full artifact filesystem')
                        return touch(path, *args, **kwargs)
                    stack.enter_context(patch.object(Path, 'touch', marker_touch))
                if defect == 'root':
                    stack.enter_context(patch.object(sandbox, 'physical', side_effect=RepositoryError('unsafe_path', 'labeled missing root')))
                stack.enter_context(patch('factory_v1.sandbox_guard.process_identity', return_value=None))
                stack.enter_context(patch.object(sandbox, 'process_identity', return_value=None))
                stack.enter_context(patch('factory_v1.sandbox_policy.Docker', return_value=docker))
                stack.enter_context(patch('factory_v1.sandbox.Docker', return_value=docker))
                if adapter == 'emergency':
                    result = controls.request(self.database, 'p', 'i', 42, 'emergency-stop', 'LABELED cleanup failure matrix')
                    outcome = result['cleanup']['a']
                else:
                    result = (sandbox.reconcile if adapter == 'reconcile' else sandbox.stop_assignment)(self.database, 'p', 'i', 42, 'a')
                    self.assertIsNotNone(result)
                    outcome = result['runtime']
                    self.assertEqual(outcome['status'], 'unconfirmed' if 'remove' in defect else 'stopped')
                    self.assertEqual(outcome['error'], 'stop_unconfirmed' if 'remove' in defect else
                                     'controller_lost' if adapter == 'reconcile' else 'cancelled')
                    stored = json.loads(self.database.execute('SELECT payload FROM role_assignments').fetchone()[0])
                    self.assertEqual(stored['runtime'], outcome)
                    if defect in {'marker', 'root', 'marker-remove'}:
                        self.assertEqual(outcome['diagnostics_error'], 'artifacts_unavailable')
                    if defect == 'artifact':
                        self.assertEqual(json.loads((self.root / 'container-log-error.json').read_text())['type'], 'PermissionError')
                self.assertEqual((self.root / 'stop').exists(), defect not in {'marker', 'root', 'marker-remove'})
                docker.remove.assert_called_once_with('fixture')
                self.assertEqual(outcome['container_removed'], 'remove' not in defect)
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
        for failure in (None, FileNotFoundError('injected Docker disappearance during cleanup'),
                        RepositoryError('stop_unconfirmed', 'injected uncertain removal')):
            with self.subTest(failure=type(failure).__name__), ExitStack() as stack:
                stack.enter_context(patch.object(Path, 'touch', side_effect=OSError(28, 'labeled guard-finish ENOSPC')))
                restored = stack.enter_context(patch.object(sandbox.signal, 'signal'))
                guard, broker = Mock(), Mock()
                broker.is_alive.return_value = False
                with self.database:
                    self.database.execute('UPDATE role_assignments SET payload=?', (json.dumps(self.assignment),))
                docker = Mock()
                docker.remove.side_effect = failure
                runtime = self.assignment['runtime']
                result = sandbox.cleanup_attempt(str(self.state), 'p', 'i', 42, 'a', 'r', self.root,
                    runtime, threading.Event(), docker, guard, broker, [], None, {2: 'previous-handler'},
                    'failed', 'source_blocked', str(self.root / 'config.json'))
                docker.remove.assert_called_once_with('fixture')
                guard.wait.assert_called_once_with(timeout=35)
                broker.join.assert_called_once_with(timeout=1)
                restored.assert_called_once_with(2, 'previous-handler')
                self.assertEqual(result['runtime']['status'], 'unconfirmed' if failure else 'failed')
                self.assertEqual(result['runtime']['error'], 'stop_unconfirmed' if failure else 'source_blocked')
                self.assertEqual(result['runtime']['container_removed'], failure is None)
                self.assertEqual(result['runtime']['diagnostics_error'], 'artifacts_unavailable')




if __name__ == '__main__':
    unittest.main(verbosity=2)
