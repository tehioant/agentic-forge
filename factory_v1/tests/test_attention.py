"""Mock-only transport: real CLI processes and SQLite, never platform calls."""
import json
import sqlite3
import sys
import time
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor

from factory_v1.tests import test_intake, test_planning


MOCK_HERMES = '''import argparse, json, os, pathlib, signal, sys
root = pathlib.Path(sys.argv[1])
p = argparse.ArgumentParser()
p.add_argument('command', choices=['send'])
p.add_argument('--to', required=True)
p.add_argument('--file', choices=['-'], required=True)
p.add_argument('--json', action='store_true', required=True)
a = p.parse_args(sys.argv[2:])
body = sys.stdin.read()
mode = (root / 'mode').read_text()
with (root / 'calls.jsonl').open('a') as f:
    f.write(json.dumps({'target': a.to, 'body': body, 'argv': sys.argv[2:]}) + '\\n')
if mode == 'usage':
    sys.exit(2)
if mode == 'crash_before':
    os.kill(os.getppid(), signal.SIGKILL)
    sys.exit(1)
if mode not in ['unknown', 'malformed', 'skipped', 'false_success', 'duplicate']:
    with (root / 'effects.jsonl').open('a') as f:
        f.write(json.dumps({'target': a.to, 'body': body}) + '\\n')
if mode == 'crash_after':
    os.kill(os.getppid(), signal.SIGKILL)
    sys.exit(1)
if mode in ['unknown', 'lost_after']:
    print('secret-do-not-retain', file=sys.stderr)
    sys.exit(1)
if mode == 'malformed':
    print('not JSON secret-do-not-retain')
elif mode == 'duplicate':
    print('{"success":true,"success":false}')
elif mode == 'skipped':
    print('{"success":true,"skipped":true}')
elif mode == 'false_success':
    print('{"success":1}')
else:
    print('{"success":true,"note":"mock transport acknowledgement"}')
'''


class AttentionTests(unittest.TestCase):
    invoke = test_intake.IntakeLifecycleTests.invoke
    register = test_intake.IntakeLifecycleTests.register

    def setUp(self):
        test_intake.IntakeLifecycleTests.setUp(self)
        self.assertEqual(self.register().returncode, 0)
        self.event = {'event_id': 'incident-1', 'kind': 'incident', 'message': 'Unhealthy delivery',
                      'issue_number': 15, 'run_id': 'run-15', 'evidence_ids': ['evidence-15']}
        self.script = self.root / 'mock_hermes.py'
        self.script.write_text(MOCK_HERMES)
        self.config = self.root / 'transport.json'
        self.config.write_text(json.dumps({'command': [sys.executable, str(self.script), str(self.root)],
                                           'approved_destinations': [self.request['origin']]}))
        self.mode('success')

    def mode(self, mode):
        (self.root / 'mode').write_text(mode)

    def command(self, name, request=None, *extra):
        options = []
        if request is not None:
            path = self.root / (name + '.json')
            path.write_text(json.dumps(request))
            options = ['--request', str(path)]
        return self.invoke(name, '--project', 'approved-product', '--iteration', 'milestone-1', *options, *extra)

    def enqueue(self, event=None):
        return self.command('attention', self.event if event is None else event)

    def notifications(self):
        result = self.command('notifications')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def deliver(self, config=True):
        return self.command('deliver', None, '--event', self.event['event_id'],
                            *(['--transport-config', str(self.config)] if config else []))

    def calls(self, filename='calls.jsonl'):
        path = self.root / filename
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def receipt(self, outcome='delivered'):
        record = self.notifications()[0]
        receipt = {'event_id': self.event['event_id'], 'attempt': record['attempts'],
                   'delivery_key': ['approved-product', 'milestone-1', self.event['event_id']],
                   'destination': self.request['origin'],
                   'content': {'event': record['event'], 'correlation': record['correlation']},
                   'outcome': outcome, 'reference': 'mock-only trusted fixture observation'}
        if outcome == 'delivered':
            receipt['message_id'] = '1001'
        return receipt

    def response(self):
        return {'event_id': self.event['event_id'], 'decision_id': 'direction-15', 'operator_id': '42',
                'origin': self.request['origin'], 'reference': 'verified-operator-message',
                'response': 'Investigate the outage'}

    def test_intent_correlations_restart_and_silent_routine_operations(self):
        before = self.invoke('inspect', '--project', 'approved-product', '--iteration', 'milestone-1')
        self.assertEqual(self.notifications(), [])
        self.assertEqual(self.register().returncode, 0)
        self.assertEqual(self.enqueue(dict(self.event, kind='stage_success')).returncode, 2)
        self.assertEqual(self.notifications(), [])
        first = self.enqueue()
        self.assertEqual(first.returncode, 0, first.stderr)
        record = json.loads(first.stdout)
        self.assertEqual(record['destination'], self.request['origin'])
        self.assertEqual(record['correlation']['iteration_id'], 'milestone-1')
        self.assertEqual(record['correlation']['work_item']['issue_number'], 15)
        self.assertEqual(record['correlation']['worker_run_id'], 'run-15')
        self.assertEqual(record['correlation']['evidence_ids'], ['evidence-15'])
        self.assertEqual(self.notifications(), [record])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.invoke('inspect', '--project', 'approved-product', '--iteration', 'milestone-1').stdout, before.stdout)

    def test_duplicate_conflict_and_new_event_or_decision(self):
        first = self.enqueue()
        self.assertEqual(self.enqueue().stdout, first.stdout)
        self.assertEqual(self.enqueue(dict(self.event, message='Other')).returncode, 2)
        self.assertEqual(self.enqueue(dict(self.event, event_id='incident-2')).returncode, 0)
        decision = dict(self.event, event_id='decision-1', kind='decision', decision_id='direction-15')
        self.assertEqual(self.enqueue(decision).returncode, 0)
        self.assertEqual(self.enqueue(dict(decision, event_id='decision-duplicate')).returncode, 2)
        self.assertEqual(self.enqueue(dict(decision, event_id='decision-2', decision_id='direction-16')).returncode, 0)
        self.assertEqual(len(self.notifications()), 4)

    def test_malformed_event_json_and_context_fail_closed(self):
        for event in [dict(self.event, issue_number=True), dict(self.event, run_id=' run'),
                      dict(self.event, evidence_ids=[]), dict(self.event, kind=[]),
                      dict(self.event, evidence_ids=['same', 'same']), dict(self.event, destination={})]:
            with self.subTest(event=event):
                result = self.enqueue(event)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)['error'], 'invalid_attention')
        path = self.root / 'bad.json'
        for raw in ['[', '{"kind":"incident","kind":"decision"}', '[' * 1200 + '0' + ']' * 1200]:
            path.write_text(raw)
            result = self.command('attention', None, '--request', str(path))
            self.assertEqual(json.loads(result.stderr)['error'], 'invalid_attention')
        with closing(sqlite3.connect(self.state)) as db, db:
            original = json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])
        for origin in [None, dict(self.request['origin'], chat_id=' 123'),
                       dict(self.request['origin'], scope_id=789), dict(self.request['origin'], thread_id='999')]:
            item = dict(original, origin=origin)
            with closing(sqlite3.connect(self.state)) as db, db:
                db.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
            self.assertEqual(json.loads(self.enqueue().stderr)['error'], 'invalid_origin')
        item = dict(original, correlation=dict(original['correlation'], iteration_id='other'))
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
        self.assertEqual(json.loads(self.enqueue().stderr)['error'], 'invalid_context')
        self.assertEqual(self.notifications(), [])
        self.assertEqual(self.calls(), [])

    def test_origin_contract_preserves_intake_and_attention_errors_without_effects(self):
        origin = self.request['origin']
        metadata_message = 'Provide the verified originating Discord thread metadata.'
        identifier_message = 'Originating thread identifiers must be exact and consistent.'
        cases = [(None, metadata_message), ([], metadata_message),
                 (dict(origin, platform='Discord'), metadata_message),
                 (dict(origin, extra='123'), metadata_message)]
        for key in origin:
            cases.append(({k: v for k, v in origin.items() if k != key}, metadata_message))
        for key in ['chat_id', 'thread_id', 'parent_chat_id', 'scope_id']:
            for value in ['', '0', '0123', ' 123', '123\n', '-123', 123, True, None, []]:
                cases.append((dict(origin, **{key: value}), identifier_message))
        cases.append((dict(origin, thread_id='999'), identifier_message))
        with closing(sqlite3.connect(self.state)) as db:
            original = json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])
        for index, (invalid_origin, message) in enumerate(cases):
            with self.subTest(origin=invalid_origin):
                path = self.root / 'invalid-origin.json'
                path.write_text(json.dumps(dict(self.request, origin=invalid_origin)))
                absent_state = self.root / f'refused-{index}.sqlite'
                result = self.invoke('register', '--request', str(path), state=absent_state)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stderr), {'error': 'invalid_intake', 'message': message})
                self.assertFalse(absent_state.exists())
                with closing(sqlite3.connect(self.state)) as db, db:
                    db.execute('UPDATE iterations SET payload=?',
                               (json.dumps(dict(original, origin=invalid_origin)),))
                result = self.enqueue()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stderr), {
                    'error': 'invalid_origin',
                    'message': 'A verified exact originating Discord thread is required.'})
                self.assertEqual(self.notifications(), [])
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('UPDATE iterations SET payload=?', (json.dumps(original),))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.enqueue().returncode, 0)
        self.assertEqual(self.deliver().returncode, 0)
        self.assertEqual(self.notifications()[0]['destination'], origin)
        self.assertEqual(len(self.calls()), 1)

    def test_exact_supported_command_inert_mentions_ack_is_not_receipt(self):
        self.event['message'] = '@everyone <@42> <@&99> MEDIA:/secret [[as_document]]'
        self.enqueue()
        result = self.deliver()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'accepted')
        self.assertIsNone(self.notifications()[0]['receipt'])
        call = self.calls()[0]
        self.assertEqual(call['argv'], ['send', '--to', 'discord:123:123', '--file', '-', '--json'])
        self.assertNotIn('@', call['body'])
        self.assertNotIn('MEDIA:', call['body'])
        self.assertNotIn('[[as_document]]', call['body'])
        self.assertEqual(json.loads(call['body'])['event'], self.event)
        self.assertEqual(self.deliver().returncode, 0)
        self.assertEqual(len(self.calls()), 1)
        receipt = self.receipt()
        self.assertEqual(self.command('attention-reconcile', receipt).returncode, 0)
        self.assertEqual(self.notifications()[0]['state'], 'delivered')
        self.assertEqual(self.command('attention-reconcile', receipt).returncode, 0)
        self.assertEqual(self.deliver().returncode, 0)
        self.assertEqual(len(self.calls()), 1)

    def test_unavailable_or_unapproved_config_never_sends(self):
        self.enqueue()
        self.assertEqual(json.loads(self.deliver(False).stderr)['error'], 'capability_required')
        self.config.write_text(json.dumps({'command': [sys.executable, str(self.script), str(self.root)],
                                           'approved_destinations': [dict(self.request['origin'], scope_id='999')]}))
        self.assertEqual(self.deliver().returncode, 2)
        self.assertEqual(self.notifications()[0]['attempts'], 0)
        self.assertEqual(self.calls(), [])

    def test_known_no_effect_can_retry_after_restart(self):
        self.enqueue()
        self.config.write_text(json.dumps({'command': [str(self.root / 'absent')],
                                           'approved_destinations': [self.request['origin']]}))
        self.assertEqual(json.loads(self.deliver().stderr)['error'], 'transport_not_sent')
        self.assertEqual(self.notifications()[0]['state'], 'pending')
        self.config.write_text(json.dumps({'command': [sys.executable, str(self.script), str(self.root)],
                                           'approved_destinations': [self.request['origin']]}))
        self.mode('usage')
        self.assertEqual(self.deliver().returncode, 2)
        self.mode('success')
        self.assertEqual(self.deliver().returncode, 0)
        self.assertEqual(self.notifications()[0]['attempts'], 3)
        self.assertEqual(len(self.calls('effects.jsonl')), 1)

    def test_unknown_and_malformed_results_do_not_retry_or_leak(self):
        self.enqueue()
        for mode in ['unknown', 'malformed', 'skipped', 'false_success', 'duplicate', 'lost_after']:
            with self.subTest(mode=mode):
                self.mode(mode)
                self.assertEqual(json.loads(self.deliver().stderr)['error'], 'transport_uncertain')
                count = len(self.calls())
                self.assertEqual(self.deliver().returncode, 0)
                self.assertEqual(len(self.calls()), count)
                record = self.notifications()[0]
                self.assertEqual(record['state'], 'uncertain')
                self.assertNotIn('secret-do-not-retain', json.dumps(record))
                receipt = self.receipt('delivered' if mode == 'lost_after' else 'not_sent')
                self.assertEqual(self.command('attention-reconcile', receipt).returncode, 0)
        self.assertEqual(self.notifications()[0]['state'], 'delivered')

    def test_receipt_shape_binding_conflicts_and_origin_drift(self):
        self.enqueue()
        self.mode('unknown')
        self.deliver()
        receipt = self.receipt()
        for changed in [dict(receipt, delivery_key=['other']), dict(receipt, attempt=True),
                        dict(receipt, attempt=2), dict(receipt, message_id=' 1001'),
                        dict(receipt, destination=dict(receipt['destination'], scope_id='999')),
                        dict(receipt, content={}), dict(receipt, reference=''), dict(receipt, extra=True)]:
            with self.subTest(receipt=changed):
                self.assertEqual(self.command('attention-reconcile', changed).returncode, 2)
                self.assertEqual(self.notifications()[0]['state'], 'uncertain')
        self.assertEqual(self.command('attention-reconcile', receipt).returncode, 0)
        self.assertEqual(self.command('attention-reconcile', dict(receipt, message_id='1002')).returncode, 2)
        with closing(sqlite3.connect(self.state)) as db, db:
            item = json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])
            item['origin']['scope_id'] = '999'
            db.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
        self.assertEqual(self.deliver().returncode, 2)
        self.assertEqual(len(self.calls()), 1)

    def test_ack_silence_unrelated_chat_cannot_authorize_pending_decision(self):
        self.event = dict(self.event, kind='decision', decision_id='direction-15')
        self.enqueue()
        response = self.response()
        for changed in [{}, dict(response, operator_id='99'), dict(response, decision_id='other'),
                        dict(response, response=''), dict(response, acknowledgement=True),
                        dict(response, origin=dict(response['origin'], parent_chat_id='999'))]:
            self.assertEqual(self.command('respond', changed).returncode, 2)
        self.deliver()
        self.command('attention-reconcile', self.receipt())
        self.assertIsNone(self.notifications()[0]['decision_response'])
        first = self.command('respond', response)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.command('respond', response).stdout, first.stdout)
        self.assertEqual(self.command('respond', dict(response, response='Other')).returncode, 2)
        item = json.loads(self.invoke('inspect', '--project', 'approved-product', '--iteration', 'milestone-1').stdout)
        self.assertFalse(item['execution_allowed'])
        self.assertEqual(item['stage'], 'intake_registered')

    def test_completion_and_exact_pending_reply_do_not_start_another_iteration(self):
        self.event = dict(self.event, kind='decision', decision_id='direction-15')
        self.assertEqual(self.enqueue().returncode, 0)
        response = self.response()
        self.assertEqual(self.command('respond', response).returncode, 0)
        self.assertEqual(self.notifications()[0]['state'], 'pending')
        self.assertEqual(self.calls(), [])
        completion = dict(self.event, event_id='completion-1', kind='completion')
        completion.pop('decision_id')
        self.assertEqual(self.enqueue(completion).returncode, 0)
        unrelated = dict(response, event_id='completion-1')
        self.assertEqual(self.command('respond', unrelated).returncode, 2)
        result = self.invoke('inspect', '--project', 'approved-product', '--iteration', 'milestone-1')
        self.assertFalse(json.loads(result.stdout)['execution_allowed'])
        with closing(sqlite3.connect(self.state)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM iterations').fetchone()[0], 1)

    def test_malformed_persisted_identity_and_approval_refuse_without_send(self):
        with closing(sqlite3.connect(self.state)) as db:
            original = json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])
        for item in [[], dict(original, project_id='different'), dict(original, approval=[]),
                     dict(original, approval={'operator_id': '99', 'reference': 'x'}),
                     dict(original, approval={'operator_id': '42', 'reference': ''})]:
            with closing(sqlite3.connect(self.state)) as db, db:
                db.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
            result = self.enqueue()
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn(json.loads(result.stderr)['error'], {'invalid_context', 'approval_required'})
        self.assertEqual(self.calls(), [])

    def test_actual_process_death_before_and_after_effect_requires_reconciliation(self):
        self.enqueue()
        for mode in ['crash_before', 'crash_after']:
            self.mode(mode)
            result = self.deliver()
            self.assertEqual(result.returncode, -9)
            self.assertEqual(self.notifications()[0]['state'], 'uncertain')
            count = len(self.calls())
            self.assertEqual(self.deliver().returncode, 0)
            self.assertEqual(len(self.calls()), count)
            receipt = self.receipt('not_sent' if mode == 'crash_before' else 'delivered')
            self.assertEqual(self.command('attention-reconcile', receipt).returncode, 0)
        self.assertEqual(len(self.calls('effects.jsonl')), 1)

    def test_concurrent_enqueue_send_response_and_spending_writes(self):
        self.event = dict(self.event, kind='decision', decision_id='direction-15')
        self.enqueue()
        response = self.response()
        response_path = self.root / 'concurrent-response.json'
        response_path.write_text(json.dumps(response))
        event_path = self.root / 'concurrent-event.json'
        event_path.write_text(json.dumps(self.event))
        expires = str(int(time.time()) + 600)
        def operation(index):
            if index % 4 == 0:
                return self.deliver()
            if index % 4 == 1:
                return self.command('respond', None, '--request', str(response_path))
            if index % 4 == 2:
                return self.command('attention', None, '--request', str(event_path))
            return self.invoke('spend-grant', '--project', 'approved-product', '--iteration', 'milestone-1',
                               '--provider', 'fixture-provider', '--model', 'fixture-model', '--operation', 'generate',
                               '--kind', 'allowance', '--ceiling', '100', '--expires', expires,
                               '--reference', 'mock-only allowance')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(operation, range(16)))
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.notifications()[0]['decision_response'], response)
        self.assertEqual(self.notifications()[0]['attempts'], 1)


class PlanningAttentionTests(unittest.TestCase):
    def test_planning_and_onboarding_remain_silent_and_concurrent_writes_preserve_both(self):
        fixture = test_planning.PlanningTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.start()
        request = fixture.plan_request
        request['expected_revision'] = 1
        path = fixture.root / 'attention.json'
        event = {'event_id': 'incident-15', 'kind': 'incident', 'message': 'mock-only incident',
                 'issue_number': 15, 'run_id': 'run-15', 'evidence_ids': ['evidence-15']}
        path.write_text(json.dumps(event))
        with ThreadPoolExecutor(max_workers=3) as pool:
            tasks = [pool.submit(fixture.command, 'plan', request), pool.submit(fixture.fixture.onboard),
                     pool.submit(fixture.cli, 'attention', '--project', 'product', '--iteration', 'm1', '--request', str(path))]
            for task in tasks:
                result = task.result()
                self.assertEqual(result.returncode, 0, result.stderr)
        item = fixture.inspect()
        self.assertEqual(item['planning']['revision'], 2)
        self.assertEqual(item['repository_onboarding']['status'], 'verified')
        result = fixture.cli('notifications', '--project', 'product', '--iteration', 'm1')
        self.assertEqual(len(json.loads(result.stdout)), 1)
        self.assertFalse(item['execution_allowed'])


if __name__ == '__main__':
    unittest.main()
