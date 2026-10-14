"""Public scheduler tick with direct durable records and mocked precursor reads."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from factory_v1 import controls, restart, service, service_dispatch as dispatch
from factory_v1.repositories import RepositoryError


class DispatchComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'controller'
        self.service = service.initialize(self.root, 'operator', 'p', 'i', 'https://api.github.com')
        self.item = {'project_id': 'p', 'iteration_id': 'i', 'repository': 'owner/repo', 'status': 'active',
                     'execution_allowed': False, 'approval': {'operator_id': 'operator', 'reference': 'intake:1'},
                     'planning': {'status': 'complete'}, 'ticket_work': {'publication_complete': True, 'status': 'published'}}
        self.seed = {'assignment_id': 'a' * 64, 'claim_id': 'seed',
                     'ticket_scope': {'project': 'p', 'iteration': 'i'},
                     'handoff': {'stage': 'implementation', 'skills': [],
                                 'workspace': str(Path(self.temp.name) / 'workspace'),
                                 'profile': {'home': str(Path(self.temp.name) / 'dedicated-role')},
                                 'adaptation': dispatch.role_skills.ADAPTATIONS['implementation']}}
        self.config = {'operator_id': 'operator', 'project': 'p', 'iteration': 'i', 'reference': 'host:1',
                       'implementation': {'stage': 'implementation', 'claim_id': 'seed'}, 'roles': {}, 'launchers': {},
                       'tickets': None, 'synthesis_config': None, 'review_config': None, 'publication_config': None, 'merge_config': None}
        self.path = self.root / 'dispatch.json'
        self.service['dispatch_config'] = str(self.path)
        self.persist()

    def persist(self):
        service.atomic(self.path, self.config)
        with closing(sqlite3.connect(self.service['state'])) as db, db:
            db.execute('INSERT OR REPLACE INTO iterations VALUES (?, ?, ?)', ('p', 'i', json.dumps(self.item)))
            db.execute('CREATE TABLE IF NOT EXISTS role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO role_assignments VALUES (?, ?)', (self.seed['assignment_id'], json.dumps(self.seed)))

    def test_paused_scope_never_reaches_dispatch_or_precursor_reads(self):
        with service.connect(self.service) as db:
            controls.request(db, 'p', 'i', 'operator', 'pause', 'pause:1')
        with patch.object(restart, 'before_dispatch') as preflight, patch.object(dispatch.sandbox, 'launch') as launch:
            with self.assertRaises(RepositoryError) as error:
                dispatch.tick(self.service, Mock())
        self.assertEqual(error.exception.code, 'iteration_paused')
        preflight.assert_not_called()
        launch.assert_not_called()

    def test_final_reconciliation_pause_blocks_new_stage(self):
        def pause(db, *args, **kwargs):
            controls.request(db, 'p', 'i', 'operator', 'pause', 'late-pause:1')
        with patch.object(restart, 'before_dispatch', side_effect=pause), \
             patch.object(dispatch.sandbox, 'launch') as launch, patch.object(dispatch, 'installed') as installed:
            with self.assertRaises(RepositoryError) as error:
                dispatch.tick(self.service, Mock())
        self.assertEqual(error.exception.code, 'iteration_paused')
        launch.assert_not_called()
        installed.assert_not_called()

    def test_prepared_assignment_calls_existing_launcher_with_shutdown_event(self):
        launcher = self.root / 'launcher.json'
        service.atomic(launcher, {})
        self.config['launchers']['implementation'] = str(launcher)
        self.persist()
        event = threading.Event()
        github = Mock()
        with patch.object(restart, 'before_dispatch') as before, \
             patch.object(dispatch.assignments, 'inspect', return_value=self.seed), \
             patch.object(dispatch, 'installed'), patch.object(dispatch.sandbox, 'launch') as launch:
            result = dispatch.tick(self.service, github, stop_event=event)
        self.assertEqual(result['stage'], 'launch')
        self.assertEqual(result['blockers'], ['attempt_requires_reconciliation'])
        before.assert_called_once()
        launch.assert_called_once_with(self.service['state'], 'p', 'i', 'operator', self.seed['assignment_id'],
                                       str(launcher), github, stop_event=event)

    def test_missing_launcher_refuses_instead_of_guessing_capacity(self):
        with patch.object(restart, 'before_dispatch'), patch.object(dispatch.assignments, 'inspect', return_value=self.seed), \
             patch.object(dispatch, 'installed'), patch.object(dispatch.sandbox, 'launch') as launch:
            result = dispatch.tick(self.service, Mock())
        self.assertEqual(result['blockers'], ['implementation_launcher_unavailable'])
        launch.assert_not_called()

    def test_completed_worker_claim_still_calls_existing_simplification_guard(self):
        self.seed.update(runtime={'status': 'complete', 'artifacts': str(Path(self.temp.name) / 'artifacts')}, submitted_result={'status': 'done'})
        self.config['roles']['simplify'] = {'profile': {}, 'skills': [], 'claim_id': 'simplify'}
        self.persist()
        with patch.object(restart, 'before_dispatch'), patch.object(dispatch, 'installed'), \
             patch.object(dispatch.simplification, 'prepare', side_effect=RepositoryError('implementation_unverified', 'missing checks')) as prepare, \
             patch.object(dispatch.sandbox, 'launch') as launch:
            with self.assertRaises(RepositoryError) as error:
                dispatch.tick(self.service, Mock())
        self.assertEqual(error.exception.code, 'implementation_unverified')
        self.assertEqual(prepare.call_args.args[4]['implementation_assignment'], self.seed['assignment_id'])
        launch.assert_not_called()

    def test_publication_seam_owns_its_lock_without_nested_service_lock(self):
        self.config['publication_config'] = str(self.root / 'publication.json')
        self.persist()
        axes = {'standards_assignment': 'b' * 64, 'spec_assignment': 'c' * 64}
        axis = {'handoff': {'review': {'pins': {'candidate_assignment': 'd' * 64}}}}
        candidate = {'assignment_id': 'd' * 64}
        def publish(db, *args):
            with controls.quiescent_lock(db) as quiet:
                self.assertTrue(quiet, 'publisher must acquire its own lock without deadlock')
        with patch.object(dispatch, 'select', return_value=('evaluate', axes)), \
             patch.object(restart, 'before_dispatch'), patch.object(dispatch, 'evaluate', return_value={'status': 'review-accepted'}), \
             patch.object(dispatch.assignments, 'inspect', side_effect=[axis, candidate]), \
             patch.object(dispatch.publication, 'publish', side_effect=publish) as publish:
            dispatch.tick(self.service, Mock())
        publish.assert_called_once()

    def test_merge_authority_cannot_target_a_different_reviewed_candidate(self):
        self.config['merge_config'] = str(self.root / 'merge.json')
        self.persist()
        axes = {'standards_assignment': 'b' * 64, 'spec_assignment': 'c' * 64}
        axis = {'handoff': {'review': {'pins': {'candidate_assignment': 'd' * 64}}}}
        candidate = {'assignment_id': 'd' * 64, 'publication': {'status': 'published'}}
        with patch.object(dispatch, 'select', return_value=('evaluate', axes)), \
             patch.object(restart, 'before_dispatch'), patch.object(dispatch, 'evaluate', return_value={'status': 'review-accepted'}), \
             patch.object(dispatch.assignments, 'inspect', side_effect=[axis, candidate]), \
             patch.object(dispatch.merge, 'load_config', return_value={'candidate_assignment': 'e' * 64}), \
             patch.object(dispatch.merge, 'lifecycle') as merge:
            with self.assertRaises(RepositoryError) as error:
                dispatch.tick(self.service, Mock())
        self.assertEqual(error.exception.code, 'scope_mismatch')
        merge.assert_not_called()

    def test_stage_change_during_reconciliation_cannot_use_stale_selection(self):
        def change(db, *args, **kwargs):
            item = deepcopy(self.item)
            item['planning']['status'] = 'pending'
            db.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
            db.commit()
        with patch.object(restart, 'before_dispatch', side_effect=change), \
             patch.object(dispatch.sandbox, 'launch') as launch:
            result = dispatch.tick(self.service, Mock())
        self.assertEqual(result['blockers'], ['stage_changed_during_reconciliation'])
        launch.assert_not_called()

    def test_shutdown_before_tick_never_reads_or_dispatches(self):
        event = threading.Event()
        event.set()
        with patch.object(dispatch.assignments, 'current') as read:
            result = dispatch.tick(self.service, Mock(), stop_event=event)
        self.assertEqual(result['blockers'], ['service_stopping'])
        read.assert_not_called()
