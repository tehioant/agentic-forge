"""Deterministic admitted stage routing over existing controller lifecycle contracts.

No shell plugin registry, agent polling, worker-authored authority or parallel claims.
Legacy fixed envelopes and finite continuation use the authoritative frontier;
a missing host capability is a hold, never permission to fabricate or fall back.
"""
from pathlib import Path
from contextlib import contextmanager
import os
import signal
import subprocess
import sys
import time

from . import assignments, controls, merge, publication, requirement_execution, review
from . import role_skills, simplification, tickets
from .planning import checkpoint, require, text
from .repositories import RepositoryError, state_lock

FIELDS = {'operator_id', 'project', 'iteration', 'reference', 'implementation', 'roles', 'launchers',
          'tickets', 'synthesis_config', 'review_config', 'publication_config', 'merge_config'}
ROLES = {'simplify', 'review-standards', 'review-spec'}


def blocked(reason):
    return {'status': 'blocked', 'dispatch_allowed': False, 'blockers': [reason]}


def validate(config, service):
    require(isinstance(config, dict) and FIELDS <= set(config) <= FIELDS | {'planning_config', 'continuation'} and text(config['reference']) and
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
    require(config.get('planning_config') is None or assignments.absolute(config['planning_config']),
            'Use an explicit private native wakeup approval or null.', 'invalid_dispatch')
    require(all(assignments.absolute(p) for p in config['launchers'].values()),
            'Explicit private launcher paths required.', 'invalid_dispatch')
    if 'continuation' in config:
        from .continuation_dispatch import validate as validate_continuation
        validate_continuation(config, service)
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
        return 'blocked', 'origin_conversation_pending'
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
    @contextmanager
    def spawn_admission():
        with controls.admission(database, item['project_id'], item['iteration_id']):
            require(stop_event is None or not stop_event.is_set(), 'Service is stopping.', 'service_stopping')
            yield
    produced = requirement_execution.invoke(capability, work,
        cancelled=lambda: (stop_event is not None and stop_event.is_set()) or
                          controls.blocked(database, item['project_id'], item['iteration_id']),
        admission=spawn_admission)
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


def _stop_child(process):
    for signum, timeout in ((signal.SIGTERM, 35), (signal.SIGKILL, 5)):
        try:
            os.killpg(process.pid, signum)
        except OSError:
            pass  # Still escalate and reap; a signal is not join proof.
        try:
            process.wait(timeout=timeout)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise RepositoryError('execution_uncertain', 'Owned launcher could not be joined; reconcile before further dispatch.')


def run_assignment(service, assignment_id, launcher, *, stop_event=None):
    from .service import connect, scope
    try:
        _launch_child(service, assignment_id, launcher, stop_event=stop_event)
    except (RepositoryError, OSError, subprocess.SubprocessError):
        # Even pre-runtime/unknown child failures cannot become automatic retries.
        with connect(service) as database:
            controls.request(database, *scope(service), 'pause', 'service-launch-reconciliation:' + assignment_id)
        raise


def _launch_child(service, assignment_id, launcher, *, stop_event=None):
    """Own a bounded public CLI child; its exit is never completion evidence."""
    from .service import connect, scope
    argv = [sys.executable, '-m', 'factory_v1', '--state', service['state'],
            '--operator-id', service['operator_id'], 'launch-assignment',
            '--project', service['project'], '--iteration', service['iteration'],
            '--assignment', assignment_id, '--launcher-config', launcher,
            '--api-base', service['api_base']]
    # Same host Docker routing allowlist as Docker, plus a scoped temp root.
    env = {key: os.environ[key] for key in
           ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'XDG_RUNTIME_DIR', 'TMPDIR') if key in os.environ}
    with connect(service) as database, controls.admission(database, *scope(service)[:2],
            selection={'assignment_id': assignment_id, 'run_id': None, 'launcher': launcher}):
        require(stop_event is None or not stop_event.is_set(), 'Service is stopping.', 'service_stopping')
        try:
            process = subprocess.Popen(argv, cwd=Path(__file__).parent.parent, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except OSError as error:
            raise RepositoryError('execution_uncertain', 'Launcher startup failed; inspect before retry.') from error
    joined = False
    # Public launcher permits at most 3600 worker seconds; allow bounded setup/cleanup.
    deadline = time.monotonic() + 3900
    try:
        while True:
            with connect(service) as database:
                controls.require_active(database, *scope(service)[:2])
            require(stop_event is None or not stop_event.is_set(), 'Service is stopping.', 'service_stopping')
            require(time.monotonic() < deadline, 'Owned launcher exceeded its bound.', 'execution_uncertain')
            try:
                code = process.wait(timeout=.1)
                joined = True
                break
            except subprocess.TimeoutExpired:
                continue
        require(code == 0, 'Launcher failed; reconcile the durable attempt.', 'execution_uncertain')
    finally:
        if not joined:
            _stop_child(process)


def tick(service, github, *, stop_event=None):
    if stop_event is not None and stop_event.is_set():
        return blocked('service_stopping')
    from .service import connect, scope
    from .readiness_gate import check, retirement_resolution
    with connect(service) as database:
        check(database, *scope(service)[:2], operation='effect', resolution=retirement_resolution(database, service))
    # Keep legacy fixed-envelope callers unchanged; continuation owns no second
    # executor and returns to the same public launcher/publication seams.
    with connect(service) as database:
        path = service['dispatch_config']
        merge.controller_paths(database, path)
        config = validate(publication.read_json(Path(path)), service)
    if 'continuation' in config:
        with connect(service) as database:
            item = assignments.current(database, *scope(service), require_active=False)
        planning_ready = (config.get('planning_config') is None or
                          item.get('planning', {}).get('native_wakeup', {}).get('status') == 'consumed')
        if planning_ready and (item.get('completion') or (item.get('planning', {}).get('status') == 'complete' and
                                      item.get('ticket_work', {}).get('publication_complete'))):
            return continuation_tick(service, github, stop_event=stop_event)
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
        if config.get('planning_config') is not None and (item.get('planning', {}).get('status') != 'complete' or
                item.get('planning', {}).get('native_wakeup', {}).get('status') != 'consumed'):
            from .planning_wakeup import supervise
            status = supervise(database, item, config['planning_config'], github, stop_event=stop_event)
            return {'status': status, 'stage': 'planning-wakeup', 'dispatch_allowed': False,
                    'blockers': [] if status == 'consumed' else ['origin_conversation_pending']}
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
        run_assignment(service, *launch, stop_event=stop_event)
        with connect(service) as database, state_lock(database):
            observed = assignments.inspect(database, *scope(service), launch[0])
            runtime = observed.get('runtime', {})
            if runtime.get('status') != 'complete' or runtime.get('container_removed') is not True:
                return {**blocked('attempt_requires_reconciliation'), 'stage': action}
    return {'status': 'progress', 'stage': action, 'dispatch_allowed': False, 'blockers': []}


def continuation_tick(service, github, *, stop_event=None):
    from .service import connect, scope
    from .readiness_gate import check, retirement_resolution
    with connect(service) as database:
        check(database, *scope(service)[:2], operation='effect', resolution=retirement_resolution(database, service))
    from . import continuation_dispatch as continuation, recovery, restart
    if stop_event is not None and stop_event.is_set():
        return blocked('service_stopping')
    with connect(service) as database, state_lock(database):
        path = service['dispatch_config']
        merge.controller_paths(database, path)
        config = validate(publication.read_json(Path(path)), service)
        item = assignments.current(database, *scope(service), require_active=False)
        require(not controls.blocked(database, *scope(service)[:2]), 'Explicit resume required.', 'iteration_paused')
        if item.get('completion') and item['status'] == 'waiting-for-operator':
            return {'status': 'await_next_interview', 'report_id': item['completion']['report_id'],
                    'dispatch_allowed': False, 'blockers': []}
        require(item['status'] == 'active', 'Iteration is not active.', 'iteration_paused')
        rows = assignments.rows(database)
        # Retire the exact delivered reservation BEFORE restart/health/frontier
        # admission. Otherwise restart can persist the old reservation as a hold.
        if continuation.retire_delivered(database, scope(service), config, item, rows, github):
            return {'status': 'progress', 'stage': 'retire-delivered', 'dispatch_allowed': False, 'blockers': []}
        # Planning remains the existing fixed public seam, not another backlog.
        if item.get('planning', {}).get('status') != 'complete' or not item.get('ticket_work', {}).get('publication_complete'):
            return blocked('planning_or_ticket_publication_pending')
        observed = continuation.observe(database, item, github)
        selected = continuation.choose(database, item, rows, observed)
        if selected['stage'] == 'blocked':
            return blocked(selected['target'])
        stage, target = selected['stage'], selected['target']
        if stage in {'implementation', 'repair'}:
            slot = continuation.slot_for(config, rows, stage)
            selected.update(slot=slot, request=continuation.request(config, item, target, slot, github))
            context = {'request': selected['request']}
        elif stage in {'complete', 'wait'}:
            context = {}
        else:
            ancestor = next(r for r in rows if r['assignment_id'] == target)
            context = {'assignment_id': target} if stage == 'launch' else {'request': {
                **ancestor['handoff'], 'stage': 'diagnosis' if stage in {
                    'diagnosis', 'declare-stuck', 'direction', 'apply-direction', 'request-direction'} else stage}}
        if recovery.active(item):
            restart.require_reconciled(database, *scope(service)[:2], allow_recovery=True)
        restart.before_dispatch(database, *scope(service), github, force=True, **context)
        fresh = assignments.current(database, *scope(service))
        fresh_rows = assignments.rows(database)
        again = continuation.choose(database, fresh, fresh_rows, continuation.observe(database, fresh, github))
        if stage in {'implementation', 'repair'} and again['stage'] == stage:
            slot = continuation.slot_for(config, fresh_rows, stage)
            again.update(slot=slot, request=continuation.request(config, fresh, again['target'], slot, github))
        require(again == selected and validate(publication.read_json(Path(path)), service) == config,
                'Continuation scope or capacity changed during reconciliation.', 'stale_admission')
        require(stop_event is None or not stop_event.is_set(), 'Service is stopping.', 'service_stopping')
        effect = continuation.execute(database, scope(service), config, fresh, fresh_rows, selected, github)
    if effect.get('result'):
        return effect['result']
    if effect.get('publish'):
        require(stop_event is None or not stop_event.is_set(), 'Service is stopping.', 'service_stopping')
        with connect(service) as database:
            publication.publish(database, *scope(service), *effect['publish'])
    if effect.get('launch'):
        require(stop_event is None or not stop_event.is_set(), 'Service is stopping.', 'service_stopping')
        run_assignment(service, *effect['launch'], stop_event=stop_event)
        with connect(service) as database:
            row = assignments.inspect(database, *scope(service), effect['launch'][0])
            require(row.get('runtime', {}).get('status') == 'complete' and
                    row['runtime'].get('container_removed') is True,
                    'Attempt completion/removal requires reconciliation.', 'execution_uncertain')
    return {'status': 'progress', 'stage': stage, 'dispatch_allowed': False, 'blockers': []}
