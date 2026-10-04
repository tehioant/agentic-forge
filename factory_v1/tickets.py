"""Skill-guided ticket stage and deterministic publication/frontier/reservation controls."""
import base64
import hashlib
import json
import re
import uuid
from pathlib import Path

from .planning import require, reload_locked, transition
from .repositories import RepositoryError, reconcile, save_iteration
from . import ticket_github as gh

PIN = '5c9fba69845c2519b9b35b9af42ae5142c21f8ca15ac2123dc2722002c8058ae'
ADAPTATION = 'factory-to-tickets-v1:automatic-batch;controlled-publication;no-dispatch'
INSTRUCTIONS = '''Load the attached selected to-tickets instructions and all pinned current-milestone
and provenance inputs. Follow context gathering, exploration where useful, tracer-bullet
vertical decomposition and blocking-edge drafting. Each slice must deliver a narrow
end-to-end observable behavior and be independently verifiable in one fresh context.
Only this current milestone becomes executable: future vision is not a ticket source.
Retain desired behavior, exact current spec/requirement references, acceptance criteria,
blockers, status and configured triage label. Validate references/cycles and order blockers
first. Record actual instruction/input load tool references, decomposition rationale and
observable results, correlated to this assignment. Catalogs and prebuilt external batches
are not execution evidence. Adapt ONLY the routine post-grilling quiz/batch approval to
automatic progression. Do not resolve missing product decisions by silence. Publication
uses the controlled adapter with native blockers, linked Projects schema and exact reads;
never mutate a parent spec, create/link a board, or dispatch implementation workers.'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def skill(descriptor):
    require(isinstance(descriptor, dict) and set(descriptor) == {'name', 'path', 'sha256'} and
            descriptor['name'] == 'to-tickets' and descriptor['sha256'] == PIN and
            isinstance(descriptor['path'], str) and Path(descriptor['path']).is_absolute(),
            'Select the exact pinned to-tickets source.', 'skill_blocked')
    path = Path(descriptor['path'])
    try:
        require(path.is_file() and not path.is_symlink(), 'Selected skill is not a regular snapshot.', 'skill_blocked')
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == PIN, 'Selected skill bytes changed.', 'skill_blocked')
        return {**descriptor, 'instructions': raw.decode(), 'dependencies': []}
    except (OSError, UnicodeError) as error:
        raise RepositoryError('skill_blocked', 'Selected to-tickets instructions unavailable.') from error


def pinned_inputs(database, item, github):
    require(item.get('planning', {}).get('status') == 'complete' and item.get('handoff'),
            'A completed pinned planning handoff is required.', 'planning_required')
    handoff = item['handoff']
    require(handoff['repository'] == item['repository'] and handoff['api_base'] == github.base,
            'Use the exact planning repository/capability.', 'capability_conflict')
    reconcile(database, item, github)
    commit = github.request('/repos/' + item['repository'] + '/git/commits/' + handoff['commit'])
    require(isinstance(commit, dict) and commit.get('sha') == handoff['commit'], 'Pinned commit unavailable.', 'publication_mismatch')
    docs = {}
    for name, ref in handoff['documents'].items():
        metadata = github.request('/repos/' + item['repository'] + '/contents/' + ref['path'] + '?ref=' + handoff['commit'])
        require(isinstance(metadata, dict) and metadata.get('path') == ref['path'] and metadata.get('type') == 'file' and
                metadata.get('encoding') == 'base64' and metadata.get('sha') == ref['blob'] and isinstance(metadata.get('content'), str),
                'Read back exact pinned planning document.', 'publication_mismatch')
        try:
            raw = base64.b64decode(metadata['content'].replace('\n', ''), validate=True)
            content = raw.decode('utf-8')
        except (ValueError, UnicodeError) as error:
            raise RepositoryError('publication_mismatch', 'Invalid pinned document encoding.') from error
        require(hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == ref['blob'],
                'Pinned document bytes changed.', 'publication_mismatch')
        docs[name] = {'content': content, 'sha256': hashlib.sha256(raw).hexdigest(), **ref}
    issue = gh.read_issue(github, item['repository'], handoff['issue']['number'])
    require(issue['id'] == handoff['issue']['id'] and issue['body'] == docs['milestone.md']['content'] and issue['state'] == 'open',
            'Pinned current milestone issue changed.', 'publication_mismatch')
    return {'handoff': handoff, 'documents': docs}


def active(item):
    require(item['status'] == 'active', 'Paused/out-of-iteration work cannot advance.', 'iteration_paused')


def synthesize(database, item, request, github):
    item = reload_locked(database, item)
    active(item)
    require(isinstance(request, dict) and set(request) == {'skill', 'tracker'}, 'Provide only selected skill and tracker configuration.')
    require(isinstance(request['tracker'], dict) and isinstance(request['tracker'].get('triage_label'), str) and
            request['tracker']['triage_label'].strip(), 'Configured tracker and triage label required.')
    selected = skill(request['skill'])
    inputs = pinned_inputs(database, item, github)
    previous = item.get('ticket_work')
    if previous and previous['request'] == request and previous['inputs'] == inputs:
        return item
    require(previous is None, 'Ticket scope already assigned; material revision requires explicit planning reassignment.', 'ticket_conflict')
    # Schema access is checked at publication, so missing Projects never discards synthesis.
    item['ticket_work'] = {'assignment_id': str(uuid.uuid4()), 'stage': 'to-tickets', 'status': 'synthesis_pending',
                           'request': request, 'skill': selected, 'adaptation': ADAPTATION,
                           'inputs': inputs, 'input_digest': digest(inputs), 'instructions': INSTRUCTIONS}
    transition(item, 'ticket_synthesis_pending')
    save_iteration(database, item)
    return item


def validate_contract(title, behavior, criteria, refs, inputs, document_names=('milestone.md',)):
    require(all(isinstance(value, str) and value.strip() for value in (title, behavior)),
            'Provide a substantive work-item title and desired behavior.')
    require(isinstance(criteria, list) and criteria and all(isinstance(c, str) and c.strip() for c in criteria),
            'Verifiable acceptance criteria required.')
    documents = {inputs['documents'][name]['url']: inputs['documents'][name]['content'] for name in document_names}
    require(isinstance(refs, list) and refs and all(isinstance(r, dict) and set(r) == {'spec', 'requirement'} and
            isinstance(r['spec'], str) and r['spec'] in documents and isinstance(r['requirement'], str) and r['requirement'].strip()
            for r in refs), 'Reference only immutable assigned scope and named requirements.')
    for reference in refs:
        milestone = documents[reference['spec']]
        story_section = milestone.split('## User Stories', 1)[-1].split('\n## ', 1)[0]
        story_numbers = set(re.findall(r'^([1-9][0-9]*)\. ', story_section, re.MULTILINE))
        requirement = reference['requirement']
        story = re.fullmatch(r'US-([1-9][0-9]*)', requirement)
        require((story is not None and story[1] in story_numbers) or
                (story is None and requirement in milestone), 'Requirement reference is not defined in the pinned assigned scope.')


def external_contract(issue, inputs, document_names=('milestone.md',)):
    headings = list(re.finditer(r'^## ([^\n]+)\n', issue['body'], re.MULTILINE))
    sections = {}
    for index, heading in enumerate(headings):
        name = heading[1].strip()
        if name in sections:
            return False
        end = headings[index + 1].start() if index + 1 < len(headings) else len(issue['body'])
        sections[name] = issue['body'][heading.end():end].strip()
    if not all(sections.get(name) for name in ('What to build', 'Requirement references', 'Acceptance criteria', 'Blocked by', 'Status')):
        return False
    status = re.fullmatch(r'(ready|active|done|blocked|deferred)(?: \([^\n()]+\)\.?)?', sections['Status'])
    if status is None:
        return False
    refs = []
    for line in sections['Requirement references'].splitlines():
        if not line.strip():
            continue
        reference = re.fullmatch(r'- (.+) — (.+)', line)
        if reference is None:
            return False
        spec, requirement = reference.groups()
        link = re.fullmatch(r'\[[^\]]+\]\(([^)]+)\)', spec)
        refs.append({'spec': link[1] if link else spec, 'requirement': requirement})
    criteria = []
    for line in sections['Acceptance criteria'].splitlines():
        if not line.strip():
            continue
        criterion = re.fullmatch(r'- \[[ xX]\] (.*)', line)
        if criterion is None:
            return False
        criteria.append(criterion[1])
    try:
        validate_contract(issue['title'], sections['What to build'], criteria, refs, inputs, document_names)
    except RepositoryError:
        return False
    return status[1]


def validate_batch(batch, inputs, iteration, label):
    require(isinstance(batch, list) and batch, 'Run synthesis to produce nonempty vertical slices.')
    keys = {'key', 'title', 'desired_behavior', 'references', 'acceptance_criteria', 'blockers', 'status', 'iteration', 'triage_label'}
    known = {}
    for ticket in batch:
        require(isinstance(ticket, dict) and set(ticket) == keys and
                isinstance(ticket['key'], str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', ticket['key']) and
                ticket['key'] not in known, 'Unique ticket identifiers and exact current-only contract required.')
        validate_contract(ticket['title'], ticket['desired_behavior'], ticket['acceptance_criteria'], ticket['references'], inputs)
        require(ticket['iteration'] == iteration and ticket['status'] == 'ready' and ticket['triage_label'] == label,
                'Deferred/out-of-iteration work is not executable synthesis.')
        blockers = ticket['blockers']
        require(isinstance(blockers, list) and all(isinstance(b, str) for b in blockers) and len(set(blockers)) == len(blockers),
                'Use unique blocking references.')
        known[ticket['key']] = ticket
    ordered, visiting, done = [], set(), set()
    for root in known:
        if root in done:
            continue
        visiting.add(root)
        stack = [(root, iter(known[root]['blockers']))]
        while stack:
            key, blockers = stack[-1]
            blocker = next(blockers, None)
            if blocker is None:
                stack.pop()
                visiting.remove(key)
                done.add(key)
                ordered.append(known[key])
                continue
            require(blocker in known, 'Unknown blocker reference.')
            require(blocker not in visiting, 'Ticket blocking graph contains a cycle.')
            if blocker not in done:
                visiting.add(blocker)
                stack.append((blocker, iter(known[blocker]['blockers'])))
    return ordered


def complete_synthesis(database, item, request, github):
    item = reload_locked(database, item)
    active(item)
    work = item.get('ticket_work')
    require(work is not None, 'Start the correlated to-tickets stage first.', 'synthesis_required')
    require(isinstance(request, dict) and set(request) == {'assignment_id', 'input_digest', 'adaptation', 'execution', 'tickets'} and
            request['assignment_id'] == work['assignment_id'] and request['input_digest'] == work['input_digest'] and
            request['adaptation'] == ADAPTATION, 'Synthesis result is stale or uncorrelated.', 'ticket_conflict')
    skill(work['request']['skill'])
    require(pinned_inputs(database, item, github) == work['inputs'], 'Pinned synthesis inputs changed.', 'ticket_conflict')
    evidence = validate_execution(work, request['execution'], request['tickets'])
    batch = validate_batch(request['tickets'], work['inputs'], item['iteration_id'], work['request']['tracker'].get('triage_label'))
    completion_digest = digest(request)
    require(work.get('completion_digest', completion_digest) == completion_digest, 'Completed synthesis cannot be replaced.', 'ticket_conflict')
    already_completed = 'completion_digest' in work
    work.update(tickets=batch, execution=evidence, completion_digest=completion_digest)
    if not already_completed:
        work['status'] = 'publication_pending'
    transition(item, 'ticket_publication_pending')
    checkpoint(database, item)
    return publish(database, item, github)


def validate_execution(work, evidence, result):
    require(isinstance(evidence, dict) and set(evidence) == {'run_id', 'loads', 'decomposition', 'result_digest'} and
            isinstance(evidence['run_id'], str) and evidence['run_id'].strip() and
            isinstance(evidence['decomposition'], str) and evidence['decomposition'].strip() and
            evidence['result_digest'] == digest(result), 'Actual correlated synthesis execution evidence required.', 'execution_evidence_required')
    expected = {work['skill']['path']: PIN, **{d['url']: d['sha256'] for d in work['inputs']['documents'].values()},
                **work.get('load_sources', {})}
    loads = evidence['loads']
    require(isinstance(loads, list) and len(loads) == len(expected) and
            all(isinstance(l, dict) and set(l) == {'source', 'sha256', 'tool_reference'} and
                isinstance(l.get('source'), str) and isinstance(l.get('sha256'), str) and
                l['source'] in expected and l['sha256'] == expected[l['source']] and
                isinstance(l.get('tool_reference'), str) and l['tool_reference'].strip() for l in loads) and
            {l['source'] for l in loads} == set(expected),
            'Catalogs or externally prepared batches do not prove selected instruction/input loads.', 'execution_evidence_required')
    return evidence


def checkpoint(database, item):
    save_iteration(database, item)
    database.commit()


def body(item, ticket, numbers):
    work = item['ticket_work']
    marker = f"<!-- factory-ticket:{work['assignment_id']}:{ticket['key']} -->"
    blockers = '\n'.join('- #' + str(numbers[b]) for b in ticket['blockers']) or 'None (can start immediately).'
    refs = '\n'.join('- ' + r['spec'] + ' — ' + r['requirement'] for r in ticket['references'])
    criteria = '\n'.join('- [ ] ' + c for c in ticket['acceptance_criteria'])
    return (f"## Parent\n\n#{work['inputs']['handoff']['issue']['number']}\n\n## What to build\n\n{ticket['desired_behavior']}"
            f"\n\n## Requirement references\n\n{refs}\n\n## Acceptance criteria\n\n{criteria}\n\n## Blocked by\n\n{blockers}"
            f"\n\n## Status\n\n{ticket.get('status', 'ready')} (GitHub Projects is authoritative for current progress)."
            f"\n\n## Iteration\n\n{item['iteration_id']}\n\n## Triage\n\n{ticket['triage_label']}\n\n{marker}\n")


def verified_board(work, github, repository, iteration):
    board = gh.resolve_board(github, repository, work['request']['tracker'])
    if 'scope' in board:
        require(iteration in board['scope']['options'], 'Current iteration scope option missing.', 'projects_blocked')
    else:
        board['milestone'] = gh.resolve_milestone(github, repository, iteration)
    require(work.get('board', board) == board, 'Pinned board/schema changed; controller review required.', 'projects_blocked')
    return board


def publish(database, item, github):
    # Caller holds repository/state lock across every durable network intent.
    item = reload_locked(database, item)
    active(item)
    work = item.get('ticket_work')
    require(work is not None and work.get('tickets') and work.get('execution'), 'Actual ticket synthesis must complete first.', 'synthesis_required')
    skill(work['request']['skill'])
    require(pinned_inputs(database, item, github) == work['inputs'], 'Pinned scope changed.', 'ticket_conflict')
    try:
        board = verified_board(work, github, item['repository'], item['iteration_id'])
        work['board'] = board
        checkpoint(database, item)
        if work.get('publication_complete'):
            item = frontier(database, item, github)
            item['ticket_work']['status'] = 'published'
            item['ticket_work'].pop('blocker', None)
            checkpoint(database, item)
            return item
        numbers = {k: v['number'] for k, v in work.get('published', {}).items()}
        for ticket in work['tickets']:
            key = ticket['key']
            expected_body = body(item, ticket, numbers)
            marker = f"<!-- factory-ticket:{work['assignment_id']}:{key} -->"
            listed = gh.rest_pages(github, '/repos/' + item['repository'] + '/issues?state=all')
            require(all(isinstance(i, dict) and (i.get('body') is None or isinstance(i['body'], str)) for i in listed),
                    'Malformed issue list content; restore complete issue bodies before publication.', 'github_mismatch')
            candidates = [i for i in listed if marker in (i.get('body') or '')]
            require(len(candidates) <= 1, 'Duplicate ticket marker requires controller investigation.', 'publication_uncertain')
            intent = work.setdefault('intents', {}).setdefault(key, {})
            if not candidates:
                require(not intent.get('issue'), 'Lost issue response/absent readback: investigate before retry; never create a duplicate.', 'publication_uncertain')
                intent['issue'] = {'title': ticket['title'], 'body': expected_body}
                checkpoint(database, item)  # BEFORE mutation, including lost response/process death.
                payload = {'title': ticket['title'], 'body': expected_body, 'labels': [ticket['triage_label']]}
                if 'milestone' in board:
                    payload['milestone'] = board['milestone']['number']
                created = github.request('/repos/' + item['repository'] + '/issues', payload)
                created = gh.issue_identity(created, item['repository'])
                intent['identity'] = {'number': created['number'], 'id': created['id'], 'node_id': created['node_id']}
                checkpoint(database, item)
                issue = gh.read_issue(github, item['repository'], created['number'])
            else:
                issue = gh.read_issue(github, item['repository'], gh.issue_identity(candidates[0], item['repository'])['number'])
            require(issue['number'] not in {item.get('work_item_number'), work['inputs']['handoff']['issue']['number']} and
                    issue['body'] == expected_body and issue['title'] == ticket['title'] and issue['state'] == 'open' and
                    {l.get('name') for l in issue['labels'] if isinstance(l, dict)} == {ticket['triage_label']},
                    'Exact published issue title/body/labels/state readback differs.', 'publication_mismatch')
            identity = {k: issue[k] for k in ('number', 'id', 'node_id')}
            require(intent.get('identity', identity) == identity and work.get('published', {}).get(key, identity) == identity,
                    'Issue identity replaced.', 'publication_mismatch')
            intent['identity'] = identity
            numbers[key] = issue['number']
            checkpoint(database, item)
            expected_ids = {work['published'][b]['id'] for b in ticket['blockers']}
            observed = gh.dependencies(github, item['repository'], issue['number'])
            actual_ids = {gh.issue_identity(d, item['repository'])['id'] for d in observed}
            require(actual_ids <= expected_ids and len(actual_ids) == len(observed), 'Unexpected native blocker relationships.', 'publication_mismatch')
            for blocker in expected_ids - actual_ids:
                require(blocker not in intent.get('dependencies', []),
                        'Lost native blocker write with absent relationship; investigate before retry.', 'publication_uncertain')
                intent.setdefault('dependencies', []).append(blocker)
                checkpoint(database, item)
                gh.add_dependency(github, item['repository'], issue['number'], blocker)
            observed = gh.dependencies(github, item['repository'], issue['number'])
            require({gh.issue_identity(d, item['repository'])['id'] for d in observed} == expected_ids and len(observed) == len(expected_ids),
                    'Native dependency readback differs.', 'publication_mismatch')
            member = gh.membership(github, board, item['repository'], issue)
            if member is None:
                require(not intent.get('membership'), 'Lost board write and absent membership; investigate before retry.', 'publication_uncertain')
                intent['membership'] = True
                checkpoint(database, item)
                gh.add_item(github, board['project_id'], issue['node_id'])
                member = gh.membership(github, board, item['repository'], issue)
                require(member is not None, 'Exact project membership missing after write.', 'publication_mismatch')
            target = {'status': board['status']['options'][work['request']['tracker']['statuses']['ready']]}
            if 'milestone' in board:
                require(gh.issue_scope(issue, board) == board['milestone']['id'],
                        'Exact native milestone scope readback differs.', 'publication_mismatch')
            else:
                target = {'scope': board['scope']['options'][item['iteration_id']], **target}
            for role, option in target.items():
                require(member[role] in {None, option}, 'Existing board progress/scope conflicts; do not overwrite external work.', 'publication_mismatch')
                if member[role] != option:
                    intent[role] = option
                    checkpoint(database, item)
                    gh.set_field(github, board['project_id'], member['id'], board[role]['id'], option)
            member = gh.membership(github, board, item['repository'], issue)
            require(member is not None and all(member[k] == v for k, v in target.items()), 'Exact project scope/progress readback differs.', 'publication_mismatch')
            work.setdefault('published', {})[key] = identity
            checkpoint(database, item)
        work['status'] = 'published'
        work['publication_complete'] = True
        work.pop('blocker', None)
        transition(item, 'ticket_frontier_ready')
        checkpoint(database, item)
        return frontier(database, item, github)
    except RepositoryError as error:
        work['status'] = 'blocked'
        work['blocker'] = {'code': error.code, 'message': str(error)}
        transition(item, 'ticket_publication_blocked')
        checkpoint(database, item)
        return item


def frontier(database, item, github):
    # GitHub is authority: inspect current board membership/status, never local batch eligibility.
    item = reload_locked(database, item)
    work = item.get('ticket_work')
    require(work is not None and work.get('board'), 'Verified linked board required.', 'projects_blocked')
    skill(work['request']['skill'])
    require(pinned_inputs(database, item, github) == work['inputs'], 'Pinned requirements changed.', 'ticket_conflict')
    board = verified_board(work, github, item['repository'], item['iteration_id'])
    status_options = {board['status']['options'][name]: role for role, name in work['request']['tracker']['statuses'].items()}
    rows = {}
    native_blockers = {}
    foreign_active = []
    for member in gh.project_items(github, board['project_id']):
        content = member.get('content')
        if not isinstance(content, dict) or content.get('repository', {}).get('nameWithOwner') != item['repository']:
            continue
        fields = gh.single_select_values(member)
        if 'milestone' in board:
            issue = gh.read_issue(github, item['repository'], content.get('number'))
            current_scope = gh.issue_scope(issue, board) == board['milestone']['id']
        else:
            current_scope = fields.get(board['scope']['id']) == board['scope']['options'][item['iteration_id']]
        if not current_scope:
            if status_options.get(fields.get(board['status']['id'])) == 'active':
                foreign_active.append(content.get('number'))
            continue
        if 'milestone' not in board:
            issue = gh.read_issue(github, item['repository'], content.get('number'))
        for pinned in work.get('published', {}).values():
            if pinned['number'] == issue['number']:
                require(all(issue[k] == pinned[k] for k in ('number', 'id', 'node_id')),
                        'Published issue identity replaced.', 'github_mismatch')
        require(issue['node_id'] == content.get('id') and issue['number'] not in
                {item.get('work_item_number'), work['inputs']['handoff']['issue']['number']},
                'Board issue identity/parent scope mismatch.', 'github_mismatch')
        require(issue['number'] not in rows, 'Duplicate project issue membership.', 'github_mismatch')
        state = status_options.get(fields.get(board['status']['id']))
        require(state is not None, 'Unknown authoritative progress option.', 'projects_blocked')
        contract = external_contract(issue, work['inputs'])
        triaged = any(isinstance(l, dict) and l.get('name') == work['request']['tracker']['triage_label'] for l in issue['labels'])
        deps = gh.dependencies(github, item['repository'], issue['number'])
        native_blockers[issue['number']] = deps
        blockers = []
        for dep in deps:
            dep = gh.issue_identity(dep, item['repository'])
            blockers.append(dep['number'])
        require(len(blockers) == len(set(blockers)) and issue['number'] not in blockers, 'Invalid blocker references.', 'github_mismatch')
        textual = set(re.findall(r'#([1-9][0-9]*)', issue['body'].split('## Blocked by\n', 1)[-1].split('\n## ', 1)[0]))
        require(textual == {str(number) for number in blockers}, 'Native/textual blockers disagree.', 'github_mismatch')
        rows[issue['number']] = {'number': issue['number'], 'id': issue['id'], 'node_id': issue['node_id'],
                                'membership_id': member['id'], 'status': state, 'issue_state': issue['state'],
                                'state_reason': issue.get('state_reason'), 'blockers': blockers,
                                'contract_valid': bool(contract and triaged),
                                'held': contract in {'blocked', 'deferred'} or bool(re.search(
                                    r'^## Bug scope\n\n(?:deferred|recovery)\n', issue['body'], re.MULTILINE)) or any(
                                    bug['discovery']['scope'] == 'current' and bug['status'] not in {'published', 'duplicate'} and
                                    (issue['number'] in bug['discovery']['affected'] or
                                     bug.get('identity', {}).get('number') == issue['number'])
                                    for bug in item.get('bug_work', {}).values())}
    for dependencies in native_blockers.values():
        for dependency in dependencies:
            row = rows.get(dependency['number'])
            require(row is None or all(row[key] == dependency[key] for key in ('number', 'id', 'node_id')),
                    'Native blocker identity differs from the board issue observation; reconcile GitHub identities before retrying.', 'github_mismatch')
    visiting, visited = set(), set()
    for root in rows:
        if root in visited:
            continue
        visiting.add(root)
        stack = [(root, iter(rows[root]['blockers']))]
        while stack:
            number, blockers = stack[-1]
            dep = next(blockers, None)
            if dep is None:
                stack.pop()
                visiting.remove(number)
                visited.add(number)
                continue
            if dep not in rows:
                continue
            require(dep not in visiting, 'Authoritative blocker graph contains a cycle.', 'github_mismatch')
            if dep not in visited:
                visiting.add(dep)
                stack.append((dep, iter(rows[dep]['blockers'])))
    def completed(number):
        row = rows.get(number)
        # Unknown/out-of-scope blockers cannot be asserted delivered.
        return row is not None and row['issue_state'] == 'closed' and row['state_reason'] == 'completed' and row['status'] == 'done'
    for row in rows.values():
        row['admissible'] = bool(row['contract_valid'] and not row['held'] and row['issue_state'] == 'open' and
                                 row['status'] in {'ready', 'active'} and all(completed(b) for b in row['blockers']))
    eligible = [n for n, r in rows.items() if r['admissible'] and r['status'] == 'ready']
    work['frontier'] = {'authority': 'github', 'items': list(rows.values()), 'eligible': sorted(eligible),
                        'foreign_active': foreign_active,
                        'active': sorted(n for n, r in rows.items() if r['status'] == 'active' and r['issue_state'] == 'open')}
    checkpoint(database, item)
    return item


def reserve(database, item, github, number):
    item = frontier(database, item, github)
    active(item)
    work = item['ticket_work']
    require(work.get('publication_complete'), 'Partial/unverified publication cannot admit work.', 'publication_pending')
    require(not work['frontier']['foreign_active'], 'Out-of-iteration work is already active on GitHub.', 'reservation_conflict')
    old = item.get('reservation')
    if old:
        require(old.get('assignment_id') == work['assignment_id'] and old.get('input_digest') == work['input_digest'] and
                old.get('status') != 'invalidated', 'Material revision invalidated the reservation.', 'reservation_conflict')
        require(old['issue_number'] == number, 'Exactly one reservation already exists.', 'reservation_conflict')
        require(number in work['frontier']['active'] or number in work['frontier']['eligible'],
                'Reserved work became blocked/deferred/out-of-scope.', 'ticket_ineligible')
    else:
        require(number in work['frontier']['eligible'], 'Blocked/deferred/out-of-iteration work cannot be reserved.', 'ticket_ineligible')
        require(not work['frontier']['active'], 'GitHub already has active work.', 'reservation_conflict')
        all_items = [json.loads(r[0]) for r in database.execute('SELECT payload FROM iterations')]
        require(not any(i.get('reservation') for i in all_items), 'One implementation reservation across the factory.', 'reservation_conflict')
        item['reservation'] = {'issue_number': number, 'run_id': str(uuid.uuid4()), 'status': 'pending',
                               'assignment_id': work['assignment_id'], 'input_digest': work['input_digest']}
        checkpoint(database, item)
    row = next(r for r in work['frontier']['items'] if r['number'] == number)
    require(row['admissible'], 'Reserved ticket has unresolved authoritative blockers or invalid contract.', 'ticket_ineligible')
    require(set(work['frontier']['active']) <= {number}, 'Conflicting authoritative active work.', 'reservation_conflict')
    if row['status'] != 'active':
        gh.set_field(github, work['board']['project_id'], row['membership_id'], work['board']['status']['id'],
                     work['board']['status']['options'][work['request']['tracker']['statuses']['active']])
    item = frontier(database, item, github)
    require(item['ticket_work']['frontier']['active'] == [number] and
            not item['ticket_work']['frontier']['foreign_active'] and
            any(r['number'] == number and r['admissible'] for r in item['ticket_work']['frontier']['items']),
            'Exact eligible reservation progress did not read back.', 'publication_mismatch')
    item['reservation']['status'] = 'reserved'
    transition(item, 'ticket_reserved')
    checkpoint(database, item)
    return item
