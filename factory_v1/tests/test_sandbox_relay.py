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
        scheduled = False
        def wait(seconds):
            nonlocal scheduled
            if done.is_set():
                return True
            self.assertFalse(scheduled, 'Relay completion signal missing after one upstream invocation.')
            self.assertEqual(len(target), 1)
            scheduled = True
            target[0]()
            return False
        with patch.object(relay.threading, 'Thread', side_effect=lambda **kwargs: target.append(kwargs['target']) or Mock()), \
                patch.object(relay.threading, 'Event', return_value=done), \
                patch.object(done, 'wait', side_effect=wait), patch.object(relay, 'call', side_effect=outbound):
            handler.do_POST()
        status = handler.send_error.call_args.args[0] if handler.send_error.called else handler.send_response.call_args.args[0]
        return status, handler.wfile.getvalue().decode()

    def test_exact_response_and_local_cache_metadata_matrix(self):
        base = {'model': 'gpt-6.1-sol', 'stream': True, 'store': False}
        cases = [
            ('large', base | {'input': 'x' * 50000}),
            ('local-cache', base | {'input': 'x', 'reasoning': {'effort': 'high', 'summary': 'auto'},
             'include': ['reasoning.encrypted_content'], 'prompt_cache_key': 'pck_native_hermes'}),
        ]
        for label, payload in cases:
            with self.subTest(case=label):
                self.calls.clear()
                status, content = self.post(payload)
                self.assertEqual(status, 200)
                for event in (': admitted-operation-pending', 'response.output_item.done', 'response.completed', 'FIXTURE_ONLY'):
                    self.assertIn(event, content)
                self.assertTrue(content.endswith('data: [DONE]\n\n'))
                self.assertEqual(self.calls, [('/fixture/model.sock', {'input': payload['input'], 'reasoning': {'effort': 'high'}})])
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

    def test_provider_route_body_and_metadata_denials_matrix(self):
        base = {'model': 'gpt-6.1-sol', 'stream': True, 'input': 'x'}
        cases = [('cache-' + str(index), base | {'prompt_cache_key': key}, '/v1/responses', {})
                 for index, key in enumerate((None, 123, {}, '', 'x' * 129))]
        cases += [
            ('model', base | {'model': 'paid-model'}, '/v1/responses', {}),
            ('route', base, '/v1/chat/completions', {}),
            ('provider-field', base | {'url': 'https://github.com'}, '/v1/responses', {}),
        ]
        cases += [('body-' + str(index), {}, '/v1/responses', headers) for index, headers in enumerate(
            ({'Content-Length': '2000001'}, {'Content-Length': '0'}, {'Transfer-Encoding': 'chunked'}))]
        for label, payload, path, headers in cases:
            with self.subTest(case=label):
                self.assertEqual(self.post(payload, path, headers)[0], 400)
                self.assertEqual(self.calls, [])

    def test_socket_protocol_bounds_matrix(self):
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
