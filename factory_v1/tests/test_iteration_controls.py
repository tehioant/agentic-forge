"""Current-main integration seams: real sidecar controls, mocked external reads only."""
import copy
import io
import json
import sqlite3
import sys
import unittest
from contextlib import closing, redirect_stdout
from unittest.mock import Mock, patch

from factory_v1 import controls, iteration, planning, restart, skills, tickets
from factory_v1.__main__ import main
from factory_v1.repositories import RepositoryError, state_lock
from factory_v1.tests import test_iteration as fixtures
from factory_v1.tests.test_iteration_regressions import fixture


def interrupt(f, action='pause'):
    """Public control command stays responsive while the ordinary state lock is held."""
    state = f.db.execute('PRAGMA database_list').fetchone()[2]
    args = ['factory_v1', '--state', state, '--operator-id', '42', action,
            '--project', 'product', '--iteration', 'm1', '--reference', 'integration-boundary']
    with patch.object(sys, 'argv', args), redirect_stdout(io.StringIO()):
        main()
    assert controls.blocked(f.db, 'product', 'm1')


class IterationControlTests(unittest.TestCase):
    def refused(self, f, operation):
        with f.db, self.assertRaises(RepositoryError) as caught:
            operation()
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.assertTrue(controls.blocked(f.db, 'product', 'm1'))

    def test_preexisting_pause_and_stop_refuse_completion_before_external_reads(self):
        for action in ('pause', 'emergency-stop'):
            with self.subTest(action=action):
                f = fixture(self, fixtures.IterationComponents)
                before = f.stored()
                with state_lock(f.db):
                    interrupt(f, action)
                    self.refused(f, f.complete)
                self.assertEqual(f.stored(), before)
                self.assertFalse(f.db.execute("SELECT 1 FROM sqlite_master WHERE name='notifications'").fetchone())
                f.mocks['delivery.observe'].assert_not_called()
                f.mocks['iteration.frontier'].assert_not_called()

    def test_pause_on_last_scoped_read_prevents_atomic_report_and_outbox(self):
        f = fixture(self, fixtures.IterationComponents)
        before = f.stored()
        reads = 0
        interruption_injected = False
        def read(*args):
            nonlocal reads, interruption_injected
            reads += 1
            if reads == 2:
                interrupt(f)
                interruption_injected = True
            return {'body': 'accepted', 'state': 'closed'}
        f.mocks['tickets.gh.read_issue'].side_effect = read
        with state_lock(f.db):
            self.refused(f, f.complete)
        self.assertTrue(interruption_injected)
        self.assertEqual(f.stored(), before)
        self.assertFalse(f.db.execute("SELECT 1 FROM sqlite_master WHERE name='notifications'").fetchone())

    def test_frontier_copy_cannot_hide_real_control_before_or_after_read(self):
        for moment in ('before', 'after'):
            with self.subTest(moment=moment):
                f = fixture(self, fixtures.IterationComponents)
                f.stack.close()  # Exercise the real copy wrapper, not completion's mocked frontier.
                before = f.stored()
                def read(db, item, api, *, resolution=None):
                    self.assertIsNone(resolution)
                    self.assertNotEqual(db.execute('PRAGMA database_list').fetchone()[2],
                                        f.db.execute('PRAGMA database_list').fetchone()[2])
                    interrupt(f)
                    return dict(item, ticket_work={'frontier': f.observed})
                if moment == 'before':
                    interrupt(f)
                with patch.object(tickets, 'frontier', side_effect=read) as observe, state_lock(f.db):
                    self.refused(f, lambda: iteration.frontier(f.db, f.item, f.github))
                if moment == 'before':
                    observe.assert_not_called()
                self.assertEqual(f.stored(), before)

    def test_pause_and_stop_prevent_continuation_and_skill_read_race(self):
        for action, moment in (('pause', 'before'), ('emergency-stop', 'before'), ('pause', 'skills')):
            with self.subTest(action=action, moment=moment):
                f = fixture(self, fixtures.ContinuationComponents)
                before = fixtures.context()
                stored = json.loads(f.db.execute('SELECT payload FROM iterations').fetchone()[0])
                resolve = skills.resolve_installed
                def load(request):
                    result = resolve(request)
                    interrupt(f, action)
                    return result
                if moment == 'before':
                    interrupt(f, action)
                with state_lock(f.db), patch.object(skills, 'resolve_installed',
                        side_effect=load if moment == 'skills' else resolve) as selected:
                    self.refused(f, f.start)
                if moment == 'before':
                    selected.assert_not_called()
                self.assertEqual(f.db.execute('SELECT COUNT(*) FROM iterations').fetchone()[0], 1)
                self.assertEqual(json.loads(f.db.execute('SELECT payload FROM iterations').fetchone()[0]), stored)
                self.assertEqual(stored['repository'], before['repository'])

    def test_explicit_resume_preserves_completed_wait_and_then_allows_next_input(self):
        f = fixture(self, fixtures.ContinuationComponents)
        before = json.loads(f.db.execute('SELECT payload FROM iterations').fetchone()[0])
        interrupt(f)
        with patch.object(restart, 'tracker', return_value={}):
            controls.resume(f.db, 'product', 'm1', '42', 'explicit-later-resume', Mock(base='http://127.0.0.1:9876'))
        stored = json.loads(f.db.execute('SELECT payload FROM iterations').fetchone()[0])
        report = stored['restart_reconciliation']
        self.assertEqual(report['status'], 'ready')
        self.assertEqual(report['blockers'], [])
        self.assertEqual(stored, dict(before, restart_reconciliation=report))
        self.assertFalse(controls.blocked(f.db, 'product', 'm1'))
        with state_lock(f.db):
            started = f.start()
        self.assertEqual(started['iteration_id'], 'm2')
        self.assertEqual(started['stage'], 'planning_pending')
        self.assertEqual(started['previous_iteration']['completion'], before['completion'])

    def test_continuation_pause_after_last_publication_read_keeps_interview_pending(self):
        f = fixture(self, fixtures.ContinuationComponents)
        item = f.start()
        request = f.evidence(item)
        request.update(assignment_id=item['planning']['assignment_id'], origin=fixtures.ORIGIN,
                       rationale='Actual answers', reference='agent-sufficient', commit='e' * 40, issue_number=8)
        before = copy.deepcopy(item)
        def read(*args):
            state = f.db.execute('PRAGMA database_list').fetchone()[2]
            with closing(sqlite3.connect(state)) as other:
                controls.request(other, 'product', 'm2', '42', 'pause', 'last-publication-read')
            return {'commit': 'e' * 40, 'issue': {'number': 8, 'id': 80}}
        with patch.object(planning, 'read_publication', side_effect=read), state_lock(f.db):
            with f.db, self.assertRaises(RepositoryError) as caught:
                planning.complete(f.db, item, request, Mock())
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.assertTrue(controls.blocked(f.db, 'product', 'm2'))
        self.assertEqual(json.loads(f.db.execute("SELECT payload FROM iterations WHERE iteration_id='m2'").fetchone()[0]), before)
