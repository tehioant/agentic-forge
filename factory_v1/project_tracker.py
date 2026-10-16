"""Prepare repository-linked Projects v2 and finite native milestone scope."""
import copy
import json

from . import controls, repositories, ticket_github as api
from .__main__ import read_iteration
from .errors import IntakeError
from .planning import require, text
from .project_setup import ProjectSetup
from .readiness import load_config
from .sandbox_source import physical
from .service import atomic, connect, isolated_root

DEFAULT_TRACKER = {'project_id': None, 'status_field': 'Status',
                   'statuses': {'ready': 'Todo', 'active': 'In Progress', 'done': 'Done'},
                   'triage_label': 'triage'}


class TrackerHost:
    """Concrete GitHub operations; only the host-owned capability supplies credentials."""

    def __init__(self, capability):
        self.github = repositories.GitHub('https://api.github.com', capability['bearer'], 10)

    def repository(self, name):
        return self.github.request('/repos/' + name)

    def board(self, project):
        result = api.graph(self.github, '''query TrackerBoard($id:ID!){node(id:$id){... on ProjectV2{
          id number title public closed owner{... on User{id login} ... on Organization{id login}}
          items(first:1){nodes{id} pageInfo{hasNextPage endCursor}}
          views(first:100){nodes{id name layout verticalGroupByFields(first:100){
            nodes{... on ProjectV2SingleSelectField{id}} pageInfo{hasNextPage endCursor}}}
            pageInfo{hasNextPage endCursor}}}}}''', {'id': project}).get('node')
        require(isinstance(result, dict) and result.get('id') == project and
                type(result.get('number')) is int and type(result.get('public')) is bool and
                result.get('closed') is False, 'Exact open board readback unavailable.', 'tracker_readback')
        return result

    def create_board(self, repository, title):
        result = api.graph(self.github, '''mutation TrackerCreate($input:CreateProjectV2Input!){
          createProjectV2(input:$input){projectV2{id}}}''',
          {'input': {'ownerId': repository['owner']['node_id'], 'repositoryId': repository['node_id'],
                     'title': title}})
        project = result.get('createProjectV2', {}).get('projectV2', {}).get('id')
        require(text(project), 'Board creation outcome requires reconciliation.', 'tracker_uncertain')
        return project

    def private_board(self, project):
        api.graph(self.github, '''mutation TrackerPrivate($input:UpdateProjectV2Input!){
          updateProjectV2(input:$input){projectV2{id public}}}''',
          {'input': {'projectId': project, 'public': False}})

    def link(self, project, repository):
        api.graph(self.github, '''mutation TrackerLink($input:LinkProjectV2ToRepositoryInput!){
          linkProjectV2ToRepository(input:$input){repository{id}}}''',
          {'input': {'projectId': project, 'repositoryId': repository['node_id']}})

    def status(self, field):
        api.graph(self.github, '''mutation TrackerStatus($input:UpdateProjectV2FieldInput!){
          updateProjectV2Field(input:$input){projectV2Field{... on ProjectV2SingleSelectField{id}}}}''',
          {'input': {'fieldId': field, 'singleSelectOptions': [
              {'name': name, 'color': color, 'description': ''}
              for name, color in [('Todo', 'GRAY'), ('In Progress', 'YELLOW'), ('Done', 'GREEN')]]}})

    def field_database_id(self, field):
        result = api.graph(self.github, '''query TrackerField($id:ID!){node(id:$id){
          ... on ProjectV2SingleSelectField{id databaseId}}}''', {'id': field}).get('node')
        require(isinstance(result, dict) and result.get('id') == field and
                str(result.get('databaseId', '')).isdigit(),
                'Status numeric field identity unavailable.', 'tracker_readback')
        return int(result['databaseId'])

    def view(self, board, repository, field_database_id):
        owner = repository['owner']
        prefix = '/orgs/' + owner['login'] if owner['type'] == 'Organization' else '/users/' + str(owner['id'])
        self.github.request(prefix + '/projectsV2/' + str(board['number']) + '/views',
                            {'name': 'Status', 'layout': 'board', 'filter': '',
                             'vertical_group_by': [field_database_id]})

    def milestone(self, repository, title, scope):
        return api.milestone_identity(self.github.request('/repos/' + repository + '/milestones',
                            {'title': title, 'state': 'open', 'description': scope}))


def status_view(board, field):
    views, info = api.connection(board.get('views'))
    require(not info['hasNextPage'], 'Complete board views required.', 'tracker_readback')
    found = []
    for view in views:
        require(isinstance(view, dict) and text(view.get('id')), 'Invalid board view.', 'tracker_readback')
        groups, page = api.connection(view.get('verticalGroupByFields'))
        require(not page['hasNextPage'], 'Complete view grouping required.', 'tracker_readback')
        if view.get('layout') == 'BOARD_LAYOUT' and [g.get('id') for g in groups] == [field]:
            found.append(view['id'])
    return found


class ProjectTracker:
    """Host-only prepare/read/tracker_config contract. No publication or dispatch authority."""

    def __init__(self, setup, config):
        require(isinstance(config, dict) and set(config) == {'root', 'capability_config', 'tracker'},
                'Host tracker capability required.', 'tracker_capability_required')
        self.setup, self.config = setup, copy.deepcopy(config)
        self.root = physical(config['root'], directory=True)
        controls.private(self.root, directory=True)
        isolated_root(self.root)
        require(self.root != setup.root and self.root != setup.enrollment.store.root,
                'Separate tracker journal required.', 'scope_mismatch')

    def path(self, draft_id):
        self.setup.enrollment.store.path(draft_id)
        return self.root / (draft_id + '.json')

    def read(self, draft_id):
        """Return retained identities, intents and precise blocker, without credentials."""
        path = self.path(draft_id)
        if not path.exists():
            return None
        controls.private(path)
        record = json.loads(path.read_text(), object_pairs_hook=repositories.unambiguous_fields)
        require(record['draft_id'] == draft_id, 'Tracker draft identity changed.', 'scope_mismatch')
        return record

    def save(self, record):
        atomic(self.path(record['draft_id']), record)

    def guard(self, attached):
        with connect(attached['binding']) as db:
            item = read_iteration(db, attached['project'], attached['iteration'])
            ProjectSetup.guard(db, item)
            require(item['repository'] == attached['repository'] and
                    item['repository_onboarding']['metadata']['id'] == attached['repository_id'] and
                    item['planning']['assignment_id'] == attached['transfer']['assignment_id'],
                    'Repository/planning authority drift.', 'scope_mismatch')

    def effect(self, record, attached, capability, operation, payload, action):
        record['pending_operation'] = operation
        self.guard(attached)
        require(load_config(self.config['capability_config']) == capability,
                'Host tracker authority changed during preparation.', 'tracker_permission_required')
        require(operation in capability['operations'],
                'Approved host permission missing for ' + operation + '.', 'tracker_permission_required')
        require(operation not in record['intents'],
                'Unknown ' + operation + ' outcome; reconcile exact retained intent before retry.', 'tracker_uncertain')
        record['intents'][operation] = copy.deepcopy(payload)
        self.save(record)
        try:
            return action()
        except (repositories.RepositoryError, IntakeError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise repositories.RepositoryError('tracker_uncertain',
                'Reconcile the retained ' + operation + ' intent; its response is not proof of absence.') from error

    def prepare(self, draft_id, expected_revision):
        """Resolve exact attachment and scope, then prepare only approved missing resources."""
        with self.setup.enrollment.store.locked():
            draft = self.setup.enrollment.store.read(draft_id)
            attached = self.setup.read(draft_id)
            require(draft is not None and attached is not None and draft['revision'] == expected_revision and
                    attached['draft_revision'] == expected_revision and attached['status'] == 'ready' and
                    draft.get('transfer') == attached.get('transfer') and draft['sufficient'] and
                    not draft['pending_questions'] and text(draft['product'].get('milestone')),
                    'Verified T2 binding and agreed finite milestone required.', 'tracker_setup_required')
            record = self.read(draft_id)
            if record is None:
                record = {'version': 1, 'draft_id': draft_id, 'draft_revision': expected_revision,
                          'repository': attached['repository'], 'repository_id': attached['repository_id'],
                          'binding': copy.deepcopy(attached['binding']), 'scope': draft['product']['milestone'],
                          'iteration': attached['iteration'], 'intents': {}, 'status': 'pending', 'blocker': None}
                self.save(record)
            try:
                require(all(record[k] == v for k, v in {
                    'draft_revision': expected_revision, 'repository': attached['repository'],
                    'repository_id': attached['repository_id'], 'binding': attached['binding'],
                    'scope': draft['product']['milestone'], 'iteration': attached['iteration']}.items()),
                    'Retained tracker scope changed.', 'scope_mismatch')
                self.guard(attached)
                self.setup.route(draft_id)  # Actual T2 native binding revalidation, not another backlog.
                capability = load_config(self.config['capability_config'])
                require(isinstance(capability, dict) and set(capability) == {
                    'operator_id', 'repository', 'reference', 'bearer', 'operations'} and
                    capability['operator_id'] == draft['operator_id'] and
                    capability['repository'] == record['repository'] and
                    text(capability['reference']) and text(capability['bearer']) and
                    isinstance(capability['operations'], list) and
                    all(op in {'create_board', 'private_board', 'link', 'status', 'view', 'milestone'}
                        for op in capability['operations']),
                    'Exact approved tracker host capability required.', 'tracker_capability_required')
                host = TrackerHost(capability)
                self.resolve(record, attached, capability, host, draft)
                self.guard(attached)
                record.update(status='ready', blocker=None)
            except (repositories.RepositoryError, IntakeError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                code = getattr(error, 'code', 'tracker_readback')
                record.update(status='blocked', blocker={
                    'code': code, 'operation': 'prepare-tracker', 'draft_id': draft_id,
                    'effect_operation': record.get('pending_operation'),
                    'cause': ('uncertain' if code == 'tracker_uncertain' else
                              'permission' if code in {'tracker_permission_required', 'tracker_capability_required'} else 'platform'),
                    'scope': {'draft_revision': expected_revision, 'repository': record['repository'],
                              'repository_id': record['repository_id'], 'iteration': record['iteration'],
                              'project_id': record.get('project_id')},
                    'consequence': 'Tracker setup retained; no ticket publication or worker dispatch.',
                    'next_decision': ('Reconcile exact retained effect identities.' if code == 'tracker_uncertain' else
                                      'Restore the scoped host permission or resolve the exact target ambiguity.'),
                    'detail': str(error) if isinstance(error, (repositories.RepositoryError, IntakeError)) else
                              'Exact tracker response unavailable.',
                    'reconciliation_required': code == 'tracker_uncertain'})
            self.save(record)
            return copy.deepcopy(record)

    def resolve(self, record, attached, capability, host, draft):
        repository = record['repository']
        repo = host.repository(repository)
        require(isinstance(repo, dict) and repo.get('id') == record['repository_id'] and
                repo.get('full_name') == repository and text(repo.get('node_id')) and
                isinstance(repo.get('owner'), dict) and repo['owner'].get('type') in {'User', 'Organization'} and
                repo['owner'].get('login') == repository.split('/')[0] and text(repo['owner'].get('node_id')) and
                type(repo['owner'].get('id')) is int,
                'Exact repository immutable readback differs.', 'scope_mismatch')
        tracker = copy.deepcopy(self.config['tracker'])
        require(isinstance(tracker, dict) and set(tracker) == set(DEFAULT_TRACKER),
                'Existing tracker schema contract required.', 'tracker_schema')
        require(record.get('configuration', tracker) == tracker, 'Tracker selection changed.', 'scope_mismatch')
        record['configuration'] = copy.deepcopy(tracker)
        linked = api.linked_projects(host.github, repository)
        selected = [b for b in linked if not b['closed'] and
                    (tracker['project_id'] is None or b['id'] == tracker['project_id'])]
        require(len(selected) <= 1, 'Resolve ambiguous linked board selection.', 'tracker_ambiguous')
        if 'project_id' not in record:
            require('create_board' not in record['intents'],
                    'Board creation response lost; reconcile its immutable ID before adoption.', 'tracker_uncertain')
            if selected:
                record.update(project_id=selected[0]['id'], created=False)
            else:
                require(not linked and tracker['project_id'] is None,
                        'Selected board unavailable; do not substitute an unrelated board.', 'tracker_ambiguous')
                require(tracker == DEFAULT_TRACKER, 'New boards use the default Status schema.', 'tracker_schema')
                project = self.effect(record, attached, capability, 'create_board',
                    {'repository_node_id': repo['node_id'], 'owner_id': repo['owner']['node_id'],
                     'title': draft['product']['name']}, lambda: host.create_board(repo, draft['product']['name']))
                record.update(project_id=project, created=True)
            self.save(record)
        project = record['project_id']
        require(not selected or selected[0]['id'] == project, 'Linked board identity drift.', 'tracker_ambiguous')
        board = host.board(project)
        if record['created']:
            require(board['owner']['id'] == repo['owner']['node_id'], 'Board owner differs.', 'scope_mismatch')
            if board['public']:
                self.effect(record, attached, capability, 'private_board', {'project': project, 'public': False},
                            lambda: host.private_board(project))
                board = host.board(project)
                require(board['public'] is False, 'Board privacy readback failed.', 'tracker_readback')
            if not any(b['id'] == project for b in linked):
                self.effect(record, attached, capability, 'link', {'project': project, 'repository': repo['node_id']},
                            lambda: host.link(project, repo))
                require(any(b['id'] == project for b in api.linked_projects(host.github, repository)),
                        'Repository link readback failed.', 'tracker_readback')
            fields = api.project_fields(host.github, project)
            fields = [f for f in fields if f.get('name') == 'Status']
            require(len(fields) == 1 and text(fields[0].get('id')) and isinstance(fields[0].get('options'), list),
                    'New board Status field missing or ambiguous.', 'tracker_schema')
            if [o['name'] for o in fields[0]['options']] != ['Todo', 'In Progress', 'Done']:
                nodes, info = api.connection(board.get('items'))
                require(not nodes and not info['hasNextPage'], 'Never overwrite progress on a populated board.', 'tracker_schema')
                self.effect(record, attached, capability, 'status', {'field': fields[0]['id'], 'names': DEFAULT_TRACKER['statuses']},
                            lambda: host.status(fields[0]['id']))
        tracker['project_id'] = project
        resolved = api.resolve_board(host.github, repository, tracker)
        if record['created'] and not status_view(board, resolved['status']['id']):
            field_database_id = host.field_database_id(resolved['status']['id'])
            self.effect(record, attached, capability, 'view', {'project': project, 'field': resolved['status']['id'],
                        'layout': 'BOARD_LAYOUT'}, lambda: host.view(board, repo, field_database_id))
            board = host.board(project)
            require(status_view(board, resolved['status']['id']), 'Status board view readback failed.', 'tracker_readback')
        milestones = api.rest_pages(host.github, '/repos/' + repository + '/milestones?state=all')
        matches = [m for m in milestones if m.get('title') == record['iteration']]
        require(len(matches) <= 1 and (not matches or matches[0].get('state') == 'open'),
                'Resolve current milestone ambiguity or closed scope.', 'tracker_ambiguous')
        if not matches:
            receipt = self.effect(record, attached, capability, 'milestone', {'title': record['iteration'], 'scope': record['scope']},
                        lambda: host.milestone(repository, record['iteration'], record['scope']))
            record['milestone_receipt'] = receipt
            self.save(record)
        milestone = api.resolve_milestone(host.github, repository, record['iteration'])
        require(record.get('milestone_receipt', milestone) == milestone,
                'Created milestone immutable identity differs.', 'tracker_readback')
        if matches:
            require(matches[0].get('description') == record['scope'],
                    'Current milestone differs from agreed scope; resolve instead of overwriting.', 'tracker_ambiguous')
        if 'milestone' in record['intents']:
            actual = host.github.request('/repos/' + repository + '/milestones/' + str(milestone['number']))
            require(api.milestone_identity(actual) == milestone and actual.get('description') == record['scope'],
                    'Created milestone scope readback differs.', 'tracker_readback')
        identities = dict(resolved, milestone=milestone)
        require(record.get('identities', identities) == identities, 'Tracker identity drift.', 'scope_mismatch')
        record.update(identities=identities, tracker=tracker)
        self.save(record)

    def tracker_config(self, draft_id):
        """Freshly validate and return existing ticket synthesis tracker configuration."""
        record = self.read(draft_id)
        require(record is not None, 'Prepare tracker first.', 'tracker_setup_required')
        fresh = self.prepare(draft_id, record['draft_revision'])
        require(fresh['status'] == 'ready', 'Tracker setup is held; inspect read().blocker.', 'tracker_setup_required')
        return copy.deepcopy(fresh['tracker'])
