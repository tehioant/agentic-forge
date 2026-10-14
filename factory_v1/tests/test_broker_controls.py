"""Broker control failures at the admitted-I/O seam; transport and scheduling are mocked."""
import json
import os
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from factory_v1 import controls, spending
from factory_v1.model_access import SUBSCRIPTION_SCOPE


class BrokerControlFailureTests(unittest.TestCase):
    def test_database_failure_cancels_admitted_transport_and_keeps_billing_uncertain(self):
        for defect in ('connect', 'read'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state, capability = root / 'state.sqlite', root / 'capability.sock'
                scope = ('product', 'm1', *SUBSCRIPTION_SCOPE)
                with sqlite3.connect(state) as database:
                    database.execute('CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT)')
                    item = {'project_id': 'product', 'iteration_id': 'm1', 'status': 'active', 'execution_allowed': False,
                            'approval': {'operator_id': '42', 'reference': 'test-grant'}}
                    database.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(item)))
                    spending.initialize(database)
                    spending.add_grant(database, *scope, 'allowance', 2, int(time.time()) + 600, 'test-allowance')
                database.close()
                stop = threading.Event()
                adapter = Mock(reservation=1, payload_fields={'input'})
                connection = MagicMock()
                connection.__enter__.return_value = connection
                connection.getsockopt.return_value = struct.pack('3i', 123, os.getuid(), 123)
                connection.recv.return_value = json.dumps({'operation_id': 'test-operation', 'reserve': 1,
                                                          'payload': {'input': 'test-input'}}).encode() + b'\n'
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
                def controlled_connect(*args, **kwargs):
                    if in_control_read and defect == 'connect':
                        raise sqlite3.OperationalError('test-control-database-unavailable')
                    return connect(*args, **kwargs)
                def controlled_read(*args):
                    if in_control_read and defect == 'read':
                        raise sqlite3.OperationalError('test-control-record-unavailable')
                    return blocked(*args)
                def outbound(_):
                    nonlocal in_control_read
                    self.assertEqual(len(targets), 1)
                    helper.start.assert_called_once()
                    with connect(state) as database:
                        self.assertEqual(database.execute('SELECT status,actual_units FROM model_operations').fetchall(),
                                         [('pending', None)])
                    database.close()
                    in_control_read = True
                    try:
                        targets[0]()
                    finally:
                        in_control_read = False
                    self.assertTrue(stop.is_set())
                    adapter.cancel.assert_called_once()
                    connection.shutdown.assert_called()
                    server.shutdown.assert_called()
                    return 1, {'text': 'untrusted late response'}
                adapter.call.side_effect = outbound
                with ExitStack() as mocks:
                    mocks.enter_context(patch.object(spending.socket, 'socket', return_value=server))
                    mocks.enter_context(patch.object(spending.threading, 'Thread', side_effect=prepare_thread))
                    mocks.enter_context(patch.object(spending, 'transport', return_value=adapter))
                    mocks.enter_context(patch.object(spending.sqlite3, 'connect', side_effect=controlled_connect))
                    mocks.enter_context(patch.object(controls, 'blocked', side_effect=controlled_read))
                    spending._serve_unix_socket(str(capability), str(state), scope, None, 1, '42', None,
                                               1, 10, 1, os.getuid(), spending.MAX_REQUEST, stop, None, None)
                adapter.call.assert_called_once_with({'input': 'test-input'})
                helper.join.assert_called_once()
                server.accept.assert_called_once()
                self.assertFalse(capability.exists())
                response = json.loads(connection.sendall.call_args.args[0])
                self.assertEqual(response['error'], 'outcome_uncertain')
                with connect(state) as database:
                    self.assertEqual(database.execute('SELECT status,actual_units FROM model_operations').fetchall(),
                                     [('pending', None)])
                    self.assertEqual(database.execute('SELECT remaining FROM model_grants').fetchone()[0], 1)
                    retained = json.loads(database.execute('SELECT payload FROM iterations').fetchone()[0])
                    self.assertEqual(retained['model_access']['reason'], 'outcome_uncertain')
                database.close()
