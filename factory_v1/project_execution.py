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
        require(isinstance(config, dict) and set(config) == {'root', 'capability_config'},
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
        require(isinstance(selected, dict) and set(selected) == INVENTORY_FIELDS and
                all(selected[k] == v for k, v in {
                    'operator_id': draft['operator_id'], 'repository': attached['repository'],
                    'repository_id': attached['repository_id'], 'draft_revision': draft['revision']}.items()) and
                text(selected['reference']), 'Exact reviewed host execution scope required.', 'execution_capability_required')
        policy = selected['policy']
        require(isinstance(policy, dict) and set(policy) == POLICY_FIELDS and
                policy['acceptance'] == draft['product']['acceptance'] and
                policy['merge'] == policy['production'] == 'human-approved',
                'Bind current agreed acceptance and explicit human approval boundaries.', 'execution_policy_required')
        require(policy['deployment'] is True,
                'Existing delivery contract requires deployment health; no-deployment is unsupported.', 'nondeployment_unsupported')
        require(isinstance(policy['verification_commands'], list) and policy['verification_commands'],
                'Agreed finite test commands required.', 'execution_policy_required')
        return selected

    def derive(self, draft, attached, board, tracked, host):
        try:
            current = service.load(host['service_config'])
            live = service.supervisor(current, 'status')
        except ERRORS as error:
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
        issue = tickets.gh.read_issue(assignments.ReadOnlyGitHub(github), attached['repository'], contract['issue'])
        seed_slot = next(s for s in slots if s['stage'] == 'implementation')
        standards_path = physical(Path(attached['workspace']) / 'AGENTS.md')
        from .simplification import artifact
        seed = {**{k: deepcopy(seed_slot[k]) for k in ('stage', 'profile', 'skills', 'claim_id')},
                'repository': attached['repository'], 'repository_id': attached['repository_id'],
                'workspace': attached['workspace'], 'issue': {k: issue[k] for k in ('number', 'id', 'node_id')},
                'spec_commit': attached['commit'], 'baseline': attached['commit'], 'candidate': None,
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
                pin = assignments.digest(host)
                require(record.get('inventory_pin', pin) == pin, 'Reviewed inventory changed.', 'execution_inputs_changed')
                record['inventory_pin'] = pin
                current, report = self.derive(draft, attached, board, tracked, host)
                require(readiness.load_config(self.config['capability_config']) == host,
                        'Host inventory changed during preparation.', 'execution_inputs_changed')
                record.update(service_config=host['service_config'], service=current, readiness=report)
                failed = {k: v for k, v in report['checks'].items() if k != 'controls' and v['state'] != 'ready'}
                require(not failed, 'Inspect exact readiness checks; no missing authority is granted.',
                        next(iter(failed.values()))['code'] if failed else 'execution_readiness_blocked')
                record.update(status='prepared', blocker=None)
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
            'consequence': 'Preparation retained; no worker, model, service start or allowance reset.',
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
                host = readiness.load_config(self.config['capability_config'])
                require(assignments.digest(host) == record['inventory_pin'], 'Host scope changed.', 'execution_inputs_changed')
                target, path = record['service'], Path(record['service_config'])
                old = service.load(str(path))
                require(all(old[k] == v for k, v in target.items() if k not in {'dispatch_config', 'readiness_config'}) and
                        all(old[k] in (None, target[k]) for k in ('dispatch_config', 'readiness_config')),
                        'Existing service changed.', 'execution_inputs_changed')
                with service.connect(target) as db:
                    controls.owner(db, *service.scope(target))
                    require(controls.blocked(db, *service.scope(target)[:2]),
                            'Pause before binding service policy; preparation never pauses or resumes for you.', 'iteration_paused')
                    # Short local interruption lane; no supervisor/model/external I/O.
                    with controls.lane(db, *service.scope(target)[:2]):
                        require(controls.blocked(db, *service.scope(target)[:2]),
                                'Interruption changed before registration.', 'iteration_paused')
                        require(readiness.load_config(str(path)) == old and
                                readiness.load_config(self.config['capability_config']) == host,
                                'Registration inputs changed.', 'execution_inputs_changed')
                        service.atomic(path, target)
                loaded = service.load(str(path))
                require(loaded == target, 'Service selection readback differs.', 'execution_inputs_changed')
                with service.connect(target) as db:
                    binding = readiness_gate.binding(db, target['project'], target['iteration'])
                    require(binding is not None, 'Service registration missing.', 'execution_installation_required')
                    assert binding is not None
                    if binding[1] is None:
                        readiness_gate.seal(db, path)
                    else:
                        selected = readiness.load_config(target['readiness_config'])
                        require(binding[1] == readiness_gate.policy(target, selected, db),
                                'Sealed inputs changed; no automatic reselection.', 'execution_inputs_changed')
                    final_binding = readiness_gate.binding(db, target['project'], target['iteration'])
                    require(final_binding is not None and final_binding[0] == str(path),
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
        return None if record is None else self.prepare(draft_id, record['draft_revision'])
