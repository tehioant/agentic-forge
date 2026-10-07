"""Explicit engineering stuckness, independent read-only investigation and operator direction."""
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

from . import assignments, attention, simplification, tickets
from .planning import require, text
from .repositories import RepositoryError
from .sandbox_source import manifest, physical

CONTRACT = 'factory-stuck-diagnosis-v1'
REPORT_FIELDS = {'contract', 'adaptation', 'pins', 'feedback_loop', 'commands', 'hypotheses',
                 'attempted_repairs', 'findings', 'cause_or_uncertainty', 'directions', 'recommendation', 'blockers'}
TERMINAL = {'complete', 'failed', 'stopped'}


def save_assignment(database, assignment):
    database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                     (json.dumps(assignment, sort_keys=True), assignment['assignment_id']))


def held_numbers(item):
    return {number for record in item.get('stuck_work', {}).values() if record['status'] != 'released'
            for number in record['affected']}


def released_claim(database, assignment):
    scope = assignment['ticket_scope']
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                           (scope['project'], scope['iteration'])).fetchone()
    item = json.loads(row[0]) if row else {}
    return (assignment.get('stuck_disposition') in {'held', 'superseded'} and
            (not assignment.get('runtime') or assignment['runtime']['status'] in TERMINAL) and
            assignment['assignment_id'] in {identity for record in item.get('stuck_work', {}).values()
                if record.get('blocks_verified') for identity in record['retired_assignments']})


def source(database, project, iteration, operator, identity):
    prior = assignments.inspect(database, project, iteration, operator, identity)
    assert prior is not None
    runtime = prior.get('runtime', {})
    result = prior.get('submitted_result', {})
    require(prior['handoff']['stage'] != 'diagnosis' and runtime.get('status') == 'complete' and
            runtime.get('container_removed') is True and result.get('status') == 'stuck' and
            result.get('run_id') == runtime.get('run_id') and
            prior.get('result_disposition', {}).get('isolated_execution') is True,
            'Only an explicit responsible-worker stuck result, not a crash or run limit, can escalate.', 'stuck_required')
    require(not (Path(runtime['artifacts']) / 'stop').exists(), 'Cancelled work cannot escalate.', 'cancelled')
    root = physical(runtime['artifacts'], directory=True)
    tree = manifest(root / 'workspace')
    baseline = manifest(root / 'inputs' / 'baseline')
    require(assignments.digest(tree) == runtime.get('candidate_sha256') and
            assignments.digest(baseline) == runtime.get('baseline_sha256') and
            simplification.object_json(physical(root / 'scratch/result.json').read_text(), 'stuck_required') == result,
            'Exact stuck workspace, baseline and retained result required.', 'stale_assignment')
    artifact = next((a for a in result['artifacts'] if a['name'] == 'stuck-declaration'), None)
    require(artifact is not None, 'Responsible declaration and attempted fixes must be retained.', 'stuck_required')
    assert artifact is not None
    assignments.artifacts(result['artifacts'], {'stuck-declaration'}, 'stuck_required')
    declaration = simplification.object_json(artifact['content'], 'stuck_required')
    require(set(declaration) == {'reason', 'attempted_fixes', 'findings'} and text(declaration['reason']) and
            isinstance(declaration['attempted_fixes'], list) and 0 < len(declaration['attempted_fixes']) <= 100 and
            all(isinstance(fix, dict) and set(fix) == {'change', 'command', 'result'} and all(text(v) for v in fix.values())
                for fix in declaration['attempted_fixes']) and isinstance(declaration['findings'], list) and
            declaration['findings'] and all(text(v) for v in declaration['findings']),
            'Stuck declaration requires attempted changes, actual feedback and findings.', 'stuck_required')
    pins = {'stuck_assignment': identity, 'stuck_run': runtime['run_id'], 'handoff_digest': prior['handoff_digest'],
            'result_sha256': assignments.digest(result), 'declaration_sha256': artifact['sha256'],
            'tree_sha256': assignments.digest(tree), 'baseline_sha256': assignments.digest(baseline),
            'issue_sha256': prior['handoff']['issue']['body_sha256'], 'spec_commit': prior['handoff']['spec_commit']}
    return prior, root, tree, baseline, declaration, pins


def record_for(database, project, iteration, operator, identity):
    item = assignments.current(database, project, iteration, operator)
    record = item.get('stuck_work', {}).get(identity)
    require(record is not None, 'Declare this exact stuck assignment first.', 'stuck_required')
    prior, root, tree, baseline, declaration, pins = source(database, project, iteration, operator, identity)
    require(record['pins'] == pins, 'Stuck evidence changed.', 'stale_assignment')
    return item, record, prior, root, tree, baseline, declaration


def sync_blocks(database, item, record, github, release=False):
    observed = tickets.frontier(database, item, github)
    rows = {r['number']: r for r in observed['ticket_work']['frontier']['items']}
    other = {n for r in observed.get('stuck_work', {}).values() if r is not record and
             r['pins'] != record['pins'] and r['status'] != 'released' for n in r['affected']}
    board = observed['ticket_work']['board']
    statuses = observed['ticket_work']['request']['tracker']['statuses']
    for number in record['affected']:
        row = rows.get(number)
        require(row is not None and row['issue_state'] == 'open' and
                all(row[k] == record['issues'][str(number)][k] for k in ('id', 'node_id')),
                'Affected work must remain open with exact GitHub identities.', 'scope_mismatch')
        assert row is not None
        issue = tickets.gh.read_issue(github, item['repository'], number)
        require(hashlib.sha256(issue['body'].encode()).hexdigest() == record['issues'][str(number)]['body_sha256'],
                'Affected requirements changed; stale direction cannot release revised scope.', 'scope_mismatch')
    for number in record['affected']:
        row = rows[number]
        target = record['previous_statuses'][str(number)] if release and number not in other else 'blocked'
        # A retry is eligible work, not an automatically active implementation.
        if target == 'active':
            target = 'ready'
        if row['status'] != target:
            tickets.gh.set_field(github, board['project_id'], row['membership_id'], board['status']['id'],
                                 board['status']['options'][statuses[target]])
        readback = tickets.frontier(database, observed, github)
        exact = next(r for r in readback['ticket_work']['frontier']['items'] if r['number'] == number)
        require(exact['status'] == target and exact['issue_state'] == 'open',
                'Exact blocked/retry status did not read back.', 'publication_mismatch')
        observed = readback
    return observed


def declare(database, project, iteration, operator, request, github):
    require(isinstance(request, dict) and set(request) == {'stuck_assignment'} and assignments.sha(request['stuck_assignment']),
            'Supply the exact responsible assignment.', 'invalid_diagnosis')
    identity = request['stuck_assignment']
    item = assignments.current(database, project, iteration, operator)
    prior, root, tree, baseline, declaration, pins = source(database, project, iteration, operator, identity)
    observed = tickets.frontier(database, item, github)
    issue = tickets.gh.read_issue(github, item['repository'], prior['handoff']['issue']['number'])
    require(hashlib.sha256(issue['body'].encode()).hexdigest() == pins['issue_sha256'],
            'Stuck issue requirements changed.', 'scope_mismatch')
    existing = observed.get('stuck_work', {}).get(identity)
    if existing:
        require(existing['pins'] == pins, 'Stuck declaration replay changed.', 'stale_assignment')
        if existing['status'] == 'released':
            return existing
        record = existing
    else:
        number = prior['handoff']['issue']['number']
        affected = {number}
        rows = observed['ticket_work']['frontier']['items']
        while True:
            expanded = affected | {r['number'] for r in rows if affected.intersection(r['blockers'])}
            if expanded == affected:
                break
            affected = expanded
        related = [a for a in assignments.rows(database) if a['ticket_scope'] == prior['ticket_scope']]
        require(all(not a.get('runtime') or a['runtime']['status'] in TERMINAL for a in related),
                'Stop/reconcile other live roles before releasing ticket ownership.', 'run_conflict')
        previous_statuses = {str(r['number']): r['status'] for r in rows if r['number'] in affected}
        for number in affected:
            holds = [r for r in observed.get('stuck_work', {}).values()
                     if r['status'] != 'released' and number in r['affected']]
            if holds:
                previous_statuses[str(number)] = min(holds, key=lambda r: r['sequence'])['previous_statuses'][str(number)]
        record = {'status': 'blocking', 'sequence': 1 + len(observed.get('stuck_work', {})),
                  'pins': pins, 'declaration': declaration, 'affected': sorted(affected),
                  'issues': {str(r['number']): {**{k: r[k] for k in ('id', 'node_id')},
                      'body_sha256': hashlib.sha256(tickets.gh.read_issue(github, item['repository'], r['number'])['body'].encode()).hexdigest()}
                      for r in rows if r['number'] in affected},
                  'previous_statuses': previous_statuses,
                  'retired_assignments': [a['assignment_id'] for a in related], 'blocks_verified': False,
                  'diagnosis_assignment': None, 'event_id': None, 'applied_response': None}
        observed.setdefault('stuck_work', {})[identity] = record
        for a in related:
            a['stuck_disposition'] = 'held'
            save_assignment(database, a)
        tickets.checkpoint(database, observed)  # Durable intent precedes controlled external writes.
    observed = sync_blocks(database, observed, record, github)
    record = observed['stuck_work'][identity]
    record.update(blocks_verified=True)
    if record['status'] == 'blocking':
        record['status'] = 'diagnosis'
    if observed.get('reservation', {}).get('issue_number') in record['affected']:
        observed.pop('reservation')
    tickets.checkpoint(database, observed)
    return record


def preceding(record, declaration, prior):
    return [simplification.artifact('failure-evidence', simplification.handoff_json({
        'pins': record['pins'], 'declaration': declaration, 'artifacts': prior['submitted_result']['artifacts'],
        'tests': prior['submitted_result']['tests'], 'native_execution_trusted': False}))]


def prepare(database, project, iteration, operator, request, github, dry_run=False):
    require(isinstance(request, dict) and set(request) == {'stuck_assignment', 'profile', 'skills', 'claim_id'},
            'Supply stuck identity and fresh debug profile/skills/claim.', 'invalid_diagnosis')
    item, record, prior, root, tree, baseline, declaration = record_for(database, project, iteration, operator, request['stuck_assignment'])
    require(record['blocks_verified'] and record['status'] in {'diagnosis', 'awaiting-direction'},
            'A verified held ticket requires an independent diagnosis.', 'diagnosis_held')
    bounded = {k: prior['handoff'][k] for k in assignments.REQUEST_FIELDS}
    bounded.update(stage='diagnosis', profile=request['profile'], skills=request['skills'], claim_id=request['claim_id'],
                   capabilities=['read_workspace', 'scratch', 'model'], preceding=preceding(record, declaration, prior))
    result = assignments.prepare(database, project, iteration, operator, bounded, github, dry_run=True)
    debug_claim(database, record, result['assignment_id'])
    if not dry_run:
        result = assignments.prepare(database, project, iteration, operator, bounded, github)
        item = assignments.current(database, project, iteration, operator)
        item['stuck_work'][request['stuck_assignment']]['diagnosis_assignment'] = result['assignment_id']
        tickets.checkpoint(database, item)
    return result


def debug_claim(database, record, identity):
    for axis in assignments.rows(database):
        if (axis['handoff'].get('diagnosis', {}).get('pins') != record['pins'] or axis['assignment_id'] == identity):
            continue
        runtime = axis.get('runtime', {})
        require(record['status'] == 'diagnosis' and runtime.get('status') in {'failed', 'stopped'} and
                runtime.get('container_removed') is True,
                'One debug attempt owns this escalation; stop/reconcile failure before a fresh retry.', 'claim_conflict')
    require(record['status'] != 'release-pending' or record['diagnosis_assignment'] == identity,
            'Pending release only permits exact completed diagnosis validation.', 'claim_conflict')


def handoff(database, project, iteration, operator, request):
    supplied = next(a for a in request['preceding'] if a['name'] == 'failure-evidence')
    evidence = simplification.handoff_evidence(supplied['content'])
    identity = evidence.get('pins', {}).get('stuck_assignment')
    require(assignments.sha(identity), 'Debug must link a real responsible stuck assignment.', 'stuck_required')
    item, record, prior, root, tree, baseline, declaration = record_for(database, project, iteration, operator, identity)
    require(record['blocks_verified'] and record['status'] in {'diagnosis', 'awaiting-direction', 'release-pending'} and
            all(request[k] == prior['handoff'][k] for k in
                ('repository', 'repository_id', 'workspace', 'issue', 'spec_commit', 'baseline', 'candidate', 'standards')) and
            request['preceding'] == preceding(record, declaration, prior),
            'Debug must retain original requirements and exact failure evidence independently.', 'scope_mismatch')
    return {'contract': CONTRACT, 'pins': record['pins'], 'result_fields': sorted(REPORT_FIELDS),
            'command_fields': ['command', 'result'], 'hypothesis_fields': ['hypothesis', 'prediction', 'evidence'],
            'feedback_loop_fields': ['command', 'symptom', 'result'], 'direction_fields': ['id', 'direction', 'tradeoff'],
            'read_only': True, 'native_execution_trusted': False}


def copy_candidate(database, project, iteration, operator, assignment, destination, pins):
    identity = assignment['handoff']['diagnosis']['pins']['stuck_assignment']
    _, record, prior, root, tree, baseline, _ = record_for(database, project, iteration, operator, identity)
    require(record['pins'] == assignment['handoff']['diagnosis']['pins'] and pins['baseline'] == baseline,
            'Failed candidate/baseline changed before debug staging.', 'scope_mismatch')
    shutil.rmtree(destination / 'workspace')
    shutil.copytree(root / 'workspace', destination / 'workspace')
    require(manifest(destination / 'workspace') == tree, 'Copied failed source changed.', 'scope_mismatch')
    pins['workspace'] = tree


def validate_report(report, contract, adaptation, tests):
    require(set(report) == REPORT_FIELDS and report['contract'] == CONTRACT and
            report['pins'] == contract['pins'] and report['adaptation'] == adaptation and
            all(text(report[k]) for k in ('cause_or_uncertainty', 'recommendation')),
            'Exact diagnosis pins, adaptation, uncertainty/cause and recommendation required.', 'invalid_result')
    for key in ('attempted_repairs', 'findings', 'blockers'):
        require(isinstance(report[key], list) and len(report[key]) <= 100 and all(text(v) for v in report[key]),
                'Retain attempted repairs, findings and explicit missing capabilities.', 'invalid_result')
    require(report['attempted_repairs'] and report['findings'], 'Diagnosis must retain attempts/findings.', 'invalid_result')
    for key, fields in (('commands', {'command', 'result'}),
                        ('hypotheses', {'hypothesis', 'prediction', 'evidence'}),
                        ('directions', {'id', 'direction', 'tradeoff'})):
        require(isinstance(report[key], list) and len(report[key]) <= 100 and
                all(isinstance(v, dict) and set(v) == fields and all(text(s) for s in v.values()) for v in report[key]),
                'Structured diagnostic commands, ranked falsifiable hypotheses and directions required.', 'invalid_result')
    require(report['directions'] and len({d['id'] for d in report['directions']}) == len(report['directions']) and
            report['recommendation'] in {d['id'] for d in report['directions']} and report['commands'] and
            all(c in tests for c in report['commands']),
            'Recommendation must name a candidate direction; commands must match retained test evidence.', 'invalid_result')
    loop = report['feedback_loop']
    require((isinstance(loop, dict) and set(loop) == {'command', 'symptom', 'result'} and
             all(text(v) for v in loop.values()) and {'command': loop['command'], 'result': loop['result']} in report['commands'] and
             report['hypotheses']) or (loop is None and report['blockers'] and not report['hypotheses']),
            'An executed symptom-specific feedback loop is required before hypotheses; unavailable loop needs explicit blockers.', 'invalid_result')


def validate_result(assignment, result):
    contract = assignment['handoff']['diagnosis']
    root = physical(assignment['runtime']['artifacts'], directory=True)
    require(assignments.digest(manifest(root / 'workspace')) == contract['pins']['tree_sha256'] and
            assignments.digest(manifest(root / 'inputs/baseline')) == contract['pins']['baseline_sha256'],
            'Read-only diagnosis source/baseline changed.', 'invalid_result')
    artifact = next((a for a in result['artifacts'] if a['name'] == 'diagnosis-report'), None)
    require(artifact is not None, 'Separate structured diagnosis report required.', 'invalid_result')
    assert artifact is not None
    report = simplification.object_json(artifact['content'], 'invalid_result')
    from .sandbox import check_terminal_results
    check_terminal_results(root, result['tests'], verbatim=True)
    validate_report(report, contract, assignment['handoff']['adaptation'], result['tests'])
    require(result['status'] == ('blocked' if report['feedback_loop'] is None else 'done'),
            'Missing capability is a blocker, not a successful diagnosis or repair.', 'invalid_result')
    return {**report, 'report_sha256': artifact['sha256'], 'native_execution_trusted': False,
            'advance_allowed': False, 'close_allowed': False}


def execution_binding(assignment):
    root = physical(assignment['runtime']['artifacts'], directory=True)
    files = ['scratch/result.json', 'scratch/loads.json', 'scratch/events.jsonl', 'scratch/conversation.json',
             'scratch/probes.json', 'inputs/worker.py', 'inputs/sandbox_relay.py', 'inputs/assignment.json',
             'inputs/handoff.json', 'container-inspection.json', 'launch-command.json', 'initial-source.json',
             'source-artifacts.json', 'worker-reported-tests.json']
    files += ['model-evidence/' + p.name for p in sorted((root / 'model-evidence').glob('*.json'))]
    require(any(p.startswith('model-evidence/response-') for p in files), 'Admitted response evidence required.', 'execution_required')
    evidence = {}
    for name in files:
        path = physical(root / name)
        require(path.stat().st_size <= 16_000_000, 'Execution evidence exceeds bound.', 'execution_required')
        evidence[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    h = assignment['handoff']
    return {'assignment_id': assignment['assignment_id'], 'run_id': assignment['runtime']['run_id'],
            'handoff_digest': assignment['handoff_digest'], 'pins': h['diagnosis']['pins'],
            'report_sha256': assignment['diagnosis_result']['report_sha256'],
            'skills': [{k: s[k] for k in ('name', 'source', 'sha256', 'dependencies')} for s in h['skills']],
            'adaptation_sha256': hashlib.sha256(h['adaptation'].encode()).hexdigest(),
            'loads_sha256': assignments.digest(assignment['submitted_result']['loads']),
            'work_sha256': assignments.digest(assignment['submitted_result']['work']), 'evidence': evidence}


def completed(database, project, iteration, operator, record, github):
    identity = record['diagnosis_assignment']
    require(assignments.sha(identity), 'Run the separate diagnosis first.', 'diagnosis_incomplete')
    axis = assignments.inspect(database, project, iteration, operator, identity)
    assert axis is not None
    from .sandbox import original
    assignments.prepare(database, project, iteration, operator, original(axis), github, dry_run=True,
                        diagnosis_revalidation=identity if record['status'] == 'release-pending' else None)
    require(axis.get('runtime', {}).get('status') == 'complete' and axis['runtime'].get('container_removed') is True and
            axis.get('result_disposition', {}).get('isolated_execution') is True and 'diagnosis_result' in axis,
            'Only a completed isolated diagnosis report can request direction.', 'diagnosis_incomplete')
    assignments.require_result_active(axis)
    require(validate_result(axis, axis['submitted_result']) == axis['diagnosis_result'], 'Stored debug report changed.', 'invalid_result')
    return axis


def authorization(database, path, operator, axis):
    path = physical(path)
    info = path.stat()
    require(info.st_uid == os.getuid() and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 8_000_000,
            'Private host-owned independent authorization required.', 'approval_required')
    for assignment in assignments.rows(database):
        scopes = [assignment['handoff']['workspace'], assignment['handoff']['profile']['home']]
        if assignment.get('runtime'):
            scopes.append(assignment['runtime']['artifacts'])
        require(all(not path.is_relative_to(Path(scope)) for scope in scopes),
                'Authorization must never be in any worker-controlled scope.', 'approval_required')
    root = physical(axis['runtime']['artifacts'], directory=True)
    require(root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) == 0o700,
            'Private host execution envelope required.', 'execution_required')
    config = simplification.object_json(path.read_text(), 'invalid_diagnosis')
    require(set(config) == {'operator_id', 'execution_reference', 'diagnosis'} and config['operator_id'] == operator and
            text(config['execution_reference']) and config['diagnosis'] == execution_binding(axis),
            'Independent host verification must bind the exact debug execution.', 'execution_required')
    return config


def request_direction(database, project, iteration, operator, request, github, config_path=None):
    require(isinstance(request, dict) and set(request) == {'stuck_assignment'}, 'Supply exact stuck identity.', 'invalid_diagnosis')
    item, record, _, _, _, _, _ = record_for(database, project, iteration, operator, request['stuck_assignment'])
    require(record['status'] in {'diagnosis', 'awaiting-direction'}, 'Decision cannot revive obsolete work.', 'diagnosis_held')
    axis = completed(database, project, iteration, operator, record, github)
    config = authorization(database, config_path, operator, axis) if config_path else record.get('authorization')
    require(config is not None and config['operator_id'] == operator and config['diagnosis'] == execution_binding(axis),
            'Worker provenance alone cannot authorize an operator decision lifecycle.', 'execution_required')
    report = axis['diagnosis_result']
    event_id = 'stuck-' + request['stuck_assignment']
    message = json.dumps({'request': 'Antoine: choose retry or revise, with explicit direction. Silence grants nothing.',
                          'stuck_assignment': request['stuck_assignment'], 'diagnosis_assignment': axis['assignment_id'],
                          'report_sha256': report['report_sha256'], 'cause_or_uncertainty': report['cause_or_uncertainty'],
                          'blockers': report['blockers'], 'directions': report['directions'], 'recommendation': report['recommendation']}, sort_keys=True)
    require(len(message) <= 16000, 'Direction summary exceeds attention bound; retain full report in assignment.', 'invalid_diagnosis')
    database.execute('CREATE TABLE IF NOT EXISTS notifications '
                     '(project TEXT, iteration TEXT, event_id TEXT, payload TEXT, PRIMARY KEY(project, iteration, event_id))')
    attention.validate_origin(item['origin'])
    notification = attention.enqueue(database, item, {'event_id': event_id, 'kind': 'decision', 'decision_id': event_id,
        'message': message, 'issue_number': axis['handoff']['issue']['number'], 'run_id': axis['runtime']['run_id'],
        'evidence_ids': [request['stuck_assignment'], axis['assignment_id'], report['report_sha256']]})
    record.update(status='awaiting-direction', event_id=event_id, authorization=config)
    tickets.checkpoint(database, item)
    return {'stuck': record, 'attention': notification}


def operator_direction(content):
    direction = simplification.object_json(content, 'invalid_decision')
    require(set(direction) == {'action', 'direction'} and isinstance(direction['action'], str) and
            direction['action'] in {'retry', 'revise'} and text(direction['direction']),
            'Explicit retry/revise and direction required; no inferred grant.', 'invalid_decision')
    return direction


def apply_direction(database, project, iteration, operator, request, github):
    require(isinstance(request, dict) and set(request) == {'stuck_assignment', 'event_id'},
            'Supply exact stuck identity and correlated decision event.', 'invalid_diagnosis')
    item, record, _, _, _, _, _ = record_for(database, project, iteration, operator, request['stuck_assignment'])
    require(record['event_id'] == request['event_id'] and record['event_id'] is not None,
            'Unrelated attention cannot release stuck work.', 'invalid_decision')
    notification = attention.get_record(database, item, request['event_id'])
    response = notification['decision_response']
    require(response is not None, 'Silence or delivery is not an operator direction.', 'decision_required')
    attention.validate_response(response, notification, operator)
    direction = operator_direction(response['response'])
    if record['applied_response'] is not None:
        require(record['applied_response'] == response, 'A decision cannot be applied twice with different scope.', 'decision_conflict')
        if record['status'] != 'release-pending':
            return record
    else:
        axis = completed(database, project, iteration, operator, record, github)
        require(record.get('authorization', {}).get('diagnosis') == execution_binding(axis), 'Debug execution changed after request.', 'execution_required')
        record.update(applied_response=response, direction=direction,
                      status='release-pending' if direction['action'] == 'retry' else 'revision-required')
        tickets.checkpoint(database, item)
    if direction['action'] == 'revise':
        return record  # Material changes need versioned specs/tickets, not reinterpretation here.
    axis = completed(database, project, iteration, operator, record, github)
    require(record.get('authorization', {}).get('diagnosis') == execution_binding(axis),
            'Pending release must retain exact authorized debug evidence.', 'execution_required')
    observed = sync_blocks(database, item, record, github, release=True)
    record = observed['stuck_work'][request['stuck_assignment']]
    record['status'] = 'released'
    for a in assignments.rows(database):
        if a['assignment_id'] in record['retired_assignments'] or a['assignment_id'] == record['diagnosis_assignment']:
            a['stuck_disposition'] = 'superseded'
            save_assignment(database, a)
    tickets.checkpoint(database, observed)
    tickets.frontier(database, observed, github)
    return record
