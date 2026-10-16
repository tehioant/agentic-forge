"""Direct pending-action checks; native sender and producer authority are mocked."""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

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
                         'ceiling': 0, 'question': 'Attach this repository?'},
                        {'decision_id': 'budget', 'cause': 'allowance', 'scope': {'model': 'reviewed-model'},
                         'ceiling': 10, 'question': 'Request ten units through the trusted route?'}]
            ready = False

            def dispatch(name, args, **kwargs):
                self.assertEqual(args, {'draft_id': draft_id, 'operation': operation})
                self.assertEqual(kwargs, {'session_id': 'session'})
                if name == 'factory_project_setup_status':
                    return json.dumps({'operation': operation, 'ready': ready,
                                       'pending': [] if ready else ['repository', 'budget']})
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

            def seed(raw='pending'):
                value = copy.deepcopy(base); value['observations'][0]['text'] = raw
                enrollment.store.save(value)
                adapter.request(draft_id, operation, blockers)

            def raw(answers, op=None):
                return 'forge-decision ' + json.dumps({'operation': operation if op is None else op, 'answers': answers})

            answer = {'decision_id': 'repository', 'scope': blockers[0]['scope'], 'ceiling': 0,
                      'direction': 'Approve attaching only owner/atlas.'}
            seed()
            retained = adapter.request(draft_id, operation, blockers)
            self.assertEqual(retained, adapter.request(draft_id, operation, blockers))
            with self.assertRaises(RepositoryError):
                adapter.request(draft_id, {**operation, 'scope': {'repository': 'other/repo'}}, blockers)
            for cause in CAUSES:
                with self.subTest(message=cause):
                    b = {**blockers[0], 'cause': cause}
                    message = adapter.message({'blockers': [b]})
                    self.assertIn(CAUSES[cause][0], message)
                    self.assertIn(CAUSES[cause][1], message)
                    self.assertIn('held', message)
            for label, supplied, host_changes in (
                ('affirmative-chat', 'yes', {}),
                ('acknowledgement', raw([{**answer, 'direction': ' OK '}]), {}),
                ('other-project', raw([answer], {**operation, 'project': 'other'}), {}),
                ('other-operation', raw([answer], {**operation, 'operation_id': 'other'}), {}),
                ('scope', raw([{**answer, 'scope': {'repository': 'other/repo'}}]), {}),
                ('ceiling', raw([{**answer, 'ceiling': 1}]), {}),
                ('untrusted-speaker', raw([answer]), {'user_id': '99'}),
                ('wrong-message', raw([answer]), {'message_id': 'unobserved'}),
            ):
                with self.subTest(response=label):
                    seed(supplied)
                    before = enrollment.store.read(draft_id)
                    with host(**host_changes), self.assertRaises(RepositoryError):
                        adapter.respond({'operation_id': 'attach'}, session_id='session')
                    self.assertEqual(enrollment.store.read(draft_id), before)
            seed(raw([answer]))
            with host():
                partial = json.loads(adapter.respond({'operation_id': 'attach'}, session_id='session'))
            self.assertFalse(partial['resumed'])
            self.assertEqual(set(partial['responses']), {'repository'})
            self.assertNotIn('repository: ', partial['message'])
            self.assertIn('budget: ', partial['message'])
            self.assertFalse(any(c.args[0] == 'factory_project_setup_resume' for c in ctx.dispatch_tool.call_args_list))
            self.assertEqual(adapter.request(draft_id, operation, blockers)['responses'], partial['responses'])
            ready = True  # Actual trusted producer read, never the conversational response.
            result = adapter.resume(draft_id, 'attach', session_id='session')
            self.assertEqual(result['operation'], operation)
            self.assertTrue(result['resumed'])

            self.assertFalse(adapter.resume(draft_id, 'attach', session_id='session')['resumed'])
            self.assertEqual(sum(c.args[0] == 'factory_project_setup_resume' for c in ctx.dispatch_tool.call_args_list), 1)
            with host():
                self.assertIsNone(adapter.context('session'))  # successful setup is quiet
            seed()
            with host():
                self.assertIsNone(adapter.context('session'))  # approved authority: no repetitive question
            self.assertEqual(sum(c.args[0] == 'factory_project_setup_resume' for c in ctx.dispatch_tool.call_args_list), 2)
            ready = False
            seed()
            uncertain = [{**blockers[0], 'cause': 'uncertain'}]
            value = copy.deepcopy(base); value['observations'][0]['text'] = raw([answer])
            enrollment.store.save(value); adapter.request(draft_id, operation, uncertain)
            with host(), self.assertRaises(RepositoryError):
                adapter.respond({'operation_id': 'attach'}, session_id='session')
            # Existing exact-origin outbox/respond and unrelated holds, seeded directly.
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
            registered = adapter.request(draft_id, operation, blockers, notification=n)
            adapter.request(draft_id, operation, blockers, notification=n)
            with sqlite3.connect(state) as database:
                records = attention.records(database, 'atlas', 'm1', item)
                self.assertEqual(len(records), 2)
                self.assertTrue(all(r['attempts'] == 0 and r['destination'] == origin for r in records))
            value = enrollment.store.read(draft_id); value['observations'][0]['text'] = raw([answer]); enrollment.store.save(value)
            with host():
                adapter.respond({'operation_id': 'attach'}, session_id='session')
            with sqlite3.connect(state) as database:
                records = attention.records(database, 'atlas', 'm1', item)
                self.assertEqual(sum(r['decision_response'] is not None for r in records), 1)
                self.assertEqual(database.execute('SELECT payload FROM iterations').fetchone()[0], json.dumps(item))
            ready = True
            with patch('factory_v1.onboarding_decisions.controls.require_active', side_effect=RepositoryError('iteration_paused', 'pause')):
                with self.assertRaises(RepositoryError):
                    adapter.resume(draft_id, 'attach', session_id='session')
            # Production plugin wiring replaces, rather than duplicates, enrollment hooks.
            ctx.get_config.side_effect = lambda key: {'enrollment': {}, 'onboarding_decisions': {'state': None}}.get(key)
            entry = Path(__file__).parents[2] / 'integrations/hermes-planning/__init__.py'
            spec = importlib.util.spec_from_file_location('decisions_plugin', entry)
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            with patch('factory_v1.conversation_enrollment.ConversationEnrollment', return_value=enrollment):
                module.register(ctx)
            self.assertEqual([c.args[0] for c in ctx.register_hook.call_args_list], ['post_gateway_admission', 'pre_llm_call'])
            self.assertEqual({c.kwargs['name'] for c in ctx.register_tool.call_args_list},
                             {'factory_project_draft_update', 'factory_project_setup_response'})
            # Concrete T2 adapter: direct seeded action, mocked host access/attachment only.
            enrollment.store.save(copy.deepcopy(base))
            setup = Mock()
            setup.read.return_value = None
            setup.attach.return_value = {'status': 'ready'}
            concrete = OnboardingDecisions(enrollment, {'state': None}, setup=setup)
            exact = {**operation, 'scope': {'repository': 'owner/atlas', 'expected_revision': 1}}
            concrete.request(draft_id, exact, blockers[:1])
            concrete.resume(draft_id, 'attach', session_id='session')
            self.assertEqual(setup.capability.call_args.args[0]['operator_id'], '42')
            self.assertEqual(setup.capability.call_args.args[1], 'owner/atlas')
            setup.attach.assert_called_once_with(draft_id, 1, 'owner/atlas', session_id='session')
