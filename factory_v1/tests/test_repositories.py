"""Public CLI repository onboarding; HTTP fixtures are deterministic, not live evidence."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class RepositoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / 'state.sqlite'
        self.metadata = {'id': 123, 'full_name': 'example/product', 'name': 'product',
                         'owner': {'login': 'example'}, 'private': True, 'description': 'marker-123'}
        self.calls = []
        self.repo = dict(self.metadata)
        self.drop_creation_response = False
        self.preflight_barrier = None
        self.creation_status = 201
        self.readback_status = 200
        self.raw_metadata = None
        self.truncated_path = None
        self.post_creation_repo = None
        self.creation_id = 123
        self.creation_event = threading.Event()
        self.release_response = None
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                case.calls.append(('GET', self.path))
                if self.path == '/user':
                    self.reply(200, {'login': 'example'})
                elif self.path == '/repos/example/product':
                    observed = case.repo
                    if observed is None and case.preflight_barrier:
                        try:
                            case.preflight_barrier.wait(timeout=0.5)
                        except threading.BrokenBarrierError:
                            pass
                    self.reply(404, {}) if observed is None else self.reply(case.readback_status, observed)
                elif self.path == '/repos/other/product':
                    self.reply(404, {})
                else:
                    self.reply(403, {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.calls.append(('POST', self.path, body))
                case.repo = dict(case.metadata)
                case.creation_event.set()
                if case.release_response:
                    case.release_response.wait(timeout=5)
                    self.close_connection = True
                    return
                if case.drop_creation_response:
                    self.close_connection = True
                    return
                response = dict(case.repo, id=case.creation_id)
                if case.post_creation_repo is not None:
                    case.repo = dict(case.post_creation_repo)
                self.reply(case.creation_status, response)

            def reply(self, status, body):
                self.send_response(status)
                if status < 300 and self.path == case.truncated_path:
                    self.send_header('Content-Length', '1000')
                    self.end_headers()
                    self.wfile.write(b'{"id":')
                    self.close_connection = True
                    return
                self.end_headers()
                encoded = (case.raw_metadata if self.path == '/repos/example/product' and
                           case.raw_metadata is not None else json.dumps(body))
                self.wfile.write(encoded.encode())

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.request = {
            'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
            'idea': 'Approved product', 'approval': {'operator_id': '42', 'reference': 'message-1'},
            'origin': {'platform': 'discord', 'chat_id': '123', 'thread_id': '123',
                       'parent_chat_id': '456', 'scope_id': '789'},
        }

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'factory_v1', '--state', str(self.state),
                               '--operator-id', '42', *args], capture_output=True, text=True,
                              timeout=15, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})

    def register(self):
        path = self.root / 'request.json'
        path.write_text(json.dumps(self.request))
        result = self.cli('register', '--request', str(path))
        self.assertEqual(result.returncode, 0, result.stderr)

    def onboard(self, timeout='0.2'):
        return self.cli('onboard', '--project', 'product', '--iteration', 'm1',
                        '--api-base', self.base, '--bearer', 'harmless-dummy', '--timeout', timeout)

    def receipt_path(self, **changes):
        receipt = {'repository': 'example/product', 'repository_id': 123,
                   'marker': 'marker-123', 'api_base': self.base,
                   'reference': 'trusted-controller-creation-receipt'}
        receipt.update(changes)
        path = self.root / 'receipt.json'
        path.write_text(json.dumps(receipt))
        return str(path)

    def restore(self, path):
        return self.cli('onboard', '--project', 'product', '--iteration', 'm1',
                        '--api-base', self.base, '--creation-receipt', path)

    def test_null_successful_preflight_never_authorizes_creation(self):
        self.raw_metadata = 'null'
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.onboard()
        self.assertFalse(any(call[0] == 'POST' for call in self.calls), self.calls)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'github_unavailable')
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        self.assertNotIn('repository_onboarding', json.loads(inspected.stdout))

    def test_nonpositive_metadata_identity_is_refused(self):
        for invalid_id in [0, -1]:
            with self.subTest(repository_id=invalid_id):
                self.state = self.root / f'invalid-metadata-{invalid_id}.sqlite'
                self.register()
                self.repo['id'] = invalid_id
                result = self.onboard()
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
                inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
                self.assertNotIn('repository_onboarding', json.loads(inspected.stdout))
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))

    def test_truncated_preflight_is_an_actionable_blocker(self):
        self.truncated_path = '/repos/example/product'
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], 'github_unavailable')
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        self.assertNotIn('repository_onboarding', json.loads(inspected.stdout))

    def test_truncated_creation_response_reconciles_without_second_post(self):
        self.repo = None
        self.truncated_path = '/user/repos'
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], 'creation_uncertain')
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        self.assertEqual(json.loads(inspected.stdout)['repository_onboarding']['status'], 'pending')
        self.assertEqual(self.onboard().returncode, 0)
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)

    def test_truncated_readback_preserves_returned_creation_id(self):
        self.repo = None
        self.truncated_path = '/repos/example/product'
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], 'creation_uncertain')
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        pending = json.loads(inspected.stdout)['repository_onboarding']
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual(pending['created_repository_id'], 123)
        self.truncated_path = None
        self.assertEqual(self.onboard().returncode, 0)
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)

    def test_nonpositive_identity_cannot_reconcile_lost_creation_response(self):
        self.repo = None
        self.drop_creation_response = True
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        self.assertEqual(json.loads(self.onboard().stderr)['error'], 'creation_uncertain')
        for invalid_id in [0, -1]:
            with self.subTest(repository_id=invalid_id):
                self.repo['id'] = invalid_id
                result = self.onboard()
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
                inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
                self.assertEqual(json.loads(inspected.stdout)['repository_onboarding']['status'], 'pending')
        self.repo['id'] = 123
        self.assertEqual(self.onboard().returncode, 0)
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)

    def test_trusted_creation_receipt_recovers_lost_state_with_exact_id_readback(self):
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.restore(self.receipt_path())
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record['repository_onboarding']['metadata'], self.metadata)
        self.assertEqual(record['repository_onboarding']['creation_receipt']['reference'],
                         'trusted-controller-creation-receipt')
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))
        self.assertEqual(self.onboard().returncode, 0)

    def test_receipt_requires_exact_trusted_scope_before_network(self):
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        for changes in [{'repository': 'other/product'}, {'marker': 'wrong'},
                        {'api_base': self.base + '/other'}, {'repository_id': True},
                        {'repository_id': 0}, {'reference': ''}, {'extra': 'untrusted'}]:
            with self.subTest(changes=changes):
                result = self.restore(self.receipt_path(**changes))
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error'], 'invalid_receipt')
        self.assertEqual(self.calls, [])

    def test_receipt_recovery_never_creates_an_absent_target(self):
        self.repo = None
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.restore(self.receipt_path())
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'creation_uncertain')
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        self.assertNotIn('repository_onboarding', json.loads(inspected.stdout))

    def test_successful_post_id_must_match_readback_and_restart(self):
        self.repo = None
        self.post_creation_repo = dict(self.metadata, id=999)
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        pending = json.loads(inspected.stdout)['repository_onboarding']
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual(pending['created_repository_id'], 123)
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
        self.repo = dict(self.metadata)
        result = self.onboard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['repository_onboarding']['metadata']['id'], 123)
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)

    def test_invalid_successful_post_id_cannot_authorize_later_adoption(self):
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        for index, invalid_id in enumerate([None, True, 0, -1, '123', []]):
            with self.subTest(repository_id=invalid_id):
                self.state = self.root / f'invalid-post-{index}.sqlite'
                self.repo = None
                self.creation_id = invalid_id
                self.register()
                result = self.onboard()
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
                result = self.onboard()
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
                inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
                self.assertEqual(json.loads(inspected.stdout)['repository_onboarding']['status'], 'blocked')
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 6)

    def test_successful_creation_with_denied_readback_remains_reconcilable(self):
        self.repo = None
        self.readback_status = 403
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'creation_uncertain')
        self.readback_status = 200
        result = self.onboard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)

    def test_process_death_after_creation_reconciles_committed_intent(self):
        self.repo = None
        self.release_response = threading.Event()
        self.addCleanup(self.release_response.set)
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        process = subprocess.Popen(
            [sys.executable, '-m', 'factory_v1', '--state', str(self.state),
             '--operator-id', '42', 'onboard', '--project', 'product', '--iteration', 'm1',
             '--api-base', self.base, '--timeout', '10'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        try:
            self.assertTrue(self.creation_event.wait(timeout=5))
        finally:
            process.kill()
            process.communicate(timeout=5)
            self.release_response.set()
        pending = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        self.assertEqual(json.loads(pending.stdout)['repository_onboarding']['status'], 'pending')
        result = self.onboard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)

    def test_colliding_new_target_is_not_adopted_even_with_matching_marker(self):
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'repository_collision')
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))

    def test_receipt_cannot_adopt_another_repository_id(self):
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.restore(self.receipt_path(repository_id=999))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        self.assertNotIn('repository_onboarding', json.loads(inspected.stdout))

    def test_created_target_requires_exact_private_owner_name_marker_readback(self):
        self.repo = None
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.metadata['private'] = False
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)
        self.repo = dict(self.metadata, private=True)
        for field, value in [('owner', {'login': 'other'}), ('name', 'other'),
                             ('full_name', 'other/product'), ('description', 'other')]:
            original = self.repo[field]
            self.repo[field] = value
            result = self.onboard()
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
            self.repo[field] = original
        self.assertEqual(self.onboard().returncode, 0)
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)

    def test_alternative_account_is_not_guessed_or_created(self):
        self.repo = None
        self.request['repository'] = 'other/product'
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'account_capability_required')
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))

    def test_missing_selected_existing_target_never_creates_a_replacement(self):
        self.repo = None
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))

    def test_ambiguous_or_parser_limit_metadata_is_a_structured_blocker(self):
        self.register()
        for raw in [json.dumps(self.metadata)[:-1] + ', "private": false, "private": true}',
                    '{"id":' + '9' * 5000 + '}']:
            with self.subTest(raw=raw[:80]):
                self.raw_metadata = raw
                result = self.onboard()
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error'], 'github_unavailable')
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        self.assertNotIn('repository_onboarding', json.loads(inspected.stdout))

    def test_deep_external_json_is_an_actionable_blocker(self):
        self.register()
        deep_json = '[' * 10000 + '0' + ']' * 10000
        for raw in [deep_json, deep_json[:-1]]:
            with self.subTest(complete=raw == deep_json):
                # Python 3.14 can decode valid nesting that older versions reject.
                try:
                    json.loads(raw)
                except (ValueError, RecursionError):
                    expected_error = 'github_unavailable'
                    expected_message = 'configured capability'
                else:
                    expected_error = 'repository_mismatch'
                    expected_message = 'selected repository'
                self.raw_metadata = raw
                result = self.onboard()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, '')
                blocker = json.loads(result.stderr)
                self.assertEqual(blocker['error'], expected_error)
                self.assertIn(expected_message, blocker['message'])
                inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
                self.assertNotIn('repository_onboarding', json.loads(inspected.stdout))
                self.assertFalse(any(call[0] == 'POST' for call in self.calls))

    def test_invalid_adapter_configuration_is_refused_before_network(self):
        self.register()
        for base, timeout in [('not-a-url', '1'), (self.base, '0'), (self.base, 'nan'),
                              (self.base, 'inf'), ('http://remote.invalid', '1'),
                              (self.base + '?scope=other', '1')]:
            with self.subTest(base=base, timeout=timeout):
                result = self.cli('onboard', '--project', 'product', '--iteration', 'm1',
                                  '--api-base', base, '--timeout', timeout)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr)['error'], 'invalid_capability')
        self.assertEqual(self.calls, [])

    def test_reconciliation_refuses_a_different_capability_endpoint(self):
        self.register()
        self.assertEqual(self.onboard().returncode, 0)
        self.calls.clear()
        result = self.cli('onboard', '--project', 'product', '--iteration', 'm1',
                          '--api-base', self.base + '/different')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'capability_conflict')
        self.assertEqual(self.calls, [])

    def test_definitive_creation_rejection_cannot_adopt_a_colliding_repository(self):
        self.repo = None
        self.creation_status = 422
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'github_blocked')
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'github_blocked')
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)

    def test_only_required_metadata_is_retained_not_server_credentials(self):
        self.repo['temp_clone_token'] = 'fixture-secret-never-retain'
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('fixture-secret-never-retain', result.stdout)
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        self.assertNotIn('fixture-secret-never-retain', inspected.stdout)
        self.assertNotIn(b'fixture-secret-never-retain', self.state.read_bytes())

    def test_malformed_repository_metadata_is_a_structured_refusal(self):
        self.register()
        self.repo['owner'] = None
        result = self.onboard()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))

    def test_removing_creation_intent_on_replay_is_a_scope_conflict(self):
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        self.request.pop('repository_intent')
        path = self.root / 'replay.json'
        path.write_text(json.dumps(self.request))
        result = self.cli('register', '--request', str(path))
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'intake_conflict')

    def test_verified_repository_cannot_be_replaced_by_same_name_and_marker(self):
        self.register()
        self.assertEqual(self.onboard().returncode, 0)
        self.repo['id'] = 999
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'repository_mismatch')
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        self.assertEqual(json.loads(inspected.stdout)['repository_onboarding']['metadata']['id'], 123)

    def test_concurrent_onboarding_serializes_the_creation_boundary(self):
        from concurrent.futures import ThreadPoolExecutor
        self.repo = None
        self.preflight_barrier = threading.Barrier(2)
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.onboard('2'), range(2)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)

    def test_wrong_operator_cannot_use_recorded_creation_approval(self):
        self.repo = None
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = subprocess.run([sys.executable, '-m', 'factory_v1', '--state', str(self.state),
                                '--operator-id', '99', 'onboard', '--project', 'product',
                                '--iteration', 'm1', '--api-base', self.base],
                               text=True, capture_output=True, timeout=15,
                               env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'approval_required')
        self.assertEqual(self.calls, [])

    def test_reconciliation_cannot_recreate_a_missing_uncertain_target(self):
        self.repo = None
        self.drop_creation_response = True
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        self.assertEqual(self.onboard().returncode, 2)
        self.repo = None  # A 404 is not proof a timed-out mutation cannot complete later.
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'creation_uncertain')
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)

    def test_lost_creation_response_is_durable_and_reconciled_before_retry(self):
        self.repo = None
        self.drop_creation_response = True
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)['error'], 'creation_uncertain')
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        pending = json.loads(inspected.stdout)['repository_onboarding']
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual(pending['marker'], 'marker-123')
        result = self.onboard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['repository_onboarding']['status'], 'verified')
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)
        self.assertEqual(self.onboard().returncode, 0)
        self.assertEqual(len([call for call in self.calls if call[0] == 'POST']), 1)

    def test_approved_new_product_creates_private_in_resolved_account(self):
        self.repo = None
        self.request['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        self.assertEqual(item['repository_onboarding']['metadata'], self.metadata)
        self.assertEqual(item['repository_onboarding']['mode'], 'new')
        self.assertIn(('GET', '/user'), self.calls)
        self.assertIn(('POST', '/user/repos', {'name': 'product', 'private': True,
                                             'description': 'marker-123', 'auto_init': False}), self.calls)
        self.assertEqual(self.calls[-1], ('GET', '/repos/example/product'))

    def test_selected_existing_repository_is_read_back_without_creation(self):
        self.register()
        result = self.onboard()
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)
        self.assertEqual(item['repository_onboarding']['status'], 'verified')
        self.assertEqual(item['repository_onboarding']['metadata'], self.metadata)
        self.assertFalse(item['execution_allowed'])
        self.assertFalse(any(call[0] == 'POST' for call in self.calls))
        inspected = self.cli('inspect', '--project', 'product', '--iteration', 'm1')
        self.assertEqual(json.loads(inspected.stdout), item)
