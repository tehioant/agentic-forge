"""Scoped operator-owned requirement revisions; obsolete evidence stays inspectable."""
import copy
import hashlib
import json
import re

from . import assignments, tickets
from .planning import require, text

ADAPTATION = 'factory-requirement-revision-v1:agreed-direction;affected-only;explicit-resume'
INSTRUCTIONS = '''Run the selected installed to-spec, then to-tickets, on the operator's
resolved direction and exact old documents/tickets. Preserve prior decisions and useful
unchanged requirements. Only the affected issue set is executable synthesis; retain its
existing identities and blocking graph. Do not choose product direction, relax requirements,
expand the milestone, or synthesize unrelated work. Load selected instructions, inputs and
this adaptation in the executing context and retain tool references and observable work.
Controlled host publication versions documents and updates exact affected issues. Publication
is not resumption. Missing decisions, skill execution or readback keep affected work held.'''


def held_numbers(item):
    return {n for r in item.get('requirement_changes', {}).values()
            if r['kind'] == 'material' and r['status'] != 'resumed' for n in r['affected']}


def authority(item, number):
    work = item.get('ticket_work', {})
    return item.get('requirement_authority', {}).get(str(number), {
        'spec': item.get('handoff', {}), 'ticket_assignment': work.get('assignment_id'),
        'ticket_inputs': work.get('input_digest')})


def inputs(item, number):
    pin = authority(item, number)
    if pin['spec'] == item.get('handoff', {}):
        return item.get('ticket_work', {}).get('inputs', {})
    return item['requirement_changes'][pin['ticket_assignment']]['new_inputs']


def revalidate_inputs(database, item, github, number):
    expected = inputs(item, number)
    shadow = copy.deepcopy(item)
    shadow['handoff'] = expected['handoff']
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(':memory:')) as observation:
        database.backup(observation)
        require(tickets.pinned_inputs(observation, shadow, github) == expected,
                'Current scoped requirement publication changed.', 'stale_requirements')
    return expected


def released_claim(database, assignment):
    if not assignment.get('requirement_disposition'):
        return False
    scope = assignment['ticket_scope']
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                           (scope['project'], scope['iteration'])).fetchone()
    item = json.loads(row[0]) if row else {}
    record = item.get('requirement_changes', {}).get(assignment['requirement_disposition']['change_id'])
    runtime = assignment.get('runtime')
    return bool(record and record.get('blocks_verified') and (not runtime or
                runtime.get('status') in {'complete', 'failed', 'stopped'} and runtime.get('container_removed') is True))


def direction(value, item, operator):
    require(isinstance(value, dict) and set(value) == {'operator_id', 'origin', 'reference', 'content'} and
            value['operator_id'] == operator and value['origin'] == item['origin'] and
            text(value['reference']) and text(value['content']),
            'Exact originating operator direction is required; worker choices are not authority.', 'approval_required')


def record_for(database, project, iteration, operator, request):
    item = assignments.current(database, project, iteration, operator)
    require(isinstance(request, dict) and text(request.get('change_id')), 'Name the exact change.', 'invalid_change')
    record = item.get('requirement_changes', {}).get(request['change_id'])
    require(record is not None, 'Record the change before resolving or resuming it.', 'not_found')
    return item, record


def issue_pin(issue):
    return {k: issue[k] for k in ('number', 'id', 'node_id')} | {
        'body_sha256': hashlib.sha256(issue['body'].encode()).hexdigest()}


def observations(database, item, github):
    item = tickets.frontier(database, item, github)
    rows = {r['number']: r for r in item['ticket_work']['frontier']['items']}
    issues = {n: tickets.gh.read_issue(github, item['repository'], n) for n in rows}
    return item, rows, issues


def validity(database, item, github, numbers):
    """Explicit checks, not a prose edit, permit retention of unchanged evidence."""
    checked = []
    for number in sorted(numbers):
        revalidate_inputs(database, item, github, number)
        issue = tickets.gh.read_issue(github, item['repository'], number)
        for assignment in assignments.rows(database):
            scope = assignment['ticket_scope']
            if (scope['project'], scope['iteration'], scope['issue_number']) != (item['project_id'], item['iteration_id'], number):
                continue
            if assignment.get('requirement_disposition'):
                continue
            assignments.inspect(database, scope['project'], scope['iteration'], item['approval']['operator_id'], assignment['assignment_id'])
            require(assignment['handoff']['issue'] == issue_pin(issue),
                    'Unchanged work has stale issue evidence.', 'stale_requirements')
            from .sandbox import original
            require(assignments.validate(original(assignment)) == assignment['handoff']['skills'],
                    'Retained skill/dependency bytes changed.', 'skill_blocked')
            runtime = assignment.get('runtime')
            if runtime and runtime.get('status') == 'complete':
                from .sandbox_source import manifest, physical
                root = physical(runtime['artifacts'], directory=True)
                require(assignments.digest(manifest(root / 'workspace')) == runtime.get('candidate_sha256') and
                        assignments.digest(manifest(root / 'inputs/baseline')) == runtime.get('baseline_sha256'),
                        'Retained candidate/check bytes changed.', 'stale_assignment')
                from .publication import read_json
                require(read_json(root / 'scratch/result.json') == assignment.get('submitted_result'),
                        'Retained completion output changed.', 'stale_assignment')
                if assignment.get('result_disposition', {}).get('checks_verified'):
                    require(hashlib.sha256(physical(root / 'controller-checks.json').read_bytes()).hexdigest() == runtime.get('checks_sha256'),
                            'Retained check evidence changed.', 'stale_assignment')
                if assignment.get('review_authorization'):
                    from .review import execution_binding
                    require(execution_binding(assignment) in assignment['review_authorization']['axes'],
                            'Retained independent review execution changed.', 'stale_assignment')
            checked.append({'assignment_id': assignment['assignment_id'], 'revision': assignment['revision'],
                            'evidence_digest': assignments.digest(assignment), 'checks': ['issue', 'requirements', 'skills', 'candidate']})
        checked.append({'issue': issue_pin(issue), 'revision': authority(item, number), 'checks': ['exact-publication-readback']})
    return checked


def checkpoint(database, item, *, receipt=False):
    if receipt:
        fresh = assignments.current(database, item['project_id'], item['iteration_id'],
                                    item['approval']['operator_id'], require_active=False)
        for key, record in item.get('requirement_changes', {}).items():
            target = fresh.get('requirement_changes', {}).get(key)
            require(target is not None and target.get('request') == record.get('request'),
                    'Revision receipt authority changed.', 'stale_admission')
            assert target is not None
            for stage, produced in record.get('execution_receipts', {}).items():
                attempt = record.get('attempts', {}).get(stage)
                require(isinstance(record.get('request'), dict) and isinstance(attempt, dict) and
                        set(attempt) == {'input_digest', 'capability'} and
                        attempt == target.get('attempts', {}).get(stage),
                        'Revision receipt stage attempt changed.', 'stale_admission')
                receipts = target.setdefault('execution_receipts', {})
                require(stage not in receipts or receipts[stage] == produced,
                        'Revision stage receipt conflicts with retained output.', 'stale_admission')
                receipts[stage] = produced
            for field in ('tree', 'parent', 'commit', 'issue_identity', 'issue_number'):
                if field in record.get('publication', {}):
                    target.setdefault('publication', {})[field] = record['publication'][field]
            target.setdefault('updated_issues', {}).update(record.get('updated_issues', {}))
        tickets.checkpoint(database, fresh)
    else:
        tickets.effect_checkpoint(database, item)


def sync_blocks(database, item, record, github, release=False):
    from .tracker_holds import option as hold_option, representation
    board = tickets.verified_board(item['ticket_work'], github, item['repository'], item['iteration_id'])
    other_holds = held_numbers(item) - set(record['affected'])
    from .diagnosis import held_numbers as stuck_holds
    other_holds |= stuck_holds(item)
    for number in record['affected']:
        issue = tickets.gh.read_issue(github, item['repository'], number)
        expected = record.get('updated_issues', {}).get(str(number), record['issues'][str(number)])
        require(issue_pin(issue) == expected, 'Affected identity or body changed.', 'stale_requirements')
        if issue['state'] != 'open':
            require(not release, 'Revised work closed before revalidation.', 'stale_requirements')
            tickets.effect_check(database, item)
            github.request('/repos/' + item['repository'] + '/issues/' + str(number), {'state': 'open'}, 'PATCH')
            issue = tickets.gh.read_issue(github, item['repository'], number)
            require(issue['state'] == 'open' and issue_pin(issue) == expected, 'Exact reopened work did not read back.', 'publication_mismatch')
        member = tickets.gh.membership(github, board, item['repository'], issue)
        require(member is not None and member['id'] == record['memberships'][str(number)],
                'Affected work left or replaced its pinned membership.', 'scope_mismatch')
        assert member is not None
        desired = 'blocked'
        if release and number not in other_holds:
            previous = record['previous_statuses'][str(number)]
            if previous in {'ready', 'active'}:
                desired = 'ready'
            elif previous == 'deferred':
                desired = 'deferred'

        option = hold_option(item['ticket_work'], board, desired)
        if release:
            blocked = hold_option(item['ticket_work'], board, 'blocked')
            require(member['status'] in {blocked, option},
                    'External progress changed independently of the requirement release.', 'publication_mismatch')
        if member['status'] != option:
            tickets.effect_check(database, item)
            tickets.gh.set_field(github, board['project_id'], member['id'], board['status']['id'], option)
        member = tickets.gh.membership(github, board, item['repository'], issue)
        require(member is not None and member['id'] == record['memberships'][str(number)] and member['status'] == option,
                'Exact revision hold/resume status did not read back.', 'publication_mismatch')
    record['hold_representation'] = representation(item['ticket_work'])
    record['blocks_verified'] = True
    checkpoint(database, item)
    return item


def record_change(database, project, iteration, operator, request, github):
    item = assignments.current(database, project, iteration, operator)
    fields = {'change_id', 'kind', 'direction', 'requirements', 'issues', 'old_revision'}
    require(isinstance(request, dict) and set(request) in (fields, fields | {'stuck_assignment'}) and
            isinstance(request['change_id'], str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', request['change_id']) and
            request['kind'] in {'minor', 'material'} and isinstance(request['issues'], list) and request['issues'] and
            all(type(n) is int and n > 0 for n in request['issues']) and len(set(request['issues'])) == len(request['issues']) and
            isinstance(request['requirements'], list) and request['requirements'] and all(text(r) for r in request['requirements']),
            'Provide exact change identity, classification, requirements, issues and old revision.', 'invalid_change')
    direction(request['direction'], item, operator)
    old = item.get('requirement_changes', {}).get(request['change_id'])
    if old:
        require(old['request'] == request, 'A change identity cannot be repurposed.', 'change_conflict')
        return sync_blocks(database, item, old, github) if old['status'] == 'held' else item
    item, rows, issues = observations(database, item, github)
    require(set(request['issues']) <= set(rows), 'Affected work must belong to the current authoritative iteration.', 'scope_mismatch')
    require(request['old_revision'] == {str(n): authority(item, n) for n in request['issues']},
            'Pin each affected issue\'s exact old revision.', 'stale_requirements')
    affected = set(request['issues'])
    if request['kind'] == 'material':
        while True:
            downstream = {n for n, r in rows.items() if affected.intersection(r['blockers'])}
            if downstream <= affected:
                break
            affected |= downstream
        require(not affected.intersection(held_numbers(item)), 'Resolve overlapping requirement changes first.', 'change_conflict')
    else:
        require(not affected.intersection(held_numbers(item)), 'A clarification cannot release material holds.', 'change_conflict')
    for number in affected:
        scoped = inputs(item, number)
        tickets.validate_contract('Affected requirement change', request['direction']['content'], ['Preserve agreed requirements'],
            [{'spec': scoped['documents']['milestone.md']['url'], 'requirement': r} for r in request['requirements']], scoped)
    stuck_revision = None
    if 'stuck_assignment' in request:
        from . import attention, diagnosis
        require(request['kind'] == 'material' and text(request['stuck_assignment']), 'Only material revisions can bind stuck work.', 'invalid_change')
        stuck = item.get('stuck_work', {}).get(request['stuck_assignment'])
        require(stuck is not None and stuck['status'] == 'revision-required' and set(stuck['affected']) <= affected,
                'Bind the exact approved revise decision and all its affected work.', 'decision_required')
        assert stuck is not None
        notification = attention.get_record(database, item, stuck['event_id'])
        attention.validate_response(stuck['applied_response'], notification, operator)
        response = stuck['applied_response']
        require(response == notification['decision_response'] and response['reference'] == request['direction']['reference'] and
                stuck['direction']['action'] == 'revise' and stuck['direction']['direction'] == request['direction']['content'],
                'A revision must retain the actual correlated Antoine direction.', 'decision_required')
        axis = assignments.inspect(database, project, iteration, operator, stuck['diagnosis_assignment'])
        from .sandbox import original
        _, _, _, _, _, pins = diagnosis.source(database, project, iteration, operator, request['stuck_assignment'])
        require(axis['runtime']['status'] == 'complete' and axis['runtime']['container_removed'] is True and
                axis['result_disposition']['isolated_execution'] is True and axis['handoff']['diagnosis']['pins'] == pins and
                assignments.validate(original(axis)) == axis['handoff']['skills'] and
                diagnosis.validate_result(axis, axis['submitted_result']) == axis['diagnosis_result'] and
                stuck['authorization']['diagnosis'] == diagnosis.execution_binding(axis),
                'Only the original completed independently authorized diagnosis may bind a revision.', 'execution_required')
        stuck_revision = {'assignment_id': request['stuck_assignment'], 'event_id': stuck['event_id'],
                          'applied_response': response, 'affected': stuck['affected'], 'authorization': stuck['authorization']}
    preserved = validity(database, item, github, set(rows) - affected if request['kind'] == 'material' else set(rows))
    previous_statuses = {str(n): rows[n]['status'] for n in affected}
    for number in affected:
        holds = [r for r in item.get('stuck_work', {}).values() if r['status'] != 'released' and number in r['affected']]
        if holds and rows[number]['status'] == 'blocked':
            previous_statuses[str(number)] = min(holds, key=lambda r: r['sequence'])['previous_statuses'][str(number)]
    record = {'request': request, 'kind': request['kind'], 'status': 'held' if request['kind'] == 'material' else 'clarified',
              'affected': sorted(affected), 'requirements': request['requirements'], 'direction': request['direction'],
              'issues': {str(n): issue_pin(issues[n]) for n in affected},
              'old_issues': {str(n): copy.deepcopy(issues[n]) for n in affected},
              'old_revision': {str(n): authority(item, n) for n in affected}, 'new_revision': None,
              'previous_statuses': previous_statuses,
              'memberships': {str(n): rows[n]['membership_id'] for n in affected},
              'blockers': {str(n): rows[n]['blockers'] for n in affected},
              'dependency_pins': {str(n): sorted([{k: d[k] for k in ('number', 'id', 'node_id')}
                  for d in tickets.gh.dependencies(github, item['repository'], n)], key=lambda d: d['number']) for n in affected},
              'preserved_evidence': preserved, 'blocks_verified': False, 'stuck_revision': stuck_revision}
    item.setdefault('requirement_changes', {})[request['change_id']] = record
    record['retired_assignments'] = []
    if request['kind'] == 'material':
        for assignment in assignments.rows(database):
            scope = assignment['ticket_scope']
            if (scope['project'], scope['iteration']) != (project, iteration) or scope['issue_number'] not in affected:
                continue
            record['retired_assignments'].append(assignment['assignment_id'])
            if assignment.get('requirement_disposition'):
                assignment.setdefault('requirement_disposition_history', []).append(assignment['requirement_disposition'])
            assignment['requirement_disposition'] = {'change_id': request['change_id'], 'status': 'obsolete',
                'invalidated': ['candidate', 'skill-execution', 'review', 'checks', 'completion'], 'revision': assignment['revision']}
            assignment.update(assignment_ready=False, launchable=False)
            if assignment.get('result_disposition'):
                assignment['result_disposition'].update(advance_allowed=False, close_allowed=False, requirements_valid=False)
            if assignment.get('merge'):
                assignment['requirement_disposition']['prior_completion'] = copy.deepcopy(assignment['merge'])
                assignment['merge'].update(status='incomplete' if assignment['merge'].get('integrated_evidence') else 'pending',
                    close_allowed=False, failure={'code': 'stale_requirements', 'message': 'Material revision invalidated delivery evidence.'})
            database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                             (json.dumps(assignment, sort_keys=True), assignment['assignment_id']))
        for work in item.get('bug_work', {}).values():
            if affected.intersection(work['discovery']['affected'] + [work.get('identity', {}).get('number')]):
                work['requirement_disposition'] = {'change_id': request['change_id'], 'status': 'obsolete',
                    'prior_status': work['status'], 'invalidated': ['skill-execution', 'completion']}
                work['status'] = 'obsolete'
        if item.get('reservation', {}).get('issue_number') in affected:
            item.setdefault('reservation_history', []).append(item.pop('reservation'))
    else:
        record['new_revision'] = copy.deepcopy(record['old_revision'])
    checkpoint(database, item)  # Hold and invalidation are durable before tracker mutations.
    return sync_blocks(database, item, record, github) if request['kind'] == 'material' else item


def resolve_change(database, project, iteration, operator, request, github):
    item, record = record_for(database, project, iteration, operator, request)
    require(set(request) == {'change_id', 'direction', 'old_revision'}, 'Provide correlated agreed direction.', 'invalid_change')
    direction(request['direction'], item, operator)
    require(record['kind'] == 'material' and record['old_revision'] == request['old_revision'],
            'Resolution must bind the exact held old revisions.', 'stale_requirements')
    require(record.get('resolution', request['direction']) == request['direction'], 'Resolution cannot silently change.', 'change_conflict')
    if record.get('stuck_revision'):
        from .diagnosis import operator_direction
        require(operator_direction(record['stuck_revision']['applied_response']['response'])['direction'] == request['direction']['content'],
                'Do not replace an approved stuck-work product direction implicitly.', 'decision_required')
    if record['status'] == 'held':
        sync_blocks(database, item, record, github)
        record.update(status='resolved', resolution=request['direction'])
        checkpoint(database, item)
    return item


def resume_change(database, project, iteration, operator, request, github):
    item, record = record_for(database, project, iteration, operator, request)
    require(set(request) == {'change_id', 'direction', 'new_revision'}, 'Explicit correlated resume required.', 'invalid_change')
    direction(request['direction'], item, operator)
    require(record['status'] in {'published', 'resume-pending', 'resumed'} and request['new_revision'] == record['new_revision'],
            'Only exact versioned/read-back synthesis may resume.', 'publication_required')
    require(record.get('resume_direction', request['direction']) == request['direction'], 'Resume direction changed.', 'change_conflict')
    from .requirement_publication import verify_publication
    verify_publication(database, item, record, github)
    for assignment in assignments.rows(database):
        if assignment.get('requirement_disposition', {}).get('change_id') == request['change_id']:
            require(released_claim(database, assignment), 'Old worker cleanup must reconcile before resumption.', 'worker_active')
    independent = {r['number'] for r in item['ticket_work']['frontier']['items']} - set(record['affected'])
    record['preserved_evidence'] = validity(database, item, github, independent)
    if record['status'] != 'resumed':
        linked = record.get('stuck_revision')
        if linked:
            stuck = item['stuck_work'][linked['assignment_id']]
            require(all(stuck[k] == linked[k] for k in ('event_id', 'applied_response', 'affected', 'authorization')) and
                    (stuck['status'] == 'revision-required' or stuck['status'] == 'released' and stuck.get('requirement_change') == request['change_id']),
                    'Only the exact explicitly linked revise hold can release.', 'decision_required')
            stuck.update(status='released', requirement_change=request['change_id'])
        record.update(status='resume-pending', resume_direction=request['direction'])
        checkpoint(database, item)
        sync_blocks(database, item, record, github, release=True)
        verify_publication(database, item, record, github)
        record['status'] = 'resumed'
        checkpoint(database, item)
    return tickets.frontier(database, item, github)
