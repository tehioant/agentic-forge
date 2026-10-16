"""Bind generated planning scope only to explicit, finite host delegations."""
import copy
from pathlib import Path

from . import assignments, controls, planning, planning_publication, planning_wakeup, readiness, repositories, service
from .planning import require, text
from .project_execution import retain
from .sandbox_source import physical

FIELDS = {'operator_id', 'reference', 'routes'}
ROUTE_FIELDS = {'repository', 'repository_id', 'private', 'parent', 'credential_config',
                'profile_name', 'source_digest', 'allowance', 'input_envelope'}


def inventory(path):
    selected = readiness.load_config(path)
    require(isinstance(selected, dict) and set(selected) == FIELDS and
            text(selected['operator_id']) and text(selected['reference']) and
            isinstance(selected['routes'], list) and 1 <= len(selected['routes']) <= 100 and
            all(isinstance(row, dict) and set(row) == ROUTE_FIELDS and
                repositories.valid_repository(row['repository']) and
                type(row['repository_id']) is int and row['repository_id'] > 0 and
                type(row['private']) is bool and assignments.commit(row['parent']) and
                all(text(row[k]) for k in ('credential_config', 'profile_name', 'source_digest'))
                for row in selected['routes']) and
            len({row['repository'] for row in selected['routes']}) == len(selected['routes']),
            'Exact finite reviewed native routes required.', 'approval_required')
    return selected


def publication_authority(config):
    """Concrete host reader, also called by the publisher's existing final guards."""
    selected = inventory(config['delegation_config'])
    require(assignments.digest(selected) == config['delegation_pin'] and
            selected['operator_id'] == config['operator_id'] and selected['reference'] == config['reference'],
            'Native delegation changed; never repin a live route.', 'scope_mismatch')
    routes = [row for row in selected['routes'] if row['repository'] == config['repository']]
    require(len(routes) == 1, 'Repository not explicitly delegated.', 'approval_required')
    row = routes[0]
    require(all(row[k] == config[k] for k in ('repository_id', 'private', 'parent', 'credential_config')),
            'Approved native repository scope changed.', 'scope_mismatch')
    credential = readiness.load_config(row['credential_config'])
    require(isinstance(credential, dict) and set(credential) ==
            {'operator_id', 'repository', 'reference', 'api_base', 'bearer'} and
            credential['operator_id'] == selected['operator_id'] and credential['repository'] == row['repository'] and
            text(credential['reference']) and text(credential['bearer']) and credential['api_base'] == config['api_base'] == 'https://api.github.com',
            'Exact host-only repository credential required.', 'approval_required')
    github = repositories.GitHub(credential['api_base'], credential['bearer'], 10)
    prefix = '/repos/' + row['repository']
    metadata = repositories.validate_metadata(github.request(prefix), row['repository'])
    head = github.request(prefix + '/commits/HEAD')
    require(metadata['id'] == row['repository_id'] and metadata['private'] is row['private'] and
            isinstance(head, dict) and head.get('sha') == row['parent'],
            'Repository identity/privacy/current parent drifted.', 'repository_mismatch')
    return credential['bearer'], github


class NativeContext:
    """Preserve native lifecycle while rechecking the host delegation each tick."""

    def __init__(self, ctx, path, operator):
        self.ctx, self.path, self.operator = ctx, path, operator

    def __getattr__(self, name):
        return getattr(self.ctx, name)

    def get_config(self, name):
        planning_publication.configuration(self.path, self.operator)
        return self.ctx.get_config(name)

    def inject_message(self, content, **kwargs):
        planning_publication.configuration(self.path, self.operator)
        return self.ctx.inject_message(content, **kwargs)


class NativeReader:
    """Read-only exact planning metadata/docs; no arbitrary host credential transport."""

    def __init__(self, path, operator, docs_path):
        self.path, self.operator, self.docs_path = path, operator, docs_path
        self.base = 'https://api.github.com'

    def request(self, path, body=None, method=None):
        config = planning_publication.configuration(self.path, self.operator)
        prefix = '/repos/' + config['repository']
        import re
        suffix = path[len(prefix):] if path.startswith(prefix) else None
        allowed = suffix == '' or (isinstance(suffix, str) and (
            re.fullmatch(r'/git/commits/[0-9a-f]{40}|/issues/[1-9][0-9]*', suffix) is not None or
            any(re.fullmatch(re.escape('/contents/' + self.docs_path + '/' + name) + r'\?ref=[0-9a-f]{40}', suffix)
                for name in ('milestone.md', 'vision.md', 'provenance.md'))))
        require(allowed and body is None and method in (None, 'GET'),
                'Read outside exact native planning publication.', 'capability_blocked')
        github = repositories.GitHub(config['api_base'], config['bearer'], 10)
        return github.request(path)


class NativeProject:
    """Private host inventory selection and native-owned subscriber lifecycle only."""

    def __init__(self, enrollment, setup, execution, config):
        require(isinstance(config, dict) and set(config) == {'root', 'capability_config'},
                'Exact native host selection required.', 'approval_required')
        self.enrollment, self.setup, self.execution = enrollment, setup, execution
        self.config = copy.deepcopy(config)
        self.root = physical(config['root'], directory=True)
        controls.private(self.root, directory=True)
        service.isolated_root(self.root)
        for other in (enrollment.store.root, setup.root, setup.workspaces, Path(setup.config['state']).parent,
                      Path(config['capability_config']).parent):
            require(not self.root.is_relative_to(other) and not other.is_relative_to(self.root),
                    'Native private route state must be separate.', 'workspace_conflict')
        self.routes = {}
        self.closed = False

    def resolve(self, draft_id):
        require(not self.closed, 'Native route owner is closed.', 'service_stopping')
        self.enrollment.store.path(draft_id)
        draft, attached = self.enrollment.store.read(draft_id), self.setup.read(draft_id)
        require(draft is not None and attached is not None and attached['status'] == 'ready' and
                draft['revision'] == attached['draft_revision'] and draft['sufficient'] and not draft['pending_questions'] and
                draft.get('transfer') == attached['transfer'], 'Verified current transferred project required.', 'setup_pending')
        assert draft is not None and attached is not None
        selected = inventory(self.config['capability_config'])
        rows = [row for row in selected['routes'] if row['repository'] == attached['repository']]
        require(selected['operator_id'] == draft['operator_id'] and len(rows) == 1,
                'No explicit host delegation for this operator/repository.', 'approval_required')
        row, binding = rows[0], attached['binding']
        require(row['repository_id'] == attached['repository_id'] and row['parent'] == attached['commit'] and
                binding['operator_id'] == draft['operator_id'] and binding['session_id'] == draft['native']['session_id'] and
                binding['origin'] == {k: draft['origin'][k] for k in binding['origin']} and
                binding['project'] == attached['project'] and binding['iteration'] == attached['iteration'],
                'Native setup/session/origin scope changed.', 'scope_mismatch')
        native = self.setup.route(draft_id)
        require(native.config == binding, 'Native binding differs from setup.', 'scope_mismatch')
        with service.connect(binding) as db:
            item, sources = native.current(db, binding['session_id'])
        work = item['planning']
        require(item['repository'] == row['repository'] and item['repository_onboarding']['metadata']['id'] == row['repository_id'] and
                item['repository_onboarding']['metadata']['private'] is row['private'] and
                work['assignment_id'] == attached['transfer']['assignment_id'] and
                assignments.digest({'skills': sources, 'adaptation': planning.INSTRUCTIONS}) == row['source_digest'],
                'Actual assignment, repository or reviewed native source changed.', 'scope_mismatch')
        run = self.root / draft_id
        run.mkdir(mode=0o700, exist_ok=True)
        controls.private(physical(run, directory=True), directory=True)
        paths = {k + '_config': str(run / (k + '.json')) for k in ('publication', 'wakeup')}
        publisher = {k: row[k] for k in ('repository', 'repository_id', 'private', 'parent', 'credential_config')}
        publisher.update(operator_id=draft['operator_id'], reference=selected['reference'], api_base='https://api.github.com',
            assignment_id=work['assignment_id'], session_id=binding['session_id'],
            delegation_config=self.config['capability_config'], delegation_pin=assignments.digest(selected))
        publication_authority(publisher)
        wakeup = {k: copy.deepcopy(row[k]) for k in ('profile_name', 'source_digest', 'allowance', 'input_envelope')}
        wakeup.update(binding=copy.deepcopy(binding), assignment_id=work['assignment_id'])
        # Each explicit finite route is claimed once, including its original deadline.
        claim = {'draft_id': draft_id, 'binding': binding, 'assignment_id': work['assignment_id'],
                 'publication': publisher, 'wakeup': wakeup}
        with self.enrollment.store.locked():
            require(inventory(self.config['capability_config']) == selected and
                    self.enrollment.store.read(draft_id) == draft and self.setup.read(draft_id) == attached,
                    'Selection changed during native binding.', 'scope_mismatch')
            retain(self.root / (assignments.digest([self.config['capability_config'], row['repository']]) + '.json'), claim)
            retain(Path(paths['publication_config']), publisher)
            retain(Path(paths['wakeup_config']), wakeup)
        planning_publication.configuration(paths['publication_config'], draft['operator_id'])
        planning_wakeup.configuration(paths['wakeup_config'], new_execution=work['status'] != 'complete')
        ctx = native.ctx
        require(ctx.profile_name == row['profile_name'] and ctx.get_config('provider_route') == row['allowance']['provider_route'],
                'Actual native profile/provider route differs.', 'scope_mismatch')
        return paths

    def bind(self, native, draft_id):
        """Return the same binding; replay reuses its one native-owned subscriber."""
        try:
            paths = self.resolve(draft_id)
            require(native.config == self.setup.read(draft_id)['binding'], 'Native route changed.', 'scope_mismatch')
            with service.connect(native.config) as db:
                item, _ = native.current(db, native.config['session_id'])
            completed = item['planning']['status'] == 'complete'
            old = self.routes.get(draft_id)
            if old is not None:
                require(old.config == native.config and old.publication_config == paths['publication_config'] and
                        ((completed and old.wakeup is None) or
                         (old.wakeup is not None and not old.wakeup.stopped and old.wakeup.status != 'held')),
                        'Existing native subscriber stopped or changed; reconcile.', 'execution_uncertain')
                native.github, native.publication_config, native.wakeup = old.github, old.publication_config, old.wakeup
                return native
            require(callable(getattr(native.ctx, 'spawn_task', None)) and callable(getattr(native.ctx, 'on_unload', None)),
                    'Native host task/unload lifecycle unavailable.', 'native_lifecycle_unavailable')
            native.publication_config = paths['publication_config']
            native.github = NativeReader(paths['publication_config'], native.config['operator_id'], item['planning']['docs_path'])
            # An already completed original turn needs receipt consumption, not a new turn
            # or an unexpired execution allowance. supervise remains the completion gate.
            if completed:
                native.wakeup = None
                self.routes[draft_id] = native
                return native
            native.ctx = NativeContext(native.ctx, paths['publication_config'], native.config['operator_id'])
            subscriber = planning_wakeup.NativeWakeup(native, paths['wakeup_config'])
            native.wakeup = subscriber
            subscriber.start()
            require(subscriber.status == 'started' and not subscriber.stopped,
                    'Native host subscriber could not start.', 'native_lifecycle_unavailable')
            self.routes[draft_id] = native
            return native
        except Exception:
            previous = self.routes.get(draft_id)
            if previous is not None and previous.wakeup is not None:
                previous.wakeup.stop()
            raise

    def close(self):
        """Stop each owned native task with NativeWakeup's existing bounded join."""
        if self.closed:
            return
        self.closed = True
        for native in self.routes.values():
            if native.wakeup is not None:
                native.wakeup.stop()
        self.routes.clear()
