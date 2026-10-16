"""Setup action components only; direct state and mocked native/GitHub/Git boundaries."""
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from factory_v1 import project_setup as setup
from factory_v1.conversation_enrollment import DraftStore
from factory_v1.repositories import RepositoryError


class ProjectSetupTests(unittest.TestCase):
    def test_existing_repository_setup_action_and_adapter_readbacks(self):
        for case in ('ready', 'replay', 'denied', 'identity-drift', 'checkout-drift', 'paused', 'unknown-checkout', 'native'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name in ('drafts', 'journal', 'workspaces', 'controller', 'caps'):
                    (root / name).mkdir(mode=0o700)
                state = root / 'controller/state.sqlite'
                db = sqlite3.connect(state)
                db.execute('CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT, PRIMARY KEY(project_id, iteration_id))')
                os.chmod(state, 0o600)
                enrollment = Mock()
                enrollment.store = DraftStore(root / 'drafts')
                enrollment.config = {'skills': []}
                draft_id = 'a' * 64
                origin = {'platform': 'discord', 'chat_id': '123', 'thread_id': '123', 'parent_chat_id': '122', 'scope_id': '121'}
                raw_origin = dict(origin, chat_type='group', profile='')
                draft = {'draft_id': draft_id, 'revision': 3, 'sufficient': True, 'pending_questions': [],
                         'operator_id': '42', 'origin': raw_origin, 'native': {'session_id': 'native-session', 'message_id': '555'},
                         'product': {'name': 'Atlas'}, 'observations': [{'text': 'Build Atlas unchanged', 'message_id': '555',
                         'source': dict(raw_origin, user_id='42')}], 'transfer': None}
                enrollment.store.save(draft)
                capability = {'operator_id': '42', 'repository': 'owner/product', 'reference': 'approved-scope',
                              'api_base': 'https://api.github.com', 'bearer': 'fixture-only'}
                cap_path = root / 'caps/host.json'
                cap_path.write_text(json.dumps(capability)); cap_path.chmod(0o600)
                config = {'root': str(root / 'journal'), 'workspace_root': str(root / 'workspaces'), 'state': str(state),
                          'capability_config': str(cap_path), 'native_project_db': None}
                if case == 'native':
                    config['native_project_db'] = str(root / 'caps/projects.db')
                    Path(config['native_project_db']).touch(mode=0o600)
                service = setup.ProjectSetup(enrollment, config)
                pid = 'project-' + draft_id
                item = {'project_id': pid, 'iteration_id': 'm1', 'repository': 'owner/product',
                        'idea': draft['observations'][0]['text'], 'origin': origin,
                        'approval': {'operator_id': '42', 'reference': 'approved-scope'}, 'status': 'active',
                        'repository_onboarding': {'status': 'verified', 'metadata': {'id': 77}}}
                db.execute('INSERT INTO iterations VALUES(?,?,?)', (pid, 'm1', json.dumps(item))); db.commit()
                db.close()
                journal = {'version': 1, 'draft_id': draft_id, 'draft_revision': 3, 'repository': 'owner/product',
                           'project': pid, 'iteration': 'm1', 'workspace': str(root / 'workspaces' / draft_id),
                           'repository_id': 77, 'commit': 'b' * 40, 'status': 'pending', 'blocker': None}
                if case in ('replay', 'unknown-checkout'):
                    journal['checkout_intent'] = {'repository': 'owner/product', 'commit': 'b' * 40, 'path': journal['workspace']}
                service.save(journal)
                returned = dict(item, planning={'assignment_id': 'real-returned-assignment', 'origin': origin})

                def plan(db, current, request):
                    self.assertEqual(request, {'origin': origin, 'expected_revision': 0, 'skills': []})
                    db.execute('UPDATE iterations SET payload=?', (json.dumps(returned),)); db.commit()
                    return copy.deepcopy(returned)

                def checkout(workspace, repository, revision, bearer, *, create):
                    self.assertEqual((str(workspace), repository, revision, bearer),
                                     (journal['workspace'], 'owner/product', 'b' * 40, 'fixture-only'))
                    self.assertEqual(create, case not in ('replay', 'unknown-checkout'))
                    if case == 'unknown-checkout':
                        raise RepositoryError('checkout_blocked', 'Incomplete checkout remains held')
                    return {'path': str(workspace), 'repository': repository,
                            'commit': 'c' * 40 if case == 'checkout-drift' else revision}

                def onboard(db, current, github):
                    if case == 'denied':
                        raise RepositoryError('github_blocked', 'Denied')
                    result = copy.deepcopy(item)
                    if case == 'identity-drift':
                        result['repository_onboarding']['metadata']['id'] = 88
                    return result

                with patch.object(setup, 'register_iteration', return_value=copy.deepcopy(item)) as intake, \
                     patch.object(setup.repositories, 'onboard', side_effect=onboard) as on, \
                     patch.object(setup.planning, 'plan', side_effect=plan) as planning, \
                     patch.object(setup, 'git_checkout', side_effect=checkout) as git, \
                     patch.object(setup, 'native_project', return_value='p_returned') as native, \
                     patch.object(setup.controls, 'require_active', side_effect=RepositoryError('iteration_paused', 'Paused') if case == 'paused' else None):
                    if case == 'ready':
                        enrollment.correlated.return_value = (draft, draft['observations'][0])
                        service.register()
                        self.assertEqual(enrollment.ctx.register_tool.call_args.kwargs['name'], 'factory_project_attach')
                        result = json.loads(service.tool({'repository': 'owner/product', 'expected_revision': 3}, session_id='native-session'))
                    else:
                        result = service.attach(draft_id, 3, 'owner/product', session_id='native-session')
                    self.assertEqual(enrollment.store.read(draft_id)['observations'], draft['observations'])
                    self.assertEqual(enrollment.store.read(draft_id)['origin'], raw_origin)
                    self.assertEqual(intake.call_args.args[1]['origin'], origin)
                    if case in ('ready', 'replay', 'native'):
                        self.assertEqual(result['status'], 'ready')
                        self.assertEqual(result['binding']['session_id'], 'native-session')
                        self.assertEqual(result['transfer']['assignment_id'], 'real-returned-assignment')
                        self.assertEqual(enrollment.store.read(draft_id)['transfer'], result['transfer'])
                        self.assertEqual(planning.call_count, 1)
                        if case == 'native':
                            self.assertEqual(result['native_project'], 'p_returned')
                        else:
                            native.assert_not_called()
                        with patch.object(setup.ConversationBinding, 'current', return_value=(returned, {})):
                            route = service.route(draft_id)
                            self.assertEqual(route.config, result['binding'])
                        restored = setup.ProjectSetup(enrollment, config)
                        self.assertEqual(restored.read(draft_id), result)
                    else:
                        self.assertEqual(result['status'], 'blocked')
                        self.assertIsNone(enrollment.store.read(draft_id)['transfer'])
                        planning.assert_not_called()
                        self.assertEqual(result['blocker']['code'], {'denied': 'github_blocked', 'identity-drift': 'repository_mismatch',
                            'checkout-drift': 'workspace_mismatch', 'paused': 'iteration_paused', 'unknown-checkout': 'checkout_blocked'}[case])
                        if case == 'paused':
                            on.assert_not_called(); git.assert_not_called()
                    with self.assertRaises(RepositoryError):
                        service.attach(draft_id, 3, 'other/product', session_id='native-session')
                    self.assertNotIn('fixture-only', json.dumps(service.read(draft_id)))

        # The concrete Git adapter uses exact argv/readbacks; never execute a process.
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / 'product'
            outputs = ['', '', str(workspace), 'https://github.com/owner/product.git', 'b' * 40]
            def process(argv, **kwargs):
                if 'clone' in argv:
                    workspace.mkdir()
                self.assertNotIn('fixture-secret', repr(argv))
                self.assertEqual(kwargs['env']['GIT_CONFIG_GLOBAL'], '/dev/null')
                return SimpleNamespace(returncode=0, stdout=outputs.pop(0))
            with patch.object(setup.subprocess, 'run', side_effect=process) as run:
                self.assertEqual(setup.git_checkout(workspace, 'owner/product', 'b' * 40, 'fixture-secret', create=True)['commit'], 'b' * 40)
                self.assertEqual(run.call_args_list[1].args[0][-3:], ['checkout', '--detach', 'b' * 40])
        # Optional native API source-contract mock; no installed native import or DB needed.
        project = SimpleNamespace(id='p_actual', primary_path='/product', archived=False,
                                  folders=[SimpleNamespace(path='/product', is_primary=True)])
        api = Mock(); api.find_by_primary_path.return_value = None
        api.get_project.return_value = project; api.create_project.return_value = 'p_actual'
        with patch.dict(sys.modules, {'hermes_cli': SimpleNamespace(projects_db=api)}):
            self.assertEqual(setup.native_project('/explicit/projects.db', 'Atlas', '/product'), 'p_actual')
            api.create_project.assert_called_once_with(api.connect.return_value, name='Atlas', folders=['/product'], primary_path='/product')
            api.connect.return_value.close.assert_called_once()
