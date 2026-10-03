"""Host-owned model transports: labeled fixtures or approved subscription UDS only."""
import http.client
import ipaddress
import json
import math
import os
import socket
import stat
import urllib.parse
import urllib.request

MAX_RESPONSE = 2_000_000
SUBSCRIPTION_SCOPE = ('openai-codex', 'gpt-6.1-sol', 'responses')


class ModelAccessError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def bounded_json(response):
    data = response.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise ModelAccessError('outcome_uncertain')
    return json.loads(data)


class FixtureTransport:
    """A deterministic test seam, never a live billing adapter."""
    payload_fields = None
    def __init__(self, url, timeout, reservation):
        try:
            parsed = urllib.parse.urlsplit(url)
            host = ipaddress.ip_address(parsed.hostname) if parsed.hostname else None
            valid = (parsed.scheme == 'http' and host is not None and host.is_loopback
                     and not parsed.username and not parsed.password and not parsed.query
                     and not parsed.fragment and bool(parsed.path)
                     and (parsed.port is None or parsed.port > 0)
                     and not any(c.isspace() for c in url))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ModelAccessError('provider_unavailable')
        self.url = url
        self.timeout = timeout
        self.reservation = reservation
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, payload):
        request = urllib.request.Request(self.url, data=json.dumps(payload).encode(),
                                         method='POST', headers={'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=self.timeout) as response:
            result = bounded_json(response)
        if not isinstance(result, dict):
            raise ModelAccessError('outcome_uncertain')
        return result.get('actual_units'), result.get('output')


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__('host-subscription-capability', timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class SubscriptionTransport:
    """Call the trusted fixed-route host broker without provider credentials or TCP."""
    reservation = 1  # Approved subscription request allowance; never currency/tokens.
    payload_fields = {'input', 'instructions', 'tools', 'tool_choice', 'parallel_tool_calls', 'reasoning'}

    def __init__(self, path, scope, timeout):
        if tuple(scope[2:]) != SUBSCRIPTION_SCOPE:
            raise ModelAccessError('provider_unavailable')
        if not isinstance(path, str) or not os.path.isabs(path):
            raise ModelAccessError('provider_unavailable')
        info = os.lstat(path)
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ModelAccessError('provider_unavailable')
        self.path = path
        self.timeout = timeout

    def call(self, payload):
        if set(payload) - self.payload_fields or 'input' not in payload:
            raise ModelAccessError('invalid_request')
        body = {**payload, 'model': SUBSCRIPTION_SCOPE[1], 'store': False,
                'stream': True, 'service_tier': 'default'}
        connection = UnixHTTPConnection(self.path, self.timeout)
        try:
            connection.request('POST', '/v1/responses', json.dumps(body).encode(),
                               {'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status == 429:
                raise ModelAccessError('quota_exhausted')
            if response.status != 200:
                raise ModelAccessError('outcome_uncertain')
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ModelAccessError('outcome_uncertain')
            completed = None
            output_items = {}
            for event in raw.decode('utf-8').replace('\r\n', '\n').split('\n\n'):
                lines = [line[5:].lstrip() for line in event.splitlines() if line.startswith('data:')]
                if not lines or lines == ['[DONE]']:
                    continue
                item = json.loads('\n'.join(lines))
                if item.get('type') == 'response.output_item.done':
                    index, output_item = item.get('output_index'), item.get('item')
                    if type(index) is not int or not 0 <= index < 1000 or not isinstance(output_item, dict) or index in output_items:
                        raise ModelAccessError('outcome_uncertain')
                    output_items[index] = output_item
                if item.get('type') == 'response.completed':
                    completed = item.get('response')
                if item.get('type') in {'error', 'response.failed', 'response.incomplete'}:
                    raise ModelAccessError('outcome_uncertain')
            if (not isinstance(completed, dict) or completed.get('status') != 'completed'
                    or completed.get('model') != SUBSCRIPTION_SCOPE[1]):
                raise ModelAccessError('outcome_uncertain')
            if output_items:
                if sorted(output_items) != list(range(len(output_items))):
                    raise ModelAccessError('outcome_uncertain')
                completed['output'] = [output_items[index] for index in sorted(output_items)]
            elif not isinstance(completed.get('output'), list) or not completed['output']:
                raise ModelAccessError('outcome_uncertain')
            return 1, completed
        finally:
            connection.close()


def transport(scope, timeout, fixture_url=None, subscription_socket=None, reservation=3):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 180:
        raise ModelAccessError('provider_unavailable')
    if type(reservation) is not int or not 0 < reservation <= 2**63 - 1:
        raise ModelAccessError('invalid_reservation')
    if bool(fixture_url) == bool(subscription_socket):
        raise ModelAccessError('provider_unavailable')
    if fixture_url:
        if scope[2] != 'fixture-provider':
            raise ModelAccessError('provider_unavailable')
        return FixtureTransport(fixture_url, timeout, reservation)
    return SubscriptionTransport(subscription_socket, scope, timeout)
