"""Derive project execution inputs from a private reviewed host installation.

Preparation is not execution, a credential grant, a review attestation or resume.
The existing service and continuation retain every effect/admission boundary.
"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess

from . import assignments, controls, delivery, readiness, readiness_gate, sandbox_policy
from . import service, service_dispatch, tickets
from .errors import IntakeError
from .planning import require, text
from .repositories import RepositoryError, unambiguous_fields
from .sandbox_source import physical

INVENTORY_FIELDS = {'operator_id', 'repository', 'repository_id', 'draft_revision', 'reference',
                    'service_config', 'role_root', 'templates', 'planning_skills', 'ticket_skill',
                    'planning_config', 'synthesis_config', 'authorities', 'protection', 'policy'}
PLATFORM_FIELDS = INVENTORY_FIELDS - {'operator_id', 'repository', 'repository_id', 'draft_revision', 'service_config'}
INSTALLATION_FIELDS = {'operator_id', 'reference', 'state_root', 'workspace_root', 'service_template', 'unit_directory', 'actions'}
POLICY_FIELDS = {'acceptance', 'verification_commands', 'merge', 'production', 'deployment'}
ERRORS = (RepositoryError, IntakeError, OSError, ValueError, TypeError, KeyError,
          AttributeError, sqlite3.Error, subprocess.SubprocessError)


def retain(path, value):
    """Write once, or compare exact private bytes semantically; never replace drift."""
    if path.exists() or path.is_symlink():
        require(readiness.load_config(str(path)) == value,
                'Retained execution input changed; reconcile instead of overwriting.', 'execution_inputs_changed')
    else:
        service.atomic(path, value)
    require(readiness.load_config(str(path)) == value, 'Execution input readback differs.', 'execution_inputs_changed')


class ProjectExecution:
    """Host-only prepare/read/status/resume/register seam for T7 composition."""

    def __init__(self, setup, tracker, config):
        require(isinstance(config, dict) and set(config) in ({'root', 'capability_config'}, {'root', 'capability_config', 'installation_config'}),
                'Private reviewed execution inventory required.', 'execution_capability_required')
        self.setup, self.tracker, self.config = setup, tracker, deepcopy(config)
        self.root = physical(config['root'], directory=True)
        controls.private(self.root, directory=True)
        service.isolated_root(self.root)
        for other in (setup.root, tracker.root, setup.enrollment.store.root,
                      setup.workspaces, Path(setup.config['state']).parent,
                      Path(config['capability_config']).parent):
            require(not self.root.is_relative_to(other) and not other.is_relative_to(self.root),
                    'Separate private execution state required.', 'workspace_conflict')

    def path(self, draft_id):
        self.setup.enrollment.store.path(draft_id)
        return self.root / (draft_id + '.json')

    def read(self, draft_id):
        """Retained preparation/blocker only; never execution or health authority."""
        path = self.path(draft_id)
        if not path.exists():
            return None
        controls.private(physical(path))
        record = json.loads(path.read_text(), object_pairs_hook=unambiguous_fields)
        require(record['draft_id'] == draft_id, 'Execution draft identity differs.', 'scope_mismatch')
        return deepcopy(record)

    def save(self, record):
        service.atomic(self.path(record['draft_id']), record)

    def context(self, draft_id, revision):
        draft = self.setup.enrollment.store.read(draft_id)
        attached = self.setup.read(draft_id)
        require(draft is not None and attached is not None and
                type(revision) is int and draft['revision'] == attached['draft_revision'] == revision and
                draft['sufficient'] and not draft['pending_questions'] and attached['status'] == 'ready' and
                draft.get('transfer') == attached['transfer'],
                'Current sufficient verified attachment required.', 'execution_setup_required')
        if draft is None or attached is None:
            raise RepositoryError('execution_setup_required', 'Current attachment required.')
        # Actual T4 signature; never supply a native session_id here.
        with service.connect(attached['binding']) as db:
            paused = controls.blocked(db, attached['project'], attached['iteration'])
        if not paused:
            board = self.tracker.tracker_config(draft_id)
        else:
            # T4 prepare owns possible effects and refuses pause. Revalidate its
            # already-complete contract read-only so registration can preserve pause.
            from .project_tracker import TrackerHost
            capability = readiness.load_config(self.tracker.config['capability_config'])
            require(capability['operator_id'] == draft['operator_id'] and
                    capability['repository'] == attached['repository'], 'Tracker authority differs.', 'scope_mismatch')
            tracked = self.tracker.read(draft_id)
            if tracked is None:
                raise RepositoryError('execution_setup_required', 'Prepare tracker before execution binding.')
            board = deepcopy(tracked['tracker'])
            github = assignments.ReadOnlyGitHub(TrackerHost(capability).github)
            actual = tickets.gh.resolve_board(github, attached['repository'], board)
            milestone = tickets.gh.resolve_milestone(github, attached['repository'], attached['iteration'])
            require(dict(actual, milestone=milestone) == tracked['identities'],
                    'Paused tracker readback differs.', 'scope_mismatch')
        tracked = self.tracker.read(draft_id)
        if tracked is None:
            raise RepositoryError('execution_setup_required', 'Prepare tracker first.')
        require(tracked['status'] == 'ready' and
                tracked['draft_revision'] == revision and tracked['repository_id'] == attached['repository_id'] and
                tracked['binding'] == attached['binding'], 'Fresh tracker binding differs.', 'scope_mismatch')
        return draft, attached, board, tracked

    def inventory(self, draft, attached):
        try:
            selected = readiness.load_config(self.config['capability_config'])
        except (OSError, RepositoryError, ValueError) as error:
            raise RepositoryError('execution_capability_required', 'Restore the private reviewed host inventory.') from error
        if 'installation_config' in self.config:
            require(isinstance(selected, dict) and set(selected) == PLATFORM_FIELDS and
                    set(selected['policy']) == POLICY_FIELDS - {'acceptance'},
                    'Reviewed reusable platform inventory required.', 'execution_capability_required')
            selected = dict(selected, operator_id=draft['operator_id'], repository=attached['repository'],
                repository_id=attached['repository_id'], draft_revision=draft['revision'],
                service_config=str(Path(attached['binding']['state']).parent / 'service.json'),
                policy=dict(selected['policy'], acceptance=draft['product']['acceptance']))
            self.installation(draft, attached)
        require(isinstance(selected, dict) and set(selected) == INVENTORY_FIELDS and
                all(selected[k] == v for k, v in {
                    'operator_id': draft['operator_id'], 'repository': attached['repository'],
                    'repository_id': attached['repository_id'], 'draft_revision': draft['revision']}.items()) and
                text(selected['reference']), 'Exact reviewed host execution scope required.', 'execution_capability_required')
        policy = selected['policy']
        assert isinstance(policy, dict)
        require(isinstance(policy, dict) and set(policy) == POLICY_FIELDS and
                policy['acceptance'] == draft['product']['acceptance'] and
                policy['merge'] == policy['production'] == 'human-approved',
                'Bind current agreed acceptance and explicit human approval boundaries.', 'execution_policy_required')
        require(policy['deployment'] is True,
                'Existing delivery contract requires deployment health; no-deployment is unsupported.', 'nondeployment_unsupported')
        require(isinstance(policy['verification_commands'], list) and policy['verification_commands'],
                'Agreed finite test commands required.', 'execution_policy_required')
        return selected

    def installation(self, draft, attached):
        selected = readiness.load_config(self.config['installation_config'])
        require(isinstance(selected, dict) and set(selected) == INSTALLATION_FIELDS and
                selected['operator_id'] == draft['operator_id'] and text(selected['reference']) and
                isinstance(selected['actions'], list) and len(set(selected['actions'])) == len(selected['actions']) and
                set(selected['actions']) <= {'install', 'start'},
                'Exact reviewed platform lifecycle authority required.', 'execution_lifecycle_required')
        state_root = physical(selected['state_root'], directory=True)
        workspace_root = physical(selected['workspace_root'], directory=True)
        controls.private(state_root, directory=True)
        controls.private(workspace_root, directory=True)
        require(Path(attached['binding']['state']).parent.is_relative_to(state_root) and
                Path(attached['workspace']).is_relative_to(workspace_root) and
                state_root != workspace_root and not state_root.is_relative_to(workspace_root) and
                not workspace_root.is_relative_to(state_root),
                'Attachment outside reviewed platform roots.', 'scope_mismatch')
        return dict(selected, template_pin=assignments.digest(readiness.load_config(selected['service_template'])))

    def managed_service(self, draft, attached, host):
        authority = self.installation(draft, attached)
        template = readiness.load_config(authority['service_template'])
        require(isinstance(template, dict) and set(template) == service.FIELDS -
                {'state', 'operator_id', 'project', 'iteration'} and
                template['dispatch_config'] is None and template['readiness_config'] is None,
                'Reviewed native service template required.', 'invalid_service')
        config = dict(template, state=attached['binding']['state'], operator_id=draft['operator_id'],
                      project=attached['project'], iteration=attached['iteration'], delivery_config=None)
        path = Path(host['service_config'])
        if not path.exists():
            retain(path, config)
        actual = service.load(str(path), register_binding=False)
        require(all(actual[k] == v for k, v in config.items() if k not in
                    {'delivery_config', 'dispatch_config', 'readiness_config'}),
                'Managed service scope/template drift.', 'execution_inputs_changed')
        return actual, authority, template

    def prepare_planning(self, draft_id, expected_revision):
        """Setup-only phase: no issue, AGENTS, health, running unit or worker required."""
        record = self.read(draft_id) or {'version': 1, 'draft_id': draft_id,
            'draft_revision': expected_revision, 'status': 'pending', 'blocker': None}
        try:
            draft, attached, _, _ = self.context(draft_id, expected_revision)
            with self.setup.enrollment.store.locked():
                require(record['draft_revision'] == expected_revision and
                        self.setup.enrollment.store.read(draft_id) == draft and self.setup.read(draft_id) == attached,
                        'Preparation scope changed.', 'scope_mismatch')
                host = self.inventory(draft, attached)
                installation = self.installation(draft, attached) if 'installation_config' in self.config else None
                pin = assignments.digest(host if installation is None else [host, installation])
                require(record.get('inventory_pin', pin) == pin, 'Reviewed inputs changed.', 'execution_inputs_changed')
                if installation is not None:
                    current, _, _ = self.managed_service(draft, attached, host)
                else:
                    current = service.load(host['service_config'])
                require(current['state'] == attached['binding']['state'] and
                        all(current[k] == v for k, v in {'operator_id': draft['operator_id'],
                            'project': attached['project'], 'iteration': attached['iteration']}.items()),
                        'Planning service scope differs.', 'scope_mismatch')
                # Loading/validating these host files is not skill execution.
                readiness.load_config(host['planning_config'])
                readiness.load_config(host['synthesis_config'])
                record.update(inventory_pin=pin, service_config=host['service_config'], service=current,
                    planning_config=host['planning_config'], synthesis_config=host['synthesis_config'],
                    status='planning_prepared', phase='preplanning', blocker=None)
        except ERRORS as error:
            self.block(record, error, 'prepare-planning')
        self.save(record)
        return deepcopy(record)

    def published_seed(self, current, attached, github):
        with service.connect(current) as db:
            item = assignments.current(db, *service.scope(current), require_active=False)
            work = item.get('ticket_work', {})
            require(item.get('planning', {}).get('status') == 'complete' and
                    work.get('publication_complete') is True and work.get('status') == 'published' and
                    work.get('tickets') and work.get('published'),
                    'Publish the actual specification and ticket batch before engineering preparation.', 'execution_publication_required')
            inputs = tickets.pinned_inputs(db, item, assignments.ReadOnlyGitHub(github))
            require(inputs == work['inputs'], 'Published requirement authority changed.', 'publication_mismatch')
            first = work['tickets'][0]
            identity = work['published'][first['key']]
            issue = tickets.gh.read_issue(assignments.ReadOnlyGitHub(github), attached['repository'], identity['number'])
            numbers = {k: v['number'] for k, v in work['published'].items()}
            require(all(issue[k] == v for k, v in identity.items()) and issue['state'] == 'open' and
                    issue['body'] == tickets.body(item, first, numbers),
                    'First published ticket readback differs.', 'publication_mismatch')
            return issue, inputs['handoff']['commit']

    def derive(self, draft, attached, board, tracked, host):
        issue = None
        spec_commit = None
        try:
            managed = 'installation_config' in self.config
            if managed:
                current, authority, template = self.managed_service(draft, attached, host)
                github = service.github_client(current)
                issue, spec_commit = self.published_seed(current, attached, github)
                contract = readiness.load_config(template['delivery_config'])
                contract = dict(contract, operator_id=draft['operator_id'], repository=attached['repository'],
                                repository_id=attached['repository_id'], issue=issue['number'])
                delivery.validate_config(contract, draft['operator_id'])
                delivery_path = Path(host['service_config']).parent / 'delivery.json'
                retain(delivery_path, contract)
                current['delivery_config'] = str(delivery_path)
                unit = service.instance(current)
                unit_path = physical(authority['unit_directory'], directory=True) / unit
                if not unit_path.exists() and not unit_path.is_symlink():
                    require('install' in authority['actions'], 'Platform installation not authorized.', 'execution_lifecycle_required')
                    require(self.installation(draft, attached) == authority and self.inventory(draft, attached) == host,
                            'Lifecycle authority changed.', 'execution_inputs_changed')
                    service.install(current, authority['unit_directory'], unit=unit)
                live = service.supervisor(current, 'status', unit=unit)
                if live is not None and live.get('ActiveState') == 'inactive' and live.get('MainPID') == '0':
                    require('start' in authority['actions'], 'Platform startup not authorized.', 'execution_lifecycle_required')
                    require(self.installation(draft, attached) == authority and self.inventory(draft, attached) == host,
                            'Startup authority changed.', 'execution_inputs_changed')
                    service.supervisor(current, 'start', unit=unit)
                    live = service.supervisor(current, 'status', unit=unit)
            else:
                current = service.load(host['service_config'])
                live = service.supervisor(current, 'status')
        except ERRORS as error:
            if isinstance(error, RepositoryError):
                raise
            raise RepositoryError('execution_installation_required',
                                  'Restore the existing scoped reviewed service installation.') from error
        require(current['state'] == attached['binding']['state'] and
                all(current[k] == v for k, v in {'operator_id': draft['operator_id'],
                    'project': attached['project'], 'iteration': attached['iteration']}.items()),
                'Existing scoped service must use the original controller state.', 'execution_installation_required')
        require(live is not None, 'Supervisor status unavailable.', 'execution_installation_required')
        assert live is not None
        require(live.get('ActiveState') == 'active' and live.get('SubState') == 'running' and
                int(live.get('MainPID', '0')) > 0,
                'Existing reviewed supervisor is not running; host setup required.', 'execution_installation_required')
        run = self.root / draft['draft_id']
        run.mkdir(mode=0o700, exist_ok=True)
        controls.private(physical(run, directory=True), directory=True)
        homes = physical(host['role_root'], directory=True)
        controls.private(homes, directory=True)
        service.isolated_root(homes)
        for scope in (self.root, self.setup.root, self.tracker.root, self.setup.enrollment.store.root,
                      self.setup.workspaces, Path(current['state']).parent,
                      Path(self.config['capability_config']).parent):
            require(not homes.is_relative_to(scope) and not scope.is_relative_to(homes),
                    'Role homes must be isolated from workspaces/controller/capabilities.', 'workspace_conflict')
        templates = host['templates']
        require(isinstance(templates, list) and {s['stage'] for s in templates} == set(assignments.ROLES) and
                len(templates) == len(assignments.ROLES) and
                all(set(s) == {'stage', 'count', 'profile', 'skills', 'launcher'} and
                    type(s['count']) is int and 1 <= s['count'] <= 100 for s in templates),
                'Finite reviewed templates for each existing stage required.', 'execution_capacity_unavailable')
        slots = []
        for template in templates:
            stage = template['stage']
            source = sandbox_policy.load_config(template['launcher'])
            require(set(template['profile']) == {'provider', 'model', 'operation', 'reasoning'},
                    'Template must not reuse a named profile/home.', 'profile_blocked')
            service_dispatch.installed(stage, template['skills'])
            for index in range(template['count']):
                name = 'forge-' + assignments.digest([draft['draft_id'], stage, index])[:32]
                profile = {**template['profile'], 'name': name, 'home': str(homes / name), 'role': assignments.ROLES[stage]}
                assignments.validate_profile(stage, profile)
                artifacts = run / (name + '-artifacts')
                artifacts.mkdir(mode=0o700, exist_ok=True)
                launcher = dict(source, artifacts_root=str(artifacts),
                                verification_commands=host['policy']['verification_commands'])
                path = run / (name + '-launcher.json')
                retain(path, launcher)
                sandbox_policy.load_config(str(path))  # Validate derived permissions, not only template.
                slots.append({'stage': stage, 'profile': profile, 'skills': deepcopy(template['skills']),
                              'claim_id': name, 'launcher': str(path)})
        require(len(slots) <= 1000, 'Finite slot ceiling exceeded.', 'execution_capacity_unavailable')
        scope = {k: current[k] for k in ('operator_id', 'project', 'iteration')}
        contract = delivery.load_config(current['delivery_config'], draft['operator_id'])
        require(all(contract[k] == v for k, v in {'repository': attached['repository'],
                    'repository_id': attached['repository_id'], 'api_base': current['api_base']}.items()),
                'Delivery contract must bind the verified repository.', 'scope_mismatch')
        github = service.github_client(current)
        # A real delivery anchor supplies schema fields only. Continuation.request
        # re-reads each selected ticket, main and requirement authority before use.
        if not managed:
            issue = tickets.gh.read_issue(assignments.ReadOnlyGitHub(github), attached['repository'], contract['issue'])
            spec_commit = attached['commit']
        assert issue is not None and spec_commit is not None
        seed_slot = next(s for s in slots if s['stage'] == 'implementation')
        standards_path = physical(Path(attached['workspace']) / 'AGENTS.md')
        from .simplification import artifact
        seed = {**{k: deepcopy(seed_slot[k]) for k in ('stage', 'profile', 'skills', 'claim_id')},
                'repository': attached['repository'], 'repository_id': attached['repository_id'],
                'workspace': attached['workspace'], 'issue': {k: issue[k] for k in ('number', 'id', 'node_id')},
                'spec_commit': spec_commit, 'baseline': attached['commit'], 'candidate': None,
                'standards': [artifact('AGENTS.md', standards_path.read_text())], 'preceding': [],
                'capabilities': ['read_workspace', 'scratch', 'model', 'write_workspace'], 'result_contract': assignments.CONTRACT}
        import hashlib
        seed['issue']['body_sha256'] = hashlib.sha256(issue['body'].encode()).hexdigest()
        dispatch = {**scope, 'reference': host['reference'], 'implementation': seed, 'roles': {}, 'launchers': {},
                    'tickets': {'tracker': board, 'skill': host['ticket_skill']},
                    'synthesis_config': host['synthesis_config'], 'planning_config': host['planning_config'],
                    'review_config': None, 'publication_config': None, 'merge_config': None,
                    'continuation': {'reference': host['reference'], 'slots': slots, 'authorities': host['authorities']}}
        assignments.validate(seed)
        service_dispatch.validate(dispatch, current)
        selected = {**scope, 'reference': host['reference'], 'slots': slots, 'planning_skills': host['planning_skills'],
                    'ticket_skill': host['ticket_skill'], 'board': board,
                    'board_pin': {k: v for k, v in tracked['identities'].items() if k != 'milestone'},
                    'protection': host['protection'], 'synthesis_config': host['synthesis_config']}
        for kind, value in (('dispatch', dispatch), ('readiness', selected)):
            path = run / (kind + '.json')
            retain(path, value)
            require(current[kind + '_config'] in (None, str(path)),
                    'Do not replace an existing service selection.', 'execution_inputs_changed')
            current[kind + '_config'] = str(path)
        with service.connect(current) as db:
            for path in (host['service_config'], self.config['capability_config'], *[s['launcher'] for s in slots]):
                from .merge import controller_paths
                controller_paths(db, path)
            observed = assignments.current(db, *service.scope(current), require_active=False)
            require(observed['repository'] == attached['repository'] and
                    observed['repository_onboarding']['metadata']['id'] == attached['repository_id'] and
                    observed['planning']['assignment_id'] == attached['transfer']['assignment_id'],
                    'Registered project authority differs.', 'scope_mismatch')
            report = readiness.collect(db, current, selected, github, running=True)
        return current, report

    def prepare(self, draft_id, expected_revision):
        """Derive immutable private inputs; fresh concrete readers report exact blockers."""
        # T4 owns its own draft lock; do not nest it under this journal lock.
        try:
            context = self.context(draft_id, expected_revision)
        except ERRORS as error:
            record = self.read(draft_id) or {'version': 1, 'draft_id': draft_id,
                'draft_revision': expected_revision, 'status': 'pending', 'blocker': None}
            self.block(record, error, 'prepare-execution')
            self.save(record)
            return deepcopy(record)
        with self.setup.enrollment.store.locked():
            record = self.read(draft_id) or {'version': 1, 'draft_id': draft_id,
                'draft_revision': expected_revision, 'status': 'pending', 'blocker': None}
            try:
                require(record['draft_revision'] == expected_revision, 'Execution revision changed.', 'scope_mismatch')
                draft, attached, board, tracked = context
                require(self.setup.enrollment.store.read(draft_id) == draft and self.setup.read(draft_id) == attached,
                        'Draft changed during tracker readback.', 'scope_mismatch')
                host = self.inventory(draft, attached)
                installation = self.installation(draft, attached) if 'installation_config' in self.config else None
                pin = assignments.digest(host if installation is None else [host, installation])
                require(record.get('inventory_pin', pin) == pin, 'Reviewed inventory changed.', 'execution_inputs_changed')
                record['inventory_pin'] = pin
                record['phase'] = 'postpublication'
                current, report = self.derive(draft, attached, board, tracked, host)
                require(self.inventory(draft, attached) == host and (installation is None or
                        self.installation(draft, attached) == installation),
                        'Host inventory changed during preparation.', 'execution_inputs_changed')
                selected = readiness.load_config(current['readiness_config'])
                with service.connect(current) as db:
                    policy_pin = readiness_gate.policy(current, selected, db)
                record.update(service_config=host['service_config'], service=current, readiness=report, policy_pin=policy_pin)
                failed = {k: v for k, v in report['checks'].items() if k != 'controls' and v['state'] != 'ready'}
                require(not failed, 'Inspect exact readiness checks; no missing authority is granted.',
                        next(iter(failed.values()))['code'] if failed else 'execution_readiness_blocked')
                record.update(status='prepared', phase='postpublication', blocker=None)
            except ERRORS as error:
                self.block(record, error, 'prepare-execution')
            self.save(record)
            return deepcopy(record)

    def block(self, record, error, operation):
        code = getattr(error, 'code', 'execution_platform_unavailable')
        failures = {k: v for k, v in record.get('readiness', {}).get('checks', {}).items()
                    if k != 'controls' and v['state'] == 'blocked'}
        selected = record.get('service', {})
        scope = {k: selected[k] for k in ('project', 'iteration', 'operator_id') if k in selected}
        cause = 'platform'
        if 'capacity' in code or code.startswith('spending'):
            cause = 'capacity'
        elif 'capability' in code or 'approval' in code or code.startswith('github'):
            cause = 'permission'
        record.update(status='blocked', blocker={'code': code, 'operation': operation,
            'cause': cause, 'checks': failures,
            'scope': dict(scope, draft_id=record['draft_id'], draft_revision=record['draft_revision']),
            'consequence': 'Preparation retained; no engineering worker, model or allowance reset. Explicitly authorized scoped service lifecycle effects may have occurred.',
            'next_decision': (next(iter(failures.values()))['remediation'] if failures else
                              'Restore the exact reviewed host prerequisite; explicit controls resume remains separate.'),
            'reconciliation_required': code == 'execution_inputs_changed'})

    def register(self, draft_id, expected_revision):
        """Bind the prepared continuation to the installed service, without starting/resuming."""
        record = self.prepare(draft_id, expected_revision)
        if record['status'] != 'prepared':
            return record
        try:
            with self.setup.enrollment.store.locked():
                draft = self.setup.enrollment.store.read(draft_id)
                attached = self.setup.read(draft_id)
                require(draft is not None and attached is not None and draft['revision'] == expected_revision,
                        'Registration scope changed.', 'scope_mismatch')
                host = self.inventory(draft, attached)
                installation = self.installation(draft, attached) if 'installation_config' in self.config else None
                require(assignments.digest(host if installation is None else [host, installation]) == record['inventory_pin'], 'Host scope changed.', 'execution_inputs_changed')
                target, path = record['service'], Path(record['service_config'])
                old = service.load(str(path))
                require(all(old[k] == v for k, v in target.items() if k not in {'dispatch_config', 'readiness_config', 'delivery_config'}) and
                        all(old[k] in (None, target[k]) for k in ('dispatch_config', 'readiness_config', 'delivery_config')),
                        'Existing service changed.', 'execution_inputs_changed')
                selected = readiness.load_config(target['readiness_config'])
                tracked = self.tracker.read(draft_id)
                expected_service = old

                def validate_initial():
                    require(self.read(draft_id) == record and
                            self.setup.enrollment.store.read(draft_id) == draft and
                            self.setup.read(draft_id) == attached and self.tracker.read(draft_id) == tracked and
                            self.inventory(draft, attached) == host and (installation is None or
                            self.installation(draft, attached) == installation) and
                            service.load(str(path), register_binding=False) == expected_service,
                            'Initial registration authority changed.', 'execution_inputs_changed')

                with service.connect(target) as db:
                    controls.owner(db, *service.scope(target))
                    value = readiness_gate.policy(target, selected, db)
                    require(value == record['policy_pin'], 'Prepared policy bytes changed.', 'execution_inputs_changed')
                    initial = None if controls.blocked(db, *service.scope(target)[:2]) else (value, validate_initial)
                    # Short local interruption lane; no supervisor/model/external I/O.
                    with controls.lane(db, *service.scope(target)[:2]):
                        controls.owner(db, *service.scope(target))
                        if initial is None:
                            require(controls.blocked(db, *service.scope(target)[:2]),
                                    'Interruption changed before registration.', 'iteration_paused')
                        require(readiness.load_config(str(path)) == old and
                                self.inventory(draft, attached) == host and (installation is None or
                                self.installation(draft, attached) == installation),
                                'Registration inputs changed.', 'execution_inputs_changed')
                        if initial is not None:
                            readiness_gate.initial_policy(db, path, target, value, validate_initial)
                        service.atomic(path, target)
                        expected_service = target
                loaded = service.load(str(path))
                require(loaded == target, 'Service selection readback differs.', 'execution_inputs_changed')
                with service.connect(target) as db:
                    binding = readiness_gate.binding(db, target['project'], target['iteration'])
                    require(binding is not None, 'Service registration missing.', 'execution_installation_required')
                    assert binding is not None
                    if initial is not None or binding[1] is None:
                        readiness_gate.seal(db, path, initial=initial)
                    else:
                        require(binding[1] == readiness_gate.policy(target, selected, db),
                                'Sealed inputs changed; no automatic reselection.', 'execution_inputs_changed')
                    final_binding = readiness_gate.binding(db, target['project'], target['iteration'])
                    require(final_binding == (str(path), value) and
                            service.load(str(path), register_binding=False) == target,
                            'Service binding readback differs.', 'execution_installation_required')
                record.update(status='registered', blocker=None)
        except ERRORS as error:
            self.block(record, error, 'register-execution')
        self.save(record)
        return deepcopy(record)

    def resume(self, draft_id, expected_revision):
        """Recheck setup only, then retry registration; never call controls.resume."""
        return self.register(draft_id, expected_revision)

    def status(self, draft_id):
        """Fresh readiness/preparation status; retained green never grants dispatch."""
        record = self.read(draft_id)
        return None if record is None else (self.prepare_planning(draft_id, record['draft_revision'])
            if record.get('phase') == 'preplanning' else self.prepare(draft_id, record['draft_revision']))
