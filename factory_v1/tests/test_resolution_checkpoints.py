"""Single-publication boundaries; explicit rows and mocked external reads only."""
import copy
import json
import unittest
from itertools import product
from unittest.mock import patch

from factory_v1 import bugs, controls, publication, repositories, restart, tickets
from factory_v1.errors import IntakeError
from factory_v1.tests import test_restart_resolution as fixtures


class ResolutionCheckpointTests(unittest.TestCase):
    def fixture(self, cls):
        return fixtures.fixture(self, cls)

    def test_known_bug_restart_reads_assignment_not_defect_key_without_writes(self):
        f = self.fixture(fixtures.BugResolutionTests)
        f.member = {'id': 'M13', 'status': 'todo'}
        f.f.item['ticket_work']['frontier'] = {'foreign_active': [], 'active': [], 'items': []}
        f.f.save()
        item = f.f.stored()
        f.f.github.base = 'https://api.github.com'
        before = f.f.stored()
        with patch.object(repositories, 'reconcile', side_effect=lambda db, item, api: item):
            restart.tracker(f.f.database, item, restart.Readback(f.f.github, item['repository']))
        self.assertTrue(item['bug_work']['defect']['publication_complete'])
        self.assertEqual(item['bug_work']['defect']['assignment_id'], 'bug-assignment')
        self.assertEqual(f.f.stored(), before)
        for mutation in f.f.mutations:
            mutation.assert_not_called()

    def test_candidate_late_holds_at_intent_receipt_and_success(self):
        for boundary in ('intent', 'write', 'receipt', 'success'):
            for field, resolving in product(('stuck_disposition', 'recovery_disposition', 'requirement_disposition', 'restart_disposition', 'stop', 'pause'), (False, True)):
                with self.subTest(boundary=boundary, field=field, resolving=resolving):
                    f = self.fixture(fixtures.CandidateResolutionTests)
                    if not resolving:
                        f.item.pop('restart_reconciliation')
                        f.persist()
                    injected = None
                    queries = 0
                    def read(github, path, payload=None, method=None):
                        nonlocal injected, queries
                        result = f.transport(github, path, payload, method)
                        if path.endswith(publication.pr_query(f.f.config)):
                            queries += 1
                        at_boundary = (boundary == 'intent' and path.endswith(publication.pr_query(f.f.config)) or
                                       boundary == 'write' and path.endswith(publication.pr_query(f.f.config)) and queries > 1 or
                                       boundary == 'receipt' and payload is not None or
                                       boundary == 'success' and path.endswith('/git/ref/heads/main') and bool(f.writes))
                        if at_boundary and injected is None:
                            row = json.loads(f.db.execute('SELECT payload FROM role_assignments').fetchone()[0])
                            if field == 'pause':
                                controls.request(f.db, 'p', 'i', 'operator', 'pause', 'fixture:late-hold')
                            elif field == 'stop':
                                from pathlib import Path
                                (Path(row['runtime']['artifacts']) / 'stop').touch()
                            else:
                                row[field] = {'reference': 'fixture:new-hold'}
                                with f.db:
                                    f.db.execute('UPDATE role_assignments SET payload=?', (json.dumps(row),))
                            injected = copy.deepcopy(row)
                        return result
                    with patch.object(publication.GitHub, 'request', read), self.assertRaises(IntakeError):
                        f.publish()
                    self.assertIsNotNone(injected)
                    assert injected is not None
                    stored = json.loads(f.db.execute('SELECT payload FROM role_assignments').fetchone()[0])
                    if field not in {'stop', 'pause'}:
                        self.assertEqual(stored[field], injected[field])
                    self.assertEqual(stored['publication']['status'], 'pending')
                    self.assertEqual(len(f.writes), 0 if boundary in {'intent', 'write'} else 1)
                    expected = copy.deepcopy(injected)
                    if boundary in {'receipt', 'success'}:
                        expected['publication']['pr_number'] = 20
                    self.assertEqual(stored, expected)
                    f.doCleanups()

    def test_ticket_and_bug_late_holds_preserve_fresh_item_and_only_admitted_receipts(self):
        for cls in (fixtures.TicketResolutionTests, fixtures.BugResolutionTests):
            for boundary in ('intent', 'receipt', 'success'):
                for hold in ('restart', 'requirements', 'recovery', 'diagnosis', 'pause'):
                    with self.subTest(cls=cls.__name__, boundary=boundary, hold=hold):
                        f = self.fixture(cls)
                        is_bug = cls is fixtures.BugResolutionTests
                        db = f.f.database
                        original = f.f.membership.side_effect if is_bug else f.f.membership
                        injected = None
                        writes_at_hold = None
                        def read(*args):
                            nonlocal injected, writes_at_hold
                            result = original(*args)
                            # Receipt boundary is the first read after the admitted membership write;
                            # success boundary is the verified final field read.
                            at_boundary = (boundary == 'intent' and result is None or
                                           boundary == 'receipt' and result is not None and f.f.add_item.called or
                                           boundary == 'success' and f.f.set_field.called)
                            if at_boundary and injected is None:
                                item = f.f.stored()
                                if hold == 'pause':
                                    if is_bug:
                                        f.f.pause(None)
                                    else:
                                        f.f.pause()
                                else:
                                    if hold == 'restart':
                                        item['restart_reconciliation']['blockers'].append({'boundary': 'model', 'code': 'operation_uncertain'})
                                    elif hold == 'requirements':
                                        item['requirement_changes'] = {'change': {'kind': 'material', 'status': 'held', 'affected': [10]}}
                                    elif hold == 'recovery':
                                        item['main_incident'] = {'reference': 'fixture:new-recovery'}
                                    else:
                                        item['stuck_work'] = {'diagnosis': {'status': 'held', 'affected': [10]}}
                                    tickets.checkpoint(db, item)
                                injected = f.f.stored()
                                writes_at_hold = [f.f.add_item.call_count, f.f.set_field.call_count, f.f.add_dependency.call_count, f.f.github.request.call_count]
                            return result
                        with patch.object(tickets.gh, 'membership', side_effect=read), self.assertRaises(IntakeError):
                            f.publish()
                        self.assertIsNotNone(injected)
                        self.assertEqual([f.f.add_item.call_count, f.f.set_field.call_count, f.f.add_dependency.call_count, f.f.github.request.call_count], writes_at_hold)
                        stored = f.f.stored()
                        expected = copy.deepcopy(injected)
                        assert expected is not None
                        work = stored['bug_work']['defect'] if is_bug else stored['ticket_work']
                        if boundary == 'success':
                            if is_bug:
                                expected['bug_work']['defect']['observation'] = work['observation']
                                self.assertEqual(work['observation']['issue']['number'], 13)
                            else:
                                expected['ticket_work']['published'] = {'note': {'number': 10, 'id': 1010, 'node_id': 'I10'}}
                        self.assertEqual(stored, expected)
                        self.assertFalse(work.get('publication_complete'))
                        self.assertEqual(f.f.add_item.call_count, 0 if boundary == 'intent' else 1)
                        if boundary == 'intent':
                            self.assertFalse(work.get('membership_intent') if is_bug else work.get('intents', {}).get('note', {}).get('membership'))
                        else:
                            self.assertTrue(work.get('membership_intent') if is_bug else work['intents']['note']['membership'])
                        f.doCleanups()

    def test_published_ticket_final_frontier_read_cannot_erase_new_hold(self):
        f = self.fixture(fixtures.TicketResolutionTests)
        f.f.work.update(publication_complete=True, status='published')
        f.f.persist()
        injected = None
        def read(*args):
            nonlocal injected
            item = f.f.stored()
            item['restart_reconciliation']['blockers'].append({'boundary': 'model', 'code': 'operation_uncertain'})
            tickets.checkpoint(f.f.database, item)
            injected = item
            return []
        with patch.object(tickets.gh, 'project_items', side_effect=read), self.assertRaises(IntakeError):
            f.publish()
        self.assertIsNotNone(injected)
        self.assertEqual(f.f.stored(), injected)
        f.f.add_item.assert_not_called()
        f.f.set_field.assert_not_called()


if __name__ == '__main__':
    unittest.main()
