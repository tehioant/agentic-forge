"""Attention validators and durable state; transport execution is mocked."""
import copy
import json
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest import mock

from factory_v1 import attention, messaging, repositories
from factory_v1.errors import IntakeError


class AttentionComponentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / 'state.sqlite'
        self.origin = {'platform': 'discord', 'chat_id': '123', 'thread_id': '123',
                       'parent_chat_id': '456', 'scope_id': '789'}
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
                     'approval': {'operator_id': '42', 'reference': 'approved'}, 'origin': self.origin,
                     'execution_allowed': False, 'stage': 'intake_registered',
                     'correlation': {'project_id': 'product', 'iteration_id': 'm1', 'stage': 'intake_registered',
                                     'control_run_id': 'control-1', 'evidence_id': 'intake-1', 'worker_run_id': None}}
        self.event = {'event_id': 'incident-1', 'kind': 'incident', 'message': 'Unhealthy delivery',
                      'issue_number': 15, 'run_id': 'run-15', 'evidence_ids': ['evidence-15']}
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
            db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(self.item)))
            db.execute('CREATE TABLE notifications (project TEXT, iteration TEXT, event_id TEXT, payload TEXT, PRIMARY KEY(project,iteration,event_id))')
        self.config = self.root / 'transport.json'
        self.config.write_text(json.dumps({'command': ['/mock/hermes'], 'approved_destinations': [self.origin]}))
        self.send = mock.patch.object(messaging.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{"success":true}', ''))
        self.runner = self.send.start()
        self.addCleanup(self.send.stop)
        for target in ('socket.socket', 'subprocess.Popen'):
            patcher = mock.patch(target, side_effect=AssertionError('external creation denied'))
            patcher.start()
            self.addCleanup(patcher.stop)

    def operation(self, action, request=None, event_id=None, config_path=None):
        with closing(sqlite3.connect(self.state)) as db:
            return attention.lifecycle(db, 'product', 'm1', '42', action, request,
                                       event_id or self.event['event_id'], config_path or self.config)

    def enqueue(self, event=None):
        return self.operation('attention', self.event if event is None else event)

    def deliver(self):
        return self.operation('deliver')

    def records(self):
        with closing(sqlite3.connect(self.state)) as db:
            return attention.records(db, 'product', 'm1', attention.load_iteration(db, 'product', 'm1'))

    def rows(self, table):
        with closing(sqlite3.connect(self.state)) as db:
            return db.execute('SELECT * FROM ' + table).fetchall()

    def replace(self, table, raw):
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('UPDATE ' + table + ' SET payload=?', (raw,))

    def receipt(self, outcome='delivered'):
        record = self.records()[0]
        receipt = {'event_id': record['event']['event_id'], 'attempt': record['attempts'],
                   'delivery_key': ['product', 'm1', record['event']['event_id']],
                   'destination': self.origin, 'content': {'event': record['event'], 'correlation': record['correlation']},
                   'outcome': outcome, 'reference': 'trusted fixture observation'}
        if outcome == 'delivered':
            receipt['message_id'] = '1001'
        return receipt

    def response(self):
        return {'event_id': self.event['event_id'], 'decision_id': 'direction-15', 'operator_id': '42',
                'origin': self.origin, 'reference': 'verified operator message', 'response': 'Investigate outage'}

    def test_enqueue_exact_correlations_reopen_and_routine_silence(self):
        before = self.rows('iterations')
        self.assertEqual(self.records(), [])
        with self.assertRaises(IntakeError):
            self.enqueue(dict(self.event, kind='stage_success'))
        record = self.enqueue()
        self.assertEqual(record['destination'], self.origin)
        self.assertEqual(record['correlation']['work_item'], {'repository': 'example/product', 'issue_number': 15})
        self.assertEqual(record['correlation']['worker_run_id'], 'run-15')
        self.assertEqual(record['correlation']['evidence_ids'], ['evidence-15'])
        self.assertEqual(self.records(), [record])
        self.assertEqual(self.rows('iterations'), before)
        self.runner.assert_not_called()

    def test_idempotence_event_conflict_and_unique_decision_identity(self):
        first = self.enqueue()
        self.assertEqual(self.enqueue(), first)
        with self.assertRaises(IntakeError):
            self.enqueue(dict(self.event, message='different'))
        self.enqueue(dict(self.event, event_id='incident-2'))
        decision = dict(self.event, event_id='decision-1', kind='decision', decision_id='direction-15')
        self.enqueue(decision)
        with self.assertRaises(IntakeError):
            self.enqueue(dict(decision, event_id='duplicate-decision'))
        self.enqueue(dict(decision, event_id='decision-2', decision_id='direction-16'))
        self.assertEqual(len(self.records()), 4)

    def test_event_negative_matrix_preserves_empty_outbox(self):
        for changes in ({'issue_number': True}, {'run_id': ' run'}, {'evidence_ids': []}, {'kind': []},
                        {'evidence_ids': ['same', 'same']}, {'destination': {}}, {'message': 'x' * 16001}):
            with self.subTest(changes=changes), self.assertRaises(IntakeError) as raised:
                self.enqueue(dict(self.event, **changes))
            self.assertEqual(raised.exception.code, 'invalid_attention')
        self.assertEqual(self.records(), [])
        self.runner.assert_not_called()

    def test_invalid_persisted_repository_refuses_existing_and_new_event_then_recovers(self):
        original = self.enqueue()
        before = self.rows('notifications')
        values = [None, 42, True, [], {}, '', ' ', 'example', 'example/repo/other',
                  'https://github.com/example/repo', ' example/repo', 'example/repo ', 'example/repo\n',
                  '-owner/repo', 'owner-/repo', 'a--b/repo', 'owner_name/repo', 'owner/.', 'owner/..',
                  'owner/', '/repo', 'a' * 40 + '/repo', 'owner/' + 'r' * 101]
        cases = [{k: v for k, v in self.item.items() if k != 'repository'}]
        cases += [dict(self.item, repository=v) for v in values]
        for item in cases:
            self.replace('iterations', json.dumps(item))
            for event in (self.event, dict(self.event, event_id='new')):
                with self.assertRaises(IntakeError) as raised:
                    self.enqueue(event)
                self.assertEqual(raised.exception.code, 'invalid_context')
                self.assertEqual(str(raised.exception), 'Recorded repository must be an exact GitHub owner/repository identifier.')
            with self.assertRaises(IntakeError) as raised:
                self.operation('deliver', event_id='new')
            self.assertEqual(raised.exception.code, 'not_found')
            self.assertEqual(self.rows('notifications'), before)
            self.assertEqual(json.loads(self.rows('iterations')[0][-1]), item)
        self.replace('iterations', json.dumps(self.item))
        self.assertEqual(self.records(), [original])
        for index, repository in enumerate(('A/.repo_name-1', 'a-b/repo.', 'a' * 39 + '/' + 'r' * 100)):
            self.assertTrue(repositories.valid_repository(repository))
            self.replace('iterations', json.dumps(dict(self.item, repository=repository)))
            record = self.enqueue(dict(self.event, event_id=f'valid-{index}'))
            self.assertEqual(record['correlation']['work_item']['repository'], repository)
            self.assertEqual(record['attempts'], 0)
        self.runner.assert_not_called()

    def test_origin_approval_and_correlation_contracts_fail_closed(self):
        from factory_v1 import __main__ as intake
        origins = [None, [], dict(self.origin, platform='Discord'), dict(self.origin, extra='123')]
        origins += [{k: v for k, v in self.origin.items() if k != missing} for missing in self.origin]
        origins += [dict(self.origin, **{k: v}) for k in ('chat_id', 'thread_id', 'parent_chat_id', 'scope_id')
                    for v in ('', '0', '0123', ' 123', '123\n', '-123', 123, True, None, [])]
        origins.append(dict(self.origin, thread_id='999'))
        request = {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
                   'idea': 'Approved idea', 'approval': self.item['approval'], 'origin': self.origin}
        for origin in origins:
            with self.assertRaises(IntakeError) as raised:
                intake.validate_intake(dict(request, origin=origin), '42')
            self.assertEqual(raised.exception.code, 'invalid_intake')
            self.replace('iterations', json.dumps(dict(self.item, origin=origin)))
            with self.assertRaises(IntakeError) as raised:
                self.enqueue()
            self.assertEqual(raised.exception.code, 'invalid_origin')
        for changes in ({'project_id': 'other'}, {'approval': []},
                        {'approval': {'operator_id': '99', 'reference': 'x'}},
                        {'approval': {'operator_id': '42', 'reference': ''}},
                        {'correlation': dict(self.item['correlation'], iteration_id='other')}):
            self.replace('iterations', json.dumps(dict(self.item, **changes)))
            with self.assertRaises(IntakeError):
                self.enqueue()
        self.assertEqual(self.rows('notifications'), [])
        self.runner.assert_not_called()
        self.replace('iterations', json.dumps(self.item))
        self.assertEqual(self.enqueue()['destination'], self.origin)

    def test_transport_exact_command_privacy_ack_is_not_receipt(self):
        self.event['message'] = '@everyone <@42> <@&99> MEDIA:/secret [[as_document]]'
        self.enqueue()
        accepted = self.deliver()
        self.assertEqual(accepted['state'], 'accepted')
        self.assertIsNone(accepted['receipt'])
        args, kwargs = self.runner.call_args
        self.assertEqual(args[0], ['/mock/hermes', 'send', '--to', 'discord:123:123', '--file', '-', '--json'])
        self.assertNotIn('@', kwargs['input'])
        self.assertNotIn('MEDIA:', kwargs['input'])
        self.assertNotIn('[[as_document]]', kwargs['input'])
        self.assertEqual(json.loads(kwargs['input'])['event'], self.event)
        self.assertEqual(self.deliver(), accepted)
        receipt = self.receipt()
        delivered = self.operation('attention-reconcile', receipt)
        self.assertEqual(delivered['state'], 'delivered')
        self.assertEqual(self.operation('attention-reconcile', receipt), delivered)
        self.assertEqual(self.deliver(), delivered)
        self.runner.assert_called_once()

    def test_transport_known_no_effect_unknown_and_malformed_are_distinct(self):
        record = self.enqueue()
        transport = messaging.HermesTransport(self.config, self.origin)
        for error in (OSError('missing executable'),):
            self.runner.side_effect = error
            self.assertEqual(transport.send(record), 'not_sent')
        self.runner.side_effect = subprocess.TimeoutExpired('mock', 1)
        self.assertEqual(transport.send(record), 'uncertain')
        self.runner.side_effect = None
        for code, raw, expected in ((2, '', 'not_sent'), (1, 'secret-do-not-retain', 'uncertain'),
                                   (0, 'not JSON secret-do-not-retain', 'uncertain'), (0, '{"success":true,"skipped":true}', 'uncertain'),
                                   (0, '{"success":1}', 'uncertain'), (0, '{"success":true,"success":false}', 'uncertain'),
                                   (0, 'x' * 1048577, 'uncertain'), (0, '{"success":true}', 'accepted')):
            self.runner.return_value = subprocess.CompletedProcess([], code, raw, 'secret-do-not-retain')
            self.assertEqual(transport.send(record), expected)
        self.assertNotIn('secret-do-not-retain', json.dumps(self.records()))

    def test_no_effect_retry_new_attempt_and_uncertain_effect_no_blind_retry(self):
        self.enqueue()
        self.runner.side_effect = OSError('not executed')
        with self.assertRaises(IntakeError) as raised:
            self.deliver()
        self.assertEqual(raised.exception.code, 'transport_not_sent')
        self.assertEqual(self.records()[0]['state'], 'pending')
        self.runner.side_effect = None
        self.runner.return_value = subprocess.CompletedProcess([], 1, '', 'secret')
        with self.assertRaises(IntakeError):
            self.deliver()
        uncertain = self.records()[0]
        self.assertEqual(uncertain['state'], 'uncertain')
        self.assertEqual(self.deliver(), uncertain)
        self.assertEqual(self.runner.call_count, 2)
        receipt = self.receipt('not_sent')
        self.operation('attention-reconcile', receipt)
        self.runner.return_value = subprocess.CompletedProcess([], 0, '{"success":true}', '')
        accepted = self.deliver()
        self.assertEqual(accepted['attempts'], 3)
        self.assertEqual(accepted['reconciliation'], receipt)
        self.assertIsNone(accepted['receipt'])
        with self.assertRaises(IntakeError):
            self.operation('attention-reconcile', receipt)
        self.operation('attention-reconcile', self.receipt())
        self.deliver()
        self.assertEqual(self.runner.call_count, 3)

    def test_interruption_at_send_return_preserves_uncertain_intent_before_and_after_effect(self):
        self.enqueue()
        effects = []
        for effect in (False, True):
            def interrupted(*args, **kwargs):
                retained = self.records()[0]
                self.assertEqual(retained['state'], 'uncertain')
                self.assertGreater(retained['attempts'], 0)
                if effect:
                    effects.append(retained['event']['event_id'])
                raise SystemExit('simulated effect-return interruption')
            self.runner.side_effect = interrupted
            with self.assertRaises(SystemExit):
                self.deliver()
            count = self.runner.call_count
            self.assertEqual(self.deliver()['state'], 'uncertain')
            self.assertEqual(self.runner.call_count, count)
            self.operation('attention-reconcile', self.receipt('delivered' if effect else 'not_sent'))
        self.assertEqual(self.records()[0]['state'], 'delivered')
        self.assertEqual(effects, [self.event['event_id']])

    def test_receipt_and_response_negative_matrices_do_not_authorize_execution(self):
        self.event = dict(self.event, kind='decision', decision_id='direction-15')
        self.enqueue()
        response = self.response()
        for changed in ({}, dict(response, operator_id='99'), dict(response, decision_id='other'),
                        dict(response, response=''), dict(response, acknowledgement=True),
                        dict(response, origin=dict(self.origin, parent_chat_id='999'))):
            with self.assertRaises(IntakeError):
                self.operation('respond', changed)
        self.deliver()
        self.assertIsNone(self.records()[0]['decision_response'])
        receipt = self.receipt()
        for changed in (dict(receipt, delivery_key=['other']), dict(receipt, attempt=True),
                        dict(receipt, attempt=2), dict(receipt, message_id=' 1001'),
                        dict(receipt, destination=dict(self.origin, scope_id='999')),
                        dict(receipt, content={}), dict(receipt, reference=''), dict(receipt, extra=True)):
            with self.assertRaises(IntakeError):
                self.operation('attention-reconcile', changed)
        self.operation('attention-reconcile', receipt)
        with self.assertRaises(IntakeError):
            self.operation('attention-reconcile', dict(receipt, message_id='1002'))
        first = self.operation('respond', response)
        self.assertEqual(self.operation('respond', response), first)
        with self.assertRaises(IntakeError):
            self.operation('respond', dict(response, response='Other'))
        completion = {k: v for k, v in dict(self.event, event_id='completion-1', kind='completion').items() if k != 'decision_id'}
        self.enqueue(completion)
        with self.assertRaises(IntakeError):
            self.operation('respond', dict(response, event_id='completion-1'))
        self.assertEqual(json.loads(self.rows('iterations')[0][-1]), self.item)
        self.assertFalse(self.item['execution_allowed'])
        self.assertEqual(len(self.rows('iterations')), 1)
        self.replace('iterations', json.dumps(dict(self.item, origin=dict(self.origin, scope_id='999'))))
        with self.assertRaises(IntakeError):
            self.deliver()
        self.runner.assert_called_once()

    def test_unapproved_or_absent_transport_never_spends_attempt(self):
        self.enqueue()
        for config in (self.root / 'absent', self.config):
            self.config.write_text(json.dumps({'command': ['/mock/hermes'], 'approved_destinations': [dict(self.origin, scope_id='999')]}))
            with self.assertRaises(IntakeError) as raised:
                self.operation('deliver', config_path=config)
            self.assertEqual(raised.exception.code, 'capability_required')
            self.assertEqual(self.records()[0]['attempts'], 0)
        self.runner.assert_not_called()

    def test_corrupt_durable_notifications_fail_all_real_lifecycle_seams(self):
        self.event = dict(self.event, kind='decision', decision_id='direction-15')
        original = self.enqueue()
        receipt = self.receipt()
        response = self.response()
        malformed = ['[', '[]', 'null', '"secret-diagnostic"', '[' * 1200 + '0' + ']' * 1199,
                     json.dumps(original)[:-1] + ',"state":"accepted"}']
        malformed += [json.dumps({k: v for k, v in original.items() if k != missing}) for missing in original]
        malformed += [json.dumps(dict(original, **changes)) for changes in
                      ({'event': []}, {'event': {}}, {'destination': []}, {'state': []}, {'state': 'unknown'},
                       {'attempts': True}, {'attempts': -1}, {'attempts': '1'}, {'project_id': 'other'},
                       {'iteration_id': 'other'}, {'correlation': []}, {'correlation': {}}, {'receipt': []},
                       {'decision_response': []}, {'last_error': []}, {'reconciliation': []},
                       {'correlation': dict(original['correlation'], worker_run_id='other')})]
        delivered_receipt = dict(receipt, attempt=1)
        valid_delivered = dict(original, state='delivered', attempts=1, receipt=delivered_receipt, reconciliation=delivered_receipt)
        malformed += [json.dumps(dict(valid_delivered, **changes)) for changes in
                      ({'receipt': None}, {'reconciliation': None}, {'state': 'accepted'}, {'attempts': 2},
                       {'last_error': 'transport_uncertain'})]
        for key, value in (('attempt', True), ('attempt', 0), ('event_id', 'other'), ('delivery_key', ['other']),
                           ('destination', {}), ('content', {}), ('message_id', ' 1001'), ('reference', ''), ('extra', True)):
            changed = dict(delivered_receipt, **{key: value})
            malformed.append(json.dumps(dict(valid_delivered, receipt=changed, reconciliation=changed)))
        for changed in ({}, dict(response, operator_id='99'), dict(response, decision_id='other'),
                        dict(response, origin={}), dict(response, response=''), dict(response, acknowledgement=True)):
            malformed.append(json.dumps(dict(original, decision_response=changed)))
        for raw in malformed:
            self.replace('notifications', raw)
            before = self.rows('notifications')
            for operation in (self.records, self.deliver, self.enqueue,
                              lambda: self.operation('respond', response), lambda: self.operation('attention-reconcile', receipt)):
                with self.assertRaises(IntakeError) as raised:
                    operation()
                self.assertEqual(raised.exception.code, 'state_error')
                self.assertEqual(self.rows('notifications'), before)
            self.assertEqual(json.loads(self.rows('iterations')[0][-1]), self.item)
        self.runner.assert_not_called()
        self.replace('notifications', json.dumps(original))
        self.assertEqual(self.records(), [original])
        self.assertEqual(self.deliver()['state'], 'accepted')

    def test_corrupt_iteration_json_is_rejected_before_send_or_outbox_mutation(self):
        self.enqueue()
        before = self.rows('notifications')
        original = json.dumps(self.item)
        malformed = ['[', '[' * 10000 + '0' + ']' * 9999,
                     original[:-1] + ',"origin":null,"origin":' + json.dumps(self.origin) + '}',
                     original.replace('"operator_id": "42"', '"operator_id":"99","operator_id":"42"'),
                     original.replace('"control_run_id":', '"control_run_id":"other","control_run_id":')]
        for raw in malformed:
            self.replace('iterations', raw)
            for operation in (self.enqueue, self.deliver, self.records):
                with self.assertRaises(IntakeError) as raised:
                    operation()
                self.assertEqual(raised.exception.code, 'invalid_context')
            self.assertEqual(self.rows('notifications'), before)
            self.assertEqual(self.rows('iterations')[0][-1], raw)
        self.runner.assert_not_called()

    def test_concurrent_enqueue_send_and_response_share_one_durable_send(self):
        self.event = dict(self.event, kind='decision', decision_id='direction-15')
        self.enqueue()
        operations = [self.enqueue, self.deliver, lambda: self.operation('respond', self.response())] * 3
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(operation) for operation in operations]
            for future in futures:
                future.result(timeout=3)
        record = self.records()[0]
        self.assertEqual(record['attempts'], 1)
        self.assertEqual(record['decision_response'], self.response())
        self.runner.assert_called_once()


    def test_semantic_receipt_and_decision_consistency_without_unrelated_guards(self):
        self.event = dict(self.event, kind='decision', decision_id='direction-15')
        original = self.enqueue()
        receipt = dict(self.receipt(), attempt=1)
        delivered = dict(original, state='delivered', attempts=1, receipt=receipt, reconciliation=receipt)
        self.assertEqual(attention.load_record(json.dumps(delivered), 'product', 'm1', self.event['event_id'], self.item), delivered)
        not_sent = {k: v for k, v in dict(receipt, outcome='not_sent').items() if k != 'message_id'}
        malformed = [dict(original, receipt=receipt), dict(original, state='delivered', attempts=1, receipt=None),
                     dict(original, state='delivered', attempts=1, receipt={}), dict(delivered, reconciliation={}),
                     dict(delivered, reconciliation=None), dict(delivered, receipt=None),
                     dict(delivered, state='accepted'), dict(delivered, attempts=2), dict(delivered, last_error='transport_uncertain'),
                     dict(original, reconciliation=not_sent), dict(original, state='accepted', attempts=1, reconciliation=not_sent),
                     dict(original, state='uncertain', attempts=1, reconciliation=not_sent)]
        response = self.response()
        for changed in (dict(response, operator_id=42), dict(response, event_id='other'), dict(response, reference=''),
                        dict(response, origin=dict(self.origin, chat_id=' 123'))):
            malformed.append(dict(original, decision_response=changed))
        incident = {k: v for k, v in dict(self.event, kind='incident').items() if k != 'decision_id'}
        malformed.append(dict(original, event=incident, decision_response=response))
        for record in malformed:
            with self.subTest(record=record), self.assertRaises(IntakeError) as raised:
                attention.load_record(json.dumps(record), 'product', 'm1', self.event['event_id'], self.item)
            self.assertEqual(raised.exception.code, 'state_error')
        valid_pending = dict(original, attempts=1, reconciliation=not_sent)
        self.assertEqual(attention.load_record(json.dumps(valid_pending), 'product', 'm1', self.event['event_id'], self.item), valid_pending)
        self.runner.assert_not_called()

    def test_no_retry_guard_sensitivity(self):
        record = self.enqueue()
        self.deliver()
        count = self.runner.call_count
        self.deliver()
        self.assertEqual(self.runner.call_count, count)
        import inspect
        source = inspect.getsource(attention.deliver).replace("if record['state'] != 'pending':", 'if False:')
        namespace = dict(vars(attention))
        exec(compile(source, '<no-retry-guard-probe>', 'exec'), namespace)
        with closing(sqlite3.connect(self.state)) as db:
            namespace['deliver'](db, self.item, self.event['event_id'], self.config)
        self.assertEqual(self.runner.call_count, count + 1)

    def test_planning_onboarding_and_grant_writes_preserve_attention_and_remain_silent(self):
        from factory_v1 import planning, spending
        self.item['status'] = 'active'
        self.item['repository_onboarding'] = {'status': 'verified'}
        self.replace('iterations', json.dumps(self.item))
        with closing(sqlite3.connect(self.state)) as db:
            spending.initialize(db)
        request = {'origin': self.origin, 'expected_revision': 0, 'skills': {'fixture': 'selected'}}
        def plan():
            with closing(sqlite3.connect(self.state)) as db, repositories.state_lock(db), db, mock.patch.object(planning, 'resolve', return_value={'fixture': 'selected'}):
                return planning.plan(db, self.item, request)
        def grant():
            with closing(sqlite3.connect(self.state)) as db, repositories.state_lock(db), db:
                return spending.add_grant(db, 'product', 'm1', 'fixture-provider', 'fixture-model',
                                          'generate', 'allowance', 100, 2000, 'fixture allowance', now=1000)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(operation) for operation in (plan, grant, self.enqueue)]
            for future in futures:
                future.result(timeout=3)
        with closing(sqlite3.connect(self.state)) as db:
            item = attention.load_iteration(db, 'product', 'm1')
            self.assertEqual(item['planning']['revision'], 1)
            self.assertEqual(item['repository_onboarding']['status'], 'verified')
            self.assertFalse(item['execution_allowed'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM model_grants').fetchone()[0], 1)
        self.assertEqual(len(self.records()), 1)
        self.runner.assert_not_called()
