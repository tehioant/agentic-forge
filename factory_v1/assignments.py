"""Bounded assignment admission; no worker execution or authoritative stage completion."""
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from . import role_skills, tickets
from .model_access import SUBSCRIPTION_SCOPE
from .planning import require, text
from .repositories import RepositoryError
from .spending import SpendingError, admission_preview

ROLES = {
    'implementation': 'implementation', 'corrections': 'implementation',
    'simplify': 'simplification', 'review-standards': 'review', 'review-spec': 'review',
    'diagnosis': 'debug', 'repair': 'repair',
}
PRECEDING = {
    'implementation': set(), 'corrections': {'candidate', 'findings'},
    'simplify': {'candidate', 'implementation-evidence', 'implementation-diff'},
    'review-standards': {'candidate', 'simplification-evidence'},
    'review-spec': {'candidate', 'simplification-evidence'},
    'diagnosis': {'failure-evidence'}, 'repair': {'incident-evidence'},
}
CONTRACT = 'factory-bounded-result-v1'
REQUEST_FIELDS = {'stage', 'profile', 'repository', 'repository_id', 'workspace', 'issue',
                  'spec_commit', 'baseline', 'candidate', 'standards', 'preceding', 'skills',
                  'capabilities', 'result_contract', 'claim_id'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def commit(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}', value) is not None


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value) is not None


def absolute(value):
    return (isinstance(value, str) and '\x00' not in value and not value.startswith('//') and
            Path(value).is_absolute() and str(Path(value)) == value and '..' not in Path(value).parts)


def artifacts(values, required, code='invalid_assignment'):
    require(isinstance(values, list), 'Supply explicit immutable artifacts.', code)
    names = set()
    for value in values:
        require(isinstance(value, dict) and set(value) == {'name', 'content', 'sha256'} and
                identifier(value['name']) and value['name'] not in names and text(value['content']) and
                sha(value['sha256']) and hashlib.sha256(value['content'].encode()).hexdigest() == value['sha256'],
                'Artifact names must be unique and actual content must match its pin.', code)
        names.add(value['name'])
    require(required <= names, 'Missing required preceding artifacts or standards.', code)


def validate(request):
    require(isinstance(request, dict) and set(request) == REQUEST_FIELDS, 'Provide only the bounded assignment fields.', 'invalid_assignment')
    stage = request['stage']
    role_skills.closure(stage)
    profile = request['profile']
    require(isinstance(profile, dict) and set(profile) == {'name', 'home', 'role', 'provider', 'model', 'operation', 'reasoning'} and
            identifier(profile['name']) and profile['name'] not in {'default', 'personal'} and
            absolute(profile['home']) and profile['role'] == ROLES[stage],
            'Explicit dedicated role profile, not an ambient personal profile, required.', 'profile_blocked')
    require((profile['provider'], profile['model'], profile['operation']) == SUBSCRIPTION_SCOPE and
            profile['reasoning'] in ('low', 'medium', 'high', 'xhigh'),
            'Only the existing approved provider/model/operation with explicit reasoning is supported; no fallback.', 'model_blocked')
    issue = request['issue']
    require(isinstance(issue, dict) and set(issue) == {'number', 'id', 'node_id', 'body_sha256'} and
            type(issue['number']) is int and issue['number'] > 0 and type(issue['id']) is int and issue['id'] > 0 and
            text(issue['node_id']) and sha(issue['body_sha256']) and
            type(request['repository_id']) is int and request['repository_id'] > 0 and
            text(request['repository']) and absolute(request['workspace']) and
            request['workspace'] != '/' and profile['home'] != request['workspace'] and
            commit(request['spec_commit']) and commit(request['baseline']) and
            (request['candidate'] is None or commit(request['candidate'])) and identifier(request['claim_id']),
            'Exact repository/workspace, issue, full revision pins and stable claim identity required.', 'invalid_assignment')
    if stage in {'corrections', 'simplify', 'review-standards', 'review-spec'}:
        require(commit(request['candidate']), 'This stage needs an exact candidate commit.', 'invalid_assignment')
    artifacts(request['standards'], {'AGENTS.md'})
    artifacts(request['preceding'], PRECEDING[stage])
    capabilities = ['read_workspace', 'scratch', 'model']
    if stage in {'implementation', 'corrections', 'simplify', 'repair'}:
        capabilities.append('write_workspace')
    require(request['capabilities'] == capabilities and request['result_contract'] == CONTRACT,
            'No broad credentials, publication, merge, protection changes or out-of-role capabilities.', 'capability_blocked')
    return role_skills.snapshots(stage, request['skills'])


class ReadOnlyGitHub:
    """Allow metadata queries, including GraphQL reads; deny all mutations."""
    def __init__(self, github):
        self.github = github
        self.base = github.base

    def request(self, path, payload=None, method=None):
        require((payload is None and method in {None, 'GET'}) or
                (path == '/graphql' and isinstance(payload, dict) and
                 isinstance(payload.get('query'), str) and payload['query'].lstrip().startswith('query ') and
                 method in {None, 'POST'}),
                'Assignment eligibility may only query configured GitHub metadata.', 'capability_blocked')
        return self.github.request(path, payload, method) if method else self.github.request(path, payload)


def rows(database):
    if not database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='role_assignments'").fetchone():
        return []
    return [json.loads(row[0]) for row in database.execute('SELECT payload FROM role_assignments')]


def current(database, project, iteration, operator, require_active=True):
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?', (project, iteration)).fetchone()
    require(row is not None, 'Exact registered iteration required.', 'not_found')
    item = json.loads(row[0])
    require(item.get('approval', {}).get('operator_id') == operator and text(item.get('approval', {}).get('reference')),
            'Trusted configured operator approval required.', 'approval_required')
    require(item.get('execution_allowed') is False and (not require_active or item.get('status') == 'active'),
            'Paused or unsafe iteration cannot admit assignments.', 'iteration_paused')
    if require_active:
        from .controls import require_active as control_active
        control_active(database, project, iteration)
    return item


def completed_iteration_claim(database, assignment):
    """Only verified finite completion retires old engineering ownership, not tracker closure."""
    scope = assignment['ticket_scope']
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                           (scope['project'], scope['iteration'])).fetchone()
    item = json.loads(row[0]) if row else {}
    completion = item.get('completion', {})
    report = completion.get('report', {})
    runtime = assignment.get('runtime', {})
    return bool(item.get('status') == 'waiting-for-operator' and
                completion.get('report_id') == digest(report) and
                runtime.get('status') == 'complete' and runtime.get('container_removed') is True and
                any(work['issue']['number'] == scope['issue_number'] and work['issue']['id'] == scope['issue_id']
                    for work in report.get('delivered', [])))


def retirement_record_digest(assignment):
    """Seal delivery authority, not restart's current-main ancestry observation."""
    record = dict(assignment)
    journal = record.get('merge')
    if journal is not None:
        evidence = journal['integrated_evidence']
        require(isinstance(evidence, dict) and commit(evidence.get('main')) and
                evidence.get('membership') in {'identical', 'ahead'} and
                (evidence['membership'] == 'identical') == (evidence['main'] == evidence['integrated']),
                'Known integrated membership observation required.', 'invalid_retirement')
        record['merge'] = dict(journal, integrated_evidence={k: v for k, v in evidence.items()
                                                          if k not in {'main', 'membership'}})
    return digest(record)


def delivered_ticket_proof(database, item, delivered, authority, lineage=None):
    """Correlate retained delivery and absent workers without external reads."""
    from . import bugs, diagnosis, recovery, requirements
    number = delivered['issue']['number']
    held = (requirements.held_numbers(item) | diagnosis.held_numbers(item) |
            bugs.held_numbers(item) | recovery.held_numbers(item))
    require(number not in held and not item.get('main_incident') and not recovery.pending_registration(item),
            'Delivery ownership remains held.', 'delivery_held')
    records = {a['assignment_id']: a for a in rows(database)}
    candidate = records.get(delivered['assignment_id'])
    require(candidate is not None, 'Delivered candidate is missing.', 'invalid_retirement')
    assert candidate is not None
    scope = candidate['ticket_scope']
    issue = candidate['handoff']['issue']
    require(scope['project'] == item['project_id'] and scope['iteration'] == item['iteration_id'] and
            scope['repository'] == item['repository'] == authority['repository'] and
            scope['repository_id'] == item['repository_onboarding']['metadata']['id'] == authority['repository_id'] and
            scope['issue_number'] == number == authority['issue'] == issue['number'] and
            scope['issue_id'] == delivered['issue']['id'] == issue['id'] and
            candidate['revision'] == delivered['requirements'] == requirements.authority(item, number) and
            authority['candidate_assignment'] == candidate['assignment_id'] and
            authority['head'] == delivered['integration']['candidate'] and
            delivered['reviews'] == [authority['standards_assignment'], authority['spec_assignment']],
            'Exact delivered ticket/revision/candidate authority required.', 'invalid_retirement')
    journal = candidate.get('merge', {})
    require(journal.get('status') == 'delivered' and journal.get('failure') is None and
            journal.get('close_allowed') is True and journal.get('authority') == authority and
            all(journal.get('integrated_evidence', {}).get(k) == delivered['integration'][k]
                for k in ('candidate', 'integrated', 'tree', 'parents')) and
            journal.get('delivery_checks') == delivered['checks'],
            'Correlated integrated delivery proof required.', 'invalid_retirement')
    if lineage is None:
        lineage = sorted(a['assignment_id'] for a in records.values() if a['ticket_scope'] == scope)
    require(candidate['assignment_id'] in lineage and set(delivered['reviews']) <= set(lineage),
            'Delivery lineage is incomplete.', 'invalid_retirement')
    pins = {}
    for identity in lineage:
        a = records.get(identity)
        require(a is not None and a['ticket_scope'] == scope and digest(a['handoff']) == a['handoff_digest'],
                'Retired assignment/handoff identity changed.', 'invalid_retirement')
        assert a is not None
        runtime = a.get('runtime')
        require((runtime is None and not a.get('submitted_result') and
                 (requirements.released_claim(database, a) or diagnosis.released_claim(database, a))) or
                (isinstance(runtime, dict) and runtime.get('status') in {'complete', 'stopped', 'failed'} and
                 runtime.get('container_removed') is True and runtime.get('error') != 'stop_unconfirmed'),
                'Every launched lineage worker needs verified terminal cleanup.', 'cleanup_required')
        pins[identity] = {'handoff_digest': a['handoff_digest'], 'claim_id': a['claim_id'],
                          'revision': a['revision'], 'run_id': (runtime or {}).get('run_id'),
                          'record_sha256': retirement_record_digest(a)}
    work = item['ticket_work']
    reservations = [*item.get('reservation_history', []), item.get('reservation', {})]
    reservation = next((r for r in reversed(reservations) if r.get('issue_number') == number and
                        r.get('status') == 'reserved' and r.get('assignment_id') == work['assignment_id'] and
                        r.get('input_digest') == work['input_digest']), None)
    require(reservation is not None, 'Exact retained reservation required.', 'invalid_retirement')
    return {'reservation': reservation, 'scope': scope, 'issue': issue, 'revision': candidate['revision'],
            'candidate': candidate['assignment_id'], 'merge_authority': authority,
            'delivered': delivered, 'lineage': pins}


def delivered_ticket_receipt(database, assignment):
    """Retained history forbids reuse even after its release authority becomes obsolete."""
    scope = assignment['ticket_scope']
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                           (scope['project'], scope['iteration'])).fetchone()
    item = json.loads(row[0]) if row else {}
    return item, next((r for r in item.get('delivered_ticket_retirements', {}).values()
                       if assignment['assignment_id'] in r.get('proof', {}).get('lineage', {})), None)


def delivered_ticket_claim(database, assignment):
    """Only a correlated per-ticket receipt retires ownership; never perform I/O."""
    item, receipt = delivered_ticket_receipt(database, assignment)
    if receipt is None:
        return False
    try:
        proof = receipt['proof']
        return bool(proof['lineage'][assignment['assignment_id']]['record_sha256'] == retirement_record_digest(assignment) and
                    receipt.get('receipt_id') == digest(proof) and
                    proof == delivered_ticket_proof(database, item, proof['delivered'],
                                                   proof['merge_authority'], proof['lineage']))
    except (RepositoryError, KeyError, TypeError, ValueError):
        return False


def ticket_claims(database, scope):
    """Check durable role ownership for every supplied ticket identity field."""
    from .diagnosis import released_claim
    from .requirements import released_claim as revised_claim
    from .recovery import released_claim as recovery_claim
    from .restart import released_claim as recovered_claim
    existing = [old for old in rows(database) if old['handoff']['stage'] != 'diagnosis' and
                not released_claim(database, old) and not revised_claim(database, old) and
                not completed_iteration_claim(database, old) and not delivered_ticket_claim(database, old) and
                not recovery_claim(database, old) and
                not recovered_claim(old)]
    for old in existing:
        require(all(old['ticket_scope'].get(key) == value for key, value in scope.items()),
                'One active ticket across the factory; stale claims are never automatically stolen.', 'claim_conflict')
    return existing


def claims(database, request, scope, assignment_id):
    existing = rows(database)
    if request['stage'] != 'diagnosis':
        ticket_claims(database, scope)
    from .requirements import released_claim as revised_claim
    from .restart import released_claim as recovered_claim
    from .recovery import released_claim as recovery_claim
    from .diagnosis import released_claim as directed_claim
    for old in existing:
        if delivered_ticket_receipt(database, old)[1] is not None:
            require(old['assignment_id'] != assignment_id and old['claim_id'] != request['claim_id'],
                    'Delivered attempts require fresh claims and assignments.', 'claim_conflict')
            require(old['handoff']['profile']['name'] != request['profile']['name'] and
                    old['handoff']['profile']['home'] != request['profile']['home'],
                    'Delivered profiles are historical, never reusable.', 'profile_claim_conflict')
            if delivered_ticket_claim(database, old):
                continue
        if recovered_claim(old):
            require(old['assignment_id'] == assignment_id or old['claim_id'] != request['claim_id'],
                    'Infrastructure recovery requires a fresh claim identity.', 'claim_conflict')
            continue
        if revised_claim(database, old) or completed_iteration_claim(database, old):
            continue
        if old['assignment_id'] == assignment_id:
            continue
        require(not (not recovery_claim(database, old) and not directed_claim(database, old) and
                     request['stage'] in {'implementation', 'corrections', 'simplify', 'repair'} and
                     old['handoff']['stage'] in {'implementation', 'corrections', 'simplify', 'repair'} and
                     old.get('runtime', {}).get('status') in {'preparing', 'running', 'unconfirmed', 'stopped', 'failed'}),
                'Existing modifying attempt needs exact cleanup and explicit recovery, not a concurrent writer.', 'claim_conflict')
        require(old['claim_id'] != request['claim_id'], 'Claim identity already binds a different assignment.', 'claim_conflict')
        old_profile = old['handoff']['profile']
        profile = request['profile']
        require(old_profile['name'] != profile['name'] and old_profile['home'] != profile['home'],
                'One top-level worker assignment per dedicated profile.', 'profile_claim_conflict')
    if request['stage'] == 'diagnosis':
        return
    for row in database.execute('SELECT payload FROM iterations'):
        item = json.loads(row[0])
        reservation = item.get('reservation')
        if reservation:
            require((item['project_id'], item['iteration_id'], item['repository'], reservation.get('issue_number')) ==
                    (scope['project'], scope['iteration'], scope['repository'], scope['issue_number']) and
                    reservation.get('status') != 'invalidated' and
                    reservation.get('assignment_id') == item.get('ticket_work', {}).get('assignment_id') and
                    reservation.get('input_digest') == item.get('ticket_work', {}).get('input_digest'),
                    'Existing ticket reservation conflicts or was invalidated.', 'claim_conflict')


def prepare(database, project, iteration, operator, request, github, dry_run=False, *, diagnosis_revalidation=None):
    """Validate startup before read-only frontier queries, and persist only local assignments."""
    selected = validate(request)
    # Pin the same canonical closure used in handoff/replay, including repair's
    # two entry points; caller descriptor order cannot change a claim identity.
    request = {**request, 'skills': [{k: v for k, v in s.items() if k != 'instructions'} for s in selected]}
    item = current(database, project, iteration, operator)
    if not dry_run:
        from .restart import before_dispatch
        before_dispatch(database, project, iteration, operator, github, request=request)
        item = current(database, project, iteration, operator)
    from . import recovery
    recovery.admission(item, request['stage'], request['issue']['number'])
    recovery_contract = recovery.handoff(database, item, request) if request['stage'] == 'repair' else None
    simplification = None
    review_contract = None
    corrections_contract = None
    diagnosis_contract = None
    if request['stage'] == 'diagnosis':
        from .diagnosis import handoff as diagnosis_handoff
        diagnosis_contract = diagnosis_handoff(database, project, iteration, operator, request)
    if request['stage'] in {'review-standards', 'review-spec'}:
        from .review import handoff as review_handoff
        review_contract = review_handoff(database, project, iteration, operator, request)
    if request['stage'] == 'corrections':
        from .review import corrections_handoff
        corrections_contract = corrections_handoff(database, project, iteration, operator, request, github)
    if request['stage'] == 'simplify':
        from .simplification import handoff as simplify_handoff
        simplification = simplify_handoff(database, project, iteration, operator, request)
    if recovery_contract is None and recovery.recovery_number(item) == request['issue']['number'] and request['stage'] != 'diagnosis':
        if simplification:
            parent = simplification['implementation_assignment']
        elif review_contract:
            parent = review_contract['pins']['candidate_assignment']
        elif corrections_contract:
            parent = corrections_contract['pins']['candidate_assignment']
        else:
            parent = None
        require(parent is not None, 'Recovery stage requires controller-prepared ancestry.', 'recovery_held')
        prior = inspect(database, project, iteration, operator, parent)
        root = recovery.lineage(database, prior)
        recovery_record = recovery.active(item)
        assert recovery_record is not None
        require(root['assignment_id'] == recovery_record['assignment'], 'Obsolete recovery ancestor.', 'recovery_held')
        recovery_contract = root['handoff']['recovery']
    from .requirements import authority, inputs, held_numbers
    number = request['issue']['number']
    require(number not in held_numbers(item), 'Affected requirements await explicit revision resumption.', 'requirements_held')
    revision = authority(item, number)
    scoped_inputs = inputs(item, number)
    require(request['repository'] == item['repository'] and request['spec_commit'] == revision['spec']['commit'],
            'Wrong repository or stale specification pin.', 'scope_mismatch')
    require(item.get('repository_onboarding', {}).get('status') == 'verified' and
            request['repository_id'] == item['repository_onboarding']['metadata']['id'],
            'Exact verified repository ID required.', 'scope_mismatch')
    profile = request['profile']
    try:
        policy = admission_preview(database, (project, iteration, profile['provider'], profile['model'], profile['operation']))
    except SpendingError as error:
        raise RepositoryError(error.code, str(error)) from error
    scope = {'project': project, 'iteration': iteration, 'repository': item['repository'],
             'repository_id': request['repository_id'], 'issue_number': request['issue']['number'],
             'issue_id': request['issue']['id'], 'workspace': request['workspace']}
    assignment_id = digest({'request': request, 'scope': scope})
    if diagnosis_contract is not None:
        from .diagnosis import debug_claim
        debug_claim(database, item['stuck_work'][diagnosis_contract['pins']['stuck_assignment']], assignment_id)
    diagnosis_statuses = {'blocked'}
    if diagnosis_revalidation is not None:
        require(dry_run and diagnosis_contract is not None and diagnosis_revalidation == assignment_id,
                'Release reconciliation cannot admit new assignments.', 'diagnosis_held')
        from .diagnosis import execution_binding
        assert diagnosis_contract is not None
        record = item['stuck_work'][diagnosis_contract['pins']['stuck_assignment']]
        axis = inspect(database, project, iteration, operator, assignment_id)
        assert axis is not None
        require(record['status'] == 'release-pending' and record['diagnosis_assignment'] == assignment_id and
                record['applied_response'] is not None and record['direction']['action'] == 'retry' and
                axis.get('runtime', {}).get('status') == 'complete' and axis['runtime'].get('container_removed') is True and
                axis.get('result_disposition', {}).get('isolated_execution') is True and
                record.get('authorization', {}).get('diagnosis') == execution_binding(axis),
                'Only exact completed host-authorized diagnosis can reconcile release.', 'execution_required')
        previous_status = record['previous_statuses'][str(request['issue']['number'])]
        diagnosis_statuses.add('ready' if previous_status == 'active' else previous_status)
    claims(database, request, scope, assignment_id)
    # Frontier currently checkpoints. Copy state so query validation never writes lifecycle state.
    with closing(sqlite3.connect(':memory:')) as observation:
        database.backup(observation)
        observed = tickets.frontier(observation, item, ReadOnlyGitHub(github))
    work = observed['ticket_work']
    frontier = work['frontier']
    number = request['issue']['number']
    require(work.get('publication_complete') and not frontier['foreign_active'] and
            (request['stage'] == 'diagnosis' or recovery_contract is not None or set(frontier['active']) <= {number}), 'Conflicting or incomplete authoritative work frontier.', 'ticket_ineligible')
    row = next((row for row in frontier['items'] if row['number'] == number), None)
    require(row is not None and (row['admissible'] if request['stage'] != 'diagnosis' else
            row['issue_state'] == 'open' and row['held'] and row['status'] in diagnosis_statuses) and
            all(row[key] == request['issue'][key] for key in ('id', 'node_id')),
            'Issue is blocked, wrong identity or outside the current eligible frontier.', 'ticket_ineligible')
    issue = tickets.gh.read_issue(ReadOnlyGitHub(github), item['repository'], number)
    require(hashlib.sha256(issue['body'].encode()).hexdigest() == request['issue']['body_sha256'],
            'Issue body changed; prepare a new reviewed scope, never reuse stale evidence.', 'scope_mismatch')
    if request['candidate'] is not None:
        candidate_artifact = next((a for a in request['preceding'] if a['name'] == 'candidate'), None)
        if candidate_artifact:
            require(candidate_artifact['content'] == request['candidate'], 'Candidate artifact differs from pinned head.', 'scope_mismatch')
    directions = [record for record in item.get('stuck_work', {}).values() if record['status'] == 'released' and
                  number in record['affected'] and all(record['issues'][str(number)][key] == request['issue'][key]
                                                       for key in ('id', 'node_id', 'body_sha256'))]
    operator_direction = (max(directions, key=lambda record: record['sequence'])['applied_response']
                          if directions and request['stage'] in {'implementation', 'repair'} else None)
    input_loads = {issue['html_url']: request['issue']['body_sha256']}
    input_loads.update({document['url']: document['sha256'] for document in scoped_inputs['documents'].values()})
    for group in ('standards', 'preceding'):
        input_loads.update({'handoff:' + group + ':' + a['name']: a['sha256'] for a in request[group]})
    support_keys = {'required', 'repository-inputs'}
    for selected_skill in selected:
        key = 'implement-review' if selected_skill['name'] == 'implement' else selected_skill['name']
        if key in role_skills.SUPPORT_POLICY:
            support_keys.add(key)
    handoff = {**request, 'skills': selected, 'issue_body': issue['body'],
               'specification': scoped_inputs, 'adaptation': role_skills.ADAPTATIONS[request['stage']],
               'stage_rules': role_skills.STAGE_RULES[request['stage']],
               'support_policy': {key: role_skills.SUPPORT_POLICY[key] for key in sorted(support_keys)},
               'input_loads': input_loads,
               'skill_entry_points': list(role_skills.STAGES[request['stage']]),
               'tracker': {'api_base': github.base, 'board': work['board'], 'issue_url': issue['html_url']},
               'fresh_context': True, 'inherited_memory': False, 'inherited_sessions': False,
               'credentials': [], 'model_policy': {'scope': policy['scope'], 'reservation': 1, 'recheck_at_call': True},
               'result_requirements': {'identity': ['assignment_id', 'handoff_digest', 'claim_id', 'run_id'],
                   'required_evidence': ['actual-selected-instruction-loads', 'observable-skill-guided-work',
                                         'artifacts', 'tests', 'trusted-whole-process-execution'],
                   'advancement': 'disabled-without-trusted-whole-process-evidence'}}
    if operator_direction is not None:
        handoff['operator_direction'] = operator_direction
    if recovery_contract is not None:
        handoff['recovery'] = recovery_contract
    if revision['spec'] != item['handoff']:
        change = item['requirement_changes'][revision['ticket_assignment']]
        handoff['requirement_change'] = {k: change[k] for k in
            ('requirements', 'old_revision', 'new_revision', 'resolution', 'resume_direction')}
        input_loads['handoff:requirement-change'] = digest(handoff['requirement_change'])
    if diagnosis_contract is not None:
        handoff['diagnosis'] = diagnosis_contract
    if simplification is not None:
        handoff['simplification'] = simplification
    if review_contract is not None:
        handoff['review'] = review_contract
    if corrections_contract is not None:
        handoff['corrections'] = corrections_contract
    result = {'assignment_id': assignment_id, 'claim_id': request['claim_id'], 'ticket_scope': scope,
              'handoff': handoff, 'handoff_digest': digest(handoff), 'assignment_ready': True,
              'launchable': False, 'execution_allowed': False, 'status': 'prepared',
              'refusal': 'whole_process_isolation_unavailable',
              'revision': revision}
    previous = next((old for old in rows(database) if old['assignment_id'] == assignment_id), None)
    if previous:
        require(previous['handoff_digest'] == result['handoff_digest'] and previous['revision'] == result['revision'],
                'Assignment inputs changed; original claim remains held for controller reconciliation.', 'stale_assignment')
        result = previous
    if not dry_run and previous is None:
        from .controls import admission
        with admission(database, project, iteration), database:
            database.execute('BEGIN IMMEDIATE')
            database.execute('CREATE TABLE IF NOT EXISTS role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            database.execute('INSERT INTO role_assignments VALUES (?,?)', (assignment_id, json.dumps(result, sort_keys=True)))
            if request['stage'] == 'repair':
                record = recovery.active(item)
                assert record is not None
                record.update(assignment=assignment_id, status='repair')
                item['main_incident']['recovery_assignment'] = assignment_id
                recovery.checkpoint(database, item)
    return {**result, 'dry_run': dry_run, 'spending_admission': policy, 'claim_persisted': not dry_run or previous is not None}


def inspect(database, project, iteration, operator, assignment_id):
    item = current(database, project, iteration, operator, require_active=False)
    assignment = next((r for r in rows(database) if r['assignment_id'] == assignment_id), None)
    require(assignment is not None and assignment['ticket_scope']['project'] == project and
            assignment['ticket_scope']['iteration'] == iteration, 'Exact assignment identity not found.', 'not_found')
    assert assignment is not None
    from .requirements import authority, held_numbers
    number = assignment['ticket_scope']['issue_number']
    require(not assignment.get('requirement_disposition') and number not in held_numbers(item),
            'Obsolete or held requirements cannot reuse evidence.', 'requirements_held')
    require(assignment['revision'] == authority(item, number),
            'Requirements or ticket synthesis changed; old claim cannot be reused.', 'stale_assignment')
    require(digest(assignment['handoff']) == assignment['handoff_digest'], 'Stored handoff changed.', 'stale_assignment')
    for skill in assignment['handoff']['skills']:
        require(hashlib.sha256(skill['instructions'].encode()).hexdigest() == skill['sha256'],
                'Immutable skill snapshot changed.', 'skill_blocked')
    return assignment


def require_result_active(assignment):
    require(not assignment.get('recovery_disposition'), 'Ordinary work parked for main recovery.', 'main_frozen')
    require(not assignment.get('restart_disposition'), 'Recovered attempt is historical; prepare a fresh scoped claim.', 'stale_assignment')
    require(not assignment.get('requirement_disposition'), 'Obsolete work cannot admit results or merge.', 'requirements_held')
    require(not assignment.get('stuck_disposition'), 'Held or superseded work requires fresh scoped implementation.', 'stuck_held')
    runtime = assignment.get('runtime')
    require(not runtime or not (Path(runtime['artifacts']) / 'stop').exists(),
            'Attempt cancelled before result admission.', 'cancelled')


def store_result(database, project, iteration, operator, assignment_id, request, github, *, sandbox_run_id=None):
    assignment = inspect(database, project, iteration, operator, assignment_id)
    assert assignment is not None
    require(delivered_ticket_receipt(database, assignment)[1] is None,
            'Delivered results are historical, never resubmittable.', 'stale_assignment')
    keys = {'assignment_id', 'handoff_digest', 'claim_id', 'run_id', 'status', 'loads', 'work', 'artifacts', 'tests'}
    require(isinstance(request, dict) and set(request) == keys and request['assignment_id'] == assignment_id and
            request['handoff_digest'] == assignment['handoff_digest'] and request['claim_id'] == assignment['claim_id'] and
            identifier(request['run_id']) and request['status'] in ('done', 'blocked', 'stuck'),
            'Exact correlated result identity and structured evidence required.', 'invalid_result')
    handoff = assignment['handoff']
    expected = {s['source']: s['sha256'] for s in handoff['skills']}
    expected.update(handoff['input_loads'])
    loads = request['loads']
    require(isinstance(loads, list) and len(loads) == len(expected) and
            all(isinstance(load, dict) and set(load) == {'source', 'sha256', 'tool_reference'} and
                isinstance(load['source'], str) and expected.get(load['source']) == load['sha256'] and text(load['tool_reference']) for load in loads) and
            {load['source'] for load in loads} == set(expected),
            'Names or catalog entries do not demonstrate actual selected instruction loads.', 'invalid_result')
    require(isinstance(request['work'], list) and request['work'] and all(text(w) for w in request['work']),
            'Observable skill-guided work evidence required.', 'invalid_result')
    artifacts(request['artifacts'], {'stage-evidence'}, 'invalid_result')
    tests = request['tests']
    require(isinstance(tests, list) and tests and all(isinstance(t, dict) and set(t) == {'command', 'result'} and
            text(t['command']) and text(t['result']) for t in tests), 'Actual command/result evidence or explicit missing capability required.', 'invalid_result')
    original = {key: handoff[key] for key in REQUEST_FIELDS}
    original['skills'] = [{key: value for key, value in s.items() if key != 'instructions'} for s in handoff['skills']]
    prepare(database, project, iteration, operator, original, github, dry_run=True)
    stage_result = None
    recovery_modification = handoff['stage'] in {'repair', 'corrections'} and 'recovery' in handoff
    if (handoff['stage'] in {'simplify', 'review-standards', 'review-spec', 'diagnosis'} or recovery_modification) and not (recovery_modification and request['status'] == 'stuck'):
        runtime = assignment.get('runtime') or {}
        launcher_submission = (runtime.get('status') == 'running' and
                               sandbox_run_id == request['run_id'] == runtime.get('run_id'))
        completed_replay = (runtime.get('status') == 'complete' and runtime.get('container_removed') is True and
                            assignment.get('submitted_result') == request)
        require(launcher_submission or completed_replay,
                'Stage accepts only launcher-validated output or exact completed replay.', 'invalid_result')
        if recovery_modification:
            from .recovery import validate_result
        elif handoff['stage'] == 'diagnosis':
            from .diagnosis import validate_result
        elif handoff['stage'] == 'simplify':
            from .simplification import validate_result
        else:
            from .review import validate_result
        stage_result = validate_result(assignment, request)
    old = assignment.get('submitted_result')
    require(old is None or old == request, 'Result replay cannot replace previously stored evidence.', 'result_conflict')
    assignment['submitted_result'] = request
    assignment['result_disposition'] = {'structurally_valid': True, 'trusted_execution': False,
                                       'advance_allowed': False, 'close_allowed': False,
                                       'reason': 'trusted_whole_process_execution_evidence_unavailable'}
    if stage_result is not None:
        assert assignment is not None
        if recovery_modification:
            result_key = 'recovery_result'
        elif 'diagnosis' in handoff:
            result_key = 'diagnosis_result'
        elif 'review' in handoff:
            result_key = 'review_result'
        else:
            result_key = 'simplification_result'
        assignment[result_key] = stage_result
    from .controls import admission
    with admission(database, project, iteration), database:
        database.execute('BEGIN IMMEDIATE')
        require_result_active(assignment)
        database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?', (json.dumps(assignment, sort_keys=True), assignment_id))
        require_result_active(assignment)
    return assignment


def launch():
    raise RepositoryError('isolation_unavailable', 'Public launch is disabled: snapshots and caller assertions do not prove whole-process isolation.')
