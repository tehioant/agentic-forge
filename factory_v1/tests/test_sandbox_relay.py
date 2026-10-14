"""Relay handlers/protocol components, with no HTTP server or real socket."""
import io
import json
import threading
import unittest
from email.message import Message
from unittest.mock import MagicMock, Mock, patch

from factory_v1 import sandbox_relay as relay


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.response = {'model': 'gpt-6.1-sol', 'status': 'completed', 'output': [
            {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'FIXTURE_ONLY'}]}]}

    def post(self, payload, path='/v1/responses', headers=None):
        handler = object.__new__(relay.Handler)
        body = json.dumps(payload).encode()
        handler.path = path
        handler.headers = Message()
        for name, value in {'Content-Length': str(len(body)), **(headers or {})}.items():
            handler.headers[name] = value
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler.server = object.__new__(relay.ResponsesRelay)
        handler.server.socket_path = '/fixture/model.sock'
        handler.server.reasoning = 'high'
        handler.send_error, handler.send_response = Mock(), Mock()
        handler.send_header, handler.end_headers = Mock(), Mock()
        target = []
        done = threading.Event()
        def outbound(socket_path, payload):
            self.calls.append((socket_path, payload))
            return self.response
        def wait(seconds):
            if not done.is_set():
                target[0]()
                return False
            return True
        with patch.object(relay.threading, 'Thread', side_effect=lambda **kwargs: target.append(kwargs['target']) or Mock()), \
                patch.object(relay.threading, 'Event', return_value=done), \
                patch.object(done, 'wait', side_effect=wait), patch.object(relay, 'call', side_effect=outbound):
            handler.do_POST()
        status = handler.send_error.call_args.args[0] if handler.send_error.called else handler.send_response.call_args.args[0]
        return status, handler.wfile.getvalue().decode()

    def test_large_request_streams_keepalive_and_exact_completed_response(self):
        status, content = self.post({'model': 'gpt-6.1-sol', 'stream': True, 'store': False, 'input': 'x' * 50000})
        self.assertEqual(status, 200)
        self.assertIn(': admitted-operation-pending', content)
        self.assertIn('response.output_item.done', content)
        self.assertIn('response.completed', content)
        self.assertIn('FIXTURE_ONLY', content)
        self.assertTrue(content.endswith('data: [DONE]\n\n'))
        self.assertEqual(self.calls, [('/fixture/model.sock', {'input': 'x' * 50000, 'reasoning': {'effort': 'high'}})])

    def test_native_hermes_cache_key_is_local_only_metadata(self):
        status, content = self.post({'model': 'gpt-6.1-sol', 'stream': True, 'store': False,
            'input': 'x', 'reasoning': {'effort': 'high', 'summary': 'auto'},
            'include': ['reasoning.encrypted_content'], 'prompt_cache_key': 'pck_native_hermes'})
        self.assertEqual(status, 200)
        self.assertIn('response.completed', content)
        self.assertEqual(self.calls[0][1], {'input': 'x', 'reasoning': {'effort': 'high'}})

    def test_invalid_cache_metadata_never_reaches_socket(self):
        for key in (None, 123, {}, '', 'x' * 129):
            status, _ = self.post({'model': 'gpt-6.1-sol', 'stream': True, 'input': 'x', 'prompt_cache_key': key})
            self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])

    def test_wrong_model_arbitrary_route_and_extra_provider_fields_never_reach_socket(self):
        for payload, path in [({'model': 'paid-model', 'stream': True, 'input': 'x'}, '/v1/responses'),
                              ({'model': 'gpt-6.1-sol', 'stream': True, 'input': 'x'}, '/v1/chat/completions'),
                              ({'model': 'gpt-6.1-sol', 'stream': True, 'input': 'x', 'url': 'https://github.com'}, '/v1/responses')]:
            status, _ = self.post(payload, path)
            self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])

    def test_oversized_body_is_denied_without_model_request(self):
        for headers in ({'Content-Length': '2000001'}, {'Content-Length': '0'}, {'Transfer-Encoding': 'chunked'}):
            self.assertEqual(self.post({}, headers=headers)[0], 400)
        self.assertEqual(self.calls, [])

    def test_socket_protocol_uses_fixed_reservation_and_exact_completed_response(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.return_value = json.dumps({'result': {'result': self.response}}).encode() + b'\n'
        with patch.object(relay.socket, 'socket', return_value=connection) as socket:
            self.assertEqual(relay.call('/fixture/model.sock', {'input': 'x'}), self.response)
        socket.assert_called_once_with(relay.socket.AF_UNIX, relay.socket.SOCK_STREAM)
        connection.connect.assert_called_once_with('/fixture/model.sock')
        request = json.loads(connection.sendall.call_args.args[0])
        self.assertEqual(request['reserve'], 1)
        self.assertEqual(request['payload'], {'input': 'x'})
        self.assertTrue(request['operation_id'].startswith('sandbox-'))

    def test_socket_response_and_request_bounds_fail_closed(self):
        with patch.object(relay.socket, 'socket') as socket:
            with self.assertRaises(ValueError):
                relay.call('/fixture/model.sock', {'input': 'x' * relay.MAX_REQUEST})
            socket.assert_not_called()
        for raw in (b'', b'{"result":{}}', b'x' * (relay.MAX_RESPONSE + 1), b'{"error":"outcome_uncertain"}\n'):
            connection = MagicMock()
            connection.__enter__.return_value = connection
            connection.recv.side_effect = [raw, b'']
            with self.subTest(raw_length=len(raw)), patch.object(relay.socket, 'socket', return_value=connection):
                with self.assertRaises(ValueError):
                    relay.call('/fixture/model.sock', {'input': 'x'})


if __name__ == '__main__':
    unittest.main()
