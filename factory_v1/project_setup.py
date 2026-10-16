"""Host-scoped existing repository attachment; no execution/service authority is granted."""
import base64
import copy
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess

from . import controls, planning, repositories
from .__main__ import read_iteration, register_iteration, validate_intake
from .assignments import commit
from .conversation_binding import ConversationBinding
from .origin import validate_origin
from .errors import IntakeError
from .planning import require, text
from .readiness import load_config
from .sandbox_source import physical
from .service import atomic, connect, isolated_root


def git_checkout(workspace, repository, revision, bearer, *, create=False):
    """Fixed Git HTTPS adapter. Secrets use child-only config environment, never argv/disk."""
    url = 'https://github.com/' + repository + '.git'
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(workspace.parent), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0', 'GIT_ASKPASS': '/bin/false',
           'GIT_CONFIG_COUNT': '4', 'GIT_CONFIG_KEY_0': 'http.https://github.com/.extraHeader',
           'GIT_CONFIG_VALUE_0': 'Authorization: Basic ' + base64.b64encode(('x-access-token:' + bearer).encode()).decode(),
           'GIT_CONFIG_KEY_1': 'credential.helper', 'GIT_CONFIG_VALUE_1': '',
           'GIT_CONFIG_KEY_2': 'core.hooksPath', 'GIT_CONFIG_VALUE_2': '/dev/null',
           'GIT_CONFIG_KEY_3': 'http.followRedirects', 'GIT_CONFIG_VALUE_3': 'false'}

    def run(*args):
        result = subprocess.run(['/usr/bin/git', *args], env=env, capture_output=True, text=True, timeout=120)
        require(result.returncode == 0, 'Exact checkout unavailable; reconcile the retained workspace, never overwrite it.', 'checkout_blocked')
        return result.stdout.strip()

    if create:
        require(not os.path.lexists(workspace), 'Workspace already exists; reconcile instead of cloning.', 'workspace_conflict')
        run('clone', '--no-checkout', '--no-local', '--', url, str(workspace))
        run('-C', str(workspace), 'checkout', '--detach', revision)
    physical(workspace, directory=True)
    require(run('-C', str(workspace), 'rev-parse', '--show-toplevel') == str(workspace) and
            run('-C', str(workspace), 'remote', 'get-url', 'origin') == url and
            run('-C', str(workspace), 'rev-parse', 'HEAD') == revision,
            'Checkout origin, root or immutable commit differs.', 'workspace_mismatch')
    return {'path': str(workspace), 'repository': repository, 'commit': revision}


def native_project(db_path, name, workspace, previous=None):
    """Selected supported projects_db API only; no native kanban/backlog creation."""
    try:
        from hermes_cli import projects_db
    except ImportError as error:
        raise repositories.RepositoryError('native_project_unavailable', 'Selected native Project API is unavailable.') from error
    with closing(projects_db.connect(db_path=Path(db_path))) as db:
        project = projects_db.find_by_primary_path(db, workspace)
        if previous:
            project = projects_db.get_project(db, previous)
        if project is None:
            require(previous is None, 'Native project disappeared.', 'native_project_mismatch')
            pid = projects_db.create_project(db, name=name, folders=[workspace], primary_path=workspace)
            project = projects_db.get_project(db, pid)
        require(project is not None and not project.archived and project.primary_path == workspace and
                any(f.path == workspace and f.is_primary for f in project.folders) and
                (previous is None or project.id == previous),
                'Native primary folder differs from the exact checkout.', 'native_project_mismatch')
        return project.id


class ProjectSetup:
    """Attach/read/route a retained draft. All identity and capability selectors are host-owned."""

    def __init__(self, enrollment, config):
        fields = {'root', 'workspace_root', 'state', 'capability_config', 'native_project_db'}
        require(isinstance(config, dict) and set(config) == fields, 'Exact host project_setup configuration required.', 'setup_capability_required')
        self.enrollment, self.config = enrollment, copy.deepcopy(config)
        self.root = physical(config['root'], directory=True)
        self.workspaces = physical(config['workspace_root'], directory=True)
        require(self.root != enrollment.store.root, 'Setup and draft snapshots need distinct roots.', 'workspace_conflict')
        for root in (self.root, self.workspaces):
            controls.private(root, directory=True)
            isolated_root(root)
        state = Path(config['state'])
        require(state.is_absolute(), 'Absolute private controller state required.', 'setup_capability_required')
        controls.private(physical(state.parent, directory=True), directory=True)
        isolated_root(state.parent)
        # Separate state/capabilities, drafts and source from the product checkout tree.
        scopes = [self.root, state.parent, enrollment.store.root, Path(__file__).resolve().parent.parent,
                  Path.home() / '.hermes/profiles', Path(config['capability_config']).parent]
        require(all(not self.workspaces.is_relative_to(p) and not p.is_relative_to(self.workspaces) for p in scopes),
                'Product workspaces must be separate from controller state, source and private capabilities.', 'workspace_conflict')

    def path(self, draft_id):
        self.enrollment.store.path(draft_id)  # Reuse exact draft-ID validation.
        return self.root / (draft_id + '.json')

    def read(self, draft_id):
        """Host-only setup journal; includes checkpoints/blocker, never host credentials."""
        path = self.path(draft_id)
        if not path.exists():
            return None
        controls.private(path)
        record = json.loads(path.read_text())
        require(record['draft_id'] == draft_id, 'Setup identity changed.', 'scope_mismatch')
        return record

    def save(self, record):
        atomic(self.path(record['draft_id']), record)

    def capability(self, draft, repository):
        selected = load_config(self.config['capability_config'])
        require(isinstance(selected, dict) and set(selected) == {'operator_id', 'repository', 'reference', 'api_base', 'bearer'} and
                selected['operator_id'] == draft['operator_id'] and selected['repository'] == repository and
                text(selected['reference']) and text(selected['bearer']) and selected['api_base'] == 'https://api.github.com',
                'Approved exact operator/repository HOST capability required.', 'approval_required')
        return selected

    def attach(self, draft_id, expected_revision, repository, *, session_id):
        """Host-only attachment; model-facing tool derives draft/session from admitted context."""
        require(repositories.valid_repository(repository), 'Select an exact owner/repository.', 'invalid_repository')
        require(type(expected_revision) is int and expected_revision >= 0 and text(session_id),
                'Exact draft revision and native session required.', 'scope_mismatch')
        with self.enrollment.store.locked():
            draft = self.enrollment.store.read(draft_id)
            require(draft is not None and draft['revision'] == expected_revision and draft['sufficient'] and
                    not draft['pending_questions'] and isinstance(draft.get('native'), dict) and draft['native'].get('session_id') == session_id,
                    'Current sufficient correlated draft required.', 'planning_conflict')
            record = self.read(draft_id)
            if record is None:
                record = {'version': 1, 'draft_id': draft_id, 'draft_revision': expected_revision,
                          'repository': repository, 'project': 'project-' + draft_id, 'iteration': 'm1',
                          'workspace': str(self.workspaces / draft_id), 'status': 'pending', 'blocker': None}
                self.save(record)
            require(record['repository'] == repository and record['draft_revision'] == expected_revision,
                    'Setup already targets a different repository/draft revision.', 'scope_mismatch')
            try:
                result = self.prepare(draft, record, session_id)
            except (repositories.RepositoryError, IntakeError, OSError, ValueError, subprocess.SubprocessError) as error:
                record.update(status='blocked', blocker={'code': getattr(error, 'code', 'setup_unavailable'),
                    'operation': 'attach-existing', 'consequence': 'Setup retained; no worker dispatched.',
                    'next_decision': 'Restore the exact approved prerequisite or reconcile the retained effect; do not clear independent holds.'})
                self.save(record)
                return copy.deepcopy(record)
        # transfer owns its own lock; association occurs only after independent readback.
        self.enrollment.store.transfer(draft_id, expected_revision, result['transfer'])
        self.enrollment.bound_planning = copy.deepcopy(result['binding'])
        return result

    def prepare(self, draft, record, session_id):
        origin = {k: draft['origin'][k] for k in ('platform', 'chat_id', 'thread_id', 'parent_chat_id', 'scope_id')}
        validate_origin(origin)  # Projection only, no normalization of raw admitted source.
        capability = self.capability(draft, record['repository'])
        github = repositories.GitHub(capability['api_base'], capability['bearer'], 10)
        state = Path(self.config['state'])
        if not state.exists():
            fd = os.open(state, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        controls.private(physical(state))
        with connect(self.config) as db:
            request = {'project_id': record['project'], 'iteration_id': record['iteration'], 'repository': record['repository'],
                       'idea': draft['observations'][0]['text'], 'origin': origin,
                       'approval': {'operator_id': draft['operator_id'], 'reference': capability['reference']}}
            validate_intake(request, draft['operator_id'])
            with db:
                item = register_iteration(db, copy.deepcopy(request))
            self.guard(db, item)
            item = repositories.onboard(db, item, github)
            metadata = item['repository_onboarding']['metadata']
            require(record.get('repository_id', metadata['id']) == metadata['id'], 'Repository identity changed.', 'repository_mismatch')
            record['repository_id'] = metadata['id']
            if 'commit' not in record:
                head = github.request('/repos/' + record['repository'] + '/commits/HEAD')
                require(isinstance(head, dict) and commit(head.get('sha')), 'Existing repository needs a verified initial commit.', 'baseline_required')
                record['commit'] = head['sha']
                self.save(record)
            workspace = Path(record['workspace'])
            self.guard(db, item)
            creating = 'checkout_intent' not in record
            if creating:
                require(not os.path.lexists(workspace), 'Unowned workspace exists.', 'workspace_conflict')
                record['checkout_intent'] = {'repository': record['repository'], 'commit': record['commit'], 'path': str(workspace)}
                self.save(record)
            checkout = git_checkout(workspace, record['repository'], record['commit'], capability['bearer'], create=creating)
            require(checkout == record['checkout_intent'], 'Checkout readback differs.', 'workspace_mismatch')
            record['checkout'] = checkout
            self.save(record)
            self.guard(db, item)
            if self.config['native_project_db'] is not None:
                native_path = physical(self.config['native_project_db'])
                controls.private(native_path)
                record['native_project'] = native_project(str(native_path), draft['product']['name'], str(workspace), record.get('native_project'))
                self.save(record)
            self.guard(db, item)
            with repositories.state_lock(db), db:
                item = planning.plan(db, item, {'origin': origin, 'expected_revision': 0, 'skills': self.enrollment.config['skills']})
            fresh = read_iteration(db, record['project'], record['iteration'])
            self.guard(db, fresh)
            require(all(fresh[k] == v for k, v in request.items()) and
                    fresh['repository_onboarding']['metadata']['id'] == record['repository_id'] and
                    fresh['planning']['origin'] == origin and text(fresh['planning']['assignment_id']),
                    'Registered project/planning readback differs.', 'scope_mismatch')
            binding = {'state': str(state), 'operator_id': draft['operator_id'], 'project': record['project'],
                       'iteration': record['iteration'], 'session_id': session_id, 'origin': origin, 'reference': capability['reference']}
            require(record.get('binding', binding) == binding, 'Native binding changed.', 'scope_mismatch')
            record.update(binding=binding, transfer={'project': record['project'], 'iteration': record['iteration'],
                'assignment_id': fresh['planning']['assignment_id'], 'reference': capability['reference']}, status='ready', blocker=None)
            self.save(record)
            return copy.deepcopy(record)

    @staticmethod
    def guard(db, item):
        controls.require_active(db, item['project_id'], item['iteration_id'])
        require(item['status'] == 'active', 'Intentional pause retains setup without dispatch.', 'iteration_paused')

    def route(self, draft_id):
        """Fresh validated ConversationBinding for T7; no pre-created binding or service needed."""
        record, draft = self.read(draft_id), self.enrollment.store.read(draft_id)
        require(record is not None and draft is not None and record['status'] == 'ready' and draft['transfer'] == record['transfer'],
                'Verified transferred setup required.', 'setup_pending')
        config = record['binding']
        github = repositories.GitHub('https://api.github.com', 'credential-blind-controller', 10)
        native = ConversationBinding(self.enrollment.ctx, config, github)
        with connect(config) as db:
            item, _ = native.current(db, config['session_id'])
            require(item['repository'] == record['repository'] and item['repository_onboarding']['metadata']['id'] == record['repository_id'] and
                    item['planning']['assignment_id'] == record['transfer']['assignment_id'], 'Setup/planning identity drift.', 'scope_mismatch')
        return native

    def tool(self, args, *, session_id='', **kwargs):
        """Attach the explicitly selected existing repository to this sufficient project interview."""
        require(isinstance(args, dict) and set(args) == {'repository', 'expected_revision'}, 'Exact repository choice and revision required.', 'scope_mismatch')
        draft, observed = self.enrollment.correlated(session_id)
        require(isinstance(draft.get('native'), dict) and draft['native'].get('message_id') == observed['message_id'],
                'Current native interview turn required.', 'scope_mismatch')
        return json.dumps(self.attach(draft['draft_id'], args['expected_revision'], args['repository'], session_id=session_id), sort_keys=True)

    def register(self):
        properties = {'repository': {'type': 'string'}, 'expected_revision': {'type': 'integer'}}
        self.enrollment.ctx.register_tool(name='factory_project_attach', toolset='factory_planning', handler=self.tool,
            schema={'name': 'factory_project_attach', 'description': self.tool.__doc__, 'parameters': {
                'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}})
        return self
