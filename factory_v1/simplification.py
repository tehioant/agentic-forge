"""Controller-owned implementation → simplification seam; no review or merge authority."""
import difflib
import hashlib
import json
import shutil
from pathlib import Path

from . import assignments
from .planning import require, text
from .repositories import RepositoryError
from .sandbox_source import manifest, physical

ANGLES = {'reuse', 'quality', 'efficiency', 'altitude'}
CONTRACT = 'factory-simplification-v1'


def artifact(name, content):
    return {'name': name, 'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest()}


def object_json(content, code):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate evidence fields.', code)
            result[key] = value
        return result
    try:
        require(isinstance(content, str) and len(content.encode()) <= 16_000_000,
                'Bounded JSON evidence required.', code)
        result = json.loads(content, object_pairs_hook=unique)
        require(isinstance(result, dict), 'Evidence must be an object.', code)
        return result
    except (ValueError, RecursionError) as error:
        raise RepositoryError(code, 'Malformed stage evidence.') from error


def changed_paths(before, after):
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def check_receipt(root, runtime):
    """Read private controller output, never a worker-supplied test assertion."""
    source = manifest(root / 'workspace')
    receipt_path = physical(root / 'controller-checks.json')
    require(receipt_path.stat().st_size <= 16_000_000, 'Check receipt exceeds its bound.', 'implementation_unverified')
    raw = receipt_path.read_text()
    receipt = object_json(raw, 'implementation_unverified')
    records = receipt.get('records')
    require(receipt.get('run_id') == runtime.get('run_id') and receipt.get('candidate') == source and
            receipt.get('provenance') == 'controller_admitted_read_only_sandbox_checks' and
            isinstance(records, list) and 0 < len(records) <= 8 and all(isinstance(record, dict) and
            text(record.get('command')) and type(record.get('exit_code')) is int and record['exit_code'] == 0 and
            record.get('output_truncated') is False and all(isinstance(record.get(key), str)
            for key in ('stdout', 'stderr')) for record in records),
            'Passing controller checks must pin this exact candidate.', 'implementation_unverified')
    return source, raw, receipt


def implementation(database, project, iteration, operator, assignment_id):
    prior = assignments.inspect(database, project, iteration, operator, assignment_id)
    assert prior is not None
    runtime = prior.get('runtime', {})
    result = prior.get('submitted_result', {})
    require(prior['handoff']['stage'] in {'implementation', 'corrections', 'repair'} and
            runtime.get('status') == 'complete' and runtime.get('container_removed') is True and
            result.get('status') == 'done' and result.get('run_id') == runtime.get('run_id') and
            prior.get('result_disposition', {}).get('checks_verified') is True,
            'A completed implementation with actual controller checks is required.', 'implementation_unverified')
    try:
        root = physical(runtime['artifacts'], directory=True)
        assignments.require_result_active(prior)
        source, raw, receipt = check_receipt(root, runtime)
        require(runtime.get('candidate_sha256') == assignments.digest(source) and
                runtime.get('checks_sha256') == hashlib.sha256(raw.encode()).hexdigest(),
                'Implementation tree or private check evidence drifted.', 'implementation_unverified')
        baseline = manifest(root / 'inputs' / 'baseline')
        initial = object_json(physical(root / 'initial-source.json').read_text(), 'implementation_unverified')
        require(initial.get('baseline') == baseline and runtime.get('baseline_sha256') == assignments.digest(baseline), 'Implementation baseline drifted.', 'implementation_unverified')
    except (OSError, KeyError, UnicodeError) as error:
        raise RepositoryError('implementation_unverified', 'Pinned implementation artifacts unavailable.') from error
    return prior, root, source, baseline, receipt


def diff_artifact(root, before, after):
    entries = []
    for path in changed_paths(before, after):
        old = (root / 'inputs' / 'baseline' / path).read_bytes() if path in before else b''
        new = (root / 'workspace' / path).read_bytes() if path in after else b''
        try:
            diff = ''.join(difflib.unified_diff(old.decode('utf-8').splitlines(keepends=True),
                new.decode('utf-8').splitlines(keepends=True), fromfile='baseline/' + path, tofile='implementation/' + path))
        except UnicodeError:
            diff = 'Binary change: inspect the pinned baseline and implementation bytes.'
        entries.append({'path': path, 'before': before.get(path), 'after': after.get(path), 'diff': diff})
    return artifact('implementation-diff', json.dumps(entries, sort_keys=True))


def preceding(prior, root, source, baseline, receipt):
    evidence = {'assignment_id': prior['assignment_id'], 'handoff_digest': prior['handoff_digest'],
                'run_id': prior['runtime']['run_id'], 'candidate_sha256': assignments.digest(source),
                'baseline_sha256': assignments.digest(baseline), 'checks_sha256': prior['runtime']['checks_sha256'],
                'checks': receipt, 'implementation_result': prior['submitted_result'],
                'native_execution_trusted': False}
    return [artifact('candidate', prior['handoff']['candidate'] or prior['handoff']['baseline']),
            artifact('implementation-evidence', json.dumps(evidence, sort_keys=True)),
            diff_artifact(root, baseline, source)]


def prepare(database, project, iteration, operator, request, github, dry_run=False):
    require(isinstance(request, dict) and set(request) == {'implementation_assignment', 'profile', 'skills', 'claim_id'} and
            assignments.sha(request['implementation_assignment']),
            'Supply the preceding assignment and a fresh simplifier profile/skills/claim.', 'invalid_assignment')
    prior, root, source, baseline, receipt = implementation(database, project, iteration, operator, request['implementation_assignment'])
    assert prior is not None
    bounded = {key: prior['handoff'][key] for key in assignments.REQUEST_FIELDS}
    bounded.update(stage='simplify', profile=request['profile'], skills=request['skills'], claim_id=request['claim_id'],
                   candidate=prior['handoff']['candidate'] or prior['handoff']['baseline'],
                   preceding=preceding(prior, root, source, baseline, receipt))
    return assignments.prepare(database, project, iteration, operator, bounded, github, dry_run)


def handoff(database, project, iteration, operator, request):
    """Also guard generic prepare-assignment; arbitrary preceding prose cannot bypass the seam."""
    supplied = next(a for a in request['preceding'] if a['name'] == 'implementation-evidence')
    evidence = object_json(supplied['content'], 'implementation_unverified')
    require(assignments.sha(evidence.get('assignment_id')), 'Exact previous implementation assignment required.', 'implementation_unverified')
    prior, root, source, baseline, receipt = implementation(database, project, iteration, operator, evidence['assignment_id'])
    assert prior is not None
    original = prior['handoff']
    fields = {'repository', 'repository_id', 'workspace', 'issue', 'spec_commit', 'baseline', 'standards'}
    require(all(request[key] == original[key] for key in fields) and
            request['candidate'] == (original['candidate'] or original['baseline']) and
            request['preceding'] == preceding(prior, root, source, baseline, receipt),
            'Simplification must retain exact original requirements, standards, baseline, diff and checked implementation.', 'scope_mismatch')
    return {'contract': CONTRACT, 'implementation_assignment': prior['assignment_id'],
            'implementation_run': prior['runtime']['run_id'], 'input_candidate_sha256': assignments.digest(source),
            'baseline_sha256': assignments.digest(baseline), 'scope': changed_paths(baseline, source),
            'verification_commands': [record['command'] for record in receipt['records']],
            'result_artifact': 'simplification-result',
            'candidate_revision_format': 'sha256 of canonical UTF-8 JSON (sort_keys=True, ensure_ascii=False, separators=(comma,colon)) of the workspace manifest: relative path → sha256, bytes, executable (boolean). No .git; executable means any execute bit. Use actual file bytes and a terminal tool to compute it.',
            'angles': sorted(ANGLES),
            'outcomes': ['cleanup', 'no-op', 'findings'],
            'behavior_preservation_values': ['preserved', 'not-established'],
            'finding_fields': ['kind', 'path', 'evidence', 'proposal'],
            'finding_kinds': ['behavior', 'out-of-scope', 'correctness'],
            'result_fields': ['contract', 'adaptation', 'input_candidate_sha256', 'candidate_sha256',
                              'outcome', 'behavior_preservation', 'angles', 'findings'],
            'native_execution_trusted': False, 'review_required': ['Standards', 'Spec']}


def copy_candidate(database, project, iteration, operator, assignment, destination, pins):
    contract = assignment['handoff']['simplification']
    prior, root, source, baseline, _ = implementation(database, project, iteration, operator, contract['implementation_assignment'])
    require(assignments.digest(source) == contract['input_candidate_sha256'] and pins['baseline'] == baseline,
            'Prepared simplification source or baseline changed.', 'scope_mismatch')
    shutil.rmtree(destination / 'workspace')
    shutil.copytree(root / 'workspace', destination / 'workspace')
    require(manifest(destination / 'workspace') == source, 'Copied implementation changed.', 'scope_mismatch')
    pins['workspace'] = source


def validate_result(assignment, result):
    handoff = assignment['handoff']
    contract = handoff['simplification']
    runtime = assignment.get('runtime', {})
    require(result['run_id'] == runtime.get('run_id'), 'Only the correlated sandbox result may submit simplification.', 'invalid_result')
    try:
        root = physical(runtime['artifacts'], directory=True)
        source, _, receipt = check_receipt(root, runtime)
        initial = object_json(physical(root / 'initial-source.json').read_text(), 'invalid_result')['workspace']
    except (OSError, KeyError, UnicodeError) as error:
        raise RepositoryError('invalid_result', 'Simplification requires private resulting-candidate checks.') from error
    require(assignments.digest(initial) == contract['input_candidate_sha256'] and
            [record['command'] for record in receipt['records']] == contract['verification_commands'],
            'Candidate and check commands must match the prepared stage.', 'invalid_result')
    changed = changed_paths(initial, source)
    require(set(changed) <= set(contract['scope']), 'Simplifier changed paths outside the ticket diff.', 'scope_violation')
    report_artifact = next((a for a in result['artifacts'] if a['name'] == 'simplification-result'), None)
    require(report_artifact is not None, 'Structured simplification result required.', 'invalid_result')
    assert report_artifact is not None
    report = object_json(report_artifact['content'], 'invalid_result')
    require(set(report) == set(contract['result_fields']) and report['contract'] == CONTRACT and
            report['adaptation'] == handoff['adaptation'] and report['input_candidate_sha256'] == contract['input_candidate_sha256'] and
            report['candidate_sha256'] == assignments.digest(source) and
            report['outcome'] in ('cleanup', 'no-op', 'findings') and report['behavior_preservation'] in ('preserved', 'not-established') and
            isinstance(report['angles'], dict) and set(report['angles']) == ANGLES and all(text(value) for value in report['angles'].values()) and
            isinstance(report['findings'], list) and all(isinstance(finding, dict) and
            set(finding) == {'kind', 'path', 'evidence', 'proposal'} and finding['kind'] in ('behavior', 'out-of-scope', 'correctness') and
            all(text(finding[key]) for key in ('path', 'evidence', 'proposal')) for finding in report['findings']),
            'Exact pins, four-angle work and structured findings required.', 'invalid_result')
    if report['outcome'] == 'findings':
        require(report['findings'] and not changed, 'Findings must not silently apply risky proposals.', 'invalid_result')
    else:
        require(result['status'] == 'done' and not report['findings'] and report['behavior_preservation'] == 'preserved' and
                bool(changed) == (report['outcome'] == 'cleanup'),
                'Approval-like output cannot include findings, altered behavior or a false no-op.', 'invalid_result')
    return {'contract': CONTRACT, 'outcome': report['outcome'], 'candidate_sha256': assignments.digest(source),
            'input_candidate_sha256': contract['input_candidate_sha256'], 'changed_paths': changed,
            'checks_verified': True, 'behavior_preservation_claim': report['behavior_preservation'],
            'native_execution_trusted': False, 'approval': False, 'advance_allowed': False,
            'review_required': ['Standards', 'Spec'], 'findings': report['findings'],
            'reason': 'findings_require_rework' if report['findings'] else 'native_execution_unattested_and_independent_review_required'}
