"""Host-only autonomous merge admission and exact integrated delivery readback."""
import hashlib
import json
import os
import re
import stat
import sqlite3
import urllib.parse
from contextlib import closing
from pathlib import Path

from . import assignments, publication, review, role_skills, simplification, tickets
from .planning import require, text
from .repositories import GitHub, RepositoryError, valid_repository
from .sandbox import original
from .sandbox_source import physical


CONFIG_FIELDS = {'operator_id', 'execution_reference', 'executions', 'candidate_assignment',
                 'standards_assignment', 'spec_assignment', 'repository', 'repository_id',
                 'base', 'branch', 'pr_number', 'head', 'baseline', 'issue', 'api_base', 'bearer',
                 'required_checks', 'protection_sha256', 'recovery'}


def execution_binding(assignment):
    """Fingerprint retained host execution, not a role label or worker assertion.

    Deployment authority must independently resolve these records before installing
    a private config. Computing this fingerprint is NOT execution attestation.
    """
    root = physical(assignment['runtime']['artifacts'], directory=True)
    require(root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) == 0o700,
            'Private host evidence envelope required.', 'execution_required')
    files = {}
    for group, names in (('scratch', ['result.json', 'loads.json', 'events.jsonl', 'conversation.json', 'probes.json']),
                         ('inputs', ['worker.py', 'sandbox_relay.py', 'assignment.json', 'handoff.json']),
                         ('', ['container-inspection.json', 'launch-command.json', 'controller-checks.json',
                               'initial-source.json', 'source-artifacts.json'])):
        for name in names:
            path = physical(root / group / name)
            require(path.stat().st_size <= 16_000_000, 'Execution evidence exceeds bound.', 'execution_required')
            files[(group + '/' if group else '') + name] = hashlib.sha256(path.read_bytes()).hexdigest()
    model = {}
    for path in sorted((root / 'model-evidence').glob('*.json')):
        path = physical(path)
        require(path.stat().st_size <= 16_000_000, 'Model evidence exceeds bound.', 'execution_required')
        model[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    require(any(name.startswith('response-') for name in model), 'Admitted model responses required.', 'execution_required')
    return {'binding': publication.binding(assignment), 'result_sha256': assignments.digest(assignment['submitted_result']),
            'skills': [{k: s[k] for k in ('name', 'source', 'sha256', 'dependencies')} for s in assignment['handoff']['skills']],
            'adaptation_sha256': hashlib.sha256(assignment['handoff']['adaptation'].encode()).hexdigest(),
            'evidence': files, 'model_evidence': model}


def private_file(path):
    path = physical(path)
    info = path.stat()
    require(info.st_uid == os.getuid() and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
            'Private controller-owned file required.', 'execution_required')
    return path


def controller_paths(database, config_path):
    config_path = private_file(config_path)
    state = private_file(database.execute('PRAGMA database_list').fetchone()[2])
    for assignment in assignments.rows(database):
        scopes = [assignment['handoff']['workspace'], assignment['handoff']['profile']['home']]
        if assignment.get('runtime'):
            scopes.append(assignment['runtime']['artifacts'])
        for scope in scopes:
            require(assignments.absolute(scope) and not any(p.is_symlink() for p in (Path(scope), *Path(scope).parents)) and
                    not config_path.is_relative_to(Path(scope)) and not state.is_relative_to(Path(scope)),
                    'Controller config/state must be outside every worker mount.', 'execution_required')


def load_config(path, operator):
    config = publication.read_json(private_file(path))
    require(isinstance(config, dict) and set(config) == CONFIG_FIELDS and config['operator_id'] == operator and
            text(config['execution_reference']) and text(config['bearer']) and
            isinstance(config['executions'], list) and len(config['executions']) == 4 and
            all(assignments.sha(config[k]) for k in ('candidate_assignment', 'standards_assignment', 'spec_assignment', 'protection_sha256')) and
            all(assignments.commit(config[k]) for k in ('head', 'baseline')) and
            type(config['pr_number']) is int and config['pr_number'] > 0 and
            type(config['issue']) is int and config['issue'] > 0 and
            type(config['repository_id']) is int and config['repository_id'] > 0,
            'Exact host execution and merge target bindings required.', 'invalid_merge')
    require(valid_repository(config['repository']) and
            all(isinstance(config[k], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,150}', config[k]) and
                all(p not in {'', '.', '..'} and not p.endswith(('.', '.lock')) for p in config[k].split('/')) and
                '..' not in config[k] and '@{' not in config[k] for k in ('base', 'branch')) and config['base'] != config['branch'],
            'Unsafe or wrong fixed merge scope.', 'scope_mismatch')
    url = urllib.parse.urlsplit(config['api_base'])
    require(config['api_base'] == 'https://api.github.com' or
            (url.scheme == 'http' and url.hostname in {'127.0.0.1', 'localhost', '::1'} and url.path == '' and
             not url.username and not url.password and not url.query and not url.fragment),
            'Only GitHub or explicit loopback simulation supported.', 'capability_blocked')
    checks = config['required_checks']
    require(isinstance(checks, list) and 0 < len(checks) <= 100 and all(isinstance(c, dict) and
            set(c) == {'context', 'app_id'} and text(c['context']) and type(c['app_id']) is int and c['app_id'] > 0 for c in checks) and
            len({c['context'] for c in checks}) == len(checks),
            'Explicit authoritative app-bound quality/security checks required.', 'checks_required')
    recovery = config['recovery']
    require(recovery is None or (isinstance(recovery, dict) and set(recovery) == {'incident_id', 'implementation_assignment', 'issue'} and
            assignments.identifier(recovery['incident_id']) and assignments.sha(recovery['implementation_assignment']) and
            recovery['issue'] == config['issue']), 'Explicit incident/recovery binding required.', 'scope_mismatch')
    return config


class MergeGitHub:
    """Only fixed metadata reads, exact SHA merge and exact issue state writes."""
    def __init__(self, config):
        self.config = config
        self.github = GitHub(config['api_base'], config['bearer'], 10)
        self.base = config['api_base']
        self.prefix = '/repos/' + config['repository']

    def request(self, path, body=None, method=None):
        suffix = path.removeprefix(self.prefix)
        c = self.config
        fixed = {'', '/issues/' + str(c['issue']), '/pulls/' + str(c['pr_number']),
                 '/git/ref/heads/' + c['base'], '/git/ref/heads/' + c['branch'],
                 '/branches/' + urllib.parse.quote(c['base'], safe='') + '/protection',
                 '/rules/branches/' + urllib.parse.quote(c['base'], safe='')}
        read = body is None and method in {None, 'GET'} and (suffix in fixed or
            re.fullmatch(r'/git/commits/[0-9a-f]{40}|/git/trees/[0-9a-f]{40}\?recursive=1|'
                                     r'/commits/[0-9a-f]{40}/check-runs\?per_page=100&filter=latest|'
                                     r'/compare/[0-9a-f]{40}\.\.\.[0-9a-f]{40}', suffix))
        write = (method == 'PUT' and suffix == '/pulls/' + str(c['pr_number']) + '/merge' and
                 body == {'sha': c['head'], 'merge_method': 'merge'}) or (
                 method == 'PATCH' and suffix == '/issues/' + str(c['issue']) and
                 body in ({'state': 'open', 'state_reason': 'reopened'}, {'state': 'closed', 'state_reason': 'completed'}))
        require(path.startswith(self.prefix + '/') or path == self.prefix, 'Wrong repository capability.', 'capability_blocked')
        require(read or write, 'Operation outside narrow merge/delivery capability.', 'capability_blocked')
        return self.github.request(path, body, method)

    def read(self, suffix):
        return self.request(self.prefix + suffix)

    def issue_state(self, state, expected):
        self.request(self.prefix + '/issues/' + str(self.config['issue']),
                     {'state': state, 'state_reason': 'completed' if state == 'closed' else 'reopened'}, 'PATCH')
        issue = self.read('/issues/' + str(self.config['issue']))
        require(isinstance(issue, dict) and issue.get('state') == state and
                all(issue.get(k) == expected[k] for k in ('id', 'node_id', 'number', 'repository_url', 'body')),
                'Exact issue identity/requirements/state readback unavailable.', 'delivery_uncertain')
        return issue


def protection(api, config):
    """Read both effective rules and classic protection; unsupported gates fail closed."""
    branch = urllib.parse.quote(config['base'], safe='')
    try:
        rules = api.read('/rules/branches/' + branch)
        classic = api.read('/branches/' + branch + '/protection')
    except RepositoryError as error:
        raise RepositoryError('protection_required', 'Protection readback inaccessible; no upgrade, publicity or bypass authorized.', error.http_status) from error
    require(isinstance(rules, list) and len(rules) <= 100 and isinstance(classic, dict),
            'Readable effective rules and classic protection required.', 'protection_required')
    require(assignments.digest({'rules': rules, 'protection': classic}) == config['protection_sha256'],
            'Required protection policy changed.', 'stale_protection')
    required = classic.get('required_status_checks')
    require(isinstance(required, dict) and required.get('strict') is True and
            isinstance(required.get('checks'), list) and required['checks'] and
            classic.get('enforce_admins', {}).get('enabled') is True and
            classic.get('allow_force_pushes', {}).get('enabled') is False and
            classic.get('allow_deletions', {}).get('enabled') is False,
            'Strict checks, no administrator exemption, force pushes or deletions required.', 'protection_required')
    observed = required['checks']
    for rule in rules:
        require(isinstance(rule, dict) and rule.get('type') in {'required_status_checks', 'pull_request',
                'non_fast_forward', 'deletion'}, 'Unsupported effective gate; cannot establish authority.', 'protection_required')
        if rule['type'] == 'required_status_checks':
            parameters = rule.get('parameters', {})
            require(parameters.get('strict_required_status_checks_policy') is True,
                    'Strict effective checks required.', 'protection_required')
            observed = observed + [{'context': c['context'], 'app_id': c['integration_id']}
                                   for c in parameters['required_status_checks']]
    require(all(isinstance(c, dict) and set(c) >= {'context', 'app_id'} and c['app_id'] is not None and
                {'context': c['context'], 'app_id': c['app_id']} in config['required_checks'] for c in observed) and
            all(c in [{'context': o['context'], 'app_id': o['app_id']} for o in observed] for c in config['required_checks']),
            'Configured checks must equal authoritative effective checks, with exact apps.', 'checks_required')
    return {'rules': rules, 'protection': classic}


def checks(api, config, sha):
    value = api.read('/commits/' + sha + '/check-runs?per_page=100&filter=latest')
    require(isinstance(value, dict) and type(value.get('total_count')) is int and
            isinstance(value.get('check_runs'), list) and value['total_count'] == len(value['check_runs']) < 100,
            'Complete bounded check-run readback required.', 'checks_unknown')
    records = []
    for expected in config['required_checks']:
        matches = [r for r in value['check_runs'] if isinstance(r, dict) and r.get('name') == expected['context'] and
                   r.get('app', {}).get('id') == expected['app_id']]
        require(len(matches) == 1, 'Missing or ambiguous required app-bound check.', 'checks_unknown')
        record = matches[0]
        require(record.get('head_sha') == sha, 'Branch-only/stale check cannot prove integrated code.', 'checks_stale')
        require(type(record.get('id')) is int and record['id'] > 0 and record.get('status') == 'completed' and
                record.get('conclusion') == 'success', 'Required checks failed, pending or unknown.', 'checks_failed')
        records.append({'id': record['id'], 'name': record['name'], 'app_id': expected['app_id'],
                        'head_sha': sha, 'status': record['status'], 'conclusion': record['conclusion']})
    return records


def chain(database, project, iteration, operator, config, config_path):
    prior, root, source, baseline, _ = review.candidate(database, project, iteration, operator, config['candidate_assignment'])
    builder, _, _, _, _ = simplification.implementation(database, project, iteration, operator,
                                                       prior['handoff']['simplification']['implementation_assignment'])
    axes = [assignments.inspect(database, project, iteration, operator, config[k])
            for k in ('standards_assignment', 'spec_assignment')]
    rows = [builder, prior, *axes]
    require([a['handoff']['stage'] for a in axes] == ['review-standards', 'review-spec'] and
            all(a['ticket_scope'] == prior['ticket_scope'] and a['revision'] == prior['revision'] for a in rows) and
            len({a['handoff']['profile']['home'] for a in rows}) == 4 and
            len({a['handoff']['profile']['name'] for a in rows}) == 4 and
            len({a['runtime']['artifacts'] for a in rows}) == 4,
            'Exact chain and two independent fresh review axes required.', 'stale_review')
    controller_paths(database, config_path)
    for assignment in rows:
        h = assignment['handoff']
        require(assignments.validate(original(assignment)) == h['skills'] and
                h['adaptation'] == role_skills.ADAPTATIONS[h['stage']] and
                h['repository'] == config['repository'] and h['repository_id'] == config['repository_id'] and
                h['issue']['number'] == config['issue'] and h['baseline'] == config['baseline'] and
                assignment['runtime'].get('status') == 'complete' and assignment['runtime'].get('container_removed') is True and
                assignment.get('result_disposition', {}).get('checks_verified') is True,
                'Selected installed source/dependency/adaptation or execution changed.', 'execution_required')
        assignments.require_result_active(assignment)
        result = assignment['submitted_result']
        require(result['status'] == 'done' and result['assignment_id'] == assignment['assignment_id'] and
                result['handoff_digest'] == assignment['handoff_digest'] and result['claim_id'] == assignment['claim_id'] and
                result['run_id'] == assignment['runtime']['run_id'] and result['work'],
                'Correlated actual stage work required.', 'execution_required')
        expected = {s['source']: s['sha256'] for s in h['skills']} | h['input_loads']
        require(len(result['loads']) == len(expected) and
                {r['source']: r['sha256'] for r in result['loads']} == expected and
                all(text(r.get('tool_reference')) for r in result['loads']),
                'Actual selected instruction loads required.', 'execution_required')
    require(prior['simplification_result'] == simplification.validate_result(prior, prior['submitted_result']) and
            prior['handoff']['preceding'] == simplification.preceding(builder, physical(builder['runtime']['artifacts'], directory=True),
                *simplification.implementation(database, project, iteration, operator, builder['assignment_id'])[2:]),
            'Implementation-to-simplification chain changed.', 'stale_review')
    require(not review.adverse_reviews(database, review.pins(prior, source, baseline)),
            'Candidate has unresolved independent rejection or stuckness; corrective work and both axes must rerun.', 'stale_review')
    for axis in axes:
        require(axis['handoff']['review']['pins'] == review.pins(prior, source, baseline) and
                axis['review_result'] == review.validate_result(axis, axis['submitted_result']) and
                axis['review_result']['verdict'] == 'pass' and not axis['review_result']['findings'],
                'Exact candidate requires both independent passes.', 'stale_review')
    saved = axes[0].get('review_authorization')
    require(isinstance(saved, dict) and saved == axes[1].get('review_authorization') and saved['operator_id'] == operator and
            text(saved['execution_reference']) and saved['axes'] == [review.execution_binding(a) for a in axes],
            'Independent review execution authorization missing or stale.', 'execution_required')
    sensitive = sorted({p for a in axes for p in a['handoff']['review']['policy_paths']})
    require(not sensitive or (saved['policy_decision'] and saved['policy_decision']['paths'] == sensitive and
                             saved['policy_decision']['findings'] == []), 'Policy changes remain held.', 'policy_held')
    require(config['executions'] == [execution_binding(a) for a in rows],
            'Host-resolved implement, simplify-code and both native review executions required.', 'execution_required')
    return prior, builder, root


def issue(api, assignment, config):
    value = api.read('/issues/' + str(config['issue']))
    pin = assignment['handoff']['issue']
    require(isinstance(value, dict) and all(value.get(k) == pin[k] for k in ('id', 'node_id', 'number')) and
            value.get('repository_url') == 'https://api.github.com/repos/' + config['repository'] and
            'pull_request' not in value and isinstance(value.get('body'), str) and value.get('state') in {'open', 'closed'},
            'Exact assigned issue identity required.', 'scope_mismatch')
    return value


def reopen(api, assignment, config, observed):
    if observed['state'] != 'closed':
        return
    try:
        api.issue_state('open', observed)
    except RepositoryError:
        after = issue(api, assignment, config)
        require(after['state'] == 'open' and after['body'] == observed['body'],
                'Reopening outcome must reconcile before retry.', 'delivery_uncertain')


def target(api, config):
    repo = api.read('')
    require(isinstance(repo, dict) and repo.get('id') == config['repository_id'] and repo.get('full_name') == config['repository'] and
            repo.get('default_branch') == config['base'], 'Wrong exact repository/main target.', 'scope_mismatch')
    pr = api.read('/pulls/' + str(config['pr_number']))
    require(isinstance(pr, dict) and pr.get('number') == config['pr_number'], 'Exact PR unavailable.', 'scope_mismatch')
    for side, branch in (('head', config['branch']), ('base', config['base'])):
        value = pr.get(side, {})
        require(value.get('ref') == branch and value.get('repo', {}).get('id') == config['repository_id'] and
                value['repo'].get('full_name') == config['repository'], 'Wrong PR repository/ref scope.', 'scope_mismatch')
    require(pr['head'].get('sha') == config['head'], 'PR head moved; review stale.', 'stale_head')
    return pr


def mapped_candidate(api, config, assignment, root):
    published = assignment.get('publication', {})
    approval = published.get('approval', {})
    require(published.get('status') == 'published' and published.get('pr_number') == config['pr_number'] and
            published.get('candidate') == config['head'] and approval.get('binding') == publication.binding(assignment) and
            all(approval.get(k) == config[k] for k in ('repository', 'repository_id', 'base', 'branch', 'issue', 'api_base')) and
            approval.get('expected_head') == config['baseline'],
            'Exact simplified publication receipt required.', 'scope_mismatch')
    plan, _ = publication.assemble(api, approval, assignment, root)
    require(plan['candidate'] == config['head'], 'Reviewed byte/mode tree differs from Git head.', 'stale_head')
    publication.verify_commit(api, plan, approval)
    return plan


def merged_evidence(api, config, pr, plan):
    require(pr.get('merged') is True and pr.get('state') == 'closed' and assignments.commit(pr.get('merge_commit_sha')),
            'PR not verifiably merged; branch-only work is incomplete.', 'not_integrated')
    sha = pr['merge_commit_sha']
    commit = api.read('/git/commits/' + sha)
    require(isinstance(commit, dict) and commit.get('sha') == sha and commit.get('tree', {}).get('sha') == plan['tree'] and
            [p.get('sha') for p in commit.get('parents', [])] == [config['baseline'], config['head']],
            'Actual integration must retain reviewed tree and exact merge parents.', 'integration_mismatch')
    main = publication.ref_sha(api.read('/git/ref/heads/' + config['base']), config['base'])
    comparison = api.read('/compare/' + sha + '...' + main)
    require(isinstance(comparison, dict) and comparison.get('status') in {'identical', 'ahead'} and
            comparison.get('base_commit', {}).get('sha') == sha and comparison.get('merge_base_commit', {}).get('sha') == sha,
            'Merge commit is not a verified member of main.', 'not_integrated')
    return {'candidate': config['head'], 'integrated': sha, 'tree': plan['tree'], 'main': main,
            'membership': comparison['status'], 'parents': [config['baseline'], config['head']]}


def save(database, assignment, journal):
    assignment['merge'] = journal
    assignment['merge_refusal'] = journal.get('failure')
    publication.save(database, assignment)


def inspect_merge(database, project, iteration, operator, assignment_id):
    item = assignments.current(database, project, iteration, operator, require_active=False)
    assignment = next((a for a in assignments.rows(database) if a['assignment_id'] == assignment_id), None)
    require(assignment is not None and assignment['ticket_scope']['project'] == project and
            assignment['ticket_scope']['iteration'] == iteration, 'Exact assignment not found.', 'not_found')
    return {'assignment_id': assignment_id, 'revision': assignment['revision'], 'current_spec': item.get('handoff'),
            'merge': assignment.get('merge'), 'refusal': assignment.get('merge_refusal'),
            'iteration_complete': False, 'deployment_verified': False}


def require_publication(database, item, api):
    # pinned_inputs reconciles onboarding; isolate its checkpoints from the trusted
    # merge transaction (and never backup a connection with an uncommitted write).
    with closing(sqlite3.connect(':memory:')) as observation:
        database.backup(observation)
        inputs = tickets.pinned_inputs(observation, json.loads(json.dumps(item)), assignments.ReadOnlyGitHub(api.github))
    require(inputs == item['ticket_work']['inputs'], 'Pinned requirement publication changed.', 'stale_requirements')


def lifecycle(database, project, iteration, operator, config_path, action):
    """Caller holds the shared state lock across reads, intent and network writes."""
    for table in ('iterations', 'role_assignments'):
        if database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            for row in database.execute('SELECT payload FROM ' + table):
                require(len(row[0].encode()) <= 8 * 1024 * 1024 and
                        isinstance(json.loads(row[0], object_pairs_hook=publication.unambiguous_fields), dict),
                        'Unambiguous bounded persistent evidence required.', 'invalid_merge')
    config = load_config(config_path, operator)
    controller_paths(database, config_path)
    api = MergeGitHub(config)
    item = assignments.current(database, project, iteration, operator, require_active=False)
    require(item['repository'] == config['repository'] and
            item['repository_onboarding']['metadata']['id'] == config['repository_id'] and
            item['repository_onboarding']['api_base'] == config['api_base'], 'Wrong configured repository capability.', 'scope_mismatch')
    if action == 'merge-preflight':
        return {'repository': config['repository'], 'base': config['base'], 'protection': protection(api, config),
                'merge_allowed': False, 'close_allowed': False}
    # Raw scope lookup allows reopening obsolete work without accepting stale requirements.
    assignment = next((a for a in assignments.rows(database) if a['assignment_id'] == config['candidate_assignment']), None)
    require(assignment is not None and assignment['ticket_scope']['project'] == project and
            assignment['ticket_scope']['iteration'] == iteration and assignment['handoff']['repository'] == config['repository'] and
            assignment['handoff']['repository_id'] == config['repository_id'] and assignment['handoff']['issue']['number'] == config['issue'],
            'Wrong assignment scope.', 'scope_mismatch')
    observed_issue = issue(api, assignment, config)
    authority = {k: v for k, v in config.items() if k != 'bearer'}
    journal = assignment.get('merge')
    if journal:
        require(journal.get('authority') == authority and journal.get('status') in {'pending', 'merged', 'incomplete', 'delivered'},
                'Fixed merge intent cannot be replaced.', 'merge_conflict')
        if journal['status'] == 'delivered':
            action = 'verify-delivery'
    try:
        prior, builder, root = chain(database, project, iteration, operator, config, config_path)
        require_publication(database, item, api)
        require(hashlib.sha256(observed_issue['body'].encode()).hexdigest() == prior['handoff']['issue']['body_sha256'],
                'Current requirements changed; old candidate obsolete.', 'stale_requirements')
        plan = mapped_candidate(api, config, prior, root)
        pr = target(api, config)
        if action == 'merge-candidate' and not journal:
            assignments.current(database, project, iteration, operator)
            assignments.ticket_claims(database, prior['ticket_scope'])
            incident = item.get('main_incident')
            if incident is not None:
                recovery = config['recovery']
                require(isinstance(incident, dict) and incident.get('status') == 'active' and recovery is not None and
                        incident.get('incident_id') == recovery['incident_id'] and
                        incident.get('recovery_assignment') == recovery['implementation_assignment'] == builder['assignment_id'] and
                        incident.get('recovery_issue') == config['issue'] and builder['handoff']['stage'] == 'repair',
                        'Ordinary merges frozen; exact incident-scoped repair only.', 'main_frozen')
            else:
                require(config['recovery'] is None, 'Recovery authorization has no current incident.', 'scope_mismatch')
                try:
                    checks(api, config, config['baseline'])
                except RepositoryError as error:
                    raise RepositoryError('main_frozen', 'Current main checks are failed or unknown; ordinary merges frozen.') from error
            pair = {k: config[k] for k in ('standards_assignment', 'spec_assignment')}
            require(review.evaluate(database, project, iteration, operator, pair, assignments.ReadOnlyGitHub(api.github))['status'] == 'accepted',
                    'Current independent review admission required.', 'stale_review')
            gate = protection(api, config)
            branch_checks = checks(api, config, config['head'])
            require(publication.ref_sha(api.read('/git/ref/heads/' + config['base']), config['base']) == config['baseline'] and
                    pr['base'].get('sha') == config['baseline'], 'Main baseline moved; refresh reviews.', 'stale_baseline')
            require(publication.ref_sha(api.read('/git/ref/heads/' + config['branch']), config['branch']) == config['head'],
                    'Published branch moved.', 'stale_head')
            require(pr.get('state') == 'open' and pr.get('merged') is False and pr.get('draft') is False and
                    pr.get('mergeable') is True and pr.get('mergeable_state') == 'clean',
                    'PR must be ready and all authoritative merge gates clean.', 'merge_not_ready')
            # Repeat all local pins and exact target immediately at the mutation boundary.
            chain(database, project, iteration, operator, config, config_path)
            require(target(api, config) == pr and protection(api, config) == gate and
                    checks(api, config, config['head']) == branch_checks and
                    publication.ref_sha(api.read('/git/ref/heads/' + config['base']), config['base']) == config['baseline'] and
                    issue(api, prior, config) == observed_issue, 'Admission changed before merge.', 'stale_admission')
            latest = assignments.current(database, project, iteration, operator)
            require(latest.get('main_incident') == incident and latest['handoff'] == item['handoff'] and
                    latest['ticket_work']['input_digest'] == item['ticket_work']['input_digest'],
                    'Incident or requirements changed at admission.', 'stale_admission')
            if incident is None:
                checks(api, config, config['baseline'])
            require_publication(database, latest, api)
            journal = {'status': 'pending', 'authority': authority, 'branch_checks': branch_checks,
                       'protection': gate, 'integrated_evidence': None, 'delivery_checks': [], 'failure': None,
                       'close_allowed': False, 'iteration_complete': False, 'deployment_verified': False}
            save(database, prior, journal)
            # Any outcome, including an HTTP failure, is reconciled by reading this exact PR.
            try:
                api.request(api.prefix + '/pulls/' + str(config['pr_number']) + '/merge',
                            {'sha': config['head'], 'merge_method': 'merge'}, 'PUT')
            except RepositoryError:
                pass
            pr = target(api, config)
        if not journal:
            require(False, 'No trusted merge intent; branch-only work cannot be delivered.', 'not_integrated')
        require(pr.get('merged') is True, 'Pending outcome not proven; reconcile before any retry, never resend blindly.', 'merge_uncertain')
        evidence = merged_evidence(api, config, pr, plan)
        require(journal.get('integrated_evidence') is None or
                journal['integrated_evidence']['integrated'] == evidence['integrated'], 'Merged commit changed.', 'integration_mismatch')
        journal.update(status='merged', integrated_evidence=evidence, delivery_checks=[], close_allowed=False, failure=None)
        save(database, prior, journal)
        observed_issue = issue(api, prior, config)
        require(hashlib.sha256(observed_issue['body'].encode()).hexdigest() == prior['handoff']['issue']['body_sha256'],
                'Requirements changed during integration.', 'stale_requirements')
        if action == 'verify-delivery':
            protection(api, config)
            integrated_checks = checks(api, config, evidence['integrated'])
            # Current main must still be exactly this reviewed tree. Later main changes
            # require their own requirement/delivery verifier; ancestry alone is insufficient.
            main_commit = api.read('/git/commits/' + evidence['main'])
            require(main_commit.get('tree', {}).get('sha') == plan['tree'],
                    'Main changed after integration; delivered requirements need revalidation.', 'delivery_stale')
            require(merged_evidence(api, config, target(api, config), plan) == evidence,
                    'Main changed during delivery checks.', 'delivery_stale')
            chain(database, project, iteration, operator, config, config_path)
            fresh_issue = issue(api, prior, config)
            require(hashlib.sha256(fresh_issue['body'].encode()).hexdigest() == prior['handoff']['issue']['body_sha256'],
                    'Requirements changed before closure.', 'stale_requirements')
            require_publication(database, item, api)
            if fresh_issue['state'] != 'closed':
                assignments.current(database, project, iteration, operator)
            journal.update(delivery_checks=integrated_checks, close_allowed=True)
            save(database, prior, journal)  # Verified integration/checks precede any closure.
            if fresh_issue['state'] != 'closed':
                try:
                    api.issue_state('closed', fresh_issue)
                except RepositoryError:
                    after = issue(api, prior, config)
                    require(after['state'] == 'closed' and after['body'] == fresh_issue['body'],
                            'Closure outcome must reconcile before retry.', 'delivery_uncertain')
            journal.update(status='delivered', failure=None)
            save(database, prior, journal)
        else:
            reopen(api, prior, config, observed_issue)
        return {**journal, 'merge_allowed': False}
    except (RepositoryError, OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
        if not isinstance(error, RepositoryError):
            error = RepositoryError('invalid_merge', 'Malformed or missing execution/merge evidence; no admission permitted.')
        # Persist failure before corrective network IO; neither pause nor obsolete
        # requirements can turn a partial operation into delivered work on restart.
        failure = {'code': error.code, 'message': str(error)}
        if journal:
            journal.update(status='incomplete' if journal.get('integrated_evidence') else 'pending',
                           close_allowed=False, failure=failure)
            save(database, assignment, journal)
        else:
            assignment['merge_refusal'] = {'candidate': config['head'], 'revision': assignment['revision'], **failure}
            publication.save(database, assignment)
        current_issue = issue(api, assignment, config)
        reopen(api, assignment, config, current_issue)
        raise error
