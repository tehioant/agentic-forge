"""CLI lifecycle + real HTTP adapter tests. All tracker/agent artifacts are labeled fixtures."""
import copy
import hashlib
import json
import re
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from factory_v1.tests import test_planning


def conn(nodes):
    return {'nodes': nodes, 'pageInfo': {'hasNextPage': False, 'endCursor': None}}


def hash_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class TicketTests(unittest.TestCase):
    def setUp(self):
        self.planning = test_planning.PlanningTests()
        self.planning.setUp()
        self.addCleanup(self.planning.doCleanups)
        self.fixture = self.planning.fixture
        completion = self.planning.completion()
        self.planning.publication(completion)
        self.planning.published[self.planning.issue_path]['node_id'] = 'SPEC7'
        self.planning.published[self.planning.issue_path]['title'] = 'Pinned note milestone'
        self.assertEqual(self.planning.command('complete-planning', completion).returncode, 0)
        self.issues = {}
        self.deps = {}
        self.dependency_observations = {}
        self.project_item_overrides = {}
        self.members = {}
        self.boards = [{'id': 'P1', 'title': 'Approved linked board', 'closed': False}]
        self.fields = [
            {'id': 'STATUS', 'name': 'Status', 'options': [{'id': n, 'name': n} for n in ['Ready', 'Active', 'Blocked', 'Deferred', 'Done']]},
            {'id': 'SCOPE', 'name': 'Iteration', 'options': [{'id': 'M1', 'name': 'm1'}, {'id': 'M2', 'name': 'm2'}]},
        ]
        self.drop = None
        self.omit_effect = False
        self.bad_readback = None
        self.graph_error = False
        self.creation_event = None
        self.release_response = None
        parent = self.fixture.server.RequestHandlerClass
        case = self
        class Handler(parent):
            def do_GET(self):
                if self.path.startswith('/repos/example/product/issues?'):
                    case.fixture.calls.append(('GET', self.path))
                    self.reply(200, list(case.issues.values()))
                    return
                match = re.fullmatch(r'/repos/example/product/issues/(\d+)(/dependencies/blocked_by\?.*)?', self.path)
                if match and int(match[1]) in case.issues:
                    n = int(match[1])
                    case.fixture.calls.append(('GET', self.path))
                    if match[2]:
                        self.reply(200, case.dependency_observations.get(n, [case.issues[b] for b in case.deps.get(n, [])]))
                    else:
                        value = copy.deepcopy(case.issues[n])
                        if case.bad_readback == 'body': value['body'] += 'wrong'
                        if case.bad_readback == 'identity': value['id'] += 1
                        if case.bad_readback == 'repo': value['repository_url'] = 'https://api.github.com/repos/wrong/repo'
                        self.reply(200, value)
                    return
                super().do_GET()

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.fixture.calls.append(('POST', self.path, data))
                kind = None
                if self.path == '/graphql':
                    query, v = data['query'], data['variables']
                    if case.graph_error:
                        self.reply(200, {'errors': [{'message': 'insufficient Projects scope'}]})
                        return
                    if 'query LinkedProjects' in query:
                        offset = int(v['cursor'] or 0)
                        page = conn(case.boards[offset:offset + 100])
                        if offset + 100 < len(case.boards):
                            page['pageInfo'] = {'hasNextPage': True, 'endCursor': str(offset + 100)}
                        result = {'repository': {'projectsV2': page}}
                    elif 'query ProjectFields' in query:
                        result = {'node': {'id': 'P1', 'fields': conn(case.fields)}}
                    elif 'query ProjectItems' in query:
                        nodes = []
                        for n, member in case.members.items():
                            issue = case.issues[n]
                            nodes.append({'id': member['id'], 'content': {'id': issue['node_id'], 'number': n,
                                          'repository': {'nameWithOwner': 'example/product'}},
                                          'fieldValues': conn([{'field': {'id': k}, 'optionId': val} for k, val in member['fields'].items()])})
                            nodes[-1].update(case.project_item_overrides.get(n, {}))
                        result = {'node': {'id': 'P1', 'items': conn(nodes)}}
                    elif 'mutation AddTicket' in query:
                        kind = 'membership'
                        n = next(n for n, i in case.issues.items() if i['node_id'] == v['issue'])
                        if not case.omit_effect:
                            case.members[n] = {'id': 'ITEM' + str(n), 'fields': {}}
                        result = {'addProjectV2ItemById': {'item': {'id': 'ITEM' + str(n)}}}
                    elif 'mutation TicketField' in query:
                        kind = 'field'
                        n = next(n for n, m in case.members.items() if m['id'] == v['item'])
                        if not case.omit_effect:
                            case.members[n]['fields'][v['field']] = v['option']
                        result = {'updateProjectV2ItemFieldValue': {'projectV2Item': {'id': v['item']}}}
                    else:
                        self.reply(403, {})
                        return
                    response = {'data': result}
                elif self.path == '/repos/example/product/issues':
                    kind = 'issue'
                    n = 10 + len(case.issues)
                    response = {'number': n, 'id': 1000 + n, 'node_id': 'I' + str(n), 'state': 'open',
                                'state_reason': None, 'title': data['title'], 'body': data['body'],
                                'labels': [{'name': l} for l in data['labels']],
                                'repository_url': 'https://api.github.com/repos/example/product',
                                'html_url': 'https://github.com/example/product/issues/' + str(n)}
                    if not case.omit_effect: case.issues[n] = response
                elif '/dependencies/blocked_by' in self.path:
                    kind = 'dependency'
                    n = int(self.path.split('/')[5])
                    b = next(n for n, i in case.issues.items() if i['id'] == data['issue_id'])
                    if not case.omit_effect: case.deps.setdefault(n, []).append(b)
                    response = case.issues[b]
                else:
                    self.reply(403, {})
                    return
                if kind == 'issue' and case.creation_event is not None:
                    case.creation_event.set()
                    case.release_response.wait(timeout=10)
                    self.close_connection = True
                    return
                if kind is not None and case.drop == kind:
                    case.drop = None
                    self.close_connection = True
                    return
                self.reply(200, response)
        self.fixture.server.RequestHandlerClass = Handler
        path = Path(__file__).resolve().parents[1] / 'ticket_skills/to-tickets.md'
        self.start_request = {'skill': {'name': 'to-tickets', 'path': str(path),
                              'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
                              'tracker': {'project_id': None, 'status_field': 'Status', 'scope_field': 'Iteration',
                                          'statuses': {'ready': 'Ready', 'active': 'Active', 'blocked': 'Blocked', 'deferred': 'Deferred', 'done': 'Done'},
                                          'triage_label': 'agent-triage'}}

    def control(self, command, request=None, issue=None):
        args = [command, '--project', 'product', '--iteration', 'm1', '--api-base', self.fixture.base]
        if request is not None:
            path = self.fixture.root / 'tickets.json'
            path.write_text(json.dumps(request))
            args += ['--request', str(path)]
        if issue is not None: args += ['--issue', str(issue)]
        return self.fixture.cli(*args)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def inspect(self):
        return self.planning.inspect()

    def completion(self):
        item = self.ok(self.control('synthesize-tickets', self.start_request))
        work = item['ticket_work']
        url = work['inputs']['documents']['milestone.md']['url']
        def ticket(key, title, behavior, blockers):
            return {'key': key, 'title': title, 'desired_behavior': behavior, 'references': [{'spec': url, 'requirement': 'US-1'}],
                    'acceptance_criteria': ['CLI retains the note after a fresh process and rejects unsafe paths.'],
                    'blockers': blockers, 'status': 'ready', 'iteration': 'm1', 'triage_label': 'agent-triage'}
        # Deliberately reverse dependency order; expected behavior is blockers first.
        batch = [ticket('reopen', 'Reopen a retained note', 'A reader reopens a stored note through the French CLI.', ['create']),
                 ticket('create', 'Retain a note', 'A reader creates a local note through the French CLI.', []),
                 ticket('invalid', 'Refuse unsafe note paths', 'A reader receives a French error without touching other files.', [])]
        loads = [{'source': work['skill']['path'], 'sha256': work['skill']['sha256'], 'tool_reference': 'fixture:read-skill'}]
        loads += [{'source': d['url'], 'sha256': d['sha256'], 'tool_reference': 'fixture:read-' + n}
                  for n, d in work['inputs']['documents'].items()]
        return {'assignment_id': work['assignment_id'], 'input_digest': work['input_digest'], 'adaptation': work['adaptation'],
                'tickets': batch, 'execution': {'run_id': 'fixture-agent-run', 'loads': loads,
                'decomposition': 'Fixture agent drafts retained-note and reopen tracer bullets; reopen genuinely depends on retention, unsafe-path refusal is independent.',
                'result_digest': hash_json(batch)}}

    def publish(self):
        return self.ok(self.control('complete-tickets', self.completion()))

    def test_synthesis_publication_frontier_and_one_reservation(self):
        item = self.publish()
        self.assertEqual(item['ticket_work']['status'], 'published')
        self.assertEqual([i['title'] for i in self.issues.values()], ['Retain a note', 'Reopen a retained note', 'Refuse unsafe note paths'])
        self.assertEqual(self.deps, {11: [10]})
        self.assertIn('#10', self.issues[11]['body'])
        self.assertEqual(item['ticket_work']['frontier']['eligible'], [10, 12])
        self.assertEqual(self.control('reserve-ticket', issue=11).returncode, 2)
        reserved = self.ok(self.control('reserve-ticket', issue=10))
        self.assertEqual(reserved['reservation']['status'], 'reserved')
        self.assertFalse(reserved['execution_allowed'])
        self.assertIsNone(reserved['correlation']['worker_run_id'])
        self.assertEqual(self.control('reserve-ticket', issue=12).returncode, 2)
        self.assertEqual(self.ok(self.control('reserve-ticket', issue=10))['reservation'], reserved['reservation'])
        self.assertEqual(self.inspect()['reservation'], reserved['reservation'])
        self.assertFalse(any('/issues/7' in c[1] and c[0] != 'GET' for c in self.fixture.calls))

    def test_repeated_completion_and_publication_do_not_reset_progress(self):
        request = self.completion()
        self.ok(self.control('complete-tickets', request))
        self.ok(self.control('reserve-ticket', issue=10))
        result = self.ok(self.control('complete-tickets', request))
        self.assertEqual(result['ticket_work']['frontier']['active'], [10])
        self.ok(self.control('publish-tickets'))
        self.assertEqual(len(self.issues), 3)
        self.assertEqual(self.deps, {11: [10]})

    def test_authoritative_changes_block_defer_and_remove_scope(self):
        self.publish()
        self.members[10]['fields']['STATUS'] = 'Blocked'
        self.members[12]['fields']['STATUS'] = 'Deferred'
        self.assertEqual(self.ok(self.control('frontier'))['ticket_work']['frontier']['eligible'], [])
        self.assertEqual(self.control('reserve-ticket', issue=10).returncode, 2)
        self.assertEqual(self.control('reserve-ticket', issue=12).returncode, 2)
        self.members[10]['fields']['STATUS'] = 'Done'
        self.issues[10].update(state='closed', state_reason='completed')
        self.assertEqual(self.ok(self.control('frontier'))['ticket_work']['frontier']['eligible'], [11])
        self.members[11]['fields']['SCOPE'] = 'M2'
        self.assertEqual(self.control('reserve-ticket', issue=11).returncode, 2)

    def test_github_board_not_local_generated_batch_is_backlog_authority(self):
        self.publish()
        self.issues[20] = copy.deepcopy(self.issues[12])
        self.issues[20].update(number=20, id=1020, node_id='I20', html_url='https://github.com/example/product/issues/20', title='Current bug')
        self.members[20] = {'id': 'ITEM20', 'fields': {'STATUS': 'Ready', 'SCOPE': 'M1'}}
        self.assertEqual(self.ok(self.control('frontier'))['ticket_work']['frontier']['eligible'], [10, 12, 20])
        self.issues[20]['labels'] = []
        self.assertEqual(self.control('reserve-ticket', issue=20).returncode, 2)

    def test_missing_ambiguous_inaccessible_or_changed_board_is_durable_blocker(self):
        request = self.completion()
        self.boards = []
        blocked = self.ok(self.control('complete-tickets', request))
        self.assertEqual(blocked['ticket_work']['status'], 'blocked')
        self.assertEqual(blocked['ticket_work']['blocker']['code'], 'projects_blocked')
        self.assertTrue(blocked['ticket_work']['execution'])
        self.assertEqual(self.issues, {})
        self.boards = [{'id': 'P1', 'closed': False}, {'id': 'P2', 'closed': False}]
        self.assertEqual(self.ok(self.control('publish-tickets'))['ticket_work']['status'], 'blocked')
        self.boards.pop()
        self.graph_error = True
        self.assertEqual(self.ok(self.control('publish-tickets'))['ticket_work']['status'], 'blocked')
        self.graph_error = False
        self.assertEqual(self.ok(self.control('publish-tickets'))['ticket_work']['status'], 'published')
        self.fields[0]['id'] = 'REPLACED'
        self.assertEqual(self.control('frontier').returncode, 2)

    def test_lost_issue_response_reconciles_without_duplicate(self):
        request = self.completion()
        self.drop = 'issue'
        self.assertEqual(self.ok(self.control('complete-tickets', request))['ticket_work']['status'], 'blocked')
        self.assertEqual(len(self.issues), 1)
        self.assertEqual(self.ok(self.control('publish-tickets'))['ticket_work']['status'], 'published')
        self.assertEqual(len(self.issues), 3)

    def test_absent_uncertain_issue_is_not_retried(self):
        request = self.completion()
        self.drop, self.omit_effect = 'issue', True
        self.ok(self.control('complete-tickets', request))
        self.omit_effect = False
        result = self.ok(self.control('publish-tickets'))
        self.assertEqual(result['ticket_work']['blocker']['code'], 'publication_uncertain')
        self.assertEqual(self.issues, {})

    def test_lost_native_dependency_membership_and_field_responses_reconcile(self):
        request = self.completion()
        self.drop = 'membership'
        self.ok(self.control('complete-tickets', request))
        self.drop = 'field'
        self.ok(self.control('publish-tickets'))
        self.drop = 'dependency'
        self.ok(self.control('publish-tickets'))
        result = self.ok(self.control('publish-tickets'))
        self.assertEqual(result['ticket_work']['status'], 'published')
        self.assertEqual(len(self.issues), 3)
        self.assertEqual(len(self.members), 3)
        self.assertEqual(self.deps, {11: [10]})

    def test_reservation_lost_response_is_durable_and_reconciles(self):
        self.publish()
        self.drop = 'field'
        self.assertEqual(self.control('reserve-ticket', issue=10).returncode, 2)
        pending = self.inspect()['reservation']
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual(self.control('reserve-ticket', issue=12).returncode, 2)
        result = self.ok(self.control('reserve-ticket', issue=10))
        self.assertEqual(result['reservation']['run_id'], pending['run_id'])
        self.assertEqual(result['reservation']['status'], 'reserved')

    def test_catalog_prebuilt_or_stale_synthesis_is_refused(self):
        request = self.completion()
        before = self.inspect()
        for defect in ['catalog', 'input', 'skill', 'adaptation', 'result', 'empty', 'assignment']:
            bad = copy.deepcopy(request)
            if defect == 'catalog': bad['execution']['loads'] = []
            if defect == 'input': bad['input_digest'] = '0' * 64
            if defect == 'skill': bad['execution']['loads'][0]['sha256'] = '0' * 64
            if defect == 'adaptation': bad['adaptation'] = 'approval-needed'
            if defect == 'result': bad['execution']['result_digest'] = '0' * 64
            if defect == 'empty': bad['execution']['decomposition'] = ''
            if defect == 'assignment': bad['assignment_id'] = 'external-batch'
            with self.subTest(defect=defect):
                self.assertEqual(self.control('complete-tickets', bad).returncode, 2)
                self.assertEqual(self.inspect(), before)
        self.assertEqual(self.issues, {})

    def test_bad_contract_unknown_blockers_and_cycles_are_refused(self):
        request = self.completion()
        for defect in ['unknown', 'cycle', 'duplicate', 'future', 'deferred', 'reference', 'criteria', 'label']:
            bad = copy.deepcopy(request)
            if defect == 'unknown': bad['tickets'][0]['blockers'] = ['absent']
            if defect == 'cycle': bad['tickets'][1]['blockers'] = ['reopen']
            if defect == 'duplicate': bad['tickets'][1]['key'] = 'reopen'
            if defect == 'future': bad['tickets'][0]['iteration'] = 'm2'
            if defect == 'deferred': bad['tickets'][0]['status'] = 'deferred'
            if defect == 'reference': bad['tickets'][0]['references'][0]['spec'] = 'https://unrelated/spec'
            if defect == 'criteria': bad['tickets'][0]['acceptance_criteria'] = []
            if defect == 'label': bad['tickets'][0]['triage_label'] = 'unconfigured'
            bad['execution']['result_digest'] = hash_json(bad['tickets'])
            with self.subTest(defect=defect):
                self.assertEqual(self.control('complete-tickets', bad).returncode, 2)
        self.assertEqual(self.issues, {})

    def test_exact_issue_and_native_relationship_readbacks(self):
        request = self.completion()
        self.bad_readback = 'body'
        result = self.ok(self.control('complete-tickets', request))
        self.assertEqual(result['ticket_work']['blocker']['code'], 'publication_mismatch')
        self.bad_readback = None
        self.ok(self.control('publish-tickets'))
        self.issues[10]['body'] = self.issues[10]['body'].replace('None (can start immediately).', '- #11')
        self.deps[10] = [11]
        self.assertEqual(self.control('frontier').returncode, 2)  # Authoritative cycle.
        self.deps[10] = []
        self.assertEqual(self.control('frontier').returncode, 2)  # Native/text discrepancy.

    def test_concurrent_reservations_are_serialized(self):
        self.publish()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda n: self.control('reserve-ticket', issue=n), [10, 12]))
        self.assertEqual(sorted(r.returncode for r in results), [0, 2])
        self.assertEqual(len(self.ok(self.control('frontier'))['ticket_work']['frontier']['active']), 1)

    def test_skill_changes_and_material_planning_revision_invalidate_stage(self):
        request = self.completion()
        self.ok(self.control('complete-tickets', request))
        self.ok(self.control('reserve-ticket', issue=10))
        self.ok(self.planning.command('plan', dict(self.planning.plan_request, expected_revision=1)))
        self.assertNotIn('ticket_work', self.inspect())
        self.assertEqual(self.inspect()['reservation']['status'], 'invalidated')
        self.assertEqual(self.control('reserve-ticket', issue=10).returncode, 2)
        self.assertEqual(self.control('complete-tickets', request).returncode, 2)

    def test_explicit_linked_board_resolves_ambiguity_but_unlinked_is_refused(self):
        self.start_request['tracker']['project_id'] = 'P1'
        self.boards.append({'id': 'P2', 'closed': False})
        self.assertEqual(self.publish()['ticket_work']['status'], 'published')
        self.boards = [{'id': 'P2', 'closed': False}]
        self.assertEqual(self.control('frontier').returncode, 2)

    def test_missing_schema_never_omits_board_or_creates_issues(self):
        request = self.completion()
        self.fields[0]['options'].pop()
        blocked = self.ok(self.control('complete-tickets', request))
        self.assertEqual(blocked['ticket_work']['blocker']['code'], 'projects_blocked')
        self.assertEqual(self.issues, {})

    def test_changed_selected_instructions_block_completion(self):
        selected = self.fixture.root / 'selected-to-tickets.md'
        selected.write_bytes(Path(self.start_request['skill']['path']).read_bytes())
        self.start_request['skill']['path'] = str(selected)
        request = self.completion()
        selected.write_text('Different instructions')
        self.fixture.calls.clear()
        self.assertEqual(self.control('complete-tickets', request).returncode, 2)
        self.assertEqual(self.fixture.calls, [])
        self.assertEqual(self.issues, {})

    def test_unknown_requirement_is_refused(self):
        request = self.completion()
        request['tickets'][0]['references'][0]['requirement'] = 'US-999'
        request['execution']['result_digest'] = hash_json(request['tickets'])
        self.assertEqual(self.control('complete-tickets', request).returncode, 2)
        self.assertEqual(self.issues, {})

    def test_pending_publication_cannot_reserve_already_created_slice(self):
        request = self.completion()
        self.drop = 'dependency'
        item = self.ok(self.control('complete-tickets', request))
        self.assertEqual(item['ticket_work']['status'], 'blocked')
        self.assertEqual(self.control('reserve-ticket', issue=10).returncode, 2)
        self.assertNotIn('reservation', self.inspect())

    def test_external_active_scope_and_changed_reserved_blockers_refuse_admission(self):
        self.publish()
        self.members[12]['fields'].update(SCOPE='M2', STATUS='Active')
        self.assertEqual(self.control('reserve-ticket', issue=10).returncode, 2)
        self.members[12]['fields'].update(SCOPE='M1', STATUS='Ready')
        self.ok(self.control('reserve-ticket', issue=10))
        self.issues[10]['body'] = self.issues[10]['body'].replace('None (can start immediately).', '- #12')
        self.deps[10] = [12]
        self.assertEqual(self.control('reserve-ticket', issue=10).returncode, 2)

    def test_unchanged_parent_and_exact_scope_progress_readback(self):
        request = self.completion()
        self.omit_effect = True
        # Without exact issue readback nothing can advance to board/reservation.
        item = self.ok(self.control('complete-tickets', request))
        self.assertEqual(item['ticket_work']['status'], 'blocked')
        self.assertEqual(self.members, {})
        self.assertFalse(any(c[0] != 'GET' and '/issues/7' in c[1] for c in self.fixture.calls))

    def test_process_death_after_issue_write_reconciles_committed_intent(self):
        request = self.completion()
        path = self.fixture.root / 'crash-result.json'
        path.write_text(json.dumps(request))
        self.creation_event, self.release_response = threading.Event(), threading.Event()
        command = [sys.executable, '-m', 'factory_v1', '--state', str(self.fixture.state), '--operator-id', '42',
                   'complete-tickets', '--project', 'product', '--iteration', 'm1', '--api-base', self.fixture.base,
                   '--request', str(path)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        try:
            self.assertTrue(self.creation_event.wait(timeout=5))
            process.kill()
            process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            self.release_response.set()
            self.creation_event = None
        self.assertEqual(len(self.issues), 1)
        self.assertTrue(self.inspect()['ticket_work']['intents']['create']['issue'])
        self.assertEqual(self.ok(self.control('publish-tickets'))['ticket_work']['status'], 'published')
        self.assertEqual(len(self.issues), 3)

    def test_linked_board_pagination_finds_unambiguous_selected_board(self):
        self.boards = [{'id': 'C' + str(n), 'closed': True} for n in range(100)] + self.boards
        self.assertEqual(self.publish()['ticket_work']['status'], 'published')

    def test_malformed_execution_load_shapes_are_safe_cli_refusals(self):
        request = self.completion()
        before = self.inspect()
        for field in ('source', 'sha256', 'tool_reference'):
            for value in ([], {}, None, 42):
                with self.subTest(field=field, value=value):
                    bad = copy.deepcopy(request)
                    bad['execution']['loads'][0][field] = value
                    result = self.control('complete-tickets', bad)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    refusal = json.loads(result.stderr)
                    self.assertEqual(refusal['error'], 'execution_evidence_required')
                    self.assertTrue(refusal['message'])
                    self.assertEqual(self.inspect(), before)
                    self.assertEqual(self.issues, {})

    def test_malformed_project_and_issue_shapes_are_safe_cli_refusals(self):
        self.publish()
        before = self.inspect()
        malformed = [
            {'fieldValues': conn([{'field': {'id': 'STATUS'}, 'optionId': ['Ready']}])},
            {'fieldValues': conn([{'field': {'id': []}, 'optionId': 'Ready'}])},
            {'fieldValues': conn([{'field': None, 'optionId': 'Ready'}])},
            {'fieldValues': conn([None])},
            {'content': {'id': 'I10', 'number': 10, 'repository': []}},
            {'content': {'id': 'I10', 'number': [], 'repository': {'nameWithOwner': 'example/product'}}},
        ]
        for override in malformed:
            with self.subTest(override=override):
                self.project_item_overrides[10] = override
                result = self.control('frontier')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stderr)['error'], 'github_mismatch')
                self.assertEqual(self.inspect(), before)
        self.project_item_overrides.clear()
        for field, value in [('state', []), ('labels', [{'name': []}]), ('state_reason', {})]:
            original = copy.deepcopy(self.issues[10][field])
            with self.subTest(field=field):
                self.issues[10][field] = value
                result = self.control('frontier')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stderr)['error'], 'github_mismatch')
                self.assertEqual(self.inspect(), before)
            self.issues[10][field] = original

    def test_external_contract_requires_substance_and_current_references(self):
        item = self.publish()
        self.issues[20] = copy.deepcopy(self.issues[12])
        self.issues[20].update(number=20, id=1020, node_id='I20', html_url='https://github.com/example/product/issues/20', title='Current bug')
        self.members[20] = {'id': 'ITEM20', 'fields': {'STATUS': 'Ready', 'SCOPE': 'M1'}}
        good = copy.deepcopy(self.issues[20])
        url = item['ticket_work']['inputs']['documents']['milestone.md']['url']
        defects = [
            {'title': ' '},
            {'body': good['body'].replace('A reader receives a French error without touching other files.', '')},
            {'body': good['body'].replace('US-1', 'US-999')},
            {'body': good['body'].replace('CLI retains the note after a fresh process and rejects unsafe paths.', '')},
            {'body': good['body'].replace(url, 'https://unrelated/spec')},
            {'body': good['body'].replace('ready (GitHub Projects is authoritative for current progress).', '')},
            {'body': good['body'] + '\n## What to build\n\nConflicting behavior.\n'},
        ]
        for defect in defects:
            with self.subTest(defect=defect):
                self.issues[20] = {**good, **defect}
                snapshot = self.ok(self.control('frontier'))['ticket_work']['frontier']
                self.assertFalse(next(r for r in snapshot['items'] if r['number'] == 20)['contract_valid'])
                self.assertEqual(snapshot['eligible'], [10, 12])
                result = self.control('reserve-ticket', issue=20)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stderr)['error'], 'ticket_ineligible')
                self.assertNotIn('reservation', self.inspect())
                self.assertEqual(self.members[20]['fields']['STATUS'], 'Ready')
                snapshot = self.ok(self.control('frontier'))['ticket_work']['frontier']
                self.assertEqual(snapshot['eligible'], [10, 12])
                self.assertFalse(next(r for r in snapshot['items'] if r['number'] == 20)['contract_valid'])
        self.issues[20] = good
        self.assertEqual(self.ok(self.control('reserve-ticket', issue=20))['reservation']['issue_number'], 20)

    def test_generated_contract_rejects_the_same_missing_substance(self):
        request = self.completion()
        for field, value in [('title', ' '), ('desired_behavior', ''), ('acceptance_criteria', [' ']),
                             ('references', [{'spec': request['tickets'][0]['references'][0]['spec'], 'requirement': 'US-999'}])]:
            with self.subTest(field=field):
                bad = copy.deepcopy(request)
                bad['tickets'][0][field] = value
                bad['execution']['result_digest'] = hash_json(bad['tickets'])
                result = self.control('complete-tickets', bad)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.issues, {})

    def test_native_blocker_identity_mismatch_refuses_completed_board_credit(self):
        self.publish()
        self.members[10]['fields']['STATUS'] = 'Done'
        self.issues[10].update(state='closed', state_reason='completed')
        before = self.inspect()
        for identity in ({'id': 999999}, {'node_id': 'OTHER'}, {'id': 999999, 'node_id': 'OTHER'}):
            with self.subTest(identity=identity):
                self.dependency_observations[11] = [{**self.issues[10], **identity}]
                result = self.control('reserve-ticket', issue=11)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stderr)['error'], 'github_mismatch')
                self.assertEqual(self.inspect(), before)
                self.assertEqual(self.members[11]['fields']['STATUS'], 'Ready')
        self.dependency_observations.clear()
        self.assertEqual(self.ok(self.control('reserve-ticket', issue=11))['reservation']['status'], 'reserved')

    def test_native_blocker_identity_is_checked_even_outside_board_scope(self):
        self.publish()
        self.members[10]['fields'].update(STATUS='Done', SCOPE='M2')
        self.issues[10].update(state='closed', state_reason='completed')
        self.dependency_observations[11] = [{**self.issues[10], 'id': 999999, 'node_id': 'OTHER'}]
        result = self.control('frontier')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], 'github_mismatch')
        self.dependency_observations.clear()
        snapshot = self.ok(self.control('frontier'))['ticket_work']['frontier']
        self.assertNotIn(11, snapshot['eligible'])
        self.assertEqual(self.control('reserve-ticket', issue=11).returncode, 2)

    def test_wrong_endpoint_refused_before_external_access(self):
        self.completion()
        self.fixture.calls.clear()
        result = self.fixture.cli('frontier', '--project', 'product', '--iteration', 'm1', '--api-base', self.fixture.base + '/other')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.fixture.calls, [])
