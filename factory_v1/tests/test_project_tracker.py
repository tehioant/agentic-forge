"""Direct tracker-setup component; raw HTTP boundary mocked, no precursor execution."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_v1 import project_tracker as tracker
from factory_v1.conversation_enrollment import DraftStore
from factory_v1.repositories import RepositoryError


def connection(nodes):
    return {'nodes': nodes, 'pageInfo': {'hasNextPage': False, 'endCursor': None}}


class ProjectTrackerTests(unittest.TestCase):
    def test_tracker_preparation_readback_and_replay(self):
        cases = ('existing', 'new', 'ambiguous', 'schema', 'denied', 'paused', 'identity',
                 'lost-create', 'partial-link', 'lost-view', 'view-readback', 'milestone-readback', 'drift', 'pause-before-view')
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name in ('drafts', 'setup', 'tracker', 'caps'):
                    (root / name).mkdir(mode=0o700)
                draft_id = 'a' * 64
                setup = Mock()
                setup.root = root / 'setup'
                setup.enrollment.store = DraftStore(root / 'drafts')
                binding = {'state': '/host/private/state', 'session_id': 'actual-native'}
                attached = {'draft_revision': 3, 'status': 'ready', 'repository': 'owner/product',
                            'repository_id': 77, 'binding': binding, 'transfer': {'assignment_id': 'native-plan'},
                            'project': 'product', 'iteration': 'm1'}
                setup.read.return_value = attached
                draft = {'draft_id': draft_id, 'revision': 3, 'operator_id': '42', 'sufficient': True,
                         'pending_questions': [], 'transfer': attached['transfer'],
                         'product': {'name': 'Atlas', 'milestone': 'Deliver bounded search and its unit checks.'}}
                setup.enrollment.store.save(draft)
                capability = {'operator_id': '42', 'repository': 'owner/product', 'reference': 'host-approval',
                              'bearer': 'mock-host-only', 'operations': ['create_board', 'private_board', 'link', 'status', 'view', 'milestone']}
                if case == 'denied':
                    capability['operations'].remove('create_board')
                cap = root / 'caps/tracker.json'
                cap.write_text(json.dumps(capability)); cap.chmod(0o600)
                service = tracker.ProjectTracker(setup, {'root': str(root / 'tracker'),
                    'capability_config': str(cap), 'tracker': tracker.DEFAULT_TRACKER})
                new = case not in ('existing', 'ambiguous', 'schema', 'identity', 'drift')
                repo = {'id': 78 if case == 'identity' else 77, 'node_id': 'R_exact', 'full_name': 'owner/product',
                        'owner': {'id': 123, 'node_id': 'O_exact', 'login': 'owner', 'type': 'Organization'}}
                board = {'id': 'P_exact', 'number': 2, 'title': 'Atlas', 'public': new, 'closed': False,
                         'owner': {'id': 'O_exact', 'login': 'owner'}, 'items': connection([]), 'views': connection([])}
                linked = [] if new else [{'id': 'P_exact', 'title': 'Atlas', 'closed': False}]
                if case == 'ambiguous':
                    linked.append({'id': 'P_other', 'title': 'Unrelated', 'closed': False})
                options = ['Backlog'] if new else ['Todo', 'In Progress', 'Done', 'Deferred']
                if case == 'schema':
                    options.append('Todo')
                milestone = {'id': 555, 'number': 1, 'title': 'm1', 'state': 'open',
                             'description': draft['product']['milestone']}
                milestones = [] if new else [milestone]
                mutations = []

                def request(path, body=None, method=None):
                    if path == '/repos/owner/product':
                        return copy.deepcopy(repo)
                    if path == '/repos/owner/product/milestones/1':
                        value = copy.deepcopy(milestone)
                        if case == 'milestone-readback':
                            value['description'] = 'wrong scope'
                        return value
                    if path.startswith('/repos/owner/product/milestones?'):
                        return copy.deepcopy(milestones)
                    if body is not None and path != '/graphql':
                        operation = 'view' if '/projectsV2/' in path else 'milestone'
                        self.assertIn(operation, service.read(draft_id)['intents'])
                        mutations.append(operation)
                        if operation == 'milestone':
                            self.assertEqual(body, {'title': 'm1', 'state': 'open', 'description': draft['product']['milestone']})
                            milestones.append(copy.deepcopy(milestone))
                            return copy.deepcopy(milestone)
                        elif case != 'view-readback':
                            self.assertEqual(path, '/orgs/owner/projectsV2/2/views')
                            self.assertEqual(body, {'name': 'Status', 'layout': 'board', 'filter': '', 'vertical_group_by': [987]})
                            board['views'] = connection([{'id': 'V_actual', 'name': 'Status', 'layout': 'BOARD_LAYOUT',
                                'verticalGroupByFields': connection([{'id': 'F_actual'}])}])
                        if case == 'lost-view' and operation == 'view':
                            raise RepositoryError('github_blocked', 'Response lost')
                        return {'id': 'returned'}
                    self.assertEqual(path, '/graphql')
                    query, variables = body['query'], body['variables']
                    if 'mutation ' in query:
                        operation = next(op for op, token in [('create_board', 'createProjectV2('),
                            ('private_board', 'updateProjectV2('), ('link', 'linkProjectV2ToRepository('),
                            ('status', 'updateProjectV2Field(')] if token in query)
                        self.assertIn(operation, service.read(draft_id)['intents'])
                        mutations.append(operation)
                        if operation == 'create_board':
                            self.assertEqual(variables['input'], {'ownerId': 'O_exact', 'repositoryId': 'R_exact', 'title': 'Atlas'})
                            if case == 'lost-create':
                                raise RepositoryError('github_blocked', 'Response lost')
                            return {'data': {'createProjectV2': {'projectV2': {'id': 'P_exact'}}}}
                        if operation == 'private_board':
                            self.assertIs(variables['input']['public'], False)
                            board['public'] = False
                        if operation == 'link':
                            linked.append({'id': 'P_exact', 'title': 'Atlas', 'closed': False})
                        if operation == 'status':
                            options[:] = [o['name'] for o in variables['input']['singleSelectOptions']]
                        return {'data': {}}
                    if 'LinkedProjects' in query:
                        return {'data': {'repository': {'projectsV2': connection(copy.deepcopy(linked))}}}
                    if 'ProjectFields' in query:
                        return {'data': {'node': {'id': 'P_exact', 'fields': connection([{'id': 'F_actual', 'name': 'Status',
                            'options': [{'id': 'option-' + str(i), 'name': n} for i, n in enumerate(options)]}])}}}
                    if 'TrackerField' in query:
                        self.assertIn('databaseId', query)
                        self.assertNotIn('fullDatabaseId', query)
                        return {'data': {'node': {'id': 'F_actual', 'databaseId': '987'}}}
                    if 'TrackerBoard' in query:
                        return {'data': {'node': copy.deepcopy(board)}}
                    self.fail('Unexpected API operation: ' + query)

                if case == 'partial-link':
                    service.save({'version': 1, 'draft_id': draft_id, 'draft_revision': 3,
                        'repository': 'owner/product', 'repository_id': 77, 'binding': binding,
                        'scope': draft['product']['milestone'], 'iteration': 'm1', 'intents': {'create_board': {}},
                        'project_id': 'P_exact', 'created': True, 'status': 'pending', 'blocker': None})
                guard = RepositoryError('iteration_paused', 'Pause retained') if case == 'paused' else None
                def active(attached):
                    if guard is not None:
                        raise guard
                    if case == 'pause-before-view' and 'status' in mutations:
                        raise RepositoryError('iteration_paused', 'Paused after last schema read')

                with patch.object(tracker.ProjectTracker, 'guard', side_effect=active), \
                     patch.object(tracker.repositories.GitHub, 'request', side_effect=request):
                    result = service.prepare(draft_id, 3)
                    if case in ('existing', 'new', 'partial-link', 'drift'):
                        self.assertEqual(result['status'], 'ready', result.get('blocker'))
                        self.assertEqual(result['identities']['status']['id'], 'F_actual')
                        self.assertEqual(result['identities']['milestone'], {'id': 555, 'number': 1, 'title': 'm1'})
                        before = list(mutations)
                        self.assertEqual(service.tracker_config(draft_id)['project_id'], 'P_exact')
                        self.assertEqual(mutations, before)
                        if case == 'existing':
                            self.assertEqual(mutations, [])
                            self.assertIn('Deferred', result['identities']['status']['options'])
                        if case == 'new':
                            self.assertEqual(mutations, ['create_board', 'private_board', 'link', 'status', 'view', 'milestone'])
                        if case == 'partial-link':
                            self.assertNotIn('create_board', mutations)
                        if case == 'drift':
                            options[:] = ['Todo', 'In Progress', 'Done']
                            options.reverse()
                            self.assertEqual(service.prepare(draft_id, 3)['blocker']['code'], 'scope_mismatch')
                    else:
                        self.assertEqual(result['status'], 'blocked')
                        if case == 'lost-view':
                            before = list(mutations)
                            restored = service.prepare(draft_id, 3)
                            self.assertEqual(restored['status'], 'ready', restored['blocker'])
                            self.assertEqual(mutations.count('view'), 1)
                            self.assertEqual(mutations, before + ['milestone'])
                        elif case == 'lost-create':
                            restored = service.prepare(draft_id, 3)
                            self.assertEqual(restored['blocker']['code'], 'tracker_uncertain')
                            self.assertEqual(mutations, ['create_board'])
                        elif case in ('ambiguous', 'schema', 'identity', 'denied', 'paused'):
                            self.assertEqual(mutations, [])
                    if case == 'pause-before-view':
                        self.assertEqual(result['blocker']['code'], 'iteration_paused')
                        self.assertNotIn('view', mutations)
                        self.assertNotIn('milestone', mutations)
                    if case == 'denied':
                        self.assertEqual(result['blocker']['effect_operation'], 'create_board')
                        self.assertEqual(result['blocker']['cause'], 'permission')
                    self.assertNotIn('mock-host-only', json.dumps(service.read(draft_id)))
                    self.assertEqual(setup.enrollment.store.read(draft_id), draft)
