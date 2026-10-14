"""Revision-sensitive review/merge admission seams, not a merge-controller journey."""
import copy
import hashlib
import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from factory_v1 import assignments, merge, repositories, requirements, review, sandbox, sandbox_source, tickets
from factory_v1.tests import test_tickets


def record_change(case, fixture, tracker):
    """Compatibility helper: invoke only the real scoped revision function."""
    item = fixture.stored()
    return requirements.record_change(fixture.database, 'product', 'm1', '42', {
        'change_id': 'changed-requirement', 'kind': 'material', 'direction': {
            'operator_id': '42', 'origin': item['origin'], 'reference': 'fixture-direction', 'content': 'Durably retain notes.'},
        'requirements': ['US-1'], 'issues': [10], 'old_revision': {'10': requirements.authority(item, 10)}}, fixture.github)


def candidate_record(fixture):
    handoff = {'stage': 'simplify', 'skills': [], 'issue': requirements.issue_pin(fixture.issues[10])}
    return {'assignment_id': 'candidate', 'ticket_scope': {'project': 'product', 'iteration': 'm1', 'issue_number': 10},
            'revision': requirements.authority(fixture.item, 10), 'handoff': handoff, 'handoff_digest': assignments.digest(handoff),
            'assignment_ready': True, 'result_disposition': {'requirements_valid': True}}


class RequirementReviewTests(unittest.TestCase):
    def test_material_revision_before_review_refuses_old_candidate_on_durable_reload(self):
        fixture = test_tickets.FrontierFixture(self); old = candidate_record(fixture)
        fixture.database.execute('INSERT INTO role_assignments VALUES (?,?)', (old['assignment_id'], json.dumps(old))); fixture.database.commit()
        fixture.item['requirement_changes'] = {'change': {'kind': 'material', 'status': 'held', 'affected': [10]}}; fixture.persist()
        before = fixture.stored()
        with self.assertRaises(repositories.RepositoryError) as caught: review.candidate(fixture.database, 'product', 'm1', '42', 'candidate')
        self.assertEqual(caught.exception.code, 'requirements_held'); self.assertEqual(fixture.stored(), before)
        fixture.github.request.assert_not_called(); self.assertEqual(assignments.rows(fixture.database), [old])


class RequirementMergeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_tickets.FrontierFixture(self)
        self.database, self.item, self.github = self.fixture.database, self.fixture.item, self.fixture.github
        self.item['handoff']['commit'] = 'a' * 40; self.fixture.persist()
        self.request = {'stage': 'implementation', 'repository': 'example/product', 'repository_id': 123,
                        'workspace': str(self.fixture.root / 'workspace'), 'issue': requirements.issue_pin(self.fixture.issues[10]),
                        'spec_commit': 'a' * 40, 'baseline': 'b' * 40, 'candidate': None, 'standards': [], 'preceding': [], 'skills': [],
                        'capabilities': ['read_workspace'], 'result_contract': assignments.CONTRACT, 'claim_id': 'fresh',
                        'profile': {'name': 'fixture', 'home': str(self.fixture.root / 'home'), 'provider': 'fixture', 'model': 'fixture', 'operation': 'chat'}}
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(assignments, 'validate', return_value=[]))
        stack.enter_context(patch.object(assignments, 'admission_preview', return_value={'scope': 'fixture'}))
        self.api = Mock(github=self.github)
        classic = {'required_status_checks': {'strict': True, 'checks': [{'context': 'unit', 'app_id': 1}]},
                   'enforce_admins': {'enabled': True}, 'allow_force_pushes': {'enabled': False}, 'allow_deletions': {'enabled': False}}
        self.classic = classic
        self.config = {'base': 'main', 'required_checks': classic['required_status_checks']['checks'],
                       'protection_sha256': assignments.digest({'rules': [], 'protection': classic})}
        self.api.read.side_effect = lambda path: [] if path == '/rules/branches/main' else copy.deepcopy(classic)

    def admission(self):
        return assignments.prepare(self.database, 'product', 'm1', '42', self.request, self.github, dry_run=True)

    def test_material_revision_after_approval_refuses_result_and_merge_inspection_retains_history(self):
        old = candidate_record(self.fixture); old['requirement_disposition'] = {'change_id': 'change', 'status': 'obsolete'}
        old['assignment_ready'] = False; old['result_disposition']['requirements_valid'] = False
        old['merge_refusal'] = {'code': 'requirements_held'}
        self.database.execute('INSERT INTO role_assignments VALUES (?,?)', ('candidate', json.dumps(old))); self.database.commit()
        for _ in range(2):
            with self.assertRaises(repositories.RepositoryError) as caught: assignments.require_result_active(assignments.rows(self.database)[0])
            self.assertEqual(caught.exception.code, 'requirements_held')
            readback = merge.inspect_merge(self.database, 'product', 'm1', '42', 'candidate')
            self.assertEqual(readback['requirement_disposition'], old['requirement_disposition']); self.assertEqual(readback['refusal']['code'], 'requirements_held')
        self.assertEqual(assignments.rows(self.database), [old]); self.api.request.assert_not_called()

    def test_independent_candidate_retains_exact_revision_evidence_when_other_issue_held(self):
        old = candidate_record(self.fixture)
        self.database.execute('INSERT INTO role_assignments VALUES (?,?)', ('candidate', json.dumps(old))); self.database.commit()
        self.item['requirement_changes'] = {'change': {'kind': 'material', 'status': 'held', 'affected': [12]}}; self.fixture.persist()
        self.assertEqual(assignments.inspect(self.database, 'product', 'm1', '42', 'candidate'), old)
        self.assertEqual(merge.inspect_merge(self.database, 'product', 'm1', '42', 'candidate')['current_revision'], old['revision'])
        self.assertEqual(assignments.rows(self.database), [old]); self.api.request.assert_not_called()

    def test_minor_prose_cannot_revalidate_changed_controller_check_bytes(self):
        root = self.fixture.root / 'artifacts'; (root / 'workspace').mkdir(parents=True); (root / 'inputs/baseline').mkdir(parents=True); (root / 'scratch').mkdir()
        (root / 'controller-checks.json').write_text('{"exit":0}\n'); result = {'fixture': 'result'}
        (root / 'scratch/result.json').write_text(json.dumps(result))
        old = candidate_record(self.fixture)
        old.update(runtime={'status': 'complete', 'artifacts': str(root), 'candidate_sha256': assignments.digest(sandbox_source.manifest(root / 'workspace')),
                            'baseline_sha256': assignments.digest(sandbox_source.manifest(root / 'inputs/baseline')), 'checks_sha256': hashlib.sha256((root / 'controller-checks.json').read_bytes()).hexdigest()},
                   submitted_result=result, result_disposition={'checks_verified': True})
        self.database.execute('INSERT INTO role_assignments VALUES (?,?)', ('candidate', json.dumps(old))); self.database.commit()
        with patch.object(sandbox, 'original', return_value={}):
            checked = requirements.validity(self.database, self.item, self.github, {10})
            self.assertEqual(checked[0]['checks'], ['issue', 'requirements', 'skills', 'candidate'])
            (root / 'controller-checks.json').write_text('{"exit":0}\n\n')
            with self.assertRaises(repositories.RepositoryError) as caught: requirements.validity(self.database, self.item, self.github, {10})
        self.assertEqual(caught.exception.code, 'stale_assignment'); self.assertEqual(assignments.rows(self.database), [old])

    def assert_protection_read_drift_refused(self, fault, code):
        before = self.fixture.stored()
        admitted = self.admission(); self.assertTrue(admitted['assignment_ready']); self.assertFalse(admitted['claim_persisted'])
        fired = []
        def read(path):
            if path == '/branches/main/protection': fault(); fired.append(path); return copy.deepcopy(self.classic)
            return []
        self.api.read.side_effect = read
        merge.protection(self.api, self.config)
        with self.assertRaises(repositories.RepositoryError) as caught: self.admission()
        self.assertEqual(caught.exception.code, code); self.database.rollback()
        self.assertEqual(fired, ['/branches/main/protection']); self.assertEqual(self.fixture.stored(), before)
        self.assertEqual(assignments.rows(self.database), []); self.api.request.assert_not_called(); self.github.request.assert_not_called()

    def test_native_textual_blocker_drift_at_protection_read_refuses_admission(self):
        def fault():
            self.fixture.deps[10] = [12]
            self.fixture.issues[10]['body'] = self.fixture.issues[10]['body'].replace('None (can start immediately).', '- #12')
        self.assert_protection_read_drift_refused(fault, 'ticket_ineligible')

    def test_board_scope_drift_at_protection_read_refuses_admission(self):
        self.assert_protection_read_drift_refused(lambda: self.fixture.members[10]['fields'].update(SCOPE='M2'), 'ticket_ineligible')

    def test_board_progress_drift_at_last_protection_read_refuses_admission(self):
        self.assert_protection_read_drift_refused(lambda: self.fixture.members[10]['fields'].update(STATUS='Blocked'), 'ticket_ineligible')

    def test_exact_ticket_body_drift_at_protection_read_refuses_admission(self):
        self.assert_protection_read_drift_refused(lambda: self.fixture.issues[10].update(body=self.fixture.issues[10]['body'] + '\nNew requirement.'), 'scope_mismatch')

    def test_last_publication_guard_rejects_remote_doc_drift_without_mutation(self):
        merge.require_publication(self.database, self.item, self.api, 10)
        before = self.fixture.stored()
        changed = copy.deepcopy(self.fixture.inputs); changed['documents']['milestone.md']['content'] += 'Remote edit'
        with patch.object(tickets, 'pinned_inputs', return_value=changed):
            with self.assertRaises(repositories.RepositoryError) as caught: merge.require_publication(self.database, self.item, self.api, 10)
        self.assertEqual(caught.exception.code, 'stale_requirements'); self.assertEqual(self.fixture.stored(), before)
        self.assertEqual(assignments.rows(self.database), []); self.api.request.assert_not_called()
