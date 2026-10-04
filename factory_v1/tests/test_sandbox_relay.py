"""Real local HTTP/UDS relay seam with labeled protocol fixtures, never live model evidence."""
import http.client
import json
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path

from factory_v1.sandbox_relay import ResponsesRelay


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='f1-relay-')
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        case = self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                request = json.loads(self.rfile.readline(2_000_001))
                case.calls.append(request)
                time.sleep(1.1)
                response = {'model': 'gpt-6.1-sol', 'status': 'completed', 'output': [
                    {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'FIXTURE_ONLY'}]}]}
                self.wfile.write(json.dumps({'result': {'result': response}}).encode() + b'\n')
        path = str(Path(self.temp.name) / 'model.sock')
        self.upstream = socketserver.UnixStreamServer(path, Handler)
        self.relay = ResponsesRelay(path, 'high')
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (self.upstream, self.relay)]
        for thread in self.threads:
            thread.start()
        self.addCleanup(self.close)

    def close(self):
        for server in (self.relay, self.upstream):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join()

    def post(self, payload, path='/v1/responses'):
        connection = http.client.HTTPConnection('127.0.0.1', self.relay.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.request('POST', path, json.dumps(payload), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        return response.status, response.read().decode()

    def test_large_request_streams_keepalive_and_exact_completed_response(self):
        status, content = self.post({'model': 'gpt-6.1-sol', 'stream': True, 'store': False, 'input': 'x' * 50000})
        self.assertEqual(status, 200)
        self.assertIn(': admitted-operation-pending', content)
        self.assertIn('response.output_item.done', content)
        self.assertIn('response.completed', content)
        self.assertIn('FIXTURE_ONLY', content)
        self.assertEqual(len(self.calls), 1)
        request = self.calls[0]
        self.assertEqual(request['reserve'], 1)
        self.assertEqual(set(request['payload']), {'input', 'reasoning'})
        self.assertEqual(request['payload']['reasoning'], {'effort': 'high'})

    def test_native_hermes_cache_key_is_local_only_metadata(self):
        status, content = self.post({'model': 'gpt-6.1-sol', 'stream': True, 'store': False,
            'input': 'x', 'reasoning': {'effort': 'high', 'summary': 'auto'},
            'include': ['reasoning.encrypted_content'], 'prompt_cache_key': 'pck_native_hermes'})
        self.assertEqual(status, 200)
        self.assertIn('response.completed', content)
        self.assertEqual(self.calls[0]['payload'], {'input': 'x', 'reasoning': {'effort': 'high'}})

    def test_invalid_cache_metadata_never_reaches_socket(self):
        for key in (None, 123, {}, '', 'x' * 129):
            status, _ = self.post({'model': 'gpt-6.1-sol', 'stream': True, 'input': 'x',
                                   'prompt_cache_key': key})
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
        connection = http.client.HTTPConnection('127.0.0.1', self.relay.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.putrequest('POST', '/v1/responses')
        connection.putheader('Content-Length', '2000001')
        connection.endheaders()
        response = connection.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
