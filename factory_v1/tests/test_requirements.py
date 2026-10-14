"""Scoped revision state, synthesis receipts and final publication/resume guards."""
import copy
import hashlib
import json
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory_v1 import assignments, controls, repositories, requirements, requirement_execution, requirement_publication, tickets
from factory_v1.tests import test_tickets


def revision_record(fixture, affected=(10, 11)):
    """Already-published external precursor; not a manufactured execution receipt."""
    issues = {str(n): copy.deepcopy(fixture.issues[n]) for n in affected}
    direction = {'operator_id': '42', 'origin': fixture.item['origin'], 'reference': 'fixture-direction', 'content': 'Retain exact durable notes.'}
    pin = {'spec': fixture.inputs['handoff'], 'ticket_assignment': 'durable-notes', 'ticket_inputs': 'b' * 64}
    return {'kind': 'material', 'status': 'published', 'affected': list(affected), 'direction': direction,
            'resolution': direction, 'request': {'change_id': 'durable-notes'}, 'new_inputs': copy.deepcopy(fixture.inputs),
            'new_revision': pin, 'old_revision': {str(n): requirements.authority(fixture.item, n) for n in affected},
            'issues': {k: requirements.issue_pin(i) for k, i in issues.items()}, 'updated_issues': {k: requirements.issue_pin(i) for k, i in issues.items()},
            'old_issues': issues, 'updates': {k: {'title': i['title'], 'body': i['body']} for k, i in issues.items()},
            'memberships': {str(n): fixture.members[n]['id'] for n in affected}, 'previous_statuses': {str(n): 'ready' for n in affected},
            'dependency_pins': {str(n): [{k: fixture.issues[b][k] for k in ('number', 'id', 'node_id')} for b in fixture.deps[n]] for n in affected},
            'executions': {'to-spec': {}, 'to-tickets': {}}, 'blocks_verified': True}


class RequirementChangeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_tickets.FrontierFixture(self)
        self.database, self.item, self.github = self.fixture.database, self.fixture.item, self.fixture.github
        self.tracker = self.fixture
        self.root = self.fixture.root
        self.original = copy.deepcopy(self.item)
        self.direction = {'operator_id': '42', 'origin': self.item['origin'], 'reference': 'fixture-direction', 'content': 'Durably retain exact notes.'}
        self.change = {'change_id': 'durable-notes', 'kind': 'material', 'direction': self.direction, 'requirements': ['US-1'],
                       'issues': [10], 'old_revision': {'10': requirements.authority(self.item, 10)}}

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def control(self, command, request, config=None):
        functions = {'record-requirement-change': requirements.record_change, 'resolve-requirement-change': requirements.resolve_change,
                     'resume-requirement-change': requirements.resume_change, 'publish-requirement-change': requirement_publication.publish,
                     'resynthesize-requirements': requirement_execution.execute}
        try:
            args = (self.database, 'product', 'm1', '42', request, self.github)
            value = functions[command](*args, *((config,) if command == 'resynthesize-requirements' else ()))
            return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr='')
        except repositories.RepositoryError as error:
            self.database.rollback()
            return SimpleNamespace(returncode=2, stdout='', stderr=json.dumps({'error': error.code, 'message': str(error)}))

    def record(self):
        return requirements.record_change(self.database, 'product', 'm1', '42', self.change, self.github)

    def resolve(self):
        record = self.fixture.stored()['requirement_changes']['durable-notes']
        return requirements.resolve_change(self.database, 'product', 'm1', '42', {'change_id': 'durable-notes', 'direction': self.direction,
                                            'old_revision': record['old_revision']}, self.github)

    def published(self, affected=(10, 11)):
        record = revision_record(self.fixture, affected)
        self.item['planning'] = {'status': 'complete', 'request': {'skills': []}, 'docs_path': 'docs/original'}
        self.item['requirement_changes'] = {'durable-notes': record}
        for n in affected: self.fixture.members[n]['fields']['STATUS'] = 'Blocked'
        self.fixture.persist()
        return record

    def resume(self, item):
        record = item['requirement_changes']['durable-notes']
        return requirements.resume_change(self.database, 'product', 'm1', '42', {'change_id': 'durable-notes',
            'direction': record['direction'], 'new_revision': record['new_revision']}, self.github)

    def test_material_hold_retires_assignments_dependents_bugs_and_reservation(self):
        assignment = {'assignment_id': 'old', 'ticket_scope': {'project': 'product', 'iteration': 'm1', 'issue_number': 10},
                      'revision': requirements.authority(self.item, 10), 'assignment_ready': True, 'launchable': True,
                      'result_disposition': {'advance_allowed': True, 'close_allowed': True, 'requirements_valid': True}}
        self.database.execute('INSERT INTO role_assignments VALUES (?,?)', ('old', json.dumps(assignment))); self.database.commit()
        pending = {'status': 'synthesis_pending', 'discovery': {'scope': 'current', 'affected': [10], 'symptoms': 'Lost note', 'expected': 'Survives', 'actual': 'Gone'}, 'execution': {'old': 'retained'}, 'issue_intent': {'title': 'unknown'}}
        independent = {'status': 'synthesis_pending', 'discovery': {'scope': 'current', 'affected': [12], 'symptoms': 'Other loss', 'expected': 'Survives', 'actual': 'Gone'}}
        self.item.update(bug_work={'affected': pending, 'independent': independent}, reservation={'issue_number': 10, 'status': 'reserved'})
        self.fixture.persist()
        with patch.object(requirements, 'validity', return_value=[{'issue': 12, 'checks': ['exact-publication-readback']}]) as validity:
            result = self.record()
        record = result['requirement_changes']['durable-notes']; self.assertEqual(record['affected'], [10, 11])
        self.assertEqual(record['retired_assignments'], ['old']); self.assertEqual(validity.call_args.args[-1], {12})
        old = assignments.rows(self.database)[0]
        self.assertFalse(old['assignment_ready']); self.assertFalse(old['launchable']); self.assertFalse(old['result_disposition']['requirements_valid'])
        self.assertEqual(old['requirement_disposition']['status'], 'obsolete')
        self.assertEqual(result['bug_work']['affected']['status'], 'obsolete'); self.assertEqual(result['bug_work']['affected']['issue_intent'], pending['issue_intent'])
        self.assertEqual(result['bug_work']['independent'], independent); self.assertNotIn('reservation', result)
        self.assertEqual(result['reservation_history'][0]['issue_number'], 10)
        self.assertEqual([self.fixture.members[n]['fields']['STATUS'] for n in (10, 11, 12)], ['Blocked', 'Blocked', 'Ready'])
        self.assertEqual(self.fixture.stored(), result)

    def test_minor_trace_requires_validity_without_status_writes_or_material_release(self):
        self.change['kind'] = 'minor'
        with patch.object(requirements, 'validity', return_value=[{'verified': 'unchanged'}]) as validity:
            result = self.record()
        record = result['requirement_changes']['durable-notes']
        self.assertEqual(record['status'], 'clarified'); self.assertEqual(record['new_revision'], record['old_revision'])
        self.assertEqual(validity.call_args.args[-1], {10, 11, 12}); self.fixture.set_field.assert_not_called()
        self.assertEqual(record['preserved_evidence'], [{'verified': 'unchanged'}])
        self.change.update(change_id='later', kind='material'); self.item = self.fixture.stored(); self.fixture.item = self.item
        self.item['requirement_changes']['held'] = {'kind': 'material', 'status': 'held', 'affected': [10]}; self.fixture.persist()
        self.change['kind'] = 'minor'
        with self.assertRaises(repositories.RepositoryError) as caught: self.record()
        self.assertEqual(caught.exception.code, 'change_conflict'); self.database.rollback()

    def test_wrong_operator_stale_revision_and_unresolved_artifacts_refuse(self):
        before = self.fixture.stored()
        for changes, code in (({'direction': {**self.direction, 'operator_id': 'worker'}}, 'approval_required'),
                              ({'old_revision': {'10': {'stale': True}}}, 'stale_requirements'), ({'issues': [99]}, 'scope_mismatch')):
            with self.subTest(changes=changes), self.assertRaises(repositories.RepositoryError) as caught:
                requirements.record_change(self.database, 'product', 'm1', '42', {**self.change, **changes}, self.github)
            self.assertEqual(caught.exception.code, code); self.database.rollback(); self.assertEqual(self.fixture.stored(), before)
        record = self.published(); record['status'] = 'held'; self.fixture.persist()
        with self.assertRaises(repositories.RepositoryError) as caught:
            requirement_execution.execute(self.database, 'product', 'm1', '42', {'change_id': 'durable-notes', 'skills': []}, self.github, '/never-read')
        self.assertEqual(caught.exception.code, 'direction_required')
        with self.assertRaises(repositories.RepositoryError) as caught: self.resume(self.fixture.stored())
        self.assertEqual(caught.exception.code, 'publication_required')

    def test_scope_authority_and_input_selection_keep_independent_original_inputs(self):
        record = self.published((10,)); new = copy.deepcopy(record['new_inputs']); new['documents']['milestone.md']['content'] += 'durable-write'
        record['new_inputs'] = new; record['new_revision']['spec'] = {'revision': 'new'}
        self.item['requirement_authority'] = {'10': record['new_revision']}; self.fixture.persist()
        self.assertEqual(requirements.inputs(self.item, 10), new)
        self.assertEqual(requirements.inputs(self.item, 12), self.fixture.inputs)
        self.assertEqual(requirements.authority(self.item, 10), record['new_revision'])
        self.assertNotEqual(requirements.authority(self.item, 10), requirements.authority(self.item, 12))

    def test_release_preserves_prior_blocked_deferred_done_and_normalizes_active(self):
        record = self.published((10,))
        for prior, expected in (('blocked', 'Blocked'), ('deferred', 'Deferred'), ('done', 'Blocked'), ('active', 'Ready'), ('ready', 'Ready')):
            with self.subTest(prior=prior):
                record['previous_statuses']['10'] = prior; self.fixture.members[10]['fields']['STATUS'] = 'Blocked'
                requirements.sync_blocks(self.database, self.item, record, self.github, release=True)
                self.assertEqual(self.fixture.members[10]['fields']['STATUS'], expected)
                self.assertFalse(self.item['execution_allowed']); self.assertEqual(self.fixture.stored()['requirement_changes']['durable-notes']['status'], 'published')

    def test_requirement_release_cannot_overwrite_independent_new_progress(self):
        record = self.published((10,)); self.fixture.members[10]['fields']['STATUS'] = 'Deferred'
        with self.assertRaises(repositories.RepositoryError) as caught: requirements.sync_blocks(self.database, self.item, record, self.github, release=True)
        self.assertEqual(caught.exception.code, 'publication_mismatch'); self.fixture.set_field.assert_not_called()
        self.assertEqual(self.fixture.members[10]['fields']['STATUS'], 'Deferred')

    def test_record_reopens_delivered_work_but_keeps_it_blocked(self):
        record = self.published((10,)); record['previous_statuses']['10'] = 'done'
        self.fixture.issues[10].update(state='closed', state_reason='completed')
        requirements.sync_blocks(self.database, self.item, record, self.github)
        self.assertEqual(self.fixture.issues[10]['state'], 'open'); self.assertEqual(self.fixture.members[10]['fields']['STATUS'], 'Blocked')
        self.assertEqual(self.github.request.call_args.args[1], {'state': 'open'})

    def test_post_status_membership_body_native_and_spec_drift_retains_pending_hold(self):
        for fault in ('membership', 'body', 'native', 'spec'):
            with self.subTest(fault=fault):
                record = self.published(); before_issues = copy.deepcopy(self.fixture.issues); before_members = copy.deepcopy(self.fixture.members)
                def write(*args):
                    self.fixture.field(*args)
                    if args[2] == 'ITEM11':
                        if fault == 'membership': self.fixture.members[11]['id'] = 'replaced'
                        if fault == 'body': self.fixture.issues[10]['body'] += 'material edit'
                        if fault == 'native': self.fixture.deps[11] = []
                        if fault == 'spec': self.fixture.inputs['documents']['milestone.md']['content'] += 'drift'
                self.fixture.set_field.side_effect = write
                with patch.object(requirement_publication, 'verify_synthesis'):
                    with self.assertRaises(repositories.RepositoryError): self.resume(self.fixture.stored())
                self.database.rollback(); stored = self.fixture.stored()
                self.assertEqual(stored['requirement_changes']['durable-notes']['status'], 'resume-pending')
                self.assertEqual(requirements.held_numbers(stored), {10, 11}); self.assertNotIn('reservation', stored)
                self.fixture.issues.clear(); self.fixture.issues.update(before_issues); self.fixture.members.clear(); self.fixture.members.update(before_members)
                self.fixture.deps[11] = [10]

    def test_partial_status_restore_replays_same_pending_hold_without_repeat_write(self):
        self.published()
        def write(*args):
            if args[2] == 'ITEM11': raise repositories.RepositoryError('github_unavailable', 'status not acknowledged')
            self.fixture.field(*args)
        self.fixture.set_field.side_effect = write
        with patch.object(requirement_publication, 'verify_synthesis'):
            with self.assertRaises(repositories.RepositoryError): self.resume(self.fixture.stored())
            self.database.rollback(); pending = self.fixture.stored()
            self.assertEqual(pending['requirement_changes']['durable-notes']['status'], 'resume-pending'); self.assertEqual(requirements.held_numbers(pending), {10, 11})
            self.fixture.set_field.reset_mock(); self.fixture.set_field.side_effect = self.fixture.field
            result = self.resume(pending)
        self.assertEqual(result['requirement_changes']['durable-notes']['status'], 'resumed')
        self.assertEqual(result['ticket_work']['frontier']['eligible'], [10, 12]); self.assertEqual(self.fixture.set_field.call_count, 1)
        self.assertEqual(self.fixture.stored(), result)

    def test_unconfirmed_old_worker_cleanup_prevents_resume(self):
        self.published(); old = {'assignment_id': 'old', 'ticket_scope': {'project': 'product', 'iteration': 'm1', 'issue_number': 10},
                                'requirement_disposition': {'change_id': 'durable-notes'}, 'runtime': {'status': 'unconfirmed', 'container_removed': False}}
        self.database.execute('INSERT INTO role_assignments VALUES (?,?)', ('old', json.dumps(old))); self.database.commit()
        with patch.object(requirement_publication, 'verify_synthesis'), self.assertRaises(repositories.RepositoryError) as caught: self.resume(self.fixture.stored())
        self.assertEqual(caught.exception.code, 'worker_active'); self.fixture.set_field.assert_not_called()
        self.assertEqual(self.fixture.stored()['requirement_changes']['durable-notes']['status'], 'published')

    def stage(self):
        record = self.published((10,)); record['executions'] = {}; record['status'] = 'resolved'; self.fixture.persist()
        work = {'skill': self.fixture.work['skill'], 'inputs': self.fixture.inputs, 'adaptation': requirements.ADAPTATION,
                'load_sources': {'revision-adaptation': tickets.digest(requirements.ADAPTATION)}}
        result = {'one': 'fixture result'}
        produced = {'result': result, 'execution': self.fixture.evidence(result, work)}
        return record, work, produced

    def test_final_blocked_status_write_cannot_finalize_drifted_publication(self):
        record = self.published(); record.update(status='publication-pending', synthesis_request={'skills': []},
            documents={'milestone.md': 'fixture versioned milestone'}, tickets=[{'number': n} for n in (10, 11)])
        record['publication'] = {'path': 'docs/revision', 'commit': 'd' * 40}
        for n in (10, 11): self.fixture.members[n]['fields']['STATUS'] = 'Ready'
        self.fixture.persist(); fired = []
        def write(*args):
            self.fixture.field(*args)
            if args[2] == 'ITEM11':
                self.fixture.issues[10]['title'] = 'Unexpected material edit'; fired.append(True)
        self.fixture.set_field.side_effect = write
        with patch.object(requirement_publication, 'verify_synthesis'), patch.object(requirement_publication, 'reconcile'), \
                patch.object(requirement_publication, 'version_documents', return_value=record['publication']), \
                patch.object(requirement_publication, 'spec_issue', return_value=8), \
                patch('factory_v1.planning.complete', side_effect=lambda _d, i, *_: i):
            with self.assertRaises(repositories.RepositoryError) as caught:
                requirement_publication.publish(self.database, 'product', 'm1', '42', {'change_id': 'durable-notes'}, self.github)
        self.assertEqual(caught.exception.code, 'stale_requirements'); self.database.rollback()
        self.assertEqual(fired, [True]); stored = self.fixture.stored()
        self.assertEqual(stored['requirement_changes']['durable-notes']['status'], 'publication-pending')
        self.assertEqual(requirements.held_numbers(stored), {10, 11}); self.github.request.assert_not_called()

    def test_updated_issue_effect_lost_response_replays_without_repeated_patch(self):
        record = self.published((10,)); record.update(status='publication-pending', synthesis_request={'skills': []},
            documents={'milestone.md': 'fixture immutable milestone'}, tickets=[{'number': 10}])
        record['publication'] = {'path': 'docs/revision', 'commit': 'd' * 40}
        record['updates']['10']['body'] += '\n<!-- fixture revision -->\n'; self.fixture.persist()
        def patch_issue(path, body, method=None):
            self.assertEqual(self.fixture.stored()['requirement_changes']['durable-notes']['update_intents'], ['10'])
            self.fixture.request(path, body, method)
            raise repositories.RepositoryError('github_unavailable', 'lost patch acknowledgement')
        self.github.request.side_effect = patch_issue
        with patch.object(requirement_publication, 'verify_synthesis'), patch.object(requirement_publication, 'reconcile'), \
                patch.object(requirement_publication, 'version_documents', return_value=record['publication']), \
                patch.object(requirement_publication, 'spec_issue', return_value=8), \
                patch('factory_v1.planning.complete', side_effect=lambda _d, i, *_: i):
            result = requirement_publication.publish(self.database, 'product', 'm1', '42', {'change_id': 'durable-notes'}, self.github)
            self.assertEqual(result['requirement_changes']['durable-notes']['status'], 'published')
            self.github.request.assert_called_once(); self.github.request.reset_mock()
            replay = requirement_publication.publish(self.database, 'product', 'm1', '42', {'change_id': 'durable-notes'}, self.github)
        self.github.request.assert_not_called(); self.assertEqual(self.fixture.stored(), replay)
        self.assertEqual(replay['requirement_changes']['durable-notes']['updated_issues']['10'], requirements.issue_pin(self.fixture.issues[10]))
        self.assertEqual(requirements.held_numbers(replay), {10})

    def test_host_executor_byte_permissions_and_arguments_are_checked_without_execution(self):
        path = self.root / 'executor'; path.write_text('fixture adapter bytes, never executed'); path.chmod(0o700)
        config_path = self.root / 'synthesis.json'
        config = {'operator_id': '42', 'reference': 'fixture-approval', 'execution_reference': 'fixture-host-verification',
                  'argv': [str(path)], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'timeout': 1}
        config_path.write_text(json.dumps(config)); config_path.chmod(0o600)
        self.assertEqual(requirement_execution.configuration(self.database, str(config_path), '42'), config)
        for fault in ('bytes', 'permission', 'arguments', 'operator'):
            with self.subTest(fault=fault):
                path.write_text('changed' if fault == 'bytes' else 'fixture adapter bytes, never executed')
                config_path.chmod(0o644 if fault == 'permission' else 0o600)
                bad = copy.deepcopy(config)
                if fault == 'arguments': bad['argv'].append('--unreviewed')
                if fault == 'operator': bad['operator_id'] = 'worker'
                config_path.write_text(json.dumps(bad))
                with self.assertRaises(repositories.RepositoryError): requirement_execution.configuration(self.database, str(config_path), '42')

    def test_stage_records_validated_execution_and_reuses_exact_inputs(self):
        record, work, produced = self.stage(); config = {'execution_reference': 'fixture-host-execution'}
        with patch.object(requirement_execution, 'invoke', return_value=produced) as invoke:
            self.assertEqual(requirement_execution.run_stage(self.database, self.item, record, 'to-spec', config, work), produced['result'])
            self.assertEqual(requirement_execution.run_stage(self.database, self.item, record, 'to-spec', config, work), produced['result'])
            invoke.assert_called_once(); self.assertEqual(self.fixture.stored()['requirement_changes']['durable-notes']['executions']['to-spec']['evidence'], produced['execution'])
            with self.assertRaises(repositories.RepositoryError): requirement_execution.run_stage(self.database, self.item, record, 'to-spec', config, {**work, 'adaptation': 'drift'})

    def test_missing_execution_evidence_retains_receipt_and_denies_automatic_retry(self):
        record, work, produced = self.stage(); produced['execution']['loads'] = []
        with patch.object(requirement_execution, 'invoke', return_value=produced) as invoke:
            with self.assertRaises(repositories.RepositoryError) as caught: requirement_execution.run_stage(self.database, self.item, record, 'to-spec', {}, work)
            self.assertEqual(caught.exception.code, 'execution_evidence_required')
            stored = self.fixture.stored()['requirement_changes']['durable-notes']
            self.assertEqual(stored['execution_receipts']['to-spec'], produced); self.assertNotIn('to-spec', stored['executions'])
            with self.assertRaises(repositories.RepositoryError) as caught: requirement_execution.run_stage(self.database, self.item, record, 'to-spec', {}, work)
            self.assertEqual(caught.exception.code, 'execution_uncertain'); invoke.assert_called_once()

    def test_late_pause_keeps_truthful_stage_receipt_but_never_admits_result(self):
        record, work, produced = self.stage()
        def invoke(*_): self.fixture.pause(); return produced
        with patch.object(requirement_execution, 'invoke', side_effect=invoke):
            with self.assertRaises(repositories.RepositoryError) as caught:
                requirement_execution.run_stage(self.database, self.item, record, 'to-spec', {'execution_reference': 'fixture'}, work)
        self.assertEqual(caught.exception.code, 'iteration_paused'); self.database.rollback()
        stored = self.fixture.stored()['requirement_changes']['durable-notes']
        self.assertEqual(stored['execution_receipts']['to-spec'], produced); self.assertNotIn('to-spec', stored['executions'])
        self.assertIn('to-spec', stored['attempts']); self.assertTrue(controls.blocked(self.database, 'product', 'm1'))

    def test_late_sqlite_exception_keeps_receipt_without_admitting_execution(self):
        class FaultConnection(sqlite3.Connection):
            fail_control_read = False

            def execute(connection, sql, *args):
                if connection.fail_control_read and sql == 'PRAGMA database_list':
                    connection.fail_control_read = False
                    # Raise an actual SQLite dependency error, not a mocked guard refusal.
                    return super().execute('SELECT * FROM nonexistent_control_read')
                return super().execute(sql, *args)

        self.database.close()
        database = sqlite3.connect(self.fixture.state, factory=FaultConnection)
        self.fixture.database = self.database = database
        record, work, produced = self.stage()
        def invoke(_config, _work):
            database.fail_control_read = True
            return produced
        with patch.object(requirement_execution, 'invoke', side_effect=invoke) as external:
            with self.assertRaisesRegex(sqlite3.OperationalError, 'nonexistent_control_read'):
                requirement_execution.run_stage(database, self.item, record, 'to-spec', {'execution_reference': 'fixture host receipt'}, work)
        database.rollback(); stored = self.fixture.stored()['requirement_changes']['durable-notes']
        self.assertEqual(stored['execution_receipts']['to-spec'], produced)
        self.assertIn('to-spec', stored['attempts']); self.assertNotIn('to-spec', stored['executions']); external.assert_called_once()

    def test_unknown_commit_intent_never_creates_another_version(self):
        record = self.published((10,)); record['publication'] = {'branch': 'factory/revision/m1/durable-notes',
            'path': 'docs/factory/m1/revisions/durable-notes', 'tree': 'f' * 40, 'parent': 'a' * 40, 'commit_intent': True}
        self.fixture.persist()
        with self.assertRaises(repositories.RepositoryError) as caught: requirement_publication.version_documents(self.database, self.item, record, self.github)
        self.assertEqual(caught.exception.code, 'publication_uncertain'); self.github.request.assert_not_called()

    def test_updated_body_preserves_unrelated_bytes_identity_blockers_and_footer(self):
        old = self.fixture.inputs; new = copy.deepcopy(old); new['handoff']['issue']['number'] = 8
        new['documents']['milestone.md']['url'] = old['documents']['milestone.md']['url'].replace('a' * 40, 'b' * 40)
        ticket = {**self.fixture.ticket(), 'desired_behavior': 'Persist durably.', 'references': [{'spec': new['documents']['milestone.md']['url'], 'requirement': 'US-1'}]}
        for newline in ('\n', '\r\n'):
            issue = copy.deepcopy(self.fixture.issues[11]); issue['body'] = issue['body'].replace('\n', newline)
            body = requirement_publication.updated_body(issue, ticket, new, old)
            before_parts, after_parts = tickets.sections(issue['body']), tickets.sections(body)
            for name in ('Blocked by', 'Status', 'Iteration', 'Triage'):
                self.assertEqual(after_parts[name], before_parts[name])
            self.assertTrue(body.endswith(issue['body'][issue['body'].index('<!--'):]))
            self.assertIn(new['documents']['milestone.md']['url'], body); self.assertEqual(after_parts['Parent'], '#8')
            self.assertTrue(tickets.external_contract({**issue, 'body': body}, new))


class StuckRequirementRevisionTests(unittest.TestCase):
    def test_correlated_resume_releases_only_linked_revise_hold(self):
        fixture = test_tickets.FrontierFixture(self); record = revision_record(fixture, (10,))
        linked = {'assignment_id': 'stuck', 'event_id': 'event', 'applied_response': {'reference': 'fixture'}, 'affected': [10], 'authorization': {'fixture': True}}
        record['stuck_revision'] = linked
        fixture.item.update(requirement_changes={'durable-notes': record}, stuck_work={
            'stuck': {**linked, 'status': 'revision-required', 'previous_statuses': {'10': 'ready'}},
            'other': {'status': 'revision-required', 'affected': [10], 'previous_statuses': {'10': 'ready'}}})
        fixture.members[10]['fields']['STATUS'] = 'Blocked'; fixture.persist()
        with patch.object(requirement_publication, 'verify_synthesis'):
            result = requirements.resume_change(fixture.database, 'product', 'm1', '42', {'change_id': 'durable-notes',
                'direction': record['direction'], 'new_revision': record['new_revision']}, fixture.github)
        self.assertEqual(result['stuck_work']['stuck']['status'], 'released')
        self.assertEqual(result['stuck_work']['other']['status'], 'revision-required')
        self.assertEqual(fixture.members[10]['fields']['STATUS'], 'Blocked'); self.assertNotIn(10, result['ticket_work']['frontier']['eligible'])
        self.assertEqual(fixture.stored(), result)
