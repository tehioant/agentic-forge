"""Exact candidate publication by an authenticated, host-only controller operator."""
import base64
import datetime
import fcntl
import hashlib
import json
import os
import re
import stat
import urllib.parse
from pathlib import Path

from . import assignments
from .planning import require
from .repositories import GitHub, RepositoryError, unambiguous_fields, valid_repository
from .sandbox_source import manifest, physical, relative, MAX_FILES


def object_sha(kind, content):
    return hashlib.sha1(kind.encode() + b' ' + str(len(content)).encode() + b'\0' + content).hexdigest()


def binding(assignment):
    handoff = assignment['handoff']
    return {key: assignment[key] for key in ('assignment_id', 'claim_id', 'handoff_digest', 'revision', 'ticket_scope')} | {
        key: handoff[key] for key in ('stage', 'profile', 'spec_commit', 'baseline', 'candidate', 'issue')} | {
        'execution': assignment.get('runtime')}


def read_json(path):
    with physical(path).open('rb') as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    require(len(data) <= 8 * 1024 * 1024, 'Publication JSON exceeds its bound.', 'invalid_publication')
    return json.loads(data, object_pairs_hook=unambiguous_fields)


def policy_path(path):
    parts = path.lower().split('/')
    return (parts[0] in {'.github', '.hermes', '.agents', '.circleci', '.buildkite'} or
            any(part in {'agents.md', 'claude.md', '.cursorrules', 'codeowners', '.gitmodules',
                         '.gitattributes', '.gitconfig', '.pre-commit-config.yaml', 'security.md',
                         'pyproject.toml', 'tox.ini', 'pytest.ini', 'setup.cfg', 'package.json',
                         'makefile', 'dockerfile', '.gitlab-ci.yml', 'jenkinsfile', '.coveragerc',
                         'mypy.ini', 'ruff.toml', '.ruff.toml', '.flake8', 'lefthook.yml',
                         'lefthook.yaml', '.lefthook.yml', '.lefthook.yaml', '.mise.toml',
                         'mise.toml', '.betterleaks.toml', '.gitleaks.toml'} for part in parts) or
            any(part in {'policy', 'policies', 'rules', 'credentials', 'secrets',
                         'hooks', '.hooks', '.githooks', '.husky'} for part in parts) or
            (parts[0] in {'scripts', 'tools', 'tooling'} and
             any(re.search(r'(?:^|[._-])(?:hook|hooks|security|gate|gates|check|checks|lint|leaks)(?:$|[._-])', part)
                 for part in parts[1:])))


def load_config(path, operator):
    try:
        path = physical(path)
        info = path.stat()
        require(info.st_nlink == 1 and info.st_uid == os.getuid() and
                stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 8 * 1024 * 1024,
                'Use a private controller-owned publication authorization.', 'approval_required')
        config = read_json(path)
        fields = {'operator_id', 'reference', 'execution_reference', 'binding', 'manifest', 'paths',
                  'policy_paths', 'repository', 'repository_id', 'branch', 'base', 'issue',
                  'expected_head', 'candidate', 'commit_date', 'api_base', 'bearer'}
        require(isinstance(config, dict) and set(config) == fields and config['operator_id'] == operator and
                all(isinstance(config[k], str) and config[k].strip() for k in ('reference', 'execution_reference', 'bearer')),
                'Authenticated operator provenance and independent execution verification required.', 'approval_required')
        require(valid_repository(config['repository']) and type(config['repository_id']) is int and config['repository_id'] > 0 and
                type(config['issue']) is int and config['issue'] > 0 and assignments.commit(config['expected_head']) and
                assignments.commit(config['candidate']) and isinstance(config['commit_date'], str) and
                re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', config['commit_date']) is not None,
                'Exact publication identity and deterministic commit pins required.', 'invalid_publication')
        datetime.datetime.strptime(config['commit_date'], '%Y-%m-%dT%H:%M:%SZ')
        for key in ('branch', 'base'):
            require(isinstance(config[key], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,150}', config[key]) and
                    all(p not in {'', '.', '..'} and not p.endswith(('.', '.lock')) for p in config[key].split('/')) and
                    '..' not in config[key] and '@{' not in config[key], 'Unsafe fixed ref.', 'scope_mismatch')
        require(config['branch'] not in {'main', 'master', config['base']}, 'No main/base writes.', 'scope_mismatch')
        require(isinstance(config['paths'], list) and 0 < len(config['paths']) <= 200 and
                all(isinstance(p, str) for p in config['paths']) and
                len(set(config['paths'])) == len(config['paths']) and
                isinstance(config['policy_paths'], list) and all(isinstance(p, str) for p in config['policy_paths']) and
                len(set(config['policy_paths'])) == len(config['policy_paths']) and
                set(config['policy_paths']) <= set(config['paths']),
                'Bounded exact approved paths required.', 'scope_mismatch')
        for name in config['paths']:
            relative(name)
        require(isinstance(config['api_base'], str), 'Explicit publication endpoint required.', 'invalid_publication')
        url = urllib.parse.urlsplit(config['api_base'])
        require(config['api_base'] == 'https://api.github.com' or
                (url.scheme == 'http' and url.hostname in {'127.0.0.1', 'localhost', '::1'} and url.path == ''),
                'Only GitHub or an explicit loopback capability is supported.', 'capability_blocked')
        return config
    except (ValueError, TypeError, KeyError, OSError, RecursionError) as error:
        raise RepositoryError('invalid_publication', 'Invalid private publication configuration.') from error


class PublicationGitHub:
    """No arbitrary URLs or operations; every write payload is preapproved verbatim."""
    def __init__(self, github, config):
        self.github = github
        self.config = config
        self.prefix = '/repos/' + config['repository']
        self.reads = set()
        self.writes = {}

    def approve(self, plan, content, pr_payload):
        config = self.config
        identity = {'name': 'Factory Controller', 'email': 'factory-controller@users.noreply.github.com', 'date': config['commit_date']}
        self.allowed = {
            ('POST', '/git/blobs'): [{'content': base64.b64encode(data).decode(), 'encoding': 'base64'} for data in content.values()],
            ('POST', '/git/trees'): [{'base_tree': plan['base_tree'], 'tree': plan['changes']}],
            ('POST', '/git/commits'): [{'message': plan['message'] + '\n', 'tree': plan['tree'],
                                       'parents': [config['expected_head']], 'author': identity, 'committer': identity}],
            ('POST', '/git/refs'): [{'ref': 'refs/heads/' + config['branch'], 'sha': plan['candidate']}],
            ('PATCH', '/git/refs/heads/' + config['branch']): [{'sha': plan['candidate'], 'force': False}],
            ('POST', '/pulls'): [pr_payload],
        }

    def read(self, suffix):
        config = self.config
        require(suffix in {'', '/git/ref/heads/' + config['branch'], '/git/ref/heads/' + config['base'], pr_query(config)} or
                re.fullmatch(r'/git/commits/[0-9a-f]{40}|/git/trees/[0-9a-f]{40}\?recursive=1|/(?:issues|pulls)/[1-9][0-9]*', suffix),
                'Read outside publication metadata.', 'capability_blocked')
        self.reads.add(self.prefix + suffix)
        return self.request(self.prefix + suffix)

    def write(self, suffix, payload, method='POST'):
        require(payload in getattr(self, 'allowed', {}).get((method, suffix), []),
                'Write outside approved exact publication payloads.', 'capability_blocked')
        self.writes[(method, self.prefix + suffix)] = payload
        return self.request(self.prefix + suffix, payload, method)

    def request(self, path, payload=None, method=None):
        method = method or ('GET' if payload is None else 'POST')
        require((method == 'GET' and payload is None and path in self.reads) or
                (method, path) in self.writes and payload == self.writes[(method, path)],
                'Operation is outside the exact publication capability.', 'capability_blocked')
        return self.github.request(path, payload, method)


def tree_sha(entries):
    ordered = sorted(entries, key=lambda e: e['path'].encode() + (b'/' if e['type'] == 'tree' else b''))
    raw = b''.join(e['mode'].lstrip('0').encode() + b' ' + e['path'].encode() + b'\0' + bytes.fromhex(e['sha']) for e in ordered)
    return object_sha('tree', raw)


def assemble(api, config, assignment, root):
    source = physical(root / 'workspace', directory=True)
    actual = manifest(source)
    require(actual == config['manifest'] == read_json(root / 'source-artifacts.json'),
            'Worker-produced bytes/modes differ from the approved source manifest.', 'manifest_mismatch')
    baseline_root = physical(root / 'inputs' / 'baseline', directory=True)
    baseline = manifest(baseline_root)
    require(baseline == read_json(root / 'initial-source.json')['baseline'], 'Baseline artifacts changed.', 'manifest_mismatch')
    changed = {p for p in actual.keys() | baseline.keys() if actual.get(p) != baseline.get(p)}
    require(changed == set(config['paths']), 'Changes differ from exact approved scope.', 'scope_mismatch')
    sensitive = {p for p in changed if policy_path(p)}
    require(sensitive <= set(config['policy_paths']), 'Policy changes require the explicit operator path.', 'policy_held')
    content = {}
    for path in actual:
        with physical(source / path).open('rb') as stream:
            data = stream.read(actual[path]['bytes'] + 1)
        require(len(data) == actual[path]['bytes'] and hashlib.sha256(data).hexdigest() == actual[path]['sha256'],
                'Source changed during validation.', 'manifest_mismatch')
        if path in changed:
            info = (source / path).stat()
            require(info.st_nlink == 1 and not info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX) and
                    not any(p.lower().startswith('.env') or p.lower().endswith(('.pem', '.key')) for p in path.split('/')) and
                    not re.search(rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}', data),
                    'Suspect modes or secrets are not publishable.', 'source_blocked')
            content[path] = data
    baseline_commit = api.read('/git/commits/' + assignment['handoff']['baseline'])
    require(isinstance(baseline_commit, dict) and baseline_commit.get('sha') == assignment['handoff']['baseline'] and
            assignments.commit(baseline_commit.get('tree', {}).get('sha')), 'Wrong baseline commit.', 'github_mismatch')
    base_tree = baseline_commit['tree']['sha']
    observed = api.read('/git/trees/' + base_tree + '?recursive=1')
    require(isinstance(observed, dict) and observed.get('sha') == base_tree and observed.get('truncated') is False and
            isinstance(observed.get('tree'), list) and len(observed['tree']) <= MAX_FILES,
            'Complete bounded baseline tree required.', 'github_mismatch')
    entries = {}
    for entry in observed['tree']:
        require(isinstance(entry, dict) and assignments.commit(entry.get('sha')) and
                (entry.get('mode'), entry.get('type')) in {('040000', 'tree'), ('100644', 'blob'), ('100755', 'blob'),
                                                        ('120000', 'blob'), ('160000', 'commit')},
                'Invalid baseline tree entry.', 'github_mismatch')
        path = relative(entry['path'])
        require(path not in entries, 'Ambiguous tree.', 'github_mismatch')
        entries[path] = {k: entry[k] for k in ('path', 'mode', 'type', 'sha')}
    regular = {p for p, e in entries.items() if e['mode'] in {'100644', '100755'}}
    require(regular == set(baseline), 'Baseline manifest does not describe every regular source file.', 'manifest_mismatch')
    for path in baseline:
        with physical(baseline_root / path).open('rb') as stream:
            data = stream.read(baseline[path]['bytes'] + 1)
        require(len(data) == baseline[path]['bytes'] and hashlib.sha256(data).hexdigest() == baseline[path]['sha256'] and
                entries[path]['sha'] == object_sha('blob', data) and
                entries[path]['mode'] == ('100755' if baseline[path]['executable'] else '100644'),
                'Baseline bytes/modes disagree with pinned repository.', 'manifest_mismatch')
    changes = []
    for path in sorted(changed):
        require(path not in entries or entries[path]['mode'] in {'100644', '100755'},
                'Unsupported modes/subtrees cannot be replaced.', 'scope_mismatch')
        require(not any(entries.get('/'.join(path.split('/')[:i]), {}).get('type') in {'blob', 'commit'}
                        for i in range(1, len(path.split('/')))), 'Cannot replace an unsupported ancestor.', 'scope_mismatch')
        if path not in actual:
            changes.append({'path': path, 'mode': entries[path]['mode'], 'type': 'blob', 'sha': None})
            del entries[path]
        else:
            entry = {'path': path, 'mode': '100755' if actual[path]['executable'] else '100644',
                     'type': 'blob', 'sha': object_sha('blob', content[path])}
            entries[path] = entry
            changes.append(entry)
    # Recompute only affected directories; opaque trees/gitlinks remain untouched.
    directories = {''}
    for path in changed:
        directories.update('/'.join(path.split('/')[:i]) for i in range(1, len(path.split('/'))))
    for directory in sorted(directories, key=lambda p: p.count('/') + bool(p), reverse=True):
        children = [dict(e, path=p[len(directory) + 1:] if directory else p) for p, e in entries.items()
                    if p.rpartition('/')[0] == directory]
        if directory:
            if children:
                entries[directory] = {'path': directory, 'mode': '040000', 'type': 'tree', 'sha': tree_sha(children)}
            else:
                entries.pop(directory, None)
        else:
            tree = tree_sha(children)
    date = datetime.datetime.strptime(config['commit_date'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
    identity = 'Factory Controller <factory-controller@users.noreply.github.com>'
    message = 'Factory candidate: Refs #' + str(config['issue'])
    raw = f"tree {tree}\nparent {config['expected_head']}\nauthor {identity} {int(date.timestamp())} +0000\ncommitter {identity} {int(date.timestamp())} +0000\n\n{message}\n".encode()
    candidate = object_sha('commit', raw)
    return {'candidate': candidate, 'tree': tree, 'base_tree': base_tree, 'changes': changes, 'message': message}, content


def publish(database, project, iteration, operator, assignment_id, config_path, plan_only=False):
    config = load_config(config_path, operator)
    state_path = database.execute('PRAGMA database_list').fetchone()[2]
    with open(state_path + '.onboarding.lock', 'r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for table in ('iterations', 'role_assignments'):
            for row in database.execute('SELECT payload FROM ' + table):
                json.loads(row[0], object_pairs_hook=unambiguous_fields)
        item = assignments.current(database, project, iteration, operator)
        assignment = assignments.inspect(database, project, iteration, operator, assignment_id)
        handoff = assignment['handoff']
        assignments.ticket_claims(database, assignment['ticket_scope'])
        require(config['binding'] == binding(assignment) and handoff['stage'] in {'implementation', 'corrections', 'simplify'} and
                handoff['profile']['role'] == assignments.ROLES[handoff['stage']] and
                assignments.identifier(handoff['profile']['name']) and handoff['profile']['name'] not in {'default', 'personal'} and
                'write_workspace' in handoff['capabilities'] and
                handoff['repository'] == item['repository'] == config['repository'] and
                handoff['repository_id'] == config['repository_id'] == item['repository_onboarding']['metadata']['id'] and
                handoff['issue']['number'] == config['issue'] and handoff['spec_commit'] == item['handoff']['commit'],
                'Wrong caller, role, assignment or pinned publication scope.', 'scope_mismatch')
        runtime = assignment.get('runtime', {})
        require(runtime.get('status') == 'complete' and runtime.get('container_removed') is True and
                assignment.get('result_disposition', {}).get('isolated_execution') is True,
                'Completed trusted full-process execution required; submitted evidence is never authority.', 'execution_required')
        root = physical(runtime['artifacts'], directory=True)
        require(root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) == 0o700,
                'Private trusted artifact envelope required.', 'execution_required')
        assignments.require_result_active(assignment)
        config_location = physical(config_path)
        scopes = (handoff['workspace'], handoff['profile']['home'], str(root))
        require(all(assignments.absolute(p) and
                    not any(parent.is_symlink() for parent in (Path(p), *Path(p).parents)) for p in scopes),
                'Worker-mounted scopes must be physical absolute paths, not aliases.', 'unsafe_path')
        require(not any(config_location.is_relative_to(Path(p)) for p in scopes),
                'Controller authorization must never be in a worker-mounted scope.', 'approval_required')
        require(config['api_base'] == item['repository_onboarding']['api_base'] == handoff['tracker']['api_base'],
                'Capability endpoint cannot be replaced.', 'capability_conflict')
        api = PublicationGitHub(GitHub(config['api_base'], config['bearer'], 10), config)
        repo = api.read('')
        require(isinstance(repo, dict) and repo.get('id') == config['repository_id'] and repo.get('full_name') == config['repository'] and
                config['branch'] != repo.get('default_branch'), 'Repository replacement/default-branch mutation refused.', 'github_mismatch')
        issue = api.read('/issues/' + str(config['issue']))
        require(isinstance(issue, dict) and all(issue.get(k) == handoff['issue'][k] for k in ('id', 'node_id', 'number')) and
                issue.get('repository_url') == 'https://api.github.com/repos/' + config['repository'] and
                issue.get('state') == 'open' and 'pull_request' not in issue and isinstance(issue.get('body'), str) and
                hashlib.sha256(issue['body'].encode()).hexdigest() == handoff['issue']['body_sha256'],
                'Exact open assigned issue and current body pin required.', 'scope_mismatch')
        base = api.read('/git/ref/heads/' + config['base'])
        require(ref_sha(base, config['base']) == handoff['baseline'], 'Base moved from approved baseline.', 'scope_mismatch')
        plan, content = assemble(api, config, assignment, root)
        if plan_only:
            return {'status': 'plan_only', **plan}
        require(plan['candidate'] == config['candidate'], 'Candidate differs from exact approval.', 'scope_mismatch')
        approval = {k: v for k, v in config.items() if k != 'bearer'}
        old = assignment.get('publication')
        if old is not None:
            require(isinstance(old, dict) and set(old) == {'status', 'approval', 'candidate', 'pr_number'} and
                    old['status'] in ('pending', 'published') and isinstance(old['approval'], dict) and
                    assignments.commit(old['candidate']) and old['approval'].get('candidate') == old['candidate'] and
                    (old['pr_number'] is None or type(old['pr_number']) is int and old['pr_number'] > 0) and
                    (old['status'] != 'published' or old['pr_number'] is not None),
                    'Malformed publication journal requires reconciliation.', 'publication_conflict')
            require(old['approval'] != approval or old['candidate'] == plan['candidate'],
                    'Journal candidate differs from approved plan.', 'publication_conflict')
        require(old is not None or config['expected_head'] == handoff['baseline'],
                'First publication must descend directly from its approved baseline.', 'scope_mismatch')
        if old and old['approval'] != approval:
            require(old['status'] == 'published' and config['expected_head'] == old['candidate'] and
                    all(old['approval'][k] == config[k] for k in ('branch', 'base', 'issue', 'repository', 'repository_id', 'api_base')),
                    'Pending intent or fixed publication target cannot be replaced.', 'publication_conflict')
            old = None
        publication = old or {'status': 'pending', 'approval': approval, 'candidate': plan['candidate'],
                              'pr_number': assignment.get('publication', {}).get('pr_number')}
        marker = '<!-- factory-v1-publication:' + assignment_id + ' -->'
        title = 'Factory candidate for #' + str(config['issue'])
        body = 'Refs #' + str(config['issue']) + '\n\nCandidate publication only; not delivery, closure or merge.\n\n' + marker
        pr_payload = {'title': title, 'body': body, 'head': config['branch'], 'base': config['base'], 'draft': True}
        ref_path = '/git/ref/heads/' + config['branch']
        observed = api.read(ref_path)
        head = None if observed is None else ref_sha(observed, config['branch'])
        require(head in {None, config['expected_head'], plan['candidate']} and
                (old is not None or head != plan['candidate']), 'Unexpected branch replacement/collision.', 'publication_conflict')
        require(head is not None or config['expected_head'] == handoff['baseline'], 'Lost fixed branch cannot be recreated.', 'publication_conflict')
        require(old is None or old['status'] != 'published' or head == plan['candidate'],
                'Published branch disappeared or drifted.', 'publication_conflict')
        if head == plan['candidate']:
            verify_commit(api, plan, config)
        query = pr_query(config)
        prs = api.read(query)
        require(isinstance(prs, list) and len(prs) <= 1, 'Ambiguous fixed PR target.', 'github_mismatch')
        if prs:
            require(isinstance(prs[0], dict) and type(prs[0].get('number')) is int and prs[0]['number'] > 0 and
                    (publication['pr_number'] is None or publication['pr_number'] == prs[0]['number']),
                    'Fixed PR identity drifted.', 'publication_conflict')
            require(old is not None or publication['pr_number'] == prs[0].get('number'),
                    'Unowned PR collision.', 'publication_conflict')
            verify_pr(prs[0], config, dict(plan, candidate=head), title, body)
        else:
            require(publication['pr_number'] is None, 'Fixed PR disappeared.', 'publication_conflict')
        api.approve(plan, content, pr_payload)
        assignment['publication'] = publication
        save(database, assignment)  # Intent is durable before any object/ref/PR write.
        if head != plan['candidate']:
            for path, data in content.items():
                expected = object_sha('blob', data)
                result = api.write('/git/blobs', {'content': base64.b64encode(data).decode(), 'encoding': 'base64'})
                require(isinstance(result, dict) and result.get('sha') == expected, 'Blob mismatch.', 'github_mismatch')
            result = api.write('/git/trees', {'base_tree': plan['base_tree'], 'tree': plan['changes']})
            require(isinstance(result, dict) and result.get('sha') == plan['tree'], 'Tree mismatch.', 'github_mismatch')
            identity = {'name': 'Factory Controller', 'email': 'factory-controller@users.noreply.github.com', 'date': config['commit_date']}
            result = api.write('/git/commits', {'message': plan['message'] + '\n', 'tree': plan['tree'],
                                              'parents': [config['expected_head']], 'author': identity, 'committer': identity})
            require(isinstance(result, dict) and result.get('sha') == plan['candidate'], 'Commit mismatch.', 'github_mismatch')
            verify_commit(api, plan, config)
            current = api.read(ref_path)
            require((None if current is None else ref_sha(current, config['branch'])) == head,
                    'Branch changed before publication.', 'publication_conflict')
            if head is None:
                api.write('/git/refs', {'ref': 'refs/heads/' + config['branch'], 'sha': plan['candidate']})
            else:
                api.write('/git/refs/heads/' + config['branch'], {'sha': plan['candidate'], 'force': False}, 'PATCH')
        require(ref_sha(api.read(ref_path), config['branch']) == plan['candidate'], 'Exact ref readback failed.', 'github_mismatch')
        verify_commit(api, plan, config)
        query = pr_query(config)
        prs = api.read(query)
        require(isinstance(prs, list) and len(prs) < 100 and len(prs) <= 1, 'Ambiguous fixed PR target.', 'github_mismatch')
        if prs:
            pr = prs[0]
            number = pr.get('number')
            require(type(number) is int and number > 0 and
                    (publication['pr_number'] is None or publication['pr_number'] == number), 'PR identity changed.', 'github_mismatch')
            verify_pr(pr, config, plan, title, body)
        else:
            require(publication['pr_number'] is None, 'Published PR disappeared.', 'publication_conflict')
            api.write('/pulls', pr_payload)
            prs = api.read(query)
            require(isinstance(prs, list) and len(prs) == 1, 'PR creation needs exact reconciliation.', 'github_mismatch')
            number = prs[0].get('number')
            require(type(number) is int and number > 0, 'PR number unavailable.', 'github_mismatch')
        publication['pr_number'] = number
        save(database, assignment)
        pr = api.read('/pulls/' + str(number))
        verify_pr(pr, config, plan, title, body)
        associated = api.read('/issues/' + str(number))
        require(isinstance(associated, dict) and associated.get('number') == number and
                associated.get('body') == body and associated.get('state') == 'open' and 'pull_request' in associated and
                associated.get('repository_url') == 'https://api.github.com/repos/' + config['repository'],
                'PR issue association readback failed.', 'github_mismatch')
        issue_after = api.read('/issues/' + str(config['issue']))
        require(issue_after == issue, 'Assigned issue changed during publication.', 'github_mismatch')
        require(ref_sha(api.read(ref_path), config['branch']) == plan['candidate'], 'Ref moved during PR verification.', 'github_mismatch')
        require(ref_sha(api.read('/git/ref/heads/' + config['base']), config['base']) == handoff['baseline'],
                'Base moved during publication.', 'github_mismatch')
        publication['status'] = 'published'
        save(database, assignment)
        return {k: publication[k] for k in ('status', 'candidate', 'pr_number')} | {'close_allowed': False, 'merge_allowed': False}


def pr_query(config):
    return '/pulls?state=all&head=' + urllib.parse.quote(config['repository'].split('/')[0] + ':' + config['branch'], safe='') + '&base=' + urllib.parse.quote(config['base'], safe='') + '&per_page=100'


def save(database, assignment):
    database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                     (json.dumps(assignment, sort_keys=True), assignment['assignment_id']))
    database.commit()


def ref_sha(value, branch):
    require(isinstance(value, dict) and value.get('ref') == 'refs/heads/' + branch and
            value.get('object', {}).get('type') == 'commit' and assignments.commit(value['object'].get('sha')),
            'Exact ref identity unavailable.', 'github_mismatch')
    return value['object']['sha']


def verify_commit(api, plan, config):
    value = api.read('/git/commits/' + plan['candidate'])
    require(isinstance(value, dict) and value.get('sha') == plan['candidate'] and
            value.get('tree', {}).get('sha') == plan['tree'] and
            [p.get('sha') for p in value.get('parents', [])] == [config['expected_head']] and
            value.get('message') == plan['message'] + '\n', 'Exact commit readback failed.', 'github_mismatch')


def verify_pr(value, config, plan, title, body):
    require(isinstance(value, dict) and value.get('state') == 'open' and value.get('merged') in {None, False} and
            value.get('title') == title and value.get('body') == body and value.get('draft') is True,
            'PR is changed, closed or merged.', 'github_mismatch')
    for side, branch in (('head', config['branch']), ('base', config['base'])):
        observed = value.get(side, {})
        require(observed.get('ref') == branch and observed.get('repo', {}).get('id') == config['repository_id'] and
                observed['repo'].get('full_name') == config['repository'], 'Wrong PR repository/head/base.', 'github_mismatch')
    require(value['base'].get('sha') == config['binding']['baseline'], 'Wrong PR base commit.', 'github_mismatch')
    require(value['head'].get('sha') == plan['candidate'], 'Wrong PR candidate.', 'github_mismatch')
