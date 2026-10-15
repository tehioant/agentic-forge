"""Broker cancellation components: admitted SQLite state, captured scheduling, mock I/O."""
import json
import os
import sqlite3
import struct
import tempfile
import threading
import unittest
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from factory_v1 import spending
from factory_v1.model_access import SUBSCRIPTION_SCOPE


def broker_component(state, scope, adapter, *, payload=None, reserve=1, stop=None,
                     authorize=None, observe=None, run=None, peer_uid=None, max_calls=1):
    """Drive the real broker loop without threads, binds, socket clients or upstream I/O."""
    stop = stop if stop is not None else threading.Event()
    path = Path(state).parent / 'capability.sock'
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.getsockopt.return_value = struct.pack('3i', 123, os.getuid() if peer_uid is None else peer_uid, 123)
    connection.recv.return_value = json.dumps({'operation_id': 'cancelled-operation', 'reserve': reserve,
                                              'payload': payload or {'input': 'labeled fixture'}}).encode() + b'\n'
    connection.sendall.side_effect = lambda data: stop.set()
    server = MagicMock()
    server.__enter__.return_value = server
    server.bind.side_effect = lambda name: Path(name).touch()
    server.accept.return_value = (connection, None)
    helper = Mock(ident=1)
    targets = []
    clock = [100.0]
    def thread(*, target, name, daemon):
        if name != 'model-broker-canceller' or daemon:
            raise AssertionError('cancellation helper must be owned and non-daemon')
        targets.append(target)
        return helper
    component = SimpleNamespace(stop=stop, path=path, connection=connection, server=server,
                                helper=helper, targets=targets, clock=clock)
    with ExitStack() as mocks:
        mocks.enter_context(patch.object(spending.socket, 'socket', return_value=server))
        mocks.enter_context(patch.object(spending.threading, 'Thread', side_effect=thread))
        mocks.enter_context(patch.object(spending, 'transport', return_value=adapter))
        mocks.enter_context(patch.object(spending.time, 'monotonic', side_effect=lambda: clock[0]))
        # A missing cancellation guard is bounded to one iteration, never the real TTL.
        mocks.enter_context(patch.object(threading.Event, 'wait', autospec=True,
                                        side_effect=lambda event, timeout: event.set() or True))
        if run:
            run(component, mocks)
        spending._serve_unix_socket(str(path), str(state), scope, None, 180, '42', None,
                                   reserve, 10, max_calls, os.getuid(), spending.MAX_REQUEST,
                                   stop, authorize, observe)
    return component


class SpendingCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cancel-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state.sqlite'
        self.scope = ('product', 'm1', *SUBSCRIPTION_SCOPE)
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT)')
            item = {'project_id': 'product', 'iteration_id': 'm1', 'status': 'active',
                    'execution_allowed': False, 'approval': {'operator_id': '42', 'reference': 'local-test'}}
            db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(item)))
            spending.initialize(db)
            spending.add_grant(db, *self.scope, 'allowance', 2, 2**40, 'labeled-local-test')

    def assert_cleanup(self, component):
        component.helper.start.assert_called_once()
        component.helper.join.assert_called_once_with()
        self.assertFalse(component.path.exists())

    def assert_no_admission(self, remaining=2):
        with closing(sqlite3.connect(self.state)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM model_operations').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT remaining FROM model_grants').fetchone()[0], remaining)

    def cancellation(self, *, deadline=False, fixture=False):
        if fixture:
            self.scope = ('product', 'm1', 'fixture-provider', 'fixture-model', 'generate')
            with closing(sqlite3.connect(self.state)) as db, db:
                spending.add_grant(db, *self.scope, 'allowance', 5, 2**40, 'labeled-fixture')
        reserve = 3 if fixture else 1
        payload = {'prompt': 'labeled fixture'} if fixture else {'input': 'labeled fixture'}
        adapter = Mock(reservation=reserve, payload_fields=None if fixture else {'input'})
        def configure(component, mocks):
            def outbound(value):
                self.assertEqual(value, payload)
                component.helper.start.assert_called_once()
                with closing(sqlite3.connect(self.state)) as db:
                    self.assertEqual(db.execute('SELECT status,actual_units FROM model_operations').fetchall(), [('pending', None)])
                if deadline:
                    component.clock[0] = 111
                else:
                    component.stop.set()
                component.targets[0]()
                self.assertTrue(component.stop.is_set())
                adapter.cancel.assert_called_once()
                # Even a late successful transport result is not a billing receipt.
                return reserve, {'untrusted': 'late response'}
            adapter.call.side_effect = outbound
        component = broker_component(self.state, self.scope, adapter, payload=payload, reserve=reserve, run=configure)
        self.assert_cleanup(component)
        adapter.call.assert_called_once_with(payload)
        adapter.cancel.assert_called_once_with()
        component.connection.shutdown.assert_called_once_with(spending.socket.SHUT_RD)
        component.server.shutdown.assert_called_once_with(spending.socket.SHUT_RDWR)
        self.assertEqual(json.loads(component.connection.sendall.call_args.args[0])['error'], 'outcome_uncertain')
        with closing(sqlite3.connect(self.state)) as db:
            self.assertEqual(db.execute('SELECT status,actual_units FROM model_operations').fetchall(), [('pending', None)])
            self.assertEqual(db.execute('SELECT remaining FROM model_grants WHERE provider=?', (self.scope[2],)).fetchone()[0], 2 if fixture else 1)
            item = json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])
            self.assertEqual(item['model_access']['reason'], 'outcome_uncertain')
            for key in ('cancelled-operation', 'new-operation'):
                with self.assertRaises(spending.SpendingError) as refused:
                    spending.execute_controlled(db, self.scope, key, payload, reserve, adapter=adapter)
                self.assertEqual(refused.exception.code, 'outcome_uncertain')
        adapter.call.assert_called_once_with(payload)

    def test_broker_shutdown_without_admission(self):
        for boundary in ('accept', 'partial-request', 'observer'):
            with self.subTest(boundary=boundary):
                adapter = Mock(reservation=1, payload_fields={'input'})
                retained = []
                def observer(*args):
                    raise RuntimeError('labeled observer failure')
                def configure(component, mocks):
                    retained.append(component)
                    if boundary == 'accept':
                        def accept():
                            component.stop.set(); component.targets[0]()
                            raise OSError('labeled cancelled accept')
                        component.server.accept.side_effect = accept
                    elif boundary == 'partial-request':
                        component.connection.recv.side_effect = [b'{"operation_id":', b'']
                        def peer(*args):
                            component.stop.set(); component.targets[0]()
                            return struct.pack('3i', 123, os.getuid(), 123)
                        component.connection.getsockopt.side_effect = peer
                if boundary == 'observer':
                    with self.assertRaisesRegex(RuntimeError, 'labeled observer failure'):
                        broker_component(self.state, self.scope, adapter, observe=observer, run=configure)
                else:
                    broker_component(self.state, self.scope, adapter, run=configure)
                component, = retained
                self.assert_cleanup(component)
                adapter.call.assert_not_called()
                self.assert_no_admission()
                if boundary == 'accept':
                    adapter.cancel.assert_called_once()
                if boundary == 'partial-request':
                    self.assertEqual(json.loads(component.connection.sendall.call_args.args[0])['error'], 'invalid_request')



    def test_active_transport_cancellation(self):
        seed = self.state.read_bytes()
        for label, deadline, fixture in (('subscription-stop', False, False), ('subscription-deadline', True, False), ('fixture-stop', False, True)):
            with self.subTest(label=label):
                self.state.write_bytes(seed)
                self.scope = ('product', 'm1', *SUBSCRIPTION_SCOPE)
                self.cancellation(deadline=deadline, fixture=fixture)




    def test_admission_rechecks_stop_and_deadline(self):
        lock = spending.state_lock
        for boundary, deadline in (('authorization', False), ('lock', False), ('lock', True)):
            with self.subTest(boundary=boundary, deadline=deadline):
                adapter = Mock(reservation=1, payload_fields={'input'})
                stop = threading.Event()
                def configure(component, mocks):
                    @contextmanager
                    def after_wait(database):
                        with lock(database):
                            if deadline:
                                component.clock[0] = 111
                            else:
                                stop.set()
                            yield
                    if boundary == 'lock':
                        mocks.enter_context(patch.object(spending, 'state_lock', side_effect=after_wait))
                component = broker_component(self.state, self.scope, adapter, stop=stop, run=configure,
                    authorize=(lambda db, request: stop.set()) if boundary == 'authorization' else None)
                self.assert_cleanup(component)
                self.assertEqual(json.loads(component.connection.sendall.call_args.args[0])['error'], 'capability_exhausted')
                adapter.call.assert_not_called()
                self.assert_no_admission()


    def after_lock(self, deadline=False):
        adapter = Mock(reservation=1, payload_fields={'input'})
        lock = spending.state_lock
        def configure(component, mocks):
            @contextmanager
            def after_wait(database):
                # The real lock is released before any cleanup/join, also on assertion failure.
                with lock(database):
                    if deadline:
                        component.clock[0] = 111
                    else:
                        component.stop.set()
                    yield
            mocks.enter_context(patch.object(spending, 'state_lock', side_effect=after_wait))
        component = broker_component(self.state, self.scope, adapter, run=configure)
        self.assert_cleanup(component)
        self.assertEqual(json.loads(component.connection.sendall.call_args.args[0])['error'], 'capability_exhausted')
        adapter.call.assert_not_called()
        self.assert_no_admission()





if __name__ == '__main__':
    unittest.main()
