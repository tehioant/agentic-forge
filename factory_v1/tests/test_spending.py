"""Exact-scope spending policy components; SQLite is real, all upstream I/O mocked."""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import spending
from factory_v1.model_access import ModelAccessError, SUBSCRIPTION_SCOPE
from factory_v1.tests import test_spending_cancel


class ModelSpendingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state.sqlite'
        self.db = sqlite3.connect(self.state)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT)')
        item = {'project_id': 'product', 'iteration_id': 'm1', 'status': 'active', 'execution_allowed': False,
                'approval': {'operator_id': '42', 'reference': 'test-approval'}}
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(item)))
        self.db.commit()
        spending.initialize(self.db)
        self.scope = ('product', 'm1', 'fixture-provider', 'fixture-model', 'generate')
        self.adapter = Mock(reservation=3, payload_fields=None)
        self.adapter.call.return_value = (2, 'fixture-answer')
        self.expires = 2**40

    def grant(self, ceiling=5, expires=None, kind='allowance', provider=None, model=None, operation=None, reference='test-grant'):
        scope = (*self.scope[:2], provider or self.scope[2], model or self.scope[3], operation or self.scope[4])
        try:
            with self.db:
                return spending.add_grant(self.db, *scope, kind, ceiling, self.expires if expires is None else expires, reference)
        except spending.SpendingError as error:
            spending.record_refusal(self.db, scope, None, error)
            raise

    def call(self, key='request-1', reserve=None, payload=None, **kwargs):
        return spending.execute_controlled(self.db, self.scope, key,
            {'prompt': 'transient no-secret fixture input'} if payload is None else payload,
            self.adapter.reservation if reserve is None else reserve, adapter=self.adapter, **kwargs)

    def receipt(self, key='request-1', outcome='failed', units=None, scope=None, reference='trusted-no-charge-receipt'):
        project, iteration, provider, model, operation = scope or self.scope
        with self.db:
            return spending.reconcile(self.db, project, iteration, provider, model, operation, key, outcome, units, reference)

    def refused(self, code, function, *args, **kwargs):
        with self.assertRaises(spending.SpendingError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def item(self):
        return json.loads(self.db.execute('SELECT payload FROM iterations').fetchone()[0])

    def subscription(self, kind='allowance'):
        self.scope = ('product', 'm1', *SUBSCRIPTION_SCOPE)
        self.adapter = Mock(reservation=1, payload_fields={'input'})
        self.adapter.call.return_value = (1, {'label': 'fixture-subscription-answer'})
        self.grant(ceiling=2, kind=kind)

    def grant_case(self, label):
        from contextlib import contextmanager
        @contextmanager
        def isolated():
            original_db, original_state, original_adapter, original_scope = self.db, self.state, self.adapter, self.scope
            self.state = self.root / (label + '.sqlite')
            self.db = sqlite3.connect(self.state); original_db.backup(self.db)
            self.adapter = Mock(reservation=3, payload_fields=None)
            self.adapter.call.return_value = (2, 'fixture-answer')
            try:
                yield
            finally:
                self.db.close()
                self.db, self.state, self.adapter, self.scope = original_db, original_state, original_adapter, original_scope
        return isolated()

    def test_grant_reservation_and_success_receipt(self):
        for kind, ceiling in (('allowance', 5), ('approval', 3)):
            with self.subTest(kind=kind), self.grant_case(kind):
                first = self.grant(kind=kind, ceiling=ceiling)
                if kind == 'allowance':
                    self.assertTrue(self.grant()['replay'])
                    self.assertEqual(self.grant()['grant_id'], first['grant_id'])
                self.assertEqual(self.call(), {'actual_units': 2, 'result': 'fixture-answer', 'status': 'complete'})
                self.adapter.call.assert_called_once()
                if kind == 'allowance':
                    self.assertTrue(self.call()['replay'])
                    self.refused('idempotency_conflict', self.call, payload={'prompt': 'different request'})
                    self.assertEqual(self.db.execute('select remaining from model_grants').fetchone()[0], 3)
                    self.assertEqual(self.db.execute('select status,actual_units from model_operations').fetchone(), ('complete', 2))
                    self.assertNotIn(b'transient no-secret fixture input', self.state.read_bytes())
                    self.assertEqual(self.db.execute('select decision from model_decisions order by rowid').fetchall(),
                                     [('grant_recorded',), ('admitted',), ('reconciled_complete',), ('refused',)])
                else:
                    self.refused('spending_blocked', self.call, 'next-operation')
                    self.assertEqual(self.item()['model_access']['status'], 'blocked')
                    self.assertFalse(self.item()['execution_allowed'])
                self.adapter.call.assert_called_once()


    def test_grant_authorization_no_paid_fallback(self):
        for kind in ('absent', 'expired', 'wrong-scope', 'paid-subscription'):
            with self.subTest(kind=kind), self.grant_case(kind):
                if kind == 'expired':
                    self.refused('invalid_grant', self.grant, expires=1)
                    self.grant()
                    with self.db:
                        self.db.execute('update model_grants set expires=1')
                elif kind == 'wrong-scope':
                    for provider, model, operation in (('other', self.scope[3], self.scope[4]),
                                                       (self.scope[2], 'other', self.scope[4]),
                                                       (self.scope[2], self.scope[3], 'other')):
                        self.grant(provider=provider, model=model, operation=operation,
                                   reference='mismatch-' + provider + '-' + model + '-' + operation)
                if kind == 'paid-subscription':
                    self.subscription(kind='approval')
                self.refused('spending_blocked', self.call, 'expired-op' if kind == 'expired' else 'request-1',
                             payload={'input': 'fixture'} if kind == 'paid-subscription' else None)
                self.assertEqual(self.item()['model_access']['status'], 'blocked')
                if kind == 'absent':
                    self.assertEqual(self.db.execute('select decision,reason from model_decisions').fetchone(),
                                     ('refused', 'authorization_unavailable_or_exhausted'))
                self.adapter.call.assert_not_called()



    def test_billing_uncertainty_reconciliation(self):
        for outcome, ceiling, lost in (('failed', 5, OSError('labeled lost response')),
                                        ('complete', 20, ModelAccessError('outcome_uncertain'))):
            with self.subTest(outcome=outcome), self.grant_case(outcome):
                self.grant(ceiling=ceiling)
                key = 'uncertain-op' if outcome == 'failed' else 'request-1'
                self.adapter.call.side_effect = lost
                self.refused('outcome_uncertain', self.call, key)
                self.adapter.call.side_effect = None
                self.refused('outcome_uncertain', self.call, key if outcome == 'failed' else 'new-key')
                self.adapter.call.assert_called_once()
                if outcome == 'failed':
                    self.refused('scope_mismatch', self.receipt, key, scope=(*self.scope[:2], 'other', *self.scope[3:]))
                    self.assertEqual(self.receipt(key)['status'], 'failed')
                    self.assertEqual(self.call(key)['status'], 'failed')
                    self.assertEqual(self.db.execute('SELECT remaining FROM model_grants').fetchone()[0], 5)
                    self.adapter.call.assert_called_once()
                else:
                    self.refused('invalid_receipt', self.receipt, outcome='complete', units=4)
                    self.assertEqual(self.receipt(outcome='complete', units=2)['status'], 'complete')
                    self.assertTrue(self.receipt(outcome='complete', units=2)['replay'])
                    self.refused('reconciliation_conflict', self.receipt, outcome='complete', units=1)
                    self.assertEqual(self.call('new-key')['status'], 'complete')
                    self.assertEqual(self.adapter.call.call_count, 2)


    def test_invalid_scope_json_and_provider_route(self):
        self.grant()
        self.refused('invalid_scope', self.grant, provider='bad/value')
        for ceiling in [-1, 2**63]:
            self.refused('invalid_grant', self.grant, ceiling=ceiling, reference='invalid-' + str(ceiling))
        for payload in [{'prompt': float('nan')}, {'prompt': 'x' * 33000}]:
            self.refused('invalid_request', self.call, 'invalid', payload=payload)
        with patch('factory_v1.model_access.socket.socket') as socket:
            self.refused('provider_unavailable', spending.execute_controlled, self.db, self.scope,
                         'invalid-route', {'prompt': 'x'}, 3, 'https://provider.example/v1')
            socket.assert_not_called()
        self.adapter.call.assert_not_called()





    def test_subscription_scope_request_allowance_and_privacy(self):
        self.subscription()
        payload = {'input': 'labeled fixture request'}
        self.assertEqual(self.call(reserve=1, payload=payload)['status'], 'complete')
        self.assertTrue(self.call(reserve=1, payload=payload)['replay'])
        for key in ['model', 'api_key', 'service_tier', 'url', 'unknown']:
            self.refused('invalid_request', self.call, 'deny-' + key, reserve=1, payload={**payload, key: 'must-not-forward'})
        self.adapter.call.assert_called_once_with(payload)
        self.assertEqual(self.db.execute('SELECT remaining FROM model_grants').fetchone()[0], 1)
        self.assertNotIn(b'labeled fixture request', self.state.read_bytes())
        self.assertNotIn(b'fixture-subscription-answer', self.state.read_bytes())


    def test_quota_refusal_requires_billing_receipt_then_explicit_resume(self):
        self.subscription()
        self.adapter.call.side_effect = ModelAccessError('quota_exhausted')
        self.refused('quota_exhausted', self.call, payload={'input': 'fixture'})
        self.adapter.call.side_effect = None
        self.refused('outcome_uncertain', self.call, 'another-key', payload={'input': 'fixture'})
        self.refused('outcome_uncertain', self.resume)
        self.receipt()
        self.refused('quota_exhausted', self.call, 'another-key', payload={'input': 'fixture'})
        self.assertEqual(self.resume(), {'status': 'ready'})
        self.assertEqual(self.call('another-key', payload={'input': 'fixture'})['status'], 'complete')
        self.assertEqual(self.adapter.call.call_count, 2)

    def resume(self):
        with self.db:
            return spending.resume_quota(self.db, self.scope, 'verified-quota-restored')

    def test_broker_capability_input_bounds(self):
        for boundary in ('peer-cap', 'request-size'):
            with self.subTest(boundary=boundary), self.grant_case(boundary):
                self.subscription()
                if boundary == 'request-size':
                    payload = {'input': 'x' * 50000}
                    self.refused('invalid_request', self.call, payload=payload)
                    self.assertEqual(self.call(payload=payload, max_request=spending.SANDBOX_MAX_REQUEST)['status'], 'complete')
                    self.refused('invalid_request', self.call, 'oversize', payload={'input': 'x' * spending.SANDBOX_MAX_REQUEST}, max_request=spending.SANDBOX_MAX_REQUEST)
                    self.adapter.call.assert_called_once_with(payload)
                else:
                    def cap(component, mocks):
                        sends = []
                        def reply(raw):
                            sends.append(json.loads(raw))
                            if len(sends) == 2: component.stop.set()
                        component.connection.sendall.side_effect = reply
                    component = test_spending_cancel.broker_component(self.state, self.scope, self.adapter, run=cap)
                    replies = [json.loads(call.args[0]) for call in component.connection.sendall.call_args_list]
                    self.assertEqual(replies[0]['result']['status'], 'complete')
                    self.assertEqual(replies[1]['error'], 'capability_exhausted')
                    self.adapter.call.assert_called_once()
                    self.assertFalse(component.path.exists())
                    denied = test_spending_cancel.broker_component(self.state, self.scope, self.adapter, peer_uid=os.getuid() + 1)
                    self.assertEqual(json.loads(denied.connection.sendall.call_args.args[0])['error'], 'worker_identity_mismatch')
                    self.adapter.call.assert_called_once()


    def test_pre_admission_and_disabled_route_refusals_survive_reopen_without_secrets(self):
        self.grant()
        marker = 'never-persist-request-secret'
        self.refused('invalid_request', self.call, 'override', payload={'provider': marker, 'prompt': marker})
        self.refused('invalid_reservation', self.call, 'under-reserve', reserve=1)
        disabled = ('product', 'm1', 'paid-provider', 'paid-model', 'generate')
        with patch.object(spending.socket, 'socket') as socket:
            self.refused('provider_unavailable', spending._serve_unix_socket,
                str(self.root / 'capability.sock'), str(self.state), disabled, 'http://127.0.0.1:1/fixed',
                1, '42', None, 3, 10, 1, os.getuid(), spending.MAX_REQUEST, None, None, None)
            socket.assert_not_called()
        with closing(sqlite3.connect(self.state)) as fresh:
            self.assertEqual(fresh.execute("SELECT provider,operation_id,reason FROM model_decisions WHERE decision='refused' ORDER BY rowid").fetchall(),
                [('fixture-provider', 'override', 'invalid_request'), ('fixture-provider', 'under-reserve', 'invalid_reservation'),
                 ('paid-provider', None, 'provider_unavailable')])
            self.assertEqual(fresh.execute('SELECT COUNT(*) FROM model_operations').fetchone()[0], 0)
            self.assertEqual(fresh.execute('SELECT remaining FROM model_grants').fetchone()[0], 5)
        self.assertNotIn(marker.encode(), self.state.read_bytes())
        self.adapter.call.assert_not_called()

    def test_controller_and_startup_refusals_have_exact_durable_order(self):
        self.grant()
        marker = 'never-persist-rejected-grant-secret'
        self.refused('grant_conflict', self.grant, ceiling=6)
        self.refused('invalid_grant', self.grant, ceiling=0, reference=marker)
        path = self.root / 'capability.sock'
        with patch.object(spending.socket, 'socket') as socket, \
                patch.object(spending.threading, 'Thread') as thread, \
                patch.object(spending, 'transport') as transport:
            self.refused('approval_required', spending.serve_unix_socket, str(path), str(self.state),
                         self.scope, operator_id='other')
            item = self.item()
            item['status'] = 'paused'
            with self.db:
                self.db.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
            self.refused('iteration_paused', spending.serve_unix_socket, str(path), str(self.state),
                         self.scope, operator_id='42')
            socket.assert_not_called()
            thread.assert_not_called()
            transport.assert_not_called()
        with closing(sqlite3.connect(self.state)) as fresh:
            self.assertEqual(fresh.execute('SELECT decision,reason FROM model_decisions ORDER BY rowid').fetchall(),
                             [('grant_recorded', 'allowance'), ('refused', 'grant_conflict'),
                              ('refused', 'invalid_grant'), ('refused', 'approval_required'),
                              ('refused', 'iteration_paused')])
            self.assertEqual(fresh.execute('SELECT remaining FROM model_grants').fetchone()[0], 5)
            self.assertEqual(fresh.execute('SELECT COUNT(*) FROM model_operations').fetchone()[0], 0)
        self.assertFalse(path.exists())
        self.assertNotIn(marker.encode(), self.state.read_bytes())
        self.adapter.call.assert_not_called()


    def test_deadline_and_grant_expiry_after_reservation_are_known_no_effect(self):
        self.grant()
        prepare = spending._prepare
        def expired(*args, **kwargs):
            result = prepare(*args, **kwargs)
            result['grant_expires'] = 0
            return result
        with patch.object(spending, '_prepare', side_effect=expired):
            self.refused('capability_exhausted', self.call)
        self.adapter.call.assert_not_called()
        self.assertEqual(self.db.execute('SELECT status,actual_units FROM model_operations').fetchall(), [('failed', 0)])
        self.assertEqual(self.db.execute('SELECT remaining FROM model_grants').fetchone()[0], 5)



if __name__ == '__main__':
    unittest.main()
