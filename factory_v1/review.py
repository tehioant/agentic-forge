"""Exact two-axis review and corrective handoffs; worker reports are never authority."""
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

from . import assignments, simplification
from .planning import require, text
from .publication import policy_path
from .repositories import RepositoryError
from .sandbox_source import manifest, physical

AXES = {'review-standards': 'Standards', 'review-spec': 'Spec'}
CONTRACT = 'factory-independent-review-v1'
FINDING_FIELDS = {'id', 'kind', 'path', 'requirement', 'evidence', 'correction'}
KINDS = {'missing', 'partial', 'incorrect', 'scope', 'standards', 'harmful-simplification', 'test-weakening', 'gate-weakening', 'security-weakening'}


def candidate(database, project, iteration, operator, assignment_id):
    prior = assignments.inspect(database, project, iteration, operator, assignment_id)
    assert prior is not None
    for child in assignments.rows(database):
        if (child['handoff'].get('corrections', {}).get('pins', {}).get('candidate_assignment') == assignment_id and
                child.get('runtime', {}).get('status') == 'complete' and child.get('submitted_result', {}).get('status') == 'done'):
            require(False, 'Corrective work superseded this candidate; both axes and checks must rerun.', 'stale_review')
    runtime = prior.get('runtime', {})
    stage = prior.get('simplification_result', {})
    require(prior['handoff']['stage'] == 'simplify' and runtime.get('status') == 'complete' and
            runtime.get('container_removed') is True and prior.get('submitted_result', {}).get('status') == 'done' and
            stage.get('outcome') in {'cleanup', 'no-op'} and stage.get('checks_verified') is True and
            not stage.get('findings'),
            'An unchanged completed simplified candidate without unresolved findings is required.', 'candidate_unverified')
    try:
        root = physical(runtime['artifacts'], directory=True)
        assignments.require_result_active(prior)
        source, raw, checks = simplification.check_receipt(root, runtime)
        baseline = manifest(root / 'inputs' / 'baseline')
        require(assignments.digest(source) == runtime.get('candidate_sha256') == stage.get('candidate_sha256') and
                assignments.digest(baseline) == runtime.get('baseline_sha256') and
                hashlib.sha256(raw.encode()).hexdigest() == runtime.get('checks_sha256') and
                [r['command'] for r in checks['records']] == prior['handoff']['simplification']['verification_commands'],
                'Candidate, baseline or check evidence changed.', 'candidate_unverified')
    except (OSError, KeyError, UnicodeError) as error:
        raise RepositoryError('candidate_unverified', 'Private candidate evidence unavailable.') from error
    return prior, root, source, baseline, checks


def pins(prior, source, baseline):
    handoff = prior['handoff']
    return {'candidate_assignment': prior['assignment_id'], 'candidate_run': prior['runtime']['run_id'],
            'head': handoff['candidate'], 'tree_sha256': assignments.digest(source),
            'baseline': handoff['baseline'], 'baseline_sha256': assignments.digest(baseline),
            'spec_commit': handoff['spec_commit'], 'checks_sha256': prior['runtime']['checks_sha256'],
            'issue_sha256': handoff['issue']['body_sha256']}


def preceding(prior, root, source, baseline, checks):
    # No implementation conversation or persuasive work summary enters either axis.
    return [simplification.artifact('candidate', prior['handoff']['candidate']),
            simplification.artifact('simplification-evidence', simplification.handoff_json({
                'pins': pins(prior, source, baseline), 'checks': checks,
                'simplification': prior['simplification_result'], 'native_execution_trusted': False})),
            simplification.diff_artifact(root, baseline, source)]


def prepare(database, project, iteration, operator, request, github, dry_run=False):
    require(isinstance(request, dict) and set(request) == {'candidate_assignment', 'axis', 'profile', 'skills', 'claim_id'} and
            assignments.sha(request['candidate_assignment']) and isinstance(request['axis'], str) and request['axis'] in AXES,
            'Supply a completed candidate and a separate fresh axis profile/skills/claim.', 'invalid_review')
    prior, root, source, baseline, checks = candidate(database, project, iteration, operator, request['candidate_assignment'])
    bounded = {key: prior['handoff'][key] for key in assignments.REQUEST_FIELDS}
    bounded.update(stage=request['axis'], profile=request['profile'], skills=request['skills'], claim_id=request['claim_id'],
                   capabilities=['read_workspace', 'scratch', 'model'], preceding=preceding(prior, root, source, baseline, checks))
    return assignments.prepare(database, project, iteration, operator, bounded, github, dry_run)


def handoff(database, project, iteration, operator, request):
    evidence = simplification.handoff_evidence(next(a['content'] for a in request['preceding'] if a['name'] == 'simplification-evidence'))
    require(isinstance(evidence.get('pins'), dict), 'Exact candidate pins required.', 'candidate_unverified')
    identity = evidence['pins'].get('candidate_assignment')
    require(assignments.sha(identity), 'Exact simplified candidate identity required.', 'candidate_unverified')
    prior, root, source, baseline, checks = candidate(database, project, iteration, operator, identity)
    require(all(request[key] == prior['handoff'][key] for key in
                ('repository', 'repository_id', 'workspace', 'issue', 'spec_commit', 'baseline', 'candidate', 'standards')) and
            request['preceding'] == preceding(prior, root, source, baseline, checks),
            'Review must retain the exact original scope, requirements, baseline, tree and checks.', 'scope_mismatch')
    changed = simplification.changed_paths(baseline, source)
    return {'contract': CONTRACT, 'axis': AXES[request['stage']], 'pins': pins(prior, source, baseline),
            'verification_commands': [r['command'] for r in checks['records']],
            'policy_paths': [p for p in changed if policy_path(p)],
            'test_changes': [p for p in changed if 'test' in p.lower()],
            'result_fields': ['contract', 'axis', 'adaptation', 'pins', 'verdict', 'findings', 'test_assessment', 'stuckness'],
            'finding_fields': sorted(FINDING_FIELDS), 'finding_kinds': sorted(KINDS),
            'verdicts': ['pass', 'reject', 'stuck'], 'native_execution_trusted': False}


def copy_candidate(database, project, iteration, operator, assignment, destination, source_pins):
    contract = assignment['handoff'].get('review') or assignment['handoff']['corrections']
    prior, root, source, baseline, _ = candidate(database, project, iteration, operator, contract['pins']['candidate_assignment'])
    require(pins(prior, source, baseline) == contract['pins'] and source_pins['baseline'] == baseline,
            'Candidate pins changed before staging.', 'scope_mismatch')
    shutil.rmtree(destination / 'workspace')
    shutil.copytree(root / 'workspace', destination / 'workspace')
    require(manifest(destination / 'workspace') == source, 'Copied candidate changed.', 'scope_mismatch')
    source_pins['workspace'] = source


def validate_result(assignment, result):
    contract = assignment['handoff']['review']
    runtime = assignment.get('runtime', {})
    root = physical(runtime['artifacts'], directory=True)
    source, raw_checks, checks = simplification.check_receipt(root, runtime)
    baseline = manifest(root / 'inputs' / 'baseline')
    require(assignments.digest(baseline) == contract['pins']['baseline_sha256'],
            'Review baseline changed.', 'invalid_result')
    if runtime.get('status') == 'complete':
        require(runtime.get('candidate_sha256') == assignments.digest(source) and
                runtime.get('baseline_sha256') == assignments.digest(baseline) and
                runtime.get('checks_sha256') == hashlib.sha256(raw_checks.encode()).hexdigest(),
                'Completed review source/check receipt pins changed.', 'invalid_result')
    require(assignments.digest(source) == contract['pins']['tree_sha256'] and
            [r['command'] for r in checks['records']] == contract['verification_commands'],
            'Review source/checks must remain unchanged.', 'invalid_result')
    artifact = next((a for a in result['artifacts'] if a['name'] == 'review-result'), None)
    require(artifact is not None, 'Separate structured axis result required.', 'invalid_result')
    report = simplification.object_json(artifact['content'], 'invalid_result')
    require(set(report) == set(contract['result_fields']) and report['contract'] == CONTRACT and
            report['axis'] == contract['axis'] and report['adaptation'] == assignment['handoff']['adaptation'] and
            report['pins'] == contract['pins'] and isinstance(report['verdict'], str) and report['verdict'] in contract['verdicts'] and
            text(report['test_assessment']) and isinstance(report['findings'], list) and len(report['findings']) <= 200,
            'Exact axis, adaptation, revisions and requirement-based test assessment required.', 'invalid_result')
    ids = set()
    for finding in report['findings']:
        require(isinstance(finding, dict) and set(finding) == FINDING_FIELDS and
                assignments.identifier(finding['id']) and finding['id'] not in ids and
                isinstance(finding['kind'], str) and finding['kind'] in KINDS and
                all(text(finding[key]) for key in ('path', 'requirement', 'evidence', 'correction')),
                'Exact uniquely identified, actionable findings required.', 'invalid_result')
        ids.add(finding['id'])
    require((report['verdict'] == 'pass' and result['status'] == 'done' and not report['findings'] and report['stuckness'] is None) or
            (report['verdict'] == 'reject' and result['status'] == 'done' and report['findings'] and report['stuckness'] is None) or
            (report['verdict'] == 'stuck' and result['status'] == 'stuck' and isinstance(report['stuckness'], dict) and
             set(report['stuckness']) == {'attempts', 'evidence', 'uncertainty', 'recommendation'} and
             all(text(v) for v in report['stuckness'].values())),
            'A pass cannot hide findings; stuck work must retain debug evidence.', 'invalid_result')
    return {**report, 'report_sha256': artifact['sha256'], 'native_execution_trusted': False,
            'advance_allowed': False, 'close_allowed': False}


def pair(database, project, iteration, operator, request, github):
    require(isinstance(request, dict) and set(request) == {'standards_assignment', 'spec_assignment'} and
            all(assignments.sha(v) for v in request.values()), 'Supply exact separate axis assignment IDs.', 'invalid_review')
    axes = []
    for stage, field in (('review-standards', 'standards_assignment'), ('review-spec', 'spec_assignment')):
        axis = assignments.inspect(database, project, iteration, operator, request[field])
        assert axis is not None
        require(axis['handoff']['stage'] == stage, 'Wrong review axis.', 'scope_mismatch')
        bounded = {key: axis['handoff'][key] for key in assignments.REQUEST_FIELDS}
        bounded['skills'] = [{k: v for k, v in s.items() if k != 'instructions'} for s in bounded['skills']]
        assignments.prepare(database, project, iteration, operator, bounded, github, dry_run=True)
        require(axis.get('runtime', {}).get('status') == 'complete' and axis['runtime'].get('container_removed') is True and
                axis.get('result_disposition', {}).get('checks_verified') is True and 'review_result' in axis,
                'Both axes need completed read-only runs and checks.', 'review_incomplete')
        assignments.require_result_active(axis)
        require(validate_result(axis, axis['submitted_result']) == axis['review_result'], 'Stored axis report changed.', 'invalid_result')
        axes.append(axis)
    require(axes[0]['handoff']['review']['pins'] == axes[1]['handoff']['review']['pins'],
            'Both axes must review the same exact candidate.', 'stale_review')
    return axes


def execution_binding(axis):
    handoff = axis['handoff']
    root = physical(axis['runtime']['artifacts'], directory=True)
    evidence = {}
    for group, names in (('scratch', ['result.json', 'loads.json', 'events.jsonl', 'conversation.json', 'probes.json']),
                         ('inputs', ['worker.py', 'sandbox_relay.py', 'assignment.json', 'handoff.json']),
                         ('', ['container-inspection.json', 'launch-command.json', 'controller-checks.json',
                               'initial-source.json', 'source-artifacts.json'])):
        for name in names:
            path = physical(root / group / name)
            require(path.stat().st_size <= 16_000_000, 'Execution evidence exceeds bound.', 'execution_required')
            evidence[(group + '/' if group else '') + name] = hashlib.sha256(path.read_bytes()).hexdigest()
    model = {}
    for path in sorted((root / 'model-evidence').glob('*.json')):
        path = physical(path)
        require(path.stat().st_size <= 16_000_000, 'Model evidence exceeds bound.', 'execution_required')
        model[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    require(any(name.startswith('response-') for name in model), 'Admitted model responses required.', 'execution_required')
    return {'assignment_id': axis['assignment_id'], 'run_id': axis['runtime']['run_id'],
            'handoff_digest': axis['handoff_digest'], 'pins': handoff['review']['pins'],
            'axis': handoff['review']['axis'], 'report_sha256': axis['review_result']['report_sha256'],
            'skills': [{k: s[k] for k in ('name', 'source', 'sha256', 'dependencies')} for s in handoff['skills']],
            'adaptation_sha256': hashlib.sha256(handoff['adaptation'].encode()).hexdigest(),
            'loads_sha256': assignments.digest(axis['submitted_result']['loads']),
            'work_sha256': assignments.digest(axis['submitted_result']['work']), 'evidence': evidence, 'model_evidence': model}


def authorization(path, operator, axes):
    path = physical(path)
    info = path.stat()
    require(info.st_uid == os.getuid() and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 8_000_000,
            'Host-owned private review authorization required.', 'approval_required')
    for axis in axes:
        for scope in (axis['handoff']['workspace'], axis['handoff']['profile']['home'], axis['runtime']['artifacts']):
            require(not path.is_relative_to(physical(scope, directory=True) if Path(scope).exists() else Path(scope)),
                    'Authorization must not be worker-mounted.', 'approval_required')
        root = physical(axis['runtime']['artifacts'], directory=True)
        require(root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) == 0o700,
                'Private host evidence envelope required.', 'execution_required')
    config = simplification.object_json(path.read_text(), 'invalid_review')
    require(set(config) == {'operator_id', 'execution_reference', 'axes', 'policy_decision'} and
            config['operator_id'] == operator and text(config['execution_reference']) and
            config['axes'] == [execution_binding(axis) for axis in axes],
            'Independent host verification must bind the exact executions, not worker assertions.', 'execution_required')
    decision = config['policy_decision']
    require(decision is None or (isinstance(decision, dict) and set(decision) == {'reference', 'paths', 'findings'} and
            text(decision['reference']) and isinstance(decision['paths'], list) and all(text(v) for v in decision['paths']) and
            isinstance(decision['findings'], list) and all(text(v) for v in decision['findings'])),
            'Policy changes need an explicit exact operator decision.', 'invalid_review')
    return config


def evaluate(database, project, iteration, operator, request, github, config_path=None):
    assignments.current(database, project, iteration, operator)
    axes = pair(database, project, iteration, operator, request, github)
    reports = {a['handoff']['review']['axis']: a['review_result'] for a in axes}
    findings = [{'axis': axis, **finding} for axis, report in reports.items() for finding in report['findings']]
    sensitive = sorted({p for a in axes for p in a['handoff']['review']['policy_paths']})
    weakening = sorted(f['axis'] + ':' + f['id'] for f in findings if f['kind'] in {'gate-weakening', 'security-weakening'})
    config = authorization(config_path, operator, axes) if config_path else None
    if config is None and axes[0].get('review_authorization') is not None:
        saved = axes[0]['review_authorization']
        require(saved == axes[1].get('review_authorization') and saved.get('operator_id') == operator and
                text(saved.get('execution_reference')) and saved.get('axes') == [execution_binding(a) for a in axes],
                'Saved host authorization is stale.', 'execution_required')
        config = saved
    decision = config['policy_decision'] if config else None
    policy_held = bool(sensitive or weakening) and not (decision and decision['paths'] == sensitive and decision['findings'] == weakening)
    stuck_corrections = [child for child in assignments.rows(database)
                         if child['handoff'].get('corrections', {}).get('pins') == axes[0]['handoff']['review']['pins'] and
                         child.get('runtime', {}).get('status') == 'complete' and
                         child.get('submitted_result', {}).get('status') == 'stuck']
    status = ('operator-decision' if policy_held else 'diagnosis' if stuck_corrections or any(r['verdict'] == 'stuck' for r in reports.values())
              else 'corrections' if findings else 'accepted' if config else 'execution-unverified')
    result = {'contract': CONTRACT, 'pins': axes[0]['handoff']['review']['pins'], 'axes': reports,
              'status': status, 'findings': findings, 'policy_paths': sensitive, 'policy_findings': weakening,
              'debug_evidence': [{'assignment_id': c['assignment_id'], 'runtime': c['runtime'],
                                  'result': c['submitted_result']} for c in stuck_corrections],
              'execution_verified': config is not None, 'advance_allowed': status == 'accepted',
              'merge_allowed': False, 'close_allowed': False,
              'execution_reference': config['execution_reference'] if config else None}
    if config_path:
        for axis in axes:
            axis['review_authorization'] = config
            database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(axis, sort_keys=True), axis['assignment_id']))
        assignments.current(database, project, iteration, operator)
        for axis in axes:
            assignments.require_result_active(axis)
        database.commit()
    return result


def prepare_corrections(database, project, iteration, operator, request, github, dry_run=False):
    require(isinstance(request, dict) and set(request) == {'standards_assignment', 'spec_assignment', 'profile', 'skills', 'claim_id'},
            'Corrections require exact separate findings and a fresh implementation context.', 'invalid_review')
    pair_request = {k: request[k] for k in ('standards_assignment', 'spec_assignment')}
    feedback = evaluate(database, project, iteration, operator, pair_request, github)
    require(feedback['status'] == 'corrections', 'Policy decisions and stuckness cannot be bypassed by corrections.', 'corrections_held')
    axes = pair(database, project, iteration, operator, pair_request, github)
    bounded = {k: axes[0]['handoff'][k] for k in assignments.REQUEST_FIELDS}
    bounded.update(stage='corrections', profile=request['profile'], skills=request['skills'], claim_id=request['claim_id'],
                   capabilities=['read_workspace', 'scratch', 'model', 'write_workspace'],
                   preceding=[simplification.artifact('candidate', bounded['candidate']),
                              simplification.artifact('findings', simplification.handoff_json({'reviews': pair_request, 'feedback': feedback}))])
    return assignments.prepare(database, project, iteration, operator, bounded, github, dry_run)


def corrections_handoff(database, project, iteration, operator, request, github):
    evidence = simplification.handoff_evidence(next(a['content'] for a in request['preceding'] if a['name'] == 'findings'))
    require(set(evidence) == {'reviews', 'feedback'}, 'Controller-correlated review feedback required.', 'invalid_review')
    feedback = evaluate(database, project, iteration, operator, evidence['reviews'], github)
    require(feedback == evidence['feedback'] and feedback['status'] == 'corrections', 'Exact unresolved findings required.', 'corrections_held')
    axes = pair(database, project, iteration, operator, evidence['reviews'], github)
    require(all(request[k] == axes[0]['handoff'][k] for k in
                ('repository', 'repository_id', 'workspace', 'issue', 'spec_commit', 'baseline', 'candidate', 'standards')),
            'Corrections must retain exact rejected scope and requirements.', 'scope_mismatch')
    return {'pins': feedback['pins'], 'reviews': evidence['reviews'], 'findings': feedback['findings'],
            'verification_commands': axes[0]['handoff']['review']['verification_commands'],
            'rerun_required': ['implementation-checks', 'simplify', 'Standards', 'Spec'],
            'advance_allowed': False}
