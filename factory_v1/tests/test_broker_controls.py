"""Durable broker interruption at admitted I/O; transport and scheduling are mocked."""
import json
import os
import sqlite3
import struct
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from factory_v1 import controls, spending
from factory_v1.model_access import SUBSCRIPTION_SCOPE


class BrokerControlFailureTests(unittest.TestCase):
    def test_database_failure_cancels_admitted_transport_and_keeps_billing_uncertain(self):
        for defect in ('connect', 'read'):
            with self.subTest(defect=defect):
                self._assert_admitted_cancellation(defect=defect)

    def test_durable_pause_cancels_admitted_transport_and_denies_retries(self):
        self._assert_admitted_cancellation(action='pause')

    def test_durable_emergency_stop_cancels_admitted_transport_and_denies_retries(self):
        self._assert_admitted_cancellation(action='emergency-stop')

    def _assert_admitted_cancellation(self, *, defect=None, action=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, capability = root / 'state.sqlite', root / 'capability.sock'
            scope = ('product', 'm1', *SUBSCRIPTION_SCOPE)
            with sqlite3.connect(state) as database:
                database.execute('CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT)')
                item = {'project_id': 'product', 'iteration_id': 'm1', 'status': 'active', 'execution_allowed': False,
                        'approval': {'operator_id': '42', 'reference': 'test-grant'}}
                database.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(item)))
                spending.initialize(database)
                spending.add_grant(database, *scope, 'allowance', 2, 2**40, 'test-allowance')
            database.close()
            stop = threading.Event()
            adapter = Mock(reservation=1, payload_fields={'input'})
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connection.getsockopt.return_value = struct.pack('3i', 123, os.getuid(), 123)
            payload = {'input': 'test-input'}
            connection.recv.return_value = json.dumps({'operation_id': 'test-operation', 'reserve': 1,
                                                      'payload': payload}).encode() + b'\n'
            server = MagicMock()
            server.__enter__.return_value = server
            server.bind.side_effect = lambda path: Path(path).touch()
            server.accept.return_value = (connection, None)
            targets = []
            helper = Mock(ident=1)

            def prepare_thread(*, target, name, daemon):
                self.assertEqual(name, 'model-broker-canceller')
                self.assertFalse(daemon)
                targets.append(target)
                return helper

            connect, blocked = sqlite3.connect, controls.blocked
            in_control_read = False
            control_reads, control_failures, canceller_stopped = [], [], []

            def controlled_connect(*args, **kwargs):
                if in_control_read and defect == 'connect':
                    control_failures.append('connect')
                    raise sqlite3.OperationalError('test-control-database-unavailable')
                return connect(*args, **kwargs)

            def controlled_read(*args):
                if in_control_read and defect == 'read':
                    control_failures.append('read')
                    raise sqlite3.OperationalError('test-control-record-unavailable')
                interrupted = blocked(*args)
                if in_control_read:
                    control_reads.append((interrupted, stop.is_set(), adapter.cancel.call_count))
                return interrupted

            def finish_if_guard_ignored(event, timeout):
                # Bound a missing boolean guard to one read, without setting the stop signal.
                event.set()
                return True

            def outbound(_):
                nonlocal in_control_read
                self.assertEqual(len(targets), 1)
                helper.start.assert_called_once()
                with connect(state) as database:
                    self.assertEqual(database.execute('SELECT status,actual_units FROM model_operations').fetchall(),
                                     [('pending', None)])
                    if action:
                        controls.request(database, *scope[:2], '42', action, 'test-interruption')
                database.close()
                in_control_read = True
                try:
                    targets[0]()
                finally:
                    in_control_read = False
                canceller_stopped.append(stop.is_set())
                # Retire the mocked accept loop even if the canceller missed the hold.
                stop.set()
                return 1, {'text': 'untrusted late response'}

            adapter.call.side_effect = outbound
            with ExitStack() as mocks:
                mocks.enter_context(patch.object(spending.socket, 'socket', return_value=server))
                mocks.enter_context(patch.object(spending.threading, 'Thread', side_effect=prepare_thread))
                wait = mocks.enter_context(patch.object(threading.Event, 'wait', autospec=True,
                                                       side_effect=finish_if_guard_ignored))
                mocks.enter_context(patch.object(spending, 'transport', return_value=adapter))
                mocks.enter_context(patch.object(spending.sqlite3, 'connect', side_effect=controlled_connect))
                mocks.enter_context(patch.object(controls, 'blocked', side_effect=controlled_read))
                spending._serve_unix_socket(str(capability), str(state), scope, None, 1, '42', None,
                                           1, 10, 1, os.getuid(), spending.MAX_REQUEST, stop, None, None)
            adapter.call.assert_called_once_with(payload)
            helper.start.assert_called_once()
            helper.join.assert_called_once_with()
            self.assertEqual(len(targets), 1)
            self.assertEqual(canceller_stopped, [True],
                             'The canceller must act on the first blocked control read.')
            self.assertTrue(stop.is_set())
            adapter.cancel.assert_called_once_with()
            connection.shutdown.assert_called_once_with(spending.socket.SHUT_RD)
            server.shutdown.assert_called_once_with(spending.socket.SHUT_RDWR)
            wait.assert_not_called()
            self.assertEqual(control_failures, [defect] if defect else [])
            self.assertEqual(control_reads, [] if defect else [(True, False, 0)])
            server.accept.assert_called_once()
            self.assertFalse(capability.exists())
            response = json.loads(connection.sendall.call_args.args[0])
            self.assertEqual(response['error'], 'outcome_uncertain')
            for operation_id in ('test-operation', 'new-operation'):
                with self.subTest(operation_id=operation_id), connect(state) as database:
                    if action:
                        retained_control = controls.read(database, *scope[:2])
                        assert retained_control is not None
                        self.assertEqual(retained_control['action'], action)
                        self.assertTrue(controls.blocked(database, *scope[:2]))
                        self.assertEqual(retained_control['requested_checkpoint']['pending_model_operations'],
                                         [['test-operation', 'pending', 1, None]])
                    with self.assertRaises(spending.SpendingError) as refused:
                        spending.execute_controlled(database, scope, operation_id, payload, 1, adapter=adapter)
                    self.assertEqual(refused.exception.code, 'iteration_paused' if action else 'outcome_uncertain')
                database.close()
            adapter.call.assert_called_once_with(payload)
            with connect(state) as database:
                self.assertEqual(database.execute('SELECT operation_id,status,actual_units FROM model_operations').fetchall(),
                                 [('test-operation', 'pending', None)])
                self.assertEqual(database.execute('SELECT remaining FROM model_grants').fetchone()[0], 1)
                retained = json.loads(database.execute('SELECT payload FROM iterations').fetchone()[0])
                self.assertEqual(retained['status'], 'active')
                self.assertEqual(retained['model_access']['reason'], 'outcome_uncertain')
            database.close()
