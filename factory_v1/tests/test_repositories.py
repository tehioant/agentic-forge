"""Direct repository state and adapter checks with mocked external boundaries."""
import json
import sys
import tempfile
import unittest
from pathlib import Path


import copy
import io
import sqlite3
import http.client
import urllib.error
from email.message import Message
from contextlib import closing
from unittest import mock
from factory_v1 import repositories


class RepositoryComponentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / 'state.sqlite'
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
                     'approval': {'operator_id': '42', 'reference': 'approved'},
                     'execution_allowed': False}
        self.metadata = {'id': 123, 'full_name': 'example/product', 'name': 'product',
                         'owner': {'login': 'example'}, 'private': True, 'description': 'marker-123'}
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
            db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(self.item)))
        self.api = mock.Mock(spec=repositories.GitHub)
        self.api.base = 'https://api.github.com'
        self.api.request.return_value = copy.deepcopy(self.metadata)
        for target in ('socket.socket', 'subprocess.Popen'):
            patcher = mock.patch(target, side_effect=AssertionError('external creation denied'))
            patcher.start()
            self.addCleanup(patcher.stop)

    def persist(self):
        with closing(sqlite3.connect(self.state)) as db, db:
            repositories.save_iteration(db, self.item)

    def stored(self):
        with closing(sqlite3.connect(self.state)) as db:
            return json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])

    def onboard(self, receipt=None):
        with closing(sqlite3.connect(self.state)) as db:
            return repositories.onboard(db, self.item, self.api, receipt)

    def new(self):
        self.item['repository_intent'] = {'mode': 'new', 'marker': 'marker-123'}
        self.persist()

    def receipt(self, **changes):
        return dict({'repository': 'example/product', 'repository_id': 123, 'marker': 'marker-123',
                     'api_base': self.api.base, 'reference': 'trusted receipt'}, **changes)

    def refuse(self, code, receipt=None):
        with self.assertRaises(repositories.RepositoryError) as raised:
            self.onboard(receipt)
        self.assertEqual(raised.exception.code, code)

    def test_existing_exact_metadata_reopens_without_creation_or_secret_retention(self):
        self.api.request.return_value['temp_clone_token'] = 'secret-never-retain'
        result = self.onboard()
        self.assertEqual(result, self.stored())
        self.assertEqual(result['repository_onboarding']['metadata'], self.metadata)
        self.assertEqual(result['repository_onboarding']['mode'], 'existing')
        self.assertFalse(result['execution_allowed'])
        self.assertNotIn(b'secret-never-retain', self.state.read_bytes())
        self.api.request.assert_called_once_with('/repos/example/product')
        self.assertEqual(self.onboard(), result)

    def test_repository_identity_and_capability_are_immutable(self):
        cases = [None, [], dict(self.metadata, owner=None)]
        cases += [dict(self.metadata, id=v) for v in (0, -1, True, '123')]
        cases += [dict(self.metadata, **{field: value}) for field, value in
                  (('full_name', 'other/product'), ('name', 'other'), ('owner', {'login': 'other'}), ('private', 1))]
        for phase, observations in (('unverified', cases), ('verified', [dict(self.metadata, id=999)])):
            with self.subTest(phase=phase):
                self.api.request.reset_mock()
                if phase == 'verified':
                    self.api.request.return_value = copy.deepcopy(self.metadata)
                    self.onboard()
                original = self.state.read_bytes()
                original_record = self.stored()
                for metadata in observations:
                    with self.subTest(metadata=metadata):
                        self.api.request.return_value = metadata
                        self.refuse('repository_mismatch')
                        self.assertEqual(self.state.read_bytes(), original)
                        self.assertEqual(self.stored(), original_record)
                self.assertTrue(all(call.args == ('/repos/example/product',) for call in self.api.request.call_args_list))
        self.api.request.reset_mock()
        self.api.base += '/other'
        self.refuse('capability_conflict')
        self.api.request.assert_not_called()

    def test_creation_intent_precedes_single_effect(self):
        from concurrent.futures import ThreadPoolExecutor
        self.new()
        observed = None
        def request(path, body=None):
            nonlocal observed
            if path == '/user':
                return {'login': 'example'}
            if body is not None:
                pending = self.stored()['repository_onboarding']
                self.assertEqual(pending['status'], 'pending')
                self.assertEqual(pending['marker'], 'marker-123')
                self.assertEqual(body, {'name': 'product', 'private': True, 'description': 'marker-123', 'auto_init': False})
                observed = self.metadata
                return {'id': 123}
            if observed is not None:
                retained = self.stored()['repository_onboarding']
                if retained['status'] == 'pending':
                    self.assertEqual(retained['created_repository_id'], 123)
                else:
                    self.assertEqual(retained['metadata']['id'], 123)
            return observed
        self.api.request.side_effect = request
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.onboard) for _ in range(2)]
            results = [future.result(timeout=3) for future in futures]
        self.assertEqual(results[0], results[1])
        record = results[0]
        self.assertEqual(record['repository_onboarding']['status'], 'verified')
        self.assertEqual(record['repository_onboarding']['mode'], 'new')
        self.assertEqual(record['repository_onboarding']['metadata'], self.metadata)
        self.assertEqual(record, self.stored())
        self.assertEqual(sum(len(c.args) == 2 for c in self.api.request.call_args_list), 1)

    def test_creation_authority_refuses_before_effect(self):
        from factory_v1 import __main__ as cli
        from factory_v1.errors import IntakeError
        self.new()
        for boundary, responses, code in (
                ('collision', [self.metadata, {'login': 'example'}], 'repository_collision'),
                ('account', [None, {'login': 'other'}], 'account_capability_required'),
                ('operator', [], 'approval_required')):
            with self.subTest(boundary=boundary):
                self.api.request.reset_mock(); self.api.request.side_effect = responses
                if boundary == 'operator':
                    with mock.patch.object(sys, 'argv', ['factory', '--state', str(self.state), '--operator-id', '99',
                            'onboard', '--project', 'product', '--iteration', 'm1', '--api-base', self.api.base]), \
                            mock.patch.object(repositories.GitHub, 'request') as request, self.assertRaises(IntakeError) as raised:
                        cli.main()
                    self.assertEqual(raised.exception.code, code); request.assert_not_called()
                else:
                    self.refuse(code)
                    if boundary == 'account': self.assertEqual(self.api.request.call_count, 2)
                self.assertNotIn('repository_onboarding', self.stored())
                self.assertTrue(all(len(call.args) == 1 for call in self.api.request.call_args_list))

    def test_creation_unknown_reconciles_without_repost(self):
        cases = (('lost', repositories.RepositoryError('github_unavailable', 'lost response'), None),
                 ('interrupted', SystemExit('interrupted at effect return'), None),
                 ('acknowledged', {'id': 123}, repositories.RepositoryError('github_blocked', 'denied', 403)))
        for name, response, readback in cases:
            with self.subTest(outcome=name):
                self.item.pop('repository_onboarding', None)
                self.new()
                self.api.request.reset_mock()
                self.api.request.side_effect = [None, {'login': 'example'}, response] + ([readback] if readback else [])
                with self.assertRaises(SystemExit if isinstance(response, SystemExit) else repositories.RepositoryError):
                    self.onboard()
                self.assertEqual(self.stored()['repository_onboarding']['status'], 'pending')
                if readback:
                    self.assertEqual(self.stored()['repository_onboarding']['created_repository_id'], 123)
                    invalid = [dict(self.metadata, **{field: value}) for field, value in
                               (('id', 999), ('private', False), ('owner', {'login': 'other'}),
                                ('name', 'other'), ('full_name', 'other/product'), ('description', 'other'))]
                else:
                    self.api.request.side_effect = [None, {'login': 'example'}]
                    self.refuse('creation_uncertain')
                    invalid = [dict(self.metadata, id=0)]
                for metadata in invalid:
                    self.api.request.side_effect = [metadata, {'login': 'example'}]
                    self.refuse('repository_mismatch')
                    self.assertEqual(self.stored()['repository_onboarding']['status'], 'pending')
                self.api.request.side_effect = [self.metadata, {'login': 'example'}]
                self.assertEqual(self.onboard()['repository_onboarding']['status'], 'verified')
                self.assertEqual(sum(len(c.args) == 2 for c in self.api.request.call_args_list), 1)


    def test_invalid_post_identity_and_definitive_rejection_remain_blocked(self):
        for value in (None, True, 0, -1, '123', []):
            self.item.pop('repository_onboarding', None)
            self.new()
            self.api.request.reset_mock()
            self.api.request.side_effect = [None, {'login': 'example'}, {'id': value}]
            self.refuse('repository_mismatch')
            self.assertEqual(self.stored()['repository_onboarding']['status'], 'blocked')
            self.refuse('repository_mismatch')
            self.assertEqual(self.api.request.call_count, 3)
        for status in (401, 403, 404, 409, 422):
            self.item.pop('repository_onboarding', None)
            self.persist()
            self.api.request.reset_mock()
            self.api.request.side_effect = [None, {'login': 'example'}, repositories.RepositoryError('github_blocked', 'refused', status)]
            self.refuse('github_blocked')
            self.refuse('github_blocked')
            self.assertEqual(self.api.request.call_count, 3)

    def test_receipt_shape_binding_absence_and_immutable_id(self):
        self.new()
        for changes in ({'repository': 'other/product'}, {'marker': 'other'}, {'api_base': self.api.base + '/other'},
                        {'repository_id': True}, {'repository_id': 0}, {'reference': ''}, {'extra': 'untrusted'}):
            self.refuse('invalid_receipt', self.receipt(**changes))
        self.api.request.assert_not_called()
        self.api.request.side_effect = [None, {'login': 'example'}]
        self.refuse('creation_uncertain', self.receipt())
        self.assertNotIn('repository_onboarding', self.stored())
        self.api.request.side_effect = [self.metadata, {'login': 'example'}]
        self.refuse('repository_mismatch', self.receipt(repository_id=999))
        self.assertNotIn('repository_onboarding', self.stored())
        self.api.request.side_effect = [self.metadata, {'login': 'example'}]
        record = self.onboard(self.receipt())
        self.assertEqual(record, self.stored())
        self.assertEqual(record['repository_onboarding']['creation_receipt'], self.receipt())
        self.api.request.side_effect = [self.metadata, {'login': 'example'}]
        self.assertEqual(self.onboard(), record)
        self.assertTrue(all(len(c.args) == 1 for c in self.api.request.call_args_list))



    def test_adapter_configuration_and_raw_response_refusals_without_network(self):
        for base, timeout in (('not-a-url', 1), (self.api.base, 0), (self.api.base, float('nan')),
                              (self.api.base, float('inf')), ('http://remote.invalid', 1),
                              (self.api.base + '?scope=other', 1)):
            with self.assertRaises(repositories.RepositoryError) as raised:
                repositories.GitHub(base, 'unused', timeout)
            self.assertEqual(raised.exception.code, 'invalid_capability')
        self.assertIsNone(repositories.NoRedirect().redirect_request(mock.Mock(), io.BytesIO(), 302, 'redirect', mock.Mock(), 'https://other.invalid'))
        api = repositories.GitHub(self.api.base, 'unused', 1)
        for raw in ('null', '{"id":1,"id":2}', '{"id":' + '9' * 5000 + '}', '{', '[' * 10000 + '0' + ']' * 9999):
            api.opener = mock.Mock()
            api.opener.open.return_value = io.StringIO(raw)
            with self.assertRaises(repositories.RepositoryError) as raised:
                api.request('/repos/example/product')
            self.assertEqual(raised.exception.code, 'github_unavailable')
        api.opener.open.side_effect = None
        api.opener.open.return_value = io.StringIO('[' * 10000 + '0' + ']' * 10000)
        try:
            nested = api.request('/repos/example/product')
        except repositories.RepositoryError as error:
            self.assertEqual(error.code, 'github_unavailable')
        else:
            self.api.request.return_value = nested
            self.refuse('repository_mismatch')
        for error in (http.client.IncompleteRead(b'{"id":'), OSError('unreachable')):
            api.opener.open.side_effect = error
            with self.assertRaises(repositories.RepositoryError) as raised:
                api.request('/repos/example/product')
            self.assertEqual(raised.exception.code, 'github_unavailable')
        missing = urllib.error.HTTPError('https://api.github.com', 404, 'missing', Message(), None)
        self.addCleanup(missing.close)
        api.opener.open.side_effect = missing
        self.assertIsNone(api.request('/repos/example/product'))
        with self.assertRaises(repositories.RepositoryError):
            api.request('/user/repos', {'name': 'product'})

    def test_creation_identity_guard_sensitivity(self):
        self.new()
        self.item['repository_onboarding'] = {'status': 'pending', 'mode': 'new', 'marker': 'marker-123',
                                             'api_base': self.api.base, 'created_repository_id': 123}
        self.persist()
        self.api.request.side_effect = [dict(self.metadata, id=999), {'login': 'example'}]
        self.refuse('repository_mismatch')
        # The exact ID check is inline: run an isolated code object with only that guard removed.
        import inspect
        source = inspect.getsource(repositories.reconcile)
        source = source.replace("if previous and 'created_repository_id' in previous and metadata['id'] != previous['created_repository_id']:", 'if False:')
        namespace = dict(vars(repositories))
        exec(compile(source, '<identity-guard-probe>', 'exec'), namespace)
        self.api.request.side_effect = [dict(self.metadata, id=999), {'login': 'example'}]
        with closing(sqlite3.connect(self.state)) as db, db:
            broken = namespace['reconcile'](db, self.stored(), self.api)
        self.assertEqual(broken['repository_onboarding']['metadata']['id'], 999)
