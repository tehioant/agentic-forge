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
        item = {'approval': {'operator_id': 42, 'reference': 'LABELED_REVIEW_FIXTURE'},
                'execution_allowed': False, 'status': 'active'}
        self.database.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(item)))
        self.assignment = {'assignment_id': 'a', 'ticket_scope': {'project': 'p', 'iteration': 'i'},
                           'runtime': {'run_id': 'r', 'status': 'running', 'container': 'fixture',
                                       'artifacts': str(self.root)}}
        self.database.execute('INSERT INTO role_assignments VALUES (?,?)', ('a', json.dumps(self.assignment)))
        self.database.commit()

    def test_docker_call_normalizes_exec_oserror(self):
        with patch('factory_v1.sandbox_policy.shutil.which', return_value='/fixture/docker'):
            docker = Docker()
        with patch('factory_v1.sandbox_policy.subprocess.run', side_effect=FileNotFoundError('injected executable vanished')):
            with self.assertRaises(RepositoryError):
                docker.call('rm', '-f', 'fixture')

    def test_docker_inspect_normalizes_exec_oserror(self):
        with patch('factory_v1.sandbox_policy.shutil.which', return_value='/fixture/docker'):
            docker = Docker()
        with patch('factory_v1.sandbox_policy.subprocess.run', side_effect=PermissionError('injected exec refusal')):
            with self.assertRaises(RepositoryError):
                docker.inspect('fixture', optional=True)

    def test_stop_removes_even_when_optional_log_capture_fails(self):
        docker = Mock()
        docker.call.side_effect = RepositoryError('container_unavailable', 'injected log timeout')
        with patch('factory_v1.sandbox.Docker', return_value=docker):
            try:
                sandbox.stop_assignment(self.database, 'p', 'i', 42, 'a')
            except RepositoryError:
                pass
        self.assertTrue((self.root / 'stop').exists())
        docker.remove.assert_called_once_with('fixture')

    def test_guard_removes_even_when_optional_log_capture_fails(self):
        record = {'seconds': 1, 'controller_pid': 123, 'controller_start': 'fixture',
                  'container': 'fixture', 'capability': str(self.root / 'absent-capability')}
        path = self.root / 'guard.json'
        path.write_text(json.dumps(record))
        (self.root / 'guard-finish').touch()
        docker = Mock()
        docker.call.side_effect = RepositoryError('container_unavailable', 'injected log timeout')
        with patch('sys.argv', ['guard', str(path)]), patch('factory_v1.sandbox_guard.Docker', return_value=docker):
            sandbox_guard.main()
        observed = json.loads((self.root / 'guard-result.json').read_text())
        print('guard failure readback:', observed)
        docker.remove.assert_called_once_with('fixture')

    def test_launch_cleanup_oserror_is_retained_as_unconfirmed(self):
        artifacts = self.root / 'artifacts'
        artifacts.mkdir()
        assignment = {'assignment_id': 'a', 'ticket_scope': {'project': 'p', 'iteration': 'i'},
                      'handoff': {'profile': {'home': str(self.root / 'absent-home')},
                                  'workspace': str(self.root / 'source'), 'baseline': 'b' * 40,
                                  'candidate': None}}
        config = {'subscription_socket': str(self.root / 'unused-socket'),
                  'artifacts_root': str(artifacts), 'image': 'fixture', 'limits': {'seconds': 1}}
        (self.root / 'source').mkdir()
        (self.root / 'config.json').write_text(json.dumps(config))
        docker = Mock()
        docker.image.return_value = 'fixture'
        docker.remove.side_effect = FileNotFoundError('injected Docker disappearance during cleanup')
        with ExitStack() as stack:
            stack.enter_context(patch('factory_v1.sandbox.load_config', return_value=config))
            stack.enter_context(patch('factory_v1.sandbox.Docker', return_value=docker))
            stack.enter_context(patch('factory_v1.model_access.transport'))
            stack.enter_context(patch('factory_v1.sandbox.assignments.inspect', return_value=assignment))
            stack.enter_context(patch('factory_v1.sandbox.assignments.prepare'))
            stack.enter_context(patch('factory_v1.sandbox.original', return_value={}))
            stack.enter_context(patch('factory_v1.sandbox.snapshot', side_effect=RepositoryError('source_blocked', 'injected snapshot refusal')))
            stack.enter_context(patch('factory_v1.sandbox.signal.signal'))
            try:
                result = sandbox.launch(str(self.state), 'p', 'i', 42, 'a', str(self.root / 'config.json'), None)
            except OSError as error:
                saved = json.loads(self.database.execute('SELECT payload FROM role_assignments').fetchone()[0])
                print('launch escaped:', type(error).__name__, 'persisted runtime:', saved['runtime']['status'])
                self.fail('cleanup OSError escaped instead of retaining unconfirmed runtime')
        self.assertEqual(result['runtime']['status'], 'unconfirmed')
        self.assertEqual(result['runtime']['error'], 'stop_unconfirmed')
        self.assertFalse(result['runtime']['container_removed'])


    def test_stop_removes_even_when_log_artifact_write_fails(self):
        docker = Mock()
        docker.call.return_value = b'labeled fixture logs'
        with patch('factory_v1.sandbox.Docker', return_value=docker), \
                patch.object(Path, 'write_bytes', side_effect=PermissionError('injected log write refusal')):
            result = sandbox.stop_assignment(self.database, 'p', 'i', 42, 'a')
        docker.remove.assert_called_once_with('fixture')
        assert result is not None
        self.assertEqual(result['runtime']['status'], 'stopped')
        self.assertTrue(result['runtime']['container_removed'])
        self.assertEqual(json.loads((self.root / 'container-log-error.json').read_text())['type'], 'PermissionError')

    def test_guard_removes_even_when_log_artifact_write_fails(self):
        path = self.root / 'guard.json'
        path.write_text(json.dumps({'seconds': 1, 'controller_pid': 123, 'controller_start': 'fixture',
                                    'container': 'fixture', 'capability': str(self.root / 'absent')}))
        (self.root / 'guard-finish').touch()
        docker = Mock()
        docker.call.return_value = b'labeled fixture logs'
        with patch('sys.argv', ['guard', str(path)]), \
                patch('factory_v1.sandbox_guard.Docker', return_value=docker), \
                patch.object(Path, 'write_bytes', side_effect=PermissionError('injected log write refusal')):
            sandbox_guard.main()
        docker.remove.assert_called_once_with('fixture')
        self.assertEqual(json.loads((self.root / 'guard-result.json').read_text())['status'], 'stopped')

if __name__ == '__main__':
    unittest.main(verbosity=2)
