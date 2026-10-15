"""Exact persisted authorities for resolving effects, never worker admission."""
from dataclasses import dataclass

from . import assignments
from .planning import require


@dataclass(frozen=True)
class EffectResolution:
    project: str
    iteration: str
    operator: str
    kind: str
    target: str
    authority: str
    resolving: bool


def branch_reopen_binding(row):
    return assignments.digest({k: v for k, v in row.items() if k != 'merge_refusal'})


def authority(item, rows, kind, target):
    if kind == 'branch-reopen':
        from . import requirements
        row = next((a for a in rows if a['assignment_id'] == target), None)
        refusal = row.get('merge_refusal', {}) if row else {}
        correction = refusal.get('correction', {})
        config = correction.get('authority', {})
        require(row is not None and not row.get('merge') and refusal.get('code') == 'not_integrated' and
                row['handoff']['stage'] == 'simplify' and
                row['ticket_scope']['project'] == item['project_id'] and
                row['ticket_scope']['iteration'] == item['iteration_id'] and
                config.get('candidate_assignment') == target and
                config.get('operator_id') == item['approval']['operator_id'] and
                config.get('repository') == item['repository'] == row['handoff']['repository'] and
                config.get('repository_id') == item['repository_onboarding']['metadata']['id'] == row['handoff']['repository_id'] and
                config.get('api_base') == item['repository_onboarding']['api_base'] and
                config.get('issue') == row['ticket_scope']['issue_number'] == row['handoff']['issue']['number'] and
                refusal.get('candidate') == config.get('head') and refusal.get('revision') == row['revision'] and
                row['revision'] == requirements.authority(item, config['issue']) and
                config['issue'] not in requirements.held_numbers(item) and
                assignments.digest(row['handoff']) == row['handoff_digest'] and
                correction.get('binding') == branch_reopen_binding(row),
                'Exact current branch-only refusal authority required.', 'scope_mismatch')
        assignments.require_result_active(row)
        return [row, correction]
    if kind == 'merge-reopen':
        row = next((a for a in rows if a['assignment_id'] == target), None)
        journal = row.get('merge', {}) if row else {}
        config = journal.get('authority', {})
        require(row is not None and journal.get('status') in {'pending', 'merged', 'incomplete'} and
                journal.get('close_allowed') is False and
                row['ticket_scope']['project'] == item['project_id'] and
                row['ticket_scope']['iteration'] == item['iteration_id'] and
                config.get('candidate_assignment') == target and
                config.get('operator_id') == item['approval']['operator_id'] and
                config.get('repository') == item['repository'] == row['handoff']['repository'] and
                config.get('repository_id') == item['repository_onboarding']['metadata']['id'] == row['handoff']['repository_id'] and
                config.get('api_base') == item['repository_onboarding']['api_base'] and
                config.get('issue') == row['ticket_scope']['issue_number'] == row['handoff']['issue']['number'] and
                assignments.commit(config.get('head')) and assignments.commit(config.get('baseline')) and
                type(config.get('pr_number')) is int and config['pr_number'] > 0,
                'Exact incomplete merge authority required for corrective reopening.', 'scope_mismatch')
        assert row is not None
        return [row['handoff'], row['revision'], journal]
    if kind == 'delivered-retirement':
        row = next((a for a in rows if a['assignment_id'] == target), None)
        require(row is not None and row.get('merge', {}).get('status') == 'delivered' and
                row['merge'].get('close_allowed') is True and row['merge'].get('failure') is None and
                (item.get('reservation', {}).get('issue_number') == row['handoff']['issue']['number'] or
                 target in item.get('delivered_ticket_retirements', {})) and
                not item.get('main_incident'), 'Exact delivered reservation required.', 'invalid_retirement')
        return [item.get('reservation'), item.get('delivered_ticket_retirements', {}).get(target), row]
    if kind == 'ticket-publication':
        work = item.get('ticket_work')
        require(work is not None and work['assignment_id'] == target and work.get('tickets') and work.get('execution'),
                'Exact completed ticket synthesis required.', 'synthesis_required')
        return [work['assignment_id'], work['inputs'], work['request'], work['tickets'], work['execution']]
    if kind == 'candidate-publication':
        row = next((a for a in rows if a['assignment_id'] == target), None)
        journal = row.get('publication') if row else None
        require(journal is not None and journal.get('status') in {'pending', 'published'},
                'Exact existing candidate intent required.', 'publication_conflict')
        assignments.require_result_active(row)
        return [row['handoff_digest'], row['revision'], row['runtime'], row.get('result_disposition'),
                journal['approval'], journal['candidate'],
                *[row.get(k) for k in ('recovery_disposition', 'restart_disposition',
                                      'requirement_disposition', 'stuck_disposition')]]
    if kind == 'bug-publication':
        from .bugs import assigned
        work = assigned(item, target)
        require(not work.get('requirement_disposition') and work.get('bug') and work.get('execution'),
                'Exact current completed bug synthesis required.', 'synthesis_required')
        from .requirements import authority as requirement_authority
        return [work['assignment_id'], work['inputs'], work['request'], work['bug'], work['execution'],
                work['board'], work.get('scoped_inputs'), work.get('scoped_revisions'),
                [requirement_authority(item, n) for n in work['discovery']['affected']]]
    require(False, 'Unknown effect resolution lane.', 'invalid_restart')


def pinned_authority(database, item, kind, target):
    from .repositories import valid_repository
    onboarding = item.get('repository_onboarding', {})
    identity = onboarding.get('metadata', {}).get('id')
    require(valid_repository(item.get('repository')) and type(identity) is int and identity > 0 and
            isinstance(onboarding.get('api_base'), str) and onboarding['api_base'],
            'Exact registered repository/capability required for effect resolution.', 'scope_mismatch')
    return assignments.digest([item['repository'], identity, onboarding['api_base'], item['approval'],
                               *[item.get(k) for k in ('handoff', 'requirement_changes', 'main_incident',
                                                       'main_recoveries', 'stuck_work')],
                               authority(item, assignments.rows(database), kind, target)])


def bound(database, item, kind, target):
    return EffectResolution(item['project_id'], item['iteration_id'], item['approval']['operator_id'], kind, target,
                            pinned_authority(database, item, kind, target),
                            item.get('restart_reconciliation', {}).get('status') == 'blocked')


def permitted(database, project, iteration, resolution):
    require(type(resolution) is EffectResolution and type(resolution.resolving) is bool,
            'Explicit exact effect authority required.', 'invalid_restart')
    require((resolution.project, resolution.iteration) == (project, iteration),
            'Effect resolution scope changed.', 'scope_mismatch')
    item = assignments.current(database, project, iteration, resolution.operator, require_active=False)
    require(item['status'] == 'active', 'Paused/out-of-iteration effects remain held.', 'iteration_paused')
    require(resolution.authority == pinned_authority(database, item, resolution.kind, resolution.target),
            'Persisted effect authority changed.', 'stale_admission')
    if resolution.kind == 'merge-reopen':
        return {('delivery', 'corrective_reopen')}
    if not resolution.resolving:
        return set()
    if resolution.kind == 'delivered-retirement':
        return {('tracker', 'reservation_conflict'), ('ownership', 'claim_conflict')}
    if resolution.kind == 'ticket-publication':
        return {('tracker', 'publication_uncertain')}
    if resolution.kind == 'candidate-publication':
        return {(resolution.target + ':publication', 'publication_uncertain'),
                (resolution.target + ':publication', 'github_mismatch')}
    if resolution.kind == 'bug-publication':
        return {('tracker', 'publication_uncertain'), ('bug-publication', 'publication_uncertain')}
    return set()
