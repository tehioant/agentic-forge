"""Pure, sanitized projections of retained Forge assignment records.

``project_worker_status(assignment, repository=..., native_blockers=...)`` returns
exactly state, label, reason and blocker. The caller selects the project's raw
role_assignments payloads, including historical rows, and supplies its selected
owner/repository. Optional native_blockers must come from a trusted read-only
native dependency observation for this exact assignment's ticket, never worker
text or handoff URLs. No source reads, controller inspection or liveness checks
are performed here. Render labels verbatim: Running is only a retained flag;
Done means this assignment completed, not that its ticket was delivered.

No authoritative spawned-agent parentage is recorded by the current worker
(which forbids delegation). Accordingly this contract exposes no relationship
field and ignores parent/profile/workflow lineage, even if marked 'verified'.
"""

import re


_ERROR_REASONS = {
    'cancelled': 'Attempt cancelled.',
    'iteration_paused': 'Iteration paused.',
    'controller_lost': 'Controller ownership lost.',
    'time_limit': 'Attempt time limit reached.',
    'worker_failed': 'Worker execution failed.',
    'helper_failed': 'Controller helper failed.',
    'provider_unavailable': 'Approved model access unavailable.',
    'requirements_held': 'Requirements held.',
    'main_frozen': 'Work held for main recovery.',
    'stuck_held': 'Work held for diagnosis.',
    'invalid_result': 'Worker result was not accepted.',
    'recoverable_run_error': 'Attempt failed; recovery required.',
}


def _record(value):
    return value if isinstance(value, dict) else {}


def _blocker(assignment, repository, dependencies):
    handoff = _record(assignment.get('handoff'))
    scope = _record(assignment.get('ticket_scope'))
    if (not isinstance(repository, str) or
            re.fullmatch(r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', repository) is None or
            repository.split('/')[1] in ('.', '..') or
            handoff.get('repository') != repository or
            type(scope.get('issue_number')) is not int or scope['issue_number'] <= 0):
        return None
    if not isinstance(dependencies, (list, tuple)):
        return None
    numbers = []
    for raw in dependencies:
        issue = _record(raw)
        number = issue.get('number')
        if (issue.get('repository_url') == 'https://api.github.com/repos/' + repository and
                type(number) is int and number > 0 and number != scope['issue_number'] and
                type(issue.get('id')) is int and issue['id'] > 0 and
                isinstance(issue.get('node_id'), str) and issue['node_id'] and
                issue.get('state') == 'open' and 'pull_request' not in issue):
            numbers.append(number)
    if not numbers:
        return None
    number = min(numbers)
    return {'number': number, 'url': f'https://github.com/{repository}/issues/{number}'}


def project_worker_status(assignment, *, repository, native_blockers=()):
    """Project raw assignment/runtime/submitted_result records without I/O.

    Missing/malformed evidence stays unknown. A result is successful only with
    correlated assignment/run identities, admitted isolated execution and
    confirmed cleanup. trusted_execution/advance_allowed/close_allowed are not
    completion flags; checks_verified can legitimately be false for diagnosis.
    Arbitrary errors, result work/log text and disposition reasons never escape.
    ``blocker`` is null or a canonical exact-repository issue {number, url}.
    """
    assignment = _record(assignment)
    runtime = _record(assignment.get('runtime'))
    result = _record(assignment.get('submitted_result'))
    disposition = _record(assignment.get('result_disposition'))

    def projected(state, label, reason=None, *, waiting=False):
        blocker = None
        if waiting:
            blocker = _blocker(assignment, repository, native_blockers)
        return {'state': state, 'label': label, 'reason': reason, 'blocker': blocker}

    if not runtime:
        if assignment.get('status') == 'prepared' and 'runtime' not in assignment:
            return projected('waiting', 'Waiting (prepared; not launched)',
                             'Prepared assignment; no recorded worker attempt.', waiting=True)
        return projected('unknown', 'Unknown')

    status = runtime.get('status')
    error = runtime.get('error')
    if status == 'unconfirmed' or error == 'stop_unconfirmed':
        return projected('unconfirmed', 'Unconfirmed', 'Worker cleanup is unconfirmed.')
    if status in ('complete', 'failed', 'stopped') and runtime.get('container_removed') is not True:
        return projected('unconfirmed', 'Unconfirmed', 'Worker cleanup is unconfirmed.')
    reason = _ERROR_REASONS.get(error) if isinstance(error, str) else None
    if status == 'failed':
        return projected('failed', 'Failed (recorded)', reason)
    if status == 'stopped':
        return projected('stopped', 'Stopped (recorded)', reason)
    if not isinstance(runtime.get('run_id'), str) or not runtime['run_id']:
        return projected('unknown', 'Unknown')
    if status == 'running' and runtime.get('container_removed') is not True and error is None:
        return projected('running', 'Running (recorded; not live-verified)')
    if status == 'preparing' and runtime.get('container_removed') is not True and error is None:
        return projected('waiting', 'Waiting (recorded preparation)',
                         'Attempt preparation recorded; worker start unconfirmed.', waiting=True)
    if status != 'complete' or error is not None:
        return projected('unknown', 'Unknown', reason)
    correlated = (isinstance(assignment.get('assignment_id'), str) and bool(assignment['assignment_id']) and
                  result.get('assignment_id') == assignment['assignment_id'] and
                  result.get('run_id') == runtime['run_id'])
    if not correlated or disposition.get('structurally_valid') is not True:
        return projected('unknown', 'Unknown', 'Successful assignment outcome is not confirmed.')
    if result.get('status') == 'blocked':
        return projected('waiting', 'Waiting (blocked result)', 'Worker reported a blocked outcome.', waiting=True)
    if result.get('status') == 'stuck':
        return projected('waiting', 'Waiting (stuck result)', 'Worker reported a stuck outcome.', waiting=True)
    if result.get('status') == 'done' and disposition.get('isolated_execution') is True:
        return projected('done', 'Done (recorded assignment)', 'Assignment completed; ticket delivery is separate.')
    return projected('unknown', 'Unknown', 'Successful assignment outcome is not confirmed.')
