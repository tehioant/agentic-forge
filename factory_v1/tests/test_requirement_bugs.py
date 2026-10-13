"""Public bug revision regressions; all tracker/execution precursors are SIMULATED."""
import copy
import json
import unittest

from factory_v1 import requirements, tickets
from factory_v1.tests import test_requirements


class RequirementBugTests(unittest.TestCase):
    def setUp(self):
        self.case = test_requirements.RequirementChangeTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.tracker = self.case.tracker

    def discovery(self, discovery_id='notes-bug', affected=None):
        return {'discovery_id': discovery_id, 'skill': self.tracker.start_request['skill'], 'discovery': {
            'symptoms': 'SIMULATED durable notes disappear', 'reproduction': ['Create note', 'Restart'],
            'evidence': ['SIMULATED symptom, NOT native execution'], 'expected': 'Note survives', 'actual': 'Note disappears',
            'environment': 'Offline loopback fixture', 'scope': 'current', 'scope_reason': 'Violates durable-note retention',
            'affected': [10] if affected is None else affected}}

    def synthesize(self, request):
        item = self.case.ok(self.tracker.control('synthesize-bug', request))
        return item, item['bug_work'][item['bug_discoveries'][request['discovery_id']]['key']]

    def completion(self, work):
        bug = {'title': 'Preserve durable notes after restart', 'desired_behavior': 'Readers reopen the retained note.',
            'references': [{'spec': work['inputs']['documents']['milestone.md']['url'], 'requirement': 'US-1'}],
            'acceptance_criteria': ['SIMULATED exact round-trip after restart.'], 'blockers': [], 'status': 'ready',
            'scope': 'current', 'affected': work['discovery']['affected'], 'triage_label': 'agent-triage',
            'comparisons': [{'number': o['issue']['number'], 'symptoms_match': False, 'scope_match': False,
                            'rationale': 'SIMULATED feature/spec observation, not this defect.'} for o in work['candidates']]}
        sources = {work['skill']['path']: work['skill']['sha256'],
                   **{d['url']: d['sha256'] for d in work['inputs']['documents'].values()}, **work['load_sources']}
        return {'assignment_id': work['assignment_id'], 'input_digest': work['input_digest'], 'adaptation': work['adaptation'],
            'bug': bug, 'execution': {'run_id': 'SIMULATED bug synthesis',
                'loads': [{'source': source, 'sha256': sha, 'tool_reference': 'SIMULATED load, NOT native execution'}
                          for source, sha in sources.items()],
                'decomposition': 'SIMULATED selected to-tickets work, NOT native acceptance.', 'result_digest': tickets.digest(bug)}}

    def test_revision_retires_old_bug_assignment_and_replay_requires_fresh_synthesis(self):
        request = self.discovery()
        _, old = self.synthesize(request)
        completion = self.completion(old)
        published = self.case.publish()
        held = self.tracker.inspect()
        retired = held['bug_work'][held['bug_discoveries'][request['discovery_id']]['key']]
        self.assertEqual(retired['status'], 'obsolete')
        self.assertEqual(retired['requirement_disposition']['change_id'], 'durable-notes')
        self.assertEqual(retired['discovery'], old['discovery'])
        resumed = self.case.ok(self.case.resume(published))
        self.assertEqual(resumed['ticket_work']['frontier']['eligible'], [10, 12])
        for command, payload in [('complete-bug', completion), ('publish-bug', {'assignment_id': old['assignment_id']})]:
            result = self.tracker.control(command, payload)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('error', json.loads(result.stderr))
        item, fresh = self.synthesize(request)
        self.assertNotEqual(fresh['assignment_id'], old['assignment_id'])
        self.assertEqual(fresh['inputs'], requirements.inputs(item, 10))
        self.assertNotEqual(fresh['inputs'], old['inputs'])
        self.assertEqual(item['bug_work_history'][0], retired)
        for command, payload in [('complete-bug', completion), ('publish-bug', {'assignment_id': old['assignment_id']})]:
            self.assertEqual(self.tracker.control(command, payload).returncode, 2)
        _, replay = self.synthesize(request)
        self.assertEqual(replay, fresh)
        self.assertEqual(self.tracker.inspect()['bug_work_history'][0], retired)

    def assert_fresh_bug_publication(self, affected):
        self.case.ok(self.case.resume(self.case.publish()))
        before = copy.deepcopy(self.tracker.issues)
        item, work = self.synthesize(self.discovery(affected=affected))
        self.assertEqual(work['inputs'], requirements.inputs(item, affected[0]))
        for number in affected:
            self.assertEqual(work['scoped_inputs'][str(number)], requirements.inputs(item, number))
            for doc in work['scoped_inputs'][str(number)]['documents'].values():
                self.assertEqual(work['load_sources'][doc['url']], doc['sha256'])
        result = self.case.ok(self.tracker.control('complete-bug', self.completion(work)))
        published = result['bug_work'][result['bug_discoveries']['notes-bug']['key']]
        self.assertEqual(published['status'], 'published', published.get('blocker'))
        number = published['identity']['number']
        self.assertIn(number, result['ticket_work']['frontier']['eligible'])
        self.assertEqual(requirements.inputs(result, number), work['inputs'])
        for affected_number in affected:
            self.assertIn(number, self.tracker.deps[affected_number])
            self.assertIn('- #' + str(number), self.tracker.issues[affected_number]['body'])
            self.assertIn(requirements.inputs(item, affected_number)['documents']['milestone.md']['url'],
                          self.tracker.issues[affected_number]['body'])
        self.assertEqual(self.tracker.issues[11], before[11])
        replay = self.case.ok(self.tracker.control('publish-bug', {'assignment_id': work['assignment_id']}))
        self.assertEqual(replay['bug_work'][replay['bug_discoveries']['notes-bug']['key']]['identity'], published['identity'])

    def test_new_bug_for_revised_issue_completes_and_publishes_with_current_inputs(self):
        self.assert_fresh_bug_publication([10])

    def test_mixed_original_and_revised_affected_issues_keep_each_scoped_authority(self):
        self.assert_fresh_bug_publication([10, 12])

    def test_independent_bug_synthesis_is_not_retired_by_unrelated_revision(self):
        request = self.discovery('independent-bug', [12])
        _, old = self.synthesize(request)
        self.case.ok(self.case.resume(self.case.publish()))
        _, replay = self.synthesize(request)
        self.assertEqual(replay, old)
        self.assertNotIn('requirement_disposition', replay)

    def test_retired_uncertain_creation_remains_a_no_duplicate_write_guard(self):
        request = self.discovery()
        _, old = self.synthesize(request)
        self.tracker.drop, self.tracker.omit_effect = 'issue', True
        uncertain = self.case.ok(self.tracker.control('complete-bug', self.completion(old)))
        key = uncertain['bug_discoveries'][request['discovery_id']]['key']
        self.assertTrue(uncertain['bug_work'][key]['issue_intent'])
        self.assertFalse(uncertain['bug_work'][key].get('publication_complete'))
        self.tracker.omit_effect = False
        self.case.ok(self.case.resume(self.case.publish()))
        _, fresh = self.synthesize(request)
        self.tracker.fixture.calls.clear()
        item = self.case.ok(self.tracker.control('complete-bug', self.completion(fresh)))
        self.assertEqual(item['bug_work'][key]['blocker']['code'], 'publication_uncertain')
        self.assertTrue(item['bug_work_history'][0]['issue_intent'])
        self.assertFalse(any(c[:2] == ('POST', '/repos/example/product/issues') for c in self.tracker.fixture.calls))

    def test_retired_discovery_cannot_silently_drop_an_affected_issue_on_refresh(self):
        _, old = self.synthesize(self.discovery(affected=[10, 12]))
        self.case.ok(self.case.resume(self.case.publish()))
        before = self.tracker.inspect()
        result = self.tracker.control('synthesize-bug', self.discovery('narrowed-observer', [10]))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], 'bug_conflict')
        self.assertEqual(self.tracker.inspect(), before)
        item, fresh = self.synthesize(self.discovery(affected=[10, 12]))
        self.assertNotEqual(fresh['assignment_id'], old['assignment_id'])
        self.assertEqual(fresh['discovery']['affected'], [10, 12])
        self.assertEqual(fresh['scoped_inputs']['12'], requirements.inputs(item, 12))
