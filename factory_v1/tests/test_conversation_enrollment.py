"""Focused enrollment transitions; fixture host IDs are not live native provenance."""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_v1 import skills
from factory_v1.assignments import digest
from factory_v1.conversation_enrollment import ConversationEnrollment, PRODUCT_FIELDS
from factory_v1.repositories import RepositoryError


class ConversationEnrollmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = self.root / 'drafts'
        self.store.mkdir(mode=0o700)
        installed = self.root / 'installed'
        for name in skills.INSTALLED_PINS:
            target = installed / name / 'SKILL.md'
            target.parent.mkdir(parents=True)
            target.write_bytes((Path(__file__).parent / 'planning_skill_snapshots' / (name + '.md')).read_bytes())
        patcher = patch.object(skills, 'INSTALLED_ROOT', installed)
        patcher.start(); self.addCleanup(patcher.stop)
        self.config = {'root': str(self.store), 'operators': {'discord': ['42']}, 'skills': [
            {'name': name, 'path': str(installed / name / 'SKILL.md'), 'sha256': pin}
            for name, pin in skills.INSTALLED_PINS.items()]}
        self.ctx = Mock(profile_name='test-profile')
        self.ctx.get_config.side_effect = lambda key: self.config if key == 'enrollment' else None
        self.selected = skills.resolve_installed(self.config['skills'])
        self.ctx.dispatch_tool.side_effect = lambda tool, args, **kwargs: json.dumps({
            'success': True, 'name': args['name'], 'content': self.selected[args['name']]['instructions']})
        entry = Path(__file__).parents[2] / 'integrations/hermes-planning/__init__.py'
        spec = importlib.util.spec_from_file_location('enrollment_test_plugin', entry)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.adapter = module.register(self.ctx)
        self.hooks = dict(c.args for c in self.ctx.register_hook.call_args_list)
        self.tool = self.ctx.register_tool.call_args.kwargs['handler']
        self.source = {'platform': 'discord', 'chat_id': 'channel', 'chat_type': 'group',
                       'thread_id': 'thread', 'scope_id': 'guild', 'parent_chat_id': 'parent',
                       'profile': '', 'user_id': '42', 'user_name': 'Operator'}
        self.raw = 'Build Atlas for readers: save notes; first milestone search; offline only; verify search works.'
        self.event = {'session_key': 'shared-key', 'platform': 'discord', 'source': self.source,
                      'message_id': 'message-1', 'text': self.raw}
        self.draft_id = self.adapter.identity(self.source, 'discord')[1]
        self.native = {'session_id': 'native-session', 'task_id': 'task', 'turn_id': 'turn',
                       'sender_id': '42', 'user_message': '[Operator] ' + self.raw}

    @contextmanager
    def host(self, **changes):
        values = {k: self.source.get(k, '') for k in ('platform', 'chat_id', 'chat_type', 'thread_id',
                  'scope_id', 'parent_chat_id', 'profile', 'user_id')}
        values.update(key='shared-key', message_id='message-1', id='native-session')
        values.update(changes)
        tokens = [(var := ContextVar('HERMES_SESSION_' + k.upper()), var.set(v)) for k, v in values.items()]
        try:
            yield
        finally:
            for var, token in reversed(tokens):
                var.reset(token)

    def draft(self):
        return self.adapter.store.read(self.draft_id)

    def test_identity_admission_and_native_turn(self):
        self.assertEqual(set(self.hooks), {'post_gateway_admission', 'pre_llm_call'})
        self.assertEqual(self.ctx.register_tool.call_args.kwargs['name'], 'factory_project_draft_update')
        for label, changes in (
            ('missing-user', {'source': {**self.source, 'user_id': ''}}),
            ('shared-other-speaker', {'source': {**self.source, 'user_id': '99'}}),
            ('unsupported-platform', {'platform': 'slack'}),
            ('missing-reference', {'message_id': ''}),
            ('silence', {'text': ' '}),
        ):
            with self.subTest(admission=label):
                result = self.hooks['post_gateway_admission'](**{**self.event, **changes})
                self.assertEqual(result['action'], 'handled')
                self.assertIsNone(self.draft())
                self.ctx.dispatch_tool.assert_not_called()
        self.assertIsNone(self.hooks['post_gateway_admission'](**self.event))
        before = self.draft()
        for label, changes, payload in (
            ('other-speaker', {'user_id': '99'}, {}),
            ('other-message', {'message_id': 'other'}, {}),
            ('other-origin', {'thread_id': 'other'}, {}),
            ('wrong-native-session', {'id': 'other'}, {}),
            ('wrong-sender', {}, {'sender_id': '99'}),
            ('wrong-native-input', {}, {'user_message': 'yes'}),
        ):
            with self.subTest(correlation=label), self.host(**changes):
                with self.assertRaises(RepositoryError):
                    self.hooks['pre_llm_call'](**{**self.native, **payload})
                self.assertEqual(self.draft(), before)
        with patch.dict(os.environ, {'HERMES_SESSION_KEY': 'shared-key', 'HERMES_SESSION_ID': 'native-session',
                                    'HERMES_SESSION_USER_ID': '42', 'HERMES_SESSION_MESSAGE_ID': 'message-1'}):
            with self.assertRaises(RepositoryError):
                self.hooks['pre_llm_call'](**self.native)
        with self.host():
            result = self.hooks['pre_llm_call'](**self.native)
        for name in ('grill-me', 'grilling'):
            self.assertIn(self.selected[name]['instructions'], result['context'])
        self.assertIn(self.raw, result['context'])
        self.assertIn('Recommend defaults', result['context'])
        self.assertEqual([c.args[1]['name'] for c in self.ctx.dispatch_tool.call_args_list], ['grill-me', 'grilling'])
        self.assertTrue(all(c.kwargs == {'session_id': 'native-session'} for c in self.ctx.dispatch_tool.call_args_list))
        self.assertEqual(self.draft()['observations'][0]['source'], self.source)
        self.assertNotIn('repository', self.draft())
        self.assertNotIn('approval', self.draft())
        # Both options coexist without routing the existing planning session through enrollment.
        self.adapter.bound_planning = {'origin': {'platform': 'discord', 'chat_id': 'channel'}, 'session_id': 'bound'}
        self.assertIsNone(self.adapter.inbound(**self.event))
        self.assertIsNone(self.adapter.context('bound'))

    def test_draft_resumption_and_correlated_product_updates(self):
        # Seed minimal admitted/native records directly: this is not a simulated interview journey.
        identity, draft_id = self.adapter.identity(self.source, 'discord')
        base = {'version': 1, 'draft_id': draft_id, **identity, 'revision': 3,
                'observations': [{'message_id': 'message-1', 'session_key': 'shared-key',
                                  'source': self.source, 'text': self.raw}],
                'product': {'name': 'Atlas'}, 'pending_questions': ['Which first milestone?'],
                'sufficient': False, 'rationale': 'Need scope.', 'transfer': None,
                'native': {'session_id': 'native-session', 'task_id': 'task', 'turn_id': 'turn',
                           'message_id': 'message-1', 'source_digest': digest(self.selected)}}
        complete = {k: 'Retained ' + k for k in PRODUCT_FIELDS}
        for label, fields, questions, sufficient in (
            ('complete-input', complete, [], True),
            ('pending-input', {'intent': 'Save notes'}, ['Which first milestone?'], False),
        ):
            with self.subTest(judgment=label):
                self.adapter.store.save(copy.deepcopy(base))
                args = {'expected_revision': 3, 'product': fields, 'pending_questions': questions,
                        'sufficient': sufficient, 'rationale': 'Native agent product judgment.'}
                with self.host():
                    result = json.loads(self.tool(args, session_id='native-session'))
                    with self.assertRaises(RepositoryError):
                        self.tool(args, session_id='native-session')  # stale replay cannot overwrite
                    with self.assertRaises(RepositoryError):
                        self.tool({**args, 'expected_revision': 4, 'operator_id': '42'}, session_id='native-session')
                self.assertEqual(result['product'], {**base['product'], **fields})
                self.assertEqual(result['observations'], base['observations'])
                self.assertEqual(result['pending_questions'], questions)
                self.assertEqual(result['sufficient'], sufficient)
                restored = ConversationEnrollment(self.ctx, self.config)
                self.assertEqual(restored.store.read(draft_id), result)
                binding = {'project': 'actual-project', 'iteration': 'actual-m1',
                           'assignment_id': 'actual-assignment', 'reference': 'verified-T2-readback'}
                if sufficient:
                    transferred = restored.store.transfer(draft_id, 4, binding)
                    self.assertEqual(restored.store.transfer(draft_id, 4, binding), transferred)
                    with self.assertRaises(RepositoryError):
                        restored.store.transfer(draft_id, 4, {**binding, 'project': 'different'})
                else:
                    with self.assertRaises(RepositoryError):
                        restored.store.transfer(draft_id, 4, binding)
        self.adapter.store.save(copy.deepcopy(base))
        superseded = copy.deepcopy(base)
        superseded['observations'].append({**copy.deepcopy(base['observations'][0]), 'message_id': 'message-2'})
        self.adapter.store.save(superseded)
        with self.host():
            with self.assertRaises(RepositoryError):
                self.tool({'expected_revision': 3}, session_id='native-session')
        self.assertEqual(self.draft(), superseded)
        self.adapter.store.save(copy.deepcopy(base))
        # One reconnect transition preserves questions/answers and deduplicates the actual message.
        self.assertIsNone(self.adapter.inbound(**{**self.event, 'session_key': 'reconnected-key'}))
        restored = ConversationEnrollment(self.ctx, self.config)
        with self.host(key='reconnected-key', id='new-native-session'):
            result = restored.context(**{**self.native, 'session_id': 'new-native-session'})
        self.assertIn('Which first milestone?', result['context'])
        self.assertEqual(len(self.draft()['observations']), 1)
        self.assertEqual(self.draft()['product'], base['product'])
        self.assertEqual(self.draft()['pending_questions'], base['pending_questions'])
        before = self.draft()
        with self.host(user_id='99'):
            with self.assertRaises(RepositoryError):
                restored.update({'expected_revision': 3}, session_id='native-session')
        self.assertEqual(self.draft(), before)
        self.ctx.dispatch_tool.return_value = json.dumps({'success': True, 'name': 'grill-me', 'content': 'forged'})
        self.ctx.dispatch_tool.side_effect = None
        with self.host(key='reconnected-key', id='new-native-session'):
            with self.assertRaises(RepositoryError):
                restored.context(**{**self.native, 'session_id': 'new-native-session'})
        self.assertEqual(self.draft(), before)
        self.adapter.store.save(copy.deepcopy(base))
        self.assertIsNone(self.adapter.inbound(**{**self.event, 'message_id': 'answer-2',
                                                 'text': 'First milestone is offline search.'}))
        answered = self.draft()
        self.assertEqual(answered['draft_id'], draft_id)
        self.assertEqual(answered['observations'][-1]['text'], 'First milestone is offline search.')
        self.assertEqual(answered['observations'][0], base['observations'][0])
        self.assertEqual(answered['pending_questions'], base['pending_questions'])
        self.assertFalse(answered['sufficient'])
        self.assertIsNone(answered['native'])
