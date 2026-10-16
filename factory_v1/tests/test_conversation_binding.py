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

    def test_native_skill_loads_preserve_session_and_pending_state(self):
        self.assertIsNone(self.binding.context('other-session')); self.ctx.dispatch_tool.assert_not_called()
        result = self.binding.context('native-origin')
        self.assertIn('factory_planning_sufficient', result['context'])
        self.assertEqual([c.args[1]['name'] for c in self.ctx.dispatch_tool.call_args_list], ['grill-me', 'grilling'])
        self.assertTrue(all(c.kwargs == {'session_id': 'native-origin'} for c in self.ctx.dispatch_tool.call_args_list))
        stored = self.fixture.stored()
        self.assertEqual(stored['planning'].pop('native_binding'), {'assignment_id': 'origin-assignment',
                         'session_id': 'native-origin', 'reference': self.config['reference']})
        self.assertEqual(stored, self.item)
        output = json.loads(self.binding.sufficient(self.judgment, session_id='native-origin'))
        self.assertEqual(output['instructions'], self.selected['to-spec']['instructions'])
        self.assertIn('Execute to-spec now', output['next'])
        self.assertEqual(self.fixture.stored()['planning']['status'], 'pending')
        self.fixture.github.request.assert_not_called()



    def test_native_judgment_and_loader_contract(self):
        original = copy.deepcopy(self.item)
        loader = self.ctx.dispatch_tool.side_effect
        for label, loaded, changes, session, response in (
            ('unloaded', False, {}, 'native-origin', None),
            ('unanswered', True, {'pending_questions': ['Which language?']}, 'native-origin', None),
            ('stale', True, {'assignment_id': 'stale'}, 'native-origin', None),
            ('wrong-session', True, {}, 'other-session', None),
            ('forged-instructions', False, {}, 'native-origin', {'success': True, 'name': 'grill-me', 'content': 'forged'}),
            ('failed-loader', False, {}, 'native-origin', {'success': False}),
        ):
            with self.subTest(label=label):
                self.item.clear(); self.item.update(copy.deepcopy(original)); self.fixture.persist()
                self.ctx.dispatch_tool.reset_mock(); self.ctx.dispatch_tool.side_effect = loader
                if loaded:
                    self.binding.context('native-origin')
                if response is not None:
                    self.ctx.dispatch_tool.side_effect = None
                    self.ctx.dispatch_tool.return_value = json.dumps(response)
                with self.assertRaises(repositories.RepositoryError):
                    if response is not None:
                        self.binding.context('native-origin')
                    else:
                        self.binding.sufficient({**self.judgment, **changes}, session_id=session)
                self.assertNotIn('to-spec', [c.args[1]['name'] for c in self.ctx.dispatch_tool.call_args_list])
        self.ctx.dispatch_tool.side_effect = loader



    def test_registers_real_native_hook_and_tool_contracts(self):
        self.binding.register()
        self.ctx.register_hook.assert_called_once_with('pre_llm_call', self.binding.context)
        self.assertEqual([c.kwargs['name'] for c in self.ctx.register_tool.call_args_list],
                         ['factory_planning_sufficient', 'factory_planning_publish', 'factory_planning_complete'])
        # Direct host inventory/lifecycle component: no interview, publication or worker chain.
        import concurrent.futures
        import time
        from contextlib import nullcontext
        from types import SimpleNamespace
        from factory_v1 import assignments, planning_publication, project_native, service
        root = self.fixture.root
        roots = {}
        for name in ('native', 'drafts', 'setup', 'workspaces', 'credentials'):
            roots[name] = root / name
            roots[name].mkdir(mode=0o700)
        import tempfile
        native_directory = tempfile.TemporaryDirectory()
        self.addCleanup(native_directory.cleanup)
        Path(native_directory.name).chmod(0o700)
        roots['native'] = Path(native_directory.name)
        credential = roots['credentials'] / 'github.json'
        service.atomic(credential, {'operator_id': '42', 'repository': self.item['repository'],
            'reference': 'reviewed-host', 'api_base': 'https://api.github.com', 'bearer': 'fixture-only-secret'})
        route = {'repository': self.item['repository'], 'repository_id': 123, 'private': True, 'parent': 'a' * 40,
            'credential_config': str(credential), 'profile_name': 'native-profile',
            'source_digest': assignments.digest({'skills': self.selected, 'adaptation': planning.INSTRUCTIONS}),
            'allowance': {'reference': 'original-finite-approval', 'provider_route': 'reviewed-provider',
                          'model': 'fixture-model', 'expires_at': int(time.time()) + 600, 'max_wakeups': 1},
            'input_envelope': {'kind': 'plain'}}
        inventory = {'operator_id': '42', 'reference': 'reviewed-delegation', 'routes': [route]}
        selector = roots['credentials'] / 'native.json'
        service.atomic(selector, inventory)
        transfer = {'assignment_id': 'origin-assignment'}
        draft = {'draft_id': 'draft', 'operator_id': '42', 'revision': 1, 'sufficient': True, 'pending_questions': [],
                 'native': {'session_id': 'native-origin'}, 'origin': self.config['origin'], 'transfer': transfer}
        attached = {'status': 'ready', 'draft_revision': 1, 'repository': self.item['repository'], 'repository_id': 123,
                    'commit': 'a' * 40, 'binding': self.config, 'project': 'product', 'iteration': 'm1', 'transfer': transfer}
        store = SimpleNamespace(root=roots['drafts'], path=lambda identity: roots['drafts'] / identity,
                                read=lambda identity: copy.deepcopy(draft), locked=nullcontext)
        enrollment = SimpleNamespace(store=store)
        setup = SimpleNamespace(root=roots['setup'], workspaces=roots['workspaces'], config={'state': self.config['state']},
                                read=lambda identity: copy.deepcopy(attached), route=lambda identity: self.binding)
        self.item['repository_onboarding']['metadata']['private'] = True
        self.fixture.persist()
        self.ctx.profile_name = 'native-profile'; self.ctx.get_config.return_value = 'reviewed-provider'
        metadata = {'id': 123, 'full_name': self.item['repository'], 'name': 'product',
                    'owner': {'login': 'example'}, 'private': True}
        host = Mock(base='https://api.github.com')
        host.request.side_effect = lambda path: copy.deepcopy({'sha': 'a' * 40} if path.endswith('/commits/HEAD') else metadata)
        spawned = []
        def spawn(coro, **kwargs):
            spawned.append(coro); coro.close()
            return concurrent.futures.Future()
        self.ctx.spawn_task.side_effect = spawn
        config = {'root': str(roots['native']), 'capability_config': str(selector)}
        with patch.object(project_native.repositories, 'GitHub', return_value=host):
            owner = project_native.NativeProject(enrollment, setup, None, config)
            paths = owner.resolve('draft')
            publisher = planning_publication.configuration(paths['publication_config'], '42')
            self.assertEqual(publisher['bearer'], 'fixture-only-secret')
            self.assertEqual(json.loads(Path(paths['wakeup_config']).read_text())['allowance'], route['allowance'])
            self.assertTrue(all('fixture-only-secret' not in p.read_text() for p in roots['native'].rglob('*.json')))
            self.assertIs(owner.bind(self.binding, 'draft'), self.binding)
            subscriber = self.binding.wakeup
            assert subscriber is not None
            subscriber.joined = Mock(); subscriber.joined.wait.return_value = True
            self.assertIs(owner.bind(self.binding, 'draft').wakeup, subscriber)
            self.assertEqual(len(spawned), 1)
            self.ctx.on_unload.assert_called_once_with(subscriber.stop)
            for path, body, method in (('/user', None, None), ('/repos/example/product/issues/7', {}, 'POST'),
                                       ('/repos/example/product/contents/private?ref=' + 'a' * 40, None, None)):
                with self.subTest(route=path), self.assertRaises(repositories.RepositoryError):
                    self.binding.github.request(path, body, method)
            original = copy.deepcopy(inventory)
            for field, value in (('repository_id', 999), ('private', False), ('parent', 'b' * 40),
                                  ('source_digest', '0' * 64), ('profile_name', 'wrong-profile'),
                                  ('input_envelope', {'kind': 'arbitrary'}),
                                  ('allowance', {**route['allowance'], 'expires_at': 1}),
                                  ('allowance', {**route['allowance'], 'max_wakeups': 2})) :
                inventory['routes'][0][field] = value; service.atomic(selector, inventory)
                with self.subTest(drift=field), self.assertRaises(repositories.RepositoryError): owner.resolve('draft')
                service.atomic(selector, original); inventory = copy.deepcopy(original)
            metadata['private'] = False
            with self.assertRaises(repositories.RepositoryError): self.binding.ctx.get_config('provider_route')
            with self.assertRaises(repositories.RepositoryError): owner.bind(self.binding, 'draft')
            self.assertTrue(subscriber.stopped); self.assertTrue(subscriber.task.cancelled())
            subscriber.joined.wait.assert_called_with(2)
            metadata['private'] = True
            with self.assertRaises(repositories.RepositoryError): owner.bind(self.binding, 'draft')
            self.assertEqual(len(spawned), 1)
            owner.close(); owner.close()
            # Missing native lifecycle blocks, rather than manufacturing a loop/thread.
            missing = project_native.NativeProject(enrollment, setup, None, config)
            self.ctx.spawn_task = None
            with self.assertRaises(repositories.RepositoryError) as error: missing.bind(self.binding, 'draft')
            self.assertEqual(error.exception.code, 'native_lifecycle_unavailable')
            # Completed original work can bind read-only with the original expired grant;
            # only supervise's actual execution/publication receipt may consume it.
            self.ctx.spawn_task = Mock(side_effect=spawn)
            self.binding.ctx = self.ctx
            self.item['planning']['status'] = 'complete'; self.fixture.persist()
            # Use a separate explicit delegated route claim for this direct fixture.
            for path in roots['native'].rglob('*.json'): path.unlink()
            original['routes'][0]['allowance']['expires_at'] = 1; service.atomic(selector, original)
            completed = project_native.NativeProject(enrollment, setup, None, config)
            self.assertIs(completed.bind(self.binding, 'draft'), self.binding)
            self.assertIsNone(self.binding.wakeup); self.ctx.spawn_task.assert_not_called()
            completed.close()


    def test_supplied_publication_pointers_cannot_substitute_native_host_execution(self):
        from factory_v1.tests.test_planning import PlanningTests
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
        with self.assertRaises(repositories.RepositoryError) as error:
            self.binding.complete(request, session_id='native-origin')
        self.assertEqual(error.exception.code, 'execution_required')
        self.assertEqual(self.fixture.stored()['planning']['status'], 'pending')
        self.assertNotIn('completion_digest', self.fixture.stored()['planning'])

    def test_native_instruction_current_authority(self):
        original = copy.deepcopy(self.item)
        for field, value in (('status', 'paused'), ('origin', {'platform': 'discord', 'chat_id': 'different'}),
                             ('instructions', original['planning']['instructions'] + '\nInvent the missing answers.')):
            with self.subTest(field=field):
                self.item.clear(); self.item.update(copy.deepcopy(original))
                target = self.item['planning'] if field == 'instructions' else self.item
                target[field] = value
                self.fixture.persist()
                self.ctx.dispatch_tool.reset_mock()
                with self.assertRaises(repositories.RepositoryError):
                    self.binding.context('native-origin')
                self.ctx.dispatch_tool.assert_not_called()
        self.item.clear(); self.item.update(original); self.fixture.persist()



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
