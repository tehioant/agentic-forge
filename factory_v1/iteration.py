"""Verified finite-milestone reports and operator-only same-product continuation."""
import copy
import hashlib
import json
import sqlite3
from contextlib import closing

from . import assignments, attention, bugs, controls, delivery, diagnosis, merge, recovery, requirements, tickets
from .planning import require, text, transition
from .repositories import save_iteration


def frontier(database, item, github, *, resolution=None):
    """Observe tracker read-only, checking controls against the real durable state."""
    controls.require_active(database, item['project_id'], item['iteration_id'], resolution=resolution)
    with closing(sqlite3.connect(':memory:')) as observation:
        database.backup(observation)
        result = tickets.frontier(observation, item, assignments.ReadOnlyGitHub(github), resolution=resolution)['ticket_work']['frontier']
    controls.require_active(database, item['project_id'], item['iteration_id'], resolution=resolution)
    return result


def required_items(item, observed, github):
    require(item['ticket_work'].get('publication_complete') and not observed['foreign_active'],
            'Complete authoritative milestone publication required.', 'iteration_incomplete')
    rows = {r['number']: r for r in observed['items']}
    pinned = {r['number'] for r in item['ticket_work']['published'].values()}
    require(pinned and pinned <= rows.keys(), 'Required published work left the milestone.', 'iteration_incomplete')
    required, deferred, issues = [], [], {}
    for number, row in sorted(rows.items()):
        issue = tickets.gh.read_issue(github, item['repository'], number)
        issues[number] = copy.deepcopy(issue)
        parts = tickets.sections(issue['body']) or {}
        if number not in pinned and parts.get('Bug scope') == 'deferred' and row['status'] == 'deferred' and row['contract_valid']:
            deferred.append(number)
            continue
        require(row['contract_valid'] and not row['held'] and row['status'] == 'done' and
                row['issue_state'] == issue['state'] == 'closed' and row['state_reason'] == 'completed' and
                all(rows.get(n, {}).get('status') == 'done' and rows[n]['issue_state'] == 'closed' and
                    not rows[n]['held'] for n in row['blockers']),
                'Open, blocked, reopened or invalid required work remains.', 'iteration_incomplete')
        required.append(row)
    require(not (requirements.held_numbers(item) | diagnosis.held_numbers(item) | bugs.held_numbers(item)),
            'Unresolved requirement, diagnosis or bug-discovery holds remain.', 'iteration_incomplete')
    return required, deferred, issues


def delivered_item(database, item, row, config_path, operator, github, main):
    """Recheck original execution/reviews and external integration, not journal booleans."""
    config = merge.load_config(config_path, operator)
    require(config['issue'] == row['number'] and config['base'] == 'main' and
            config['repository'] == item['repository'] and
            config['repository_id'] == item['repository_onboarding']['metadata']['id'] and
            config['api_base'] == github.base,
            'Exact milestone issue/repository merge authority required.', 'scope_mismatch')
    prior, _, root = merge.chain(database, item['project_id'], item['iteration_id'], operator, config, config_path)
    assert prior is not None
    api = merge.MergeGitHub(config)
    observed_issue = merge.issue(api, prior, config)
    require(all(observed_issue[k] == row[k] for k in ('number', 'id', 'node_id')) and
            observed_issue['state'] == 'closed' and observed_issue.get('state_reason') == 'completed' and
            hashlib.sha256(observed_issue['body'].encode()).hexdigest() == prior['handoff']['issue']['body_sha256'],
            'Exact delivered issue requirements/state changed.', 'stale_requirements')
    merge.require_publication(database, item, api, row['number'])
    journal = prior.get('merge', {})
    require(journal.get('status') == 'delivered' and journal.get('failure') is None and
            journal.get('close_allowed') is True and
            journal.get('authority') == {k: v for k, v in config.items() if k != 'bearer'},
            'Trusted delivered integration journal required.', 'not_integrated')
    plan = merge.mapped_candidate(api, config, prior, root)
    pr = merge.target(api, config)
    evidence = merge.merged_evidence(api, config, pr, plan)
    require(evidence['main'] == main and all(journal.get('integrated_evidence', {}).get(k) == evidence[k]
            for k in ('candidate', 'integrated', 'tree', 'parents')),
            'Exact merged candidate must remain integrated into observed healthy main.', 'not_integrated')
    merge.protection(api, config)
    checks = merge.checks(api, config, evidence['integrated'])
    require(checks == journal.get('delivery_checks'), 'Integrated check evidence changed.', 'checks_required')
    require(merge.issue(api, prior, config) == observed_issue,
            'Work changed during completion verification.', 'stale_requirements')
    return {'issue': {'number': row['number'], 'id': row['id'],
                     'url': 'https://github.com/' + item['repository'] + '/issues/' + str(row['number'])},
            'pr': {'number': config['pr_number'], 'url': 'https://github.com/' + item['repository'] + '/pull/' + str(config['pr_number'])},
            'assignment_id': prior['assignment_id'], 'requirements': prior['revision'],
            'reviews': [config['standards_assignment'], config['spec_assignment']],
            'integration': evidence, 'checks': checks}


def pending_decisions(database, item):
    return [{'event_id': r['event']['event_id'], 'decision_id': r['event']['decision_id'],
             'issue_number': r['event']['issue_number']}
            for r in attention.records(database, item['project_id'], item['iteration_id'], item)
            if r['event']['kind'] == 'decision' and r['decision_response'] is None]


def complete(database, project, iteration, operator, request, github):
    """Caller holds state_lock; all external reads precede atomic report + outbox + wait."""
    item = delivery.context(database, project, iteration, operator)
    controls.require_active(database, project, iteration)
    require(item['status'] in {'active', 'waiting-for-operator'} and not item.get('next_iteration'),
            'Paused or continued iteration cannot complete.', 'iteration_paused')
    require(all((a.get('runtime') is None and
                 (requirements.released_claim(database, a) or diagnosis.released_claim(database, a))) or
                ((a.get('runtime') or {}).get('status') in {'complete', 'failed', 'stopped'} and
                 a['runtime'].get('container_removed') is True) for a in assignments.rows(database)
                if a['ticket_scope']['project'] == project and a['ticket_scope']['iteration'] == iteration),
            'Workers must be reconciled and absent before completion.', 'iteration_incomplete')
    require(isinstance(request, dict) and set(request) == {'merge_configs'} and
            isinstance(request['merge_configs'], list) and request['merge_configs'] and
            all(text(p) for p in request['merge_configs']) and len(set(request['merge_configs'])) == len(request['merge_configs']),
            'Provide one private merge configuration per required milestone issue.', 'invalid_completion')
    saved = delivery.monitor_state(item, operator)
    require(saved is not None and item.get('main_incident') is None and not recovery.pending_registration(item),
            'Configured healthy delivery and no unresolved incident/recovery required.', 'iteration_incomplete')
    assert saved is not None
    delivery.validate_persisted(database, item, saved)
    require(saved['latest']['status'] == 'healthy', 'Unknown delivery is not completion.', 'iteration_incomplete')
    observed = frontier(database, item, github)
    required, deferred, scoped_issues = required_items(item, observed, github)
    configs = {merge.load_config(p, operator)['issue']: p for p in request['merge_configs']}
    require(len(configs) == len(request['merge_configs']) and set(configs) == {r['number'] for r in required},
            'Cover every required item exactly; caller cannot select a subset.', 'iteration_incomplete')
    decisions = pending_decisions(database, item)
    require(not any(d['issue_number'] in configs or d['issue_number'] == item['handoff']['issue']['number'] for d in decisions),
            'Required milestone operator decisions remain unresolved.', 'iteration_incomplete')
    health = delivery.observe(saved['config'])
    require(health['status'] == 'healthy', 'Actual configured CI/CD and deployment health unknown/failed.', 'iteration_incomplete')
    delivered = [delivered_item(database, item, row, configs[row['number']], operator, github, health['main']) for row in required]
    bug_records = [{'discovery': w['discovery'], 'identity': w.get('identity'), 'status': w['status'],
                    'archived': archived, 'publication_complete': w.get('publication_complete', False)}
                   for archived, records in ((False, item.get('bug_work', {}).values()), (True, item.get('bug_work_history', [])))
                   for w in records]
    require(all(w['archived'] or w['discovery']['scope'] == 'deferred' or (w['publication_complete'] and w['identity'] and
                w['identity']['number'] in configs) for w in bug_records),
            'Blocking/recovery bugs must have verified delivery.', 'iteration_incomplete')
    require(delivery.observe(saved['config']) == health and
            [delivered_item(database, item, row, configs[row['number']], operator, github, health['main']) for row in required] == delivered,
            'Milestone or delivery changed during verification.', 'completion_race')
    final_frontier = frontier(database, item, github)
    require(final_frontier == observed and
            required_items(item, final_frontier, github) == (required, deferred, scoped_issues),
            'Milestone scope or issue changed during verification.', 'completion_race')
    report = {'project_id': project, 'iteration_id': iteration, 'repository': item['repository'],
              'spec': item['handoff'], 'delivered': delivered, 'delivery': health,
              'verification': 'live' if github.base == 'https://api.github.com' and saved['config']['api_base'] == github.base else 'simulation',
              'bugs': bug_records, 'deferred_issues': deferred, 'pending_decisions': decisions,
              'invitation': "This iteration is delivered, not the whole product. Reply explicitly to interview the next iteration of this same product."}
    identity = assignments.digest(report)
    completion = {'report_id': identity, 'event_id': 'completion-' + identity, 'report': report}
    require(item.get('completion', completion) == completion,
            'Completed report is immutable; changed delivery requires investigation.', 'completion_conflict')
    from .readiness_gate import admission
    with admission(database, project, iteration, operation='effect'):
        controls.require_active(database, project, iteration)
        database.execute('BEGIN IMMEDIATE')
        latest = delivery.context(database, project, iteration, operator)
        require(latest == item, 'Iteration changed across external verification; no report admitted.', 'completion_race')
        if 'completion' not in item:
            item['completion'] = completion
            if item.get('reservation'):
                item.setdefault('reservation_history', []).append(item.pop('reservation'))
            item['status'] = 'waiting-for-operator'
            transition(item, 'waiting-for-operator')
            database.execute('CREATE TABLE IF NOT EXISTS notifications (project TEXT, iteration TEXT, event_id TEXT, payload TEXT, PRIMARY KEY(project, iteration, event_id))')
            lines = [f"Iteration {iteration} delivered for {item['repository']} ({report['verification']}). Report {identity}."]
            lines += [f"#{r['issue']['number']} {r['issue']['url']} PR {r['pr']['url']} commit {r['integration']['integrated']}" for r in delivered]
            lines += [f"Deployment {health['deployment']['id']} at {health['main']}: healthy. Deferred issues: {deferred}. Pending decisions: {decisions}.",
                      f"Retained bug discoveries: {len(bug_records)}. Inspect the durable completion report for scope/evidence.", report['invitation']]
            message = '\n'.join(lines)
            attention.enqueue(database, item, {'event_id': completion['event_id'], 'kind': 'completion',
                'message': message, 'issue_number': item['handoff']['issue']['number'],
                'run_id': completion['event_id'], 'evidence_ids': [identity]})
            save_iteration(database, item)
        database.commit()
    return completion


def continuation_inputs(item):
    """Pin the current scoped authorities, not the milestone's superseded handoff."""
    delivered = item['completion']['report'].get('delivered', [])
    numbers = {r['number'] for r in item['ticket_work']['published'].values()}
    numbers.update(int(n) for n in item.get('requirement_authority', {}))
    numbers.update(r['issue']['number'] for r in delivered)
    require(all(r['requirements'] == requirements.authority(item, r['issue']['number']) for r in delivered),
            'Delivered scoped requirement authority changed.', 'stale_requirements')
    specs, loads = {}, {}
    for number in sorted(numbers):
        authority = requirements.authority(item, number)
        inputs = requirements.inputs(item, number)
        require(inputs.get('handoff') == authority['spec'] and inputs.get('documents'),
                'Current scoped specification inputs required.', 'stale_requirements')
        specs[str(number)] = copy.deepcopy(authority['spec'])
        for document in inputs['documents'].values():
            require(loads.get(document['url'], document['sha256']) == document['sha256'],
                    'Conflicting scoped document pins.', 'stale_requirements')
            loads[document['url']] = document['sha256']
    require(specs, 'Previous scoped specifications required.', 'stale_requirements')
    return {'previous_spec': next(iter(specs.values())), 'previous_specs': specs, 'input_loads': loads}


def next_iteration(database, project, iteration, operator, request):
    """Authenticated later operator input is the only continuation admission."""
    from . import planning
    from .skills import resolve_installed
    database.execute('BEGIN IMMEDIATE')
    item = delivery.context(database, project, iteration, operator)
    controls.require_active(database, project, iteration)
    fields = {'iteration_id', 'event_id', 'operator_id', 'origin', 'reference', 'message_id', 'input', 'skills'}
    require(isinstance(request, dict) and set(request) == fields and request['operator_id'] == operator and
            request['origin'] == item['origin'] and text(request['reference']) and text(request['input']) and
            attention.identifier(request['iteration_id']) and request['iteration_id'] != iteration,
            'Later explicit input from the exact originating operator required.', 'approval_required')
    completion = item.get('completion')
    require(completion is not None and item['status'] == 'waiting-for-operator' and
            request['event_id'] == completion['event_id'], 'Bind the completed iteration invitation.', 'iteration_wait_required')
    assert completion is not None
    require(completion['report_id'] == assignments.digest(completion['report']) and
            completion['event_id'] == 'completion-' + completion['report_id'] and
            all(completion['report'].get(k) == item[k] for k in ('project_id', 'iteration_id', 'repository')),
            'Exact immutable completion report required.', 'invalid_completion')
    record = attention.get_record(database, item, completion['event_id'])
    require(record['state'] == 'delivered' and text(request['message_id']) and request['message_id'].isascii() and
            request['message_id'].isdigit() and not request['message_id'].startswith('0') and len(request['message_id']) <= 20 and
            int(request['message_id']) > int(record['receipt']['message_id']) and
            request['reference'] != record['receipt']['reference'],
            'Verify exact-origin report delivery, then retain a distinct later operator message.', 'delivery_required')
    if item.get('next_iteration'):
        require(item['next_iteration']['request'] == request, 'Continuation input cannot be rebound.', 'iteration_conflict')
        result = delivery.context(database, project, request['iteration_id'], operator)
        database.commit()
        return result
    require(not database.execute("SELECT 1 FROM iterations WHERE json_extract(payload, '$.status')='active'").fetchone(),
            'Another active iteration must wait or pause first.', 'iteration_conflict')
    require(not database.execute('SELECT 1 FROM iterations WHERE project_id=? AND iteration_id=?',
            (project, request['iteration_id'])).fetchone(), 'Iteration identity already exists.', 'iteration_conflict')
    skills = resolve_installed(request['skills'])
    result = {k: copy.deepcopy(item[k]) for k in ('project_id', 'repository', 'origin', 'repository_onboarding')}
    result.update(iteration_id=request['iteration_id'], idea=request['input'], status='active', execution_allowed=False,
                  approval={'operator_id': operator, 'reference': request['reference']},
                  previous_iteration={'iteration_id': iteration, 'completion': completion, 'input': request},
                  correlation={**item['correlation'], 'iteration_id': request['iteration_id'],
                               'worker_run_id': None, 'work_item': None, 'evidence_id': assignments.digest(request)})
    planning.assign(result, {'origin': item['origin'], 'expected_revision': 0, 'skills': request['skills']}, skills)
    result['planning']['continuation'] = {**continuation_inputs(item), 'input': request,
                                       'adaptation': planning.NEXT_ADAPTATION,
                                       'dependencies': {'grill-me': ['grilling'], 'grilling': [], 'to-spec': []}}
    result['planning']['instructions'] += ('\nContinue this SAME product from ALL continuation.previous_specs and the explicit input. '
        'Preserve any explicit round-completion gate; no synthesis before actual answers. '
        'Load continuation input_loads, planning:input and planning:adaptation; retain correlated '
        'instruction loads, ordered skill work and actual rounds through complete-planning execution. '
        'Publish new updated-spec pointers, never overwrite the preceding milestone.')
    with controls.admission(database, project, iteration):
        delivery.inherit_monitor(database, item, result, operator)
        database.execute('INSERT INTO iterations VALUES (?, ?, ?)', (project, result['iteration_id'], json.dumps(result)))
        item['next_iteration'] = {'iteration_id': result['iteration_id'], 'request': request}
        save_iteration(database, item)
        database.commit()
    return result
