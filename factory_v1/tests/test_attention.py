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

    def assert_invalid_repository_refused(self, values):
        original_record = json.loads(self.enqueue().stdout)
        with closing(sqlite3.connect(self.state)) as db:
            original = db.execute('SELECT payload FROM iterations').fetchone()[0]
            before = db.execute('SELECT * FROM notifications').fetchall()
        for index, (present, value) in enumerate(values):
            with self.subTest(present=present, repository=value):
                item = json.loads(original)
                if present:
                    item['repository'] = value
                else:
                    item.pop('repository')
                raw = json.dumps(item)
                with closing(sqlite3.connect(self.state)) as db, db:
                    db.execute('UPDATE iterations SET payload=?', (raw,))
                event = dict(self.event, event_id=f'refused-repository-{index}')
                for candidate in [event, self.event]:
                    result = self.enqueue(candidate)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(json.loads(result.stderr), {
                        'error': 'invalid_context',
                        'message': 'Recorded repository must be an exact GitHub owner/repository identifier.'})
                result = self.command('deliver', None, '--event', event['event_id'],
                                      '--transport-config', str(self.config))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stderr)['error'], 'not_found')
                with closing(sqlite3.connect(self.state)) as db:
                    self.assertEqual(db.execute('SELECT * FROM notifications').fetchall(), before)
                    self.assertEqual(db.execute('SELECT payload FROM iterations').fetchone()[0], raw)
                self.assertEqual(self.calls(), [])
                self.assertEqual(self.calls('effects.jsonl'), [])
                with closing(sqlite3.connect(self.state)) as db, db:
                    db.execute('UPDATE iterations SET payload=?', (original,))
        self.assertEqual(self.notifications(), [original_record])
        recovered = dict(self.event, event_id='valid-repository-recovery')
        result = self.enqueue(recovered)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record['correlation']['work_item']['repository'], self.request['repository'])
        self.assertEqual(record['state'], 'pending')
        self.assertEqual(record['attempts'], 0)
        self.assertEqual(self.notifications(), [original_record, record])
        self.assertEqual(self.calls(), [])

    def test_missing_persisted_repository_refused_and_valid_context_recovers(self):
        self.assert_invalid_repository_refused([(False, None)])

    def test_null_persisted_repository_refused_and_valid_context_recovers(self):
        self.assert_invalid_repository_refused([(True, None)])

    def test_non_string_empty_and_malformed_persisted_repository_refused(self):
        values = [42, True, [], {}, '', ' ', 'example', 'example/repo/other',
                  'https://github.com/example/repo', ' example/repo', 'example/repo ',
                  'example/repo\n', '-owner/repo', 'owner-/repo', 'a--b/repo',
                  'owner_name/repo', 'owner/.', 'owner/..', 'owner/', '/repo',
                  'a' * 40 + '/repo', 'owner/' + 'r' * 101]
        self.assert_invalid_repository_refused([(True, value) for value in values])

    def test_valid_repository_grammar_is_shared_by_intake_and_attention(self):
        with closing(sqlite3.connect(self.state)) as db:
            original = db.execute('SELECT payload FROM iterations').fetchone()[0]
        values = ['A/.repo_name-1', 'a-b/repo.', 'a' * 39 + '/' + 'r' * 100]
        for index, repository in enumerate(values):
            with self.subTest(repository=repository):
                request = dict(self.request, repository=repository)
                path = self.root / 'valid-repository.json'
                path.write_text(json.dumps(request))
                registered = self.invoke('register', '--request', str(path),
                                         state=self.root / f'valid-repository-{index}.sqlite')
                self.assertEqual(registered.returncode, 0, registered.stderr)
                item = dict(json.loads(original), repository=repository)
                with closing(sqlite3.connect(self.state)) as db, db:
                    db.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
                event = dict(self.event, event_id=f'valid-repository-{index}')
                result = self.enqueue(event)
                self.assertEqual(result.returncode, 0, result.stderr)
                record = json.loads(result.stdout)
                self.assertEqual(record['correlation']['work_item']['repository'], repository)
                self.assertEqual(self.notifications()[-1], record)
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

    def test_malformed_persisted_notifications_refuse_all_lifecycle_operations(self):
        self.event = dict(self.event, kind='decision', decision_id='direction-15')
        original = json.loads(self.enqueue().stdout)
        receipt = self.receipt()
        response = self.response()
        malformed = ['[', '[]', 'null', '"secret-persisted-diagnostic"',
                     '[' * 1200 + '0' + ']' * 1200]
        for key in original:
            malformed.append(json.dumps({k: v for k, v in original.items() if k != key}))
        for changes in [dict(event=[]), dict(event={}), dict(destination=[]),
                        dict(destination=dict(original['destination'], chat_id=' 123')),
                        dict(state=[]), dict(state='unknown'), dict(attempts=True),
                        dict(attempts=-1), dict(attempts='1'), dict(project_id='other'),
                        dict(iteration_id='other'), dict(event=dict(self.event, event_id='other')),
                        dict(correlation=[]), dict(correlation={}),
                        dict(correlation=dict(original['correlation'], worker_run_id='other')),
                        dict(receipt=[]), dict(decision_response=[]), dict(last_error=[]),
                        dict(reconciliation=[])]:
            malformed.append(json.dumps(dict(original, **changes)))
        malformed.append(json.dumps(original)[:-1] + ',"state":"accepted"}')
        for raw in malformed:
            with self.subTest(payload=raw[:120]):
                with closing(sqlite3.connect(self.state)) as db, db:
                    db.execute('UPDATE notifications SET payload=?', (raw,))
                    before = db.execute('SELECT * FROM notifications').fetchall()
                    iteration_before = db.execute('SELECT * FROM iterations').fetchall()
                results = [self.command('notifications'), self.deliver(), self.enqueue(),
                           self.command('respond', response),
                           self.command('attention-reconcile', receipt)]
                for result in results:
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(json.loads(result.stderr), {
                        'error': 'state_error',
                        'message': 'Persisted attention state is malformed or inconsistent; no delivery was attempted.'})
                with closing(sqlite3.connect(self.state)) as db:
                    self.assertEqual(db.execute('SELECT * FROM notifications').fetchall(), before)
                    self.assertEqual(db.execute('SELECT * FROM iterations').fetchall(), iteration_before)
                self.assertEqual(self.calls(), [])
                self.assertEqual(self.calls('effects.jsonl'), [])
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('UPDATE notifications SET payload=?', (json.dumps(original),))
        self.assertEqual(self.notifications(), [original])
        self.assertEqual(self.deliver().returncode, 0)
        self.assertEqual(self.notifications()[0]['state'], 'accepted')
        self.assertEqual(len(self.calls()), 1)

    def test_ambiguous_and_parser_limit_iteration_json_refuse_all_attention_actions(self):
        self.event = dict(self.event, kind='decision', decision_id='direction-15')
        self.enqueue()
        receipt, response = self.receipt(), self.response()
        with closing(sqlite3.connect(self.state)) as db:
            original = db.execute('SELECT payload FROM iterations').fetchone()[0]
        item = json.loads(original)
        malformed = ['[', '[' * 10000 + '0' + ']' * 10000,
                     original[:-1] + ',"origin":null,"origin":' + json.dumps(item['origin']) + '}',
                     original.replace('"chat_id": "123"', '"chat_id":"999","chat_id":"123"'),
                     original.replace('"operator_id": "42"', '"operator_id":"99","operator_id":"42"'),
                     original.replace('"control_run_id":', '"control_run_id":"other","control_run_id":')]
        for raw in malformed:
            with self.subTest(payload=raw[:100]):
                with closing(sqlite3.connect(self.state)) as db, db:
                    db.execute('UPDATE iterations SET payload=?', (raw,))
                    before = db.execute('SELECT * FROM notifications').fetchall()
                results = [self.enqueue(), self.deliver(), self.command('notifications'),
                           self.command('respond', response), self.command('attention-reconcile', receipt)]
                for result in results:
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(json.loads(result.stderr)['error'], 'invalid_context')
                with closing(sqlite3.connect(self.state)) as db:
                    self.assertEqual(db.execute('SELECT payload FROM iterations').fetchone()[0], raw)
                    self.assertEqual(db.execute('SELECT * FROM notifications').fetchall(), before)
                self.assertEqual(self.calls(), [])
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('UPDATE iterations SET payload=?', (original,))
        self.assertEqual(self.deliver().returncode, 0)
        self.assertEqual(len(self.calls()), 1)

    def test_inconsistent_persisted_receipts_and_responses_refuse_all_actions(self):
        self.event = dict(self.event, kind='decision', decision_id='direction-15')
        original = json.loads(self.enqueue().stdout)
        receipt = dict(self.receipt(), attempt=1)
        response = self.response()
        delivered = dict(original, state='delivered', attempts=1,
                         receipt=receipt, reconciliation=receipt)
        malformed = [dict(original, state='delivered', attempts=1, receipt=value)
                     for value in [None, {}]]
        malformed += [dict(original, receipt=receipt), dict(delivered, reconciliation={}),
                      dict(delivered, reconciliation=None), dict(delivered, receipt=None),
                      dict(delivered, state='accepted'), dict(delivered, attempts=2),
                      dict(delivered, last_error='transport_uncertain')]
        for key, value in [('attempt', True), ('attempt', 0), ('event_id', 'other'),
                           ('delivery_key', ['other']), ('destination', {}), ('content', {}),
                           ('message_id', ' 1001'), ('reference', ''), ('extra', True)]:
            changed = dict(receipt, **{key: value})
            malformed.append(dict(delivered, receipt=changed, reconciliation=changed))
        not_sent = dict(receipt, outcome='not_sent')
        not_sent.pop('message_id')
        malformed += [dict(original, reconciliation=not_sent),
                      dict(original, state='accepted', attempts=1, reconciliation=not_sent),
                      dict(original, state='uncertain', attempts=1, reconciliation=not_sent)]
        for changed in [{}, {'operator_id': '99', 'decision_id': 'unrelated', 'origin': {}},
                        dict(response, operator_id='99'), dict(response, operator_id=42),
                        dict(response, decision_id='other'), dict(response, event_id='other'),
                        dict(response, origin={}), dict(response, reference=''),
                        dict(response, response=''), dict(response, acknowledgement=True)]:
            malformed.append(dict(original, decision_response=changed))
        incident = dict(self.event, kind='incident')
        incident.pop('decision_id')
        malformed.append(dict(original, event=incident, decision_response=response))
        for record in malformed:
            with self.subTest(record=record):
                raw = json.dumps(record)
                with closing(sqlite3.connect(self.state)) as db, db:
                    db.execute('UPDATE notifications SET payload=?', (raw,))
                    iteration_before = db.execute('SELECT * FROM iterations').fetchall()
                results = [self.command('notifications'), self.deliver(), self.enqueue(),
                           self.command('respond', response), self.command('attention-reconcile', receipt)]
                for result in results:
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(json.loads(result.stderr)['error'], 'state_error')
                with closing(sqlite3.connect(self.state)) as db:
                    self.assertEqual(db.execute('SELECT payload FROM notifications').fetchone()[0], raw)
                    self.assertEqual(db.execute('SELECT * FROM iterations').fetchall(), iteration_before)
                self.assertEqual(self.calls(), [])
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('UPDATE notifications SET payload=?', (json.dumps(original),))
        self.assertEqual(self.command('respond', response).returncode, 0)
        self.assertEqual(self.notifications()[0]['decision_response'], response)
        self.assertEqual(self.deliver().returncode, 0)
        self.assertEqual(self.command('attention-reconcile', self.receipt()).returncode, 0)
        self.assertEqual(self.notifications()[0]['state'], 'delivered')
        self.assertEqual(self.deliver().returncode, 0)
        self.assertEqual(len(self.calls()), 1)

    def test_no_effect_reconciliation_survives_retry_with_new_attempt(self):
        self.enqueue()
        self.mode('unknown')
        self.assertEqual(self.deliver().returncode, 2)
        receipt = self.receipt('not_sent')
        self.assertEqual(self.command('attention-reconcile', receipt).returncode, 0)
        self.assertEqual(self.notifications()[0]['state'], 'pending')
        self.mode('success')
        self.assertEqual(self.deliver().returncode, 0)
        record = self.notifications()[0]
        self.assertEqual(record['attempts'], 2)
        self.assertEqual(record['state'], 'accepted')
        self.assertEqual(record['reconciliation'], receipt)
        self.assertIsNone(record['receipt'])
        self.assertEqual(self.command('attention-reconcile', receipt).returncode, 2)
        self.assertEqual(self.command('attention-reconcile', self.receipt()).returncode, 0)
        self.assertEqual(self.notifications()[0]['state'], 'delivered')
        self.assertEqual(self.deliver().returncode, 0)
        self.assertEqual(len(self.calls()), 2)

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
