"""Trusted discovered-bug synthesis and scoped, reconciled GitHub tracking only."""
import re
import uuid

from . import ticket_github as gh
from . import tickets
from .planning import require, reload_locked, text, transition
from .repositories import RepositoryError

ADAPTATION = 'factory-to-tickets-bug-v1:one-defect;compare-symptoms-and-scope;no-new-milestone;no-dispatch'
INSTRUCTIONS = '''Load the selected to-tickets instructions, pinned documents, discovery and
complete exact-repository issue observations. Follow context gathering and draft one
complete, independently verifiable bug slice using the common ticket contract.
Compare observable symptoms AND scope against every observed issue, retaining each
comparison and rationale. Do not equate titles or discovery IDs with duplicate identity.
Preserve reproduction/evidence, expected/actual behavior and available environment.
Current-milestone blockers join required work with explicit affected downstream edges;
unrelated defects remain deferred for next grilling; broken main has immediate recovery
priority outside the ordinary frontier. Do not invent requirements, products or milestones.
Adapt only routine quiz/batch approval to automatic trusted-stage completion. Retain
actual instruction/dependency/input loads and skill-guided work/results. Missing decisions
remain pending. Controlled publication owns writes and exact readbacks. No incident
detection, repair, worker launch, parent edit or new board/milestone creation is allowed.'''


def numbers(value):
    return (isinstance(value, list) and all(type(n) is int and n > 0 for n in value)
            and len(set(value)) == len(value))


def sections(body):
    matches = list(re.finditer(r'^## ([^\n]+)\n', body, re.MULTILINE))
    result = {}
    for index, match in enumerate(matches):
        require(match[1] not in result, 'Ambiguous issue sections.', 'github_mismatch')
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        result[match[1]] = body[match.end():end].strip()
    return result


def normalized(value):
    return ' '.join(value.casefold().split())


def fingerprint(discovery, item):
    scope = [item['repository'], discovery['scope']]
    if discovery['scope'] == 'current':
        scope.append(item['iteration_id'])
    return tickets.digest([scope, *[normalized(discovery[k]) for k in ('symptoms', 'expected', 'actual')]])


def validate_discovery(value):
    keys = {'symptoms', 'reproduction', 'evidence', 'expected', 'actual', 'environment', 'scope', 'scope_reason', 'affected'}
    require(isinstance(value, dict) and set(value) == keys and
            all(text(value[k]) for k in ('symptoms', 'expected', 'actual', 'scope_reason')) and
            isinstance(value['reproduction'], list) and all(text(s) for s in value['reproduction']) and
            isinstance(value['evidence'], list) and all(text(s) for s in value['evidence']) and
            (value['reproduction'] or value['evidence']) and
            (value['environment'] is None or text(value['environment'])) and
            isinstance(value['scope'], str) and value['scope'] in {'current', 'deferred', 'recovery'} and
            numbers(value['affected']), 'Provide defect evidence and explicit justified scope.', 'invalid_bug')
    require(bool(value['affected']) if value['scope'] == 'current' else not value['affected'],
            'Current blockers name affected work; deferred/recovery cannot extend the ordinary milestone.', 'invalid_bug')


def context(database, item, github):
    tickets.active(item)
    work = item.get('ticket_work')
    require(work is not None and work.get('publication_complete'), 'Publish current-milestone tickets first.', 'tickets_required')
    tickets.skill(work['request']['skill'])
    inputs = tickets.pinned_inputs(database, item, github)
    require(inputs == work['inputs'], 'Pinned ticket scope changed.', 'bug_conflict')
    return inputs, tickets.verified_board(work, github, item['repository'], item['iteration_id'])


def observations(item, github, board):
    listed = gh.rest_pages(github, '/repos/' + item['repository'] + '/issues?state=all')
    result = []
    seen = set()
    for value in listed:
        require(isinstance(value, dict), 'Malformed duplicate-search observations.', 'github_mismatch')
        if 'pull_request' in value:
            continue
        identity = gh.issue_identity(value, item['repository'])
        issue = gh.read_issue(github, item['repository'], identity['number'])
        require(all(identity[k] == issue[k] for k in ('number', 'id', 'node_id')) and issue['number'] not in seen,
                'Duplicate-search issue identity changed.', 'github_mismatch')
        seen.add(issue['number'])
        result.append(observe(item, github, board, issue))
    return sorted(result, key=lambda o: o['issue']['number'])


def observe(item, github, board, issue):
    member = gh.membership(github, board, item['repository'], issue)
    scope = gh.issue_scope(issue, board) if 'milestone' in board else member and member['scope']
    return {'issue': issue, 'scope': scope, 'membership': member,
            'dependencies': [{k: d[k] for k in ('number', 'id', 'node_id')}
                             for d in gh.dependencies(github, item['repository'], issue['number'])]}


def synthesize(database, item, request, github):
    item = reload_locked(database, item)
    require(isinstance(request, dict) and set(request) == {'discovery_id', 'skill', 'discovery'} and
            isinstance(request['discovery_id'], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', request['discovery_id']),
            'Provide stable discovery identity, selected skill and defect.', 'invalid_bug')
    validate_discovery(request['discovery'])
    selected = tickets.skill(request['skill'])
    inputs, board = context(database, item, github)
    require(request['skill'] == item['ticket_work']['request']['skill'], 'Use the current selected to-tickets source.', 'skill_blocked')
    key = fingerprint(request['discovery'], item)
    ids = item.setdefault('bug_discoveries', {})
    receipt = {'key': key, 'request_digest': tickets.digest(request)}
    require(request['discovery_id'] not in ids or ids[request['discovery_id']] == receipt,
            'Discovery identity cannot change evidence or scope.', 'bug_conflict')
    ids[request['discovery_id']] = receipt
    work = item.setdefault('bug_work', {}).get(key)
    if work:
        require(work['inputs'] == inputs and work['board'] == board, 'Bug assignment scope changed.', 'bug_conflict')
        require(set(request['discovery']['affected']) <= set(work['discovery']['affected']),
                'Additional affected edges require explicit controller investigation; never silently drop them.', 'bug_conflict')
        tickets.checkpoint(database, item)
        return item
    candidates = observations(item, github, board)
    affected = request['discovery']['affected']
    current_option = board['milestone']['id'] if 'milestone' in board else board['scope']['options'][item['iteration_id']]
    for number in affected:
        matches = [o for o in candidates if o['issue']['number'] == number]
        require(len(matches) == 1 and matches[0]['scope'] == current_option and
                matches[0]['issue']['state'] == 'open' and
                tickets.external_contract(matches[0]['issue'], inputs),
                'Affected work must be an open contracted current-milestone issue.', 'invalid_bug')
    assignment = str(uuid.uuid4())
    sources = {f'bug-discovery:{assignment}': tickets.digest(request['discovery']),
               f'bug-comparisons:{assignment}': tickets.digest(candidates),
               f'bug-adaptation:{assignment}': tickets.digest({'adaptation': ADAPTATION, 'instructions': INSTRUCTIONS})}
    item['bug_work'][key] = {'assignment_id': assignment, 'stage': 'to-tickets', 'status': 'synthesis_pending',
                             'skill': selected, 'request': request, 'adaptation': ADAPTATION, 'inputs': inputs,
                             'board': board, 'discovery': request['discovery'], 'candidates': candidates,
                             'load_sources': sources, 'instructions': INSTRUCTIONS,
                             'input_digest': tickets.digest([inputs, request['discovery'], candidates, board])}
    transition(item, 'bug_synthesis_pending')
    tickets.checkpoint(database, item)
    return item


def assigned(item, assignment):
    matches = [w for w in item.get('bug_work', {}).values() if w['assignment_id'] == assignment]
    require(len(matches) == 1, 'Use a current correlated bug assignment.', 'synthesis_required')
    return matches[0]


def validate_result(work, bug, item):
    keys = {'title', 'desired_behavior', 'references', 'acceptance_criteria', 'blockers', 'status', 'scope',
            'affected', 'triage_label', 'comparisons'}
    require(isinstance(bug, dict) and set(bug) == keys and bug['scope'] == work['discovery']['scope'] and
            bug['affected'] == work['discovery']['affected'] and numbers(bug['blockers']) and
            isinstance(bug['status'], str) and bug['status'] in ({'deferred'} if bug['scope'] == 'deferred' else {'ready', 'blocked'}) and
            bug['triage_label'] == item['ticket_work']['request']['tracker']['triage_label'],
            'Preserve the common bug contract and assigned classification.', 'invalid_bug')
    allowed = ('milestone.md',) if bug['scope'] == 'current' else ('vision.md', 'milestone.md')
    tickets.validate_contract(bug['title'], bug['desired_behavior'], bug['acceptance_criteria'], bug['references'], work['inputs'], allowed)
    known = {o['issue']['number']: o for o in work['candidates']}
    require(all(n in known and known[n]['scope'] == (work['board']['milestone']['id'] if 'milestone' in work['board'] else
                work['board']['scope']['options'][item['iteration_id']]) for n in bug['blockers']) and
            (bug['scope'] != 'deferred' or not bug['blockers']), 'Use observed current blocking issues.', 'invalid_bug')
    reachable = set(bug['blockers'])
    pending = list(reachable)
    while pending:
        number = pending.pop()
        for dep in known.get(number, {}).get('dependencies', []):
            if dep['number'] not in reachable:
                reachable.add(dep['number'])
                pending.append(dep['number'])
    require(not reachable.intersection(bug['affected']), 'Bug edges would create a dependency cycle.', 'invalid_bug')
    comparisons = bug['comparisons']
    require(isinstance(comparisons, list) and len(comparisons) == len(known) and
            all(isinstance(c, dict) and set(c) == {'number', 'symptoms_match', 'scope_match', 'rationale'} and
                type(c['number']) is int and c['number'] in known and type(c['symptoms_match']) is bool and
                type(c['scope_match']) is bool and text(c['rationale']) for c in comparisons) and
            {c['number'] for c in comparisons} == set(known),
            'Retain symptom AND scope comparisons for every search observation.', 'execution_evidence_required')
    duplicates = {c['number'] for c in comparisons if c['symptoms_match'] and c['scope_match']}
    for number, candidate in known.items():
        parts = sections(candidate['issue']['body'])
        if (all(normalized(parts.get(name, '')) == normalized(work['discovery'][field])
                for name, field in [('Observable symptoms', 'symptoms'), ('Expected behavior', 'expected'), ('Actual behavior', 'actual')]) and
                parts.get('Bug scope') == bug['scope'] and (bug['scope'] != 'current' or candidate['scope'] ==
                    (work['board']['milestone']['id'] if 'milestone' in work['board'] else work['board']['scope']['options'][item['iteration_id']]))):
            duplicates.add(number)
    require(len(duplicates) <= 1, 'Multiple duplicates require controller investigation.', 'publication_uncertain')
    return next(iter(duplicates), None)


def complete(database, item, request, github):
    item = reload_locked(database, item)
    require(isinstance(request, dict) and set(request) == {'assignment_id', 'input_digest', 'adaptation', 'execution', 'bug'},
            'Provide correlated bug synthesis and execution evidence.', 'invalid_bug')
    work = assigned(item, request['assignment_id'])
    tickets.active(item)
    tickets.skill(work['request']['skill'])
    inputs = tickets.pinned_inputs(database, item, github)
    require(request['input_digest'] == work['input_digest'] and request['adaptation'] == ADAPTATION and
            inputs == work['inputs'] and item.get('ticket_work', {}).get('inputs') == inputs,
            'Bug synthesis is stale or uncorrelated.', 'bug_conflict')
    evidence = tickets.validate_execution(work, request['execution'], request['bug'])
    duplicate = validate_result(work, request['bug'], item)
    completion_digest = tickets.digest(request)
    require(work.get('completion_digest', completion_digest) == completion_digest, 'Completed synthesis cannot be replaced.', 'bug_conflict')
    work.update(bug=request['bug'], execution=evidence, completion_digest=completion_digest, duplicate_number=duplicate)
    if work['status'] == 'synthesis_pending':
        work['status'] = 'publication_pending'
    tickets.checkpoint(database, item)
    return publish_locked(database, item, work, github)


def render(item, work):
    bug = work['bug']
    view = {**item, 'ticket_work': work, 'iteration_id': item['iteration_id'] if bug['scope'] == 'current' else bug['scope']}
    base = tickets.body(view, {**bug, 'key': 'bug'}, {n: n for n in bug['blockers']})
    marker = f"<!-- factory-ticket:{work['assignment_id']}:bug -->"
    discovery = work['discovery']
    extra = {'Bug scope': bug['scope'], 'Priority': 'immediate recovery' if bug['scope'] == 'recovery' else
             'required iteration work' if bug['scope'] == 'current' else 'next grilling only',
             'Scope rationale': discovery['scope_reason'], 'Observable symptoms': discovery['symptoms'],
             'Reproduction': '\n'.join('- ' + s for s in discovery['reproduction']) or 'Unavailable; see evidence.',
             'Evidence': '\n'.join('- ' + s for s in discovery['evidence']) or 'Reproduction steps above.',
             'Expected behavior': discovery['expected'], 'Actual behavior': discovery['actual'],
             'Environment': discovery['environment'] or 'Unavailable.',
             'Affected work': '\n'.join('- #' + str(n) for n in bug['affected']) or 'None.'}
    return base.replace(marker + '\n', '') + '\n' + '\n\n'.join('## ' + k + '\n\n' + v for k, v in extra.items()) + f'\n\n{marker}\n'


def edges(database, item, work, github, number, expected):
    observed = gh.dependencies(github, item['repository'], number)
    actual = {d['id'] for d in observed}
    require(actual <= expected and len(actual) == len(observed), 'Unexpected native bug dependency.', 'publication_mismatch')
    intents = work.setdefault('edge_intents', [])
    for blocker in sorted(expected - actual):
        intent = [number, blocker]
        require(intent not in intents, 'Uncertain edge is absent; investigate before retry.', 'publication_uncertain')
        intents.append(intent)
        tickets.checkpoint(database, item)
        gh.add_dependency(github, item['repository'], number, blocker)
    observed = gh.dependencies(github, item['repository'], number)
    require({d['id'] for d in observed} == expected and len(observed) == len(expected),
            'Exact native bug edges did not read back.', 'publication_mismatch')


def affected_edges(database, item, work, github, issue):
    for number in work['bug']['affected']:
        original = next(o for o in work['candidates'] if o['issue']['number'] == number)
        dependent = gh.read_issue(github, item['repository'], number)
        require(all(dependent[k] == original['issue'][k] for k in ('number', 'id', 'node_id')),
                'Affected issue identity replaced.', 'publication_mismatch')
        intents = work.setdefault('affected_intents', {})
        key = str(number)
        if key not in intents:
            require(dependent == original['issue'], 'Affected work changed before edge publication.', 'bug_conflict')
            expected_numbers = sorted({d['number'] for d in original['dependencies']} | {issue['number']})
            replacement = '## Blocked by\n\n' + '\n'.join('- #' + str(n) for n in expected_numbers) + '\n\n'
            target = re.sub(r'^## Blocked by\n.*?(?=^## |\Z)', lambda m: replacement,
                            dependent['body'], count=1, flags=re.MULTILINE | re.DOTALL)
            intents[key] = {'before': dependent['body'], 'body': target}
            tickets.checkpoint(database, item)
        intent = intents[key]
        require(dependent['body'] in {intent['before'], intent['body']}, 'Affected body conflicts with scoped edge update.', 'publication_mismatch')
        if dependent['body'] != intent['body']:
            github.request('/repos/' + item['repository'] + '/issues/' + str(number), {'body': intent['body']}, method='PATCH')
        dependent = gh.read_issue(github, item['repository'], number)
        require(dependent['body'] == intent['body'] and all(dependent[k] == original['issue'][k] for k in ('number', 'id', 'node_id')),
                'Affected issue body/identity did not read back.', 'publication_mismatch')
        edges(database, item, work, github, number, {d['id'] for d in original['dependencies']} | {issue['id']})


def verify_affected(item, work, github, issue):
    for number in work['bug']['affected']:
        original = next(o for o in work['candidates'] if o['issue']['number'] == number)
        dependent = gh.read_issue(github, item['repository'], number)
        observation = observe(item, github, work['board'], dependent)
        require(all(dependent[k] == original['issue'][k] for k in ('number', 'id', 'node_id')) and
                observation['scope'] == original['scope'] and observation['membership'] is not None and
                any(d['number'] == issue['number'] and d['id'] == issue['id'] and d['node_id'] == issue['node_id']
                    for d in observation['dependencies']) and
                str(issue['number']) in re.findall(r'#([1-9][0-9]*)', sections(dependent['body']).get('Blocked by', '')),
                'Affected dependency/body/identity/scope readback differs.', 'publication_mismatch')


def publish(database, item, request, github):
    item = reload_locked(database, item)
    require(isinstance(request, dict) and set(request) == {'assignment_id'}, 'Select the exact bug assignment.', 'invalid_bug')
    return publish_locked(database, item, assigned(item, request['assignment_id']), github)


def verify_observation(item, work, observation):
    issue = observation['issue']
    board = work['board']
    parts = sections(issue['body'])
    allowed = ('milestone.md',) if work['bug']['scope'] == 'current' else ('vision.md', 'milestone.md')
    require(tickets.external_contract(issue, work['inputs'], allowed) and
            parts.get('Bug scope') == work['bug']['scope'] and all(parts.get(k) for k in
                ('Observable symptoms', 'Reproduction', 'Evidence', 'Expected behavior', 'Actual behavior', 'Environment', 'Priority')) and
            any(l['name'] == work['bug']['triage_label'] for l in issue['labels']),
            'Verified common bug contract/refs/triage required.', 'publication_mismatch')
    member = observation['membership']
    statuses = item['ticket_work']['request']['tracker']['statuses'].values()
    require(member is not None and member['status'] in {board['status']['options'][s] for s in statuses},
            'Exact bug board membership/progress unavailable.', 'publication_mismatch')
    current_scope = board['milestone']['id'] if 'milestone' in board else board['scope']['options'][item['iteration_id']]
    require(observation['scope'] == (current_scope if work['bug']['scope'] == 'current' else None),
            'Bug scope changed.', 'publication_mismatch')
    textual = {int(n) for n in re.findall(r'#([1-9][0-9]*)', parts['Blocked by'])}
    require(textual == {d['number'] for d in observation['dependencies']},
            'Native/textual bug blockers disagree.', 'publication_mismatch')
    if work.get('observation'):
        previous = sections(work['observation']['issue']['body'])
        require(issue['title'] == work['observation']['issue']['title'] and
                {k: v for k, v in parts.items() if k not in {'Status', 'Blocked by'}} ==
                {k: v for k, v in previous.items() if k not in {'Status', 'Blocked by'}},
                'Published bug evidence/requirements changed.', 'publication_mismatch')


def publish_locked(database, item, work, github):
    require(work.get('bug') and work.get('execution'), 'Actual bug synthesis must complete first.', 'synthesis_required')
    tickets.skill(work['request']['skill'])
    tickets.active(item)
    try:
        inputs, board = context(database, item, github)
        require(inputs == work['inputs'] and board == work['board'], 'Pinned bug scope changed.', 'bug_conflict')
        if work.get('publication_complete'):
            issue = gh.read_issue(github, item['repository'], work['identity']['number'])
            require(all(issue[k] == work['identity'][k] for k in ('number', 'id', 'node_id')),
                    'Published bug identity replaced.', 'publication_mismatch')
            observation = observe(item, github, board, issue)
            verify_observation(item, work, observation)
            verify_affected(item, work, github, issue)
            work.update(observation=observation, status='duplicate' if work.get('duplicate_number') is not None else 'published')
            work.pop('blocker', None)
            tickets.checkpoint(database, item)
            return tickets.frontier(database, item, github)
        observed = observations(item, github, board)
        duplicate = work.get('duplicate_number')
        if duplicate is not None:
            candidate = next(o for o in work['candidates'] if o['issue']['number'] == duplicate)
            current = next((o for o in observed if o['issue']['number'] == duplicate), None)
            require(current == candidate, 'Duplicate changed; investigate before adoption.', 'bug_conflict')
            verify_observation(item, work, current)
            require({d['number'] for d in current['dependencies']} == set(work['bug']['blockers']),
                    'Duplicate blocker implications must match synthesis.', 'publication_mismatch')
            issue = current['issue']
        else:
            expected_body = render(item, work)
            marker = f"<!-- factory-ticket:{work['assignment_id']}:bug -->"
            matches = [o for o in observed if marker in o['issue']['body']]
            require(len(matches) <= 1, 'Duplicate publication marker requires investigation.', 'publication_uncertain')
            if not matches:
                require(not work.get('issue_intent'), 'Uncertain issue is absent; never retry creation.', 'publication_uncertain')
                unresolved = [bug for bug in item.get('bug_work', {}).values() if bug is not work and
                              bug.get('issue_intent') and not bug.get('publication_complete')]
                require(not unresolved, 'Another uncertain bug creation must reconcile before any new issue write.', 'publication_uncertain')
                require(observed == work['candidates'], 'Duplicate-search observations changed; do not create blindly.', 'bug_conflict')
                payload = {'title': work['bug']['title'], 'body': expected_body, 'labels': [work['bug']['triage_label']],
                           'milestone': board['milestone']['number'] if work['bug']['scope'] == 'current' and 'milestone' in board else None}
                work['issue_intent'] = payload
                tickets.checkpoint(database, item)
                created = gh.issue_identity(github.request('/repos/' + item['repository'] + '/issues', payload), item['repository'])
                work['identity'] = {k: created[k] for k in ('number', 'id', 'node_id')}
                tickets.checkpoint(database, item)
                issue = gh.read_issue(github, item['repository'], created['number'])
            else:
                issue = matches[0]['issue']
            require(issue['body'] == expected_body and issue['title'] == work['bug']['title'] and issue['state'] == 'open' and
                    {l['name'] for l in issue['labels']} == {work['bug']['triage_label']},
                    'Exact bug body/title/status/triage readback differs.', 'publication_mismatch')
        identity = {k: issue[k] for k in ('number', 'id', 'node_id')}
        require(work.get('identity', identity) == identity and issue['number'] not in work['bug']['blockers'] + work['bug']['affected'] +
                [item.get('work_item_number'), inputs['handoff']['issue']['number']], 'Bug identity/scope conflict.', 'publication_mismatch')
        work['identity'] = identity
        tickets.checkpoint(database, item)
        known = {o['issue']['number']: o['issue'] for o in work['candidates']}
        if duplicate is None:
            edges(database, item, work, github, issue['number'], {known[n]['id'] for n in work['bug']['blockers']})
        member = gh.membership(github, board, item['repository'], issue)
        if member is None:
            require(not work.get('membership_intent'), 'Uncertain membership is absent; investigate before retry.', 'publication_uncertain')
            work['membership_intent'] = True
            tickets.checkpoint(database, item)
            gh.add_item(github, board['project_id'], issue['node_id'])
            member = gh.membership(github, board, item['repository'], issue)
            require(member is not None, 'Exact bug board membership missing.', 'publication_mismatch')
        if 'milestone' in board:
            require(gh.issue_scope(issue, board) == (board['milestone']['id'] if work['bug']['scope'] == 'current' else None),
                    'Bug native scope readback differs.', 'publication_mismatch')
        target = {'status': board['status']['options'][item['ticket_work']['request']['tracker']['statuses']['ready']]}
        if 'scope' in board:
            target['scope'] = board['scope']['options'][item['iteration_id']] if work['bug']['scope'] == 'current' else None
        if duplicate is None:
            for role, option in target.items():
                require(member[role] in {None, option}, 'Existing bug board scope/progress conflicts.', 'publication_mismatch')
                if member[role] != option:
                    work.setdefault('field_intents', {})[role] = option
                    tickets.checkpoint(database, item)
                    gh.set_field(github, board['project_id'], member['id'], board[role]['id'], option)
            member = gh.membership(github, board, item['repository'], issue)
            require(member is not None and all(member[k] == v for k, v in target.items()),
                    'Exact bug board status/scope did not read back.', 'publication_mismatch')
        affected_edges(database, item, work, github, issue)
        observation = observe(item, github, board, gh.read_issue(github, item['repository'], issue['number']))
        verify_observation(item, work, observation)
        work.update(status='duplicate' if duplicate is not None else 'published', publication_complete=True, observation=observation)
        work.pop('blocker', None)
        transition(item, 'bug_tracked')
        tickets.checkpoint(database, item)
        return tickets.frontier(database, item, github)
    except RepositoryError as error:
        work.update(status='blocked', blocker={'code': error.code, 'message': str(error)})
        transition(item, 'bug_tracking_blocked')
        tickets.checkpoint(database, item)
        return item
