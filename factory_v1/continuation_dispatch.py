"""Finite host-approved execution capacity over the authoritative ticket frontier.

Slots are consumed by durable claims, not polling cursors. All effects still use
existing preparation, execution, independent authorization and delivery guards.
"""
from copy import deepcopy
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

from . import assignments, attention, controls, diagnosis, iteration, merge, publication
from . import recovery, requirements, review, sandbox_policy, simplification, tickets
from .planning import require, text

KINDS = {'review', 'diagnosis', 'publication', 'merge'}
SLOT_FIELDS = {'stage', 'profile', 'skills', 'claim_id', 'launcher'}


def validate(config, service):
    plan = config['continuation']
    require(isinstance(plan, dict) and set(plan) == {'reference', 'slots', 'authorities'} and
            text(plan['reference']) and isinstance(plan['slots'], list) and 0 < len(plan['slots']) <= 1000 and
            isinstance(plan['authorities'], dict) and set(plan['authorities']) == KINDS,
            'Explicit bounded continuation capacity and authority paths required.', 'invalid_dispatch')
    seed = config['implementation']
    require(isinstance(seed, dict) and set(seed) == assignments.REQUEST_FIELDS and seed['candidate'] is None,
            'Continuation requires an explicit fresh-candidate implementation envelope.', 'invalid_dispatch')
    claims, names, homes = set(), set(), set()
    from .service_dispatch import installed
    for slot in plan['slots']:
        require(isinstance(slot, dict) and set(slot) == SLOT_FIELDS and slot['stage'] in assignments.ROLES and
                assignments.absolute(slot['launcher']), 'Exact execution slot required.', 'invalid_dispatch')
        stage = slot['stage']
        # Validate the full literal profile/provider/role and stage closure using
        # the existing contract; candidate/precursors here are validation-only.
        probe = {**seed, **{k: slot[k] for k in ('stage', 'profile', 'skills', 'claim_id')}}
        probe['candidate'] = seed['baseline'] if stage in {'corrections', 'simplify', 'review-standards', 'review-spec'} else None
        probe['preceding'] = [simplification.artifact(n, 'validation-only') for n in sorted(assignments.PRECEDING[stage])]
        probe['capabilities'] = ['read_workspace', 'scratch', 'model'] + (
            ['write_workspace'] if stage in {'implementation', 'corrections', 'simplify', 'repair'} else [])
        assignments.validate(probe)
        installed(stage, slot['skills'])
        require(slot['claim_id'] not in claims and slot['profile']['name'] not in names and
                slot['profile']['home'] not in homes, 'Slots must have distinct fresh identities.', 'invalid_dispatch')
        claims.add(slot['claim_id'])
        names.add(slot['profile']['name'])
        homes.add(slot['profile']['home'])
    for paths in plan['authorities'].values():
        require(isinstance(paths, list) and len(paths) <= 1000 and len(set(paths)) == len(paths) and
                all(assignments.absolute(p) for p in paths), 'Finite explicit private authority paths required.', 'invalid_dispatch')
    return config


def scoped(rows, item):
    return [r for r in rows if (r['ticket_scope']['project'], r['ticket_scope']['iteration'],
                               r['ticket_scope']['repository'], r['ticket_scope']['repository_id']) ==
            (item['project_id'], item['iteration_id'], item['repository'], item['repository_onboarding']['metadata']['id'])]


def one(rows):
    require(len(rows) <= 1, 'Ambiguous stage ancestry; reconcile exact ownership.', 'claim_conflict')
    return rows[0] if rows else None


def action(stage, target=None, **values):
    return {'stage': stage, 'target': target, **values}


def pending(row):
    runtime = row.get('runtime')
    if runtime is None:
        return action('launch', row['assignment_id'])
    if runtime.get('status') != 'complete' or runtime.get('container_removed') is not True:
        return action('blocked', 'attempt_requires_exact_host_retry')
    if row.get('submitted_result', {}).get('status') == 'stuck':
        return action('declare-stuck', row['assignment_id'])
    return None


def select(item, rows, observed):
    """Pure ancestry selection; no configured seed claim or stage cursor is trusted."""
    if item.get('completion') and item['status'] == 'waiting-for-operator':
        return action('wait', item['completion']['report_id'])
    rows = scoped(rows, item)
    frozen = bool(item.get('main_incident') or recovery.pending_registration(item))
    registered = recovery.active(item)
    if frozen and registered is None:
        return action('blocked', 'registered_repair_required')
    allowed_number = recovery.recovery_number(item) if frozen else None
    # Only a real responsible-worker declaration can enter diagnosis. The
    # declaration seam independently verifies run, isolation and attempted fixes.
    for identity, record in sorted(item.get('stuck_work', {}).items()):
        if record['status'] == 'released':
            continue
        prior = one([r for r in rows if r['assignment_id'] == identity])
        if prior is None or (frozen and prior['handoff']['issue']['number'] != allowed_number):
            continue
        if record['status'] == 'blocking':
            return action('declare-stuck', identity)
        if record['status'] == 'revision-required':
            continue  # Existing authorized requirement publication owns revision.
        if record['status'] == 'release-pending':
            return action('apply-direction', identity, event_id=record['event_id'])
        debug = one([r for r in rows if r['assignment_id'] == record.get('diagnosis_assignment')])
        if debug is None:
            return action('diagnosis', identity)
        waiting = pending(debug)
        if waiting:
            return waiting if waiting['stage'] != 'declare-stuck' else action('blocked', 'diagnosis_requires_operator')
        if record['status'] == 'awaiting-direction':
            return action('direction', identity, event_id=record['event_id'])
        return action('request-direction', identity, diagnosis=debug['assignment_id'])
    active_rows = [r for r in rows if not any(r.get(k) for k in
                   ('requirement_disposition', 'recovery_disposition', 'restart_disposition', 'bug_disposition')) and
                   r.get('stuck_disposition') not in {'held', 'superseded'} and
                   r['assignment_id'] not in {identity for v in item.get('delivered_ticket_retirements', {}).values()
                                              for identity in v['proof']['lineage']}]
    if frozen:
        assert registered is not None
    roots = [r for r in active_rows if r['handoff']['stage'] in {'implementation', 'repair'} and
             (not frozen or r['assignment_id'] == registered.get('assignment'))]
    root = one(roots)
    if root is None:
        if frozen:
            assert registered is not None
            if registered.get('assignment'):
                return action('blocked', 'repair_continuation_authority_required')
            return action('repair', allowed_number)
        if observed['foreign_active'] or observed['active']:
            return action('blocked', 'frontier_ownership_conflict')
        eligible = sorted(r['number'] for r in observed['items'] if r['number'] in observed['eligible'] and
                          r['admissible'] and r['contract_valid'] and not r['held'])
        return action('implementation', eligible[0]) if eligible else action('complete')
    # Follow corrective descendants, then restart simplification and BOTH axes.
    seen = set()
    modifier = root
    while True:
        require(modifier['assignment_id'] not in seen, 'Cyclic correction ancestry.', 'claim_conflict')
        seen.add(modifier['assignment_id'])
        waiting = pending(modifier)
        if waiting:
            return waiting
        simplified = one([r for r in active_rows if r['handoff'].get('simplification', {}).get(
            'implementation_assignment') == modifier['assignment_id']])
        if simplified is None:
            return action('simplify', modifier['assignment_id'])
        corrections = one([r for r in active_rows if r['handoff'].get('corrections', {}).get('pins', {}).get(
            'candidate_assignment') == simplified['assignment_id']])
        if corrections is not None:
            modifier = corrections
            continue
        waiting = pending(simplified)
        if waiting:
            return waiting
        route = simplification.findings_route(simplified)
        if route:
            return action(route, simplified['assignment_id'])
        axes = {}
        for stage in ('review-standards', 'review-spec'):
            axis = one([r for r in active_rows if r['handoff']['stage'] == stage and r['handoff'].get(
                'review', {}).get('pins', {}).get('candidate_assignment') == simplified['assignment_id']])
            if axis is None:
                return action(stage, simplified['assignment_id'])
            waiting = pending(axis)
            if waiting:
                return waiting
            axes[stage.replace('review-', '') + '_assignment'] = axis['assignment_id']
        return action('evaluate', simplified['assignment_id'], axes=axes)


def observe(database, item, github):
    """Read-only selection can precede restart, but cannot admit any effect."""
    require(not controls.blocked(database, item['project_id'], item['iteration_id']),
            'Explicit resume required.', 'iteration_paused')
    with closing(sqlite3.connect(':memory:')) as shadow:
        database.backup(shadow)
        copied = deepcopy(item)
        copied.pop('restart_reconciliation', None)
        from .repositories import save_iteration
        save_iteration(shadow, copied)
        shadow.commit()
        result = iteration.frontier(shadow, copied, assignments.ReadOnlyGitHub(github))
    require(not controls.blocked(database, item['project_id'], item['iteration_id']),
            'Explicit resume required.', 'iteration_paused')
    return result


def slot_for(config, rows, stage, commands=None):
    """First compatible UNUSED literal slot; every historical row consumes capacity."""
    from .service_dispatch import installed
    used_claims = {r['claim_id'] for r in rows}
    used_names = {r['handoff']['profile']['name'] for r in rows}
    used_homes = {r['handoff']['profile']['home'] for r in rows}
    for slot in config['continuation']['slots']:
        if slot['stage'] != stage or slot['claim_id'] in used_claims or slot['profile']['name'] in used_names or slot['profile']['home'] in used_homes:
            continue
        if Path(slot['profile']['home']).exists():
            continue
        installed(stage, slot['skills'])
        launcher = sandbox_policy.load_config(slot['launcher'])
        if commands is not None and launcher.get('verification_commands', []) != commands:
            continue
        return slot
    require(False, 'No unused compatible approved execution slot.', 'execution_capacity_unavailable')


def role(slot):
    return {k: slot[k] for k in ('profile', 'skills', 'claim_id')}


def request(config, item, number, slot, github):
    seed = config['implementation']
    require(seed['repository'] == item['repository'] and seed['repository_id'] == item['repository_onboarding']['metadata']['id'],
            'Continuation envelope repository changed.', 'scope_mismatch')
    issue = tickets.gh.read_issue(assignments.ReadOnlyGitHub(github), item['repository'], number)
    pin = {k: issue[k] for k in ('number', 'id', 'node_id')}
    pin['body_sha256'] = hashlib.sha256(issue['body'].encode()).hexdigest()
    baseline = publication.ref_sha(assignments.ReadOnlyGitHub(github).request(
        '/repos/' + item['repository'] + '/git/ref/heads/main'), 'main')
    bounded = {**deepcopy(seed), **role(slot), 'stage': slot['stage'], 'issue': pin,
               'spec_commit': requirements.authority(item, number)['spec']['commit'],
               'baseline': baseline, 'candidate': None}
    if slot['stage'] == 'repair':
        registered = recovery.active(item)
        require(registered is not None and baseline == registered['evidence']['main'] and pin == registered['config']['issue'],
                'Exact registered repair main and issue required.', 'recovery_held')
        bounded['preceding'] = recovery.preceding(registered)
    return bounded


def authority(database, config, kind, predicate):
    """Select an exact host-owned authority, never materialize one from reports."""
    from .readiness_gate import runtime_receipt
    matches = []
    for path in config['continuation']['authorities'][kind]:
        value = runtime_receipt(database, config['project'], config['iteration'], kind, path, missing_ok=True)
        if value is None:
            continue  # Only an approved, never-issued future slot may be absent.
        merge.controller_paths(database, path)
        if value.get('operator_id') == config['operator_id'] and predicate(value):
            matches.append(path)
    require(len(matches) == 1, 'Missing, stale or ambiguous exact ' + kind + ' authority.', kind + '_authority_unavailable')
    return matches[0]


def choose(database, item, rows, observed):
    selected = select(item, rows, observed)
    shadow = deepcopy(item)
    while selected['stage'] == 'direction':
        notification = attention.get_record(database, item, selected['event_id'])
        if notification['decision_response'] is not None:
            return selected
        shadow['stuck_work'].pop(selected['target'])
        other = select(shadow, rows, observed)
        if other['stage'] == 'complete':
            return selected  # Waiting is not milestone completion.
        selected = other
    return selected


def merge_authority(database, config, candidate):
    rows = assignments.rows(database)
    axes = [one([r for r in rows if r['handoff']['stage'] == stage and
                 r['handoff'].get('review', {}).get('pins', {}).get('candidate_assignment') == candidate['assignment_id']])
            for stage in ('review-standards', 'review-spec')]
    builder = one([r for r in rows if r['assignment_id'] ==
                   candidate['handoff']['simplification']['implementation_assignment']])
    require(builder is not None and all(a is not None for a in axes),
            'Complete exact delivery ancestry required.', 'merge_authority_unavailable')
    assert builder is not None and axes[0] is not None and axes[1] is not None
    bindings = [merge.execution_binding(a) for a in [builder, candidate, *axes]]
    return authority(database, config, 'merge', lambda c:
        c.get('candidate_assignment') == candidate['assignment_id'] and
        c.get('standards_assignment') == axes[0]['assignment_id'] and
        c.get('spec_assignment') == axes[1]['assignment_id'] and
        c.get('repository') == candidate['handoff']['repository'] and
        c.get('repository_id') == candidate['handoff']['repository_id'] and
        c.get('issue') == candidate['handoff']['issue']['number'] and
        c.get('baseline') == candidate['handoff']['baseline'] and
        c.get('executions') == bindings and c.get('base') == 'main')


def launcher_for(database, config, row):
    from .service_dispatch import installed
    matches = [s for s in config['continuation']['slots'] if s['claim_id'] == row['claim_id'] and
               s['stage'] == row['handoff']['stage'] and s['profile'] == row['handoff']['profile']]
    require(len(matches) == 1, 'Retained assignment no longer matches its approved slot.', 'execution_capacity_unavailable')
    slot = matches[0]
    require(installed(slot['stage'], slot['skills']) == row['handoff']['skills'],
            'Retained slot instruction closure changed.', 'skill_blocked')
    merge.controller_paths(database, slot['launcher'])
    loaded = sandbox_policy.load_config(slot['launcher'])
    contract = row['handoff'].get('review') or row['handoff'].get('corrections') or row['handoff'].get('simplification')
    require(contract is None or loaded.get('verification_commands', []) == contract['verification_commands'],
            'Launcher checks differ from inherited checks.', 'invalid_launcher')
    return slot['launcher']


def prepare_action(database, scope, config, item, rows, selected, github):
    stage, target = selected['stage'], selected['target']
    commands = None
    if stage in {'simplify', 'review-standards', 'review-spec', 'corrections', 'simplification-corrections'}:
        prior = next(r for r in rows if r['assignment_id'] == target)
        contract = prior['handoff'].get('simplification') or prior['handoff'].get('corrections')
        if contract:
            commands = contract['verification_commands']
        else:
            _, _, receipt = simplification.check_receipt(Path(prior['runtime']['artifacts']), prior['runtime'])
            commands = [r['command'] for r in receipt['records']]
    slot = selected.get('slot') or slot_for(config, rows, 'corrections' if stage == 'simplification-corrections' else stage, commands)
    merge.controller_paths(database, slot['launcher'])
    if stage in {'implementation', 'repair'}:
        bounded = selected.get('request') or request(config, item, target, slot, github)
        tickets.reserve(database, item, github, target)
        return (recovery.prepare if stage == 'repair' else assignments.prepare)(database, *scope, bounded, github)
    if stage == 'simplify':
        return simplification.prepare(database, *scope, {'implementation_assignment': target, **role(slot)}, github)
    if stage in {'review-standards', 'review-spec'}:
        return review.prepare(database, *scope, {'candidate_assignment': target, 'axis': stage, **role(slot)}, github)
    if stage == 'corrections':
        return review.prepare_corrections(database, *scope, {**selected['axes'], **role(slot)}, github)
    if stage == 'simplification-corrections':
        return review.prepare_findings_corrections(database, *scope, {'candidate_assignment': target, **role(slot)}, github)
    if stage == 'diagnosis':
        return diagnosis.prepare(database, *scope, {'stuck_assignment': target, **role(slot)}, github)
    require(False, 'Unsupported continuation preparation.', 'invalid_dispatch')


def retire_delivered(database, scope, config, item, rows, github):
    from . import restart
    delivered = one([r for r in scoped(rows, item) if
        r['handoff']['issue']['number'] == item.get('reservation', {}).get('issue_number') and
        r.get('merge', {}).get('status') == 'delivered'])
    if delivered is None:
        return False
    path = merge_authority(database, config, delivered)
    if item.get('main_incident'):
        registered = recovery.active(item)
        require(registered is not None and delivered['handoff'].get('recovery') == recovery.contract(registered),
                'Only exact registered repair delivery may resolve its freeze.', 'recovery_held')
        restart.before_dispatch(database, *scope, github, force=True,
                                assignment_id=delivered['assignment_id'], verify_recovery=True)
        recovery.verify(database, *scope, path, github)
        item = assignments.current(database, *scope)
    tickets.advance_delivered(database, item, github, path)
    restart.reconcile(database, *scope, github)
    return True


def execute(database, scope, config, item, rows, selected, github):
    """Caller holds state_lock. Return outside-lock operations to the existing tick."""
    stage, target = selected['stage'], selected['target']
    if stage in assignments.ROLES or stage == 'simplification-corrections':
        prepare_action(database, scope, config, item, rows, selected, github)
    elif stage == 'simplification-direction':
        simplification.request_findings_direction(database, *scope, target)
        return {'result': {'status': 'waiting', 'dispatch_allowed': False, 'blockers': ['simplification_operator_revision_required']}}
    elif stage == 'launch':
        row = next(r for r in rows if r['assignment_id'] == target)
        return {'launch': (target, launcher_for(database, config, row))}
    elif stage == 'declare-stuck':
        diagnosis.declare(database, *scope, {'stuck_assignment': target}, github)
    elif stage in {'direction', 'apply-direction'}:
        record = attention.get_record(database, item, selected['event_id'])
        if record['decision_response'] is None:
            return {'result': {'status': 'waiting', 'dispatch_allowed': False, 'blockers': ['operator_direction_required']}}
        diagnosis.apply_direction(database, *scope, {'stuck_assignment': target, 'event_id': selected['event_id']}, github)
    elif stage == 'request-direction':
        axis = next(r for r in rows if r['assignment_id'] == selected['diagnosis'])
        binding = diagnosis.execution_binding(axis)
        path = authority(database, config, 'diagnosis', lambda c: c.get('diagnosis') == binding)
        diagnosis.request_direction(database, *scope, {'stuck_assignment': target}, github, path)
    elif stage == 'evaluate':
        axes = [next(r for r in rows if r['assignment_id'] == selected['axes'][k]) for k in ('standards_assignment', 'spec_assignment')]
        bindings = [review.execution_binding(a) for a in axes]
        path = authority(database, config, 'review', lambda c: c.get('axes') == bindings)
        verdict = review.evaluate(database, *scope, selected['axes'], github, path)
        if verdict['status'] == 'corrections':
            prepare_action(database, scope, config, item, rows, {**selected, 'stage': 'corrections'}, github)
        elif verdict['advance_allowed']:
            candidate = next(r for r in rows if r['assignment_id'] == target)
            if candidate.get('publication', {}).get('status') != 'published':
                binding = publication.binding(candidate)
                path = authority(database, config, 'publication', lambda c: c.get('binding') == binding)
                return {'publish': (target, path)}
            path = merge_authority(database, config, candidate)
            journal = candidate.get('merge', {})
            if journal.get('status') == 'delivered':
                if candidate['handoff'].get('recovery'):
                    recovery.verify(database, *scope, path, github)
                else:
                    tickets.advance_delivered(database, item, github, path)
            else:
                merge.lifecycle(database, *scope, path, 'verify-delivery' if journal.get('status') in
                                {'merged', 'incomplete', 'revalidation-required'} else 'merge-candidate')
        else:
            return {'result': {'status': 'blocked', 'dispatch_allowed': False, 'blockers': ['review_' + verdict['status']]}}
    elif stage == 'complete':
        observed = observe(database, item, github)
        required, _, _ = iteration.required_items(item, observed, github)
        paths = []
        for row in required:
            candidates = [r for r in rows if r['handoff']['issue']['number'] == row['number'] and r.get('merge', {}).get('status') == 'delivered']
            candidate = one(candidates)
            require(candidate is not None, 'Required delivery authority unavailable.', 'iteration_incomplete')
            paths.append(merge_authority(database, config, candidate))
        completed = iteration.complete(database, *scope, {'merge_configs': paths}, github)
        readback = assignments.current(database, *scope, require_active=False)
        require(readback.get('completion') == completed and readback['status'] == 'waiting-for-operator',
                'Completion report did not read back.', 'completion_race')
        return {'result': {'status': 'await_next_interview', 'report_id': completed['report_id'], 'dispatch_allowed': False, 'blockers': []}}
    return {}
