"""Deterministic bug admission checks; all tracker/source boundaries are mocked."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import bugs, controls, requirements, tickets
from factory_v1.repositories import RepositoryError


class BugAdmissionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = sqlite3.connect(Path(directory.name) / 'state.sqlite')
        self.addCleanup(self.database.close)
        self.database.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
        self.inputs = {'handoff': {'issue': {'number': 7}}, 'documents': {
            'milestone.md': {'url': 'fixture:milestone', 'sha256': 'document-pin'}}}
        self.board = {'project_id': 'P1', 'milestone': {'id': 501, 'number': 1},
                      'status': {'id': 'STATUS', 'options': {'Todo': 'todo'}}}
        self.discovery = {'symptoms': 'Accents disappear', 'reproduction': ['Reopen café.'],
                          'evidence': ['fixture:observation'], 'expected': 'café', 'actual': 'caf',
                          'environment': 'Mock tracker', 'scope': 'current',
                          'scope_reason': 'Blocks the current requirement.', 'affected': [10]}
        self.bug = {'title': 'Preserve accents', 'desired_behavior': 'Reopen café unchanged.',
                    'references': [{'spec': 'fixture:milestone', 'requirement': 'US-1'}],
                    'acceptance_criteria': ['Reopening preserves café.'], 'blockers': [],
                    'status': 'ready', 'scope': 'current', 'affected': [10], 'triage_label': 'triage',
                    'comparisons': [{'number': 10, 'symptoms_match': False, 'scope_match': False,
                                     'rationale': 'Feature work, not the observed defect.'}]}
        self.affected = {'number': 10, 'id': 1010, 'node_id': 'I10', 'state': 'open',
                         'title': 'Feature', 'body': '## Blocked by\r\n\r\nNone (can start immediately).\r\n\r\n## Unrelated\r\n\r\nKeep café.\r\n'}
        self.member: dict = {'id': 'M13', 'status': 'todo'}
        self.work = {'assignment_id': 'bug-assignment', 'input_digest': 'bug-input-pin',
                     'adaptation': bugs.ADAPTATION, 'status': 'publication_pending',
                     'skill': {'path': 'fixture:to-tickets', 'sha256': 'skill-pin'},
                     'request': {'skill': 'fixture:to-tickets'}, 'inputs': self.inputs,
                     'board': self.board, 'discovery': self.discovery,
                     'scoped_inputs': {'10': self.inputs}, 'bug': self.bug,
                     'candidates': [{'issue': copy.deepcopy(self.affected), 'scope': 501,
                                     'membership': {'id': 'M10', 'status': 'todo'}, 'dependencies': []}],
                     'execution': {'run_id': 'fixture-run', 'decomposition': 'One observed defect.',
                                   'loads': [{'source': source, 'sha256': pin, 'tool_reference': 'fixture:load'}
                                             for source, pin in [('fixture:to-tickets', 'skill-pin'),
                                                                 ('fixture:milestone', 'document-pin')]],
                                   'result_digest': tickets.digest(self.bug)}}
        self.item = {'project_id': 'p1', 'iteration_id': 'm1', 'repository': 'example/product',
                     'status': 'active', 'stage': 'bug_synthesis_pending',
                     'correlation': {'stage': 'bug_synthesis_pending'}, 'execution_allowed': False,
                     'approval': {'operator_id': 'operator', 'reference': 'fixture:approval'},
                     'origin': {'conversation': 'fixture:origin'},
                     'repository_onboarding': {'status': 'verified'},
                     'planning': {'revision': 1}, 'handoff': self.inputs['handoff'],
                     'budget': {'remaining': 3},
                     'ticket_work': {'assignment_id': 'tickets-assignment', 'input_digest': 'tickets-input-pin',
                                     'publication_complete': True, 'request': {
                         'skill': 'fixture:to-tickets', 'tracker': {
                             'triage_label': 'triage', 'statuses': {'ready': 'Todo'}}}},
                     'bug_work': {'defect': self.work}}
        self.work['scoped_revisions'] = {'10': requirements.authority(self.item, 10)}
        self.issue = {'number': 13, 'id': 1013, 'node_id': 'I13', 'title': self.bug['title'],
                      'body': bugs.render(self.item, self.work), 'state': 'open',
                      'labels': [{'name': 'triage'}],
                      'repository_url': 'https://api.github.com/repos/example/product',
                      'html_url': 'https://github.com/example/product/issues/13'}
        self.issues = {10: self.affected, 13: self.issue}
        self.dependencies = {10: [], 13: []}
        self.github = Mock(spec=['request'])
        self.github.request.side_effect = AssertionError('Unexpected tracker mutation')
        self.context = self.enterContext(patch.object(bugs, 'context', return_value=(
            self.inputs, self.board, {'10': self.inputs})))
        self.enterContext(patch.object(tickets, 'skill', return_value=self.work['skill']))
        self.enterContext(patch.object(tickets, 'external_contract', return_value=True))
        self.enterContext(patch.object(tickets, 'validate_contract'))
        self.frontier = self.enterContext(patch.object(tickets, 'frontier', side_effect=lambda db, item, gh: item))
        self.observations = self.enterContext(patch.object(bugs, 'observations', return_value=[
            *self.work['candidates'], {'issue': self.issue}]))
        self.read_issue = self.enterContext(patch.object(bugs.gh, 'read_issue', side_effect=lambda gh, repo, n: copy.deepcopy(self.issues[n])))
        self.membership = self.enterContext(patch.object(bugs.gh, 'membership', side_effect=lambda *args: copy.deepcopy(self.member)))
        self.scope = self.enterContext(patch.object(bugs.gh, 'issue_scope', return_value=501))
        self.read_dependencies = self.enterContext(patch.object(bugs.gh, 'dependencies', side_effect=lambda gh, repo, n: copy.deepcopy(self.dependencies[n])))
        self.add_dependency = self.enterContext(patch.object(bugs.gh, 'add_dependency', side_effect=AssertionError('Unexpected edge write')))
        self.add_item = self.enterContext(patch.object(bugs.gh, 'add_item', side_effect=AssertionError('Unexpected membership write')))
        self.set_field = self.enterContext(patch.object(bugs.gh, 'set_field', side_effect=AssertionError('Unexpected field write')))
        self.mutations = (self.github.request, self.add_dependency, self.add_item, self.set_field)
        self.database.execute('INSERT INTO iterations VALUES (?, ?, ?)', ('p1', 'm1', json.dumps(self.item)))
        self.database.commit()

    def save(self):
        tickets.checkpoint(self.database, self.item)

    def stored(self):
        return json.loads(self.database.execute('SELECT payload FROM iterations').fetchone()[0])

    def pause(self, result):
        # Inject at a read/response return, after the caller's initial active check.
        self.requested = self.stored()
        with controls.lane(self.database, 'p1', 'm1'):
            controls.write(self.database, {
                'project': 'p1', 'iteration': 'm1', 'operator_id': 'operator',
                'reference': 'fixture:pause-at-final-read', 'request_id': 'pause-request',
                'action': 'pause', 'state': 'requested', 'history': [], 'cleanup': {},
                'checkpoint': {'iteration': self.requested}})
        self.writes_at_pause = [call.call_count for call in self.mutations]
        return copy.deepcopy(result)

    def assert_paused(self, operation):
        before = self.stored()
        with self.assertRaises(RepositoryError) as raised, self.database:
            operation()
        self.assertEqual(raised.exception.code, 'iteration_paused')
        self.assertTrue(controls.blocked(self.database, 'p1', 'm1'))
        self.assertEqual([call.call_count for call in self.mutations], self.writes_at_pause)
        after = self.stored()
        for key in ('stage', 'status', 'correlation', 'execution_allowed', 'repository',
                    'repository_onboarding', 'origin', 'approval', 'planning', 'handoff', 'ticket_work', 'budget'):
            self.assertEqual(after[key], before[key], key)
        self.assertNotIn('requirement_authority', after)
        for work in after.get('bug_work', {}).values():
            self.assertNotIn('blocker', work, 'Pause is not a publication failure')
            self.assertFalse(work.get('publication_complete', False))
        self.frontier.assert_not_called()
        return after

    def publish(self):
        return bugs.publish(self.database, self.item, {'assignment_id': self.work['assignment_id']}, self.github)

    def test_final_duplicate_search_prevents_new_synthesis_claim(self):
        self.item.pop('bug_work')
        self.save()
        self.observations.side_effect = lambda *args: self.pause(self.work['candidates'])
        before = self.stored()
        after = self.assert_paused(lambda: bugs.synthesize(self.database, self.item, {
            'discovery_id': 'observer', 'skill': 'fixture:to-tickets', 'discovery': self.discovery}, self.github))
        self.assertNotIn('bug_work', self.requested)
        self.assertEqual(after, before)
        self.assertNotIn('bug_discoveries', after)

    def test_pinned_read_prevents_new_discovery_alias(self):
        key = bugs.fingerprint(self.discovery, self.item)
        self.item['bug_work'] = {key: self.work}
        self.item['bug_discoveries'] = {'original-observer': {'key': key, 'request_digest': 'original-request'}}
        self.save()
        self.context.side_effect = lambda *args: self.pause((self.inputs, self.board, {'10': self.inputs}))
        before = self.stored()
        after = self.assert_paused(lambda: bugs.synthesize(self.database, self.item, {
            'discovery_id': 'new-observer', 'skill': 'fixture:to-tickets', 'discovery': self.discovery}, self.github))
        self.assertEqual(after, before)
        self.assertEqual(after['bug_discoveries'], before['bug_discoveries'])
        self.observations.assert_not_called()

    def test_pinned_read_prevents_result_admission(self):
        self.work['status'] = 'synthesis_pending'
        self.work.pop('bug')
        evidence = self.work.pop('execution')
        self.save()
        self.context.side_effect = lambda *args: self.pause((self.inputs, self.board, {'10': self.inputs}))
        before = self.stored()
        after = self.assert_paused(lambda: bugs.complete(self.database, self.item, {
            'assignment_id': self.work['assignment_id'], 'input_digest': self.work['input_digest'],
            'adaptation': bugs.ADAPTATION, 'execution': evidence, 'bug': self.bug}, self.github))
        self.assertEqual(after, before)
        self.assertNotIn('completion_digest', after['bug_work']['defect'])
        self.observations.assert_not_called()

    def test_final_duplicate_search_prevents_new_issue_intent(self):
        self.work['status'] = 'synthesis_pending'
        evidence = self.work.pop('execution')
        self.work.pop('bug')
        self.save()
        self.observations.side_effect = lambda *args: self.pause(self.work['candidates'])
        request = {'assignment_id': self.work['assignment_id'], 'input_digest': self.work['input_digest'],
                   'adaptation': bugs.ADAPTATION, 'execution': evidence, 'bug': self.bug}
        after = self.assert_paused(lambda: bugs.complete(self.database, self.item, request, self.github))
        work = after['bug_work']['defect']
        self.assertEqual(work, self.requested['bug_work']['defect'])
        self.assertEqual(work['status'], 'publication_pending')
        self.assertEqual(work['execution'], evidence)
        self.assertEqual(work['completion_digest'], tickets.digest(request))
        self.assertNotIn('issue_intent', work)

    def test_admitted_issue_response_retains_receipt_without_next_intent(self):
        self.observations.return_value = self.work['candidates']
        self.github.request.side_effect = lambda *args: self.pause(self.issue)
        self.membership.return_value = None
        self.membership.side_effect = None
        after = self.assert_paused(self.publish)
        self.assertIn('issue_intent', self.requested['bug_work']['defect'])
        self.assertNotIn('identity', self.requested['bug_work']['defect'])
        work = after['bug_work']['defect']
        self.assertEqual(work['identity'], {'number': 13, 'id': 1013, 'node_id': 'I13'})
        self.assertEqual(work['issue_intent']['body'], self.issue['body'])
        self.assertNotIn('membership_intent', work)
        self.github.request.assert_called_once()
        self.add_item.assert_not_called()

    def test_native_blocker_read_prevents_new_edge_intent(self):
        self.bug['blockers'] = [12]
        self.work['candidates'].append({'issue': {'number': 12, 'id': 1012, 'node_id': 'I12'}})
        self.issue['body'] = bugs.render(self.item, self.work)
        self.save()
        self.read_dependencies.side_effect = lambda *args: self.pause([])
        after = self.assert_paused(self.publish)
        work = after['bug_work']['defect']
        self.assertEqual(work['identity']['number'], 13)
        self.assertNotIn('edge_intents', work)
        self.add_dependency.assert_not_called()

    def test_membership_read_prevents_new_membership_intent(self):
        self.membership.side_effect = lambda *args: self.pause(None)
        after = self.assert_paused(self.publish)
        self.assertNotIn('membership_intent', after['bug_work']['defect'])
        self.add_item.assert_not_called()

    def test_membership_read_prevents_new_field_intent(self):
        self.member['status'] = None
        reads = 0

        def membership(*args):
            nonlocal reads
            reads += 1
            return None if reads == 1 else self.pause(self.member)

        self.membership.side_effect = membership
        self.add_item.side_effect = None
        after = self.assert_paused(self.publish)
        self.assertTrue(after['bug_work']['defect']['membership_intent'])
        self.assertNotIn('field_intents', after['bug_work']['defect'])
        self.add_item.assert_called_once_with(self.github, 'P1', 'I13')
        self.set_field.assert_not_called()
        self.assertIsNone(self.member['status'])

    def test_affected_issue_read_prevents_new_body_intent(self):
        body = self.affected['body']

        def read_issue(gh, repo, number):
            return self.pause(self.affected) if number == 10 else copy.deepcopy(self.issue)

        self.read_issue.side_effect = read_issue
        after = self.assert_paused(self.publish)
        self.assertNotIn('affected_intents', after['bug_work']['defect'])
        self.assertEqual(self.affected['body'], body)
        self.github.request.assert_not_called()

    def test_affected_native_read_prevents_new_edge_intent(self):
        body = self.affected['body']
        target = body.replace('None (can start immediately).', '- #13')
        self.github.request.side_effect = lambda path, payload, method: self.affected.update(payload)

        def dependencies(gh, repo, number):
            return self.pause([]) if number == 10 else []

        self.read_dependencies.side_effect = dependencies
        after = self.assert_paused(self.publish)
        self.assertEqual(after['bug_work']['defect']['affected_intents']['10'], {'before': body, 'body': target})
        self.assertEqual(after['bug_work']['defect'].get('edge_intents', []), [])
        self.assertEqual(self.affected['body'], target)
        self.github.request.assert_called_once_with('/repos/example/product/issues/10', {'body': target}, method='PATCH')
        self.add_dependency.assert_not_called()

    def test_final_affected_read_retains_observation_without_advancement(self):
        body = self.affected['body']
        target = body.replace('None (can start immediately).', '- #13')
        self.work['affected_intents'] = {'10': {'before': body, 'body': target}}
        self.affected['body'] = target
        self.dependencies[10] = [{'number': 13, 'id': 1013, 'node_id': 'I13'}]
        self.save()
        reads = 0

        def dependencies(gh, repo, number):
            nonlocal reads
            if number == 10:
                reads += 1
                if reads == 3:  # Two edge readbacks, then the final affected observation.
                    return self.pause(self.dependencies[10])
            return copy.deepcopy(self.dependencies[number])

        self.read_dependencies.side_effect = dependencies
        after = self.assert_paused(self.publish)
        work = after['bug_work']['defect']
        self.assertEqual(work['status'], 'publication_pending')
        self.assertNotIn('publication_complete', work)
        self.assertNotIn('observation', self.requested['bug_work']['defect'])
        self.assertEqual(work['observation']['issue'], self.issue)
        self.assertEqual(work['observation']['membership']['status'], 'todo')
        self.assertEqual(self.affected['body'], target)
        self.assertEqual(self.dependencies[10], [{'number': 13, 'id': 1013, 'node_id': 'I13'}])
        self.github.request.assert_not_called()

    def test_explicit_resume_reopens_only_admission_and_reuses_publication(self):
        body = self.affected['body']
        target = body.replace('None (can start immediately).', '- #13')
        self.work['affected_intents'] = {'10': {'before': body, 'body': target}}
        self.work['issue_intent'] = {'body': self.issue['body']}
        self.affected['body'] = target
        self.dependencies[10] = [{'number': 13, 'id': 1013, 'node_id': 'I13'}]
        self.save()
        before = self.stored()
        self.pause(None)
        self.assert_paused(self.publish)
        record = controls.read(self.database, 'p1', 'm1')
        assert record is not None
        with controls.lane(self.database, 'p1', 'm1'):
            controls.write(self.database, {**record, 'action': 'resume', 'state': 'completed',
                                          'reference': 'fixture:explicit-resume'})
        with self.database:
            after = self.publish()
        work = after['bug_work']['defect']
        self.assertTrue(work['publication_complete'])
        self.assertEqual(work['status'], 'published')
        for key in ('assignment_id', 'input_digest', 'execution', 'issue_intent'):
            self.assertEqual(work[key], before['bug_work']['defect'][key])
        self.assertEqual(after['stage'], 'bug_tracked')
        self.assertEqual(after['budget'], before['budget'])
        self.assertEqual(self.stored(), after)
        self.assertEqual(self.affected['body'], target)
        self.assertEqual(after['requirement_authority']['13'], self.work['scoped_revisions']['10'])
        for mutation in self.mutations:
            mutation.assert_not_called()
        self.frontier.assert_called_once()

    def test_revision_and_retired_assignments_refuse_completion_and_publication(self):
        for archived in (False, True):
            for retired in (False, True):
                with self.subTest(archived=archived, retired=retired):
                    item = copy.deepcopy(self.item)
                    work = item['bug_work']['defect']
                    if archived:
                        item['bug_work_history'] = [item.pop('bug_work')['defect']]
                    if retired:
                        work['requirement_disposition'] = 'retired'
                    item['requirement_changes'] = {'revision': {
                        'kind': 'material', 'status': 'publication_pending', 'affected': [10]}}
                    self.assertEqual(requirements.held_numbers(item), {10})
                    tickets.checkpoint(self.database, item)
                    completion = {'assignment_id': work['assignment_id'], 'input_digest': work['input_digest'],
                                  'adaptation': bugs.ADAPTATION, 'execution': work['execution'], 'bug': work['bug']}
                    for operation in (
                        lambda: bugs.publish(self.database, item, {'assignment_id': work['assignment_id']}, self.github),
                        lambda: bugs.complete(self.database, item, completion, self.github),
                    ):
                        with self.assertRaises(RepositoryError) as raised, self.database:
                            operation()
                        self.assertEqual(raised.exception.code, 'stale_requirements' if retired else 'requirements_held')
                        self.assertEqual(self.stored(), item)
                    item['requirement_changes']['revision']['status'] = 'resumed'
                    self.assertEqual(bugs.held_numbers(item), {10})
                    if not retired:
                        self.assertIs(bugs.assigned(item, work['assignment_id']), work)
                    else:
                        with self.assertRaises(RepositoryError) as raised:
                            bugs.assigned(item, work['assignment_id'])
                        self.assertEqual(raised.exception.code, 'stale_requirements')
        self.context.assert_not_called()
        for mutation in self.mutations:
            mutation.assert_not_called()

    def test_resumed_control_does_not_release_revision_or_discovery_holds(self):
        self.work['requirement_disposition'] = 'retired'
        archived = copy.deepcopy(self.work)
        archived['discovery']['affected'] = [11]
        archived['discovery']['symptoms'] = 'Another unsettled discovery'
        self.item['bug_work_history'] = [archived]
        self.item['requirement_changes'] = {'revision': {
            'kind': 'material', 'status': 'publication_pending', 'affected': [10]}}
        self.save()
        before = self.stored()
        self.pause(None)
        record = controls.read(self.database, 'p1', 'm1')
        assert record is not None
        with controls.lane(self.database, 'p1', 'm1'):
            controls.write(self.database, {**record, 'action': 'resume', 'state': 'completed',
                                          'reference': 'fixture:explicit-resume'})
        self.assertFalse(controls.blocked(self.database, 'p1', 'm1'))
        self.assertEqual(requirements.held_numbers(self.stored()), {10})
        self.assertEqual(bugs.held_numbers(self.stored()), {10, 11})
        with self.assertRaises(RepositoryError) as raised, self.database:
            bugs.synthesize(self.database, self.item, {
                'discovery_id': 'new-observer', 'skill': 'fixture:to-tickets', 'discovery': self.discovery}, self.github)
        self.assertEqual(raised.exception.code, 'requirements_held')
        self.assertEqual(self.stored(), before)
        self.context.assert_not_called()
        for mutation in self.mutations:
            mutation.assert_not_called()

    def test_discovery_settlement_does_not_clear_unresolved_current_or_archived_effects(self):
        for archived in (False, True):
            for effect in (None, 'issue_intent', 'membership_intent', 'edge_intents', 'affected_intents', 'field_intents'):
                with self.subTest(archived=archived, effect=effect):
                    old = copy.deepcopy(self.work)
                    old['requirement_disposition'] = 'retired'
                    old['identity'] = {'number': 13, 'id': 1013, 'node_id': 'I13'}
                    if effect:
                        old[effect] = {'unresolved': True}
                    fresh = copy.deepcopy(self.work)
                    fresh.update(assignment_id='fresh-bug', publication_complete=True, status='duplicate')
                    item = {**self.item, 'bug_work': {'fresh': fresh}}
                    if archived:
                        item['bug_work_history'] = [old]
                    else:
                        item['bug_work'] = {'old': old, 'fresh': fresh}
                    self.assertEqual(bugs.held_numbers(item), {10, 13} if effect else set())
                    item['requirement_changes'] = {'revision': {'kind': 'material', 'status': 'resumed', 'affected': [10]}}
                    self.assertEqual(bugs.held_numbers(item), {10, 13} if effect else set())
                    # An older successful publication cannot erase a newer discovery.
                    if not effect:
                        item['bug_work_history'] = [fresh]
                        item['bug_work'] = {'new': old}
                        self.assertEqual(bugs.held_numbers(item), {10, 13})
