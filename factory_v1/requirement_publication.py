"""Host-only versioned docs and exact affected ticket publication; never writes main."""
import base64
import copy
import hashlib
import re
import sqlite3
from contextlib import closing

from . import planning, publication, requirements, tickets
from .planning import require
from .repositories import RepositoryError, reconcile


def verify_synthesis(item, record):
    from .skills import resolve
    selected = resolve(record['synthesis_request']['skills'])
    require(set(record.get('executions', {})) == {'to-spec', 'to-tickets'}, 'Actual selected synthesis required.', 'execution_required')
    for stage, execution in record['executions'].items():
        work = execution['work']
        expected = ({**selected['to-spec'], 'dependencies': []} if stage == 'to-spec' else
                    tickets.skill(item['ticket_work']['request']['skill']))
        require(work['skill'] == expected == execution['source'] and execution['input_digest'] == tickets.digest(work) and
                work['adaptation'] == requirements.ADAPTATION and work['direction'] == record['resolution'],
                'Selected synthesis source/input/adaptation changed.', 'execution_required')
        tickets.validate_execution(work, execution['evidence'], execution['result'])
    require(record['documents'] == record['executions']['to-spec']['result'] and
            record['tickets'] == record['executions']['to-tickets']['result'],
            'Only actual invoked synthesis outputs may publish.', 'execution_required')


def verify_publication(database, item, record, github):
    require(record.get('new_inputs') and record.get('new_revision') and
            set(record.get('executions', {})) == {'to-spec', 'to-tickets'},
            'Actual selected synthesis and versioned publication required.', 'publication_required')
    verify_synthesis(item, record)
    with closing(sqlite3.connect(':memory:')) as observation:
        database.backup(observation)
        shadow = copy.deepcopy(item)
        shadow['handoff'] = record['new_inputs']['handoff']
        require(tickets.pinned_inputs(observation, shadow, github) == record['new_inputs'],
                'Exact immutable updated specification changed.', 'stale_requirements')
    board = tickets.verified_board(item['ticket_work'], github, item['repository'], item['iteration_id'])
    for number in record['affected']:
        issue = tickets.gh.read_issue(github, item['repository'], number)
        require(requirements.issue_pin(issue) == record['updated_issues'][str(number)] and
                issue['title'] == record['updates'][str(number)]['title'] and
                tickets.external_contract(issue, record['new_inputs']) and issue['state'] == 'open' and
                sorted(l['name'] for l in issue['labels']) == sorted(l['name'] for l in record['old_issues'][str(number)]['labels']),
                'Updated exact issue publication drifted.', 'stale_requirements')
        deps = tickets.gh.dependencies(github, item['repository'], number)
        require(sorted([{k: d[k] for k in ('number', 'id', 'node_id')} for d in deps], key=lambda d: d['number']) == record['dependency_pins'][str(number)],
                'Native affected dependencies changed.', 'stale_requirements')
        member = tickets.gh.membership(github, board, item['repository'], issue)
        require(member is not None and member['id'] == record['memberships'][str(number)] and (tickets.gh.issue_scope(issue, board) == board['milestone']['id'] if 'milestone' in board else
                member['scope'] == board['scope']['options'][item['iteration_id']]),
                'Exact affected membership/scope changed.', 'scope_mismatch')


def version_documents(database, item, record, github):
    prefix = '/repos/' + item['repository']
    change_id = record['request']['change_id']
    branch = 'factory/revision/' + item['iteration_id'] + '/' + change_id
    path = 'docs/factory/' + item['iteration_id'] + '/revisions/' + change_id
    journal = record.setdefault('publication', {'branch': branch, 'path': path})
    require(journal['branch'] == branch and journal['path'] == path, 'Revision publication target changed.', 'scope_mismatch')
    if 'commit' not in journal:
        if 'tree' not in journal:
            parent = next(iter(record['old_revision'].values()))['spec']['commit']
            commit = github.request(prefix + '/git/commits/' + parent)
            require(commit.get('sha') == parent and isinstance(commit.get('tree', {}).get('sha'), str),
                    'Exact old publication tree unavailable.', 'publication_mismatch')
            blobs = []
            for name, content in record['documents'].items():
                raw = content.encode()
                blob = publication.object_sha('blob', raw)
                tickets.effect_check(database, item)
                created = github.request(prefix + '/git/blobs', {'encoding': 'base64', 'content': base64.b64encode(raw).decode()})
                require(created.get('sha') == blob, 'Exact versioned blob did not read back.', 'publication_mismatch')
                blobs.append({'path': path + '/' + name, 'mode': '100644', 'type': 'blob', 'sha': blob})
            tickets.effect_check(database, item)
            tree = github.request(prefix + '/git/trees', {'base_tree': commit['tree']['sha'], 'tree': blobs})
            require(isinstance(tree, dict) and isinstance(tree.get('sha'), str) and len(tree['sha']) == 40,
                    'Versioned tree unavailable.', 'publication_mismatch')
            journal.update(tree=tree['sha'], parent=parent)
            requirements.checkpoint(database, item, receipt=True)
        require(not journal.get('commit_intent'), 'Unknown commit outcome needs reconciliation; no repeated commit creation.', 'publication_uncertain')
        journal['commit_intent'] = True
        requirements.checkpoint(database, item)
        tickets.effect_check(database, item)
        result = github.request(prefix + '/git/commits', {'message': 'Factory requirement revision ' + change_id + '\n',
                                 'tree': journal['tree'], 'parents': [journal['parent']]})
        require(isinstance(result, dict) and isinstance(result.get('sha'), str) and len(result['sha']) == 40,
                'Versioned commit unavailable.', 'publication_mismatch')
        journal['commit'] = result['sha']
        requirements.checkpoint(database, item, receipt=True)
    ref_path = prefix + '/git/ref/heads/' + branch
    ref = github.request(ref_path)
    if ref is None:
        require(not journal.get('ref_intent'), 'Unknown revision ref outcome; no duplicate mutation.', 'publication_uncertain')
        journal['ref_intent'] = True
        requirements.checkpoint(database, item)
        tickets.effect_check(database, item)
        try:
            github.request(prefix + '/git/refs', {'ref': 'refs/heads/' + branch, 'sha': journal['commit']})
        except RepositoryError:
            pass
        ref = github.request(ref_path)
    require(publication.ref_sha(ref, branch) == journal['commit'], 'Revision branch moved or collided.', 'publication_mismatch')
    observed = github.request(prefix + '/git/commits/' + journal['commit'])
    require(observed.get('sha') == journal['commit'] and observed.get('tree', {}).get('sha') == journal['tree'] and
            [p.get('sha') for p in observed.get('parents', [])] == [journal['parent']],
            'Exact version commit/tree/parent did not read back.', 'publication_mismatch')
    return journal


def spec_issue(database, item, record, github):
    prefix = '/repos/' + item['repository']
    journal = record['publication']
    title = 'Factory requirement revision ' + item['iteration_id'] + '/' + record['request']['change_id']
    listed = tickets.gh.rest_pages(github, prefix + '/issues?state=all')
    matches = [i for i in listed if i.get('title') == title and 'pull_request' not in i]
    require(len(matches) <= 1, 'Duplicate revision specifications require reconciliation.', 'publication_uncertain')
    if not matches:
        require(not journal.get('issue_intent'), 'Uncertain spec creation; no duplicate issue.', 'publication_uncertain')
        journal['issue_intent'] = True
        requirements.checkpoint(database, item)
        tickets.effect_check(database, item)
        created = github.request(prefix + '/issues', {'title': title, 'body': record['documents']['milestone.md'], 'labels': ['ready-for-agent']})
        identity = tickets.gh.issue_identity(created, item['repository'])
        number = identity['number']
        journal['issue_identity'] = {k: identity[k] for k in ('number', 'id', 'node_id')}
        journal['issue_number'] = number
        requirements.checkpoint(database, item, receipt=True)
    else:
        number = tickets.gh.issue_identity(matches[0], item['repository'])['number']
    require(journal.get('issue_number', number) == number, 'Specification issue identity changed.', 'publication_mismatch')
    observed = tickets.gh.read_issue(github, item['repository'], number)
    identity = {k: observed[k] for k in ('number', 'id', 'node_id')}
    require(journal.get('issue_identity', identity) == identity, 'Created specification identity changed.', 'publication_mismatch')
    journal.update(issue_number=number, issue_identity=identity)
    requirements.checkpoint(database, item, receipt=True)
    return number


def updated_body(issue, ticket, inputs, old_inputs):
    parts = tickets.section_spans(issue['body'])
    require(parts is not None and tickets.external_contract(issue, old_inputs),
            'Invalid or ambiguous affected ticket body.', 'scope_mismatch')
    replacements = {'Parent': '#' + str(inputs['handoff']['issue']['number']),
                    'What to build': ticket['desired_behavior'],
                    'Requirement references': '\n'.join('- ' + inputs['documents']['milestone.md']['url'] + ' — ' + r['requirement'] for r in ticket['references']),
                    'Acceptance criteria': '\n'.join('- [ ] ' + c for c in ticket['acceptance_criteria'])}
    assert parts is not None
    if 'Change' in parts:
        context = issue['body'][slice(*parts['Context'])].strip()
        context = re.sub(r'\bParent[ \t]+#([^;\s]+)',
                         lambda match: 'Parent #' + str(inputs['handoff']['issue']['number']), context)
        context = re.sub(r'\bStories:[ \t]*([^.\r\n]+)\.',
                         lambda match: 'Stories: ' + ', '.join(r['requirement'] for r in ticket['references']) + '.', context)
        for name, document in old_inputs['documents'].items():
            context = context.replace(document['url'], inputs['documents'][name]['url'])
        replacements = {'Change': ticket['desired_behavior'], 'Done': '\n'.join('- ' + c for c in ticket['acceptance_criteria']),
                        'Context': context}
    elif 'Parent' not in parts:
        replacements.pop('Parent')
    body = issue['body']
    for name, value in sorted(replacements.items(), key=lambda pair: parts[pair[0]][0], reverse=True):
        start, end = parts[name]
        newline = '\r\n' if body[start - 2:start] == '\r\n' else '\n'
        value = value.replace('\r\n', '\n').replace('\n', newline)
        body = body[:start] + newline + value + newline * 2 + body[end:]
    require(tickets.external_contract({**issue, 'title': ticket['title'], 'body': body}, inputs),
            'Updated ticket must retain the admitted contract.', 'scope_mismatch')
    return body


def publish(database, project, iteration, operator, request, github):
    item, record = requirements.record_for(database, project, iteration, operator, request)
    require(set(request) == {'change_id'} and record['status'] in {'synthesized', 'publication-pending', 'published'},
            'Run actual selected resynthesis before publication.', 'execution_required')
    verify_synthesis(item, record)
    reconcile(database, item, github)
    requirements.checkpoint(database, item)
    if record['status'] == 'published':
        verify_publication(database, item, record, github)
        requirements.checkpoint(database, item)
        return item
    record['status'] = 'publication-pending'
    requirements.checkpoint(database, item)
    journal = version_documents(database, item, record, github)
    number = spec_issue(database, item, record, github)
    with closing(sqlite3.connect(':memory:')) as observation:
        database.backup(observation)
        shadow = copy.deepcopy(item)
        shadow['handoff'] = planning.read_publication(observation, shadow, journal['path'], record['documents'],
                                                     journal['commit'], number, github)
        record['new_inputs'] = tickets.pinned_inputs(observation, shadow, github)
    record['new_revision'] = {'spec': record['new_inputs']['handoff'], 'ticket_assignment': request['change_id'],
                              'ticket_inputs': tickets.digest([record['new_inputs'], record['tickets'], record['resolution']])}
    requirements.checkpoint(database, item)
    for ticket in record['tickets']:
        number = ticket['number']
        key = str(number)
        issue = tickets.gh.read_issue(github, item['repository'], number)
        expected = record.setdefault('updates', {}).get(key)
        if expected is None:
            require(requirements.issue_pin(issue) == record['issues'][key], 'Old issue changed before controlled update.', 'stale_requirements')
            expected = {'title': ticket['title'], 'body': updated_body(issue, ticket, record['new_inputs'], requirements.inputs(item, number))}
            record['updates'][key] = expected
            requirements.checkpoint(database, item)
        if issue['title'] != expected['title'] or issue['body'] != expected['body']:
            require(requirements.issue_pin(issue) == record['issues'][key] and key not in record.get('update_intents', []),
                    'Affected update drift or uncertain response requires reconciliation.', 'publication_uncertain')
            record.setdefault('update_intents', []).append(key)
            requirements.checkpoint(database, item)
            tickets.effect_check(database, item)
            try:
                github.request('/repos/' + item['repository'] + '/issues/' + key, expected, 'PATCH')
            except RepositoryError:
                pass
            issue = tickets.gh.read_issue(github, item['repository'], number)
        require(all(issue[k] == record['issues'][key][k] for k in ('number', 'id', 'node_id')) and
                issue['title'] == expected['title'] and issue['body'] == expected['body'],
                'Exact updated ticket title/body/identity must read back.', 'publication_mismatch')
        record.setdefault('updated_issues', {})[key] = requirements.issue_pin(issue)
        requirements.checkpoint(database, item, receipt=True)
        item.setdefault('requirement_authority', {})[key] = record['new_revision']
        requirements.checkpoint(database, item)
    verify_publication(database, item, record, github)
    requirements.sync_blocks(database, item, record, github)
    verify_publication(database, item, record, github)
    record['status'] = 'published'
    requirements.checkpoint(database, item)
    return item
