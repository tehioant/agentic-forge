"""Current/archive discovery holds and obsolete-result admission with explicit state."""
import copy
import unittest
from unittest.mock import patch

from factory_v1 import bugs, repositories, requirements, tickets
from factory_v1.tests import test_tickets


def discovery(affected):
    return {'scope': 'current', 'affected': affected, 'symptoms': 'Lost note', 'expected': 'Note survives',
            'actual': 'Note disappears', 'reproduction': ['Save', 'Read'], 'evidence': ['fixture-output'],
            'environment': None, 'scope_reason': 'Current note retention.'}


def work_record(fixture, affected=(10,), retired=False, published=False, assignment='bug-old'):
    value = {'assignment_id': assignment, 'discovery': discovery(list(affected)),
             'status': 'published' if published else 'synthesis_pending', 'publication_complete': published,
             'inputs': fixture.inputs, 'scoped_inputs': {str(n): fixture.inputs for n in affected},
             'board': fixture.board, 'skill': fixture.work['skill'], 'request': {'skill': fixture.start_request['skill']},
             'scoped_revisions': {str(n): requirements.authority(fixture.item, n) for n in affected},
             'candidates': [], 'load_sources': {}, 'input_digest': 'a' * 64, 'adaptation': bugs.ADAPTATION}
    if retired:
        value.update(status='obsolete', requirement_disposition={'status': 'obsolete', 'change_id': 'durable-notes'})
    return value


class RequirementBugTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_tickets.FrontierFixture(self)
        self.database, self.item, self.github = self.fixture.database, self.fixture.item, self.fixture.github

    def assert_held(self, *numbers):
        before = copy.deepcopy((self.fixture.issues, self.fixture.members, self.fixture.deps))
        snapshot = tickets.frontier(self.database, self.fixture.stored(), self.github)['ticket_work']['frontier']
        for number in numbers:
            self.assertNotIn(number, snapshot['eligible']); self.assertTrue(next(r for r in snapshot['items'] if r['number'] == number)['held'])
            with self.assertRaises(repositories.RepositoryError) as caught: tickets.reserve(self.database, self.item, self.github, number)
            self.assertEqual(caught.exception.code, 'ticket_ineligible'); self.database.rollback()
        self.assertEqual((self.fixture.issues, self.fixture.members, self.fixture.deps), before)
        self.assertNotIn('reservation', self.fixture.stored()); self.fixture.set_field.assert_not_called()

    def test_archived_and_current_pending_discoveries_hold_every_affected_issue(self):
        for archived in (False, True):
            old = work_record(self.fixture, (10, 12), retired=archived)
            self.item.update(bug_work={} if archived else {'old': old}, bug_work_history=[old] if archived else [])
            self.fixture.persist(); self.assert_held(10, 12)
            self.assertEqual(bugs.held_numbers(self.fixture.stored()), {10, 12})

    def test_obsolete_result_ids_refused_current_and_archived_after_restart(self):
        for archived in (False, True):
            old = work_record(self.fixture, retired=True)
            self.item.update(bug_work={} if archived else {'old': old}, bug_work_history=[old] if archived else []); self.fixture.persist()
            for function, request in ((bugs.publish, {'assignment_id': 'bug-old'}), (bugs.complete, {'assignment_id': 'bug-old',
                'input_digest': old['input_digest'], 'adaptation': bugs.ADAPTATION, 'execution': {}, 'bug': {}})):
                with self.subTest(archived=archived, function=function.__name__), self.assertRaises(repositories.RepositoryError) as caught:
                    function(self.database, self.fixture.stored(), request, self.github)
                self.assertEqual(caught.exception.code, 'stale_requirements'); self.database.rollback()
                self.assertEqual(self.fixture.stored()['bug_work_history'] if archived else list(self.fixture.stored()['bug_work'].values()), [old])
        self.github.request.assert_not_called()

    def test_retired_refresh_preserves_history_scope_and_uses_each_scoped_authority(self):
        affected = [10, 12]; old = work_record(self.fixture, affected, retired=True)
        key = bugs.fingerprint(old['discovery'], self.item); self.item['bug_work'] = {key: old}
        revised = copy.deepcopy(self.fixture.inputs); revised['documents']['milestone.md']['url'] += '-revision'
        revised['handoff'] = {'revision': 'new'}
        revised['documents']['milestone.md']['sha256'] = 'c' * 64
        record = {'new_inputs': revised, 'kind': 'material', 'status': 'resumed', 'affected': [10]}
        self.item['requirement_changes'] = {'durable-notes': record}
        self.item['requirement_authority'] = {'10': {'spec': {'revision': 'new'}, 'ticket_assignment': 'durable-notes', 'ticket_inputs': 'b' * 64}}
        self.fixture.persist()
        self.fixture.issues[10]['body'] = self.fixture.issues[10]['body'].replace(self.fixture.inputs['documents']['milestone.md']['url'], revised['documents']['milestone.md']['url'])
        candidates = [{'issue': copy.deepcopy(self.fixture.issues[n]), 'scope': 'M1'} for n in affected]
        request = {'discovery_id': 'refresh', 'skill': self.fixture.start_request['skill'], 'discovery': discovery(affected)}
        scoped = {'10': revised, '12': self.fixture.inputs}
        with patch.object(bugs, 'context', return_value=(revised, self.fixture.board, scoped)), patch.object(bugs, 'observations', return_value=candidates):
            result = bugs.synthesize(self.database, self.item, request, self.github)
            fresh = result['bug_work'][key]
            self.assertNotEqual(fresh['assignment_id'], old['assignment_id']); self.assertEqual(result['bug_work_history'], [old])
            self.assertEqual(fresh['inputs'], requirements.inputs(result, 10)); self.assertEqual(fresh['scoped_inputs']['12'], requirements.inputs(result, 12))
            for pin in scoped.values():
                for doc in pin['documents'].values(): self.assertEqual(fresh['load_sources'][doc['url']], doc['sha256'])
            self.assertEqual(bugs.synthesize(self.database, result, request, self.github)['bug_work'][key], fresh)
            self.assertEqual(self.fixture.stored()['bug_work_history'], [old])
        with patch.object(tickets, 'pinned_inputs', side_effect=lambda _d, i, _g: revised if i['handoff'] == {'revision': 'new'} else self.fixture.inputs):
            self.assert_held(10, 12)

    def test_retired_refresh_cannot_drop_affected_scope(self):
        old = work_record(self.fixture, (10, 12), retired=True); key = bugs.fingerprint(old['discovery'], self.item)
        self.item['bug_work'] = {key: old}; self.fixture.persist(); before = self.fixture.stored()
        request = {'discovery_id': 'narrow', 'skill': self.fixture.start_request['skill'], 'discovery': discovery([10])}
        with patch.object(bugs, 'context', return_value=(self.fixture.inputs, self.fixture.board, {'10': self.fixture.inputs})):
            with self.assertRaises(repositories.RepositoryError) as caught: bugs.synthesize(self.database, self.item, request, self.github)
        self.assertEqual(caught.exception.code, 'bug_conflict'); self.database.rollback(); self.assertEqual(self.fixture.stored(), before)
        self.assert_held(10, 12)

    def test_independent_current_assignment_is_not_invalidated_by_unrelated_authority(self):
        old = work_record(self.fixture, (12,)); key = bugs.fingerprint(old['discovery'], self.item)
        self.item.update(bug_work={key: old}, requirement_changes={'durable-notes': {'kind': 'material', 'status': 'resumed', 'affected': [10]}})
        self.fixture.persist()
        request = {'discovery_id': 'independent', 'skill': self.fixture.start_request['skill'], 'discovery': discovery([12])}
        with patch.object(bugs, 'context', return_value=(self.fixture.inputs, self.fixture.board, {'12': self.fixture.inputs})):
            result = bugs.synthesize(self.database, self.item, request, self.github)
        self.assertEqual(result['bug_work'][key], old); self.assertNotIn('requirement_disposition', result['bug_work'][key])

    def test_archived_unknown_creation_survives_later_settlement_and_closed_blocker(self):
        old = work_record(self.fixture, (10, 12), retired=True); old.update(issue_intent={'title': 'unknown'}, identity={'number': 13})
        fresh = work_record(self.fixture, (10, 12), published=True, assignment='fresh')
        self.item.update(bug_work_history=[old], bug_work={'fresh': fresh}); self.fixture.persist()
        self.assert_held(10, 12); self.assertEqual(bugs.held_numbers(self.fixture.stored()), {10, 12, 13})
        self.fixture.issues[10].update(state='closed', state_reason='completed'); self.fixture.members[10]['fields']['STATUS'] = 'Done'
        snapshot = tickets.frontier(self.database, self.item, self.github)['ticket_work']['frontier']
        self.assertNotIn(11, snapshot['eligible'])
        with self.assertRaises(repositories.RepositoryError) as caught: tickets.reserve(self.database, self.item, self.github, 11)
        self.assertEqual(caught.exception.code, 'ticket_ineligible'); self.database.rollback()
        self.assertNotIn('reservation', self.fixture.stored()); self.assertEqual(self.fixture.stored()['bug_work_history'], [old])

    def test_fresh_settlement_releases_discovery_only_not_older_unknown_effect(self):
        old = work_record(self.fixture, retired=True); fresh = work_record(self.fixture, published=True, assignment='fresh')
        self.item.update(bug_work_history=[old], bug_work={'fresh': fresh}); self.fixture.persist()
        self.assertEqual(bugs.held_numbers(self.fixture.stored()), set())
        self.assertEqual(tickets.frontier(self.database, self.item, self.github)['ticket_work']['frontier']['eligible'], [10, 12])
        for field, intent in {'issue_intent': {'title': 'unknown'}, 'membership_intent': True, 'edge_intents': [[13, 1010]],
                              'affected_intents': {'10': {'body': 'pending'}}, 'field_intents': {'status': 'Ready'}}.items():
            with self.subTest(field=field):
                uncertain = {**old, field: intent}; self.item['bug_work_history'] = [uncertain]; self.fixture.persist(); self.assert_held(10)
                self.assertEqual(self.fixture.stored()['bug_work_history'][0][field], intent)


class BugHistoryHoldTests(unittest.TestCase):
    """Pure admission records, not native execution/publication evidence."""

    def record(self, affected, retired=True, published=False):
        work = {'discovery': {'scope': 'current', 'affected': affected, 'symptoms': 'Lost note',
                             'expected': 'Note survives', 'actual': 'Note disappears'},
                'status': 'published' if published else 'synthesis_pending', 'publication_complete': published}
        if retired:
            work['requirement_disposition'] = {'status': 'obsolete'}
            work['status'] = 'obsolete'
        return work

    def item(self, history, current=None):
        return {'repository': 'example/product', 'iteration_id': 'm1', 'bug_work_history': history,
                'bug_work': {'current': current} if current else {}}

    def test_archived_discovery_holds_without_a_current_record(self):
        self.assertEqual(bugs.held_numbers(self.item([self.record([10, 12])])), {10, 12})

    def test_every_archived_pending_effect_survives_fresh_settlement(self):
        effects = {'issue_intent': {'title': 'Lost note'}, 'membership_intent': True,
                   'edge_intents': [[13, 1010]], 'affected_intents': {'10': {'body': 'Pending update'}},
                   'field_intents': {'status': 'Ready'}}
        for field, intent in effects.items():
            with self.subTest(field=field):
                old = self.record([10])
                old.update(identity={'number': 13}, **{field: intent})
                fresh = self.record([10], retired=False, published=True)
                self.assertEqual(bugs.held_numbers(self.item([old], fresh)), {10, 13})

    def test_fresh_settlement_cannot_drop_uncovered_scope(self):
        fresh = self.record([10], retired=False, published=True)
        self.assertEqual(bugs.held_numbers(self.item([self.record([10, 12])], fresh)), {10, 12})

    def test_known_settlement_survives_retirement_but_cannot_settle_newer_discovery(self):
        pending, published = self.record([10]), self.record([10], published=True)
        self.assertEqual(bugs.held_numbers(self.item([pending], published)), set())
        self.assertEqual(bugs.held_numbers(self.item([pending, published], self.record([10]))), {10})
