"""Narrow Issues/dependency and Projects v2 seam; no board creation or worker API."""
from .planning import require


def graph(github, query, variables):
    result = github.request('/graphql', {'query': query, 'variables': variables})
    require(isinstance(result, dict) and not result.get('errors') and isinstance(result.get('data'), dict),
            'Projects access/schema unavailable; restore the authorized capability.', 'projects_blocked')
    return result['data']


def connection(value):
    require(isinstance(value, dict) and isinstance(value.get('nodes'), list) and
            isinstance(value.get('pageInfo'), dict) and type(value['pageInfo'].get('hasNextPage')) is bool,
            'Incomplete GitHub connection.', 'github_mismatch')
    return value['nodes'], value['pageInfo']


def pages(fetch):
    cursor = None
    seen = set()
    result = []
    while True:
        nodes, info = connection(fetch(cursor))
        result.extend(nodes)
        if not info['hasNextPage']:
            return result
        cursor = info.get('endCursor')
        require(isinstance(cursor, str) and cursor and cursor not in seen,
                'Invalid GitHub pagination cursor.', 'github_mismatch')
        seen.add(cursor)


def linked_projects(github, repository):
    owner, name = repository.split('/')
    def fetch(cursor):
        data = graph(github, '''query LinkedProjects($owner:String!,$name:String!,$cursor:String){
          repository(owner:$owner,name:$name){projectsV2(first:100,after:$cursor){
            nodes{id title closed} pageInfo{hasNextPage endCursor}}}}''',
                     {'owner': owner, 'name': name, 'cursor': cursor})
        repo = data.get('repository')
        require(isinstance(repo, dict), 'Repository-linked Projects inaccessible.', 'projects_blocked')
        return repo.get('projectsV2')
    return pages(fetch)


def project_fields(github, project):
    def fetch(cursor):
        data = graph(github, '''query ProjectFields($project:ID!,$cursor:String){
          node(id:$project){... on ProjectV2{id fields(first:100,after:$cursor){
            nodes{... on ProjectV2SingleSelectField{id name options{id name}}}
            pageInfo{hasNextPage endCursor}}}}}''', {'project': project, 'cursor': cursor})
        node = data.get('node')
        require(isinstance(node, dict) and node.get('id') == project,
                'Exact linked project is inaccessible.', 'projects_blocked')
        return node.get('fields')
    return pages(fetch)


def resolve_board(github, repository, configuration):
    keys = {'project_id', 'status_field', 'statuses', 'triage_label'}
    require(isinstance(configuration, dict) and set(configuration) in (keys, keys | {'scope_field'}),
            'Configure project selection (or null), progress names and triage label; native milestones define scope.')
    legacy_scope = 'scope_field' in configuration
    names = configuration['statuses']
    roles = {'ready', 'active', 'done'} | ({'blocked', 'deferred'} if legacy_scope else set())
    require(isinstance(names, dict) and set(names) == roles and
            all(isinstance(v, str) and v.strip() for v in names.values()) and len(set(names.values())) == len(roles) and
            all(isinstance(configuration[k], str) and configuration[k].strip()
                for k in ('status_field', 'triage_label')) and
            (not legacy_scope or isinstance(configuration['scope_field'], str) and configuration['scope_field'].strip() and
             configuration['status_field'] != configuration['scope_field']) and
            (configuration['project_id'] is None or isinstance(configuration['project_id'], str) and configuration['project_id']),
            'Use unambiguous field/option names and a configured triage label.')
    boards = linked_projects(github, repository)
    require(all(isinstance(b, dict) and isinstance(b.get('id'), str) and type(b.get('closed')) is bool for b in boards),
            'Malformed linked project identity.', 'projects_blocked')
    selected = [b for b in boards if not b['closed'] and
                (configuration['project_id'] is None or b['id'] == configuration['project_id'])]
    require(len(selected) == 1, 'Select exactly one accessible open repository-linked board; no replacement or creation is permitted.',
            'projects_blocked')
    project = selected[0]['id']
    fields = project_fields(github, project)
    resolved = {}
    for role in (('status', 'scope') if legacy_scope else ('status',)):
        candidates = [f for f in fields if isinstance(f, dict) and f.get('name') == configuration[role + '_field']]
        require(len(candidates) == 1, 'Selected board field missing or ambiguous.', 'projects_blocked')
        field = candidates[0]
        require(isinstance(field.get('id'), str) and isinstance(field.get('options'), list),
                'Selected field must be single-select.', 'projects_blocked')
        options = field['options']
        require(all(isinstance(o, dict) and isinstance(o.get('id'), str) and isinstance(o.get('name'), str) for o in options) and
                len({o['name'] for o in options}) == len(options) and len({o['id'] for o in options}) == len(options),
                'Ambiguous board options.', 'projects_blocked')
        resolved[role] = {'id': field['id'], 'options': {o['name']: o['id'] for o in options}}
    require(all(n in resolved['status']['options'] for n in names.values()),
            'Configured progress options unavailable.', 'projects_blocked')
    return {'project_id': project, **resolved}


def project_items(github, project):
    def fetch(cursor):
        data = graph(github, '''query ProjectItems($project:ID!,$cursor:String){
          node(id:$project){... on ProjectV2{id items(first:100,after:$cursor){nodes{
            id content{... on Issue{id number repository{nameWithOwner}}}
            fieldValues(first:100){nodes{... on ProjectV2ItemFieldSingleSelectValue{
              optionId field{... on ProjectV2SingleSelectField{id}}}} pageInfo{hasNextPage endCursor}}}
            pageInfo{hasNextPage endCursor}}}}}''', {'project': project, 'cursor': cursor})
        node = data.get('node')
        require(isinstance(node, dict) and node.get('id') == project, 'Wrong project readback.', 'github_mismatch')
        return node.get('items')
    result = pages(fetch)
    for item in result:
        require(isinstance(item, dict) and isinstance(item.get('id'), str), 'Malformed project item.', 'github_mismatch')
        values, info = connection(item.get('fieldValues'))
        require(not info['hasNextPage'], 'Too many project item fields; cannot verify exact scope.', 'projects_blocked')
        ids = []
        for value in values:
            require(isinstance(value, dict), 'Malformed project field value; restore complete Projects metadata.', 'github_mismatch')
            if 'optionId' in value:
                require((value['optionId'] is None or isinstance(value['optionId'], str) and value['optionId']) and
                        isinstance(value.get('field'), dict) and isinstance(value['field'].get('id'), str) and value['field']['id'],
                        'Malformed single-select value; restore exact field and option IDs.', 'github_mismatch')
                ids.append(value['field']['id'])
        require(len(ids) == len(set(ids)), 'Ambiguous item field values.', 'github_mismatch')
        content = item.get('content')
        require(content is None or isinstance(content, dict), 'Malformed project content; restore exact issue metadata.', 'github_mismatch')
        if isinstance(content, dict) and 'repository' in content:
            require(isinstance(content['repository'], dict) and isinstance(content['repository'].get('nameWithOwner'), str) and
                    content['repository']['nameWithOwner'] and isinstance(content.get('id'), str) and content['id'] and
                    type(content.get('number')) is int and content['number'] > 0,
                    'Malformed board issue identity; restore repository, positive issue number and node ID.', 'github_mismatch')
    return result


def single_select_values(item):
    """Project option IDs from an item already validated by project_items."""
    return {v['field']['id']: v['optionId'] for v in item['fieldValues']['nodes'] if v.get('optionId')}


def membership(github, board, repository, issue):
    matches = [i for i in project_items(github, board['project_id']) if isinstance(i.get('content'), dict) and
               i['content'].get('id') == issue['node_id']]
    require(len(matches) <= 1, 'Duplicate project membership.', 'github_mismatch')
    if not matches:
        return None
    item = matches[0]
    require(item['content'].get('number') == issue['number'] and
            item['content'].get('repository', {}).get('nameWithOwner') == repository,
            'Project content identity differs from exact issue.', 'github_mismatch')
    values = single_select_values(item)
    return {'id': item['id'], 'scope': issue_scope(issue, board) if 'milestone' in board else values.get(board['scope']['id']),
            'status': values.get(board['status']['id'])}


def milestone_identity(value):
    require(isinstance(value, dict) and type(value.get('id')) is int and value['id'] > 0 and
            type(value.get('number')) is int and value['number'] > 0 and
            isinstance(value.get('title'), str) and value['title'].strip() and
            value.get('state') in ('open', 'closed'),
            'Exact native milestone scope is unavailable.', 'projects_blocked')
    return {key: value[key] for key in ('id', 'number', 'title')}


def resolve_milestone(github, repository, iteration):
    values = rest_pages(github, '/repos/' + repository + '/milestones?state=all')
    identities = [milestone_identity(value) for value in values]
    require(len({m['id'] for m in identities}) == len(identities) and
            len({m['number'] for m in identities}) == len(identities),
            'Ambiguous native milestone identities.', 'projects_blocked')
    matches = [m for m, value in zip(identities, values) if m['title'] == iteration and value['state'] == 'open']
    require(len(matches) == 1, 'Current native milestone missing or ambiguous; no scope creation is permitted.', 'projects_blocked')
    return matches[0]


def issue_scope(issue, board):
    if issue.get('milestone') is None:
        return None
    observed = milestone_identity(issue['milestone'])
    expected = board['milestone']
    require(not (observed['number'] == expected['number'] or observed['id'] == expected['id'] or
                 observed['title'] == expected['title']) or observed == expected,
            'Native milestone identity changed or scope is ambiguous.', 'projects_blocked')
    return observed['id']


def add_item(github, project, issue):
    graph(github, '''mutation AddTicket($project:ID!,$issue:ID!){addProjectV2ItemById(
      input:{projectId:$project,contentId:$issue}){item{id}}}''', {'project': project, 'issue': issue})


def set_field(github, project, item, field, option):
    graph(github, '''mutation TicketField($project:ID!,$item:ID!,$field:ID!,$option:String!){
      updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,fieldId:$field,
      value:{singleSelectOptionId:$option}}){projectV2Item{id}}}''',
          {'project': project, 'item': item, 'field': field, 'option': option})


def rest_pages(github, path):
    result = []
    page = 1
    while True:
        separator = '&' if '?' in path else '?'
        values = github.request(path + separator + 'per_page=100&page=' + str(page))
        require(isinstance(values, list), 'Complete GitHub issue/dependency list required.', 'github_mismatch')
        result.extend(values)
        if len(values) < 100:
            return result
        page += 1


def issue_identity(issue, repository, number=None):
    require(isinstance(issue, dict) and type(issue.get('number')) is int and issue['number'] > 0 and
            (number is None or issue['number'] == number) and type(issue.get('id')) is int and issue['id'] > 0 and
            isinstance(issue.get('node_id'), str) and issue['node_id'] and 'pull_request' not in issue and
            issue.get('repository_url') == 'https://api.github.com/repos/' + repository and
            issue.get('html_url') == 'https://github.com/' + repository + '/issues/' + str(issue['number']) and
            isinstance(issue.get('state'), str) and issue['state'] in {'open', 'closed'} and
            (issue.get('state_reason') is None or isinstance(issue['state_reason'], str)) and isinstance(issue.get('body'), str) and
            isinstance(issue.get('title'), str) and isinstance(issue.get('labels'), list) and
            all(isinstance(label, dict) and isinstance(label.get('name'), str) and label['name'] for label in issue['labels']),
            'Exact GitHub issue identity/content could not be verified.', 'github_mismatch')
    return issue


def read_issue(github, repository, number):
    require(type(number) is int and number > 0, 'Read an exact positive GitHub issue number.', 'github_mismatch')
    return issue_identity(github.request('/repos/' + repository + '/issues/' + str(number)), repository, number)


def dependencies(github, repository, number):
    result = rest_pages(github, '/repos/' + repository + '/issues/' + str(number) + '/dependencies/blocked_by')
    for dependency in result:
        issue_identity(dependency, repository)
        observed = read_issue(github, repository, dependency['number'])
        require(all(dependency[key] == observed[key] for key in ('repository_url', 'number', 'id', 'node_id')),
                'Native blocker identity differs from the exact issue; reconcile GitHub dependency metadata before retrying.', 'github_mismatch')
    return result


def add_dependency(github, repository, number, blocker):
    github.request('/repos/' + repository + '/issues/' + str(number) + '/dependencies/blocked_by', {'issue_id': blocker})
