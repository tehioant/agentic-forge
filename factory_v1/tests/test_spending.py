import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class FixtureHandler(BaseHTTPRequestHandler):
    calls = []
    uncertain = False

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        self.calls.append(body)
        if self.uncertain:
            self.close_connection = True
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"actual_units":2,"output":"fixture-answer"}')

    def log_message(self, format, *args):
        pass


class ModelSpendingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state.sqlite'
        self.sock = self.root / 'model.sock'
        self.expires = int(time.time()) + 600
        FixtureHandler.calls = []
        FixtureHandler.uncertain = False
        self.http = HTTPServer(('127.0.0.1', 0), FixtureHandler)
        self.fixture_url = f'http://127.0.0.1:{self.http.server_port}/fixed-fixture'
        self.http_thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.http_thread.start()
        self.addCleanup(self.http.shutdown)
        self.addCleanup(self.http.server_close)
        request = {
            'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
            'idea': 'approved test product', 'approval': {'operator_id': '42', 'reference': 'operator-approval'},
            'origin': {'platform': 'discord', 'chat_id': '1', 'thread_id': '1', 'parent_chat_id': '2', 'scope_id': '3'},
        }
        intake = self.root / 'intake.json'
        intake.write_text(json.dumps(request))
        self.assertEqual(self.invoke('register', '--request', str(intake)).returncode, 0)

    def invoke(self, *args):
        return subprocess.run([sys.executable, '-m', 'factory_v1', '--state', str(self.state),
                                '--operator-id', '42', *args], text=True, capture_output=True,
                               timeout=15, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})

    def grant(self, ceiling=5, expires=None, kind='allowance', provider='fixture-provider', model='fixture-model', operation='generate', reference='subscription-allowance-approval'):
        return self.invoke('spend-grant', '--project', 'product', '--iteration', 'm1',
                           '--provider', provider, '--model', model,
                           '--operation', operation, '--kind', kind, '--ceiling', str(ceiling),
                           '--expires', str(self.expires if expires is None else expires), '--reference', reference)

    def stop_broker(self, process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if process.stderr:
            process.stderr.close()

    def broker(self, extra=(), provider='fixture-provider', model='fixture-model', operation='generate', subscription_socket=None):
        process = subprocess.Popen([sys.executable, '-m', 'factory_v1', '--state', str(self.state),
            '--operator-id', '42', 'model-broker', '--socket', str(self.sock), '--project', 'product',
            '--iteration', 'm1', '--provider', provider, '--model', model,
            '--operation', operation, *(['--subscription-socket', str(subscription_socket)] if subscription_socket else ['--fixture-url', self.fixture_url]), *extra],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.addCleanup(self.stop_broker, process)
        deadline = time.monotonic() + 5
        while not self.sock.exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(process.stderr.read().decode())
            time.sleep(.01)
        self.assertTrue(self.sock.exists(), 'broker socket did not become ready')
        return process

    def call(self, op_id='request-1', reserve=3, request=None):
        path = self.root / f'{op_id}.json'
        path.write_text(json.dumps({'prompt': 'transient no-secret fixture input'} if request is None else request))
        return self.invoke('model-call', '--socket', str(self.sock), '--operation-id', op_id,
                           '--reserve', str(reserve), '--request', str(path))

    def test_allowance_reaches_actual_unix_socket_operation_boundary_and_replay_is_idempotent(self):
        self.assertEqual(self.grant().returncode, 0)
        self.assertEqual(self.grant().returncode, 0)
        self.broker()
        result = self.call()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'actual_units': 2, 'result': 'fixture-answer', 'status': 'complete'})
        replay = self.call()
        self.assertEqual(replay.returncode, 0, replay.stderr)
        conflict = self.call(request={'prompt': 'different request under same key'})
        self.assertEqual(conflict.returncode, 2)
        self.assertEqual(json.loads(conflict.stderr)['error'], 'idempotency_conflict')
        self.assertEqual(len(FixtureHandler.calls), 1)
        with closing(sqlite3.connect(self.state)) as db:
            self.assertEqual(db.execute('select remaining from model_grants').fetchone()[0], 3)
            self.assertEqual(db.execute('select status,actual_units from model_operations').fetchone(), ('complete', 2))
            self.assertNotIn(b'transient no-secret fixture input', self.state.read_bytes())
            self.assertEqual(db.execute('select decision from model_decisions order by rowid').fetchall(),
                             [('grant_recorded',), ('admitted',), ('reconciled_complete',), ('refused',)])

    def test_unapproved_and_exhausted_spending_is_refused_and_persistently_blocks(self):
        self.broker()
        refused = self.call(reserve=3)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(json.loads(refused.stderr)['error'], 'spending_blocked')
        with closing(sqlite3.connect(self.state)) as db:
            item = json.loads(db.execute('select payload from iterations').fetchone()[0])
            self.assertEqual(item['model_access']['status'], 'blocked')
            self.assertEqual(db.execute('select decision,reason from model_decisions').fetchone(),
                             ('refused', 'authorization_unavailable_or_exhausted'))
        self.assertEqual(FixtureHandler.calls, [])

    def test_expired_and_mismatched_grants_do_not_reach_fixture(self):
        expired = self.grant(expires=int(time.time()) - 1)
        self.assertEqual(expired.returncode, 2)
        self.assertEqual(json.loads(expired.stderr)['error'], 'invalid_grant')
        self.assertEqual(self.grant().returncode, 0)
        with closing(sqlite3.connect(self.state)) as db:
            db.execute('update model_grants set expires=?', (int(time.time()) - 1,))
            db.commit()
        self.broker()
        expired_call = self.call('expired-op', reserve=3)
        self.assertEqual(expired_call.returncode, 2)
        self.assertEqual(json.loads(expired_call.stderr)['error'], 'spending_blocked')
        path = self.root / 'bad.json'
        path.write_text(json.dumps({'provider': 'other', 'prompt': 'x'}))
        result = self.invoke('model-call', '--socket', str(self.sock), '--operation-id', 'bad',
                             '--reserve', '3', '--request', str(path))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'invalid_request')
        self.assertEqual(FixtureHandler.calls, [])

    def test_uncertain_result_is_frozen_until_exact_trusted_reconciliation(self):
        self.assertEqual(self.grant().returncode, 0)
        self.broker()
        FixtureHandler.uncertain = True
        uncertain = self.call('uncertain-op', reserve=3)
        self.assertEqual(uncertain.returncode, 2)
        self.assertEqual(json.loads(uncertain.stderr)['error'], 'outcome_uncertain')
        FixtureHandler.uncertain = False
        retry = self.call('uncertain-op', reserve=3)
        self.assertEqual(retry.returncode, 2)
        self.assertEqual(json.loads(retry.stderr)['error'], 'outcome_uncertain')
        self.assertEqual(len(FixtureHandler.calls), 1)
        wrong_scope = self.invoke('spend-reconcile', '--project', 'product', '--iteration', 'm1',
            '--provider', 'other', '--model', 'fixture-model', '--operation', 'generate',
            '--operation-id', 'uncertain-op', '--outcome', 'failed', '--reference', 'receipt')
        self.assertEqual(wrong_scope.returncode, 2)
        self.assertEqual(json.loads(wrong_scope.stderr)['error'], 'scope_mismatch')
        fixed = self.invoke('spend-reconcile', '--project', 'product', '--iteration', 'm1',
            '--provider', 'fixture-provider', '--model', 'fixture-model', '--operation', 'generate',
            '--operation-id', 'uncertain-op', '--outcome', 'failed', '--reference', 'operator-reconciled-no-charge')
        self.assertEqual(fixed.returncode, 0, fixed.stderr)
        self.assertEqual(json.loads(fixed.stdout)['status'], 'failed')
        retry_after_receipt = self.call('uncertain-op', reserve=3)
        self.assertEqual(retry_after_receipt.returncode, 0, retry_after_receipt.stderr)
        self.assertEqual(json.loads(retry_after_receipt.stdout)['status'], 'failed')
        self.assertEqual(len(FixtureHandler.calls), 1)

    def test_fixture_route_rejects_non_loopback_and_live_provider_is_disabled(self):
        from factory_v1.spending import SpendingError, execute_controlled, initialize
        import sqlite3
        with closing(sqlite3.connect(self.state)) as db:
            initialize(db)
            self.assertEqual(self.grant().returncode, 0)
            with self.assertRaises(SpendingError) as error:
                execute_controlled(db, ('product', 'm1', 'fixture-provider', 'fixture-model', 'generate'),
                                   'invalid-route', {'prompt': 'x'}, 1, 'https://provider.example/v1')
        self.assertEqual(error.exception.code, 'provider_unavailable')
        self.assertEqual(FixtureHandler.calls, [])


    def subscription_fixture(self, status=200):
        """Real UDS HTTP fixture; not a provider or billing-success claim."""
        import socketserver
        case = self
        self.upstream_calls = []
        self.upstream_status = status
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                case.upstream_calls.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(case.upstream_status)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                done = {'type': 'response.output_item.done', 'output_index': 0, 'item': {'type': 'message', 'content': [{'type': 'output_text', 'text': 'labeled-fixture'}]}}
                body = {'type': 'response.completed', 'response': {'status': 'completed', 'model': 'gpt-6.1-sol', 'output': []}}
                self.wfile.write(('data: ' + json.dumps(done) + '\n\ndata: ' + json.dumps(body) + '\n\n').encode())
        path = self.root / 'upstream.sock'
        server = socketserver.UnixStreamServer(str(path), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return path

    def subscription(self, kind='allowance', status=200):
        path = self.subscription_fixture(status)
        result = self.grant(ceiling=2, kind=kind, provider='openai-codex', model='gpt-6.1-sol', operation='responses')
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.broker(provider='openai-codex', model='gpt-6.1-sol', operation='responses', subscription_socket=path)

    def receipt(self, op_id, provider='fixture-provider', model='fixture-model', operation='generate', outcome='failed', units=None, reference='trusted-no-charge-receipt'):
        return self.invoke('spend-reconcile', '--project', 'product', '--iteration', 'm1',
            '--provider', provider, '--model', model, '--operation', operation,
            '--operation-id', op_id, '--outcome', outcome, '--reference', reference,
            *(['--actual-units', str(units)] if units is not None else []))

    def test_scoped_approval_ceiling_and_fixed_adapter_reservation(self):
        self.assertEqual(self.grant(ceiling=3, kind='approval').returncode, 0)
        self.broker()
        lower = self.call('lower-reservation', reserve=1)
        self.assertEqual(json.loads(lower.stderr)['error'], 'invalid_reservation')
        self.assertEqual(FixtureHandler.calls, [])
        self.assertEqual(self.call().returncode, 0)
        exhausted = self.call('next-operation')
        self.assertEqual(json.loads(exhausted.stderr)['error'], 'spending_blocked')
        self.assertEqual(len(FixtureHandler.calls), 1)
        item = json.loads(self.invoke('inspect', '--project', 'product', '--iteration', 'm1').stdout)
        self.assertEqual(item['model_access']['status'], 'blocked')
        self.assertFalse(item['execution_allowed'])

    def test_exact_provider_model_and_operation_grants_are_not_interchangeable(self):
        for terms in ({'provider': 'other'}, {'model': 'other'}, {'operation': 'other'}):
            self.assertEqual(self.grant(reference='mismatch-' + next(iter(terms)), **terms).returncode, 0)
        self.broker()
        denied = self.call()
        self.assertEqual(json.loads(denied.stderr)['error'], 'spending_blocked')
        self.assertEqual(FixtureHandler.calls, [])

    def test_new_key_cannot_escape_uncertain_outcome_and_receipt_replay_is_exact(self):
        self.assertEqual(self.grant(ceiling=20).returncode, 0)
        self.broker()
        FixtureHandler.uncertain = True
        self.assertEqual(json.loads(self.call().stderr)['error'], 'outcome_uncertain')
        FixtureHandler.uncertain = False
        self.assertEqual(json.loads(self.call('new-key').stderr)['error'], 'outcome_uncertain')
        self.assertEqual(len(FixtureHandler.calls), 1)
        invalid = self.receipt('request-1', outcome='complete', units=4)
        self.assertEqual(json.loads(invalid.stderr)['error'], 'invalid_receipt')
        first = self.receipt('request-1', outcome='complete', units=2)
        self.assertEqual(first.returncode, 0, first.stderr)
        again = self.receipt('request-1', outcome='complete', units=2)
        self.assertTrue(json.loads(again.stdout)['replay'])
        conflict = self.receipt('request-1', outcome='complete', units=1)
        self.assertEqual(json.loads(conflict.stderr)['error'], 'reconciliation_conflict')
        self.assertEqual(self.call('new-key').returncode, 0)
        self.assertEqual(len(FixtureHandler.calls), 2)

    def test_subscription_uds_pins_route_model_tier_and_uses_only_allowance(self):
        self.subscription()
        payload = {'input': [{'role': 'user', 'content': 'labeled fixture request'}]}
        result = self.call(reserve=1, request=payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result']['output'][0]['content'][0]['text'], 'labeled-fixture')
        path, upstream = self.upstream_calls[0]
        self.assertEqual(path, '/v1/responses')
        self.assertEqual(upstream['model'], 'gpt-6.1-sol')
        self.assertEqual(upstream['service_tier'], 'default')
        self.assertFalse(upstream['store'])
        self.assertTrue(upstream['stream'])
        self.assertEqual(len(self.upstream_calls), 1)
        self.assertEqual(self.call(reserve=1, request=payload).returncode, 0)
        self.assertEqual(len(self.upstream_calls), 1)
        for key in ['model', 'api_key', 'service_tier', 'url', 'unknown']:
            denied = self.call('deny-' + key, reserve=1, request={**payload, key: 'must-not-forward'})
            self.assertEqual(json.loads(denied.stderr)['error'], 'invalid_request')
        self.assertEqual(len(self.upstream_calls), 1)
        with closing(sqlite3.connect(self.state)) as db:
            self.assertEqual(db.execute('SELECT remaining FROM model_grants').fetchone()[0], 1)
            self.assertNotIn(b'labeled fixture request', self.state.read_bytes())
            self.assertNotIn(b'labeled-fixture', self.state.read_bytes())

    def test_paid_approval_does_not_authorize_subscription_or_any_paid_fallback(self):
        self.subscription(kind='approval')
        result = self.call(reserve=1, request={'input': 'fixture'})
        self.assertEqual(json.loads(result.stderr)['error'], 'spending_blocked')
        self.assertEqual(self.upstream_calls, [])
        self.assertEqual(FixtureHandler.calls, [])

    def test_quota_refusal_persists_and_requires_billing_receipt_then_explicit_resume(self):
        self.subscription(status=429)
        payload = {'input': 'fixture'}
        refused = self.call(reserve=1, request=payload)
        self.assertEqual(json.loads(refused.stderr)['error'], 'quota_exhausted')
        self.upstream_status = 200
        self.assertEqual(json.loads(self.call('another-key', reserve=1, request=payload).stderr)['error'], 'outcome_uncertain')
        self.assertEqual(len(self.upstream_calls), 1)
        self.assertEqual(self.receipt('request-1', provider='openai-codex', model='gpt-6.1-sol', operation='responses').returncode, 0)
        retry = self.call('another-key', reserve=1, request=payload)
        self.assertEqual(json.loads(retry.stderr)['error'], 'quota_exhausted')
        resumed = self.invoke('spend-resume', '--project', 'product', '--iteration', 'm1',
            '--provider', 'openai-codex', '--model', 'gpt-6.1-sol', '--operation', 'responses', '--reference', 'verified-quota-restored')
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.call('another-key', reserve=1, request=payload).returncode, 0)
        self.assertEqual(len(self.upstream_calls), 2)

    def test_capability_request_cap_expiry_and_exact_peer_uid_are_enforced(self):
        self.assertEqual(self.grant(ceiling=20).returncode, 0)
        process = self.broker(extra=['--max-calls', '1', '--ttl', '2'])
        self.assertEqual(self.call().returncode, 0)
        exhausted = self.call('next')
        self.assertEqual(json.loads(exhausted.stderr)['error'], 'capability_exhausted')
        process.wait(timeout=5)
        self.assertFalse(self.sock.exists())
        process = self.broker(extra=['--worker-uid', str(os.getuid() + 1)])
        denied = self.call('wrong-worker')
        self.assertEqual(json.loads(denied.stderr)['error'], 'worker_identity_mismatch')
        self.assertEqual(len(FixtureHandler.calls), 1)

    def test_wrong_operator_and_paused_iteration_cannot_start_broker(self):
        from factory_v1.spending import SpendingError, serve_unix_socket
        with self.assertRaises(SpendingError) as error:
            serve_unix_socket(str(self.sock), str(self.state), ('product', 'm1', 'fixture-provider', 'fixture-model', 'generate'), self.fixture_url, operator_id='other')
        self.assertEqual(error.exception.code, 'approval_required')
        self.assertFalse(self.sock.exists())
        with closing(sqlite3.connect(self.state)) as db:
            item = json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])
            item['status'] = 'paused'
            db.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
            db.commit()
        with self.assertRaises(SpendingError) as error:
            serve_unix_socket(str(self.sock), str(self.state), ('product', 'm1', 'fixture-provider', 'fixture-model', 'generate'), self.fixture_url, operator_id='42')
        self.assertEqual(error.exception.code, 'iteration_paused')
        self.assertEqual(FixtureHandler.calls, [])

    def test_capability_expiry_is_rechecked_after_controller_lock_wait(self):
        import fcntl
        self.assertEqual(self.grant(ceiling=20).returncode, 0)
        process = self.broker(extra=['--ttl', '1'])
        results = []
        with open(str(self.state) + '.onboarding.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            caller = threading.Thread(target=lambda: results.append(self.call()))
            caller.start()
            time.sleep(1.3)
            fcntl.flock(lock, fcntl.LOCK_UN)
        caller.join(timeout=10)
        self.assertFalse(caller.is_alive())
        self.assertEqual(json.loads(results[0].stderr)['error'], 'capability_exhausted')
        self.assertEqual(FixtureHandler.calls, [])
        process.wait(timeout=5)
        with closing(sqlite3.connect(self.state)) as db:
            self.assertEqual(db.execute('SELECT remaining FROM model_grants').fetchone()[0], 20)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM model_operations').fetchone()[0], 0)

    def test_pre_admission_and_disabled_route_refusals_survive_restart_without_secrets(self):
        self.assertEqual(self.grant().returncode, 0)
        process = self.broker()
        marker = 'never-persist-request-secret'
        override = self.call('override', request={'provider': marker, 'prompt': marker})
        self.assertEqual(json.loads(override.stderr)['error'], 'invalid_request')
        reservation = self.call('under-reserve', reserve=1)
        self.assertEqual(json.loads(reservation.stderr)['error'], 'invalid_reservation')
        self.stop_broker(process)
        self.sock.unlink(missing_ok=True)
        disabled = self.invoke('model-broker', '--socket', str(self.sock), '--project', 'product',
            '--iteration', 'm1', '--provider', 'paid-provider', '--model', 'paid-model',
            '--operation', 'generate', '--fixture-url', self.fixture_url)
        self.assertEqual(json.loads(disabled.stderr)['error'], 'provider_unavailable')
        self.assertFalse(self.sock.exists())
        self.broker()
        with closing(sqlite3.connect(self.state)) as db:
            refusals = db.execute("SELECT provider,model,operation,operation_id,reason FROM model_decisions WHERE decision='refused' ORDER BY rowid").fetchall()
            self.assertEqual(refusals, [
                ('fixture-provider', 'fixture-model', 'generate', 'override', 'invalid_request'),
                ('fixture-provider', 'fixture-model', 'generate', 'under-reserve', 'invalid_reservation'),
                ('paid-provider', 'paid-model', 'generate', None, 'provider_unavailable')])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM model_operations').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT remaining FROM model_grants').fetchone()[0], 5)
        self.assertNotIn(marker.encode(), self.state.read_bytes())
        self.assertEqual(FixtureHandler.calls, [])

    def test_invalid_json_bounds_and_grant_replays_are_structured(self):
        self.assertEqual(self.grant().returncode, 0)
        self.assertEqual(json.loads(self.grant(ceiling=6).stderr)['error'], 'grant_conflict')
        for ceiling in [0, -1, 2**63]:
            self.assertEqual(json.loads(self.grant(ceiling=ceiling, reference='invalid-' + str(ceiling)).stderr)['error'], 'invalid_grant')
        self.broker()
        for payload in [{'prompt': float('nan')}, {'prompt': 'x' * 33000}]:
            refused = self.call('invalid', request=payload)
            self.assertEqual(json.loads(refused.stderr)['error'], 'invalid_request')
        self.assertEqual(FixtureHandler.calls, [])


if __name__ == '__main__':
    unittest.main()
