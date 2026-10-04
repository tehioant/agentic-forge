"""Container-local OpenAI Responses relay; only one fixed admitted UDS route exists."""
import json
import os
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

MAX_REQUEST = 2_000_000
MAX_RESPONSE = 2_000_000
MODEL = 'gpt-6.1-sol'


def call(socket_path, payload):
    request = json.dumps({'operation_id': 'sandbox-' + uuid.uuid4().hex, 'reserve': 1,
                          'payload': payload}, separators=(',', ':'), allow_nan=False).encode() + b'\n'
    if len(request) > MAX_REQUEST:
        raise ValueError('invalid_request')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(190)
        connection.connect(socket_path)
        connection.sendall(request)
        reply = bytearray()
        while not reply.endswith(b'\n') and len(reply) <= MAX_RESPONSE:
            part = connection.recv(4096)
            if not part:
                break
            reply.extend(part)
    if len(reply) > MAX_RESPONSE or not reply.endswith(b'\n'):
        raise ValueError('broker_unavailable')
    result = json.loads(reply)
    if 'error' in result:
        raise ValueError(result['error'])
    return result['result']['result']


class ResponsesRelay(HTTPServer):
    def __init__(self, socket_path, reasoning):
        self.socket_path = socket_path
        self.reasoning = reasoning
        super().__init__(('127.0.0.1', 0), Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_error(404)

    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if self.path != '/v1/responses' or not 0 < size <= MAX_REQUEST or self.headers.get('Transfer-Encoding'):
                raise ValueError('invalid_route')
            request = json.loads(self.rfile.read(size))
            allowed = {'model', 'stream', 'store', 'input', 'instructions', 'tools', 'tool_choice',
                       'parallel_tool_calls', 'reasoning', 'include', 'max_output_tokens', 'service_tier'}
            if (not isinstance(request, dict) or set(request) - allowed or request.get('model') != MODEL or
                    request.get('stream') is not True or request.get('store', False) is not False or
                    request.get('service_tier', 'default') != 'default' or 'input' not in request):
                raise ValueError('invalid_request')
            fields = {'input', 'instructions', 'tools', 'tool_choice', 'parallel_tool_calls'}
            payload = {key: value for key, value in request.items() if key in fields}
            payload['reasoning'] = {'effort': self.server.reasoning}
        except (ValueError, TypeError, RecursionError):
            self.send_error(400, 'Only the pinned Responses route is admitted')
            return
        # Keep the SDK stream alive while the host's admitted, buffered operation computes.
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        done, answer = threading.Event(), {}

        def upstream():
            try:
                answer['response'] = call(self.server.socket_path, payload)
            except (OSError, ValueError, KeyError, TypeError) as error:
                answer['error'] = str(error)
            finally:
                done.set()

        threading.Thread(target=upstream, daemon=True).start()
        try:
            while not done.wait(1):
                self.wfile.write(b': admitted-operation-pending\n\n')
                self.wfile.flush()
            if 'error' in answer:
                self.event({'type': 'error', 'error': {'code': 'admission_blocked', 'message': answer['error']}})
                return
            response = answer['response']
            for index, item in enumerate(response['output']):
                self.event({'type': 'response.output_item.done', 'output_index': index, 'item': item})
            self.event({'type': 'response.completed', 'response': response})
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Client disappearance never triggers a second upstream operation.

    def event(self, value):
        self.wfile.write(b'data: ' + json.dumps(value).encode() + b'\n\n')
        self.wfile.flush()
