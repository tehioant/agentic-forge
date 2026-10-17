"""Read-only, explicitly scoped GitHub Projects observations for the dashboard.

No cached production fallback. Call read_board once per shared snapshot and pass its
result to render_board; the application owns last-good retention and refresh.
"""
import html
import json
import re
import subprocess
from datetime import datetime, timezone


LINKED_PROJECTS = '''query DashboardLinkedProjects($owner:String!,$name:String!,$cursor:String){
  repository(owner:$owner,name:$name){nameWithOwner projectsV2(first:50,after:$cursor){
    nodes{id} pageInfo{hasNextPage endCursor}}}}'''
PROJECT_ITEMS = '''query DashboardProjectItems($project:ID!,$cursor:String){
  node(id:$project){... on ProjectV2{id title url items(first:50,after:$cursor){
    nodes{id fieldValueByName(name:"Status"){
      ... on ProjectV2ItemFieldSingleSelectValue{name}}
      content{__typename ... on Issue{number title url repository{nameWithOwner}
        blockedBy(first:50){nodes{number url state repository{nameWithOwner}}
          pageInfo{hasNextPage endCursor}}}}}
    pageInfo{hasNextPage endCursor}}}}}'''
COLUMNS = ('Todo', 'In Progress', 'Blocked', 'Done', 'Unknown')
STATUS_COLUMNS = {name: name for name in COLUMNS[:-1]}
MAX_PAGES = 4


class BoardUnavailable(ValueError):
    """An incomplete or out-of-scope observation must not look healthy."""


def _require(condition):
    if not condition:
        raise BoardUnavailable('GitHub board observation unavailable.')


def _scope(repository, project_id):
    _require(isinstance(repository, str) and re.fullmatch(
        r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}', repository))
    _require(isinstance(project_id, str) and re.fullmatch(r'PVT_[A-Za-z0-9_-]{1,150}', project_id))


class HostGitHubReadTransport:
    """Host-only gh credential use; exact query allowlist, no generic HTTP seam.

    Construct one per observation. Bearers never enter Python, argv or responses.
    The configured gh installation/credential store remains a trusted host boundary.
    """

    def __init__(self, repository, project_id):
        _scope(repository, project_id)
        self.repository = repository
        self.project_id = project_id
        self.calls = 0

    def query(self, document, variables):
        _require(isinstance(variables, dict))
        cursor = variables.get('cursor')
        _require(cursor is None or isinstance(cursor, str) and 0 < len(cursor) <= 1024)
        if document == LINKED_PROJECTS:
            owner, name = self.repository.split('/')
            _require(variables == {'owner': owner, 'name': name, 'cursor': cursor})
        elif document == PROJECT_ITEMS:
            _require(variables == {'project': self.project_id, 'cursor': cursor})
        else:
            raise BoardUnavailable('Read-only board transport refused the operation.')
        _require(self.calls < MAX_PAGES * 2)
        self.calls += 1
        try:
            response = subprocess.run(
                ['gh', 'api', '--hostname', 'github.com', '--method', 'POST',
                 'graphql', '--input', '-'],
                input=json.dumps({'query': document, 'variables': variables}),
                capture_output=True, text=True, timeout=8, check=True)
            _require(len(response.stdout) <= 2_000_000)
            result = json.loads(response.stdout)
            _require(isinstance(result, dict) and not result.get('errors') and
                     isinstance(result.get('data'), dict))
            return result['data']
        except (OSError, subprocess.SubprocessError, ValueError, RecursionError):
            raise BoardUnavailable('GitHub board read failed.') from None


def _connection(value):
    _require(isinstance(value, dict) and isinstance(value.get('nodes'), list) and
             len(value['nodes']) <= 50 and isinstance(value.get('pageInfo'), dict) and
             type(value['pageInfo'].get('hasNextPage')) is bool)
    return value['nodes'], value['pageInfo']


def _pages(fetch):
    cursor = None
    seen = set()
    for _ in range(MAX_PAGES):
        nodes, info = _connection(fetch(cursor))
        yield from nodes
        if not info['hasNextPage']:
            return
        cursor = info.get('endCursor')
        _require(isinstance(cursor, str) and 0 < len(cursor) <= 1024 and cursor not in seen)
        seen.add(cursor)
    raise BoardUnavailable('GitHub board pagination limit reached.')


def _text(value, limit=300):
    _require(isinstance(value, str))
    value = ''.join(c for c in value if c.isprintable())[:limit]
    return re.sub(r'(?i)(?:bearer\s+\S+|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)',
                  '[redacted]', value)


def _issue_url(issue, repository):
    _require(type(issue.get('number')) is int and issue['number'] > 0)
    expected = f'https://github.com/{repository}/issues/{issue["number"]}'
    _require(issue.get('url') == expected)
    return expected


def _ticket(item, repository):
    _require(isinstance(item, dict) and isinstance(item.get('id'), str))
    issue = item.get('content')
    if issue is None:
        return None  # Deleted/inaccessible items are not fabricated issues.
    _require(isinstance(issue, dict))
    if issue.get('__typename') != 'Issue':
        return None
    _require(isinstance(issue.get('repository'), dict))
    if issue['repository'].get('nameWithOwner') != repository:
        return None
    url = _issue_url(issue, repository)
    value = item.get('fieldValueByName')
    _require(value is None or isinstance(value, dict) and isinstance(value.get('name'), str))
    source_status = _text(value['name']) if value else None
    column = STATUS_COLUMNS.get(value['name'], 'Unknown') if value else 'Unknown'
    dependencies, info = _connection(issue.get('blockedBy'))
    _require(not info['hasNextPage'])  # Never call a truncated blocker list complete.
    blockers = []
    has_open_dependency = False
    for dependency in dependencies:
        _require(isinstance(dependency, dict) and dependency.get('state') in ('OPEN', 'CLOSED') and
                 isinstance(dependency.get('repository'), dict))
        if dependency['state'] != 'OPEN':
            continue
        has_open_dependency = True
        if dependency['repository'].get('nameWithOwner') == repository:
            blockers.append({'number': dependency['number'], 'url': _issue_url(dependency, repository)})
    derived = has_open_dependency and column not in ('Done', 'Blocked')
    return {'number': issue['number'], 'title': _text(issue.get('title')), 'url': url,
            'column': 'Blocked' if derived else column, 'source_status': source_status,
            'derived_blocked': derived, 'blockers': blockers,
            'reason': 'Open GitHub dependency' if derived else None}


def read_board(repository, project_id, *, transport=None, now=None):
    """Return a small independent source observation; failures have no fresh time.

    Explicitly select an existing repository-linked ProjectV2 node ID. There is no
    project discovery fallback, worker-derived ticket state or board mutation.
    Only the conventional Status field and exact four standard names are mapped.
    """
    unavailable = {'status': 'unavailable', 'observed_at': None, 'project': None,
                   'tickets': [], 'error': 'GitHub board unavailable; check explicit project scope and read access.'}
    try:
        _scope(repository, project_id)
        transport = transport if transport is not None else HostGitHubReadTransport(repository, project_id)
        owner, name = repository.split('/')

        def linked(cursor):
            data = transport.query(LINKED_PROJECTS, {'owner': owner, 'name': name, 'cursor': cursor})
            repo = data.get('repository')
            _require(isinstance(repo, dict) and repo.get('nameWithOwner') == repository)
            return repo.get('projectsV2')

        linked_ids = []
        for project in _pages(linked):
            _require(isinstance(project, dict) and isinstance(project.get('id'), str))
            linked_ids.append(project['id'])
        _require(linked_ids.count(project_id) == 1)
        project_identity = None

        def items(cursor):
            nonlocal project_identity
            data = transport.query(PROJECT_ITEMS, {'project': project_id, 'cursor': cursor})
            node = data.get('node')
            _require(isinstance(node, dict) and node.get('id') == project_id)
            title = _text(node.get('title'))
            url = node.get('url')
            _require(isinstance(url, str) and re.fullmatch(
                r'https://github\.com/(?:users|orgs)/[A-Za-z0-9-]+/projects/[1-9][0-9]*', url))
            identity = {'id': project_id, 'title': title, 'url': url, 'repository': repository}
            _require(project_identity is None or project_identity == identity)
            project_identity = identity
            return node.get('items')

        tickets = []
        seen_items = set()
        seen_numbers = set()
        for item in _pages(items):
            ticket = _ticket(item, repository)
            _require(item['id'] not in seen_items)
            seen_items.add(item['id'])
            if ticket is not None:
                _require(ticket['number'] not in seen_numbers)
                seen_numbers.add(ticket['number'])
                tickets.append(ticket)
        observed = now() if now is not None else datetime.now(timezone.utc)
        _require(isinstance(observed, datetime) and observed.utcoffset() is not None)
        return {'status': 'available', 'observed_at': observed.astimezone(timezone.utc).isoformat(),
                'project': project_identity, 'tickets': tickets, 'error': None}
    except (BoardUnavailable, KeyError, TypeError, AttributeError, OSError,
            subprocess.SubprocessError, ValueError, RecursionError):
        return unavailable


def render_board(observation):
    """Pure secondary-view HTML, also usable for retained last-good observations."""
    escape = lambda value: html.escape(str(value), quote=True)
    result = ['<section id="ticket-board"><h2>GitHub Projects tickets</h2>']
    status = observation.get('status')
    if status != 'available':
        result.append('<p>GitHub board unavailable. Retained tickets may be stale.</p>')
    observed = observation.get('observed_at')
    result.append('<p>GitHub observed: ' + escape(observed or 'Unknown') + '</p>')
    project = observation.get('project')
    if project:
        result.append('<p>' + escape(project['title']) + '</p>')
    tickets = observation.get('tickets', [])
    if status == 'available' and not tickets:
        result.append('<p>No tickets in the selected repository board.</p>')
    for column in COLUMNS:
        result.append('<section class="board-column"><h3>' + column + '</h3><ul>')
        for ticket in tickets:
            if ticket.get('column') != column:
                continue
            # Revalidate href at the rendering boundary, including retained data.
            try:
                _require(isinstance(project, dict))
                url = _issue_url(ticket, project['repository'])
            except (BoardUnavailable, KeyError, TypeError):
                continue
            result.append('<li><a rel="noreferrer" href="' + escape(url) + '">#' +
                          escape(ticket['number']) + ' ' + escape(ticket['title']) + '</a>')
            result.append('<p>Source status: ' + escape(ticket.get('source_status') or 'Unknown') + '</p>')
            if ticket.get('derived_blocked'):
                result.append('<p>Blocked (derived): Open GitHub dependency</p>')
                for blocker in ticket.get('blockers', []):
                    try:
                        blocker_url = _issue_url(blocker, project['repository'])
                    except (BoardUnavailable, KeyError, TypeError):
                        continue
                    result.append('<a rel="noreferrer" href="' + escape(blocker_url) + '">#' +
                                  escape(blocker['number']) + '</a>')
            result.append('</li>')
        result.append('</ul></section>')
    result.append('</section>')
    return ''.join(result)
