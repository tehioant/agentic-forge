"""Revision probes around review and merge. All native/tracker precursors are SIMULATED."""
import json
import unittest

from factory_v1 import requirements
from factory_v1.tests import test_merge, test_review


def record_change(case, fixture, tracker):
    item = tracker.inspect()
    request = {'change_id': 'changed-requirement', 'kind': 'material',
        'direction': {'operator_id': '42', 'origin': item['origin'], 'reference': 'SIMULATED operator change',
                      'content': 'Durably retain notes; obsolete branches must wait for resynthesis.'},
        'requirements': ['US-1'], 'issues': [10], 'old_revision': {'10': requirements.authority(item, 10)}}
    file = fixture.root / 'material-revision.json'
    file.write_text(json.dumps(request))
    return case.assignment.ok(fixture.cli('record-requirement-change', '--project', 'product', '--iteration', 'm1',
        '--request', str(file), '--api-base', fixture.base))


class RequirementReviewTests(unittest.TestCase):
    def test_material_revision_before_review_refuses_old_candidate(self):
        case = test_review.ReviewTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        item = record_change(case, case.assignment.fixture, case.assignment.tracker)
        self.assertEqual(len(item['requirement_changes']['changed-requirement']['retired_assignments']), 2)
        case.assignment.refused(case.command('prepare-review', case.request('review-spec')), 'requirements_held')
        self.assertEqual(case.assignment.tracker.members[10]['fields']['STATUS'], 'Blocked')


class RequirementMergeTests(unittest.TestCase):
    def setUp(self):
        self.case = test_merge.MergeTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_material_revision_after_approval_refuses_merge_even_after_restart(self):
        item = record_change(self.case, self.case.fixture, self.case.assignment.tracker)
        self.assertEqual(len(item['requirement_changes']['changed-requirement']['retired_assignments']), 4)
        self.case.calls.clear()
        self.case.refused('requirements_held')
        self.case.refused('requirements_held')
        self.assertEqual(self.case.writes('PUT'), [])
        stored = self.case.stored()
        self.assertFalse(stored['assignment_ready'])
        self.assertFalse(stored['result_disposition']['requirements_valid'])
        self.assertEqual(stored['merge_refusal']['code'], 'requirements_held')

    def test_independent_approved_candidate_retains_evidence_after_explicit_validity_checks(self):
        tracker = self.case.assignment.tracker
        item = tracker.inspect()
        request = {'change_id': 'independent-change', 'kind': 'material',
            'direction': {'operator_id': '42', 'origin': item['origin'], 'reference': 'SIMULATED independent direction',
                          'content': 'Only unsafe-path refusal changes; retained-note delivery is unchanged.'},
            'requirements': ['US-1'], 'issues': [12], 'old_revision': {'12': requirements.authority(item, 12)}}
        file = self.case.fixture.root / 'independent-change.json'
        file.write_text(json.dumps(request))
        recorded = self.case.ok(self.case.fixture.cli('record-requirement-change', '--project', 'product', '--iteration', 'm1',
            '--request', str(file), '--api-base', self.case.fixture.base))
        preserved = recorded['requirement_changes']['independent-change']['preserved_evidence']
        self.assertEqual(len([e for e in preserved if 'assignment_id' in e]), 4)
        self.case.calls.clear()
        self.assertEqual(self.case.ok(self.case.cli())['status'], 'merged')
        self.assertEqual(len(self.case.writes('PUT')), 1)
        self.assertEqual(tracker.members[12]['fields']['STATUS'], 'Blocked')

    def test_minor_prose_never_revalidates_changed_review_checks(self):
        item = self.case.assignment.tracker.inspect()
        from pathlib import Path
        checks = Path(self.case.axes[1]['runtime']['artifacts']) / 'controller-checks.json'
        checks.write_text(checks.read_text() + '\n')
        request = {'change_id': 'clarification', 'kind': 'minor',
            'direction': {'operator_id': '42', 'origin': item['origin'], 'reference': 'SIMULATED clarification', 'content': 'No requirements changed.'},
            'requirements': ['US-1'], 'issues': [10], 'old_revision': {'10': requirements.authority(item, 10)}}
        file = self.case.fixture.root / 'minor-change.json'
        file.write_text(json.dumps(request))
        self.case.assignment.refused(self.case.fixture.cli('record-requirement-change', '--project', 'product', '--iteration', 'm1',
            '--request', str(file), '--api-base', self.case.fixture.base), 'stale_assignment')
        self.assertNotIn('clarification', self.case.assignment.tracker.inspect().get('requirement_changes', {}))

    def assert_final_frontier_drift_refused(self, fault):
        self.case.boundary_change = fault
        result = self.case.cli()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('error', json.loads(result.stderr))
        self.assertEqual(self.case.writes('PUT'), [])
        self.assertNotIn('merge', self.case.stored())
        self.assertIn('merge_refusal', self.case.stored())

    def test_native_blocker_drift_at_protection_read_refuses_merge(self):
        self.assert_final_frontier_drift_refused(lambda: self.case.assignment.tracker.deps.update({10: [12]}))

    def test_board_scope_drift_at_protection_read_refuses_merge(self):
        self.assert_final_frontier_drift_refused(lambda: self.case.assignment.tracker.members[10]['fields'].update(SCOPE='M2'))

    def test_board_progress_drift_at_last_protection_read_refuses_merge(self):
        parent = self.case.fixture.server.RequestHandlerClass
        case = self.case
        fired = []
        class Handler(parent):
            def do_GET(inner):
                if inner.path.endswith('/branches/main/protection') and sum(
                        c[:2] == ('GET', inner.path) for c in case.calls) == 2:
                    fired.append(True)
                    case.assignment.tracker.members[10]['fields']['STATUS'] = 'Blocked'
                super().do_GET()
        self.case.fixture.server.RequestHandlerClass = Handler
        self.assert_final_frontier_drift_refused(None)
        self.assertTrue(fired)

    def test_exact_ticket_revision_at_last_protection_read_refuses_approved_candidate(self):
        self.case.calls.clear()
        self.case.boundary_change = lambda: self.case.assignment.tracker.issues[10].update(
            body=self.case.assignment.tracker.issues[10]['body'] + '\nNew material requirement at final admission.')
        result = self.case.cli()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(json.loads(result.stderr)['error'], {'scope_mismatch', 'stale_admission', 'stale_requirements'})
        self.assertEqual(self.case.writes('PUT'), [])
        self.assertNotIn('merge', self.case.stored())
