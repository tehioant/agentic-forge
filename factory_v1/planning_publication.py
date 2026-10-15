"""Initial same-origin HOST publication, confined to a new docs branch and issue."""
import base64
import copy
import os
import stat

from . import assignments, planning, publication
from .planning import require, text
from .repositories import RepositoryError


def configuration(path, operator):
    """Load explicit host authorization; never discover ambient credentials."""
    try:
        source = publication.physical(path)
        info = source.stat()
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
                'Private controller-owned planning authorization required.', 'approval_required')
        config = publication.read_json(source)
        fields = {'operator_id', 'reference', 'repository', 'repository_id', 'private', 'api_base',
                  'bearer', 'assignment_id', 'session_id', 'parent'}
        require(isinstance(config, dict) and set(config) == fields and config['operator_id'] == operator and
                all(text(config[k]) for k in fields - {'repository_id', 'private'}) and
                publication.valid_repository(config['repository']) and type(config['repository_id']) is int and
                config['repository_id'] > 0 and type(config['private']) is bool and assignments.commit(config['parent']),
                'Exact approved planning publication identity required.', 'approval_required')
        # Reuse the credential/endpoint validator, then restrict the supported host routes.
        from .repositories import GitHub
        GitHub(config['api_base'], config['bearer'], 10)
        require(config['api_base'] == 'https://api.github.com' or
                config['api_base'].startswith(('http://127.0.0.1:', 'http://localhost:', 'http://[::1]:')),
                'Only GitHub or an explicit loopback host route is supported.', 'capability_blocked')
        return config
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise RepositoryError('invalid_publication', 'Invalid private planning authorization.') from error


def publish(database, item, documents, execution, github, config, guard):
    """Publish actual native tool output; every mutation has a durable exact intent."""
    planning.validate_documents(documents)
    work = item['planning']
    require(not work.get('continuation'), 'Use the existing continuation publication contract.', 'scope_mismatch')
    assignment = work['assignment_id']
    branch = 'factory/planning/' + item['iteration_id'] + '/' + assignment
    path = 'docs/factory/' + item['iteration_id'] + '/' + assignment
    require(publication.valid_ref(branch) and path == work['docs_path'] and
            config['repository'] == item['repository'] and config['assignment_id'] == assignment and
            config['session_id'] == execution['session_id'] and
            config['repository_id'] == item['repository_onboarding']['metadata']['id'] and
            config['api_base'] == github.base == item['repository_onboarding']['api_base'],
            'Publication is bound to the exact native assignment/repository.', 'scope_mismatch')
    scope = {k: v for k, v in config.items() if k != 'bearer'}
    record = work.setdefault('host_publication', {'scope': scope, 'execution': copy.deepcopy(execution),
        'documents': copy.deepcopy(documents), 'branch': branch, 'path': path, 'effects': {}})
    require(record['scope'] == scope and record['execution'] == execution and record['documents'] == documents and
            record['branch'] == branch and record['path'] == path,
            'Existing publication cannot be replaced or retried under changed authority.', 'publication_uncertain')
    api = publication.PublicationGitHub(github, config)
    prefix = api.prefix

    def read(suffix):
        api.reads.add(prefix + suffix)
        return api.request(prefix + suffix)

    def active():
        guard()
        metadata = read('')
        require(isinstance(metadata, dict) and metadata.get('id') == config['repository_id'] and
                metadata.get('full_name') == config['repository'] and metadata.get('private') is config['private'],
                'Approved repository identity/privacy changed.', 'repository_mismatch')
        guard()
        from .tickets import effect_check
        effect_check(database, item)

    def checkpoint():
        active()
        fresh = assignments.current(database, item['project_id'], item['iteration_id'], config['operator_id'])
        fresh['planning']['host_publication'] = record
        item.clear(); item.update(fresh)
        from .tickets import effect_checkpoint
        effect_checkpoint(database, item)

    def effect(key, suffix, payload):
        active()
        effects = record['effects']
        if key in effects:
            require(effects[key]['payload'] == payload, 'Exact write intent changed.', 'publication_uncertain')
            require('response' in effects[key], 'Unknown write outcome; reconcile, never blindly retry.', 'publication_uncertain')
            return effects[key]['response']
        effects[key] = {'path': suffix, 'payload': copy.deepcopy(payload)}
        checkpoint()  # Must commit before any external mutation.
        active()
        api.writes[('POST', prefix + suffix)] = payload
        result = api.request(prefix + suffix, payload, 'POST')
        effects[key]['response'] = result
        # Receipt-only save retains a newly appearing independent pause/hold.
        fresh = assignments.current(database, item['project_id'], item['iteration_id'], config['operator_id'], require_active=False)
        require(fresh['planning']['assignment_id'] == assignment, 'Assignment changed during publication.', 'scope_mismatch')
        fresh['planning']['host_publication'] = copy.deepcopy(record)
        from .repositories import save_iteration
        save_iteration(database, fresh)
        database.commit()
        item.clear(); item.update(fresh)
        return result

    checkpoint()
    parent = read('/git/commits/' + config['parent'])
    require(isinstance(parent, dict) and parent.get('sha') == config['parent'] and
            assignments.commit(parent.get('tree', {}).get('sha')), 'Exact parent unavailable.', 'publication_mismatch')
    base_tree = read('/git/trees/' + parent['tree']['sha'] + '?recursive=1')
    require(isinstance(base_tree, dict) and base_tree.get('sha') == parent['tree']['sha'] and
            base_tree.get('truncated') is False and isinstance(base_tree.get('tree'), list),
            'Exact complete parent tree required.', 'publication_mismatch')
    baseline = {e['path']: {k: e[k] for k in ('mode', 'type', 'sha')}
                for e in base_tree['tree'] if e.get('type') != 'tree'}
    require(not any(p == path or p.startswith(path + '/') for p in baseline),
            'Initial docs must not replace existing paths.', 'publication_mismatch')
    changes = []
    for name, content in sorted(documents.items()):
        raw = content.encode('utf-8')
        sha = publication.object_sha('blob', raw)
        result = effect('blob:' + name, '/git/blobs', {'encoding': 'base64', 'content': base64.b64encode(raw).decode()})
        require(isinstance(result, dict) and result.get('sha') == sha, 'Blob response mismatch.', 'publication_mismatch')
        observed = read('/git/blobs/' + sha)
        try:
            exact = (isinstance(observed, dict) and isinstance(observed.get('content'), str) and
                     base64.b64decode(observed['content'].replace('\n', ''), validate=True) == raw)
        except ValueError as error:
            raise RepositoryError('publication_mismatch', 'Invalid blob readback encoding.') from error
        require(isinstance(observed, dict) and observed.get('sha') == sha and observed.get('encoding') == 'base64' and
                exact,
                'Exact blob readback required.', 'publication_mismatch')
        changes.append({'path': path + '/' + name, 'mode': '100644', 'type': 'blob', 'sha': sha})
    tree = effect('tree', '/git/trees', {'base_tree': parent['tree']['sha'], 'tree': changes})
    require(isinstance(tree, dict) and assignments.commit(tree.get('sha')), 'Tree response mismatch.', 'publication_mismatch')
    observed = read('/git/trees/' + tree['sha'] + '?recursive=1')
    require(isinstance(observed, dict) and observed.get('sha') == tree['sha'] and observed.get('truncated') is False and
            all(any(all(entry.get(k) == v for k, v in change.items()) for entry in observed.get('tree', [])
                    if isinstance(entry, dict)) for change in changes), 'Exact docs tree readback required.', 'publication_mismatch')
    expected = {**baseline, **{c['path']: {k: c[k] for k in ('mode', 'type', 'sha')} for c in changes}}
    actual = {e['path']: {k: e[k] for k in ('mode', 'type', 'sha')}
              for e in observed['tree'] if e.get('type') != 'tree'}
    require(actual == expected, 'Tree changed outside the exact three docs paths.', 'publication_mismatch')
    commit = effect('commit', '/git/commits', {'message': 'Factory initial specification ' + assignment + '\n',
        'tree': tree['sha'], 'parents': [config['parent']]})
    require(isinstance(commit, dict) and assignments.commit(commit.get('sha')), 'Commit response mismatch.', 'publication_mismatch')
    sha = commit['sha']
    observed = read('/git/commits/' + sha)
    require(isinstance(observed, dict) and observed.get('sha') == sha and observed.get('tree', {}).get('sha') == tree['sha'] and
            [p.get('sha') for p in observed.get('parents', [])] == [config['parent']],
            'Exact commit/tree/parent readback required.', 'publication_mismatch')
    ref = read('/git/ref/heads/' + branch)
    if ref is None:
        effect('ref', '/git/refs', {'ref': 'refs/heads/' + branch, 'sha': sha})
        ref = read('/git/ref/heads/' + branch)
    require(publication.ref_sha(ref, branch) == sha, 'Docs branch collision/drift.', 'publication_mismatch')
    title = 'Factory initial specification ' + assignment
    # Reconcile a lost issue-create response without another write.
    # Do not expose arbitrary list queries to the native tool.
    issues = []
    page = 1
    while True:
        batch = read('/issues?state=all&per_page=100&page=' + str(page))
        require(isinstance(batch, list) and page <= 100, 'Bounded exact issue listing required.', 'publication_uncertain')
        issues.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    matches = [i for i in issues if isinstance(i, dict) and i.get('title') == title and 'pull_request' not in i]
    require(len(matches) <= 1, 'Duplicate specification issue; reconcile.', 'publication_uncertain')
    if matches:
        prior = record['effects'].get('issue')
        require(prior is not None, 'Preexisting specification title collision.', 'publication_mismatch')
        issue = matches[0]
        response = prior.get('response')
        require(response is None or all(response.get(k) == issue.get(k) for k in ('number', 'id')),
                'Created issue identity changed.', 'publication_mismatch')
    else:
        issue = effect('issue', '/issues', {'title': title, 'body': documents['milestone.md'], 'labels': ['ready-for-agent']})
    require(isinstance(issue, dict) and type(issue.get('number')) is int and issue['number'] > 0 and
            type(issue.get('id')) is int and issue['id'] > 0, 'Exact issue identity required.', 'publication_mismatch')
    identity = {k: issue[k] for k in ('number', 'id')}
    require(record.get('issue', identity) == identity, 'Specification issue replaced.', 'publication_mismatch')
    record['issue'] = identity
    checkpoint()
    observed_issue = read('/issues/' + str(issue['number']))
    require(isinstance(observed_issue, dict) and all(observed_issue.get(k) == v for k, v in identity.items()) and
            observed_issue.get('title') == title and observed_issue.get('body') == documents['milestone.md'] and
            observed_issue.get('state') == 'open' and
            [label.get('name') for label in observed_issue.get('labels', []) if isinstance(label, dict)] == ['ready-for-agent'],
            'Exact created issue title/body/identity/label readback required.', 'publication_mismatch')
    api.reads.update(prefix + '/contents/' + path + '/' + name + '?ref=' + sha for name in documents)
    handoff = planning.read_publication(database, item, path, documents, sha, issue['number'], api)
    require(handoff['issue']['id'] == identity['id'] and
            publication.ref_sha(read('/git/ref/heads/' + branch), branch) == sha,
            'Final exact publication identity changed.', 'publication_mismatch')
    record.update(commit=sha, handoff=handoff, status='verified')
    checkpoint()
    return record
