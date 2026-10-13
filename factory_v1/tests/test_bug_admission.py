"""LABELED loopback tracker/public CLI races; not live GitHub or native inference."""
import copy
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from factory_v1.tests import test_bugs
from factory_v1.tests.test_controls import cli, value


class BugAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.bug = test_bugs.BugTests()
        self.bug.setUp()
        self.addCleanup(self.bug.doCleanups)
        self.case = self.bug.case
        self.state = self.case.fixture.state

    def pause_during_response(self, command, request, predicate):
        entered, release = threading.Event(), threading.Event()
        parent = self.case.fixture.server.RequestHandlerClass

        class Handler(parent):
            def reply(handler, status, response):
                if not entered.is_set() and predicate(handler.path, response):
                    entered.set()
                    if not release.wait(8):
                        raise RuntimeError('Bounded fixture release expired')
                super().reply(status, response)

        self.case.fixture.server.RequestHandlerClass = Handler
        before = self.case.inspect()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.case.control, command, request)
            try:
                self.assertTrue(entered.wait(4), 'Public command did not reach the selected boundary')
                paused = value(self, cli(self.state, 'pause'))
                self.assertEqual(paused['state'], 'requested', 'Pause must not wait for network I/O')
                self.assertFalse(paused['dispatch_allowed'])
                call_start = len(self.case.fixture.calls)
            finally:
                release.set()
            result = pending.result(timeout=10)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], 'iteration_paused')
        after = self.case.inspect()
        self.assertEqual(after['stage'], before['stage'])
        self.assertEqual(after['status'], 'paused')
        for key in ('repository', 'repository_onboarding', 'approval', 'origin', 'planning', 'handoff'):
            self.assertEqual(after.get(key), before.get(key), key)
        self.assertEqual(after['ticket_work'], before['ticket_work'])
        for work in after.get('bug_work', {}).values():
            self.assertNotIn('blocker', work, 'Interruption is not a publication failure')
        self.assertFalse(any(c[0] == 'PATCH' or c[0] == 'POST' and
                             (c[1] != '/graphql' or 'mutation' in c[2]['query'])
                             for c in self.case.fixture.calls[call_start:]))
        return paused, after

    def resume_publication(self, request):
        before = self.case.inspect()
        value(self, cli(self.state, 'resume'))
        after = self.bug.retry(request['assignment_id'])
        work = self.bug.work(after)
        self.assertTrue(work['publication_complete'])
        self.assertEqual(work['status'], 'published')
        self.assertEqual(work['assignment_id'], request['assignment_id'])
        self.assertEqual(work['input_digest'], self.bug.work(before)['input_digest'])
        self.assertEqual(work['execution'], request['execution'])
        creates = [c for c in self.case.fixture.calls
                   if c[0] == 'POST' and c[1] == '/repos/example/product/issues']
        self.assertEqual(len(creates), 4, 'Three feature fixtures and exactly one bug')
        self.assertEqual(self.case.deps, {11: [10], 10: [13]})
        self.assertEqual(self.bug.work(self.case.inspect()), work)

    def test_final_duplicate_search_prevents_new_synthesis_claim(self):
        before = self.case.inspect()
        paused, after = self.pause_during_response('synthesize-bug', self.bug.discovery(),
            lambda path, response: path.startswith('/repos/example/product/issues?'))
        self.assertNotIn('bug_work', paused['requested_checkpoint']['iteration'])
        self.assertEqual(after.get('bug_work'), before.get('bug_work'))
        self.assertEqual(after.get('bug_discoveries'), before.get('bug_discoveries'))
        value(self, cli(self.state, 'resume'))
        self.assertEqual(self.bug.work(self.case.ok(self.case.control('synthesize-bug', self.bug.discovery())))['status'],
                         'synthesis_pending')

    def test_pinned_read_prevents_new_discovery_alias(self):
        self.bug.completion()
        before = self.case.inspect()
        _, after = self.pause_during_response('synthesize-bug', self.bug.discovery(discovery_id='new-observer'),
            lambda path, response: path == '/repos/example/product/issues/7')
        self.assertEqual(after['bug_discoveries'], before['bug_discoveries'])
        self.assertEqual(after['bug_work'], before['bug_work'])

    def test_pinned_read_prevents_result_admission(self):
        request = self.bug.completion()
        before = self.case.inspect()
        _, after = self.pause_during_response('complete-bug', request,
            lambda path, response: path == '/repos/example/product/issues/7')
        self.assertEqual(after['bug_work'], before['bug_work'])
        self.assertEqual(set(self.case.issues), {10, 11, 12})
        value(self, cli(self.state, 'resume'))
        self.assertTrue(self.bug.work(self.bug.complete(request))['publication_complete'])

    def test_final_duplicate_search_prevents_new_issue_intent(self):
        request = self.bug.completion()
        paused, after = self.pause_during_response('complete-bug', request,
            lambda path, response: path.startswith('/repos/example/product/issues?'))
        self.assertNotIn('issue_intent', self.bug.work(paused['requested_checkpoint']['iteration']))
        work = self.bug.work(after)
        self.assertEqual(work['status'], 'publication_pending')
        self.assertEqual(work['execution'], request['execution'])
        self.assertNotIn('issue_intent', work)
        self.assertEqual(set(self.case.issues), {10, 11, 12})
        self.resume_publication(request)

    def test_admitted_issue_response_retains_receipt_without_next_intent(self):
        request = self.bug.completion()
        paused, after = self.pause_during_response('complete-bug', request,
            lambda path, response: path == '/repos/example/product/issues')
        requested = self.bug.work(paused['requested_checkpoint']['iteration'])
        self.assertIn('issue_intent', requested)
        self.assertNotIn('identity', requested)
        work = self.bug.work(after)
        self.assertEqual(work['identity'], {'number': 13, 'id': 1013, 'node_id': 'I13'})
        self.assertNotIn('membership_intent', work)
        self.assertNotIn(13, self.case.members)
        self.resume_publication(request)

    def test_native_blocker_read_prevents_new_edge_intent(self):
        request = self.bug.completion()
        request['bug']['blockers'] = [12]
        request = self.bug.signed(request)
        _, after = self.pause_during_response('complete-bug', request,
            lambda path, response: path.startswith('/repos/example/product/issues/13/dependencies/'))
        self.assertEqual(self.bug.work(after)['identity']['number'], 13)
        self.assertNotIn('edge_intents', self.bug.work(after))
        self.assertEqual(self.case.deps, {11: [10]})

    def test_membership_read_prevents_new_membership_intent(self):
        request = self.bug.completion()
        _, after = self.pause_during_response('complete-bug', request,
            lambda path, response: path == '/graphql' and 13 in self.case.issues and
                'items' in response.get('data', {}).get('node', {}))
        self.assertNotIn('membership_intent', self.bug.work(after))
        self.assertNotIn(13, self.case.members)
        self.resume_publication(request)

    def test_membership_read_prevents_new_field_intent(self):
        request = self.bug.completion()
        _, after = self.pause_during_response('complete-bug', request,
            lambda path, response: path == '/graphql' and 13 in self.case.members and
                'items' in response.get('data', {}).get('node', {}))
        self.assertTrue(self.bug.work(after)['membership_intent'])
        self.assertNotIn('field_intents', self.bug.work(after))
        self.assertEqual(self.case.members[13]['fields'], {})
        self.resume_publication(request)

    def test_affected_issue_read_prevents_new_body_intent(self):
        body = self.case.issues[10]['body'].replace('\n', '\r\n')
        self.case.issues[10]['body'] = body
        request = self.bug.completion()
        _, after = self.pause_during_response('complete-bug', request,
            lambda path, response: path == '/repos/example/product/issues/10' and
                self.case.members.get(13, {}).get('fields', {}).get('STATUS') == 'Todo')
        self.assertNotIn('affected_intents', self.bug.work(after))
        self.assertEqual(self.case.issues[10]['body'], body)
        self.resume_publication(request)
        self.assertEqual(self.case.issues[10]['body'], body.replace('None (can start immediately).', '- #13'))

    def test_affected_native_read_prevents_new_edge_intent(self):
        request = self.bug.completion()
        _, after = self.pause_during_response('complete-bug', request,
            lambda path, response: path.startswith('/repos/example/product/issues/10/dependencies/') and
                '- #13' in self.case.issues[10]['body'])
        self.assertIn('10', self.bug.work(after)['affected_intents'])
        self.assertEqual(self.bug.work(after).get('edge_intents', []), [])
        self.assertEqual(self.case.deps, {11: [10]})
        self.resume_publication(request)

    def test_final_affected_read_retains_observation_without_advancement(self):
        request = self.bug.completion()
        reads = 0

        def final_read(path, response):
            nonlocal reads
            if path.startswith('/repos/example/product/issues/10/dependencies/') and self.case.deps.get(10) == [13]:
                reads += 1
                return reads == 2  # Native edge receipt, then final affected observation.
            return False

        _, after = self.pause_during_response('complete-bug', request, final_read)
        work = self.bug.work(after)
        self.assertNotIn('publication_complete', work)
        self.assertEqual(work['status'], 'publication_pending')
        self.assertEqual(work['observation']['issue']['number'], 13)
        self.assertEqual(work['observation']['membership']['status'], 'Todo')
        external = copy.deepcopy((self.case.issues, self.case.members, self.case.deps))
        self.resume_publication(request)
        self.assertEqual((self.case.issues, self.case.members, self.case.deps), external)
