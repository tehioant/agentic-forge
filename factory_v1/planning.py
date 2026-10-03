"""Same-conversation skill assignment and read-only GitHub completion verifier."""
import base64
import hashlib
import json
import re
import uuid

from .repositories import RepositoryError, reconcile, save_iteration
from .skills import resolve

INSTRUCTIONS = """Carry out this assignment in the same originating conversation, not in a
noninteractive worker. Load and follow the attached pinned grill-me instructions,
which call grilling, then follow to-spec once clarification is sufficient.
Use grilling's design tree and rounds: ask the whole currently unblocked frontier,
number each question, recommend an answer, and wait for actual user answers.
Find accessible facts yourself within authorized capabilities; never invent facts
or user answers. The user owns product and technical decisions (including stack,
programming language, and user-facing language); the agent judges sufficiency.
Missing answers and unresolved current decisions remain pending in this exact
conversation. Preserve original question, recommendation, answer, message/actor
references and decision rationale in provenance.md; clarify contradictions with
the user, without rewriting earlier answers. Retain accepted recommendations as
interpretations separate from the user's original answer.
Local adaptations override only routine upstream confirmation prompts: there is
no additional routine shared-understanding, spec, test-seam or batch approval.
Discuss actual unresolved testing choices during grilling, not after synthesis.
When the frontier is empty, use to-spec to synthesize real prose, not categorized
raw-answer copies. Use its Problem Statement, Solution, User Stories,
Implementation Decisions, Testing Decisions, Out of Scope and Further Notes
sections. Explain finite milestone scope, acceptance and definition of done;
keep future capabilities in vision.md, not executable current requirements.
Publish milestone.md, vision.md and provenance.md as adjacent Markdown docs at
the assigned docs_path in the onboarded repository through the HOST's controlled
GitHub publication path. The new GitHub specification issue body must exactly
match milestone.md; label it ready-for-agent. Do not edit the parent spec issue.
GitHub and these immutable docs are the sole handoff for #8. Never run to-tickets,
resolve future execution/review skills, synthesize tickets or start implementation.
If publication is unavailable, retain pending state; do not build a publisher or
claim success. Completion requires the actual issue number and full commit SHA,
plus exact Markdown snapshots for readback. HOST handles live publication.
Required security/review/CI gates remain authoritative. No spending, paid fallback,
unrelated memory/credentials, beta access, inferred approvals or gate weakening.
"""
SECTIONS = ('Problem Statement', 'Solution', 'User Stories', 'Implementation Decisions',
            'Testing Decisions', 'Out of Scope', 'Further Notes')


def require(condition, message, code='invalid_planning'):
    if not condition:
        raise RepositoryError(code, message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def reload_locked(database, item):
    database.execute('BEGIN IMMEDIATE')
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                           (item['project_id'], item['iteration_id'])).fetchone()
    return json.loads(row[0])


def plan(database, item, request):
    item = reload_locked(database, item)
    require(item.get('repository_onboarding', {}).get('status') == 'verified',
            'Onboard the explicitly selected repository first.', 'onboarding_required')
    require(isinstance(request, dict) and set(request) == {'origin', 'expected_revision', 'skills'},
            'Provide only origin, expected_revision and the three selected skills.')
    require(request['origin'] == item['origin'], 'Use the exact originating conversation.')
    require(type(request['expected_revision']) is int and request['expected_revision'] >= 0,
            'Use a nonnegative integer planning revision.')
    skills = resolve(request['skills'])
    previous = item.get('planning')
    if previous and previous['request'] == request:
        return item
    require(request['expected_revision'] == (previous['revision'] if previous else 0),
            'Inspect the current revision before changing planning scope.', 'planning_conflict')
    if previous:
        item.setdefault('planning_history', []).append(previous)
    assignment_id = str(uuid.uuid4())
    item['planning'] = {'assignment_id': assignment_id, 'revision': request['expected_revision'] + 1,
                        'request': request, 'origin': item['origin'], 'status': 'pending',
                        'skills': skills, 'instructions': INSTRUCTIONS,
                        'docs_path': f"docs/factory/{item['iteration_id']}/{assignment_id}",
                        'pending_questions': []}
    item.pop('handoff', None)
    if item.get('ticket_work'):
        item.setdefault('ticket_history', []).append(item.pop('ticket_work'))
    if item.get('reservation'):
        item['reservation']['status'] = 'invalidated'
    transition(item, 'planning_pending')
    save_iteration(database, item)
    return item


def transition(item, stage):
    item['stage'] = stage
    item['correlation']['stage'] = stage
    item['execution_allowed'] = False


def complete(database, item, request, github):
    item = reload_locked(database, item)
    planning = item.get('planning')
    require(planning is not None, 'Start a same-conversation planning assignment first.', 'planning_required')
    common = {'assignment_id', 'origin', 'sufficient', 'rationale', 'reference', 'pending_questions'}
    require(isinstance(request, dict) and common <= request.keys() and type(request['sufficient']) is bool,
            'Provide exact assignment identity and agent clarification judgment.')
    require(set(request) == (common | {'documents', 'commit', 'issue_number'} if request['sufficient'] else common),
            'Sufficient completion needs Markdown snapshots and actual GitHub references; pending needs only unanswered questions.')
    require(request['assignment_id'] == planning['assignment_id'], 'Assignment is stale.', 'planning_conflict')
    require(request['origin'] == item['origin'], 'Completion must originate in the registered conversation.')
    require(text(request['rationale']) and text(request['reference']), 'Retain agent judgment and its reference.')
    pending = request['pending_questions']
    require(isinstance(pending, list) and all(text(q) for q in pending), 'Pending questions must be a list of actual open questions.')
    resolve(planning['request']['skills'])  # Revalidate even on restart/retry.
    if not request['sufficient']:
        require(planning['status'] != 'complete', 'Reassign explicitly before changing a completed milestone.', 'planning_conflict')
        planning['pending_questions'] = pending
        planning['judgment'] = {key: request[key] for key in ('sufficient', 'rationale', 'reference')}
        transition(item, 'planning_pending')
        save_iteration(database, item)
        return item
    require(not pending, 'Missing answers cannot be replaced by an agent sufficiency assertion.')
    docs = request['documents']
    require(isinstance(docs, dict) and set(docs) == {'milestone.md', 'vision.md', 'provenance.md'} and
            all(text(value) for value in docs.values()), 'Provide three separate synthesized Markdown snapshots.')
    milestone = docs['milestone.md']
    # This checks the upstream document shape, not natural-language truth. The
    # trusted conversational agent owns semantic synthesis and provenance review.
    headings = list(re.finditer(r'^## (.+)\s*$', milestone, re.MULTILINE))
    require(tuple(h.group(1).strip() for h in headings) == SECTIONS and
            all(milestone[h.end():headings[i + 1].start() if i + 1 < len(headings) else len(milestone)].strip()
                for i, h in enumerate(headings)), 'Use the real to-spec prose template with substantive sections.')
    commit = request['commit']
    number = request['issue_number']
    require(isinstance(commit, str) and re.fullmatch(r'[0-9a-f]{40}', commit) is not None and
            type(number) is int and number > 0, 'Use an exact immutable full commit SHA and positive issue number.')
    require(number != item.get('work_item_number'), 'Do not repurpose the unchanged parent work/spec issue.')
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    require(planning.get('completion_digest', digest) == digest,
            'Completed publication cannot be silently replaced; reassign explicitly.', 'planning_conflict')
    reconcile(database, item, github)  # Endpoint and immutable repository ID continuity.
    prefix = '/repos/' + item['repository']
    observation = github.request(prefix + '/git/commits/' + commit)
    require(isinstance(observation, dict) and observation.get('sha') == commit,
            'Read back the exact publication commit.', 'publication_mismatch')
    references = {}
    for name, content in docs.items():
        path = planning['docs_path'] + '/' + name
        metadata = github.request(prefix + '/contents/' + path + '?ref=' + commit)
        require(isinstance(metadata, dict) and metadata.get('type') == 'file' and
                metadata.get('path') == path and metadata.get('encoding') == 'base64' and
                isinstance(metadata.get('content'), str), 'Read back each exact immutable documentation file.', 'publication_mismatch')
        try:
            raw = base64.b64decode(metadata['content'].replace('\n', ''), validate=True)
        except ValueError as error:
            raise RepositoryError('publication_mismatch', 'Invalid documentation encoding.') from error
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        require(raw == content.encode('utf-8') and metadata.get('sha') == blob,
                'Published docs differ from the supplied synthesized Markdown snapshots.', 'publication_mismatch')
        references[name] = {'path': path, 'blob': blob,
                            'url': 'https://github.com/' + item['repository'] + '/blob/' + commit + '/' + path}
    issue = github.request(prefix + '/issues/' + str(number))
    url = 'https://github.com/' + item['repository'] + '/issues/' + str(number)
    require(isinstance(issue, dict) and type(issue.get('number')) is int and issue['number'] == number and
            type(issue.get('id')) is int and issue['id'] > 0 and issue.get('html_url') == url and
            issue.get('repository_url') == 'https://api.github.com/repos/' + item['repository'] and
            'pull_request' not in issue and issue.get('state') == 'open' and issue.get('body') == milestone and
            isinstance(issue.get('labels'), list) and
            any(isinstance(label, dict) and label.get('name') == 'ready-for-agent' for label in issue['labels']),
            'Exact open specification issue identity, label and immutable milestone body must read back.', 'publication_mismatch')
    previous = item.get('handoff')
    require(previous is None or previous['issue']['id'] == issue['id'],
            'Specification issue identity changed.', 'publication_mismatch')
    planning['status'] = 'complete'
    planning['pending_questions'] = []
    planning['judgment'] = {key: request[key] for key in ('sufficient', 'rationale', 'reference')}
    planning['completion_digest'] = digest
    item['handoff'] = {'repository': item['repository'], 'commit': commit,
                       'issue': {'number': number, 'id': issue['id'], 'url': url},
                       'documents': references, 'api_base': github.base}
    transition(item, 'planning_complete')
    save_iteration(database, item)
    return item
