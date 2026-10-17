"""Host-only deterministic main delivery readback and atomic incident/outbox state."""
import copy
import http.client
import json
import math
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request

from . import assignments, attention
from .planning import require, text
from .repositories import NoRedirect, RepositoryError, save_iteration, unambiguous_fields, valid_repository

LIMIT = 2 * 1024 * 1024
CONFIG_FIELDS = {'operator_id', 'repository', 'repository_id', 'api_base', 'issue',
                 'required_checks', 'required_workflows', 'environment', 'contract', 'timeout'}


def decode(raw):
    try:
        require(len(raw.encode() if isinstance(raw, str) else raw) <= LIMIT,
                'Delivery JSON exceeds supported bounds.', 'delivery_unknown')
        value = json.loads(raw, object_pairs_hook=unambiguous_fields,
                           parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        pending = [value]
        while pending:
            entry = pending.pop()
            if isinstance(entry, str):
                entry.encode('utf-8', errors='strict')
            elif isinstance(entry, float) and not math.isfinite(entry):
                raise ValueError('nonfinite number')
            elif isinstance(entry, dict):
                pending.extend(entry.keys())
                pending.extend(entry.values())
            elif isinstance(entry, list):
                pending.extend(entry)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise RepositoryError('delivery_unknown', 'Delivery JSON is malformed or ambiguous.') from error


def positive(value):
    return type(value) is int and 0 < value < 2 ** 63


def safe_url(value, api=False):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        return False
    try:
        url = urllib.parse.urlsplit(value)
        return (url.scheme == 'https' or (url.scheme == 'http' and url.hostname in {'127.0.0.1', '::1', 'localhost'})) and bool(url.hostname) and (
            url.port is None or url.port > 0) and not url.username and not url.password and not url.query and not url.fragment and (
            not api or value == 'https://api.github.com' or (url.scheme == 'http' and url.path == ''))
    except ValueError:
        return False


def deployment_applies(config):
    return config.get('deployment_applicability', 'configured') == 'configured'


def validate_config(config, operator):
    require(isinstance(config, dict) and set(config) in (CONFIG_FIELDS, CONFIG_FIELDS | {'deployment_applicability'}) and
            isinstance(config.get('deployment_applicability', 'configured'), str) and
            config.get('deployment_applicability', 'configured') in {'configured', 'not-applicable'} and config['operator_id'] == operator and
            valid_repository(config['repository']) and positive(config['repository_id']) and positive(config['issue']) and
            safe_url(config['api_base'], api=True) and
            ((text(config['environment']) and len(config['environment']) <= 100) if deployment_applies(config) else config['environment'] is None) and
            type(config['timeout']) in {int, float} and math.isfinite(config['timeout']) and 0 < config['timeout'] <= 30,
            'Explicit exact host delivery target required.', 'invalid_delivery')
    checks = config['required_checks']
    require(isinstance(checks, list) and 0 < len(checks) <= 100 and all(isinstance(c, dict) and
            set(c) == {'context', 'app_id'} and text(c['context']) and len(c['context']) <= 100 and positive(c['app_id']) for c in checks) and
            len({c['context'] for c in checks}) == len(checks), 'Exact app-bound required checks required.', 'invalid_delivery')
    workflows = config['required_workflows']
    require(isinstance(workflows, list) and 0 < len(workflows) <= 20 and all(isinstance(w, dict) and
            set(w) == {'id', 'event'} and positive(w['id']) and w['event'] in {'push', 'workflow_dispatch'} for w in workflows) and
            len({w['id'] for w in workflows}) == len(workflows), 'Explicit main CI/CD workflows required.', 'invalid_delivery')
    contract = config['contract']
    require(isinstance(contract, dict) and set(contract) == {'reference', 'revision', 'health_url', 'acceptance'} and
            text(contract['reference']) and len(contract['reference']) <= 1000 and assignments.commit(contract['revision']) and
            (safe_url(contract['health_url']) if deployment_applies(config) else contract['health_url'] is None) and isinstance(contract['acceptance'], dict) and 0 < len(contract['acceptance']) <= 20 and
            all(attention.identifier(k) and type(v) in {str, bool, int} and (not isinstance(v, str) or 0 < len(v) <= 200) and
                (type(v) is not int or abs(v) < 2 ** 63) for k, v in contract['acceptance'].items()),
            'Pinned product-agreed explicit health/acceptance contract required.', 'invalid_delivery')
    if not deployment_applies(config):
        require(all(positive(value) and value in {w['id'] for w in workflows} for value in contract['acceptance'].values()),
                'Every non-deployment acceptance criterion must bind a required exact-main workflow ID.', 'invalid_delivery')
    return config


def load_config(path, operator):
    with private_config(path).open('rb') as source:
        return validate_config(decode(source.read(LIMIT + 1)), operator)


def private_config(path):
    # Reuse the existing controller-file trust boundary, never stage in workers.
    from .merge import private_file
    return private_file(path)


def http_json(url, timeout):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        request = urllib.request.Request(url, headers={'Accept': 'application/json', 'Cache-Control': 'no-cache'})
        with opener.open(request, timeout=timeout) as response:
            require(response.status == 200, 'Health/readback HTTP result unknown.', 'delivery_unknown')
            return decode(response.read(LIMIT + 1))
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise RepositoryError('delivery_unavailable', 'Configured readback is unavailable.') from error


class DeliveryGitHub:
    """Fixed GET-only repository reads; gh auth stays on the trusted host."""
    def __init__(self, config):
        self.config = config
        self.prefix = '/repos/' + config['repository']

    def read(self, suffix):
        allowed = isinstance(suffix, str) and (suffix in {'', '/git/ref/heads/main', '/deployments?' + urllib.parse.urlencode(
            {'environment': self.config['environment'], 'per_page': 100})} or re.fullmatch(
            r'/commits/[0-9a-f]{40}/check-runs\?per_page=100&filter=latest|/deployments/[1-9][0-9]{0,18}/statuses\?per_page=100', suffix) or
            any(re.fullmatch('/actions/workflows/' + str(w['id']) +
                r'/runs\?branch=main&head_sha=[0-9a-f]{40}&event=' + w['event'] + '&per_page=100', suffix)
                for w in self.config['required_workflows']))
        require(allowed, 'Read outside fixed main delivery capability.', 'capability_blocked')
        path = self.prefix + suffix
        if self.config['api_base'] != 'https://api.github.com':
            return http_json(self.config['api_base'] + path, self.config['timeout'])
        try:
            with tempfile.TemporaryFile() as output:
                completed = subprocess.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET', path],
                                           stdout=output, stderr=subprocess.DEVNULL, timeout=self.config['timeout'], check=False)
                require(completed.returncode == 0, 'GitHub delivery readback unavailable.', 'delivery_unavailable')
                output.seek(0)
                return decode(output.read(LIMIT + 1))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RepositoryError('delivery_unavailable', 'Host GitHub read capability unavailable.') from error


def bounded_list(value):
    require(isinstance(value, list) and len(value) < 100 and all(isinstance(r, dict) for r in value),
            'Complete bounded readback required; pagination is not inferred.', 'delivery_unknown')
    return value


def complete_runs(value, key):
    require(isinstance(value, dict) and type(value.get('total_count')) is int and
            isinstance(value.get(key), list) and value['total_count'] == len(value[key]),
            'Complete run count required.', 'delivery_unknown')
    return bounded_list(value[key])


def successful(record):
    require(positive(record.get('id')) and record.get('status') == 'completed' and record.get('conclusion') == 'success',
            'Required main CI/CD failed, pending or unknown.', 'delivery_failed')


def retained_record(record, fields):
    values = {k: record.get(k) for k in fields}
    require(all(positive(value) if key in {'id', 'workflow_id'} else
                value is None or (isinstance(value, str) and 0 < len(value) <= 100)
                for key, value in values.items()),
            'Delivery evidence fields must be bounded typed identities/states.', 'delivery_unknown')
    return values


def _expected_health(config, sha, deployment_id):
    contract = config['contract']
    if not deployment_applies(config):
        return {'repository': config['repository'], 'sha': sha, 'deployment_applicability': 'not-applicable',
                'contract_sha256': assignments.digest(contract), 'acceptance': contract['acceptance']}
    return {'repository': config['repository'], 'sha': sha, 'deployment_id': deployment_id,
            'environment': config['environment'], 'contract_sha256': assignments.digest(contract),
            'acceptance': contract['acceptance']}


def observe(config, api=None):
    """A coherent exact-main snapshot; partial evidence survives failure, never healthy."""
    api = api or DeliveryGitHub(config)
    reads = {}

    def read(suffix):
        value = api.read(suffix)
        reads[suffix] = value
        return value

    evidence = {'repository': config['repository'], 'repository_id': config['repository_id'], 'main': None,
                'checks': [], 'workflows': [], 'deployment': None, 'health': None, 'status': 'unknown', 'failure': None}
    try:
        repo = read('')
        require(isinstance(repo, dict) and type(repo.get('id')) is int and repo['id'] == config['repository_id'] and repo.get('full_name') == config['repository'] and
                repo.get('default_branch') == 'main', 'Wrong exact repository/main identity.', 'delivery_scope')
        main = read('/git/ref/heads/main')
        require(isinstance(main, dict) and main.get('ref') == 'refs/heads/main' and isinstance(main.get('object'), dict) and
                main['object'].get('type') == 'commit' and assignments.commit(main['object'].get('sha')),
                'Exact main commit unknown.', 'delivery_unknown')
        sha = evidence['main'] = main['object']['sha']
        runs = complete_runs(read('/commits/' + sha + '/check-runs?per_page=100&filter=latest'), 'check_runs')
        for expected in config['required_checks']:
            matches = [r for r in runs if r.get('name') == expected['context'] and isinstance(r.get('app'), dict) and
                       type(r['app'].get('id')) is int and r['app']['id'] == expected['app_id']]
            require(len(matches) == 1 and matches[0].get('head_sha') == sha, 'Exact required main check unknown.', 'delivery_unknown')
            r = matches[0]
            evidence['checks'].append(retained_record(r, ('id', 'name', 'head_sha', 'status', 'conclusion')) | {'app_id': expected['app_id']})
            successful(r)
        for expected in config['required_workflows']:
            query = urllib.parse.urlencode({'branch': 'main', 'head_sha': sha, 'event': expected['event'], 'per_page': 100})
            runs = complete_runs(read('/actions/workflows/' + str(expected['id']) + '/runs?' + query), 'workflow_runs')
            require(runs and all(positive(r.get('id')) and type(r.get('workflow_id')) is int and r['workflow_id'] == expected['id'] and
                    r.get('head_sha') == sha and r.get('head_branch') == 'main' and r.get('event') == expected['event'] and
                    isinstance(r.get('repository'), dict) and type(r['repository'].get('id')) is int and
                    r['repository']['id'] == config['repository_id'] and r['repository'].get('full_name') == config['repository'] for r in runs) and
                    len({r['id'] for r in runs}) == len(runs), 'Exact main workflow identity unknown.', 'delivery_unknown')
            r = max(runs, key=lambda r: r['id'])
            evidence['workflows'].append(retained_record(r, ('id', 'workflow_id', 'head_sha', 'head_branch', 'event', 'status', 'conclusion')))
            successful(r)
        if not deployment_applies(config):
            expected = _expected_health(config, sha, None)
            evidence['deployment'] = {'applicability': 'not-applicable', 'contract_sha256': assignments.digest(config['contract'])}
            evidence['health'] = {'response_sha256': assignments.digest(expected), 'expected': expected, 'matched': True}
        else:
            _observe_deployment(config, read, evidence, sha)
        require(all(api.read(suffix) == value for suffix, value in reads.items()) and
                api.read('/git/ref/heads/main') == main,
                'Main/checks/workflows/deployment changed during observation.', 'delivery_race')
        evidence['status'] = 'healthy'
    except RepositoryError as error:
        evidence['failure'] = error.code
        evidence['status'] = 'failed' if error.code in {'delivery_failed', 'delivery_unhealthy'} else 'unknown'
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError):
        evidence['failure'] = 'delivery_unknown'
    return evidence


def _observe_deployment(config, read, evidence, sha):
    query = urllib.parse.urlencode({'environment': config['environment'], 'per_page': 100})
    deployments = bounded_list(read('/deployments?' + query))
    require(deployments and all(positive(d.get('id')) and d.get('environment') == config['environment'] for d in deployments) and
            len({d['id'] for d in deployments}) == len(deployments), 'Deployment identity unknown.', 'delivery_unknown')
    deployed = max(deployments, key=lambda d: d['id'])
    evidence['deployment'] = retained_record(deployed, ('id', 'sha', 'ref', 'environment'))
    require(deployed.get('sha') == sha and deployed.get('ref') in {'main', sha}, 'Latest deployment is not exact current main.', 'delivery_unknown')
    statuses = bounded_list(read('/deployments/' + str(deployed['id']) + '/statuses?per_page=100'))
    require(statuses and all(positive(s.get('id')) for s in statuses) and len({s['id'] for s in statuses}) == len(statuses),
            'Latest deployment status unknown.', 'delivery_unknown')
    status = max(statuses, key=lambda s: s['id'])
    evidence['deployment']['status'] = retained_record(status, ('id', 'state'))
    require(status.get('state') == 'success', 'Deployment failed, pending or unknown.', 'delivery_failed')
    contract = config['contract']
    health = http_json(contract['health_url'], config['timeout'])
    expected_health = _expected_health(config, sha, deployed['id'])
    response_sha256 = assignments.digest(health)
    evidence['health'] = {'response_sha256': response_sha256, 'expected': expected_health,
                          'matched': isinstance(health, dict) and response_sha256 == assignments.digest(expected_health)}
    require(evidence['health']['matched'],
            'Exact deployment health/acceptance contract not satisfied.', 'delivery_unhealthy')


def context(database, project, iteration, operator):
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?', (project, iteration)).fetchone()
    require(row is not None, 'Exact iteration required.', 'not_found')
    item = decode(row[0])
    require(isinstance(item, dict) and item.get('project_id') == project and item.get('iteration_id') == iteration and
            isinstance(item.get('approval'), dict) and item['approval'].get('operator_id') == operator and text(item['approval'].get('reference')),
            'Trusted recorded operator approval required.', 'approval_required')
    attention.validate_origin(item.get('origin'))
    require(valid_repository(item.get('repository')), 'Exact recorded repository required.', 'invalid_delivery')
    return item


def monitor_state(item, operator):
    saved = item.get('delivery_monitor')
    if saved is None:
        return None
    require(isinstance(saved, dict) and set(saved) == {'config', 'config_sha256', 'latest', 'sequence'} and
            type(saved['sequence']) is int and saved['sequence'] >= 0 and isinstance(saved['latest'], dict) and
            set(saved['latest']) == {'evidence_id', 'status', 'main'} and assignments.sha(saved['latest']['evidence_id']) and
            saved['latest']['status'] in {'healthy', 'failed', 'unknown'} and
            (saved['latest']['main'] is None or assignments.commit(saved['latest']['main'])),
            'Malformed persisted delivery monitor.', 'invalid_delivery')
    validate_config(saved['config'], operator)
    require(saved['config_sha256'] == assignments.digest(saved['config']) and saved['config']['repository'] == item['repository'],
            'Persisted delivery target/pin changed.', 'invalid_delivery')
    return saved


def persisted_evidence(database, project, iteration, evidence_id, config):
    row = database.execute('SELECT payload FROM delivery_observations WHERE project=? AND iteration=? AND id=?',
                           (project, iteration, evidence_id)).fetchone()
    require(row is not None, 'Persisted delivery evidence missing.', 'invalid_delivery')
    return validate_evidence(row[0], evidence_id, config)


def validate_evidence(payload, evidence_id, config):
    evidence = decode(payload)
    require(isinstance(evidence, dict) and set(evidence) == {'repository', 'repository_id', 'main', 'checks', 'workflows',
            'deployment', 'health', 'status', 'failure'} and assignments.digest(evidence) == evidence_id and
            evidence['repository'] == config['repository'] and type(evidence['repository_id']) is int and
            evidence['repository_id'] == config['repository_id'] and evidence['status'] in {'healthy', 'failed', 'unknown'} and
            (evidence['main'] is None or assignments.commit(evidence['main'])) and isinstance(evidence['checks'], list) and
            isinstance(evidence['workflows'], list) and (evidence['status'] != 'healthy' or
            (evidence['failure'] is None and isinstance(evidence['health'], dict) and evidence['health'].get('matched') is True)),
            'Malformed/inconsistent persisted delivery evidence.', 'invalid_delivery')
    if evidence['status'] == 'healthy':
        sha, deployed, health = evidence['main'], evidence['deployment'], evidence['health']
        require(assignments.commit(sha) and len(evidence['checks']) == len(config['required_checks']) and
                len(evidence['workflows']) == len(config['required_workflows']),
                'Healthy persisted delivery has incomplete check evidence.', 'invalid_delivery')
        if deployment_applies(config):
            require(isinstance(deployed, dict) and set(deployed) == {'id', 'sha', 'ref', 'environment', 'status'} and
                    positive(deployed['id']) and deployed['sha'] == sha and deployed['ref'] in {'main', sha} and
                    deployed['environment'] == config['environment'] and isinstance(deployed['status'], dict) and
                    set(deployed['status']) == {'id', 'state'} and positive(deployed['status']['id']) and deployed['status']['state'] == 'success',
                    'Healthy persisted delivery has incomplete deployment/check evidence.', 'invalid_delivery')
        else:
            require(deployed == {'applicability': 'not-applicable', 'contract_sha256': assignments.digest(config['contract'])},
                    'Explicit pinned non-deployment evidence required.', 'invalid_delivery')
        for expected, record in zip(config['required_checks'], evidence['checks'], strict=True):
            require(isinstance(record, dict) and set(record) == {'id', 'name', 'head_sha', 'status', 'conclusion', 'app_id'} and
                    record['name'] == expected['context'] and type(record['app_id']) is int and record['app_id'] == expected['app_id'] and
                    record['head_sha'] == sha, 'Healthy persisted main check binding inconsistent.', 'invalid_delivery')
            successful(record)
        for expected, record in zip(config['required_workflows'], evidence['workflows'], strict=True):
            require(isinstance(record, dict) and set(record) == {'id', 'workflow_id', 'head_sha', 'head_branch', 'event', 'status', 'conclusion'} and
                    type(record['workflow_id']) is int and record['workflow_id'] == expected['id'] and record['head_sha'] == sha and
                    record['head_branch'] == 'main' and record['event'] == expected['event'],
                    'Healthy persisted main workflow binding inconsistent.', 'invalid_delivery')
            successful(record)
        expected_health = _expected_health(config, sha, deployed['id'] if deployment_applies(config) else None)
        expected_sha256 = assignments.digest(expected_health)
        require(set(health) == {'response_sha256', 'expected', 'matched'} and
                assignments.digest(health['expected']) == expected_sha256 and
                health['response_sha256'] == expected_sha256,
                'Healthy persisted deployment health contract inconsistent.', 'invalid_delivery')
    return evidence


def validate_incident(incident, config):
    fields = {'incident_id', 'status', 'monitor_sha256', 'first_evidence', 'latest_evidence', 'recovery_assignment', 'recovery_issue'}
    require(isinstance(incident, dict) and incident.get('status') in {'active', 'cleared'} and
            set(incident) == fields | ({'clearance_evidence'} if incident['status'] == 'cleared' else set()) and
            attention.identifier(incident['incident_id']) and incident['monitor_sha256'] == assignments.digest(config) and
            assignments.sha(incident['first_evidence']) and assignments.sha(incident['latest_evidence']) and
            ((incident['recovery_assignment'] is None and incident['recovery_issue'] is None) or
             (assignments.sha(incident['recovery_assignment']) and positive(incident['recovery_issue']))) and
            (incident['status'] != 'cleared' or assignments.sha(incident['clearance_evidence'])),
            'Malformed or foreign incident; cannot clear it.', 'invalid_delivery')


def validate_persisted(database, item, saved):
    if saved is None:
        return
    project, iteration = item['project_id'], item['iteration_id']
    evidence = persisted_evidence(database, project, iteration, saved['latest']['evidence_id'], saved['config'])
    require(all(evidence[k] == saved['latest'][k] for k in ('main', 'status')),
            'Latest observation pointer inconsistent.', 'invalid_delivery')
    incident = item.get('main_incident')
    require(incident is not None or evidence['status'] == 'healthy', 'Incident/freeze pointer inconsistent.', 'invalid_delivery')
    if incident is not None:
        validate_incident(incident, saved['config'])
        require(incident['status'] == 'active' and incident['latest_evidence'] == saved['latest']['evidence_id'],
                'Active freeze/evidence inconsistent.', 'invalid_delivery')
        row = database.execute('SELECT payload FROM delivery_incidents WHERE project=? AND iteration=? AND id=?',
                               (project, iteration, incident['incident_id'])).fetchone()
        require(row is not None and decode(row[0]) == incident, 'Incident journal pointer inconsistent.', 'invalid_delivery')


def sample(database, project, iteration, operator, config=None):
    """Caller holds shared lock. Evidence + incident + outbox commit in ONE transaction."""
    item = context(database, project, iteration, operator)
    visited = {iteration}
    while item.get('next_iteration'):
        successor = context(database, project, item['next_iteration']['iteration_id'], operator)
        require(successor['iteration_id'] not in visited and successor['repository'] == item['repository'] and
                successor['origin'] == item['origin'] and
                successor.get('previous_iteration', {}).get('iteration_id') == item['iteration_id'],
                'Delivery continuation must retain exact same-product lineage.', 'invalid_delivery')
        item = successor
        iteration = item['iteration_id']
        visited.add(iteration)
    saved = monitor_state(item, operator)
    validate_persisted(database, item, saved)
    config = validate_config(config, operator) if config is not None else (saved['config'] if saved else None)
    if config is None:
        raise RepositoryError('invalid_delivery', 'Explicit configured delivery observer required.')
    require(config['repository'] == item['repository'], 'Exact configured repository required.', 'invalid_delivery')
    require(saved is None or saved['config_sha256'] == assignments.digest(config),
            'Delivery configuration is pinned; changes require a new reviewed iteration.', 'delivery_conflict')
    incident = item.get('main_incident')
    if incident is not None:
        validate_incident(incident, config)
        require(incident['status'] == 'active', 'Only active incident can hold the freeze.', 'invalid_delivery')
    notifications = attention.records(database, project, iteration, item)
    if incident is not None:
        predecessor = item
        visited = {iteration}
        while predecessor.get('previous_iteration'):
            predecessor = context(database, project, predecessor['previous_iteration']['iteration_id'], operator)
            require(predecessor['iteration_id'] not in visited and predecessor['repository'] == item['repository'] and
                    predecessor['origin'] == item['origin'], 'Invalid inherited delivery attention lineage.', 'invalid_delivery')
            visited.add(predecessor['iteration_id'])
            notifications += attention.records(database, project, predecessor['iteration_id'], predecessor)
        require(any(r['event']['event_id'] == incident['incident_id'] and r['event']['kind'] == 'incident' and
                r['event']['evidence_ids'] == [incident['first_evidence']] and r['event']['issue_number'] == config['issue'] and
                r['destination'] == item['origin'] for r in notifications),
                'Active incident attention intent missing or inconsistent.', 'invalid_delivery')
    evidence = observe(config)
    evidence_id = assignments.digest(evidence)
    evidence_payload = json.dumps(evidence)
    validate_evidence(evidence_payload, evidence_id, config)
    sequence = saved['sequence'] if saved else 0
    with database:
        database.execute('BEGIN IMMEDIATE')
        database.execute('CREATE TABLE IF NOT EXISTS delivery_observations (project TEXT, iteration TEXT, id TEXT, payload TEXT, PRIMARY KEY(project, iteration, id))')
        database.execute('CREATE TABLE IF NOT EXISTS delivery_incidents (project TEXT, iteration TEXT, id TEXT, payload TEXT, PRIMARY KEY(project, iteration, id))')
        database.execute('CREATE TABLE IF NOT EXISTS notifications (project TEXT, iteration TEXT, event_id TEXT, payload TEXT, PRIMARY KEY(project, iteration, event_id))')
        database.execute('INSERT OR IGNORE INTO delivery_observations VALUES (?, ?, ?, ?)', (project, iteration, evidence_id, evidence_payload))
        require(persisted_evidence(database, project, iteration, evidence_id, config) == evidence,
                'Existing durable evidence differs; no pointer promotion permitted.', 'invalid_delivery')
        if evidence['status'] != 'healthy':
            if incident is None:
                sequence += 1
                incident_id = 'main-' + assignments.digest({'project': project, 'iteration': iteration, 'config': assignments.digest(config), 'sequence': sequence})
                incident = {'incident_id': incident_id, 'status': 'active', 'monitor_sha256': assignments.digest(config),
                            'first_evidence': evidence_id, 'latest_evidence': evidence_id,
                            'recovery_assignment': None, 'recovery_issue': None}
                attention.enqueue(database, item, {'event_id': incident_id, 'kind': 'incident',
                    'message': 'Main delivery failed or is unknown; ordinary merges frozen. Inspect delivery incident ' + incident_id + '.',
                    'issue_number': config['issue'], 'run_id': incident_id, 'evidence_ids': [evidence_id]})
            incident['latest_evidence'] = evidence_id
            item['main_incident'] = incident
        elif incident is not None:
            incident['latest_evidence'] = evidence_id
            if incident['recovery_issue'] is None:
                incident.update(status='cleared', clearance_evidence=evidence_id)
                item.pop('main_incident')
        if incident is not None:
            incident_payload = json.dumps(incident)
            validate_incident(decode(incident_payload), config)
            database.execute('INSERT OR REPLACE INTO delivery_incidents VALUES (?, ?, ?, ?)', (project, iteration, incident['incident_id'], incident_payload))
        item['delivery_monitor'] = {'config': config, 'config_sha256': assignments.digest(config), 'sequence': sequence,
                                    'latest': {'evidence_id': evidence_id, 'status': evidence['status'], 'main': evidence['main']}}
        durable_item = decode(json.dumps(item))
        validate_persisted(database, durable_item, monitor_state(durable_item, operator))
        save_iteration(database, item)
    return {'monitor': item['delivery_monitor'], 'incident': item.get('main_incident'), 'evidence': evidence}


def inherit_monitor(database, previous, successor, operator):
    """Transfer exact delivery evidence without clearing freezes or duplicating attention."""
    from . import recovery
    require(not recovery.pending_registration(previous),
            'Clear registered recovery in its owning iteration before continuation.', 'recovery_held')
    saved = monitor_state(previous, operator)
    require(saved is not None or previous.get('main_incident') is None,
            'An incident requires its configured monitor.', 'invalid_delivery')
    if saved is None:
        return
    validate_persisted(database, previous, saved)
    successor['delivery_monitor'] = copy.deepcopy(saved)
    if previous.get('main_incident') is not None:
        successor['main_incident'] = copy.deepcopy(previous['main_incident'])
    for table in ('delivery_observations', 'delivery_incidents'):
        database.execute('INSERT INTO ' + table + ' SELECT project, ?, id, payload FROM ' + table +
                         ' WHERE project=? AND iteration=?',
                         (successor['iteration_id'], previous['project_id'], previous['iteration_id']))
    validate_persisted(database, successor, saved)


def inspect(database, project, iteration, operator):
    item = context(database, project, iteration, operator)
    saved = monitor_state(item, operator)
    validate_persisted(database, item, saved)
    result = {'monitor': saved, 'incident': item.get('main_incident'), 'incidents': [], 'observations': []}
    for table, key in [('delivery_incidents', 'incidents'), ('delivery_observations', 'observations')]:
        if database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            result[key] = [{'id': identity, 'record': decode(raw)} for identity, raw in database.execute(
                'SELECT id, payload FROM ' + table + ' WHERE project=? AND iteration=? ORDER BY rowid', (project, iteration))]
    if saved is None:
        require(not result['observations'] and not result['incidents'], 'Unbound persisted delivery records.', 'invalid_delivery')
        return result
    for entry in result['observations']:
        require(assignments.sha(entry['id']), 'Unbound persisted delivery observation.', 'invalid_delivery')
        entry['record'] = persisted_evidence(database, project, iteration, entry['id'], saved['config'])
    for entry in result['incidents']:
        require(saved is not None, 'Unbound persisted incident.', 'invalid_delivery')
        validate_incident(entry['record'], saved['config'])
        require(entry['id'] == entry['record']['incident_id'], 'Incident journal identity inconsistent.', 'invalid_delivery')
        if entry['record']['status'] == 'cleared':
            cleared = persisted_evidence(database, project, iteration, entry['record']['clearance_evidence'], saved['config'])
            require(cleared['status'] == 'healthy', 'Incident clearance lacks verified health.', 'invalid_delivery')
    return result


def mutation_boundary(database, project, iteration, operator):
    """Do not trust stale healthy polling: read health again under the merge lock."""
    item = context(database, project, iteration, operator)
    if 'delivery_monitor' in item:
        sample(database, project, iteration, operator)
    return context(database, project, iteration, operator)
