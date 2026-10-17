"""Fresh supervised admission over the existing private service/state envelope.

No report, caller token or environment credential grants admission. External reads
finish before the interruption lane; only local comparisons run inside that lane.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path

from . import assignments, controls, readiness
from .planning import require
from .readiness_receipts import receipt_policy, runtime_receipt, validate_runtime_receipts

_active = ContextVar('supervised_readiness_admission', default={})


def binding(database, project, iteration):
    if not database.execute("SELECT 1 FROM sqlite_master WHERE name='supervised_readiness'").fetchone():
        return None
    row = database.execute('SELECT service, policy FROM supervised_readiness WHERE project=? AND iteration=?',
                           (project, iteration)).fetchone()
    return row


def register(database, service_path, config):
    """Persist the service selection, including a missing-readiness fail-closed state."""
    database.execute('CREATE TABLE IF NOT EXISTS supervised_readiness '
                     '(project TEXT, iteration TEXT, service TEXT, policy TEXT, PRIMARY KEY(project, iteration))')
    old = binding(database, config['project'], config['iteration'])
    require(old is None or old[0] == str(service_path) or (old == ('', None) and
            assignments.current(database, config['project'], config['iteration'], config['operator_id'], require_active=False).get('previous_iteration')), 'Supervised service binding changed.', 'readiness_binding_changed')
    if old == ('', None):
        database.execute('UPDATE supervised_readiness SET service=? WHERE project=? AND iteration=?',
                         (str(service_path), config['project'], config['iteration']))
    database.execute('INSERT OR IGNORE INTO supervised_readiness VALUES (?,?,?,NULL)',
                     (config['project'], config['iteration'], str(service_path)))
    database.commit()


def inherit_supervision(database, predecessor, successor):
    """Atomically fence a successor until its own scoped service policy is selected."""
    if binding(database, predecessor['project_id'], predecessor['iteration_id']) is not None:
        database.execute('INSERT INTO supervised_readiness VALUES (?,?,?,NULL)',
                         (successor['project_id'], successor['iteration_id'], ''))


def configuration(path):
    # Do not call service.load: it registers state and would recurse during admission.
    from . import service
    require(bool(path) and Path(path).is_absolute(), 'Select a successor-scoped service policy.', 'readiness_configuration_required')
    config = readiness.load_config(path)
    require(isinstance(config, dict) and set(config) == service.FIELDS,
            'Exact service configuration required.', 'invalid_service')
    require(config['readiness_config'] is not None, 'Select private readiness configuration.', 'readiness_configuration_required')
    return config, readiness.load_config(config['readiness_config'])


def policy(config, selected, database):
    """Immutable local admission inputs, not counters, health samples or slot use."""
    files = {}
    visited = set()
    state = config['state']
    runtime = receipt_policy(database, config, selected)
    dynamic = {p for paths in (runtime or {}).get('receipts', {}).values() for p in paths}

    def visit(value, runtime_paths=False):
        if isinstance(value, dict):
            for key, child in value.items():
                # Mutable execution destinations are not immutable policy inputs.
                if key not in {'home', 'workspace', 'artifacts_root', 'artifacts', 'state', 'subscription_socket'}:
                    visit(child, runtime_paths or key in {'authorities', 'receipts'})
        elif isinstance(value, list):
            for child in value:
                visit(child, runtime_paths)
        elif isinstance(value, str) and value.startswith('/') and value != state and value not in visited:
            if runtime_paths and value in dynamic:
                parent = Path(value).parent
                info = parent.stat()
                files[value] = ['issued-exact-receipt', str(parent), info.st_mode, info.st_uid]
                return
            visited.add(value)
            path = Path(value)
            if path.is_file():
                require(not any(p.is_symlink() for p in (path, *path.parents)),
                        'Physical selected inputs required.', 'readiness_inputs_changed')
                info = path.stat()
                raw = path.read_bytes()
                files[value] = [hashlib.sha256(raw).hexdigest(), info.st_mode, info.st_uid]
                if path.suffix == '.json':
                    visit(json.loads(raw))
    visit(config)
    visit(selected)
    for path in Path(__file__).parent.glob('*.py'):
        visit(str(path.absolute()))
    grant_columns = [r[1] for r in database.execute('PRAGMA table_info(model_grants)') if r[1] != 'remaining']
    require(grant_columns, 'Existing allowance policy required.', 'readiness_configuration_required')
    grants = database.execute('SELECT ' + ','.join('"' + c.replace('"', '""') + '"' for c in grant_columns) +
                              ' FROM model_grants WHERE project=? AND iteration=? ORDER BY grant_id',
                              (config['project'], config['iteration'])).fetchall()
    item = assignments.current(database, config['project'], config['iteration'], config['operator_id'], require_active=False)
    return assignments.digest({'scope': scope_identity(item), 'service': config, 'selected': selected, 'files': files,
                               'grant_columns': grant_columns, 'grants': grants})




def seal(database, path):
    """Explicit operator selection pins existing local policy; it grants no capability."""
    config, selected = configuration(path)
    controls.owner(database, config['project'], config['iteration'], config['operator_id'])
    current = binding(database, config['project'], config['iteration'])
    require(current is not None and current[0] == str(path), 'Registered service required.', 'readiness_binding_changed')
    require(controls.blocked(database, config['project'], config['iteration']),
            'Pause before selecting readiness inputs.', 'iteration_paused')
    value = policy(config, selected, database)
    with controls.lane(database, config['project'], config['iteration']):
        require(policy(config, selected, database) == value, 'Inputs changed during selection.', 'readiness_inputs_changed')
        database.execute('UPDATE supervised_readiness SET policy=? WHERE project=? AND iteration=?',
                         (value, config['project'], config['iteration']))
        database.commit()


def snapshot(database, project, iteration):
    # This short-lived comparison intentionally includes mutable state: it detects
    # changes DURING observation, never seals changing observations across calls.
    item = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                            (project, iteration)).fetchone()
    tables = {r[0] for r in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = {}
    for name in ('role_assignments', 'model_grants', 'model_operations'):
        if name in tables:
            rows[name] = sorted(database.execute('SELECT * FROM ' + name).fetchall(), key=repr)
    return assignments.digest({'item': item, 'rows': rows, 'binding': binding(database, project, iteration)})


def dispatch_selection(database, service, github):
    """Project the existing selector, never a retained green/runnable cursor."""
    from . import service_dispatch, continuation_dispatch as continuation
    require(service['dispatch_config'] is not None, 'Select an existing dispatch configuration.', 'readiness_blocked')
    config = service_dispatch.validate(readiness.load_config(service['dispatch_config']), service)
    item = assignments.current(database, service['project'], service['iteration'], service['operator_id'], require_active=False)
    rows = assignments.rows(database)
    if config.get('planning_config') is not None and (item.get('planning', {}).get('status') != 'complete' or
            item.get('planning', {}).get('native_wakeup', {}).get('status') != 'consumed'):
        from . import planning_wakeup
        native = planning_wakeup.configuration(config['planning_config'], new_execution=item.get('planning', {}).get('status') != 'complete')
        return ('effect' if item.get('planning', {}).get('status') == 'complete' else 'work'), {
            'native_config': config['planning_config'], 'identity': planning_wakeup.scope(item, native)}, 'planning-wakeup'
    if 'continuation' in config and retirement_resolution(database, service) is not None:
        return 'effect', None, 'retire-delivered'
    if 'continuation' in config and item.get('planning', {}).get('status') == 'complete' and item.get('ticket_work', {}).get('publication_complete'):
        chosen = continuation.choose(database, item, rows, continuation.observe(database, item, github))
        stage, target = chosen['stage'], chosen['target']
        if stage in {'blocked', 'wait', 'direction'}:
            require(False, 'Selected action is held.', 'readiness_blocked')
        if stage in {'implementation', 'repair'}:
            slot = continuation.slot_for(config, rows, stage)
            return 'work', continuation.request(config, item, target, slot, github), stage
        if stage == 'launch':
            row = assignments.inspect(database, service['project'], service['iteration'], service['operator_id'], target)
            return 'work', {'assignment_id': target, 'run_id': None,
                            'launcher': continuation.launcher_for(database, config, row)}, stage
        if stage in assignments.ROLES:
            slot = continuation.slot_for(config, rows, stage)
            ancestor = assignments.inspect(database, service['project'], service['iteration'], service['operator_id'], target)
            return 'work', {**{k: slot[k] for k in ('stage', 'profile', 'skills', 'claim_id')},
                            'issue': ancestor['handoff']['issue']}, stage
        return 'effect', None, stage
    stage, target = service_dispatch.select(item, rows, config)
    require(stage != 'blocked', 'Selected action is held.', 'readiness_blocked')
    if stage == 'implementation':
        return 'work', config['implementation'], stage
    if stage == 'launch':
        row = assignments.inspect(database, service['project'], service['iteration'], service['operator_id'], target)
        return 'work', {'assignment_id': target, 'run_id': None,
                        'launcher': config['launchers'].get(row['handoff']['stage'])}, stage
    if stage in service_dispatch.ROLES:
        ancestor = assignments.inspect(database, service['project'], service['iteration'], service['operator_id'], target)
        return 'work', {'stage': stage, **config['roles'][stage], 'issue': ancestor['handoff']['issue']}, stage
    if stage == 'evaluate':
        axes = [assignments.inspect(database, service['project'], service['iteration'], service['operator_id'], identity)
                for identity in target.values()]
        require(all(a.get('review_result', {}).get('verdict') == 'pass' for a in axes),
                'Selected review remains rejected or incomplete.', 'review_held')
        candidate = assignments.inspect(database, service['project'], service['iteration'], service['operator_id'],
                                        axes[0]['handoff']['review']['pins']['candidate_assignment'])
        require(not candidate.get('review_holds'), 'Candidate rejection remains held.', 'review_held')
    return ('effect' if stage in {'publish-tickets', 'evaluate'} else 'work'), None, stage


def retirement_resolution(database, service):
    item = assignments.current(database, service['project'], service['iteration'], service['operator_id'], require_active=False)
    candidates = [r for r in assignments.rows(database) if
        (r['ticket_scope']['project'], r['ticket_scope']['iteration']) == (service['project'], service['iteration']) and
        r['handoff']['issue']['number'] == item.get('reservation', {}).get('issue_number') and
        r.get('merge', {}).get('status') == 'delivered' and r['merge'].get('close_allowed') is True and not r['merge'].get('failure')]
    require(len(candidates) <= 1, 'Ambiguous delivered retirement.', 'claim_conflict')
    if not candidates:
        return None
    from .effect_resolution import bound
    return bound(database, item, 'delivered-retirement', candidates[0]['assignment_id'])


def scope_identity(item):
    return assignments.digest({k: item.get(k) for k in
        ('project_id', 'iteration_id', 'repository', 'approval', 'origin')} | {
            'repository_id': item['repository_onboarding']['metadata']['id'],
            'api_base': item['repository_onboarding']['api_base']})


def repair_scope(database, config, selection, operation):
    """Recognize existing host authority, never a caller's repair flag."""
    from . import recovery, delivery, requirements
    item = assignments.current(database, config['project'], config['iteration'], config['operator_id'], require_active=False)
    record = recovery.active(item)
    if record is None or record['status'] == 'cleared' or operation == 'notification':
        return None
    if selection is None and operation != 'effect':
        return None
    saved = delivery.monitor_state(item, config['operator_id'])
    require(saved is not None, 'Registered delivery observer required.', 'recovery_held')
    assert saved is not None
    delivery.validate_persisted(database, item, saved)
    incident, authority = item['main_incident'], record['config']
    require(authority['operator_id'] == config['operator_id'] and
            authority['incident_id'] == incident['incident_id'] and
            incident['recovery_issue'] == authority['issue']['number'] and
            incident['recovery_assignment'] == (record['assignment'] or assignments.digest(authority)) and
            record['evidence'] == delivery.persisted_evidence(database, config['project'], config['iteration'], authority['evidence_id'], saved['config']) and
            authority['issue']['number'] not in requirements.held_numbers(item),
            'Exact current host recovery authority required.', 'recovery_held')
    if selection is not None:
        if 'assignment_id' in selection:
            row = assignments.inspect(database, config['project'], config['iteration'], config['operator_id'], selection['assignment_id'])
            require(row['handoff'].get('recovery') == recovery.contract(record),
                    'Unrelated assignment cannot use repair health admission.', 'recovery_held')
        else:
            stage = selection.get('stage')
            descendants = {'simplify', 'review-standards', 'review-spec', 'corrections', 'diagnosis'}
            root = next((r for r in assignments.rows(database) if r['assignment_id'] == record['assignment']), None)
            require(selection.get('issue') == authority['issue'] and (stage == 'repair' or
                    (stage in descendants and root is not None and root['handoff'].get('recovery') == recovery.contract(record))),
                    'Only registered repair lineage may start.', 'recovery_held')
    return recovery.contract(record)


def active_repair(database, project, iteration):
    key = (database.execute('PRAGMA database_list').fetchone()[2], project, iteration)
    observed = _active.get().get(key)
    return observed is not None and observed[5] is not None


def ownership(database, config, resolution):
    from .restart import ownership as validate
    from .repositories import RepositoryError
    item = assignments.current(database, config['project'], config['iteration'], config['operator_id'], require_active=False)
    try:
        validate(database, item)
    except RepositoryError as error:
        from .effect_resolution import permitted
        require(resolution is not None and ('ownership', error.code) in
                permitted(database, config['project'], config['iteration'], resolution),
                'Current ownership remains held.', error.code)


def local_check(database, project, iteration, observation, resolution, selection, operation):
    if observation is None:
        require(binding(database, project, iteration) is None, 'Supervision changed.', 'readiness_binding_changed')
        return
    config, selected, local, before, report, repair, identity = observation
    current = binding(database, project, iteration)
    require(current is not None, 'Supervised binding removed.', 'readiness_binding_changed')
    assert current is not None
    latest, latest_selected = configuration(current[0])
    require(current[1] == local == policy(latest, latest_selected, database),
            'Immutable local readiness policy changed.', 'readiness_inputs_changed')
    if operation != 'notification':
        validate_runtime_receipts(database, latest, latest_selected, local=True)
    item = assignments.current(database, project, iteration, config['operator_id'], require_active=False)
    require(scope_identity(item) == identity, 'Exact origin/repository/operator changed.', 'readiness_inputs_changed')
    if operation != 'notification':
        required_status = 'waiting-for-operator' if operation == 'continuation' else 'active'
        require(item['status'] == required_status and not controls.blocked(database, project, iteration),
                'Iteration held.', 'iteration_paused')
    controls.resume_scope(database, project, iteration)
    ownership(database, config, resolution)
    current_repair = repair_scope(database, config, selection, operation)
    if operation not in {'notification', 'continuation'}:
        require(not item.get('main_incident') or current_repair is not None,
                'Ordinary work remains frozen by the incident.', 'main_frozen')
    require(current_repair == repair, 'Recovery admission scope changed.', 'recovery_held')
    excluded = {'controls'}
    if operation in {'effect', 'notification', 'continuation'}:
        excluded |= {'capacity', 'spending'}
    if operation in {'notification', 'continuation'}:
        excluded |= {'containers', 'execution'}
    if operation in {'notification', 'continuation'} or repair is not None:
        excluded.add('delivery')
    if resolution is not None:
        from .effect_resolution import permitted
        allowed = permitted(database, project, iteration, resolution)
        if resolution.kind in {'merge-reopen', 'branch-reopen'}:
            require(operation == 'effect' and selection == {'merge_reopen': resolution.target},
                    'Corrective authority permits only exact reopening.', 'scope_mismatch')
        if allowed:
            excluded |= {'capacity', 'spending', 'delivery'}
    required_checks = {'scope', 'notification'} if operation == 'notification' else set(report['checks']) - excluded
    require(all(report['checks'].get(name, {}).get('state') == 'ready' for name in required_checks),
            'Current operation capabilities unavailable.', 'readiness_blocked')
    validate_selection(database, latest, latest_selected, selection, operation=operation)
    if operation == 'work':
        # Recompute mutable capacity/allowance locally, including nested checkpoints.
        scope = {'project': project, 'iteration': iteration, 'repository': item['repository'],
                 'repository_id': item['repository_onboarding']['metadata']['id']}
        readiness.slot_capacity(database, latest_selected['slots'], scope)
        from .model_access import SUBSCRIPTION_SCOPE
        from . import spending
        spending.capacity_preview(database, (project, iteration, *SUBSCRIPTION_SCOPE), now=int(readiness.time.time()))


def observe(database, project, iteration, *, resolution=None, selection=None, operation='work'):
    current = binding(database, project, iteration)
    if current is None:
        return None
    from .service import github_client
    config, selected = configuration(current[0])
    require((config['project'], config['iteration']) == (project, iteration) and
            config['state'] == database.execute('PRAGMA database_list').fetchone()[2],
            'Exact supervised state required.', 'readiness_binding_changed')
    before = snapshot(database, project, iteration)
    local = policy(config, selected, database)
    require(current[1] == local, 'Select/review changed local readiness inputs while paused.', 'readiness_inputs_changed')
    if operation != 'notification':
        validate_runtime_receipts(database, config, selected)
    client = github_client(config)
    arguments = {}
    if operation == 'planning-bootstrap':
        validate_bootstrap(database, config, selected, selection)
        assert selection is not None
        arguments['planning_bootstrap'] = selection['planning_bootstrap']
    report = readiness.collect(database, config, selected, client, **arguments)
    repair = repair_scope(database, config, selection, operation)
    if repair is not None:
        from .restart import registered_recovery
        require(registered_recovery(database, assignments.current(database, project, iteration, config['operator_id'], require_active=False), client) == repair,
                'Registered repair authority changed.', 'recovery_held')
    excluded = {'controls'}
    if operation in {'effect', 'notification', 'continuation'}:
        excluded |= {'capacity', 'spending'}
    if operation in {'notification', 'continuation'}:
        excluded |= {'containers', 'execution'}
    if operation in {'notification', 'continuation'} or repair is not None:
        excluded.add('delivery')
    if resolution is not None:
        from .effect_resolution import permitted
        allowed = permitted(database, project, iteration, resolution)
        if resolution.kind in {'merge-reopen', 'branch-reopen'}:
            require(operation == 'effect' and selection == {'merge_reopen': resolution.target},
                    'Corrective authority permits only exact reopening.', 'scope_mismatch')
        if allowed:
            # Exact previously-authorized effect resolution needs no NEW worker
            # allowance, unused slot or healthy deployment. All other gates stay.
            excluded |= {'capacity', 'spending', 'delivery'}
    required_checks = {'scope', 'notification'} if operation == 'notification' else set(report['checks']) - excluded
    require(all(report['checks'].get(name, {}).get('state') == 'ready' for name in required_checks),
            'Current scoped capabilities unavailable; inspect readiness.', 'readiness_blocked')
    validate_selection(database, config, selected, selection, operation=operation)
    return config, selected, local, before, report, repair, scope_identity(assignments.current(database, project, iteration, config['operator_id'], require_active=False))


def initial_planning_assignment(database, item):
    """Use the already-approved assignment identity, never invent a future approval."""
    current = binding(database, item['project_id'], item['iteration_id'])
    if current is None:
        return
    config, selected = configuration(current[0])
    require(current[1] == policy(config, selected, database), 'Sealed planning policy required.', 'readiness_inputs_changed')
    from . import planning_wakeup
    dispatch = readiness.load_config(config['dispatch_config'])
    approval = planning_wakeup.configuration(dispatch['planning_config'])
    item['planning']['assignment_id'] = approval['assignment_id']
    item['planning']['docs_path'] = f"docs/factory/{item['iteration_id']}/{approval['assignment_id']}"


def bootstrap_identity(database, config, selected, proposed, approval):
    """Authenticate only initial assignment/host-observation persistence, not dispatch."""
    from . import planning, planning_wakeup, skills
    item = assignments.current(database, config['project'], config['iteration'], config['operator_id'], require_active=False)
    work = proposed.get('planning', {})
    old = item.get('planning')
    require(item['status'] == proposed.get('status') == 'active' and not item.get('completion') and
            not item.get('previous_iteration') and not item.get('requirement_authority') and
            not item.get('requirement_changes') and not item.get('main_incident'),
            'Bootstrap cannot replace existing lifecycle authority.', 'scope_mismatch')
    if old is None:
        expected = {**item, 'planning': work, 'stage': 'planning_pending', 'execution_allowed': False,
                    'correlation': {**item['correlation'], 'stage': 'planning_pending'}}
        require(not any(item.get(k) for k in ('handoff', 'ticket_work', 'reservation')) and
                set(work) == {'assignment_id', 'revision', 'request', 'origin', 'status', 'skills',
                              'instructions', 'docs_path', 'pending_questions'} and work['revision'] == 1 and
                work['request'] == {'origin': item['origin'], 'expected_revision': 0, 'skills': selected['planning_skills']},
                'Only a fresh initial planning assignment may bootstrap.', 'scope_mismatch')
    else:
        expected = {**item, 'planning': {**old, **{k: work[k] for k in ('native_binding', 'native_execution') if k in work}}}
        require(not old.get('native_execution') and not old.get('host_publication') and not old.get('native_wakeup'),
                'Existing execution cannot be replaced by bootstrap.', 'execution_required')
    require(proposed == expected and work.get('status') == 'pending' and work.get('pending_questions', []) == [] and
            work.get('origin') == item['origin'] and work.get('instructions') == planning.INSTRUCTIONS and
            assignments.identifier(work.get('assignment_id')) and work.get('assignment_id') == approval['assignment_id'] and
            work.get('docs_path') == f"docs/factory/{item['iteration_id']}/{approval['assignment_id']}" and
            work.get('skills') == skills.resolve_installed(selected['planning_skills']),
            'Only exact approved planning observations may be saved.', 'scope_mismatch')
    host = approval['binding']
    require(all(host.get(k) == config[k] for k in ('state', 'project', 'iteration', 'operator_id')) and
            host.get('origin') == item['origin'] and planning.text(host.get('session_id')) and
            planning.text(host.get('reference')) and
            approval['source_digest'] == assignments.digest({'skills': work['skills'], 'adaptation': work['instructions']}),
            'Exact approved host origin and source binding required.', 'scope_mismatch')
    if old is not None:
        native = work.get('native_execution', {})
        require(work.get('native_binding') == {'assignment_id': approval['assignment_id'],
                'session_id': host['session_id'], 'reference': host['reference']} and
                native.get('loads') == ['grill-me', 'grilling'] and native.get('sufficient') is False and
                isinstance(native.get('observations'), list) and bool(native['observations']) and
                all(isinstance(m, dict) and set(m) == {'reference', 'role', 'content'} and
                    m['role'] in {'user', 'assistant'} and planning.text(m['reference']) and
                    planning.text(m['content']) and planning_wakeup.TAG not in m['content']
                    for m in native.get('observations', [])) and
                native.get('observations', [{}])[-1].get('role') == 'user',
                'Only genuine first host observations and initial loads may bootstrap.', 'execution_required')
        return planning_wakeup.scope(proposed, approval)
    return {'approval': approval, 'assignment_id': work['assignment_id']}


def validate_bootstrap(database, config, selected, selection):
    require(isinstance(selection, dict) and set(selection) == {'planning_bootstrap'},
            'Exact planning bootstrap selection required.', 'scope_mismatch')
    from . import planning_wakeup
    dispatch = readiness.load_config(config['dispatch_config'])
    approval = planning_wakeup.configuration(dispatch['planning_config'])
    return bootstrap_identity(database, config, selected, selection['planning_bootstrap'], approval)


def validate_selection(database, config, selected, selection, *, operation='work'):
    if operation == 'planning-bootstrap':
        validate_bootstrap(database, config, selected, selection)
        return
    if operation == 'continuation':
        from .iteration import validate_continuation
        item = assignments.current(database, config['project'], config['iteration'], config['operator_id'], require_active=False)
        require(isinstance(selection, dict) and set(selection) == {'continuation', 'predecessor'} and
                selection['predecessor'] == assignments.digest(item),
                'Exact completed predecessor required.', 'iteration_conflict')
        validate_continuation(database, item, config['operator_id'], selection['continuation'])
        return
    if selection is None:
        return
    require(not {'continuation', 'planning_bootstrap'} & set(selection),
            'Bootstrap/continuation permits no ordinary work.', 'scope_mismatch')
    if 'merge_reopen' in selection:
        require(operation == 'effect' and set(selection) == {'merge_reopen'},
                'Only corrective reopening may use this selection.', 'scope_mismatch')
        return
    if 'native_config' in selection:
        from . import planning_wakeup
        item = assignments.current(database, config['project'], config['iteration'], config['operator_id'], require_active=False)
        native = planning_wakeup.configuration(selection['native_config'], new_execution=item.get('planning', {}).get('status') != 'complete')
        require(planning_wakeup.scope(item, native) == selection['identity'],
                'Native wakeup selection changed.', 'slot_binding_changed')
        return
    scope = {'project': config['project'], 'iteration': config['iteration']}
    item = assignments.current(database, config['project'], config['iteration'], config['operator_id'], require_active=False)
    scope.update(repository=item['repository'], repository_id=item['repository_onboarding']['metadata']['id'])
    if 'assignment_id' in selection:
        row = assignments.inspect(database, config['project'], config['iteration'], config['operator_id'], selection['assignment_id'])
        request = row['handoff']
        runtime = row.get('runtime') or {}
        require(selection.get('run_id') == runtime.get('run_id'),
                'Selected run changed.', 'slot_binding_changed')
        assignments.require_result_active(row)
        require(not row.get('review_holds'), 'Candidate rejection remains held.', 'review_held')
        from . import requirements, diagnosis, bugs, recovery
        held = requirements.held_numbers(item) | diagnosis.held_numbers(item) | bugs.held_numbers(item) | recovery.held_numbers(item)
        require(request['issue']['number'] not in held, 'Selected requirements remain held.', 'requirements_held')
        readiness.slot_capacity(database, selected['slots'], scope, stage=request['stage'],
                                assignment_id=row['assignment_id'], run_id=selection.get('run_id'),
                                terminal=operation == 'effect')
        slot = next((s for s in selected['slots'] if s['claim_id'] == row['claim_id']), None)
    else:
        request = selection
        from .requirements import held_numbers
        require(request.get('issue', {}).get('number') not in held_numbers(item),
                'Selected requirements remain held.', 'requirements_held')
        readiness.slot_capacity(database, selected['slots'], scope, stage=request['stage'])
        slot = next((s for s in selected['slots'] if s['claim_id'] == request['claim_id']), None)
    require(slot is not None and slot['stage'] == request['stage'] and slot['profile'] == request['profile'],
            'Selected stage/profile/claim not configured.', 'slot_binding_changed')
    assert slot is not None
    if 'launcher' in selection:
        require(selection['launcher'] == slot['launcher'], 'Selected launcher changed.', 'slot_binding_changed')
    descriptors = [{k: v for k, v in d.items() if k != 'instructions'} for d in request['skills']]
    require(slot['skills'] == descriptors, 'Selected source closure changed.', 'slot_binding_changed')


def compare(database, project, iteration, observation, *, resolution=None):
    if observation is None:
        require(binding(database, project, iteration) is None, 'Supervision changed.', 'readiness_binding_changed')
        return
    config, selected, local, before, report, repair, identity = observation
    current = binding(database, project, iteration)
    require(current is not None, 'Supervised binding removed.', 'readiness_binding_changed')
    assert current is not None
    latest, latest_selected = configuration(current[0])
    require(policy(latest, latest_selected, database) == local and snapshot(database, project, iteration) == before,
            'Local inputs/ownership changed during capability observation.', 'readiness_inputs_changed')
    controls.resume_scope(database, project, iteration)
    ownership(database, config, resolution)


@contextmanager
def admission(database, project, iteration, *, resolution=None, selection=None, operation='work'):
    require(type(operation) is str and operation in {'work', 'effect', 'notification', 'continuation', 'planning-bootstrap'}, 'Unknown readiness operation.', 'invalid_readiness')
    key = (database.execute('PRAGMA database_list').fetchone()[2], project, iteration)
    active = _active.get()
    if key in active:
        # Existing nested checkpoints may not perform remote I/O under the lane.
        local_check(database, project, iteration, active[key], resolution, selection, operation)
        if operation != 'notification':
            controls.require_active(database, project, iteration, resolution=resolution)
        yield
        return
    observation = observe(database, project, iteration, resolution=resolution, selection=selection, operation=operation)
    with controls.lane(database, project, iteration):
        compare(database, project, iteration, observation, resolution=resolution)
        local_check(database, project, iteration, observation, resolution, selection, operation)
        token = _active.set({**active, key: observation})
        try:
            if operation != 'notification':
                controls.require_active(database, project, iteration, resolution=resolution)
            yield
        finally:
            _active.reset(token)


def check(database, project, iteration, *, resolution=None, selection=None, operation='work'):
    """Final pre-write check where the existing seam does not claim durable state."""
    with admission(database, project, iteration, resolution=resolution, selection=selection, operation=operation):
        if operation != 'notification':
            controls.require_active(database, project, iteration, resolution=resolution)
