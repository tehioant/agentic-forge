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



    def test_supervised_first_observation_uses_default_host_execution_reader(self):
        for mode in ('initial', 'successor'):
            with self.subTest(mode=mode):
                case = ConversationBindingTests()
                case.setUp()
                try:
                    case.supervised_first_observation(mode)
                finally:
                    case.doCleanups()

    def supervised_first_observation(self, mode):
        from factory_v1 import readiness, readiness_capabilities, readiness_gate, requirement_execution
        import hashlib
        self.item.pop('handoff'); self.item.pop('ticket_work')
        self.item['repository_onboarding']['api_base'] = 'https://api.github.com'
        predecessor = {}
        if mode == 'successor':
            from factory_v1 import attention, iteration
            from factory_v1.tests.test_iteration import context
            predecessor = context()
            predecessor.update(iteration_id='m0', origin=copy.deepcopy(self.item['origin']), status='waiting-for-operator')
            predecessor['repository_onboarding'] = copy.deepcopy(self.item['repository_onboarding'])
            report = {k: predecessor[k] for k in ('project_id', 'iteration_id', 'repository')}
            report_id = iteration.assignments.digest(report)
            completion = {'report_id': report_id, 'event_id': 'completion-' + report_id, 'report': report}
            predecessor['completion'] = completion
            request = {'iteration_id': 'm1', 'event_id': completion['event_id'], 'operator_id': '42',
                       'origin': copy.deepcopy(self.item['origin']), 'reference': 'later-authenticated-input',
                       'message_id': '102', 'input': 'Add search.', 'skills': self.descriptors}
            predecessor['next_iteration'] = {'iteration_id': 'm1', 'request': request}
            self.fixture.database.execute('INSERT INTO iterations VALUES (?,?,?)',
                (predecessor['project_id'], predecessor['iteration_id'], json.dumps(predecessor)))
            self.item.update(previous_iteration={'iteration_id': 'm0', 'completion': completion, 'input': request},
                             idea=request['input'], approval={'operator_id': '42', 'reference': request['reference']})
            self.item['planning']['continuation'] = {**iteration.continuation_inputs(predecessor), 'input': request,
                'adaptation': planning.NEXT_ADAPTATION,
                'dependencies': {'grill-me': ['grilling'], 'grilling': [], 'to-spec': []}}
            self.item['planning']['instructions'] += ('\nContinue this SAME product from ALL continuation.previous_specs and the explicit input. '
                'Preserve any explicit round-completion gate; no synthesis before actual answers. '
                'Load continuation input_loads, planning:input and planning:adaptation; retain correlated '
                'instruction loads, ordered skill work and actual rounds through complete-planning execution. '
                'Publish new updated-spec pointers, never overwrite the preceding milestone.')
            patch.object(attention, 'get_record', return_value={'state': 'delivered',
                'receipt': {'reference': 'delivered-invitation', 'message_id': '101'}}).start()
        self.fixture.persist()
        root = self.fixture.root
        native_source = root / 'native-contract.py'; native_source.write_text('# reviewed native fixture\n'); native_source.chmod(0o600)
        approval = {'binding': self.config, 'assignment_id': 'origin-assignment', 'profile_name': 'approved-profile',
            'source_digest': None,
            'allowance': {'reference': 'existing-grant', 'provider_route': 'approved', 'model': 'fixture',
                          'expires_at': 9999999999, 'max_wakeups': 1}}
        from factory_v1 import assignments
        approval['source_digest'] = assignments.digest({'skills': self.selected, 'adaptation': self.item['planning']['instructions']})
        def private(name, value):
            path = root / name; path.write_text(json.dumps(value)); path.chmod(0o600); return str(path)
        approved_path = private('approved-native.json', approval)
        selected = {'planning_skills': self.descriptors, 'synthesis_config': '/approved/synthesis.json'}
        service = {**{k: self.config[k] for k in ('state', 'project', 'iteration', 'operator_id')},
                   'dispatch_config': private('dispatch-bootstrap.json', {**{k: self.config[k] for k in ('project', 'iteration', 'operator_id')},
                       'planning_config': approved_path, 'synthesis_config': selected['synthesis_config']})}
        db = self.fixture.database
        readiness_gate.register(db, '/approved/service.json', service)
        db.execute('UPDATE supervised_readiness SET policy=?', ('sealed',)); db.commit()
        reader = readiness_capabilities.HostReaders(db, service, selected, self.fixture.github, native_root=root)
        calls = []
        def collect(database, config, policy, github, **kwargs):
            pin = reader.execution_support({**{k: self.config[k] for k in ('project', 'iteration', 'operator_id')}},
                ('grill-me', 'grilling', 'to-spec', 'to-tickets', *assignments.ROLES), **kwargs)
            calls.append(pin)
            return {'checks': {'execution': {'state': 'ready'}}}
        original = copy.deepcopy(self.fixture.stored())
        with patch.object(readiness_gate, 'configuration', return_value=(service, selected)), \
                patch.object(readiness_gate, 'policy', return_value='sealed'), \
                patch('factory_v1.service.github_client', return_value=self.fixture.github), \
                patch.object(readiness, 'collect', side_effect=collect), \
                patch.object(requirement_execution, 'configuration', return_value={'reviewed': 'fixture'}), \
                patch.object(readiness_capabilities, 'physical', return_value=native_source), \
                patch.object(readiness_capabilities, 'NATIVE_FILES', {'native-contract.py': hashlib.sha256(native_source.read_bytes()).hexdigest()}):
            with self.assertRaises(repositories.RepositoryError): readiness_gate.check(db, 'product', 'm1')
            if mode == 'successor':
                valid_predecessor = copy.deepcopy(predecessor)
                for defect in ('report', 'predecessor-status', 'backlink', 'late-input', 'origin', 'specs', 'adaptation', 'installed-source'):
                    with self.subTest(successor_refusal=defect):
                        candidate = copy.deepcopy(original)
                        prior = copy.deepcopy(valid_predecessor)
                        source = Path(self.descriptors[1]['path'])
                        source_bytes = source.read_bytes()
                        if defect == 'report': prior['completion']['report']['repository'] = 'other/product'
                        elif defect == 'predecessor-status': prior['status'] = 'active'
                        elif defect == 'backlink': prior['next_iteration']['iteration_id'] = 'other'
                        elif defect == 'late-input':
                            candidate['previous_iteration']['input']['message_id'] = '100'
                            candidate['planning']['continuation']['input']['message_id'] = '100'
                            prior['next_iteration']['request']['message_id'] = '100'
                        elif defect == 'origin': candidate['origin'] = {'platform': 'other'}
                        elif defect == 'specs': candidate['planning']['continuation']['input_loads'] = {}
                        elif defect == 'adaptation': candidate['planning']['continuation']['adaptation'] = 'unapproved'
                        else: source.write_bytes(source_bytes + b'drift')
                        repositories.save_iteration(db, candidate)
                        repositories.save_iteration(db, prior); db.commit()
                        self.ctx.dispatch_tool.reset_mock()
                        before = list(db.iterdump())
                        try:
                            with self.assertRaises(repositories.RepositoryError):
                                self.binding.context('native-origin', task_id='actual-task', turn_id='actual-turn',
                                    sender_id='42', user_message='Use Python.', conversation_history=[])
                            self.assertEqual(list(db.iterdump()), before)
                            self.ctx.dispatch_tool.assert_not_called()
                        finally:
                            source.write_bytes(source_bytes)
                            repositories.save_iteration(db, original)
                            repositories.save_iteration(db, valid_predecessor); db.commit()
            # First hook produces only authenticated observations; no prior execution row.
            result = self.binding.context('native-origin', task_id='actual-task', turn_id='actual-turn',
                sender_id='42', user_message='Use Python.', conversation_history=[])
            self.assertTrue(result['context']); self.assertEqual(len(calls), 1)
            stored = self.fixture.stored()
            self.assertEqual(stored['planning']['native_execution']['observations'][-1]['content'], 'Use Python.')
            self.assertNotIn('host_publication', stored['planning'])
            # Fresh assignment producer is independent of the observation fixture;
            # it consumes the already-approved ID rather than a random future ID.
            if mode == 'initial':
                fresh = copy.deepcopy(original); fresh.pop('planning')
                repositories.save_iteration(db, fresh); db.commit()
                planned = planning.plan(db, fresh, {'origin': fresh['origin'], 'expected_revision': 0, 'skills': self.descriptors})
                self.assertEqual(planned['planning']['assignment_id'], approval['assignment_id'])
                self.assertEqual(planned['planning']['skills'], self.selected)
                self.assertEqual(original['planning']['skills'], self.selected)
                drift = copy.deepcopy(self.descriptors); drift[0]['sha256'] = '0' * 64
                with self.assertRaises(repositories.RepositoryError):
                    skills.resolve_planning(drift)
                self.assertNotIn('native_execution', self.fixture.stored()['planning'])
            # The same admission cannot alter arbitrary state or launder sufficient/output authority.
            for field in ('repository', 'sufficient', 'origin', *(['predecessor', 'input', 'adaptation', 'source'] if mode == 'successor' else [])):
                repositories.save_iteration(db, original); db.commit()
                proposal = copy.deepcopy(stored)
                if field == 'repository': proposal['repository'] = 'other/product'
                elif field == 'sufficient': proposal['planning']['native_execution']['sufficient'] = True
                elif field == 'origin': proposal['planning']['native_execution']['origin'] = {'platform': 'other'}
                elif field == 'predecessor': proposal['previous_iteration']['completion']['report_id'] = 'stale'
                elif field == 'input': proposal['previous_iteration']['input']['message_id'] = '100'
                elif field == 'adaptation': proposal['planning']['continuation']['adaptation'] = 'unapproved'
                else: proposal['planning']['skills']['grilling']['instructions'] += 'drift'
                with self.subTest(field=field), self.assertRaises(repositories.RepositoryError):
                    planning.checkpoint(db, proposal, bootstrap=True)
                self.assertEqual(self.fixture.stored(), original)
        self.fixture.github.request.assert_not_called()
