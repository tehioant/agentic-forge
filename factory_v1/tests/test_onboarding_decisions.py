"""Direct pending choices; no worker/setup journey or external effects."""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, create_autospec, patch

from factory_v1 import attention
from factory_v1.conversation_enrollment import ConversationEnrollment, DraftStore
from factory_v1.onboarding_decisions import CAUSES, OnboardingDecisions
from factory_v1.repositories import RepositoryError


class OnboardingDecisionTests(unittest.TestCase):
    def test_scoped_pending_action_decisions_and_native_dispatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drafts = root / 'drafts'; drafts.mkdir(mode=0o700)
            ctx = Mock(profile_name='test')
            enrollment = object.__new__(ConversationEnrollment)
            enrollment.ctx = ctx
            enrollment.config = {'operators': {'discord': ['42']}}
            enrollment.store = DraftStore(drafts)
            enrollment.bound_planning = None
            source = {'platform': 'discord', 'chat_id': '123', 'thread_id': '123',
                      'parent_chat_id': '456', 'scope_id': '789', 'chat_type': 'group', 'profile': '', 'user_id': '42'}
            identity, draft_id = enrollment.identity(source, 'discord')
            base = {'draft_id': draft_id, **identity, 'observations': [
                {'message_id': 'm1', 'session_key': 'key', 'source': source, 'text': 'pending'}],
                'revision': 1, 'native': None, 'transfer': None}
            operation = {'operation_id': 'attach', 'project': 'atlas', 'scope': {'repository': 'owner/atlas'}, 'ceiling': 10}
            blockers = [{'decision_id': 'repository', 'cause': 'permission', 'scope': {'repository': 'owner/atlas'},
                         'ceiling': 0, 'question': 'Attach owner/atlas to Atlas?'},
                        {'decision_id': 'budget', 'cause': 'allowance', 'scope': {'model': 'reviewed-model'},
                         'ceiling': 10, 'question': 'Request ten units for the reviewed model through the trusted route?'}]
            ready = False

            def dispatch(name, args, **kwargs):
                self.assertEqual(args, {'draft_id': draft_id, 'operation': operation})
                self.assertEqual(kwargs, {'session_id': 'session'})
                if name == 'factory_project_setup_status':
                    retained = enrollment.store.read(draft_id)['onboarding_decisions']['attach']['blockers']
                    return json.dumps({'operation': operation, 'ready': ready,
                                       'pending': [] if ready else [b['decision_id'] for b in retained]})
                self.assertEqual(name, 'factory_project_setup_resume')
                return json.dumps({'operation': operation, 'status': 'complete'})

            ctx.dispatch_tool.side_effect = dispatch
            adapter = OnboardingDecisions(enrollment, {'state': None})

            @contextmanager
            def host(**changes):
                values = {**source, 'id': 'session', 'key': 'key', 'message_id': 'm1', **changes}
                tokens = [(v := ContextVar('HERMES_SESSION_' + k.upper()), v.set(value)) for k, value in values.items()]
                try:
                    yield
                finally:
                    for var, token in reversed(tokens):
                        var.reset(token)

            def seed(selected=None):
                enrollment.store.save(copy.deepcopy(base))
                adapter.request(draft_id, operation, blockers if selected is None else selected)

            def present(message_id='m1'):
                with host(message_id=message_id):
                    return adapter.context('session')['context']

            def admit(raw, message_id='m2'):
                adapter.inbound(source=source, platform='discord', session_key='key', message_id=message_id, text=raw)

            seed()
            retained = adapter.request(draft_id, operation, blockers)
            self.assertEqual(retained, adapter.request(draft_id, operation, blockers))
            with self.assertRaises(RepositoryError):
                adapter.request(draft_id, {**operation, 'scope': {'repository': 'other/repo'}}, blockers)
            for cause in CAUSES:
                with self.subTest(message=cause):
                    message = adapter.message({'blockers': [{**blockers[0], 'cause': cause}]})
                    self.assertIn(CAUSES[cause][0], message)
                    self.assertIn(CAUSES[cause][1], message)
                    self.assertIn('held', message)
                    self.assertNotIn('decision_id', message)
            for label, supplied, changes in (
                ('affirmative-chat', 'yes', {}), ('ack', 'OK', {}),
                ('unrelated', 'I approve the weather forecast', {}),
                ('ambiguous-batch', 'approve', {}), ('duplicate', '1 approve, 1 reject', {}),
                ('unknown-choice', '3 approve', {}), ('json', 'forge-decision {"answers": []}', {}),
                ('speaker', '1 approve', {'user_id': '99'}),
                ('wrong-message', '1 approve', {'message_id': 'unobserved'}),
            ):
                with self.subTest(response=label):
                    seed(); present(); admit(supplied)
                    before = enrollment.store.read(draft_id)
                    with host(message_id='m2', **changes) if 'message_id' not in changes else host(**changes):
                        with self.assertRaises(RepositoryError):
                            adapter.respond({}, session_id='session')
                    self.assertEqual(enrollment.store.read(draft_id), before)
            seed(); rendered = present()
            self.assertIn('1. For atlas:', rendered)
            self.assertIn('2. For atlas:', rendered)
            self.assertNotIn('operation_id', rendered)
            self.assertNotIn('forge-decision', rendered)
            admit('1 approve')
            with host(message_id='m2'):
                # pre-LLM refresh must retain the exact displayed map for this answer.
                self.assertEqual(present('m2'), rendered)
                with self.assertRaises(RepositoryError):
                    adapter.respond({'direction': 'approve'}, session_id='session')
                partial = json.loads(adapter.respond({}, session_id='session'))['results'][0]
                with self.assertRaises(RepositoryError):
                    adapter.respond({}, session_id='session')  # consumed message cannot replay
            self.assertFalse(partial['resumed'])
            self.assertEqual(set(partial['responses']), {'repository'})
            self.assertEqual(partial['responses']['repository']['scope'], blockers[0]['scope'])
            self.assertEqual(partial['responses']['repository']['ceiling'], 0)
            self.assertNotIn(blockers[0]['question'], partial['message'])
            self.assertIn(blockers[1]['question'], partial['message'])
            self.assertFalse(any(c.args[0] == 'factory_project_setup_resume' for c in ctx.dispatch_tool.call_args_list))
            self.assertEqual(adapter.request(draft_id, operation, blockers)['responses'], partial['responses'])
            present('m2'); admit('approve', 'm3')
            with host(message_id='m3'):
                second = json.loads(adapter.respond({}, session_id='session'))['results'][0]
            self.assertEqual(set(second['responses']), {'repository', 'budget'})
            self.assertFalse(second['resumed'])  # direction did not create allowance/access
            ready = True
            result = adapter.resume(draft_id, 'attach', session_id='session')
            self.assertEqual(result['operation'], operation)
            self.assertTrue(result['resumed'])
            self.assertFalse(adapter.resume(draft_id, 'attach', session_id='session')['resumed'])
            self.assertEqual(sum(c.args[0] == 'factory_project_setup_resume' for c in ctx.dispatch_tool.call_args_list), 1)
            with host(message_id='m3'):
                self.assertIsNone(adapter.context('session'))
            seed()
            with host():
                self.assertIsNone(adapter.context('session'))  # approved host access reused quietly
            ready = False
            seed(); present(); admit('1 approve; 2 reject')
            ready = True  # Host restoration must not race ahead of a pending rejection.
            calls_before = sum(c.args[0] == 'factory_project_setup_resume' for c in ctx.dispatch_tool.call_args_list)
            with host(message_id='m2'):
                adapter.context('session')
                self.assertEqual(sum(c.args[0] == 'factory_project_setup_resume' for c in ctx.dispatch_tool.call_args_list), calls_before)
                rejected = json.loads(adapter.respond({}, session_id='session'))['results'][0]
            ready = True
            self.assertEqual(adapter.resume(draft_id, 'attach', session_id='session')['reason'], 'operator_declined')
            self.assertFalse(rejected['resumed'])
            ready = False
            for stale in ('no-prompt', 'intervening-chat', 'revision', 'operation', 'ceiling'):
                seed(blockers[:1])
                if stale != 'no-prompt':
                    present()
                if stale == 'intervening-chat':
                    admit('unrelated message', 'middle')
                admit('approve')
                if stale in {'revision', 'operation', 'ceiling'}:
                    value = enrollment.store.read(draft_id)
                    if stale == 'revision':
                        value['revision'] += 1
                    elif stale == 'operation':
                        value['onboarding_decisions']['attach']['operation']['scope'] = {'repository': 'other/repo'}
                    else:
                        value['onboarding_decisions']['attach']['blockers'][0]['ceiling'] = 1
                    enrollment.store.save(value)
                with self.subTest(stale=stale), host(message_id='m2'), self.assertRaises(RepositoryError):
                    adapter.respond({}, session_id='session')
            seed([{**blockers[0], 'cause': 'uncertain'}]); present(); admit('approve')
            with host(message_id='m2'), self.assertRaises(RepositoryError):
                adapter.respond({}, session_id='session')
            # Existing exact-origin outbox/respond and independent pause, directly seeded.
            state = root / 'state.sqlite'
            origin = {k: source[k] for k in ('platform', 'chat_id', 'thread_id', 'parent_chat_id', 'scope_id')}
            item = {'project_id': 'atlas', 'iteration_id': 'm1', 'repository': 'owner/atlas', 'origin': origin,
                    'approval': {'operator_id': '42', 'reference': 'trusted-registration'}, 'status': 'active',
                    'correlation': {'project_id': 'atlas', 'iteration_id': 'm1', 'control_run_id': 'control',
                                    'evidence_id': 'evidence', 'stage': 'setup'}}
            with sqlite3.connect(state) as database:
                database.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
                database.execute('INSERT INTO iterations VALUES (?,?,?)', ('atlas', 'm1', json.dumps(item)))
            adapter = OnboardingDecisions(enrollment, {'state': str(state)})
            enrollment.store.save(copy.deepcopy(base))
            n = {'project': 'atlas', 'iteration': 'm1', 'issue_number': 1, 'run_id': 'setup-run', 'evidence_ids': ['evidence']}
            adapter.request(draft_id, operation, blockers, notification=n)
            adapter.request(draft_id, operation, blockers, notification=n)
            with sqlite3.connect(state) as database:
                records = attention.records(database, 'atlas', 'm1', item)
                self.assertEqual(len(records), 2)
                self.assertTrue(all(r['attempts'] == 0 and r['destination'] == origin for r in records))
            present(); admit('1 approve')
            with host(message_id='m2'):
                adapter.respond({}, session_id='session')
            with sqlite3.connect(state) as database:
                records = attention.records(database, 'atlas', 'm1', item)
                self.assertEqual(sum(r['decision_response'] is not None for r in records), 1)
                self.assertEqual(database.execute('SELECT payload FROM iterations').fetchone()[0], json.dumps(item))
            ready = True
            with patch('factory_v1.onboarding_decisions.controls.require_active', side_effect=RepositoryError('iteration_paused', 'pause')):
                with self.assertRaises(RepositoryError):
                    adapter.resume(draft_id, 'attach', session_id='session')
            # Plugin registration unchanged; no approval selector or answer fields exposed.
            ctx.get_config.side_effect = lambda key: {'enrollment': {}, 'onboarding_decisions': {'state': None}}.get(key)
            entry = Path(__file__).parents[2] / 'integrations/hermes-planning/__init__.py'
            spec = importlib.util.spec_from_file_location('decisions_plugin', entry)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            with patch('factory_v1.conversation_enrollment.ConversationEnrollment', return_value=enrollment):
                module.register(ctx)
            self.assertEqual([c.args[0] for c in ctx.register_hook.call_args_list], ['post_gateway_admission', 'pre_llm_call'])
            response_tool = next(c.kwargs for c in ctx.register_tool.call_args_list if c.kwargs['name'] == 'factory_project_setup_response')
            self.assertEqual(response_tool['schema']['parameters']['properties'], {})
            # T2 final API signatures: autospec denies accidental argument drift.
            class SetupBoundary:
                def capability(self, draft, repository): pass
                def read(self, draft_id): pass
                def attach(self, draft_id, expected_revision, repository, *, session_id): pass
                def route(self, draft_id): pass
            setup = create_autospec(SetupBoundary, instance=True, spec_set=True)
            setup.read.return_value = None
            setup.attach.return_value = {'status': 'ready'}
            enrollment.store.save(copy.deepcopy(base))
            concrete = OnboardingDecisions(enrollment, {'state': None}, setup=setup)
            exact = {**operation, 'scope': {'repository': 'owner/atlas', 'expected_revision': 1}}
            concrete.request(draft_id, exact, blockers[:1])
            concrete.resume(draft_id, 'attach', session_id='session')
            self.assertEqual(setup.capability.call_args.args[0]['operator_id'], '42')
            self.assertEqual(setup.capability.call_args.args[1], 'owner/atlas')
            setup.attach.assert_called_once_with(draft_id, 1, 'owner/atlas', session_id='session')
            self.assertFalse(concrete.resume(draft_id, 'attach', session_id='session')['resumed'])
            setup.read.return_value = {'status': 'ready', 'repository': 'other/repo', 'draft_revision': 1}
            with self.assertRaises(RepositoryError):
                concrete.reconcile(draft_id, 'attach')
            setup.read.return_value['repository'] = 'owner/atlas'
            self.assertTrue(concrete.reconcile(draft_id, 'attach')['attempt']['reconciled'])
            setup.route.assert_called_once_with(draft_id)
            enrollment.store.save(copy.deepcopy(base))
            setup.read.return_value = None
            setup.attach.reset_mock()
            setup.attach.side_effect = RuntimeError('lost response')
            concrete.request(draft_id, exact, blockers[:1])
            with self.assertRaises(RuntimeError):
                concrete.resume(draft_id, 'attach', session_id='session')
            self.assertEqual(enrollment.store.read(draft_id)['onboarding_decisions']['attach']['attempt'], {'state': 'uncertain'})
            self.assertFalse(concrete.resume(draft_id, 'attach', session_id='session')['resumed'])
            setup.attach.assert_called_once()
            # Product decision is an understandable recommendation, not configuration work.
            ready = False
            seed([{**blockers[0], 'cause': 'product', 'question': 'Recommend a read-only first milestone. Use that scope?'}])
            self.assertIn('Recommend a read-only first milestone', present())
            admit('reject')
            with host(message_id='m2'):
                self.assertEqual(json.loads(adapter.respond({}, session_id='session'))['results'][0]['reason'], 'operator_declined')
