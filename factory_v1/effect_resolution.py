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


def authority(item, rows, kind, target):
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
                            pinned_authority(database, item, kind, target))


def permitted(database, project, iteration, resolution):
    require(type(resolution) is EffectResolution, 'Explicit exact effect authority required.', 'invalid_restart')
    require((resolution.project, resolution.iteration) == (project, iteration),
            'Effect resolution scope changed.', 'scope_mismatch')
    item = assignments.current(database, project, iteration, resolution.operator, require_active=False)
    require(item['status'] == 'active', 'Paused/out-of-iteration effects remain held.', 'iteration_paused')
    require(resolution.authority == pinned_authority(database, item, resolution.kind, resolution.target),
            'Persisted effect authority changed.', 'stale_admission')
    if resolution.kind == 'ticket-publication':
        return {('tracker', 'publication_uncertain')}
    if resolution.kind == 'candidate-publication':
        return {(resolution.target + ':publication', 'publication_uncertain'),
                (resolution.target + ':publication', 'github_mismatch')}
    if resolution.kind == 'bug-publication':
        return {('tracker', 'publication_uncertain'), ('bug-publication', 'publication_uncertain')}
    return set()
