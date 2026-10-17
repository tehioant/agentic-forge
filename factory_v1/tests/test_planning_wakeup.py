"""Direct mailbox/native contract tests; explicit records, no journey or running tasks."""
import copy
import json
import hashlib
import textwrap
from pathlib import Path
import time
from types import SimpleNamespace, ModuleType
import sys
import unittest
from unittest.mock import Mock, patch
from typing import Mapping, Any

from factory_v1 import assignments, controls, planning, planning_wakeup as wakeup, repositories
from factory_v1.tests import test_conversation_binding as binding_tests


def native_contract(name, namespace):
    """Pinned external API slice, NOT proof of an installed/runtime Hermes host."""
    root = Path(__file__).parent / 'native_api_snapshots'
    metadata = json.loads((root / 'metadata.json').read_text())[name]
    raw = (root / (name + '.txt')).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == metadata['sha256']
    exec(compile(textwrap.dedent(raw.decode()), metadata['path'], 'exec'), namespace)
    return namespace.get(name)


class PlanningWakeupTests(unittest.TestCase):
    def setUp(self):
        # Reuse only isolated SQLite/source setup, never execute a predecessor stage.
        binding_tests.ConversationBindingTests.setUp(self)
        self.item.pop('handoff', None)
        self.item.pop('ticket_work', None)
        self.item['planning']['native_execution'] = {
            'assignment_id': 'origin-assignment', 'session_id': 'native-origin', 'task_id': 'original-task',
            'turn_id': 'original-turn', 'run_id': 'actual-host-run-fixture', 'origin': self.item['origin'],
            'operator_id': '42', 'binding_reference': self.config['reference'], 'sources': self.selected,
            'adaptation': planning.INSTRUCTIONS, 'loads': ['grill-me', 'grilling'], 'sufficient': False,
            'observations': [{'reference': 'native-turn:original-turn', 'role': 'user', 'content': 'Use Python.'}]}
        self.approval = {'binding': self.config, 'assignment_id': 'origin-assignment', 'profile_name': 'approved-profile',
            'source_digest': assignments.digest({'skills': self.selected, 'adaptation': planning.INSTRUCTIONS}),
            'allowance': {'reference': 'explicit-single-native-turn', 'provider_route': 'approved-existing-native-profile', 'model': 'fixture-model',
                          'expires_at': int(time.time()) + 3600, 'max_wakeups': 1}}
        self.path = self.fixture.root / 'wakeup.json'
        self.write_approval()
        self.ctx.profile_name = 'approved-profile'
        self.ctx.get_config.return_value = self.approval['allowance']['provider_route']
        self.native = wakeup.NativeWakeup(self.binding, str(self.path))
        self.fixture.persist()

    def write_approval(self):
        self.path.write_text(json.dumps(self.approval)); self.path.chmod(0o600)

    def request_record(self, status='requested'):
        identity = wakeup.scope(self.item, self.approval)
        return {**identity, 'request_id': assignments.digest(identity), 'status': status, 'config_path': str(self.path)}

    def mailbox(self, status='requested'):
        self.item['planning']['native_wakeup'] = self.request_record(status)
        self.fixture.persist()

    def supervise(self):
        return wakeup.supervise(self.fixture.database, self.fixture.stored(), str(self.path), self.fixture.github)

    def complete_record(self):
        self.mailbox('running')
        work = self.item['planning']
        work['native_wakeup']['execution_turn'] = {'task_id': 'wake-task', 'turn_id': 'wake-turn'}
        work['native_execution'].update(sufficient=True, loads=['grill-me', 'grilling', 'to-spec'])
        work.update(status='complete', completion_digest='fixture-completion-digest')
        issue = {'number': 7, 'id': 700, 'url': 'https://github.com/example/product/issues/7'}
        self.item['handoff'] = {'repository': self.item['repository'], 'commit': 'a' * 40,
                                'issue': issue, 'documents': {'fixture': 'immutable-docs'}}
        work['host_publication'] = {'status': 'verified', 'execution': {'native': copy.deepcopy(work['native_execution'])},
                                    'commit': 'a' * 40, 'issue': issue, 'documents': {'fixture': 'actual-documents'}}
        self.fixture.persist()

    def original_completion(self):
        """Explicit completed original-turn record, no wakeup or stage execution."""
        work = self.item['planning']
        work.pop('native_wakeup', None)
        work['native_execution'].update(sufficient=True, loads=['grill-me', 'grilling', 'to-spec'])
        work['native_execution']['rationale'] = 'Genuine choices settled.'
        issue = {'number': 7, 'id': 700, 'url': 'https://github.com/example/product/issues/7'}
        self.item['handoff'] = {'repository': self.item['repository'], 'commit': 'a' * 40,
                                'issue': issue, 'documents': {'fixture': 'immutable-docs'}}
        work.update(status='complete', pending_questions=[],
            judgment={'sufficient': True, 'rationale': 'Genuine choices settled.', 'reference': 'original-output'},
            host_publication={'status': 'verified', 'execution': {'native': copy.deepcopy(work['native_execution']), 'reference': 'original-output'},
                              'commit': 'a' * 40, 'issue': issue, 'documents': {'fixture': 'actual-documents'}})
        completed = {'assignment_id': work['assignment_id'], 'origin': self.item['origin'], **work['judgment'],
            'pending_questions': [], 'documents': work['host_publication']['documents'], 'commit': 'a' * 40, 'issue_number': 7}
        work['completion_digest'] = hashlib.sha256(json.dumps(completed, sort_keys=True,
            ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        self.fixture.persist()

    def approve_sender(self, platform='discord', user_id='42'):
        self.item['origin']['platform'] = platform
        self.config['origin']['platform'] = platform
        self.item['planning']['origin']['platform'] = platform
        self.item['planning']['native_execution']['origin']['platform'] = platform
        self.config['operator_id'] = user_id
        self.binding.config = copy.deepcopy(self.config)
        self.item['approval']['operator_id'] = user_id
        self.item['planning']['native_execution']['operator_id'] = user_id
        self.approval['binding'] = copy.deepcopy(self.config)
        self.approval['input_envelope'] = {'kind': 'sender_attribution', 'platform': platform,
                                          'user_id': user_id, 'user_name': 'Operator'}
        self.write_approval()

    def test_original_completion_fresh_authority(self):
        self.original_completion()
        original = copy.deepcopy(self.item); approval = copy.deepcopy(self.approval)
        cases = [('adopt', None, None)]
        cases += [('entry', field, None) for field in ('origin', 'assignment_id', 'sources', 'sufficient', 'turn_id', 'publication', 'completion', 'missing-completion')]
        cases += [('read', field, boundary) for boundary in (1, 2) for field in ('publication', 'completion', 'approval', 'pause', 'handoff')]
        cases += [('read', 'durable-pause', 2)]
        for phase, field, boundary in cases:
            with self.subTest(phase=phase, field=field, boundary=boundary):
                self.item = copy.deepcopy(original); self.fixture.item = self.item
                self.approval = copy.deepcopy(approval)
                if phase == 'adopt': self.approval['allowance']['expires_at'] = 1
                self.write_approval(); calls = []
                def mutate():
                    work = self.item['planning']
                    if field == 'publication': work['host_publication']['status'] = 'revoked'
                    elif field == 'completion': work['completion_digest'] = 'changed'
                    elif field == 'missing-completion': work.pop('completion_digest')
                    elif field == 'approval': self.approval['profile_name'] = 'changed'; self.write_approval()
                    elif field == 'pause': self.item['status'] = 'paused'
                    elif field == 'durable-pause':
                        controls.request(self.fixture.database, 'product', 'm1', '42', 'pause', 'final-read-pause')
                        return
                    elif field == 'handoff': self.item['handoff']['commit'] = 'b' * 40
                    elif field == 'turn_id': work['native_execution'].pop('turn_id')
                    else:
                        work['native_execution'][field] = {'origin': {'platform': 'other'}, 'assignment_id': 'wrong', 'sources': {}, 'sufficient': False}[field]
                    self.fixture.persist()
                self.fixture.persist()
                if phase == 'entry': mutate()
                def read(*args):
                    calls.append(True)
                    if phase == 'read' and len(calls) == boundary: mutate()
                    return copy.deepcopy(original['handoff'])
                with patch.object(planning, 'read_publication', side_effect=read) as reads:
                    if phase == 'adopt':
                        self.assertEqual(self.supervise(), 'consumed'); self.assertEqual(self.supervise(), 'consumed')
                        stored = self.fixture.stored(); record = stored['planning'].pop('native_wakeup')
                        self.assertEqual(record['mode'], 'origin_turn')
                        for key, expected in (('task_id', 'original-task'), ('turn_id', 'original-turn'), ('run_id', 'actual-host-run-fixture')):
                            self.assertEqual(record['receipt'][key], expected)
                        self.assertNotIn('request_id', record); self.assertNotIn('execution_turn', record)
                        self.assertEqual(stored, original); self.assertGreaterEqual(reads.call_count, 2)
                    else:
                        with self.assertRaises(repositories.RepositoryError): self.supervise()
                        self.assertNotIn('native_wakeup', self.fixture.stored()['planning'])
                        if field == 'durable-pause': self.assertTrue(controls.blocked(self.fixture.database, 'product', 'm1'))
                self.ctx.inject_message.assert_not_called()





    def test_sender_envelope_authority_refusals(self):
        for platform, sender in (('slack', 'U123ABC'), ('discord', '42')):
            self.approve_sender(platform, sender); self.mailbox('enqueued')
            original = copy.deepcopy(self.approval)
            changes = ([{'binding': {**original['binding'], 'operator_id': bad},
                         'input_envelope': {**original['input_envelope'], 'user_id': bad}}
                        for bad in ('42', '<@U123ABC>', 'Ubad', 'U12\nABC')] if platform == 'slack' else
                       [{'input_envelope': {**original['input_envelope'], **change}} for change in
                        ({'user_name': 'bad\nname'}, {'user_name': 'bad\x00name'}, {'user_name': '[nested]'},
                         {'platform': 'slack'}, {'user_id': 'wrong'}, {'prefix': '[Operator] '}, {'kind': 'arbitrary'})])
            for change in changes:
                self.approval = {**copy.deepcopy(original), **change}; self.write_approval()
                with self.subTest(platform=platform, change=change), self.assertRaises(repositories.RepositoryError): wakeup.configuration(str(self.path))
            if platform == 'discord':
                for change in ({'profile_name': 'other'}, {'allowance': {**original['allowance'], 'expires_at': 1}},
                               {'input_envelope': {**original['input_envelope'], 'user_name': 'Other'}}):
                    self.approval = {**copy.deepcopy(original), **change}; self.write_approval()
                    with self.subTest(change=change), self.assertRaises(repositories.RepositoryError):
                        self.binding.context('native-origin', task_id='task', turn_id='turn', sender_id='42', model='fixture-model',
                            user_message='[Operator] ' + wakeup.message(self.item['planning']['native_wakeup']),
                            conversation_history=[{'role': 'user', 'content': 'Use Python.'}])
                self.assertEqual(self.fixture.stored()['planning']['native_wakeup']['status'], 'enqueued')


    def test_approved_sender_envelope_exact_rendering_only_preserves_answers(self):
        for platform, sender in (('discord', '42'), ('slack', 'U123ABC')):
            self.approve_sender(platform, sender); self.mailbox('enqueued')
            original = copy.deepcopy(self.item['planning']['native_execution'])
            prefix = native_contract('_prefix_inbound_sender_context', {
                'MessageEvent': Any, 'SessionSource': Any, 'Platform': SimpleNamespace(SLACK='slack'),
                'is_shared_multi_user_session': lambda *a, **kw: True,
                'neutralize_untrusted_inline_text': lambda name: name})
            raw = wakeup.message(self.item['planning']['native_wakeup'])
            rendered = prefix(SimpleNamespace(config=SimpleNamespace()), SimpleNamespace(),
                SimpleNamespace(platform=platform, user_id=sender, user_name='Operator'), raw)
            args = {'session_id': 'native-origin', 'task_id': 'wake-task', 'turn_id': 'wake-turn',
                'sender_id': sender, 'model': 'fixture-model', 'user_message': rendered,
                'conversation_history': [{'role': 'user', 'content': 'Use Python.'}]}
            for change in ({'sender_id': 'wrong'}, {'user_message': '[Other] ' + raw},
                           {'user_message': '[Operator] ' + rendered}, {'user_message': rendered + ' extra'},
                           {'user_message': 'backfill\n' + rendered}, {'conversation_history': []},
                           {'model': 'other'}, {'user_message': raw}):
                with self.subTest(platform=platform, change=change), self.assertRaises(repositories.RepositoryError):
                    self.binding.context(**{**args, **change})
            self.binding.context(**args)
            with self.assertRaises(repositories.RepositoryError): self.binding.context(**{**args, 'turn_id': 'replayed'})
            self.ctx.profile_name = 'wrong-profile'
            with self.assertRaises(repositories.RepositoryError): self.binding.context(**args)
            self.ctx.profile_name = 'approved-profile'
            self.assertEqual(self.fixture.stored()['planning']['native_execution'], original)
            self.assertEqual(self.fixture.stored()['planning']['native_wakeup']['status'], 'running')


    def test_enqueue_intent_and_no_blind_retry(self):
        original = copy.deepcopy(self.item)
        for label, outcome in (('request-only', None), ('acknowledged', True), ('rejected', False), ('unknown', RuntimeError('unknown outcome'))):
            with self.subTest(label=label):
                self.item = copy.deepcopy(original); self.fixture.item = self.item; self.fixture.persist()
                self.ctx.inject_message.reset_mock(); self.ctx.inject_message.side_effect = None
                if label == 'request-only':
                    self.assertEqual(self.supervise(), 'requested'); before = self.fixture.stored()
                    self.assertEqual(self.supervise(), 'requested'); self.assertEqual(self.fixture.stored(), before)
                    self.ctx.inject_message.assert_not_called()
                    continue
                self.mailbox()
                def enqueue(content, **kwargs):
                    stored = self.fixture.stored()['planning']
                    self.assertEqual(stored['native_wakeup']['status'], 'enqueue_unknown')
                    self.assertEqual(content, wakeup.message(stored['native_wakeup']))
                    self.assertEqual(kwargs, {'role': 'user', 'origin': self.item['origin']})
                    if isinstance(outcome, Exception): raise outcome
                    return outcome
                self.ctx.inject_message.side_effect = enqueue
                self.native.step(); self.native.step()
                self.ctx.inject_message.assert_called_once()
                expected = 'enqueue_unknown' if isinstance(outcome, Exception) else 'enqueued' if outcome else 'rejected'
                self.assertEqual(self.supervise(), expected)
                self.assertEqual(self.fixture.stored()['planning']['status'], 'pending')
                self.assertNotIn('handoff', self.fixture.stored())




    def test_enqueue_current_authority(self):
        self.mailbox(); original = copy.deepcopy(self.item); approved = copy.deepcopy(self.approval)
        cases = [('assignment_id', 'wrong'), ('profile_name', 'other'), ('source_digest', 'wrong'),
                 ('binding', {**self.config, 'session_id': 'wrong'}),
                 ('allowance', {**approved['allowance'], 'max_wakeups': 2}),
                 ('allowance', {**approved['allowance'], 'expires_at': 1}),
                 ('stop', None), ('pause', None), ('missing', None)]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                self.item = copy.deepcopy(original); self.fixture.item = self.item; self.fixture.persist()
                self.approval = copy.deepcopy(approved); self.write_approval(); self.native.stopped = False
                if key == 'stop':
                    self.native.stop(); self.native.step()
                else:
                    if key == 'pause': self.item['status'] = 'paused'; self.fixture.persist()
                    elif key == 'missing': self.path.unlink()
                    else: self.approval[key] = value; self.write_approval()
                    with self.assertRaises((OSError, repositories.RepositoryError)): self.native.step()
                self.ctx.inject_message.assert_not_called()



    def test_native_wakeup_turn_binding(self):
        self.mailbox('enqueued')
        initial = copy.deepcopy(self.item)
        args = {'session_id': 'native-origin', 'task_id': 'wake-task', 'turn_id': 'wake-turn', 'sender_id': '42', 'model': 'fixture-model',
                'user_message': wakeup.message(self.item['planning']['native_wakeup']),
                'conversation_history': [{'role': 'user', 'content': 'Use Python.'}]}
        for change in ({'sender_id': 'forged'}, {'conversation_history': []}, {'session_id': 'other'},
                       {'user_message': 'host timestamp prefix ' + args['user_message']},
                       {'user_message': wakeup.TAG + 'forged]'}, {'turn_id': ''}, {}):
            with self.subTest(change=change):
                self.item = copy.deepcopy(initial); self.fixture.item = self.item; self.fixture.persist()
                if not change:
                    self.binding.context(**args); self.binding.context(**args)
                    stored = self.fixture.stored()['planning']
                    self.assertEqual(stored['native_execution'], initial['planning']['native_execution'])
                    self.assertEqual(stored['native_wakeup']['execution_turn'], {'task_id': 'wake-task', 'turn_id': 'wake-turn'})
                else:
                    if change == {'session_id': 'other'}: self.assertIsNone(self.binding.context(**{**args, **change}))
                    else:
                        with self.assertRaises(repositories.RepositoryError): self.binding.context(**{**args, **change})
                    self.assertEqual(self.fixture.stored()['planning']['native_wakeup']['status'], 'enqueued')



    def test_completion_consumes_exact_fresh_receipt(self):
        self.complete_record(); original = copy.deepcopy(self.item)
        for defect in ('run_id', 'publication-absent', 'publication-unverified', 'turn-absent', 'digest-absent', 'wrong-readback', 'late-revocation', None):
            with self.subTest(defect=defect):
                self.item = copy.deepcopy(original); self.fixture.item = self.item; work = self.item['planning']
                if defect == 'run_id': work['native_execution']['run_id'] = 'wrong'
                if defect == 'publication-absent': work.pop('host_publication')
                if defect == 'publication-unverified': work['host_publication']['status'] = 'unverified'
                if defect == 'turn-absent': work['native_wakeup'].pop('execution_turn')
                if defect == 'digest-absent': work.pop('completion_digest')
                self.fixture.persist()
                def read(*args):
                    if defect == 'late-revocation': work['host_publication']['status'] = 'revoked'; self.fixture.persist()
                    return {'wrong': 'handoff'} if defect == 'wrong-readback' else copy.deepcopy(original['handoff'])
                with patch.object(planning, 'read_publication', side_effect=read) as reads:
                    if defect is None:
                        self.assertEqual(self.supervise(), 'consumed'); self.assertEqual(self.supervise(), 'consumed')
                        reads.assert_called_once()
                        self.assertEqual(self.fixture.stored()['planning']['native_wakeup']['receipt']['run_id'], 'actual-host-run-fixture')
                    else:
                        with self.assertRaises(repositories.RepositoryError): self.supervise()
                        self.assertEqual(self.fixture.stored()['planning']['native_wakeup']['status'], 'running')
                self.ctx.inject_message.assert_not_called()



    def test_lifecycle_unavailable_is_explicit_and_unload_cancels_owned_task(self):
        # Drive the coroutine with an immediate sleep seam: no event loop/socketpair.
        for hold in ('iteration_paused', 'scope_mismatch', 'execution_uncertain'):
            native = wakeup.NativeWakeup(self.binding, str(self.path))
            calls = []
            def step():
                calls.append(True)
                if len(calls) == 1:
                    raise repositories.RepositoryError(hold, 'fixture hold')
                native.status = 'idle'
            async def tick(delay):
                self.ctx.inject_message.assert_not_called()
                self.assertFalse(native.joined.is_set())
                if len(calls) >= 2: native.stopped = True
            with patch.object(native, 'step', side_effect=step), patch.object(wakeup.asyncio, 'sleep', side_effect=tick):
                operation = native.run()
                with self.assertRaises(StopIteration): operation.send(None)
            self.assertTrue(native.joined.is_set())
            self.assertEqual(len(calls), 2 if hold == 'iteration_paused' else 1)
            self.assertEqual(native.status, 'stopped' if hold == 'iteration_paused' else 'held')
        # A genuinely missing first observation is quiet, not terminal scope drift.
        self.item['planning'].pop('native_execution'); self.fixture.persist()
        self.native.step()
        self.assertEqual(self.native.status, 'waiting_input')
        self.ctx.spawn_task.side_effect = RuntimeError('no running loop')
        self.native.start()
        self.assertEqual(self.native.status, 'native_lifecycle_unavailable')
        self.ctx.on_unload.assert_called_once_with(self.native.stop)
        self.native.task = Mock(); self.native.stop()
        self.native.task.cancel.assert_called_once()
        self.native.step(); self.ctx.inject_message.assert_not_called()

    def test_cancel_is_durable_and_prevents_native_tool_admission(self):
        self.mailbox('running')
        self.item['planning']['native_wakeup']['execution_turn'] = {'task_id': 'wake-task', 'turn_id': 'wake-turn'}
        self.fixture.persist()
        self.native.stop()
        self.assertTrue(self.fixture.stored()['planning']['native_wakeup']['cancelled'])
        self.assertEqual(self.supervise(), 'cancelled')
        with self.assertRaises(repositories.RepositoryError):
            self.binding.sufficient(self.judgment, session_id='native-origin')

    def test_wrong_model_or_provider_route_and_enqueue_only_cannot_publish(self):
        self.mailbox('enqueued')
        with self.assertRaises(repositories.RepositoryError):
            self.binding.sufficient(self.judgment, session_id='native-origin')
        self.ctx.get_config.return_value = 'unapproved-personal-provider'
        with self.assertRaises(repositories.RepositoryError): self.native.step()
        with self.assertRaises(repositories.RepositoryError):
            self.binding.context('native-origin', task_id='t', turn_id='u', sender_id='42', model='unapproved',
                user_message=wakeup.message(self.item['planning']['native_wakeup']),
                conversation_history=[{'role': 'user', 'content': 'Use Python.'}])
        self.ctx.inject_message.assert_not_called()


    def test_pinned_native_host_spawn_body_and_bounded_join(self):
        import concurrent.futures
        future = concurrent.futures.Future()
        scheduler = Mock(return_value=future)
        ns = {'Any': Any, 'Optional': __import__('typing').Optional, 'inspect': __import__('inspect'),
              'asyncio': SimpleNamespace(run_coroutine_threadsafe=scheduler),
              '_on_behalf_of': lambda coro, request: coro, 'serving_request': lambda: 'native-host-request'}
        native_contract('spawn', ns)
        coro = self.native.run()
        try:
            host = SimpleNamespace(loop='existing-native-loop')
            self.assertIs(ns['spawn'](host, coro, name='wakeup'), future)
            scheduler.assert_called_once_with(coro, host.loop)
        finally:
            coro.close()
        self.native.task = future
        self.native.joined = Mock(); self.native.joined.wait.return_value = False
        self.native.stop()
        self.native.joined.wait.assert_called_once_with(2)
        self.assertEqual(self.native.status, 'shutdown_unconfirmed')

    def test_pinned_native_injection_contract(self):
        import logging
        for interface in ('router', 'context'):
            with self.subTest(interface=interface):
                if interface == 'router':
                    ns = {'Any': Any, 'Optional': __import__('typing').Optional, 'Mapping': Mapping,
                          'logger': logging.getLogger('native-body-probe')}
                    native_contract('inject_plugin_message', ns)
                    manager = SimpleNamespace(_cli_ref=None, has_gateway_message_injector=True,
                        home_path=Path('/explicit-approved-profile'), inject_gateway_message=Mock(return_value=True))
                    ctx = SimpleNamespace(_manager=manager, plugin_id='factory-planning', _gateway_injection_allowed=Mock(return_value=False))
                    kwargs = {'session_key': None, 'origin': self.item['origin']}
                    self.assertFalse(ns['inject_plugin_message'](ctx, 'wake', 'user', **kwargs))
                    manager.inject_gateway_message.assert_not_called()
                    ctx._gateway_injection_allowed.return_value = True
                    self.assertIs(ns['inject_plugin_message'](ctx, 'wake', 'user', **kwargs), True)
                    manager.inject_gateway_message.assert_called_once_with(origin=self.item['origin'],
                        plugin_home=manager.home_path, content='wake', plugin_id='factory-planning')
                else:
                    ns = {'Mapping': Mapping, 'Any': Any}; native_contract('inject_message', ns)
                    seam = ModuleType('hermes_cli.plugins_injection'); seam.inject_plugin_message = Mock(return_value=True)
                    with patch.dict(sys.modules, {'hermes_cli': ModuleType('hermes_cli'), 'hermes_cli.plugins_injection': seam}):
                        self.assertIs(ns['inject_message'](self.ctx, 'wake', role='user', origin=self.item['origin']), True)
                    seam.inject_plugin_message.assert_called_once_with(self.ctx, 'wake', 'user', session_key=None, origin=self.item['origin'])



    def test_service_pending_branch_routes_one_operation_and_holds_next_stage(self):
        from factory_v1 import service_dispatch as dispatch
        service = {'state': self.config['state'], 'operator_id': '42', 'project': 'product', 'iteration': 'm1',
                   'dispatch_config': str(self.fixture.root / 'dispatch.json')}
        config = {k: None for k in dispatch.FIELDS}
        config.update(operator_id='42', project='product', iteration='m1', reference='explicit', roles={}, launchers={},
                      planning_config=str(self.path))
        actual_read = dispatch.publication.read_json
        with patch.object(dispatch.merge, 'controller_paths'), patch.object(dispatch.publication, 'read_json',
                side_effect=lambda p: config if str(p) == service['dispatch_config'] else actual_read(p)):
            result = dispatch.tick(service, self.fixture.github)
        self.assertEqual(result['stage'], 'planning-wakeup')
        self.assertEqual(result['status'], 'requested')
        self.ctx.inject_message.assert_not_called()
        # Both optional routes coexist without making either mandatory for legacy
        # fixed envelopes. Continuation schema has its own component matrix.
        from factory_v1 import continuation_dispatch
        with patch.object(continuation_dispatch, 'validate') as continuation_validate:
            for extras in ({}, {'planning_config': str(self.path)}, {'continuation': {}},
                           {'planning_config': str(self.path), 'continuation': {}}):
                legacy = {k: v for k, v in config.items() if k in dispatch.FIELDS}
                self.assertEqual(dispatch.validate({**legacy, **extras}, service), {**legacy, **extras})
            self.assertEqual(continuation_validate.call_count, 2)
        config['continuation'] = {}
        saved = self.fixture.stored()
        saved['planning']['status'] = 'complete'
        saved['ticket_work'] = {'publication_complete': True}
        repositories.save_iteration(self.fixture.database, saved)
        self.fixture.database.commit()
        with patch.object(dispatch.merge, 'controller_paths'), patch.object(dispatch.publication, 'read_json',
                return_value=config), patch.object(continuation_dispatch, 'validate'), \
                patch.object(wakeup, 'supervise', return_value='enqueued') as supervise, \
                patch.object(dispatch, 'continuation_tick', return_value={'status': 'continued'}) as continued:
            self.assertEqual(dispatch.tick(service, self.fixture.github)['stage'], 'planning-wakeup')
            supervise.assert_called_once()
            continued.assert_not_called()
            saved['planning']['native_wakeup']['status'] = 'consumed'
            repositories.save_iteration(self.fixture.database, saved)
            self.fixture.database.commit()
            self.assertEqual(dispatch.tick(service, self.fixture.github), {'status': 'continued'})
            continued.assert_called_once()


    def test_pinned_gateway_input_slice_reaches_real_strict_hook(self):
        """Pure dispatch/preparation/hook slices; no adapter loop, provider or turn journey."""
        import logging
        from contextlib import asynccontextmanager
        logger = logging.getLogger('pinned-native-contract')
        source = SimpleNamespace(platform=SimpleNamespace(value='telegram'), chat_id='chat',
                                 user_id='42', user_name='Operator')
        events = []
        async def handle(event):
            events.append(event)
        @asynccontextmanager
        async def injection_scope(origin):
            yield
        host = SimpleNamespace(_plugin_injection_accepting=lambda: True,
            _delivery_adapter_for=lambda origin: SimpleNamespace(handle_message=handle),
            _plugin_injection_authorized=lambda *a, **kw: True,
            _session_key_for_source=lambda origin: 'native-key', _plugin_injection_scope=injection_scope)
        dispatch = native_contract('_dispatch_plugin_origin_injection', {
            'SessionSource': Any, 'MessageEvent': SimpleNamespace,
            'MessageType': SimpleNamespace(TEXT='text'), 'logger': logger})
        self.mailbox('enqueued')
        raw = wakeup.message(self.item['planning']['native_wakeup'])
        # All awaited seams are immediate fixtures; drive the exact coroutine
        # without creating an event loop or its socketpair under confinement.
        operation = dispatch(host, source=source, content=raw, plugin_id='factory-planning')
        with self.assertRaises(StopIteration) as completed:
            operation.send(None)
        self.assertTrue(completed.exception.value)
        event, = events
        self.assertIs(event.source, source)
        self.assertEqual(event.source.user_id, '42')
        self.assertTrue(event.internal)
        self.assertFalse(event.allow_gateway_control)
        self.assertEqual(event.metadata, {'hermes_plugin_id': 'factory-planning',
            'hermes_plugin_injection': True, 'gateway_session_key': 'native-key'})
        self.assertEqual(event.text, raw)

        prefix = native_contract('_prefix_inbound_sender_context', {
            'MessageEvent': Any, 'SessionSource': Any, 'Platform': SimpleNamespace(SLACK='slack'),
            'is_shared_multi_user_session': lambda *a, **kw: shared,
            'neutralize_untrusted_inline_text': lambda name: name})
        timestamp = native_contract('_hmwa_apply_message_timestamp', {'logger': logger})
        collect = native_contract('_collect_pre_llm_call_context', {
            'Any': Any, 'Optional': __import__('typing').Optional, 'logger': logger})
        prep_host = SimpleNamespace(config=SimpleNamespace())
        # Timestamp primitives/config are explicit external mocks; native selection and
        # hook bodies remain exact. No auth/config/provider modules are imported.
        run = ModuleType('gateway.run')
        run._load_gateway_config = lambda: {}
        run._message_timestamps_enabled = lambda config: timestamps
        inbound = ModuleType('gateway.run_inbound')
        inbound.strip_discord_triggering_note = lambda event, text: text
        stamps = ModuleType('gateway.message_timestamps')
        stamps.strip_leading_message_timestamps = lambda text, **kw: (text, None)
        stamps.coerce_message_timestamp = lambda value, **kw: 123.0
        stamps.render_user_content_with_timestamp = lambda text, *a, **kw: '[timestamp-fixture] ' + text
        clock = ModuleType('hermes_time'); clock.get_timezone = lambda: None
        lifecycle = ModuleType('hermes_cli.lifecycle')
        hook_calls = []
        errors = []
        def invoke(name, **kwargs):
            self.assertEqual(name, 'pre_llm_call')
            hook_calls.append(kwargs)
            try:
                return [self.binding.context(**kwargs)]
            except repositories.RepositoryError as error:
                errors.append(error.code)
                raise
        lifecycle.invoke_hook = invoke
        spill = ModuleType('tools.hook_output_spill')
        spill.get_spill_config = lambda: {}
        spill.spill_if_oversized = lambda piece, **kw: piece
        modules = {'gateway': ModuleType('gateway'), 'gateway.run': run,
            'gateway.run_inbound': inbound, 'gateway.message_timestamps': stamps,
            'hermes_time': clock, 'hermes_cli': ModuleType('hermes_cli'),
            'hermes_cli.lifecycle': lifecycle, 'tools': ModuleType('tools'), 'tools.hook_output_spill': spill}
        history = [{'role': 'user', 'content': 'Use Python.'}]
        agent = SimpleNamespace(session_id='native-origin', model='fixture-model', _user_id=source.user_id)
        for shared, timestamps, sender in ((False, False, '42'), (False, True, '42'),
                                            (True, True, '42'), (False, True, 'forged')):
            with self.subTest(shared=shared, timestamps=timestamps, sender=sender), patch.dict(sys.modules, modules):
                self.mailbox('enqueued')
                original = copy.deepcopy(self.item['planning']['native_execution'])
                prepared = prefix(prep_host, event, source, event.text)
                rendered, persisted, epoch = timestamp(prep_host, event, prepared)
                selected = {'user_message': rendered, 'persist_user_message': persisted}
                native_contract('original_user_message', selected)
                self.assertEqual(selected['original_user_message'], prepared)
                if timestamps:
                    self.assertTrue(rendered.startswith('[timestamp-fixture] '))
                messages = history + [{'role': 'user', 'content': rendered}]
                agent._user_id = sender
                result = collect(agent, effective_task_id='wake-task', turn_id='wake-turn',
                    original_user_message=selected['original_user_message'], messages=messages,
                    conversation_history=history)
                self.assertEqual(hook_calls[-1]['user_message'], prepared)
                self.assertEqual(hook_calls[-1]['conversation_history'], messages)
                self.assertIsNot(hook_calls[-1]['conversation_history'], messages)
                self.assertEqual(hook_calls[-1]['sender_id'], sender)
                stored = self.fixture.stored()['planning']
                self.assertEqual(stored['native_execution'], original)
                if shared or sender != '42':
                    self.assertEqual(result, '')  # native hook collector logs/refuses failures
                    self.assertEqual(errors[-1], 'scope_mismatch')
                    self.assertEqual(stored['native_wakeup']['status'], 'enqueued')
                    self.assertNotIn('execution_turn', stored['native_wakeup'])
                else:
                    self.assertTrue(result)
                    self.assertEqual(stored['native_wakeup']['status'], 'running')
                    self.assertEqual(stored['native_wakeup']['execution_turn'],
                                     {'task_id': 'wake-task', 'turn_id': 'wake-turn'})
