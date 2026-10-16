"""Direct creation/initialization components; external boundaries never execute."""
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_v1 import project_creation as creation
from factory_v1.conversation_enrollment import DraftStore
from factory_v1.project_setup import ProjectSetup
from factory_v1.repositories import RepositoryError


class ProjectCreationTests(unittest.TestCase):
    def test_private_creation_baseline_and_reconciliation(self):
        def fixture(root):
            for name in ('drafts', 'setup', 'products', 'controller', 'caps', 'creation'):
                (root / name).mkdir(mode=0o700)
            state = root / 'controller/state.sqlite'
            with sqlite3.connect(state) as db:
                db.execute('CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT, PRIMARY KEY(project_id, iteration_id))')
            state.chmod(0o600)
            enrollment = Mock()
            enrollment.store = DraftStore(root / 'drafts')
            draft = {'draft_id': 'a' * 64, 'revision': 3, 'operator_id': '42', 'sufficient': True,
                     'pending_questions': [], 'native': {'session_id': 'native', 'message_id': '555'},
                     'product': {'name': 'Atlas'}, 'observations': [{'text': 'Build agreed Atlas', 'message_id': '555'}],
                     'origin': {'platform': 'discord', 'chat_id': '123', 'thread_id': '123', 'parent_chat_id': '122', 'scope_id': '121'}}
            enrollment.store.save(draft)
            capability = {'operator_id': '42', 'repository': 'owner/product', 'reference': 'approved-create-and-baseline',
                          'api_base': 'https://api.github.com', 'bearer': 'fixture-only'}
            cap = root / 'caps/host.json'
            cap.write_text(json.dumps(capability)); cap.chmod(0o600)
            setup = ProjectSetup(enrollment, {'root': str(root / 'setup'), 'workspace_root': str(root / 'products'),
                'state': str(state), 'capability_config': str(cap), 'native_project_db': None})
            creator = creation.ProjectCreation(setup, {'root': str(root / 'creation'), 'state': str(root / 'creation/history.sqlite'),
                'account': 'owner', 'reference': capability['reference']})
            record = {'version': 1, 'draft_id': draft['draft_id'], 'draft_revision': 3, 'repository': 'owner/product',
                      'project': 'creation-' + draft['draft_id'], 'iteration': 'm1',
                      'marker': 'forge:new-product:' + draft['draft_id'] + ':3', 'status': 'pending', 'blocker': None}
            return creator, draft, record, capability

        # Public native seam: precursor preparation mocked, actual T2 call/immutable inputs asserted.
        for case in ('ready', 'paused', 'wrong-account', 'wrong-session'):
            with self.subTest(public=case), tempfile.TemporaryDirectory() as tmp:
                creator, draft, record, _ = fixture(Path(tmp))
                def prepared(draft, current, capability):
                    current.update(repository_id=77, baseline={'commit': 'b' * 40})
                    creator.save(current)
                if case == 'wrong-account':
                    creator.config['account'] = 'other'
                creator.enrollment.correlated.return_value = (draft, draft['observations'][0])
                ready = {'status': 'ready', 'blocker': None, 'repository_id': 77, 'commit': 'b' * 40,
                         'binding': {'session_id': 'native'}, 'transfer': {'assignment_id': 'actual-T2-result'}}
                with patch.object(creator, 'prepare', side_effect=prepared) as prepare, \
                     patch.object(creator, 'guard_attachment', side_effect=RepositoryError('iteration_paused', 'Paused') if case == 'paused' else None), \
                     patch.object(creator.setup, 'attach', return_value=ready) as attach:
                    creator.register()
                    self.assertEqual(creator.enrollment.ctx.register_tool.call_args.kwargs['name'], 'factory_project_create')
                    if case == 'wrong-session':
                        with self.assertRaises(RepositoryError):
                            creator.create(draft['draft_id'], 3, 'owner/product', session_id='unrelated')
                        prepare.assert_not_called(); attach.assert_not_called()
                        continue
                    result = json.loads(creator.tool({'repository': 'owner/product', 'expected_revision': 3}, session_id='native'))
                    if case == 'ready':
                        self.assertEqual(result, ready)
                        attach.assert_called_once_with(draft['draft_id'], 3, 'owner/product', session_id='native')
                        checkpoint = creator.setup.read(draft['draft_id'])
                        assert checkpoint is not None
                        self.assertEqual((checkpoint['repository_id'], checkpoint['commit']), (77, 'b' * 40))
                        with self.assertRaises(RepositoryError):
                            creator.create(draft['draft_id'], 3, 'owner/different', session_id='native')
                    else:
                        self.assertEqual(result['blocker']['code'], 'iteration_paused' if case == 'paused' else 'approval_required')
                        prepare.assert_not_called(); attach.assert_not_called()
                    self.assertNotIn('fixture-only', json.dumps(creator.read(draft['draft_id'])))
                    self.assertEqual(creator.enrollment.store.read(draft['draft_id']), draft)

        # Existing onboard is the real creation protocol; baseline is a mocked next component.
        for case in ('create', 'collision', 'lost-response', 'returned-id-drift', 'absent-after-intent', 'lost-create', 'denied'):
            with self.subTest(onboarding=case), tempfile.TemporaryDirectory() as tmp:
                creator, draft, record, capability = fixture(Path(tmp))
                metadata = {'id': 77, 'full_name': 'owner/product', 'name': 'product', 'owner': {'login': 'owner'},
                            'private': True, 'description': record['marker']}
                state = Path(creator.config['state'])
                state.touch(mode=0o600)
                request = {'project_id': record['project'], 'iteration_id': 'm1', 'repository': record['repository'],
                           'repository_intent': {'mode': 'new', 'marker': record['marker']},
                           'idea': draft['observations'][0]['text'], 'origin': draft['origin'],
                           'approval': {'operator_id': '42', 'reference': capability['reference']}, 'status': 'active'}
                if case in ('lost-response', 'returned-id-drift', 'absent-after-intent'):
                    request['repository_onboarding'] = {'status': 'pending', 'mode': 'new', 'marker': record['marker'], 'api_base': capability['api_base']}
                    if case == 'returned-id-drift':
                        request['repository_onboarding']['created_repository_id'] = 88
                with sqlite3.connect(state) as db:
                    db.execute('CREATE TABLE iterations(project_id TEXT, iteration_id TEXT, payload TEXT, PRIMARY KEY(project_id, iteration_id))')
                    db.execute('INSERT INTO iterations VALUES(?,?,?)', (record['project'], 'm1', json.dumps(request)))
                reads = [None, metadata] if case == 'create' else [None if case in ('absent-after-intent', 'lost-create', 'denied') else metadata]
                def creation_api(path, body=None, method=None):
                    if path == '/user':
                        return {'login': 'owner'}
                    if path == '/user/repos':
                        self.assertEqual(body, {'name': 'product', 'private': True, 'description': record['marker'], 'auto_init': False})
                        with sqlite3.connect(state) as db:
                            saved = json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])
                        self.assertEqual(saved['repository_onboarding']['status'], 'pending')
                        if case in ('lost-create', 'denied'):
                            raise RepositoryError('github_unavailable' if case == 'lost-create' else 'github_blocked', 'Mocked response loss/denial', None if case == 'lost-create' else 403)
                        return metadata
                    self.assertEqual(path, '/repos/owner/product')
                    return reads.pop(0)
                github = Mock(); github.base = capability['api_base']; github.request.side_effect = creation_api
                with patch.object(creation.repositories, 'GitHub', return_value=github), \
                     patch.object(creator, 'baseline') as baseline:
                    if case in ('create', 'lost-response'):
                        creator.prepare(draft, record, capability)
                        self.assertEqual(record['repository_id'], 77)
                        baseline.assert_called_once()
                    else:
                        with self.assertRaises(RepositoryError) as error:
                            creator.prepare(draft, record, capability)
                        self.assertEqual(error.exception.code, {'collision': 'repository_collision', 'returned-id-drift': 'repository_mismatch',
                                                               'absent-after-intent': 'creation_uncertain', 'lost-create': 'creation_uncertain', 'denied': 'github_blocked'}[case])
                        baseline.assert_not_called()
                posts = [c for c in github.request.call_args_list if c.args[0] == '/user/repos']
                self.assertEqual(len(posts), int(case in ('create', 'lost-create', 'denied')))
                with sqlite3.connect(state) as db:
                    retained = json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])
                self.assertEqual(retained['repository_intent'], request['repository_intent'])
                if case in ('create', 'lost-response'):
                    self.assertEqual(retained['repository_onboarding']['metadata']['id'], 77)
                if case in ('lost-create', 'denied'):
                    self.assertEqual(retained['repository_onboarding']['status'], 'pending' if case == 'lost-create' else 'blocked')

        # Baseline initialization/read-only recovery, no intake/onboarding/checkout executed.
        for case in ('initialize', 'replay', 'lost-put', 'unknown-put', 'nonempty', 'commit-drift', 'content-drift', 'pause'):
            with self.subTest(baseline=case), tempfile.TemporaryDirectory() as tmp:
                creator, draft, record, _ = fixture(Path(tmp))
                content = b'# Atlas\n\nBuild agreed Atlas\n'
                blob = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content, usedforsecurity=False).hexdigest()
                intent = {'path': 'README.md', 'blob': blob, 'message': 'Initialize product baseline [' + record['marker'] + ']'}
                if case in ('replay', 'lost-put', 'unknown-put', 'commit-drift', 'content-drift'):
                    record['baseline_intent'] = intent
                if case == 'replay':
                    record['baseline'] = {'commit': 'b' * 40, 'path': 'README.md', 'blob': blob}
                if case == 'commit-drift':
                    record['baseline_returned_commit'] = 'c' * 40
                def api(path, body=None, method=None):
                    if path.endswith('/branches?per_page=1'):
                        return [{'name': 'main'}] if case == 'nonempty' else []
                    if path.endswith('/contents/README.md') and body is None:
                        return None
                    if method == 'PUT':
                        saved = creator.read(draft['draft_id'])
                        assert saved is not None
                        self.assertEqual(saved['baseline_intent'], intent)
                        self.assertEqual(body, {'message': intent['message'], 'content': base64.b64encode(content).decode()})
                        return {'commit': {'sha': 'b' * 40}, 'content': {'sha': blob}}
                    if path.endswith('/commits/HEAD'):
                        if case == 'unknown-put':
                            raise RepositoryError('github_blocked', 'Empty target still uncertain', 409)
                        return {'sha': 'b' * 40, 'parents': [], 'commit': {'message': intent['message']},
                                'files': [{'filename': 'README.md', 'sha': blob, 'status': 'added'}]}
                    self.assertEqual(path, '/repos/owner/product/contents/README.md?ref=' + 'b' * 40)
                    return {'type': 'file', 'path': 'README.md', 'sha': blob, 'encoding': 'base64',
                            'content': base64.b64encode(b'wrong' if case == 'content-drift' else content).decode()}
                github = Mock(); github.request.side_effect = api
                guard = Mock(side_effect=RepositoryError('iteration_paused', 'Paused') if case == 'pause' else None)
                if case in ('initialize', 'replay', 'lost-put'):
                    creator.baseline(record, draft, github, guard)
                    self.assertEqual(record['baseline'], {'commit': 'b' * 40, 'path': 'README.md', 'blob': blob})
                else:
                    with self.assertRaises(RepositoryError):
                        creator.baseline(record, draft, github, guard)
                puts = [c for c in github.request.call_args_list if c.kwargs.get('method') == 'PUT']
                self.assertEqual(len(puts), int(case == 'initialize'))
