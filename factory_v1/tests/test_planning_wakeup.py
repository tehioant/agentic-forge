"""Direct mailbox/native contract tests; explicit records, no journey or running tasks."""
import ast
import copy
import json
from pathlib import Path
import time
from types import SimpleNamespace, ModuleType
import sys
import unittest
from unittest.mock import Mock, patch
from typing import Mapping, Any

from factory_v1 import assignments, planning, planning_wakeup as wakeup, repositories
from factory_v1.tests import test_conversation_binding as binding_tests


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

    def test_supervisor_commits_one_request_and_deduplicates_before_transport(self):
        self.assertEqual(self.supervise(), 'requested')
        before = self.fixture.stored()
        self.assertEqual(self.supervise(), 'requested')
        self.assertEqual(self.fixture.stored(), before)
        self.ctx.inject_message.assert_not_called()

    def test_native_enqueue_reads_durable_unknown_before_call_and_never_advances(self):
        self.mailbox()
        def enqueue(content, **kwargs):
            stored = self.fixture.stored()['planning']
            self.assertEqual(stored['native_wakeup']['status'], 'enqueue_unknown')
            self.assertEqual(content, wakeup.message(stored['native_wakeup']))
            self.assertEqual(kwargs, {'role': 'user', 'origin': self.item['origin']})
            return True
        self.ctx.inject_message.side_effect = enqueue
        self.native.step(); self.native.step()
        self.ctx.inject_message.assert_called_once()
        self.assertEqual(self.supervise(), 'enqueued')
        self.assertEqual(self.fixture.stored()['planning']['status'], 'pending')
        self.assertNotIn('handoff', self.fixture.stored())

    def test_false_exception_and_unknown_outcome_never_blind_retry(self):
        for result in (False, RuntimeError('unknown outcome')):
            self.mailbox()
            self.ctx.inject_message.reset_mock()
            self.ctx.inject_message.side_effect = result if isinstance(result, Exception) else None
            self.ctx.inject_message.return_value = result
            self.native.step(); self.native.step()
            self.ctx.inject_message.assert_called_once()
            expected = 'enqueue_unknown' if isinstance(result, Exception) else 'rejected'
            self.assertEqual(self.supervise(), expected)

    def test_changed_scope_source_session_allowance_profile_refuses_before_enqueue(self):
        self.mailbox()
        original = copy.deepcopy(self.approval)
        for key, value in (('assignment_id', 'wrong'), ('profile_name', 'other'), ('source_digest', 'wrong'),
                           ('binding', {**self.config, 'session_id': 'wrong'}),
                           ('allowance', {**original['allowance'], 'max_wakeups': 2}),
                           ('allowance', {**original['allowance'], 'expires_at': 1})):
            self.approval = {**copy.deepcopy(original), key: value}; self.write_approval()
            with self.subTest(key=key, value=value), self.assertRaises(repositories.RepositoryError):
                self.native.step()
        self.ctx.inject_message.assert_not_called()

    def test_missing_authorization_and_stop_pause_refuse(self):
        self.mailbox()
        self.native.stop(); self.native.step()
        self.ctx.inject_message.assert_not_called()
        self.native.stopped = False
        self.item['status'] = 'paused'; self.fixture.persist()
        with self.assertRaises(repositories.RepositoryError): self.native.step()
        self.path.unlink()
        with self.assertRaises((OSError, repositories.RepositoryError)): self.native.step()
        self.ctx.inject_message.assert_not_called()

    def test_wakeup_hook_preserves_original_answers_run_and_sufficiency(self):
        self.mailbox('enqueued')
        original = copy.deepcopy(self.item['planning']['native_execution'])
        args = {'session_id': 'native-origin', 'task_id': 'wake-task', 'turn_id': 'wake-turn', 'sender_id': '42', 'model': 'fixture-model',
                'user_message': wakeup.message(self.item['planning']['native_wakeup']),
                'conversation_history': [{'role': 'user', 'content': 'Use Python.'}]}
        self.binding.context(**args); self.binding.context(**args)
        self.assertEqual(self.fixture.stored()['planning']['native_execution'], original)
        self.assertEqual(self.fixture.stored()['planning']['native_wakeup']['execution_turn'],
                         {'task_id': 'wake-task', 'turn_id': 'wake-turn'})

    def test_wakeup_requires_actual_host_sender_history_exact_request_and_turn(self):
        self.mailbox('enqueued')
        args = {'session_id': 'native-origin', 'task_id': 'wake-task', 'turn_id': 'wake-turn', 'sender_id': '42', 'model': 'fixture-model',
                'user_message': wakeup.message(self.item['planning']['native_wakeup']),
                'conversation_history': [{'role': 'user', 'content': 'Use Python.'}]}
        for change in ({'sender_id': 'forged'}, {'conversation_history': []}, {'session_id': 'other'},
                       {'user_message': 'host timestamp prefix ' + args['user_message']},
                       {'user_message': wakeup.TAG + 'forged]'}, {'turn_id': ''}):
            with self.subTest(change=change):
                if change == {'session_id': 'other'}:
                    self.assertIsNone(self.binding.context(**{**args, **change}))
                else:
                    with self.assertRaises(repositories.RepositoryError): self.binding.context(**{**args, **change})
        self.assertEqual(self.fixture.stored()['planning']['native_wakeup']['status'], 'enqueued')

    def test_correlated_completion_consumes_only_after_fresh_immutable_readback(self):
        self.complete_record()
        with patch.object(planning, 'read_publication', return_value=copy.deepcopy(self.item['handoff'])) as read:
            self.assertEqual(self.supervise(), 'consumed')
            self.assertEqual(self.supervise(), 'consumed')
        read.assert_called_once()
        self.assertEqual(self.fixture.stored()['planning']['native_wakeup']['receipt']['run_id'], 'actual-host-run-fixture')
        self.ctx.inject_message.assert_not_called()

    def test_missing_wrong_receipts_and_readback_drift_never_consume(self):
        self.complete_record()
        original = copy.deepcopy(self.item)
        for mutate in (lambda w: w['native_execution'].update(run_id='wrong'),
                       lambda w: w.pop('host_publication'),
                       lambda w: w['host_publication'].update(status='unverified'),
                       lambda w: w['native_wakeup'].pop('execution_turn'),
                       lambda w: w.pop('completion_digest')):
            self.item = copy.deepcopy(original); mutate(self.item['planning']); self.fixture.item = self.item; self.fixture.persist()
            with self.assertRaises(repositories.RepositoryError): self.supervise()
        self.item = copy.deepcopy(original); self.fixture.item = self.item; self.fixture.persist()
        with patch.object(planning, 'read_publication', return_value={'wrong': 'handoff'}), self.assertRaises(repositories.RepositoryError):
            self.supervise()
        self.assertEqual(self.fixture.stored()['planning']['native_wakeup']['status'], 'running')

    def test_lifecycle_unavailable_is_explicit_and_unload_cancels_owned_task(self):
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

    def test_completion_drift_during_final_readback_cannot_consume(self):
        self.complete_record()
        handoff = copy.deepcopy(self.item['handoff'])
        def read(*args):
            self.item['planning']['host_publication']['status'] = 'revoked'
            self.fixture.persist()
            return handoff
        with patch.object(planning, 'read_publication', side_effect=read), self.assertRaises(repositories.RepositoryError):
            self.supervise()
        self.assertEqual(self.fixture.stored()['planning']['native_wakeup']['status'], 'running')

    def test_actual_native_host_spawn_body_and_bounded_join(self):
        import concurrent.futures
        source = Path('/home/ops/.hermes/hermes-agent/hermes_cli/plugin_host_child.py')
        tree = ast.parse(source.read_bytes())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'HostRuntime')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'spawn')
        future = concurrent.futures.Future()
        scheduler = Mock(return_value=future)
        ns = {'Any': Any, 'Optional': __import__('typing').Optional, 'inspect': __import__('inspect'),
              'asyncio': SimpleNamespace(run_coroutine_threadsafe=scheduler),
              '_on_behalf_of': lambda coro, request: coro, 'serving_request': lambda: 'native-host-request'}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), ns)
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

    def test_actual_native_injection_router_rechecks_grant_and_same_origin(self):
        import logging
        source = Path('/home/ops/.hermes/hermes-agent/hermes_cli/plugins_injection.py')
        tree = ast.parse(source.read_bytes())
        method = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'inject_plugin_message')
        ns = {'Any': Any, 'Optional': __import__('typing').Optional, 'Mapping': Mapping,
              'logger': logging.getLogger('native-body-probe')}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), ns)
        manager = SimpleNamespace(_cli_ref=None, has_gateway_message_injector=True,
                                  home_path=Path('/explicit-approved-profile'), inject_gateway_message=Mock(return_value=True))
        ctx = SimpleNamespace(_manager=manager, plugin_id='factory-planning', _gateway_injection_allowed=Mock(return_value=False))
        kwargs = {'session_key': None, 'origin': self.item['origin']}
        self.assertFalse(ns[method.name](ctx, 'wake', 'user', **kwargs))
        manager.inject_gateway_message.assert_not_called()
        ctx._gateway_injection_allowed.return_value = True
        self.assertTrue(ns[method.name](ctx, 'wake', 'user', **kwargs))
        manager.inject_gateway_message.assert_called_once_with(origin=self.item['origin'],
            plugin_home=manager.home_path, content='wake', plugin_id='factory-planning')

    def test_actual_installed_inject_body_preserves_origin_and_boolean_semantics(self):
        # Extract only the real method body: no Hermes/auth/provider imports.
        source = Path('/home/ops/.hermes/hermes-agent/hermes_cli/plugins.py')
        if not source.exists(): self.skipTest('installed native source unavailable')
        tree = ast.parse(source.read_bytes())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PluginContext')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'inject_message')
        ns = {'Mapping': Mapping, 'Any': Any}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), ns)
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
