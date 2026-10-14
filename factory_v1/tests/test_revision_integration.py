"""#21/#23 integration at public revision boundaries; SIMULATED local capabilities only."""
import copy
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from factory_v1 import requirements, tickets
from factory_v1.tests import test_requirements, test_lean_tickets
from factory_v1.tests.test_controls import cli, value
from factory_v1.tests.test_lean_tickets import lean_body


class RevisionBodyTests(unittest.TestCase):
    def test_lean_update_family_keeps_unrelated_bytes_and_admits_only_new_pins(self):
        fixture = test_lean_tickets.LeanContractTests()
        fixture.setUp()
        old = fixture.inputs
        new = copy.deepcopy(old)
        new['handoff']['issue']['number'] = 4
        for document in new['documents'].values():
            document['url'] = document['url'].replace(old['handoff']['commit'], 'd' * 40)
        ticket = {'title': 'Revised interruption', 'desired_behavior': 'Retain interruption evidence.',
                  'references': [{'requirement': 'US-35'}, {'requirement': 'US-36'}],
                  'acceptance_criteria': ['A paused revision cannot advance.']}
        from factory_v1.requirement_publication import updated_body
        for newline in ('\n', '\r\n'):
            for headings in ('## ', '## \t'):
                for footer in ('<!---->', '  <!--neighbor-->\t'):
                    body = (fixture.issue['body'] + '\n' + footer).replace('## ', headings).replace('\n', newline)
                    issue = {**fixture.issue, 'body': body}
                    with self.subTest(newline=newline, headings=headings, footer=footer):
                        revised = updated_body(issue, ticket, new, old)
                        self.assertEqual(tickets.external_contract({**issue, 'title': ticket['title'], 'body': revised}, new),
                                         'contracted')
                        self.assertFalse(tickets.external_contract({**issue, 'body': revised}, old))
                        self.assertEqual(tickets.sections(revised)['Blocked by'], tickets.sections(body)['Blocked by'])
                        self.assertTrue(revised.endswith(body[body.index('<!--'):]))
                        if newline == '\r\n':
                            self.assertNotIn('\n', revised.replace('\r\n', ''))


class RevisionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.case = test_requirements.RequirementChangeTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.tracker = self.case.tracker
        self.fixture = self.case.fixture

    def interrupt_response(self, operation, predicate, action='pause'):
        entered, release = threading.Event(), threading.Event()
        parent = self.fixture.server.RequestHandlerClass

        class Handler(parent):
            def reply(handler, status, response):
                if not entered.is_set() and predicate(handler.path, response):
                    entered.set()
                    if not release.wait(8):
                        raise RuntimeError('Bounded revision fixture release expired')
                super().reply(status, response)

        self.fixture.server.RequestHandlerClass = Handler
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(operation)
            try:
                self.assertTrue(entered.wait(5), 'Revision did not reach selected boundary')
                value(self, cli(self.fixture.state, action))
                start = len(self.fixture.calls)
            finally:
                release.set()
            result = pending.result(timeout=15)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], 'iteration_paused')
        self.assertFalse(any(c[0] == 'PATCH' or c[0] == 'POST' and
                             (c[1] != '/graphql' or c[2]['query'].startswith('mutation '))
                             for c in self.fixture.calls[start:]))
        return self.tracker.inspect()

    def test_lean_revision_preserves_crlf_unrelated_sections_footer_and_lost_update(self):
        inputs = self.case.original['ticket_work']['inputs']
        original = lean_body(inputs).replace('## ', '## \t').replace('\n', '\r\n')
        self.tracker.issues[10]['body'] = original
        self.case.drop_update = True
        published = self.case.publish()
        record = published['requirement_changes']['durable-notes']
        self.assertEqual(record['status'], 'published')
        body = self.tracker.issues[10]['body']
        parts = tickets.sections(body)
        self.assertEqual(tickets.external_contract(self.tracker.issues[10], record['new_inputs']), 'contracted')
        self.assertIn('agreed durable-note behavior', parts['Change'])
        self.assertIn('durability acceptance', parts['Done'])
        self.assertEqual(parts['Constraint'], tickets.sections(original)['Constraint'])
        self.assertEqual(parts['Blocked by'], tickets.sections(original)['Blocked by'])
        self.assertNotIn('\n', body.replace('\r\n', ''))
        self.assertTrue(body.endswith(original[original.index('<!--'):]))
        calls = copy.deepcopy(self.fixture.calls)
        self.case.ok(self.case.control('publish-requirement-change', {'change_id': 'durable-notes'}))
        self.assertEqual(sum(c[:2] == ('PATCH', '/repos/example/product/issues/10') for c in self.fixture.calls),
                         sum(c[:2] == ('PATCH', '/repos/example/product/issues/10') for c in calls))
        resumed = self.case.ok(self.case.resume(published))
        self.assertIn(10, resumed['ticket_work']['frontier']['eligible'])

    def test_pause_after_final_record_read_prevents_revision_and_retirement(self):
        reads = 0
        def final_read(path, response):
            nonlocal reads
            if path.startswith('/repos/example/product/issues/11/dependencies/blocked_by'):
                reads += 1
                return reads == 2
            return False
        after = self.interrupt_response(
            lambda: self.case.control('record-requirement-change', self.case.change), final_read)
        self.assertNotIn('requirement_changes', after)
        old = self.case.assignment.ok(self.case.assignment.inspect(self.case.prepared))
        self.assertNotIn('requirement_disposition', old)
        self.assertEqual(self.tracker.members[10]['fields']['STATUS'], 'Ready')

    def test_pause_at_spec_search_prevents_new_revision_issue_intent(self):
        self.case.synthesize()
        after = self.interrupt_response(
            lambda: self.case.control('publish-requirement-change', {'change_id': 'durable-notes'}),
            lambda path, response: path.startswith('/repos/example/product/issues?'))
        record = after['requirement_changes']['durable-notes']
        self.assertEqual(record['status'], 'publication-pending')
        self.assertIn('commit', record['publication'])
        self.assertNotIn('issue_intent', record['publication'])
        self.assertEqual(requirements.held_numbers(after), {10, 11})

    def test_pause_at_commit_response_retains_effect_without_new_ref_intent(self):
        self.case.synthesize()
        after = self.interrupt_response(
            lambda: self.case.control('publish-requirement-change', {'change_id': 'durable-notes'}),
            lambda path, response: path == '/repos/example/product/git/commits')
        journal = after['requirement_changes']['durable-notes']['publication']
        self.assertEqual(journal['commit'], 'd' * 40)
        self.assertTrue(journal['commit_intent'])
        self.assertNotIn('ref_intent', journal)
        self.assertEqual(self.case.git_refs, {})
        value(self, cli(self.fixture.state, 'resume'))
        published = self.case.ok(self.case.control('publish-requirement-change', {'change_id': 'durable-notes'}))
        self.assertEqual(published['requirement_changes']['durable-notes']['status'], 'published')
        self.assertEqual(sum(c[:2] == ('POST', '/repos/example/product/git/commits') for c in self.fixture.calls), 1)

    def test_pause_at_updated_issue_response_retains_pin_without_granting_authority(self):
        self.case.synthesize()
        after = self.interrupt_response(
            lambda: self.case.control('publish-requirement-change', {'change_id': 'durable-notes'}),
            lambda path, response: path == '/repos/example/product/issues/10' and
                isinstance(response, dict) and 'durability acceptance' in response.get('body', ''))
        record = after['requirement_changes']['durable-notes']
        self.assertEqual(record['updated_issues']['10'], requirements.issue_pin(self.tracker.issues[10]))
        self.assertNotIn('10', after.get('requirement_authority', {}))
        self.assertNotIn('11', record['updated_issues'])
        self.assertEqual(requirements.held_numbers(after), {10, 11})
        value(self, cli(self.fixture.state, 'resume'))
        published = self.case.ok(self.case.control('publish-requirement-change', {'change_id': 'durable-notes'}))
        self.assertEqual(published['requirement_changes']['durable-notes']['status'], 'published')
        self.assertEqual(sum(c[:2] == ('PATCH', '/repos/example/product/issues/10') for c in self.fixture.calls), 1)

    def test_stop_after_final_ready_write_cannot_release_requirement_hold(self):
        published = self.case.publish()
        def final_write(path, response):
            return (path == '/graphql' and self.tracker.members[11]['fields']['STATUS'] == 'Ready' and
                    self.tracker.members[10]['fields']['STATUS'] == 'Ready')
        after = self.interrupt_response(lambda: self.case.resume(published), final_write, 'emergency-stop')
        self.assertEqual(after['requirement_changes']['durable-notes']['status'], 'resume-pending')
        self.assertEqual(requirements.held_numbers(after), {10, 11})
        value(self, cli(self.fixture.state, 'resume'))
        resumed = self.case.ok(self.case.resume(published))
        self.assertEqual(resumed['requirement_changes']['durable-notes']['status'], 'resumed')
