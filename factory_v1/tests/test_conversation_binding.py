"""Native conversational tool seam; explicit state, mocked native loader only."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from factory_v1 import planning, repositories, skills
from factory_v1.conversation_binding import ConversationBinding
from factory_v1.tests.test_tickets import FrontierFixture


class ConversationBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FrontierFixture(self)
        self.item = self.fixture.item
        installed = self.fixture.root / 'installed-skills'
        for name in skills.INSTALLED_PINS:
            target = installed / name / 'SKILL.md'
            target.parent.mkdir(parents=True)
            target.write_bytes((Path(__file__).parent / 'planning_skill_snapshots' / (name + '.md')).read_bytes())
        self.addCleanup(patch.stopall)
        patch.object(skills, 'INSTALLED_ROOT', installed).start()
        self.descriptors = [{'name': name, 'path': str(skills.INSTALLED_ROOT / name / 'SKILL.md'), 'sha256': pin}
                            for name, pin in skills.INSTALLED_PINS.items()]
        self.selected = skills.resolve_installed(self.descriptors)
        self.item['planning'] = {'assignment_id': 'origin-assignment', 'revision': 1,
            'request': {'origin': self.item['origin'], 'expected_revision': 0, 'skills': self.descriptors},
            'origin': self.item['origin'], 'status': 'pending', 'skills': self.selected,
            'instructions': planning.INSTRUCTIONS, 'docs_path': 'docs/factory/m1/origin-assignment'}
        self.fixture.persist()
        self.ctx = Mock()
        self.ctx.dispatch_tool.side_effect = lambda tool, args, **kw: json.dumps({
            'success': True, 'name': args['name'], 'content': self.selected[args['name']]['instructions']})
        self.config = {'state': str(self.fixture.state), 'operator_id': '42', 'project': 'product',
                       'iteration': 'm1', 'session_id': 'native-origin', 'origin': self.item['origin'],
                       'reference': 'explicit-host-session-binding'}
        self.binding = ConversationBinding(self.ctx, self.config, self.fixture.github)
        self.judgment = {'assignment_id': 'origin-assignment', 'rationale': 'Genuine choices settled.', 'pending_questions': []}

    def test_native_origin_hook_loads_installed_interview_not_another_agent(self):
        self.assertIsNone(self.binding.context('other-session'))
        self.ctx.dispatch_tool.assert_not_called()
        result = self.binding.context('native-origin')
        self.assertIn('factory_planning_sufficient', result['context'])
        self.assertEqual([c.args[1]['name'] for c in self.ctx.dispatch_tool.call_args_list], ['grill-me', 'grilling'])
        self.assertTrue(all(c.kwargs == {'session_id': 'native-origin'} for c in self.ctx.dispatch_tool.call_args_list))
        self.assertEqual(self.fixture.stored(), self.item)

    def test_sufficient_judgment_automatically_loads_to_spec_in_same_tool_loop(self):
        self.binding.context('native-origin')
        output = json.loads(self.binding.sufficient(self.judgment, session_id='native-origin'))
        self.assertEqual(output['instructions'], self.selected['to-spec']['instructions'])
        self.assertIn('Execute to-spec now', output['next'])
        self.assertEqual(self.fixture.stored()['planning']['status'], 'pending')
        self.fixture.github.request.assert_not_called()

    def test_unanswered_stale_wrong_session_and_unloaded_judgments_refused(self):
        with self.assertRaises(repositories.RepositoryError):
            self.binding.sufficient(self.judgment, session_id='native-origin')
        self.binding.context('native-origin')
        for args, session in (({**self.judgment, 'pending_questions': ['Which language?']}, 'native-origin'),
                              ({**self.judgment, 'assignment_id': 'stale'}, 'native-origin'),
                              (self.judgment, 'other-session')):
            with self.subTest(args=args, session=session), self.assertRaises(repositories.RepositoryError):
                self.binding.sufficient(args, session_id=session)
        self.assertNotIn('to-spec', [c.args[1]['name'] for c in self.ctx.dispatch_tool.call_args_list])

    def test_loader_catalog_claim_or_different_instructions_never_admitted(self):
        for response in ({'success': True, 'name': 'grill-me', 'content': 'forged'}, {'success': False}):
            self.ctx.dispatch_tool.return_value = json.dumps(response)
            self.ctx.dispatch_tool.side_effect = None
            with self.subTest(response=response), self.assertRaises(repositories.RepositoryError):
                self.binding.context('native-origin')

    def test_loading_to_spec_is_not_execution_or_publication_completion(self):
        self.binding.context('native-origin')
        self.binding.sufficient(self.judgment, session_id='native-origin')
        with self.assertRaises(repositories.RepositoryError):
            self.binding.complete({'sufficient': True}, session_id='native-origin')
        self.assertEqual(self.fixture.stored()['planning']['status'], 'pending')
        self.assertNotIn('completion_digest', self.fixture.stored()['planning'])

    def test_registers_real_native_hook_and_tool_contracts(self):
        self.binding.register()
        self.ctx.register_hook.assert_called_once_with('pre_llm_call', self.binding.context)
        self.assertEqual([c.kwargs['name'] for c in self.ctx.register_tool.call_args_list],
                         ['factory_planning_sufficient', 'factory_planning_complete'])

    def test_synthesized_output_requires_real_publication_readback_before_scheduler_handoff(self):
        from factory_v1.tests.test_planning import PlanningTests
        from factory_v1.service_dispatch import select
        self.item.pop('handoff'); self.item.pop('ticket_work'); self.fixture.persist()
        self.binding.context('native-origin')
        self.binding.sufficient(self.judgment, session_id='native-origin')
        docs = {'milestone.md': ''.join('## ' + s + '\n\nSubstantive synthesized decision.\n\n' for s in planning.SECTIONS),
                'vision.md': 'Future sharing is not part of this finite milestone.',
                'provenance.md': 'Retain original answers and their native message references.'}
        request = {'assignment_id': 'origin-assignment', 'origin': self.item['origin'], 'sufficient': True,
                   'rationale': self.judgment['rationale'], 'reference': 'native-agent-turn', 'pending_questions': [],
                   'documents': docs, 'commit': 'a' * 40, 'issue_number': 7}
        self.repo = {'id': 123, 'full_name': 'example/product', 'name': 'product', 'owner': {'login': 'example'}, 'private': True}
        PlanningTests.publication(self, request)
        self.fixture.github.request.side_effect = lambda path: copy.deepcopy(self.published[path])
        output = json.loads(self.binding.complete(request, session_id='native-origin'))
        self.assertEqual(output, {'status': 'complete', 'stage': 'planning_complete'})
        self.assertEqual(select(self.fixture.stored(), [], {}), ('synthesize', None))

    def test_pause_or_origin_drift_prevents_native_instruction_admission(self):
        for key, value in (('status', 'paused'), ('origin', {'platform': 'discord', 'chat_id': 'different'})):
            original = copy.deepcopy(self.item)
            self.item[key] = value; self.fixture.persist()
            with self.subTest(key=key), self.assertRaises(repositories.RepositoryError):
                self.binding.context('native-origin')
            self.ctx.dispatch_tool.assert_not_called()
            self.item.clear(); self.item.update(original); self.fixture.persist()

    def test_adaptation_drift_refuses_before_loading(self):
        self.item['planning']['instructions'] += '\nInvent the missing answers.'
        self.fixture.persist()
        with self.assertRaises(repositories.RepositoryError):
            self.binding.context('native-origin')
        self.ctx.dispatch_tool.assert_not_called()

    def test_initial_plan_accepts_reviewed_installed_sources_without_repinning(self):
        original = copy.deepcopy(self.item)
        self.item.pop('planning'); self.item.pop('ticket_work'); self.item.pop('handoff')
        self.fixture.persist()
        planned = planning.plan(self.fixture.database, self.item,
                                {'origin': self.item['origin'], 'expected_revision': 0, 'skills': self.descriptors})
        self.assertEqual(planned['planning']['skills'], self.selected)
        drift = copy.deepcopy(self.descriptors); drift[0]['sha256'] = '0' * 64
        with self.assertRaises(repositories.RepositoryError):
            skills.resolve_planning(drift)
        self.assertEqual(original['planning']['skills'], self.selected)
