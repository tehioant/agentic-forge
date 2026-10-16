"""One-transition composition checks; no project/worker lifecycle precursors."""
from contextlib import nullcontext
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from typing import Any
from unittest.mock import Mock, create_autospec, patch

from factory_v1 import project_handoff as handoff
from factory_v1.conversation_enrollment import ConversationEnrollment, DraftStore, PRODUCT_FIELDS
from factory_v1.onboarding_decisions import OnboardingDecisions
from factory_v1.project_creation import ProjectCreation
from factory_v1.project_execution import ProjectExecution
from factory_v1.project_setup import ProjectSetup
from factory_v1.project_tracker import ProjectTracker
from factory_v1.repositories import RepositoryError


class ProjectHandoffTests(unittest.TestCase):
    def test_conversational_composition_transitions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drafts = root / 'drafts'; drafts.mkdir(mode=0o700)
            ctx = Mock(profile_name='test')
            enrollment = object.__new__(ConversationEnrollment)
            enrollment.ctx, enrollment.store = ctx, DraftStore(drafts)
            enrollment.config, enrollment.bound_planning = {'skills': []}, None
            enrollment.handoff = None
            setup = create_autospec(ProjectSetup, instance=True)
            creation = create_autospec(ProjectCreation, instance=True)
            tracker = create_autospec(ProjectTracker, instance=True)
            execution = create_autospec(ProjectExecution, instance=True)
            creation.config = {'account': 'reviewed', 'reference': 'approved'}
            tracker.config = {'capability_config': '/host/tracker.json'}
            enrollment.setup = setup
            setup.register.return_value = setup
            creation.register.return_value = creation
            config = {'publication_config': '/host/publisher.json', 'wakeup_config': '/host/wakeup.json'}
            draft = {'draft_id': 'a' * 64, 'operator_id': '42', 'origin': {'platform': 'discord'},
                     'revision': 3, 'sufficient': True, 'pending_questions': [], 'transfer': None,
                     'native': {'session_id': 'session', 'message_id': 'm1', 'source_digest': 'selected'},
                     'observations': [{'message_id': 'm1', 'text': 'Build Atlas'}],
                     'product': {k: 'Atlas' for k in PRODUCT_FIELDS}}
            enrollment.store.save(draft)
            decisions = OnboardingDecisions(enrollment, {'state': None}, setup=setup)
            owner = handoff.ProjectHandoff(enrollment, setup, creation, tracker, execution, decisions, config)
            native = Mock(config={'session_id': 'session'})
            setup.route.return_value = native
            setup.attach.return_value = creation.create.return_value = {'status': 'ready'}
            # Each case selects one actual setup public seam. Engineering cannot gate planning.
            for mode, repository in [('existing', 'selected/Atlas'), ('new', 'reviewed/atlas')]:
                with self.subTest(setup=mode):
                    current = copy.deepcopy(draft)
                    current['product']['repository_mode'] = mode
                    if mode == 'existing':
                        current['product']['repository'] = repository
                        current['observations'] = [{'text': 'Use selected/Atlas'}]
                    setup.attach.reset_mock(); creation.create.reset_mock()
                    result = owner.advance(current, 'session')
                    self.assertEqual(result['status'], 'planning')
                    selected = setup.attach if mode == 'existing' else creation.create
                    selected.assert_called_once_with(draft['draft_id'], 3, repository, session_id='session')
                    execution.register.assert_not_called(); tracker.prepare.assert_not_called()
            # Incomplete interview cannot select setup; exact target drift cannot replace a route.
            setup.attach.reset_mock()
            self.assertEqual(owner.advance({**draft, 'sufficient': False}, 'session'), {'status': 'interview'})
            setup.attach.assert_not_called()
            setup.route.return_value = Mock(config={'session_id': 'different'})
            with self.assertRaises(RepositoryError):
                owner.route(draft['draft_id'])
            setup.route.return_value = native
            # Actual draft update selects the coordinator outside its persistence lock.
            current = copy.deepcopy(draft); current['sufficient'] = False
            enrollment.handoff = owner
            with patch.object(enrollment, 'correlated', return_value=(current, {'message_id': 'm1'})), \
                 patch('factory_v1.conversation_enrollment.skills.resolve_installed', return_value={}), \
                 patch('factory_v1.conversation_enrollment.digest', return_value='selected'), \
                 patch.object(owner, 'advance', return_value={'status': 'planning'}) as advance:
                result = json.loads(enrollment.update({'expected_revision': 3, 'product': {
                    'repository': 'selected/Atlas', 'repository_mode': 'existing'}, 'pending_questions': [],
                    'sufficient': True, 'rationale': 'All answers retained'}, session_id='session'))
                self.assertEqual(result['handoff']['status'], 'planning')
                self.assertEqual(advance.call_args.args[0]['revision'], 4)
            # First publication consumed before downstream work; enqueue/load is not authority.
            for receipt in ('enqueued', 'consumed'):
                with self.subTest(receipt=receipt), patch.object(owner, 'consume', return_value=receipt), \
                     patch.object(owner, 'perform', return_value={'status': 'blocked', 'blocker': {
                         'cause': 'platform', 'next_decision': 'Restore the tracker'}}) as perform:
                    setup.read.return_value = {'repository': 'selected/Atlas', 'binding': {'session_id': 'session'}}
                    owner.after_publication(draft['draft_id'])
                    self.assertEqual(perform.call_count, int(receipt == 'consumed'))
                    if receipt == 'consumed':
                        self.assertEqual(perform.call_args.args[1], 'tracker')
            # Seed the consumed handoff and ready tracker independently; execution is the next seam.
            with patch.object(owner, 'consume', return_value='consumed'), \
                 patch.object(owner, 'perform', side_effect=[{'status': 'ready'}, {'status': 'registered'}]) as perform:
                self.assertEqual(owner.after_publication(draft['draft_id'])['status'], 'registered')
                self.assertEqual([c.args[1] for c in perform.call_args_list], ['tracker', 'execution'])
            # Native publication handler really invokes the continuation adapter, not an absent callback.
            with patch.object(owner, 'route', return_value=native), \
                 patch.object(owner, 'after_publication') as after, \
                 patch.object(handoff, 'native_context', return_value={'platform': 'discord'}), \
                 patch.object(enrollment, 'identity', return_value=({}, draft['draft_id'])):
                native.publish.return_value = 'real HOST publication result'
                self.assertEqual(owner.native_tool('publish', {}, session_id='session'), 'real HOST publication result')
                after.assert_called_once_with(draft['draft_id'])
            # Both scheduling orders use the existing supervise contract, never a returned document.
            with patch.object(owner, 'route', return_value=native), patch.object(handoff, 'connect') as connected, \
                 patch.object(handoff.planning_wakeup, 'supervise', return_value='consumed') as supervise:
                native.current.return_value = ({'planning': {'status': 'complete'}}, {})
                self.assertEqual(owner.consume(draft['draft_id']), 'consumed')
                supervise.assert_called_once_with(connected.return_value.__enter__.return_value,
                    native.current.return_value[0], config['wakeup_config'], native.github)
            # Producer routing cases are independent persisted requests, not an onboarding journey.
            for producer in ('setup', 'create', 'tracker', 'execution'):
                with self.subTest(producer=producer):
                    enrollment.store.save(copy.deepcopy(draft))
                    operation = owner.operation(draft, producer, 'reviewed/atlas')
                    owner.blocked(draft, producer, 'reviewed/atlas', {'status': 'blocked', 'blocker': {
                        'cause': 'permission', 'next_decision': 'Restore this exact approved operation'}})
                    decisions.producer = owner
                    with patch.object(handoff.readiness, 'load_config', return_value={
                            'repository': 'reviewed/atlas', 'operator_id': '42'}), \
                         patch.object(owner, 'perform', return_value={'status': 'ready'}) as perform:
                        self.assertTrue(decisions.status(draft['draft_id'], operation['operation_id'], session_id='session')['ready'])
                        self.assertTrue(decisions.resume(draft['draft_id'], operation['operation_id'], session_id='session')['resumed'])
                        self.assertEqual(perform.call_args.args[1], producer)
                        self.assertFalse(decisions.resume(draft['draft_id'], operation['operation_id'], session_id='session')['resumed'])
            # Ticket transitions are separate minimal records; no synthesis/publication precursor executes.
            from factory_v1 import service_dispatch as dispatch
            service = {'state': '/host/state.sqlite', 'operator_id': '42', 'project': 'p',
                       'iteration': 'm1', 'dispatch_config': '/host/dispatch.json'}
            dispatch_config: dict[str, Any] = {k: None for k in dispatch.FIELDS}
            dispatch_config.update(operator_id='42', project='p', iteration='m1', reference='approved',
                                   roles={}, launchers={}, tickets={'approved': 'tracker'})
            for stage, work, seam in (
                ('synthesize', None, 'tickets.synthesize'),
                ('execute-synthesis', {'status': 'synthesis_pending'}, 'synthesize'),
                ('publish-tickets', {'status': 'synthesized'}, 'tickets.publish'),
            ):
                with self.subTest(ticket_stage=stage):
                    item = {'planning': {'status': 'complete'}}
                    if work is not None:
                        item['ticket_work'] = work
                    with patch('factory_v1.service.connect', side_effect=lambda _: nullcontext(Mock())), \
                         patch('factory_v1.readiness_gate.check'), \
                         patch.object(dispatch, 'state_lock', side_effect=lambda _: nullcontext()), \
                         patch.object(dispatch.merge, 'controller_paths'), \
                         patch.object(dispatch.publication, 'read_json', return_value=dispatch_config), \
                         patch.object(dispatch.assignments, 'current', side_effect=lambda *a, **k: copy.deepcopy(item)), \
                         patch.object(dispatch.assignments, 'rows', return_value=[]), \
                         patch.object(dispatch.controls, 'require_active'), \
                         patch('factory_v1.restart.before_dispatch'), \
                         patch('factory_v1.service_dispatch.' + seam) as next_boundary:
                        self.assertEqual(dispatch.tick(service, Mock())['stage'], stage)
                        next_boundary.assert_called_once()
            # Real registration schemas, one hook owner, same producer identities with optional decisions.
            entry = Path(__file__).parents[2] / 'integrations/hermes-planning/__init__.py'
            spec = importlib.util.spec_from_file_location('handoff_plugin', entry)
            assert spec is not None and spec.loader is not None
            plugin = importlib.util.module_from_spec(spec); spec.loader.exec_module(plugin)
            for has_creation, has_decisions in ((False, False), (True, False), (False, True), (True, True)):
                with self.subTest(composition=(has_creation, has_decisions)):
                    ctx.register_hook.reset_mock(); ctx.register_tool.reset_mock()
                    options = {'enrollment': {}, 'project_setup': {}, 'project_tracker': {}, 'project_execution': {},
                               'project_creation': {} if has_creation else None,
                               'onboarding_decisions': {'state': None} if has_decisions else None,
                               'project_handoff': config}
                    ctx.get_config.side_effect = options.get
                    with patch('factory_v1.conversation_enrollment.ConversationEnrollment', return_value=enrollment), \
                         patch('factory_v1.project_setup.ProjectSetup', return_value=setup) as setup_constructor, \
                         patch('factory_v1.project_creation.ProjectCreation', return_value=creation), \
                         patch('factory_v1.project_tracker.ProjectTracker', return_value=tracker) as tracker_constructor, \
                         patch('factory_v1.project_execution.ProjectExecution', return_value=execution) as execution_constructor:
                        registered = plugin.register(ctx)
                    setup_constructor.assert_called_once_with(enrollment, {})
                    tracker_constructor.assert_called_once_with(setup, {})
                    execution_constructor.assert_called_once_with(setup, tracker, {})
                    self.assertIs(registered.setup, setup)
                    self.assertIs(enrollment.handoff, registered)
                    hooks = [c.args[0] for c in ctx.register_hook.call_args_list]
                    self.assertEqual(hooks, ['post_gateway_admission', 'pre_llm_call'])
                    tools = [c.kwargs['name'] for c in ctx.register_tool.call_args_list]
                    self.assertEqual(len(tools), len(set(tools)))
                    self.assertIn('factory_planning_publish', tools)
                    self.assertIn('factory_project_draft_update', tools)
