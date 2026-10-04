"""Cancellation probes use deliberately incomplete local HTTP fixtures, never inference."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from factory_v1.model_access import SUBSCRIPTION_SCOPE, FixtureTransport, ModelAccessError, SubscriptionTransport


class StalledHTTPFixture:
    def __init__(self, path=None, partial=b''):
        self.entered = threading.Event()
        self.disconnected = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.errors = []
        self.partial = partial
        family = socket.AF_UNIX if path else socket.AF_INET
        self.server = socket.socket(family, socket.SOCK_STREAM)
        self.server.bind(str(path) if path else ('127.0.0.1', 0))
        self.server.listen(1)
        self.server.settimeout(.1)
        self.address = self.server.getsockname()
        self.thread = threading.Thread(target=self.serve, name='stalled-local-http-fixture', daemon=False)
        self.thread.start()

    def serve(self):
        try:
            while not self.release.is_set():
                try:
                    peer, _ = self.server.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            with peer:
                peer.settimeout(.1)
                data = bytearray()
                while not self.release.is_set():
                    try:
                        part = peer.recv(4096)
                    except socket.timeout:
                        continue
                    if not part:
                        return
                    data.extend(part)
                    if b'\r\n\r\n' not in data:
                        continue
                    headers, body = bytes(data).split(b'\r\n\r\n', 1)
                    length = next(int(line.split(b':', 1)[1]) for line in headers.split(b'\r\n')
                                  if line.lower().startswith(b'content-length:'))
                    if len(body) >= length:
                        self.calls.append((headers.split(b'\r\n')[0], json.loads(body[:length])))
                        break
                if self.release.is_set():
                    return
                if self.partial:
                    peer.sendall(self.partial)
                self.entered.set()
                while not self.release.is_set():
                    try:
                        if not peer.recv(4096):
                            self.disconnected.set()
                            return
                    except socket.timeout:
                        continue
        except OSError as error:
            if not self.release.is_set():
                self.errors.append(error)

    def close(self):
        self.release.set()
        self.thread.join(2)
        self.server.close()
        if self.thread.is_alive():
            raise AssertionError('Local fixture thread leaked')
        if self.errors:
            raise AssertionError(self.errors)


STALLS = {
    'response': b'',
    'headers': b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n',
    'body_length': b'HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\ndata: ',
    'body_close': b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\ndata: ',
    'body_chunked': b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n100\r\ndata: ',
}


class ModelAccessCancellationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cancel-', dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'upstream.sock'

    def test_cancel_interrupts_real_subscription_response_headers_and_bodies(self):
        for name, partial in STALLS.items():
            with self.subTest(stall=name):
                fixture = StalledHTTPFixture(self.path, partial)
                adapter = SubscriptionTransport(str(self.path), ('product', 'm1', *SUBSCRIPTION_SCOPE), 180)
                errors = []
                def call():
                    try:
                        adapter.call({'input': 'labeled cancellation probe'})
                    except Exception as error:
                        errors.append(error)
                caller = threading.Thread(target=call, daemon=False)
                caller.start()
                try:
                    self.assertTrue(fixture.entered.wait(2))
                    started = time.monotonic()
                    adapter.cancel()
                    adapter.cancel()
                    caller.join(1)
                    self.assertFalse(caller.is_alive(), name)
                    self.assertLess(time.monotonic() - started, 1)
                    self.assertTrue(fixture.disconnected.wait(1))
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(len(fixture.calls), 1)
                    route, body = fixture.calls[0]
                    self.assertEqual(route, b'POST /v1/responses HTTP/1.1')
                    self.assertEqual(body['model'], 'gpt-6.1-sol')
                    self.assertFalse(body['store'])
                    self.assertTrue(body['stream'])
                    self.assertEqual(body['service_tier'], 'default')
                    with self.assertRaises((OSError, ModelAccessError)):
                        adapter.call({'input': 'must not retry'})
                    self.assertEqual(len(fixture.calls), 1)
                finally:
                    fixture.close()
                    caller.join(2)
                    self.path.unlink(missing_ok=True)

    def test_cancel_before_call_refuses_outbound_connection(self):
        fixture = StalledHTTPFixture(self.path)
        self.addCleanup(fixture.close)
        adapter = SubscriptionTransport(str(self.path), ('product', 'm1', *SUBSCRIPTION_SCOPE), 180)
        adapter.cancel()
        with self.assertRaises((OSError, ModelAccessError)):
            adapter.call({'input': 'must not connect'})
        self.assertFalse(fixture.entered.is_set())
        self.assertEqual(fixture.calls, [])

    def test_cancellable_fixture_does_not_follow_redirects(self):
        fixture = StalledHTTPFixture(partial=b'HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/never-follow\r\nContent-Length: 0\r\n\r\n')
        self.addCleanup(fixture.close)
        adapter = FixtureTransport(f'http://127.0.0.1:{fixture.address[1]}/fixed-fixture', 2, 3)
        with self.assertRaises(ModelAccessError) as error:
            adapter.call({'prompt': 'labeled redirect fixture'})
        self.assertEqual(error.exception.code, 'outcome_uncertain')
        self.assertTrue(fixture.disconnected.wait(1))
        self.assertEqual(len(fixture.calls), 1)


if __name__ == '__main__':
    unittest.main()
