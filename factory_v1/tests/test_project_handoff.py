"""One-transition composition checks; no project/worker lifecycle precursors."""
from contextlib import nullcontext
import copy

import sys
from types import SimpleNamespace
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
            tracker.prepare.return_value = {'status': 'ready'}
            execution.prepare_planning.return_value = {'status': 'planning_prepared'}
            # Host task scheduling is the next external boundary, not an inline journey.
            scheduled = []
            def spawn(coroutine, **kwargs):
                coroutine.close()
                scheduled.append(kwargs['name'])
                return Mock(done=lambda: True)
            ctx.spawn_task.side_effect = spawn
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
                    execution.register.assert_not_called()
                    execution.prepare_planning.assert_called_with(draft['draft_id'], 3)
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
            owner.native_project = Mock()
            owner.native_project.bind.side_effect = lambda native, draft_id: native
            owner.native_project.resolve.return_value = config
            transport_patch = patch('factory_v1.project_native.TicketTransport', return_value=Mock(base='https://api.github.com'))
            transport_patch.start(); self.addCleanup(transport_patch.stop)
            published = {'ticket_work': {'status': 'published', 'publication_complete': True}}
            with (patch.object(owner, 'consume', return_value='consumed'),
                  patch.object(handoff, 'connect', side_effect=lambda _: nullcontext(Mock())),
                  patch.object(handoff.repositories, 'state_lock', side_effect=lambda _: nullcontext()),
                  patch.object(owner, 'perform', side_effect=[{'status': 'ready'}, {'status': 'registered'}]) as perform):
                native.current.return_value = (published, {})
                self.assertEqual(owner.after_publication(draft['draft_id'])['status'], 'registered')
                self.assertEqual([c.args[1] for c in perform.call_args_list], ['tracker', 'execution'])
            # Native publication handler really invokes the continuation adapter, not an absent callback.
            with patch.object(owner, 'route', return_value=native), \
                 patch.object(owner, 'schedule') as after, \
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
            for producer in ('setup', 'create', 'tracker', 'planning', 'execution'):
                with self.subTest(producer=producer):
                    enrollment.store.save(copy.deepcopy(draft))
                    operation = owner.operation(draft, producer, 'reviewed/atlas')
                    owner.blocked(draft, producer, 'reviewed/atlas', {'status': 'blocked', 'blocker': {
                        'cause': 'permission', 'next_decision': 'Restore this exact approved operation'}})
                    decisions.producer = owner
                    with patch.object(handoff.readiness, 'load_config', return_value={
                            'repository': 'reviewed/atlas', 'operator_id': '42'}), \
                         patch.object(owner, 'perform', return_value={'status': 'ready'}) as perform, \
                         patch.object(owner, 'producer_continue') as continuation:
                        component = {'setup': setup, 'create': creation, 'tracker': tracker, 'planning': execution, 'execution': execution}[producer]
                        component.read.return_value = {'draft_id': draft['draft_id'], 'draft_revision': 3, 'status': 'ready',
                                                       'phase': 'postpublication'}
                        self.assertTrue(decisions.status(draft['draft_id'], operation['operation_id'], session_id='session')['ready'])
                        self.assertTrue(decisions.resume(draft['draft_id'], operation['operation_id'], session_id='session')['resumed'])
                        self.assertEqual(perform.call_args.args[1], producer)
                        continuation.assert_called_once_with(draft['draft_id'], operation, 'session')
                        self.assertFalse(decisions.resume(draft['draft_id'], operation['operation_id'], session_id='session')['resumed'])
            setup.read.return_value = {'repository': 'selected/Atlas', 'binding': {'session_id': 'session'}}
            # Independent phase records call the actual coordinator, not service.tick.
            for stage, seed, seam in (
                ('request', {}, 'tickets.synthesize'),
                ('execute', {'ticket_work': {'status': 'synthesis_pending'}}, 'service_dispatch.synthesize'),
                ('publish', {'ticket_work': {'status': 'synthesized'}}, 'tickets.publish'),
            ):
                with (self.subTest(ticket_phase=stage),
                      patch.object(owner, 'consume', return_value='consumed'),
                      patch.object(owner, 'perform', side_effect=[{'status': 'ready'}, {'status': 'registered'}]),
                      patch.object(handoff, 'connect', side_effect=lambda _: nullcontext(Mock())),
                      patch.object(handoff.repositories, 'state_lock', side_effect=lambda _: nullcontext()),
                      patch('factory_v1.project_handoff.' + seam) as boundary):
                    native.current.side_effect = [(seed, {}), (published, {}), (published, {})]
                    execution.inventory.return_value = {'ticket_skill': {'path': '/installed/to-tickets'},
                                                        'synthesis_config': '/host/synthesis.json'}
                    self.assertEqual(owner.after_publication(draft['draft_id'])['status'], 'registered')
                    boundary.assert_called_once()
                    self.assertIsNot(boundary.call_args.args[2 if stage == 'publish' else 3], native.github)
                    native.current.side_effect = None
            helper = Mock()
            helper.bind.return_value = native
            helper.resolve.return_value = config
            owner.native_project = helper
            self.assertIs(owner.route(draft['draft_id']), native)
            helper.bind.assert_called_once_with(native, draft['draft_id'])
            owner.native_project = None
            for producer in ('setup', 'create', 'tracker'):
                with self.subTest(continuation=producer), patch.object(owner, 'advance') as advance:
                    operation = owner.operation(draft, producer, 'reviewed/atlas')
                    owner.producer_continue(draft['draft_id'], operation, 'session')
                    advance.assert_called_once()
            for producer in ('setup', 'create', 'tracker', 'planning', 'execution'):
                enrollment.store.save(copy.deepcopy(draft))
                operation = owner.operation(draft, producer, 'reviewed/atlas')
                owner.blocked(draft, producer, 'reviewed/atlas', {'status': 'blocked',
                    'blocker': {'reconciliation_required': True}})
                with (self.subTest(reconcile=producer),
                      patch.object(owner, 'producer_resume', return_value={'operation': operation, 'status': 'complete'}) as resume,
                      patch.object(owner, 'producer_continue') as continuation):
                    decisions.reconcile(draft['draft_id'], operation['operation_id'])
                    resume.assert_called_once_with(draft['draft_id'], operation, 'session')
                    continuation.assert_called_once()
            # Execute only the host-task seam; downstream operation is independent.
            with patch.object(owner, 'after_publication', return_value={'status': 'planning-pending'}) as after:
                def run_task(coroutine, **kwargs):
                    with self.assertRaises(StopIteration) as completed:
                        coroutine.send(None)
                    self.assertEqual(completed.exception.value, {'status': 'planning-pending'})
                    return Mock(done=lambda: True)
                ctx.spawn_task.side_effect = run_task
                owner.schedule(draft['draft_id'])
                after.assert_called_once_with(draft['draft_id'])
            ctx.spawn_task.side_effect = spawn
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
            helper_constructor = Mock(return_value=helper)
            options['project_native'] = {'root': '/host/native', 'capability_config': '/host/native-policy.json'}
            with (patch.dict(sys.modules, {'factory_v1.project_native': SimpleNamespace(NativeProject=helper_constructor)}),
                  patch('factory_v1.conversation_enrollment.ConversationEnrollment', return_value=enrollment),
                  patch('factory_v1.project_setup.ProjectSetup', return_value=setup),
                  patch('factory_v1.project_creation.ProjectCreation', return_value=creation),
                  patch('factory_v1.project_tracker.ProjectTracker', return_value=tracker),
                  patch('factory_v1.project_execution.ProjectExecution', return_value=execution)):
                registered = plugin.register(ctx)
            helper_constructor.assert_called_once_with(enrollment, setup, execution, options['project_native'])
            self.assertIs(registered.native_project, helper)
