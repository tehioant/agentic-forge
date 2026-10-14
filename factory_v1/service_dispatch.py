"""Deterministic admitted stage routing over existing controller lifecycle contracts.

No shell plugin registry, agent polling, worker-authored authority or parallel claims.
One configured ticket is driven from the authoritative frontier; a missing host
capability is a hold, never a fabricated executor or permission to fall back.
"""
from pathlib import Path

from . import assignments, controls, merge, publication, requirement_execution, review
from . import role_skills, sandbox, simplification, tickets
from .planning import checkpoint, require, text
from .repositories import state_lock

FIELDS = {'operator_id', 'project', 'iteration', 'reference', 'implementation', 'roles', 'launchers',
          'tickets', 'synthesis_config', 'review_config', 'publication_config', 'merge_config'}
ROLES = {'simplify', 'review-standards', 'review-spec'}


def blocked(reason):
    return {'status': 'blocked', 'dispatch_allowed': False, 'blockers': [reason]}


def validate(config, service):
    require(isinstance(config, dict) and set(config) == FIELDS and text(config['reference']) and
            all(config[k] == service[k] for k in ('operator_id', 'project', 'iteration')) and
            isinstance(config['roles'], dict) and set(config['roles']) <= ROLES and
            isinstance(config['launchers'], dict) and set(config['launchers']) <= ROLES | {'implementation'},
            'Exact scoped built-in dispatch configuration required; shell plugins are unsupported.', 'invalid_dispatch')
    require(config['implementation'] is None or (isinstance(config['implementation'], dict) and
            config['implementation'].get('stage') == 'implementation'),
            'Configure one exact implementation envelope, not a second backlog.', 'invalid_dispatch')
    for role in config['roles'].values():
        require(isinstance(role, dict) and set(role) == {'profile', 'skills', 'claim_id'},
                'Fresh role profile, installed closure and claim required.', 'invalid_dispatch')
    for key in ('synthesis_config', 'review_config', 'publication_config', 'merge_config'):
        require(config[key] is None or assignments.absolute(config[key]),
                'Use explicit host capability paths or null.', 'invalid_dispatch')
    require(all(assignments.absolute(p) for p in config['launchers'].values()),
            'Explicit private launcher paths required.', 'invalid_dispatch')
    return config


def installed(stage, descriptors):
    require(isinstance(descriptors, list) and all(isinstance(d, dict) and d.get('path') == d.get('source') for d in descriptors),
            'Service dispatch requires actual installed sources, not test snapshots.', 'skill_blocked')
    return role_skills.snapshots(stage, descriptors)


def unique(rows, predicate):
    matched = [row for row in rows if predicate(row)]
    require(len(matched) <= 1, 'Ambiguous durable stage ownership; reconcile exact claims.', 'claim_conflict')
    return matched[0] if matched else None


def pending(row):
    runtime = row.get('runtime')
    if runtime is None:
        return 'launch', row['assignment_id']
    if runtime.get('status') != 'complete':
        return 'blocked', 'attempt_requires_reconciliation'
    return None


def select(item, rows, config):
    """Choose only an operation; its public seam must freshly validate all evidence."""
    if item.get('planning', {}).get('status') != 'complete':
        return 'blocked', 'same_conversation_executor_unavailable'
    work = item.get('ticket_work')
    if work is None:
        return 'synthesize', None
    if work.get('status') == 'synthesis_pending':
        return 'execute-synthesis', None
    if not work.get('publication_complete'):
        return 'publish-tickets', None
    seed_request = config.get('implementation')
    if seed_request is None:
        return 'blocked', 'implementation_envelope_unavailable'
    seed = unique(rows, lambda row: row['claim_id'] == seed_request['claim_id'])
    if seed is None:
        return 'implementation', None
    wait = pending(seed)
    if wait:
        return wait
    child = unique(rows, lambda row: row['handoff'].get('simplification', {}).get('implementation_assignment') == seed['assignment_id'])
    if child is None:
        return 'simplify', seed['assignment_id']
    wait = pending(child)
    if wait:
        return wait
    axes = {}
    for axis in ('review-standards', 'review-spec'):
        found = unique(rows, lambda row: row['handoff']['stage'] == axis and
                       row['handoff'].get('review', {}).get('pins', {}).get('candidate_assignment') == child['assignment_id'])
        if found is None:
            return axis, child['assignment_id']
        wait = pending(found)
        if wait:
            return wait
        axes[axis] = found['assignment_id']
    return 'evaluate', {'standards_assignment': axes['review-standards'], 'spec_assignment': axes['review-spec']}


def evaluate(database, config, github, axes):
    result = review.evaluate(database, config['project'], config['iteration'], config['operator_id'],
                             axes, github, config['review_config'])
    if not result['advance_allowed']:
        return blocked('review_' + result['status'])
    return {'status': 'review-accepted', 'dispatch_allowed': False, 'blockers': []}


def synthesize(database, item, config, github, *, stop_event=None):
    work = item['ticket_work']
    descriptor = work['request']['skill']
    # to-tickets has no dependencies; source identity is not caller-selectable.
    expected = Path.home() / '.hermes/skills/to-tickets/SKILL.md'
    require(descriptor['path'] == str(expected), 'Load the actual installed to-tickets source.', 'skill_blocked')
    selected = tickets.skill(descriptor)
    path = config['synthesis_config']
    require(path is not None, 'Approved host synthesis executor unavailable.', 'synthesis_executor_unavailable')
    capability = requirement_execution.configuration(database, path, config['operator_id'])
    require(work['inputs'] == tickets.pinned_inputs(database, item, github) and work['skill'] == selected,
            'Synthesis source or current requirements drifted.', 'ticket_conflict')
    # Persist intent BEFORE execution. Unknown outcomes never become an automatic retry.
    require('service_attempt' not in work, 'Reconcile the retained synthesis attempt; no blind retry.', 'execution_uncertain')
    work['service_attempt'] = {'input_digest': work['input_digest'], 'capability': capability}
    checkpoint(database, item)
    produced = requirement_execution.invoke(capability, work,
        cancelled=lambda: (stop_event is not None and stop_event.is_set()) or
                          controls.blocked(database, item['project_id'], item['iteration_id']))
    # Keep raw output even if semantic validation/refreshed admission fails.
    work['service_receipt'] = produced
    from .repositories import save_iteration
    save_iteration(database, item)
    database.commit()
    tickets.validate_execution(work, produced['execution'], produced['result'])
    require(requirement_execution.configuration(database, path, config['operator_id']) == capability and
            tickets.skill(descriptor) == selected, 'Execution authority/source changed during synthesis.', 'execution_required')
    work['host_execution_reference'] = capability['execution_reference']
    checkpoint(database, item)
    return tickets.complete_synthesis(database, item,
        {'assignment_id': work['assignment_id'], 'input_digest': work['input_digest'], 'adaptation': tickets.ADAPTATION,
         'execution': produced['execution'], 'tickets': produced['result']}, github)


def tick(service, github, *, stop_event=None):
    from .service import connect, scope
    if stop_event is not None and stop_event.is_set():
        return blocked('service_stopping')
    launch = None
    publish = None
    with connect(service) as database, state_lock(database):
        path = service['dispatch_config']
        merge.controller_paths(database, path)
        config = validate(publication.read_json(Path(path)), service)
        item = assignments.current(database, *scope(service))
        controls.require_active(database, service['project'], service['iteration'])
        rows = [row for row in assignments.rows(database) if
                row['ticket_scope']['project'] == service['project'] and
                row['ticket_scope']['iteration'] == service['iteration']]
        action, target = select(item, rows, config)
        if action == 'blocked':
            return blocked(target)
        from .restart import before_dispatch
        before_dispatch(database, *scope(service), github, force=True)
        item = assignments.current(database, *scope(service))
        refreshed_rows = [row for row in assignments.rows(database) if
                          row['ticket_scope']['project'] == service['project'] and
                          row['ticket_scope']['iteration'] == service['iteration']]
        if select(item, refreshed_rows, config) != (action, target):
            return blocked('stage_changed_during_reconciliation')
        if stop_event is not None and stop_event.is_set():
            return blocked('service_stopping')
        if action == 'synthesize':
            if config['tickets'] is None:
                return blocked('ticket_synthesis_configuration_unavailable')
            tickets.synthesize(database, item, config['tickets'], github)
        elif action == 'execute-synthesis':
            synthesize(database, item, config, github, stop_event=stop_event)
        elif action == 'publish-tickets':
            # Existing publication revalidates exact effects and never trusts a cursor.
            tickets.publish(database, item, github)
        elif action == 'implementation':
            request = config['implementation']
            installed('implementation', request['skills'])
            tickets.reserve(database, item, github, request['issue']['number'])
            assignments.prepare(database, *scope(service), request, github)
        elif action == 'simplify' or action in {'review-standards', 'review-spec'}:
            role = config['roles'].get(action)
            if role is None:
                return blocked(action + '_configuration_unavailable')
            installed(action, role['skills'])
            if action == 'simplify':
                simplification.prepare(database, *scope(service), {'implementation_assignment': target, **role}, github)
            else:
                review.prepare(database, *scope(service), {'candidate_assignment': target, 'axis': action, **role}, github)
        elif action == 'launch':
            assignment = assignments.inspect(database, *scope(service), target)
            stage = assignment['handoff']['stage']
            descriptors = [{k: v for k, v in s.items() if k != 'instructions'} for s in assignment['handoff']['skills']]
            installed(stage, descriptors)
            require(assignment['handoff']['adaptation'] == role_skills.ADAPTATIONS[stage],
                    'Stage adaptation changed; prepare a fresh reviewed assignment.', 'skill_blocked')
            launcher = config['launchers'].get(stage)
            if launcher is None:
                return blocked(stage + '_launcher_unavailable')
            merge.controller_paths(database, launcher)
            launch = target, launcher
        elif action == 'evaluate':
            result = evaluate(database, config, github, target)
            if result['status'] != 'review-accepted':
                return result
            axis = assignments.inspect(database, *scope(service), target['standards_assignment'])
            candidate = assignments.inspect(database, *scope(service), axis['handoff']['review']['pins']['candidate_assignment'])
            publication_config = config['publication_config']
            if not candidate.get('publication') or candidate['publication']['status'] != 'published':
                if publication_config is None:
                    return blocked('candidate_publication_authority_unavailable')
                publish = candidate['assignment_id'], publication_config
            else:
                if config['merge_config'] is None:
                    return blocked('merge_authority_unavailable')
                authority = merge.load_config(config['merge_config'], service['operator_id'])
                require(authority['candidate_assignment'] == candidate['assignment_id'],
                        'Merge authority targets a different candidate.', 'scope_mismatch')
                journal = candidate.get('merge', {})
                if journal.get('status') == 'delivered':
                    return {'status': 'delivered', 'dispatch_allowed': False, 'blockers': []}
                merge.lifecycle(database, *scope(service), config['merge_config'],
                                'verify-delivery' if journal.get('status') in {'merged', 'incomplete', 'revalidation-required'} else 'merge-candidate')
    if publish is not None:
        if stop_event is not None and stop_event.is_set():
            return blocked('service_stopping')
        # Publication owns its own shared lock and final pause/effect gates.
        with connect(service) as database:
            publication.publish(database, *scope(service), *publish)
    if launch is not None:
        # Launcher takes the shared lock itself, rechecks fresh restart/control,
        # owns signal cancellation, verified checks and mandatory cleanup.
        if stop_event is not None and stop_event.is_set():
            return blocked('service_stopping')
        sandbox.launch(service['state'], *scope(service), *launch, github, stop_event=stop_event)
        with connect(service) as database, state_lock(database):
            observed = assignments.inspect(database, *scope(service), launch[0])
            runtime = observed.get('runtime', {})
            if runtime.get('status') != 'complete' or runtime.get('container_removed') is not True:
                return {**blocked('attempt_requires_reconciliation'), 'stage': action}
    return {'status': 'progress', 'stage': action, 'dispatch_allowed': False, 'blockers': []}
