"""Host-owned incident scope; ordinary worker, review and delivery gates remain authoritative."""
import hashlib
import json
from pathlib import Path

from . import assignments, controls, delivery, simplification, tickets
from .planning import require, text
from .repositories import RepositoryError, save_iteration
from .sandbox_source import manifest, relative

CONTRACT = 'factory-main-recovery-v1'
CONFIG_FIELDS = {'operator_id', 'reference', 'incident_id', 'evidence_id', 'issue', 'paths', 'removable_assignments'}


def active(item):
    incident = item.get('main_incident')
    return item.get('main_recoveries', {}).get(incident['incident_id']) if incident else None


def pending_registration(item):
    """Registration authority must be cleared in its owning iteration, never dropped."""
    return bool((item.get('main_incident') or {}).get('recovery_issue') is not None or
                any(record['status'] != 'cleared' for record in item.get('main_recoveries', {}).values()))


def recovery_number(item):
    record = active(item)
    return record['config']['issue']['number'] if record else None


def held_numbers(item):
    return {receipt['issue']['number'] for record in item.get('main_recoveries', {}).values()
            if record['status'] != 'cleared' for receipt in record.get('removed', {}).values()}


def checkpoint(database, item):
    incident = item['main_incident']
    delivery.validate_incident(incident, item['delivery_monitor']['config'])
    with database:
        database.execute('UPDATE delivery_incidents SET payload=? WHERE project=? AND iteration=? AND id=?',
                         (json.dumps(incident), item['project_id'], item['iteration_id'], incident['incident_id']))
        save_iteration(database, item)


def load_config(database, path, operator, *, continuation=False):
    from .merge import private_file, controller_paths
    path = private_file(path)
    controller_paths(database, path)
    require(path.stat().st_size <= delivery.LIMIT, 'Bounded recovery configuration required.', 'invalid_recovery')
    config = simplification.object_json(path.read_text(), 'invalid_recovery')
    fields = CONFIG_FIELDS | ({'continuation'} if continuation else set())
    require(set(config) == fields and config['operator_id'] == operator and text(config['reference']) and
            assignments.identifier(config['incident_id']) and assignments.sha(config['evidence_id']),
            'Private exact incident authorization required.', 'invalid_recovery')
    issue = config['issue']
    require(isinstance(issue, dict) and set(issue) == {'number', 'id', 'node_id', 'body_sha256'} and
            delivery.positive(issue['number']) and delivery.positive(issue['id']) and text(issue['node_id']) and
            assignments.sha(issue['body_sha256']), 'Exact recovery issue required.', 'invalid_recovery')
    for key in ('paths', 'removable_assignments'):
        require(isinstance(config[key], list) and len(config[key]) <= 1000 and
                all(isinstance(v, str) for v in config[key]) and len(set(config[key])) == len(config[key]),
                'Unique bounded incident paths/removal scope required.', 'invalid_recovery')
    require(config['paths'] and all(assignments.sha(v) for v in config['removable_assignments']),
            'Explicit incident scope required.', 'invalid_recovery')
    for path in config['paths']:
        relative(path)
    if continuation:
        resume = config['continuation']
        require(isinstance(resume, dict) and set(resume) == {'kind', 'assignment_id', 'runtime_sha256', 'change_id'} and
                resume['kind'] in {'infrastructure', 'revision'} and assignments.sha(resume['assignment_id']) and
                assignments.sha(resume['runtime_sha256']) and
                (resume['change_id'] is None if resume['kind'] == 'infrastructure' else assignments.identifier(resume['change_id'])),
                'Exact prior attempt and explicit continuation kind required.', 'invalid_recovery')
    return config


def exact_issue(github, repository, pin):
    issue = tickets.gh.read_issue(github, repository, pin['number'])
    require(all(issue[k] == pin[k] for k in ('number', 'id', 'node_id')) and
            hashlib.sha256(issue['body'].encode()).hexdigest() == pin['body_sha256'],
            'Exact affected issue/requirements changed.', 'scope_mismatch')
    return issue


def register(database, project, iteration, operator, config_path, github):
    config = load_config(database, config_path, operator)
    item = assignments.current(database, project, iteration, operator)
    saved = delivery.monitor_state(item, operator)
    require(saved is not None, 'Configured delivery observer required.', 'recovery_held')
    assert saved is not None
    require(github.base == saved['config']['api_base'], 'Wrong configured tracker route.', 'scope_mismatch')
    delivery.validate_persisted(database, item, saved)
    incident = item.get('main_incident')
    require(incident is not None and incident['incident_id'] == config['incident_id'],
            'Current exact incident required.', 'recovery_held')
    assert incident is not None
    old = active(item)
    if old:
        require(old['config'] == config, 'Incident authority cannot be replaced.', 'recovery_conflict')
        return old
    require(incident['recovery_issue'] is None and incident['latest_evidence'] == config['evidence_id'],
            'Incident evidence changed or already registered.', 'recovery_conflict')
    evidence = delivery.persisted_evidence(database, project, iteration, config['evidence_id'], saved['config'])
    require(evidence['status'] != 'healthy' and assignments.commit(evidence['main']),
            'Exact broken main revision required before repair.', 'recovery_held')
    issue = exact_issue(github, item['repository'], config['issue'])
    require(issue['state'] == 'open', 'Recovery issue must be open.', 'recovery_held')
    rows = assignments.rows(database)
    related = [a for a in rows if a['ticket_scope']['project'] == project and a['ticket_scope']['iteration'] == iteration]
    require(all(not a.get('runtime') or (a['runtime']['status'] in {'complete', 'failed', 'stopped'} and
                a['runtime'].get('container_removed') is True) for a in related),
            'Stop/reconcile existing workers before recovery takes ownership.', 'run_conflict')
    removable = {}
    for identity in config['removable_assignments']:
        prior = next((a for a in related if a['assignment_id'] == identity), None)
        require(prior is not None and prior.get('merge', {}).get('status') == 'delivered' and
                prior['handoff']['repository'] == item['repository'] and
                prior['handoff']['issue']['number'] != config['issue']['number'],
                'Removal scope requires exact previously delivered work.', 'scope_mismatch')
        assert prior is not None
        exact_issue(github, item['repository'], prior['handoff']['issue'])
        removable[identity] = {'issue': prior['handoff']['issue'], 'delivery': prior['merge']}
    record = {'status': 'registered', 'config': config, 'evidence': evidence, 'assignment': None,
              'parked': [a['assignment_id'] for a in related], 'reservation': item.pop('reservation', None),
              'removable': removable, 'removed': {}, 'clearance': None}
    item.setdefault('main_recoveries', {})[incident['incident_id']] = record
    # A registration sentinel keeps health polling frozen even before assignment preparation.
    incident.update(recovery_assignment=assignments.digest(config), recovery_issue=config['issue']['number'])
    with controls.admission(database, project, iteration), database:
        for prior in related:
            prior['recovery_disposition'] = incident['incident_id']
            database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                             (json.dumps(prior), prior['assignment_id']))
        checkpoint(database, item)
    return record


def revision_for(item, record, change_id):
    """A controlled revision may expose new bytes to observations, never to old workers."""
    from . import requirements
    change = item.get('requirement_changes', {}).get(change_id)
    linked = change.get('stuck_revision') if change else None
    stuck = item.get('stuck_work', {}).get(linked['assignment_id']) if linked else None
    number = str(record['config']['issue']['number'])
    require(change is not None and linked is not None and stuck is not None and
            change['kind'] == 'material' and record['assignment'] in change['retired_assignments'] and
            record['assignment'] in stuck['retired_assignments'] and
            change['issues'][number] == record['config']['issue'] and
            all(stuck[k] == linked[k] for k in ('event_id', 'applied_response', 'affected', 'authorization')) and
            stuck['direction']['action'] == 'revise' and
            change.get('new_revision') == requirements.authority(item, int(number)) and
            change.get('new_inputs', {}).get('handoff') == change['new_revision']['spec'],
            'Recovery revision must retain the original correlated direction and exact new authority.', 'recovery_held')
    assert change is not None and linked is not None and stuck is not None
    return change, stuck


def observed_issue_pin(item, record):
    # During controlled publication/resume the old host authority stays immutable,
    # but frontier must be able to observe the exact newly published, still-held issue.
    for change_id, change in item.get('requirement_changes', {}).items():
        if (change.get('stuck_revision') and record['assignment'] in change.get('retired_assignments', []) and
                change['status'] in {'publication-pending', 'published', 'resume-pending', 'resumed'} and
                str(record['config']['issue']['number']) in change.get('updated_issues', {})):
            change, _ = revision_for(item, record, change_id)
            return change['updated_issues'][str(record['config']['issue']['number'])]
    return record['config']['issue']


def inspect(database, project, iteration, operator):
    item = assignments.current(database, project, iteration, operator, require_active=False)
    record = active(item)
    attempt = next((a for a in assignments.rows(database) if record and a['assignment_id'] == record['assignment']), None)
    receipt = None if attempt is None else {
        'assignment_id': attempt['assignment_id'], 'handoff_digest': attempt['handoff_digest'], 'revision': attempt['revision'],
        'runtime': attempt.get('runtime'), 'runtime_sha256': assignments.digest(attempt.get('runtime', {}))}
    return {'incident': item.get('main_incident'), 'recoveries': item.get('main_recoveries', {}), 'attempt': receipt}


def validate_direction(database, item, stuck, operator, action):
    from . import attention, diagnosis
    require(stuck.get('status') == 'released' and stuck.get('blocks_verified') is True and
            stuck.get('event_id') is not None and isinstance(stuck.get('applied_response'), dict) and
            stuck.get('direction', {}).get('action') == action and
            isinstance(stuck.get('authorization'), dict),
            'Fully applied and released independent diagnosis direction required.', 'recovery_held')
    notification = attention.get_record(database, item, stuck['event_id'])
    attention.validate_response(stuck['applied_response'], notification, operator)
    require(stuck['applied_response'] == notification['decision_response'] and
            diagnosis.operator_direction(stuck['applied_response']['response']) == stuck['direction'],
            'Complete actual correlated operator response required.', 'decision_required')
    axis = next((a for a in assignments.rows(database) if a['assignment_id'] == stuck.get('diagnosis_assignment')), None)
    require(axis is not None, 'Original completed diagnosis assignment required.', 'execution_required')
    assert axis is not None
    require(axis['handoff']['diagnosis']['pins'] == stuck['pins'] and
            axis['runtime']['status'] == 'complete' and axis['runtime']['container_removed'] is True and
            axis['result_disposition']['isolated_execution'] is True and
            diagnosis.validate_result(axis, axis['submitted_result']) == axis['diagnosis_result'] and
            stuck['authorization']['diagnosis'] == diagnosis.execution_binding(axis),
            'Original independent diagnosis execution must remain verifiable.', 'execution_required')


def continue_recovery(database, project, iteration, operator, config_path, github):
    """Explicit host continuation: retire a reconciled attempt; never relaunch or reset usage."""
    authorization = load_config(database, config_path, operator, continuation=True)
    resume = authorization['continuation']
    config = {k: authorization[k] for k in CONFIG_FIELDS}
    item = assignments.current(database, project, iteration, operator)
    record = active(item)
    require(record is not None and config['incident_id'] == record['config']['incident_id'],
            'Exact active registered incident required.', 'recovery_held')
    assert record is not None
    saved = delivery.monitor_state(item, operator)
    require(saved is not None and github.base == saved['config']['api_base'], 'Configured tracker route required.', 'scope_mismatch')
    delivery.validate_persisted(database, item, saved)
    history = record.get('continuations', [])
    if history and history[-1]['authorization'] == authorization:
        return record  # Receipt replay, not permission to retire another root.
    require(record['assignment'] == resume['assignment_id'] and len(history) < 100,
            'Bounded exact current attempt required.', 'recovery_held')
    prior = next(a for a in assignments.rows(database) if a['assignment_id'] == record['assignment'])
    require(prior['handoff']['stage'] == 'repair' and prior['handoff']['recovery'] == contract(record) and
            assignments.digest(prior['handoff']) == prior['handoff_digest'],
            'Exact original registered recovery handoff required.', 'stale_assignment')
    runtime = prior.get('runtime', {})
    require(runtime.get('status') in {'failed', 'stopped', 'complete'} and runtime.get('container_removed') is True and
            assignments.digest(runtime) == resume['runtime_sha256'],
            'Exact terminal runtime and verified worker absence required.', 'run_conflict')
    # A continuation is not new incident/removal authority. Revised issue bytes are
    # the only scope change; a new host reference records that explicit authorization.
    require(all(config[k] == record['config'][k] for k in CONFIG_FIELDS - {'reference', 'issue'}) and
            all(config['issue'][k] == record['config']['issue'][k] for k in ('number', 'id', 'node_id')),
            'Continuation cannot expand incident paths/removal authority or replace the repair issue.', 'scope_mismatch')
    related = [a for a in assignments.rows(database) if a['ticket_scope']['project'] == project and
               a['ticket_scope']['iteration'] == iteration and a['ticket_scope']['issue_number'] == config['issue']['number']]
    require(all(not a.get('runtime') or (a['runtime']['status'] in {'complete', 'failed', 'stopped'} and
                a['runtime'].get('container_removed') is True) for a in related) and
            not controls.live_attempts({'assignments': related}),
            'Reconcile every related worker/controller before supersession.', 'run_conflict')
    if resume['kind'] == 'infrastructure':
        require(config['issue'] == record['config']['issue'] and runtime['status'] in {'failed', 'stopped'} and
                runtime.get('recoverable') is True and not prior.get('stuck_disposition') and
                not prior.get('requirement_disposition') and not prior.get('submitted_result') and
                not any(config['issue']['number'] in r['affected'] and r['status'] != 'released'
                        for r in item.get('stuck_work', {}).values()),
                'Infrastructure continuation cannot substitute for engineering direction or revisions.', 'recovery_held')
        from .requirements import held_numbers
        require(config['issue']['number'] not in held_numbers(item), 'Requirement hold remains independent.', 'requirements_held')
    else:
        from . import requirements, requirement_publication
        change, stuck = revision_for(item, record, resume['change_id'])
        require(change['status'] == 'resumed' and stuck['status'] == 'released' and
                stuck.get('requirement_change') == resume['change_id'] and
                config['issue'] == change['updated_issues'][str(config['issue']['number'])] and
                change['old_revision'][str(config['issue']['number'])] == prior['revision'],
                'Only the fully resumed exact versioned revision may supersede recovery.', 'recovery_held')
        validate_direction(database, item, stuck, operator, 'revise')
        require(change['resolution']['reference'] == stuck['applied_response']['reference'] and
                change['resolution']['content'] == stuck['direction']['direction'],
                'Versioned resolution must retain the actual applied revise response.', 'decision_required')
        requirements.direction(change['resolution'], item, operator)
        requirements.direction(change['resume_direction'], item, operator)
        requirement_publication.verify_publication(database, item, change, assignments.ReadOnlyGitHub(github))
    profile = prior['handoff']['profile']
    try:
        assignments.admission_preview(database, (project, iteration, profile['provider'], profile['model'], profile['operation']))
    except assignments.SpendingError as error:
        raise RepositoryError(error.code, str(error)) from error
    issue = exact_issue(github, item['repository'], config['issue'])
    require(issue['state'] == 'open', 'Current revised recovery issue must remain open.', 'recovery_held')
    receipt = {'authorization': authorization, 'prior_config': record['config'], 'prior_assignment': prior['assignment_id'],
               'prior_handoff_digest': prior['handoff_digest'], 'prior_runtime': runtime,
               'prior_revision': prior['revision']}
    with controls.admission(database, project, iteration), database:
        for assignment in related:
            assignment['recovery_disposition'] = config['incident_id']
            if assignment['assignment_id'] not in record['parked']:
                record['parked'].append(assignment['assignment_id'])
            database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                             (json.dumps(assignment), assignment['assignment_id']))
        record.setdefault('continuations', []).append(receipt)
        record.update(config=config, assignment=None, status='registered')
        item['main_incident'].update(recovery_assignment=assignments.digest(config), recovery_issue=config['issue']['number'])
        checkpoint(database, item)
    return record


def released_claim(database, assignment):
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
        (assignment['ticket_scope']['project'], assignment['ticket_scope']['iteration'])).fetchone()
    if row is None:
        return False
    item = json.loads(row[0])
    record = active(item)
    if record and assignment['assignment_id'] in record['parked']:
        return True
    recovery = assignment['handoff'].get('recovery')
    return bool(recovery and item.get('main_recoveries', {}).get(recovery['incident_id'], {}).get('status') == 'cleared')


def contract(record):
    return {'contract': CONTRACT, 'incident_id': record['config']['incident_id'],
            'authority_sha256': assignments.digest(record['config']), 'evidence_id': record['config']['evidence_id'],
            'main': record['evidence']['main'], 'paths': record['config']['paths'],
            'removable_assignments': record['config']['removable_assignments']}


def preceding(record):
    evidence = {'scope': contract(record), 'evidence': record['evidence'], 'removable': record['removable']}
    if record.get('continuations'):
        evidence['continuations'] = record['continuations']
    return [simplification.artifact('incident-evidence', simplification.handoff_json(evidence))]


def handoff(database, item, request):
    record = active(item)
    require(record is not None and request['issue'] == record['config']['issue'] and
            request['baseline'] == record['evidence']['main'] and request['candidate'] is None and
            request['preceding'] == preceding(record),
            'Repair needs controller-owned incident/check/deployment evidence and exact baseline.', 'recovery_held')
    assert record is not None
    old = record['assignment']
    if old:
        prior = next(a for a in assignments.rows(database) if a['assignment_id'] == old)
        if prior['claim_id'] != request['claim_id']:
            direction = next((r for r in item.get('stuck_work', {}).values() if r['status'] == 'released' and
                              r.get('direction', {}).get('action') == 'retry' and old in r['retired_assignments']), None)
            require(direction is not None, 'Repair retry requires the separate debug/operator path.', 'recovery_held')
            validate_direction(database, item, direction, item['approval']['operator_id'], 'retry')
    for identity in record['parked']:
        prior = next(a for a in assignments.rows(database) if a['assignment_id'] == identity)
        require(request['claim_id'] != prior['claim_id'] and
                request['profile']['name'] != prior['handoff']['profile']['name'] and
                request['profile']['home'] != prior['handoff']['profile']['home'],
                'Supersession requires a fresh claim and dedicated profile, never an archived role.', 'claim_conflict')
    return contract(record)


def prepare(database, project, iteration, operator, request, github, dry_run=False):
    require(isinstance(request, dict) and set(request) == assignments.REQUEST_FIELDS and request['stage'] == 'repair',
            'Supply a bounded fresh repair-role request.', 'invalid_recovery')
    item = assignments.current(database, project, iteration, operator)
    record = active(item)
    require(record is not None, 'Register exact host incident scope first.', 'recovery_held')
    assert record is not None
    request = {**request, 'preceding': preceding(record)}
    return assignments.prepare(database, project, iteration, operator, request, github, dry_run)


def admission(item, stage, number):
    if item.get('main_incident') and stage != 'diagnosis':
        require(number == recovery_number(item) and stage != 'implementation',
                'Main recovery has priority; ordinary work is held.', 'main_frozen')
    if stage == 'repair':
        require(number == recovery_number(item), 'Repair role has no registered incident.', 'recovery_held')


def lineage(database, assignment):
    """Resolve controller-prepared ancestry, not worker prose or chosen role names."""
    rows = {a['assignment_id']: a for a in assignments.rows(database)}
    seen = set()
    while assignment['handoff']['stage'] != 'repair':
        require(assignment['assignment_id'] not in seen, 'Cyclic recovery ancestry.', 'scope_mismatch')
        seen.add(assignment['assignment_id'])
        h = assignment['handoff']
        if h['stage'] == 'simplify':
            identity = h['simplification']['implementation_assignment']
        elif h['stage'] in {'review-spec', 'review-standards'}:
            identity = h['review']['pins']['candidate_assignment']
        elif h['stage'] == 'corrections':
            identity = h['corrections']['pins']['candidate_assignment']
        else:
            identity = None
        require(identity in rows, 'Recovery ancestry missing.', 'scope_mismatch')
        assignment = rows[identity]
    return assignment


def validate_result(assignment, result):
    scope = assignment['handoff']['recovery']
    modifying_repair = assignment['handoff']['stage'] == 'repair'
    name = 'recovery-report' if modifying_repair else 'recovery-choice'
    artifact = next((a for a in result['artifacts'] if a['name'] == name), None)
    require(artifact is not None, 'Evidence-backed recovery report required.', 'invalid_result')
    assert artifact is not None
    report = simplification.object_json(artifact['content'], 'invalid_result')
    fields = {'contract', 'scope', 'mode', 'rationale', 'removed_assignments', 'regression', 'cleanup'}
    require(set(report) == fields | ({'diagnosis'} if modifying_repair else set()) and
            report['contract'] == CONTRACT and report['scope'] == scope and report['mode'] in {'roll-forward', 'safe-revert'} and
            all(text(report[k]) for k in ('rationale', 'regression', 'cleanup')) and
            isinstance(report['removed_assignments'], list) and
            all(assignments.sha(v) for v in report['removed_assignments']) and
            len(set(report['removed_assignments'])) == len(report['removed_assignments']) and
            set(report['removed_assignments']) <= set(scope['removable_assignments']),
            'Exact scope, justified choice and removed delivered behavior required.', 'invalid_result')
    from . import diagnosis
    from .sandbox import check_terminal_results
    root = Path(assignment['runtime']['artifacts'])
    check_terminal_results(root, result['tests'], verbatim=True)
    if modifying_repair:
        diagnosis.validate_report(report['diagnosis'], {'pins': scope}, assignment['handoff']['adaptation'], result['tests'])
        require(report['diagnosis']['feedback_loop'] is not None and not report['diagnosis']['blockers'],
                'Missing diagnosis capability is not recovery success.', 'invalid_result')
    require(result['status'] == 'done', 'Incomplete recovery cannot claim a successful choice.', 'invalid_result')
    require(set(simplification.changed_paths(manifest(root / 'inputs/baseline'), manifest(root / 'workspace'))) <= set(scope['paths']),
            'Repair changed unrelated paths.', 'scope_violation')
    return report


def merge_boundary(database, item, config, builder, candidate_root):
    record = active(item)
    recovery = config['recovery']
    require(record is not None and recovery is not None, 'Exact active recovery required.', 'main_frozen')
    assert record is not None
    assert recovery is not None
    root = lineage(database, builder)
    require({k: recovery.get(k) for k in ('incident_id', 'implementation_assignment', 'issue')} == {
                'incident_id': record['config']['incident_id'], 'implementation_assignment': root['assignment_id'],
                'issue': record['config']['issue']['number']} and record['assignment'] == root['assignment_id'] and
            item['main_incident']['recovery_assignment'] == root['assignment_id'] and
            config['baseline'] == record['evidence']['main'] and config['issue'] == record['config']['issue']['number'] and
            root['handoff']['recovery'] == contract(record), 'Recovery authority/ancestry changed.', 'scope_mismatch')
    assignments.require_result_active(root)
    report = validate_result(root, root['submitted_result'])
    if builder['assignment_id'] != root['assignment_id']:
        from .merge import execution_binding
        simplification.implementation(database, item['project_id'], item['iteration_id'], config['operator_id'], root['assignment_id'])
        require(recovery.get('root_execution') == execution_binding(root),
                'Corrective recovery also requires independent original diagnosis execution binding.', 'execution_required')
        report = validate_result(builder, builder['submitted_result'])
    require(recovery.get('removed_assignments') == report['removed_assignments'],
            'Independent host removal assessment must match the exact repair outcome.', 'execution_required')
    changed = simplification.changed_paths(manifest(candidate_root / 'inputs/baseline'), manifest(candidate_root / 'workspace'))
    require(changed and set(changed) <= set(record['config']['paths']), 'Only authorized incident paths may merge.', 'scope_violation')
    return report


def affected_membership(github, item, issue, identity=None):
    board = item['ticket_work']['board']
    member = tickets.gh.membership(github, board, item['repository'], issue)
    require(member is not None and (identity is None or member['id'] == identity),
            'Affected ticket board membership missing or replaced.', 'recovery_held')
    assert member is not None
    if 'milestone' in board:
        require(tickets.gh.issue_scope(issue, board) == board['milestone']['id'],
                'Affected ticket left its exact native scope.', 'scope_mismatch')
    elif 'scope' in board:
        require(member['scope'] == board['scope']['options'][item['iteration_id']],
                'Affected ticket left its exact board scope.', 'scope_mismatch')
    require(member['status'] in board['status']['options'].values(), 'Unknown affected progress.', 'recovery_held')
    return member


def reopen_removed(database, item, record, report, github):
    project, iteration = item['project_id'], item['iteration_id']
    board = item['ticket_work']['board']
    statuses = item['ticket_work']['request']['tracker']['statuses']
    for identity in report['removed_assignments']:
        controls.require_active(database, project, iteration)
        pin = record['removable'][identity]['issue']
        issue = exact_issue(github, item['repository'], pin)
        member = affected_membership(github, item, issue)
        membership_id = member['id']
        with controls.admission(database, project, iteration):
            receipt = record['removed'].setdefault(identity, {'issue': pin, 'open_intent': False, 'status_intent': False})
            checkpoint(database, item)
        if issue['state'] != 'open':
            with controls.admission(database, project, iteration):
                require(not receipt['open_intent'], 'Unknown reopening outcome; reconcile exact issue, never repeat blindly.', 'delivery_uncertain')
                receipt['open_intent'] = True
                checkpoint(database, item)
            controls.require_active(database, project, iteration)
            try:
                github.request('/repos/' + item['repository'] + '/issues/' + str(pin['number']),
                               {'state': 'open', 'state_reason': 'reopened'}, 'PATCH')
            except RepositoryError:
                pass
        issue = exact_issue(github, item['repository'], pin)
        require(issue['state'] == 'open', 'Removed behavior ticket must read back open.', 'delivery_uncertain')
        # An admitted write may finish after pause; retain its truthful receipt before any next intent.
        receipt['issue_readback'] = issue
        checkpoint(database, item)
        protected = {board['status']['options'][name] for role, name in statuses.items() if role in {'blocked', 'deferred'}}
        member = affected_membership(github, item, issue, membership_id)
        option = member['status'] if member['status'] in protected else board['status']['options'][statuses['ready']]
        if member['status'] != option:
            with controls.admission(database, project, iteration):
                require(not receipt['status_intent'], 'Unknown status outcome; reconcile before any retry.', 'delivery_uncertain')
                receipt['status_intent'] = True
                checkpoint(database, item)
            # The issue PATCH and durable intent are external-I/O boundaries. Refresh
            # exact issue/scope/progress immediately before a status mutation, not
            # from the pre-reopening snapshot. Independent Blocked/deferred wins.
            issue = exact_issue(github, item['repository'], pin)
            require(issue['state'] == 'open', 'Reopened issue changed before status mutation.', 'delivery_uncertain')
            member = affected_membership(github, item, issue, membership_id)
            option = member['status'] if member['status'] in protected else option
            controls.require_active(database, project, iteration)
            if member['status'] != option:
                try:
                    tickets.gh.set_field(github, board['project_id'], member['id'], board['status']['id'], option)
                except RepositoryError:
                    pass
        issue = exact_issue(github, item['repository'], pin)
        member = affected_membership(github, item, issue, membership_id)
        dependencies = tickets.gh.dependencies(github, item['repository'], pin['number'])
        require(issue['state'] == 'open' and member['status'] in protected | {option},
                'Reopened ticket progress not verified.', 'delivery_uncertain')
        receipt.update(issue_readback=issue, membership=member, dependencies=dependencies)
        prior = next(a for a in assignments.rows(database) if a['assignment_id'] == identity)
        prior['merge'].update(status='incomplete', close_allowed=False,
                              failure={'code': 'delivery_removed', 'message': 'Recovery removed accepted behavior.'})
        with database:
            database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(prior), identity))
            checkpoint(database, item)
        controls.require_active(database, project, iteration)


def verify(database, project, iteration, operator, config_path, github):
    from . import merge
    config = merge.load_config(config_path, operator)
    merge.controller_paths(database, config_path)
    require(github.base == config['api_base'], 'Wrong configured tracker route.', 'scope_mismatch')
    existing = assignments.current(database, project, iteration, operator)
    recovery = config['recovery']
    require(recovery is not None, 'Recovery merge authority required.', 'recovery_held')
    previous = existing.get('main_recoveries', {}).get(recovery['incident_id'])
    if previous and previous['status'] == 'cleared':
        require(not existing.get('main_incident'), 'A later incident remains frozen; old clearance cannot release it.', 'recovery_held')
        require(previous['assignment'] == recovery['implementation_assignment'] and
                previous['clearance']['merge']['candidate'] == config['head'], 'Foreign clearance replay.', 'scope_mismatch')
        return previous
    # Ordinary merge verification retains all execution, independent review and integrated gates.
    journal = merge.lifecycle(database, project, iteration, operator, config_path, 'verify-delivery')
    item = assignments.current(database, project, iteration, operator)
    record = active(item)
    require(record is not None and journal['status'] == 'delivered', 'Repair integration incomplete.', 'recovery_held')
    assert record is not None
    require(config['required_checks'] == item['delivery_monitor']['config']['required_checks'],
            'Repair and delivery observers must retain the same required checks.', 'recovery_held')
    prior, builder, root = merge.chain(database, project, iteration, operator, config, config_path)
    report = merge_boundary(database, item, config, builder, root)
    reopen_removed(database, item, record, report, github)
    item = assignments.current(database, project, iteration, operator)
    sampled = delivery.sample(database, project, iteration, operator)
    item = assignments.current(database, project, iteration, operator)
    record = active(item)
    evidence = sampled['evidence']
    require(record is not None, 'Registered recovery vanished.', 'recovery_held')
    assert record is not None
    require(evidence['status'] == 'healthy' and evidence['main'] == journal['integrated_evidence']['integrated'],
            'Actual configured deployment health on exact repair merge required.', 'recovery_held')
    # Refresh external requirements/dependencies/status after health reads, while still frozen.
    reopen_removed(database, item, record, report, github)
    observed = tickets.frontier(database, item, assignments.ReadOnlyGitHub(github))
    item = observed
    record = active(item)
    assert record is not None
    final_sample = delivery.sample(database, project, iteration, operator)
    require(final_sample['evidence']['status'] == 'healthy' and final_sample['evidence']['main'] == evidence['main'],
            'Delivery changed during affected-ticket readback.', 'recovery_held')
    item = assignments.current(database, project, iteration, operator)
    record = active(item)
    assert record is not None
    evidence = final_sample['evidence']
    with controls.admission(database, project, iteration), database:
        record.update(status='cleared', clearance={'merge': journal['integrated_evidence'], 'checks': journal['delivery_checks'],
                                                  'evidence_id': assignments.digest(evidence), 'frontier': observed['ticket_work']['frontier']})
        incident = item['main_incident']
        incident.update(status='cleared', clearance_evidence=assignments.digest(evidence))
        for assignment in assignments.rows(database):
            if assignment.get('recovery_disposition') == incident['incident_id']:
                assignment.pop('recovery_disposition')
                database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(assignment), assignment['assignment_id']))
        item.pop('main_incident')
        database.execute('UPDATE delivery_incidents SET payload=? WHERE project=? AND iteration=? AND id=?',
                         (json.dumps(incident), project, iteration, incident['incident_id']))
        save_iteration(database, item)
    return record
