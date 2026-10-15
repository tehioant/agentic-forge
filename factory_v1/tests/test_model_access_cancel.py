"""Real HTTP parsing/cancellation with in-memory socket streams, never native I/O."""
import io
import json
import os
import socket
import stat
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from factory_v1 import model_access
from factory_v1.model_access import SUBSCRIPTION_SCOPE, FixtureTransport, ModelAccessError, SubscriptionTransport


STALLS = {
    'response': b'',
    'headers': b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n',
    'body_length': b'HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\ndata: ',
    'body_close': b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\ndata: ',
    'body_chunked': b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n100\r\ndata: ',
}


def subscription_adapter(path='/fixture/upstream.sock'):
    with patch.object(model_access.os, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=os.getuid())):
        return SubscriptionTransport(path, ('product', 'm1', *SUBSCRIPTION_SCOPE), 180)


class ModelAccessCancellationTests(unittest.TestCase):
    def test_subscription_socket_cancellation(self):
        for name, partial in [('before-connect', b''), *STALLS.items()]:
            with self.subTest(stall=name):
                adapter = subscription_adapter()
                cancelled = []
                class StalledStream(io.BytesIO):
                    def interrupt(stream):
                        cancelled.append(True)
                        adapter.cancel()
                        adapter.cancel()
                        raise OSError('labeled interrupted read')
                    def readline(stream, size: int | None = -1):
                        if stream.tell() == len(partial):
                            stream.interrupt()
                        return super().readline(size)
                    def read(stream, size: int | None = -1):
                        size = -1 if size is None else size
                        if size < 0 or size > len(partial) - stream.tell():
                            stream.interrupt()
                        return super().read(size)
                sock = MagicMock()
                sock.makefile.return_value = StalledStream(partial)
                with patch.object(model_access.socket, 'socket', return_value=sock) as socket_factory:
                    with self.assertRaises(OSError):
                        adapter.call({'input': 'labeled cancellation probe'})
                    self.assertEqual(cancelled, [True])
                    self.assertTrue(adapter.cancellation.cancelled.is_set())
                    sock.shutdown.assert_any_call(socket.SHUT_RDWR)
                    self.assertEqual(adapter.cancellation.sockets, {})
                    sock.close.assert_called()
                    sock.connect.assert_called_once_with('/fixture/upstream.sock')
                    raw = b''.join(call.args[0] for call in sock.sendall.call_args_list)
                    headers, body = raw.split(b'\r\n\r\n', 1)
                    self.assertTrue(headers.startswith(b'POST /v1/responses HTTP/1.1'))
                    payload = json.loads(body)
                    self.assertEqual(payload['model'], 'gpt-6.1-sol')
                    self.assertFalse(payload['store'])
                    self.assertTrue(payload['stream'])
                    self.assertEqual(payload['service_tier'], 'default')
                    with self.assertRaises(OSError):
                        adapter.call({'input': 'must not retry'})
                    self.assertEqual(sock.connect.call_count, 1)
                    self.assertEqual(socket_factory.call_count, 2)


    def test_cancellable_fixture_does_not_follow_redirects(self):
        adapter = FixtureTransport('http://127.0.0.1:1/fixed-fixture', 2, 3)
        sock = MagicMock()
        sock.makefile.return_value = io.BytesIO(b'HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:2/never-follow\r\nContent-Length: 0\r\n\r\n')
        with patch.object(model_access.socket, 'socket', return_value=sock) as socket_factory:
            with self.assertRaises(ModelAccessError) as error:
                adapter.call({'prompt': 'labeled redirect fixture'})
        self.assertEqual(error.exception.code, 'outcome_uncertain')
        socket_factory.assert_called_once()
        sock.connect.assert_called_once_with(('127.0.0.1', 1))
        self.assertEqual(adapter.cancellation.sockets, {})

    def test_subscription_wire_contract_2(self):
        item = {'type': 'message', 'content': [{'type': 'output_text', 'text': 'labeled-fixture'}]}
        events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                  {'type': 'response.completed', 'response': {'status': 'completed', 'model': 'gpt-6.1-sol', 'output': []}}]
        body = ''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode()
        for status, code in ((200, None), (429, 'quota_exhausted'), (500, 'outcome_uncertain')):
            with self.subTest(status=status):
                adapter = subscription_adapter()
                raw = (f'HTTP/1.1 {status} Fixture\r\nContent-Type: text/event-stream\r\nContent-Length: '.encode()
                       + str(len(body) if status == 200 else 0).encode() + b'\r\n\r\n' + (body if status == 200 else b''))
                sock = MagicMock()
                sock.makefile.return_value = io.BytesIO(raw)
                with patch.object(model_access.socket, 'socket', return_value=sock):
                    if code:
                        with self.assertRaises(ModelAccessError) as error:
                            adapter.call({'input': 'labeled fixture request'})
                        self.assertEqual(error.exception.code, code)
                    else:
                        units, response = adapter.call({'input': 'labeled fixture request'})
                        self.assertEqual(units, 1)
                        self.assertEqual(response, {'status': 'completed', 'model': 'gpt-6.1-sol', 'output': [item]})
                outbound = b''.join(call.args[0] for call in sock.sendall.call_args_list)
                headers, encoded = outbound.split(b'\r\n\r\n', 1)
                self.assertTrue(headers.startswith(b'POST /v1/responses HTTP/1.1'))
                self.assertEqual(json.loads(encoded), {'input': 'labeled fixture request', 'model': 'gpt-6.1-sol',
                                                       'store': False, 'stream': True, 'service_tier': 'default'})
                self.assertEqual(adapter.cancellation.sockets, {})
                sock.connect.assert_called_once()
                sock.close.assert_called()


    def test_cancellation_registration_and_release_are_idempotent(self):
        cancellation = model_access.SocketCancellation()
        first, removed = MagicMock(), MagicMock()
        cancellation.add(first, socket.SHUT_RD)
        cancellation.add(removed)
        cancellation.remove(removed)
        first.shutdown.side_effect = OSError('already closed')
        cancellation.cancel()
        cancellation.cancel()
        self.assertEqual(first.shutdown.call_args_list, [call(socket.SHUT_RD)] * 2)
        removed.shutdown.assert_not_called()
        with self.assertRaises(OSError):
            cancellation.add(MagicMock())
        cancellation.remove(first)
        cancellation.remove(first)
        self.assertEqual(cancellation.sockets, {})


if __name__ == '__main__':
    unittest.main()
