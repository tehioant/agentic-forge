"""Direct SQLite admission and mocked host execution; no preparation journeys."""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import ANY, Mock, patch

from factory_v1 import controls, requirement_execution as execution, service_dispatch as dispatch, tickets
from factory_v1.repositories import RepositoryError


class SynthesisCleanupTests(unittest.TestCase):
    def setUp(self):
        self.process = Mock(pid=12345, returncode=0)
        self.spawn = self.enterContext(patch.object(execution.subprocess, 'Popen', return_value=self.process))
        self.kill = self.enterContext(patch.object(execution.os, 'killpg'))
        self.enterContext(patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')))
        self.config = {'argv': ['/approved/executor'], 'timeout': 10}

    def interrupted(self):
        self.process.communicate.side_effect = subprocess.TimeoutExpired('executor', .1)
        return execution.invoke(self.config, {}, cancelled=Mock(side_effect=[False, False, True]))

    def test_cancellation_finite_join_and_uncertainty_matrix(self):
        original = RepositoryError('iteration_paused', 'fixture')
        for label, communication, signal_error, wait_error, expected in (
            ('cancelled', subprocess.TimeoutExpired('executor', .1), None, None, 'execution_uncertain'),
            ('signal-error', subprocess.TimeoutExpired('executor', .1), PermissionError('fixture'), None, 'execution_uncertain'),
            ('join-timeout', subprocess.TimeoutExpired('executor', .1), None, subprocess.TimeoutExpired('executor', 5), 'execution_uncertain'),
            ('join-oserror', subprocess.TimeoutExpired('executor', .1), None, OSError('fixture'), 'execution_uncertain'),
            ('execution-oserror', OSError('fixture'), None, None, 'execution_uncertain'),
            ('original-control', original, None, None, 'iteration_paused'),
        ):
            with self.subTest(case=label):
                self.process.reset_mock()
                self.kill.reset_mock()
                self.process.communicate.side_effect = communication
                self.process.wait.side_effect = wait_error
                self.kill.side_effect = signal_error
                with self.assertRaises(RepositoryError) as caught:
                    execution.invoke(self.config, {}, cancelled=Mock(side_effect=[False, False, True]))
                self.assertEqual(caught.exception.code, expected)
                self.kill.assert_called_once_with(12345, signal.SIGKILL)
                self.process.wait.assert_called_once_with(timeout=5)
                if label == 'original-control':
                    self.assertIs(caught.exception, original)





    def test_spawn_failure_and_success_result_matrix(self):
        produced = {'result': [], 'execution': {'reference': 'fixture'}}
        for fail_spawn in (False, True):
            with self.subTest(fail_spawn=fail_spawn):
                self.process.reset_mock()
                self.kill.reset_mock()
                def spawn(*args, **kwargs):
                    if fail_spawn:
                        raise OSError('fixture')
                    kwargs['stdout'].write(json.dumps(produced).encode())
                    return self.process
                self.spawn.side_effect = spawn
                if fail_spawn:
                    with self.assertRaises(RepositoryError) as caught:
                        execution.invoke(self.config, {'input': 'fixture'})
                    self.assertEqual(caught.exception.code, 'execution_uncertain')
                else:
                    self.assertEqual(execution.invoke(self.config, {'input': 'fixture'}), produced)
                    self.process.communicate.assert_called_once_with(b'{"input": "fixture"}', timeout=ANY)
                self.kill.assert_not_called()
                self.process.wait.assert_not_called()



class SynthesisAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = sqlite3.connect(Path(temporary.name) / 'state.sqlite')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
        self.db.execute('CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT)')
        descriptor = {'name': 'to-tickets', 'path': str(Path.home() / '.hermes/skills/to-tickets/SKILL.md'),
                      'sha256': 'a' * 64}
        self.skill = {**descriptor, 'dependencies': []}
        self.work = {'assignment_id': 'b' * 64, 'input_digest': 'c' * 64, 'adaptation': tickets.ADAPTATION,
                     'request': {'skill': descriptor}, 'skill': self.skill,
                     'inputs': {'documents': {'milestone.md': {'url': 'fixture:milestone', 'sha256': 'd' * 64}}}}
        self.item = {'project_id': 'p', 'iteration_id': 'i', 'repository': 'owner/repo', 'status': 'active',
                     'approval': {'operator_id': 'operator', 'reference': 'fixture:approval'}, 'ticket_work': self.work}
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('p', 'i', json.dumps(self.item)))
        self.db.commit()
        self.config = {'synthesis_config': '/private/capability.json', 'operator_id': 'operator'}
        self.capability = {'argv': ['/approved/executor'], 'timeout': 10, 'execution_reference': 'host:fixture'}
        self.produced = {'result': [], 'execution': {'run_id': 'fixture:run', 'decomposition': 'fixture:direct',
            'result_digest': tickets.digest([]), 'loads': [
                {'source': self.skill['path'], 'sha256': self.skill['sha256'], 'tool_reference': 'fixture:skill'},
                {'source': 'fixture:milestone', 'sha256': 'd' * 64, 'tool_reference': 'fixture:input'}]}}
        self.enterContext(patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')))
        self.authority = self.enterContext(patch.object(execution, 'configuration', return_value=self.capability))
        self.source = self.enterContext(patch.object(tickets, 'skill', return_value=self.skill))
        self.enterContext(patch.object(tickets, 'pinned_inputs', return_value=self.work['inputs']))
        self.validate = self.enterContext(patch.object(tickets, 'validate_execution', wraps=tickets.validate_execution))
        self.complete = self.enterContext(patch.object(tickets, 'complete_synthesis', return_value=self.item))
        self.spawn = self.enterContext(patch.object(execution.subprocess, 'Popen', return_value=Mock(pid=12345)))
        self.kill = self.enterContext(patch.object(execution.os, 'killpg'))

    def pause(self):
        controls.request(self.db, 'p', 'i', 'operator', 'pause', 'fixture:pause')

    def stored(self):
        return json.loads(self.db.execute('SELECT payload FROM iterations').fetchone()[0])

    def synthesize(self, **kwargs):
        return dispatch.synthesize(self.db, self.item, self.config, Mock(), **kwargs)

    def test_pre_spawn_pause_and_stop_guards_matrix(self):
        initial = deepcopy(self.item)
        for phase in ('already-paused', 'last-outside-read', 'stop-in-lane'):
            with self.subTest(phase=phase):
                self.item = deepcopy(initial)
                self.work = self.item['ticket_work']
                with self.db:
                    self.db.execute('UPDATE iterations SET payload=?', (json.dumps(initial),))
                root, marker = controls.paths(self.db, 'p', 'i')
                if marker.exists():
                    marker.unlink()
                self.spawn.reset_mock()
                self.kill.reset_mock()
                self.complete.reset_mock()
                event = threading.Event()
                paused = threading.Event()
                assert root is not None and marker is not None
                def invoke_after_read(config, work, *, cancelled, admission):
                    def read_return_pause():
                        prior = cancelled()
                        if not paused.is_set():
                            with (root / 'lock').open('r+') as lock:
                                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                fcntl.flock(lock, fcntl.LOCK_UN)
                            self.pause()
                            paused.set()
                        return prior
                    return real_invoke(config, work, cancelled=read_return_pause, admission=admission)
                real_invoke = execution.invoke
                def approved(_config, _work, *, cancelled, admission):
                    self.assertFalse(cancelled())
                    event.set()
                    with admission():
                        self.fail('stop admitted')
                if phase == 'already-paused':
                    self.pause()
                invoke = real_invoke
                if phase == 'stop-in-lane':
                    invoke = approved
                elif phase == 'last-outside-read':
                    invoke = invoke_after_read
                with patch.object(execution, 'invoke', side_effect=invoke):
                    with self.assertRaises(RepositoryError) as caught:
                        self.synthesize(stop_event=event)
                self.spawn.assert_not_called()
                self.assertEqual(caught.exception.code, 'service_stopping' if phase == 'stop-in-lane' else 'iteration_paused')
                self.kill.assert_not_called()
                self.complete.assert_not_called()
                self.assertNotIn('service_receipt', self.stored()['ticket_work'])
                if phase == 'last-outside-read':
                    self.assertTrue(paused.is_set())
                    self.assertTrue(controls.blocked(self.db, 'p', 'i'))
                    self.assertIn('service_attempt', self.stored()['ticket_work'])


    def test_short_lane_and_payload_binding_matrix(self):
        root, _ = controls.paths(self.db, 'p', 'i')
        assert root is not None
        initial = deepcopy(self.item)
        for seam in ('real-invoke', 'caller-contract'):
            with self.subTest(seam=seam):
                self.item = deepcopy(initial)
                self.work = self.item['ticket_work']
                with self.db:
                    self.db.execute('UPDATE iterations SET payload=?', (json.dumps(initial),))
                self.complete.return_value = self.item
                for mocked in (self.authority, self.source, self.validate, self.complete, self.spawn, self.kill):
                    mocked.reset_mock()
                lane_active = False
                original = controls.admission
                @contextmanager
                def observed(*args, **kwargs):
                    nonlocal lane_active
                    self.assertEqual(args, (self.db, 'p', 'i'))
                    with original(*args, **kwargs):
                        lane_active = True
                        try:
                            yield
                        finally:
                            lane_active = False
                def lock_available():
                    with (root / 'lock').open('r+') as lock:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            return False
                        return True
                process = Mock(pid=12345, returncode=0)
                def spawn(*args, **kwargs):
                    self.assertTrue(lane_active)
                    self.assertFalse(lock_available())
                    kwargs['stdout'].write(json.dumps(self.produced).encode())
                    return process
                def communicate(*args, **kwargs):
                    self.assertFalse(lane_active)
                    self.assertTrue(lock_available())
                def approved(capability, work, *, cancelled, admission):
                    self.assertEqual(capability, self.capability)
                    self.assertEqual(work['inputs'], self.work['inputs'])
                    self.assertIn('service_attempt', self.stored()['ticket_work'])
                    self.assertFalse(cancelled())
                    with admission():
                        self.assertTrue(lane_active)
                    self.assertFalse(lane_active)
                    return deepcopy(self.produced)
                self.spawn.side_effect = spawn
                process.communicate.side_effect = communicate
                invoke = execution.invoke if seam == 'real-invoke' else approved
                with patch.object(controls, 'admission', side_effect=observed), patch.object(execution, 'invoke', side_effect=invoke):
                    self.assertEqual(self.synthesize(stop_event=threading.Event()), self.item)
                self.assertEqual(self.authority.call_count, 2)
                self.assertEqual(self.source.call_count, 2)
                self.validate.assert_called_once_with(self.work, self.produced['execution'], self.produced['result'])
                self.complete.assert_called_once()
                self.assertEqual(self.stored()['ticket_work']['service_receipt'], self.produced)
                self.kill.assert_not_called()
                if seam == 'real-invoke':
                    self.spawn.assert_called_once()
                else:
                    self.spawn.assert_not_called()



    def test_receipt_after_pause_not_admitted_matrix(self):
        def approved(*args, **kwargs):
            self.pause()
            return self.produced
        with patch.object(execution, 'invoke', side_effect=approved):
            with self.assertRaises(RepositoryError) as caught:
                self.synthesize()
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.assertEqual(self.stored()['ticket_work']['service_receipt'], self.produced)
        self.complete.assert_not_called()

    def test_revision_checkpoint_semantics_matrix(self):
        self.item['execution_allowed'] = False
        record = {'request': {'change_id': 'change'}, 'status': 'resolved', 'affected': [10], 'requirements': {'10': 'changed'}}
        self.item['requirement_changes'] = {'change': record}
        observed = []
        def approved(_config, _work, *, cancelled, admission):
            self.assertFalse(cancelled())
            with admission():
                observed.append('admitted-under-revision-hold')
            self.pause()
            with self.assertRaises(RepositoryError) as caught:
                with admission():
                    self.fail('paused revision admitted')
            self.assertEqual(caught.exception.code, 'iteration_paused')
            return self.produced
        with patch.object(execution, 'invoke', side_effect=approved):
            with self.assertRaises(RepositoryError) as caught:
                execution.run_stage(self.db, self.item, record, 'to-spec', self.capability, self.work)
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.assertEqual(observed, ['admitted-under-revision-hold'])
        retained = self.stored()['requirement_changes']['change']
        self.assertEqual(retained['execution_receipts']['to-spec'], self.produced)
        self.assertNotIn('executions', retained)
        self.assertIn('to-spec', retained['attempts'])
