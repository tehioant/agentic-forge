"""MOCK ONLY: public bug lifecycle in fresh processes through the real HTTP adapter."""
import copy
import json
import os
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from factory_v1.tests import test_ticket_native_scope, test_tickets


class BugTests(unittest.TestCase):
    def setUp(self):
        self.native = test_ticket_native_scope.NativeScopeTests()
        self.native.setUp()
        self.addCleanup(self.native.doCleanups)
        self.case = self.native.case
        self.case.publish()
        self.patch_drop = False
        self.patch_omit = False
        self.patch_mismatch = False
        parent = self.case.fixture.server.RequestHandlerClass
        case = self

        class Handler(parent):
            def do_PATCH(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.case.fixture.calls.append(('PATCH', self.path, data))
                number = int(self.path.split('/')[5])
                if not case.patch_omit:
                    case.case.issues[number].update(data)
                if case.patch_mismatch:
                    case.case.issues[number]['body'] += '\nUnexpected edit.'
                if case.patch_drop:
                    case.patch_drop = False
                    self.close_connection = True
                    return
                self.reply(200, case.case.issues[number])

        self.case.fixture.server.RequestHandlerClass = Handler

    def discovery(self, scope='current', discovery_id='discovered-19', symptoms='Reopened notes lose accents'):
        return {'discovery_id': discovery_id, 'skill': self.case.start_request['skill'], 'discovery': {
            'symptoms': symptoms, 'reproduction': ['Create café.', 'Reopen the note.'],
            'evidence': ['fixture:public-cli-output'], 'expected': 'Reopening retains café.',
            'actual': 'Reopening displays caf.', 'environment': 'Fixture Python on Linux; no live GitHub.',
            'scope': scope, 'scope_reason': 'Fixture observed requirement/health implication.',
            'affected': [10] if scope == 'current' else []}}

    def work(self, item, assignment=None):
        return next(w for w in item['bug_work'].values() if assignment is None or w['assignment_id'] == assignment)

    def completion(self, discovery=None, status=None):
        discovery = discovery or self.discovery()
        item = self.case.ok(self.case.control('synthesize-bug', discovery))
        work = item['bug_work'][item['bug_discoveries'][discovery['discovery_id']]['key']]
        scope = work['discovery']['scope']
        bug = {'title': 'Retain accents when reopening a note', 'desired_behavior': 'Readers reopen the exact retained French note.',
               'references': [{'spec': work['inputs']['documents']['milestone.md']['url'], 'requirement': 'US-1'}],
               'acceptance_criteria': ['The public CLI reopens café unchanged.'], 'blockers': [],
               'status': status or ('deferred' if scope == 'deferred' else 'ready'), 'scope': scope,
               'affected': work['discovery']['affected'], 'triage_label': 'agent-triage',
               'comparisons': [{'number': o['issue']['number'], 'symptoms_match': False, 'scope_match': False,
                                'rationale': 'Fixture feature ticket has no matching observed accent defect.'}
                               for o in work['candidates']]}
        sources = {work['skill']['path']: work['skill']['sha256'],
                   **{d['url']: d['sha256'] for d in work['inputs']['documents'].values()}, **work['load_sources']}
        return {'assignment_id': work['assignment_id'], 'input_digest': work['input_digest'], 'adaptation': work['adaptation'],
                'bug': bug, 'execution': {'run_id': 'fixture-bug-synthesis-19',
                'loads': [{'source': source, 'sha256': sha, 'tool_reference': 'fixture:actual-load'} for source, sha in sources.items()],
                'decomposition': 'Fixture selected to-tickets drafts one end-to-end accent regression and its justified scope/edges.',
                'result_digest': test_tickets.hash_json(bug)}}

    def complete(self, request):
        return self.case.ok(self.case.control('complete-bug', request))

    def retry(self, assignment):
        return self.case.ok(self.case.control('publish-bug', {'assignment_id': assignment}))

    def signed(self, request):
        request['execution']['result_digest'] = test_tickets.hash_json(request['bug'])
        return request

    def test_current_bug_common_contract_native_downstream_and_independent_frontier(self):
        request = self.completion(status='blocked')
        pending = self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']
        self.assertEqual(pending['eligible'], [12])
        item = self.complete(request)
        work = self.work(item)
        self.assertEqual(work['status'], 'published')
        self.assertEqual(work['identity']['number'], 13)
        self.assertEqual(self.case.deps, {11: [10], 10: [13]})
        self.assertIn('- #13', self.case.issues[10]['body'])
        self.assertEqual(self.case.issues[13]['milestone']['id'], 501)
        self.assertEqual(work['observation']['membership']['status'], 'Todo')
        self.assertEqual(item['ticket_work']['frontier']['eligible'], [12])
        for heading in ('What to build', 'Requirement references', 'Acceptance criteria', 'Blocked by', 'Status',
                        'Bug scope', 'Observable symptoms', 'Reproduction', 'Evidence', 'Expected behavior', 'Actual behavior', 'Environment'):
            self.assertIn('## ' + heading + '\n', self.case.issues[13]['body'])
        self.assertEqual(self.case.control('reserve-ticket', issue=10).returncode, 2)
        self.assertEqual(self.case.control('reserve-ticket', issue=11).returncode, 2)
        self.assertEqual(self.case.control('reserve-ticket', issue=13).returncode, 2)
        self.assertEqual(self.case.ok(self.case.control('reserve-ticket', issue=12))['reservation']['status'], 'reserved')
        self.assertFalse(item['execution_allowed'])
        self.assertFalse(any(c[0] != 'GET' and '/issues/7' in c[1] for c in self.case.fixture.calls))
        self.assertEqual(self.native.milestones, [{'id': 501, 'number': 1, 'title': 'm1', 'state': 'open'}])

    def test_ready_current_bug_is_required_and_completion_releases_affected_work(self):
        work = self.work(self.complete(self.completion()))
        self.assertEqual(self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'], [12, 13])
        self.case.issues[13].update(state='closed', state_reason='completed')
        self.case.members[13]['fields']['STATUS'] = 'Done'
        self.assertEqual(self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'], [10, 12])
        self.retry(work['assignment_id'])
        self.assertEqual(self.case.members[13]['fields']['STATUS'], 'Done')

    def test_deferred_and_recovery_never_join_normal_scope_or_reserve(self):
        for scope in ('deferred', 'recovery'):
            with self.subTest(scope=scope):
                discovery = self.discovery(scope=scope, discovery_id=scope)
                request = self.completion(discovery)
                work = self.work(self.complete(request), request['assignment_id'])
                number = work['identity']['number']
                self.assertIsNone(self.case.issues[number]['milestone'])
                self.assertEqual(self.case.control('reserve-ticket', issue=number).returncode, 2)
                self.assertEqual(self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'], [10, 12])
                if scope == 'recovery':
                    self.assertIn('immediate recovery', self.case.issues[number]['body'])
                self.case.issues[number]['milestone'] = copy.deepcopy(self.native.milestones[0])
                self.assertNotIn(number, self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'])
                self.case.issues[number]['milestone'] = None
        self.assertEqual(len(self.case.issues), 5)
        self.assertEqual(self.case.deps, {11: [10]})

    def test_held_bug_scope_whitespace_and_crlf_never_allow_public_reservation(self):
        for scope in ('recovery', 'deferred'):
            request = self.completion(self.discovery(scope=scope, discovery_id=scope))
            work = self.work(self.complete(request), request['assignment_id'])
            number = work['identity']['number']
            issue = self.case.issues[number]
            issue['milestone'] = copy.deepcopy(self.native.milestones[0])
            original = issue['body'].replace('## Status\n\ndeferred ', '## Status\n\nready ')
            section = '## Bug scope\n\n' + scope + '\n'
            variants = {
                'canonical': original,
                'heading': original.replace('## Bug scope\n', '## Bug scope \t\n'),
                'heading-leading': original.replace('## Bug scope\n', '##  Bug scope\n'),
                'content': original.replace(section, '## Bug scope\n \n\t' + scope + ' \t\n'),
                'scope-crlf': original.replace(section, section.replace('\n', '\r\n')),
                'body-crlf': original.replace('\n', '\r\n'),
            }
            for variant, body in variants.items():
                with self.subTest(scope=scope, variant=variant):
                    issue['body'] = body
                    before = copy.deepcopy((self.case.issues, self.case.members, self.case.deps))
                    frontier = self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']
                    self.assertEqual(frontier['eligible'], [10, 12])
                    row = next(row for row in frontier['items'] if row['number'] == number)
                    self.assertTrue(row['contract_valid'])
                    self.assertTrue(row['held'])
                    refused = self.case.control('reserve-ticket', issue=number)
                    self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                    self.assertEqual(json.loads(refused.stderr)['error'], 'ticket_ineligible')
                    self.assertEqual((self.case.issues, self.case.members, self.case.deps), before)
            issue['body'] = original
        self.assertEqual(self.case.ok(self.case.control('reserve-ticket', issue=12))['reservation']['status'], 'reserved')

    def test_malformed_bug_scope_never_allows_public_reservation(self):
        request = self.completion(self.discovery(scope='recovery'))
        number = self.work(self.complete(request))['identity']['number']
        issue = self.case.issues[number]
        issue['milestone'] = copy.deepcopy(self.native.milestones[0])
        original = issue['body']
        for value in ('', 'unknown', 'recovery\ncurrent'):
            with self.subTest(scope=value):
                issue['body'] = original.replace('## Bug scope\n\nrecovery', '## Bug scope\n\n' + value)
                before = copy.deepcopy((self.case.issues, self.case.members, self.case.deps))
                self.assertEqual(self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'], [10, 12])
                refused = self.case.control('reserve-ticket', issue=number)
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                self.assertEqual(json.loads(refused.stderr)['error'], 'ticket_ineligible')
                self.assertEqual((self.case.issues, self.case.members, self.case.deps), before)

    def test_duplicate_normalized_bug_scope_fails_closed_through_public_cli(self):
        request = self.completion(self.discovery(scope='recovery'))
        number = self.work(self.complete(request))['identity']['number']
        issue = self.case.issues[number]
        issue['milestone'] = copy.deepcopy(self.native.milestones[0])
        original = issue['body']
        for heading in ('Bug scope', 'Bug scope \t', ' Bug scope'):
            with self.subTest(heading=heading):
                issue['body'] = original + '\n## ' + heading + '\n\ncurrent\n'
                before = copy.deepcopy((self.case.issues, self.case.members, self.case.deps))
                frontier = self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']
                self.assertEqual(frontier['eligible'], [10, 12])
                row = next(row for row in frontier['items'] if row['number'] == number)
                self.assertFalse(row['contract_valid'])
                result = self.case.control('reserve-ticket', issue=number)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stderr)['error'], 'ticket_ineligible')
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual((self.case.issues, self.case.members, self.case.deps), before)

    def test_repeat_and_new_discovery_ids_use_same_assignment_and_no_duplicate(self):
        request = self.completion()
        first = self.complete(request)
        for discovery_id, symptoms in [('discovered-19', 'Reopened notes lose accents'), ('new-source', ' REOPENED notes  lose ACCENTS ')]:
            item = self.case.ok(self.case.control('synthesize-bug', self.discovery(discovery_id=discovery_id, symptoms=symptoms)))
            self.assertEqual(self.work(item)['assignment_id'], request['assignment_id'])
        repeated = self.complete(request)
        self.assertEqual(self.work(repeated)['identity'], self.work(first)['identity'])
        self.assertEqual(len(repeated['bug_work']), 1)
        self.assertEqual(len(self.case.issues), 4)
        self.assertEqual(self.case.deps, {11: [10], 10: [13]})

    def test_lost_issue_response_and_new_id_reconcile_same_issue(self):
        request = self.completion()
        self.case.drop = 'issue'
        self.assertEqual(self.work(self.complete(request))['status'], 'blocked')
        self.assertEqual(len(self.case.issues), 4)
        item = self.case.ok(self.case.control('synthesize-bug', self.discovery(discovery_id='repeat-from-other-worker')))
        self.assertEqual(self.work(item)['assignment_id'], request['assignment_id'])
        self.assertEqual(self.work(self.retry(request['assignment_id']))['status'], 'published')
        self.assertEqual(len(self.case.issues), 4)

    def test_absent_uncertain_issue_cannot_be_recreated_under_new_id(self):
        request = self.completion()
        self.case.drop, self.case.omit_effect = 'issue', True
        self.complete(request)
        self.case.omit_effect = False
        self.case.ok(self.case.control('synthesize-bug', self.discovery(discovery_id='another-discovery')))
        self.assertEqual(self.work(self.retry(request['assignment_id']))['blocker']['code'], 'publication_uncertain')
        self.assertEqual(len(self.case.issues), 3)
        self.assertEqual(self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'], [12])

    def test_scope_comparison_and_semantic_duplicate_reuse(self):
        first_request = self.completion()
        self.complete(first_request)
        discovery = self.discovery(discovery_id='equivalent-observation', symptoms='Accent characters disappear on reading saved notes')
        item = self.case.ok(self.case.control('synthesize-bug', discovery))
        assignment = next(w['assignment_id'] for w in item['bug_work'].values() if w['assignment_id'] != first_request['assignment_id'])
        work = self.work(item, assignment)
        request = copy.deepcopy(first_request)
        request.update(assignment_id=assignment, input_digest=work['input_digest'])
        request['bug']['comparisons'] = [{'number': o['issue']['number'], 'symptoms_match': o['issue']['number'] == 13,
                                        'scope_match': o['issue']['number'] == 13,
                                        'rationale': 'Same observed accent loss in the same current requirement scope.'}
                                       for o in work['candidates']]
        request['execution']['loads'] = [{'source': source, 'sha256': sha, 'tool_reference': 'fixture:load'} for source, sha in
                                        {work['skill']['path']: work['skill']['sha256'],
                                         **{d['url']: d['sha256'] for d in work['inputs']['documents'].values()}, **work['load_sources']}.items()]
        result = self.work(self.complete(self.signed(request)), assignment)
        self.assertEqual(result['status'], 'duplicate')
        self.assertEqual(result['identity']['number'], 13)
        self.assertEqual(len(self.case.issues), 4)
        self.assertEqual(self.case.deps, {11: [10], 10: [13]})

    def test_malformed_contract_evidence_and_cycles_leave_state_unchanged(self):
        request = self.completion()
        before = self.case.inspect()
        for defect in ('catalog', 'adaptation', 'digest', 'assignment', 'comparison', 'scope', 'affected', 'cycle', 'unknown', 'reference', 'empty'):
            bad = copy.deepcopy(request)
            if defect == 'catalog': bad['execution']['loads'] = []
            if defect == 'adaptation': bad['adaptation'] = 'generic-bug-prompt'
            if defect == 'digest': bad['input_digest'] = '0' * 64
            if defect == 'assignment': bad['assignment_id'] = 'unassigned'
            if defect == 'comparison': bad['bug']['comparisons'] = []
            if defect == 'scope': bad['bug']['scope'] = 'recovery'
            if defect == 'affected': bad['bug']['affected'] = []
            if defect == 'cycle': bad['bug']['blockers'] = [11]
            if defect == 'unknown': bad['bug']['blockers'] = [99]
            if defect == 'reference': bad['bug']['references'][0]['requirement'] = 'US-999'
            if defect == 'empty': bad['bug']['acceptance_criteria'] = [' ']
            with self.subTest(defect=defect):
                result = self.case.control('complete-bug', self.signed(bad))
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIsInstance(json.loads(result.stderr), dict)
                self.assertEqual(self.case.inspect(), before)
                self.assertEqual(len(self.case.issues), 3)

    def test_discovery_validation_id_conflict_and_changed_skill(self):
        valid = self.discovery()
        self.case.ok(self.case.control('synthesize-bug', valid))
        before = self.case.inspect()
        for field, value in [('scope', []), ('affected', [True]), ('symptoms', None), ('environment', {}), ('evidence', [None])]:
            bad = copy.deepcopy(valid)
            bad['discovery'][field] = value
            with self.subTest(field=field):
                result = self.case.control('synthesize-bug', bad)
                self.assertEqual(result.returncode, 2, result.stderr)
                json.loads(result.stderr)
                self.assertEqual(self.case.inspect(), before)
        changed = copy.deepcopy(valid)
        changed['discovery']['scope_reason'] = 'Different evidence.'
        self.assertEqual(self.case.control('synthesize-bug', changed).returncode, 2)
        path = self.case.fixture.root / 'changed-skill.md'
        path.write_text('Not the pinned selected skill.')
        changed['skill']['path'] = str(path)
        self.assertEqual(self.case.control('synthesize-bug', changed).returncode, 2)
        self.assertEqual(self.case.inspect(), before)

    def test_new_observation_between_synthesis_and_create_blocks_no_blind_duplicate(self):
        request = self.completion()
        self.case.issues[20] = copy.deepcopy(self.case.issues[12])
        self.case.issues[20].update(number=20, id=1020, node_id='I20', html_url='https://github.com/example/product/issues/20')
        item = self.complete(request)
        self.assertEqual(self.work(item)['blocker']['code'], 'bug_conflict')
        self.assertNotIn('issue_intent', self.work(item))
        self.assertEqual(len(self.case.issues), 4)

    def test_lost_board_body_and_dependency_responses_reconcile(self):
        request = self.completion()
        self.case.drop = 'membership'
        self.complete(request)
        self.case.drop = 'field'
        self.retry(request['assignment_id'])
        self.patch_drop = True
        self.retry(request['assignment_id'])
        self.case.drop = 'dependency'
        self.retry(request['assignment_id'])
        item = self.retry(request['assignment_id'])
        self.assertEqual(self.work(item)['status'], 'published')
        self.assertEqual(len(self.case.issues), 4)
        self.assertEqual(len(self.case.members), 4)
        self.assertEqual(self.case.deps, {11: [10], 10: [13]})

    def test_exact_body_scope_status_and_affected_patch_readbacks(self):
        request = self.completion()
        self.patch_mismatch = True
        item = self.complete(request)
        self.assertEqual(self.work(item)['blocker']['code'], 'publication_mismatch')
        self.assertEqual(self.case.deps, {11: [10]})
        self.assertEqual(self.case.control('reserve-ticket', issue=10).returncode, 2)
        self.assertNotIn('publication_complete', self.work(item))

    def test_crash_after_issue_effect_reuses_committed_intent(self):
        request = self.completion()
        path = self.case.fixture.root / 'crash-bug.json'
        path.write_text(json.dumps(request))
        self.case.creation_event, self.case.release_response = threading.Event(), threading.Event()
        command = [sys.executable, '-m', 'factory_v1', '--state', str(self.case.fixture.state), '--operator-id', '42',
                   'complete-bug', '--project', 'product', '--iteration', 'm1', '--api-base', self.case.fixture.base,
                   '--request', str(path)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        try:
            self.assertTrue(self.case.creation_event.wait(timeout=5))
            process.kill()
            process.communicate(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
            self.case.release_response.set()
            self.case.creation_event = None
        self.assertTrue(self.work(self.case.inspect())['issue_intent'])
        self.assertEqual(self.work(self.retry(request['assignment_id']))['status'], 'published')
        self.assertEqual(len(self.case.issues), 4)

    def test_uncertain_creation_blocks_reworded_new_discovery(self):
        first = self.completion()
        self.case.drop, self.case.omit_effect = 'issue', True
        self.complete(first)
        self.case.omit_effect = False
        reworded = self.completion(self.discovery(discovery_id='new-observer', symptoms='Accents disappear while reading notes'))
        work = self.work(self.complete(reworded), reworded['assignment_id'])
        self.assertEqual(work['blocker']['code'], 'publication_uncertain')
        self.assertNotIn('issue_intent', work)
        self.assertEqual(len(self.case.issues), 3)

    def test_explicit_bug_blockers_and_affected_edges_are_both_native(self):
        request = self.completion()
        request['bug']['blockers'] = [12]
        item = self.complete(self.signed(request))
        self.assertEqual(self.work(item)['status'], 'published')
        self.assertEqual(self.case.deps, {11: [10], 13: [12], 10: [13]})
        self.assertIn('- #12', self.case.issues[13]['body'])
        self.assertEqual(item['ticket_work']['frontier']['eligible'], [12])
        self.case.issues[12].update(state='closed', state_reason='completed')
        self.case.members[12]['fields']['STATUS'] = 'Done'
        self.assertEqual(self.case.ok(self.case.control('frontier'))['ticket_work']['frontier']['eligible'], [13])

    def test_published_readbacks_refuse_drift_and_recover_without_writes(self):
        request = self.completion()
        self.complete(request)
        original = copy.deepcopy(self.case.issues[13])
        member = copy.deepcopy(self.case.members[13])
        for defect in ('refs', 'symptoms', 'triage', 'scope', 'progress', 'membership', 'affected-edge'):
            with self.subTest(defect=defect):
                if defect == 'refs': self.case.issues[13]['body'] = original['body'].replace('US-1', 'US-999')
                if defect == 'symptoms': self.case.issues[13]['body'] = original['body'].replace('Reopened notes lose accents', 'Other symptoms')
                if defect == 'triage': self.case.issues[13]['labels'] = []
                if defect == 'scope': self.case.issues[13]['milestone'] = None
                if defect == 'progress': self.case.members[13]['fields']['STATUS'] = 'Unknown'
                if defect == 'membership': self.case.members.pop(13)
                if defect == 'affected-edge': self.case.deps[10] = []
                self.case.fixture.calls.clear()
                work = self.work(self.retry(request['assignment_id']))
                self.assertEqual(work['status'], 'blocked')
                self.assertEqual(work['blocker']['code'], 'publication_mismatch')
                self.assertFalse(any(c[0] == 'PATCH' or c[0] == 'POST' and c[1] != '/graphql' for c in self.case.fixture.calls))
                self.assertFalse(any(c[0] == 'POST' and c[1] == '/graphql' and 'mutation' in c[2]['query'] for c in self.case.fixture.calls))
                self.case.issues[13] = copy.deepcopy(original)
                self.case.members[13] = copy.deepcopy(member)
                self.case.deps[10] = [13]
                self.assertEqual(self.work(self.retry(request['assignment_id']))['status'], 'published')

    def test_oversized_numeric_blocker_readback_blocks_and_recovers_without_writes(self):
        request = self.completion()
        request['bug']['blockers'] = [12]
        published = self.work(self.complete(self.signed(request)))
        number = published['identity']['number']
        original_body = self.case.issues[number]['body']
        malformed_body = original_body.replace('- #12', '- #' + '9' * 5000)
        self.assertNotEqual(malformed_body, original_body)
        for body, status in ((malformed_body, 'blocked'), (original_body, 'published')):
            with self.subTest(status=status):
                self.case.issues[number]['body'] = body
                before = copy.deepcopy((self.case.issues, self.case.members, self.case.deps))
                self.case.fixture.calls.clear()
                result = self.case.control('publish-bug', {'assignment_id': request['assignment_id']})
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, '')
                self.assertNotIn('Traceback', result.stdout)
                work = self.work(json.loads(result.stdout))
                self.assertEqual(work['status'], status)
                self.assertTrue(work['publication_complete'])
                if status == 'blocked':
                    self.assertEqual(work['blocker']['code'], 'publication_mismatch')
                    self.assertEqual(work['observation'], published['observation'])
                else:
                    self.assertNotIn('blocker', work)
                    self.assertEqual(work['observation']['issue']['body'], original_body)
                self.assertEqual(self.work(self.case.inspect()), work)
                self.assertEqual((self.case.issues, self.case.members, self.case.deps), before)
                self.assertFalse(any(c[0] == 'PATCH' or c[0] == 'POST' and c[1] != '/graphql' for c in self.case.fixture.calls))
                self.assertFalse(any(c[0] == 'POST' and c[1] == '/graphql' and 'mutation' in c[2]['query'] for c in self.case.fixture.calls))

    def test_evidence_only_unknown_environment_and_future_vision_reference(self):
        discovery = self.discovery(scope='deferred')
        discovery['discovery'].update(reproduction=[], environment=None)
        request = self.completion(discovery)
        work = self.work(self.case.inspect())
        request['bug']['references'] = [{'spec': work['inputs']['documents']['vision.md']['url'], 'requirement': 'team sharing'}]
        result = self.work(self.complete(self.signed(request)))
        self.assertEqual(result['status'], 'published')
        self.assertIn('## Environment\n\nUnavailable.', self.case.issues[13]['body'])
        self.assertIn('team sharing', self.case.issues[13]['body'])
        self.assertEqual(self.case.control('reserve-ticket', issue=13).returncode, 2)

    def test_synthesis_pins_empty_dependencies_and_actual_adaptation_load(self):
        request = self.completion()
        work = self.work(self.case.inspect())
        self.assertEqual(work['skill']['dependencies'], [])
        self.assertIn('no-new-milestone', work['adaptation'])
        request['execution']['loads'] = [load for load in request['execution']['loads'] if not load['source'].startswith('bug-adaptation:')]
        before = self.case.inspect()
        result = self.case.control('complete-bug', request)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'execution_evidence_required')
        self.assertEqual(self.case.inspect(), before)
        self.assertEqual(len(self.case.issues), 3)

    def test_additional_affected_scope_is_not_silently_discarded(self):
        self.case.ok(self.case.control('synthesize-bug', self.discovery()))
        before = self.case.inspect()
        additional = self.discovery(discovery_id='another-affected-ticket')
        additional['discovery']['affected'] = [10, 12]
        result = self.case.control('synthesize-bug', additional)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['error'], 'bug_conflict')
        self.assertEqual(self.case.inspect(), before)

    def test_missing_projects_keeps_synthesis_and_refuses_writes(self):
        request = self.completion()
        self.case.boards = []
        item = self.complete(request)
        work = self.work(item)
        self.assertEqual(work['blocker']['code'], 'projects_blocked')
        self.assertEqual(work['status'], 'blocked')
        self.assertEqual(work['execution'], request['execution'])
        self.assertEqual(work['bug'], request['bug'])
        self.assertEqual(len(self.case.issues), 3)
        self.case.boards = [{'id': 'P1', 'title': 'Approved linked board', 'closed': False}]
        self.assertEqual(self.work(self.retry(request['assignment_id']))['status'], 'published')

    def test_concurrent_publish_retries_serialize(self):
        request = self.completion()
        self.case.drop = 'issue'
        self.complete(request)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.retry(request['assignment_id']), range(2)))
        self.assertTrue(all(self.work(item)['status'] == 'published' for item in results))
        self.assertEqual(len(self.case.issues), 4)
        self.assertEqual(self.case.deps, {11: [10], 10: [13]})
