"""Exercise the real native broker against stalled, local-only socket fixtures."""
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from factory_v1 import spending
from factory_v1.model_access import SUBSCRIPTION_SCOPE, SubscriptionTransport
from factory_v1.tests.test_model_access_cancel import STALLS, StalledHTTPFixture


class SpendingCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cancel-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state.sqlite'
        self.path = self.root / 'capability.sock'
        self.upstream = self.root / 'upstream.sock'
        self.scope = ('product', 'm1', *SUBSCRIPTION_SCOPE)
        self.stop = threading.Event()
        self.errors = []
        self.broker = None
        self.addCleanup(self.cleanup_broker)
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT)')
            item = {'project_id': 'product', 'iteration_id': 'm1', 'status': 'active',
                    'execution_allowed': False, 'approval': {'operator_id': '42', 'reference': 'local-test'}}
            db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(item)))
            spending.initialize(db)
            spending.add_grant(db, *self.scope, 'allowance', 2, int(time.time()) + 600, 'labeled-local-test')

    def cleanup_broker(self):
        self.stop.set()
        if self.broker:
            self.broker.join(3)
            self.assertFalse(self.broker.is_alive(), 'Native broker thread leaked')
        self.assertEqual(self.errors, [])

    def start_broker(self, fixture, **kwargs):
        options: dict = {'subscription_socket': str(self.upstream), 'reservation': 1}
        if isinstance(fixture.address, tuple):
            self.scope = ('product', 'm1', 'fixture-provider', 'fixture-model', 'generate')
            with closing(sqlite3.connect(self.state)) as db, db:
                spending.add_grant(db, *self.scope, 'allowance', 5, int(time.time()) + 600, 'labeled-http-fixture')
            options = {'fixture_url': f'http://127.0.0.1:{fixture.address[1]}/fixed-fixture'}
        options.update(timeout=180, ttl=20, operator_id='42', stop_event=self.stop)
        options.update(kwargs)
        def serve():
            try:
                spending.serve_unix_socket(str(self.path), str(self.state), self.scope,
                                          **options)
            except Exception as error:
                self.errors.append(error)
        self.broker = threading.Thread(target=serve, name='test-native-broker', daemon=False)
        self.broker.start()
        deadline = time.monotonic() + 2
        while not self.path.exists() and time.monotonic() < deadline and self.broker.is_alive():
            time.sleep(.005)
        self.assertTrue(self.path.exists(), self.errors)

    def client(self, partial=False, operation_id='cancelled-operation'):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(client.close)
        client.settimeout(2)
        client.connect(str(self.path))
        data = b'{"operation_id":' if partial else json.dumps({
            'operation_id': operation_id, 'reserve': 1 if self.scope[2] == 'openai-codex' else 3,
            'payload': {'input': 'labeled local cancellation fixture'} if self.scope[2] == 'openai-codex' else {'prompt': 'labeled fixture'},
        }).encode() + b'\n'
        client.sendall(data)
        return client

    def assert_stopped(self):
        assert self.broker is not None
        helpers = [thread for thread in threading.enumerate() if thread.name == 'model-broker-canceller']
        self.assertEqual(len(helpers), 1)
        self.assertFalse(helpers[0].daemon)
        started = time.monotonic()
        self.stop.set()
        self.broker.join(1)
        self.assertFalse(self.broker.is_alive(), self.errors)
        self.assertLess(time.monotonic() - started, 1)
        self.assertFalse(helpers[0].is_alive(), 'Owned cancellation helper was not joined')
        self.assertFalse(self.path.exists())
        self.assertEqual(self.errors, [])
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as late:
            with self.assertRaises(OSError):
                late.connect(str(self.path))

    def assert_no_admission(self):
        with closing(sqlite3.connect(self.state)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM model_operations').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT remaining FROM model_grants').fetchone()[0], 2)

    def assert_uncertain(self, fixture, reserve=1, remaining=1):
        self.assertTrue(fixture.disconnected.wait(1), 'Actual upstream socket was not interrupted')
        self.assertEqual(len(fixture.calls), 1)
        with closing(sqlite3.connect(self.state)) as db:
            self.assertEqual(db.execute('SELECT status,actual_units FROM model_operations').fetchall(), [('pending', None)])
            self.assertEqual(db.execute('SELECT remaining FROM model_grants WHERE provider=?', (self.scope[2],)).fetchone()[0], remaining)
            item = json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])
            self.assertEqual(item['model_access']['reason'], 'outcome_uncertain')
            if reserve == 1:
                adapter = SubscriptionTransport(str(self.upstream), self.scope, 180)
                for key in ('cancelled-operation', 'new-operation'):
                    with self.assertRaises(spending.SpendingError) as error:
                        spending.execute_controlled(db, self.scope, key, {'input': 'labeled local cancellation fixture'}, 1, adapter=adapter)
                    self.assertEqual(error.exception.code, 'outcome_uncertain')
        self.assertEqual(len(fixture.calls), 1, 'Retry or fallback reached upstream')

    def test_stop_interrupts_idle_accept_and_unlinks_capability(self):
        fixture = StalledHTTPFixture(self.upstream)
        self.addCleanup(fixture.close)
        self.start_broker(fixture)
        self.assert_stopped()
        self.assert_no_admission()
        self.assertEqual(fixture.calls, [])

    def test_stop_interrupts_partial_client_request_without_admission(self):
        fixture = StalledHTTPFixture(self.upstream)
        self.addCleanup(fixture.close)
        peer_seen = threading.Event()
        unpack = spending.struct.unpack
        def observe_peer(*args):
            result = unpack(*args)
            peer_seen.set()
            return result
        with patch.object(spending.struct, 'unpack', observe_peer):
            self.start_broker(fixture)
            self.client(partial=True)
            self.assertTrue(peer_seen.wait(2))
            self.assert_stopped()
        self.assert_no_admission()
        self.assertEqual(fixture.calls, [])

    def assert_stalled_subscription_stops(self, partial):
        fixture = StalledHTTPFixture(self.upstream, partial)
        self.addCleanup(fixture.close)
        self.start_broker(fixture)
        self.client()
        self.assertTrue(fixture.entered.wait(2))
        self.client(operation_id='queued-new-operation')
        self.assert_stopped()
        self.assert_uncertain(fixture)

    def test_stop_interrupts_subscription_response_read(self):
        self.assert_stalled_subscription_stops(STALLS['response'])

    def test_stop_interrupts_subscription_header_read(self):
        self.assert_stalled_subscription_stops(STALLS['headers'])

    def test_stop_interrupts_subscription_content_length_body(self):
        self.assert_stalled_subscription_stops(STALLS['body_length'])

    def test_stop_interrupts_subscription_connection_close_body(self):
        self.assert_stalled_subscription_stops(STALLS['body_close'])

    def test_stop_interrupts_subscription_chunked_body(self):
        self.assert_stalled_subscription_stops(STALLS['body_chunked'])

    def test_stop_interrupts_labeled_fixture_transport_without_fabricated_completion(self):
        fixture = StalledHTTPFixture(partial=STALLS['body_close'])
        self.addCleanup(fixture.close)
        self.start_broker(fixture)
        self.client()
        self.assertTrue(fixture.entered.wait(2))
        self.assert_stopped()
        self.assert_uncertain(fixture, reserve=3, remaining=2)

    def test_stop_during_authorization_refuses_reservation_and_outbound_io(self):
        fixture = StalledHTTPFixture(self.upstream)
        self.addCleanup(fixture.close)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def authorize(db, request):
            entered.set()
            self.assertTrue(release.wait(2))
        self.start_broker(fixture, authorize=authorize)
        self.client()
        self.assertTrue(entered.wait(2))
        self.stop.set()
        release.set()
        assert self.broker is not None
        self.broker.join(1)
        self.assertFalse(self.broker.is_alive(), self.errors)
        self.assertFalse(self.path.exists())
        self.assert_no_admission()
        self.assertEqual(fixture.calls, [])

    def test_deadline_interrupts_active_subscription_body_and_joins_helper(self):
        fixture = StalledHTTPFixture(self.upstream, STALLS['body_close'])
        self.addCleanup(fixture.close)
        self.start_broker(fixture, ttl=1)
        self.client()
        self.assertTrue(fixture.entered.wait(2))
        helpers = [thread for thread in threading.enumerate() if thread.name == 'model-broker-canceller']
        self.assertEqual(len(helpers), 1)
        assert self.broker is not None
        self.broker.join(2)
        self.assertFalse(self.broker.is_alive(), self.errors)
        self.assertFalse(helpers[0].is_alive())
        self.assertFalse(self.path.exists())
        self.assert_uncertain(fixture)

    def test_stop_is_rechecked_after_controller_lock_wait(self):
        import fcntl
        fixture = StalledHTTPFixture(self.upstream)
        self.addCleanup(fixture.close)
        requested = threading.Event()
        self.start_broker(fixture, observe=lambda *args: requested.set())
        with open(str(self.state) + '.onboarding.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.client()
            self.assertTrue(requested.wait(2))
            self.stop.set()
            fcntl.flock(lock, fcntl.LOCK_UN)
        assert self.broker is not None
        self.broker.join(1)
        self.assertFalse(self.broker.is_alive(), self.errors)
        self.assertFalse(self.path.exists())
        self.assert_no_admission()
        self.assertEqual(fixture.calls, [])


    def test_request_observer_failure_joins_helper_and_unlinks_capability(self):
        fixture = StalledHTTPFixture(self.upstream)
        self.addCleanup(fixture.close)
        helpers = []
        def fail_observer(*args):
            helpers.extend(thread for thread in threading.enumerate() if thread.name == 'model-broker-canceller')
            raise RuntimeError('labeled local observer failure')
        self.start_broker(fixture, observe=fail_observer)
        self.client()
        assert self.broker is not None
        self.broker.join(1)
        self.assertFalse(self.broker.is_alive())
        self.assertEqual(len(helpers), 1)
        self.assertFalse(helpers[0].is_alive())
        self.assertFalse(self.path.exists())
        self.assertEqual(len(self.errors), 1)
        self.assertIsInstance(self.errors.pop(), RuntimeError)
        self.assert_no_admission()
        self.assertEqual(fixture.calls, [])


if __name__ == '__main__':
    unittest.main()
