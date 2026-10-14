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

    def test_cancellation_joins_with_finite_bound(self):
        with self.assertRaises(RepositoryError) as caught:
            self.interrupted()
        self.assertEqual(caught.exception.code, 'execution_uncertain')
        self.kill.assert_called_once_with(12345, signal.SIGKILL)
        self.process.wait.assert_called_once_with(timeout=5)

    def test_signal_error_still_joins_and_remains_uncertain(self):
        self.kill.side_effect = PermissionError('fixture')
        with self.assertRaises(RepositoryError) as caught:
            self.interrupted()
        self.assertEqual(caught.exception.code, 'execution_uncertain')
        self.process.wait.assert_called_once_with(timeout=5)

    def test_join_timeout_and_oserror_are_structured(self):
        for failure in (subprocess.TimeoutExpired('executor', 5), OSError('fixture')):
            with self.subTest(failure=type(failure).__name__):
                self.process.wait.side_effect = failure
                with self.assertRaises(RepositoryError) as caught:
                    self.interrupted()
                self.assertEqual(caught.exception.code, 'execution_uncertain')
                self.assertEqual(self.process.wait.call_args.kwargs, {'timeout': 5})

    def test_execution_oserror_is_uncertain_after_cleanup(self):
        self.process.communicate.side_effect = OSError('fixture')
        with self.assertRaises(RepositoryError) as caught:
            execution.invoke(self.config, {})
        self.assertEqual(caught.exception.code, 'execution_uncertain')
        self.process.wait.assert_called_once_with(timeout=5)

    def test_original_control_exception_survives_confirmed_cleanup(self):
        original = RepositoryError('iteration_paused', 'fixture')
        self.process.communicate.side_effect = original
        with self.assertRaises(RepositoryError) as caught:
            execution.invoke(self.config, {})
        self.assertIs(caught.exception, original)
        self.process.wait.assert_called_once_with(timeout=5)

    def test_spawn_oserror_is_structured_without_false_join(self):
        self.spawn.side_effect = OSError('fixture')
        with self.assertRaises(RepositoryError) as caught:
            execution.invoke(self.config, {})
        self.assertEqual(caught.exception.code, 'execution_uncertain')
        self.process.wait.assert_not_called()
        self.kill.assert_not_called()

    def test_default_no_control_success_preserves_result(self):
        produced = {'result': [], 'execution': {'reference': 'fixture'}}
        def spawn(*args, **kwargs):
            kwargs['stdout'].write(json.dumps(produced).encode())
            return self.process
        self.spawn.side_effect = spawn
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

    def test_pause_after_last_outside_read_prevents_spawn(self):
        original = controls.blocked
        reads = 0
        paused = threading.Event()
        def read_return_pause(*args):
            nonlocal reads
            prior = original(*args)
            reads += 1
            if reads == 2:
                self.pause()
                paused.set()
            return prior
        with patch.object(controls, 'blocked', side_effect=read_return_pause):
            with self.assertRaises(RepositoryError) as caught:
                self.synthesize()
        self.assertTrue(paused.is_set())
        self.spawn.assert_not_called()
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.kill.assert_not_called()
        self.assertTrue(controls.blocked(self.db, 'p', 'i'))
        self.assertIn('service_attempt', self.stored()['ticket_work'])
        self.assertNotIn('service_receipt', self.stored()['ticket_work'])
        self.complete.assert_not_called()

    def test_already_paused_never_spawns(self):
        self.pause()
        with self.assertRaises(RepositoryError) as caught:
            self.synthesize()
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.spawn.assert_not_called()

    def test_lane_covers_only_actual_spawn_not_execution(self):
        root, _ = controls.paths(self.db, 'p', 'i')
        assert root is not None
        process = Mock(pid=12345, returncode=0)
        def lock_available():
            with (root / 'lock').open('r+') as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return False
                return True
        def spawn(*args, **kwargs):
            self.assertFalse(lock_available())
            kwargs['stdout'].write(json.dumps(self.produced).encode())
            return process
        def communicate(*args, **kwargs):
            self.assertTrue(lock_available())
        self.spawn.side_effect = spawn
        process.communicate.side_effect = communicate
        self.synthesize()
        self.spawn.assert_called_once()
        self.kill.assert_not_called()
        self.complete.assert_called_once()

    def test_actual_caller_passes_short_scoped_admission_and_payload(self):
        event = threading.Event()
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
        def approved(capability, work, *, cancelled, admission):
            self.assertEqual(capability, self.capability)
            self.assertEqual(work['inputs'], self.work['inputs'])
            self.assertIn('service_attempt', self.stored()['ticket_work'])
            self.assertFalse(cancelled())
            with admission():
                self.assertTrue(lane_active)
            self.assertFalse(lane_active)
            return deepcopy(self.produced)
        with patch.object(controls, 'admission', side_effect=observed), patch.object(execution, 'invoke', side_effect=approved):
            self.assertEqual(self.synthesize(stop_event=event), self.item)
        self.assertEqual(self.authority.call_count, 2)
        self.assertEqual(self.source.call_count, 2)
        self.validate.assert_called_once_with(self.work, self.produced['execution'], self.produced['result'])
        self.complete.assert_called_once()
        self.assertEqual(self.stored()['ticket_work']['service_receipt'], self.produced)
        self.spawn.assert_not_called()

    def test_stop_reread_inside_spawn_lane(self):
        event = threading.Event()
        def approved(_config, _work, *, cancelled, admission):
            self.assertFalse(cancelled())
            event.set()
            with admission():
                self.fail('stop admitted')
        with patch.object(execution, 'invoke', side_effect=approved):
            with self.assertRaises(RepositoryError) as caught:
                self.synthesize(stop_event=event)
        self.assertEqual(caught.exception.code, 'service_stopping')
        self.spawn.assert_not_called()

    def test_receipt_survives_pause_without_result_admission(self):
        def approved(*args, **kwargs):
            self.pause()
            return self.produced
        with patch.object(execution, 'invoke', side_effect=approved):
            with self.assertRaises(RepositoryError) as caught:
                self.synthesize()
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.assertEqual(self.stored()['ticket_work']['service_receipt'], self.produced)
        self.complete.assert_not_called()

    def test_revision_spawn_uses_existing_checkpoint_hold_semantics(self):
        record = {'status': 'resolved', 'affected': [10], 'requirements': {'10': 'changed'}}
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
