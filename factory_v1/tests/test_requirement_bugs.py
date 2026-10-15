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

    def test_discovery_holds_preserve_unknown_effects(self):
        initial = copy.deepcopy(self.item)
        for label in ('current', 'archived', 'archived-unknown', 'settled', 'issue_intent', 'membership_intent',
                      'edge_intents', 'affected_intents', 'field_intents'):
            with self.subTest(label=label):
                self.item.clear(); self.item.update(copy.deepcopy(initial))
                affected = (10, 12) if label in ('current', 'archived', 'archived-unknown') else (10,)
                old = work_record(self.fixture, affected, retired=label != 'current')
                fresh = work_record(self.fixture, affected, published=True, assignment='fresh')
                if label == 'current':
                    self.item.update(bug_work={'old': old}, bug_work_history=[])
                else:
                    self.item.update(bug_work_history=[old], bug_work={} if label == 'archived' else {'fresh': fresh})
                intents = {'issue_intent': {'title': 'unknown'}, 'membership_intent': True, 'edge_intents': [[13, 1010]],
                           'affected_intents': {'10': {'body': 'pending'}}, 'field_intents': {'status': 'Ready'}}
                if label == 'archived-unknown': old.update(issue_intent={'title': 'unknown'}, identity={'number': 13})
                elif label in intents: old[label] = intents[label]
                self.fixture.persist()
                if label == 'settled':
                    self.assertEqual(bugs.held_numbers(self.fixture.stored()), set())
                    self.assertEqual(tickets.frontier(self.database, self.item, self.github)['ticket_work']['frontier']['eligible'], [10, 12])
                else:
                    self.assert_held(*affected)
                    if label in ('current', 'archived', 'archived-unknown'):
                        self.assertEqual(bugs.held_numbers(self.fixture.stored()), {10, 12, 13} if label == 'archived-unknown' else {10, 12})
                    if label in intents:
                        self.assertEqual(self.fixture.stored()['bug_work_history'][0][label], intents[label])
                if label == 'archived-unknown':
                    self.fixture.issues[10].update(state='closed', state_reason='completed'); self.fixture.members[10]['fields']['STATUS'] = 'Done'
                    snapshot = tickets.frontier(self.database, self.item, self.github)['ticket_work']['frontier']
                    self.assertNotIn(11, snapshot['eligible'])
                    with self.assertRaises(repositories.RepositoryError) as caught: tickets.reserve(self.database, self.item, self.github, 11)
                    self.assertEqual(caught.exception.code, 'ticket_ineligible'); self.database.rollback()
                    self.assertNotIn('reservation', self.fixture.stored()); self.assertEqual(self.fixture.stored()['bug_work_history'], [old])
                    self.fixture.issues[10].update(state='open', state_reason=None); self.fixture.members[10]['fields']['STATUS'] = 'Ready'


    def test_bug_refresh_scoped_revision(self):
        initial = copy.deepcopy(self.item)
        original_body = self.fixture.issues[10]['body']
        for label, affected, requested in (('refresh', [10, 12], [10, 12]), ('narrow', [10, 12], [10]), ('independent', [12], [12])):
            with self.subTest(label=label):
                self.item.clear(); self.item.update(copy.deepcopy(initial)); self.fixture.issues[10]['body'] = original_body
                old = work_record(self.fixture, affected, retired=label != 'independent')
                key = bugs.fingerprint(old['discovery'], self.item); self.item['bug_work'] = {key: old}
                revised = copy.deepcopy(self.fixture.inputs)
                if label == 'refresh':
                    revised['documents']['milestone.md']['url'] += '-revision'
                    revised['handoff'] = {'revision': 'new'}; revised['documents']['milestone.md']['sha256'] = 'c' * 64
                    self.item['requirement_authority'] = {'10': {'spec': {'revision': 'new'}, 'ticket_assignment': 'durable-notes', 'ticket_inputs': 'b' * 64}}
                    self.fixture.issues[10]['body'] = original_body.replace(self.fixture.inputs['documents']['milestone.md']['url'], revised['documents']['milestone.md']['url'])
                if label != 'narrow':
                    self.item['requirement_changes'] = {'durable-notes': {'new_inputs': revised, 'kind': 'material', 'status': 'resumed', 'affected': [10]}}
                self.fixture.persist(); before = self.fixture.stored()
                request = {'discovery_id': label, 'skill': self.fixture.start_request['skill'], 'discovery': discovery(requested)}
                scoped = {str(n): revised if n == 10 else self.fixture.inputs for n in requested}
                candidates = [{'issue': copy.deepcopy(self.fixture.issues[n]), 'scope': 'M1'} for n in affected]
                with patch.object(bugs, 'context', return_value=(revised, self.fixture.board, scoped)), patch.object(bugs, 'observations', return_value=candidates):
                    if label == 'narrow':
                        with self.assertRaises(repositories.RepositoryError) as caught: bugs.synthesize(self.database, self.item, request, self.github)
                        self.assertEqual(caught.exception.code, 'bug_conflict'); self.database.rollback(); self.assertEqual(self.fixture.stored(), before)
                    else:
                        result = bugs.synthesize(self.database, self.item, request, self.github); fresh = result['bug_work'][key]
                        if label == 'independent':
                            self.assertEqual(fresh, old); self.assertNotIn('requirement_disposition', fresh)
                        else:
                            self.assertNotEqual(fresh['assignment_id'], old['assignment_id']); self.assertEqual(result['bug_work_history'], [old])
                            self.assertEqual(fresh['inputs'], requirements.inputs(result, 10)); self.assertEqual(fresh['scoped_inputs']['12'], requirements.inputs(result, 12))
                            for pin in scoped.values():
                                for doc in pin['documents'].values(): self.assertEqual(fresh['load_sources'][doc['url']], doc['sha256'])
                            self.assertEqual(bugs.synthesize(self.database, result, request, self.github)['bug_work'][key], fresh)
                            self.assertEqual(self.fixture.stored()['bug_work_history'], [old])
                if label != 'independent':
                    with patch.object(tickets, 'pinned_inputs', side_effect=lambda _d, i, _g: revised if i['handoff'] == {'revision': 'new'} else self.fixture.inputs):
                        self.assert_held(10, 12)







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

    def test_history_settlement_scope_and_chronology(self):
        for label, history, current, held in (
            ('uncovered', [self.record([10, 12])], self.record([10], retired=False, published=True), {10, 12}),
            ('retired-settlement', [self.record([10])], self.record([10], published=True), set()),
            ('newer-discovery', [self.record([10]), self.record([10], published=True)], self.record([10]), {10}),
        ):
            with self.subTest(label=label):
                self.assertEqual(bugs.held_numbers(self.item(history, current)), held)
