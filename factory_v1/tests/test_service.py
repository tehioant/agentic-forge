"""Focused service checks: direct records; no supervisor, worker or network runs."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from factory_v1 import controls, service
from factory_v1.repositories import RepositoryError


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'isolated'
        self.config = service.initialize(self.root, 'operator', 'product', 'iteration', 'https://api.github.com')
        self.item = {'project_id': 'product', 'iteration_id': 'iteration', 'repository': 'owner/repo',
                     'status': 'active', 'execution_allowed': False,
                     'approval': {'operator_id': 'operator', 'reference': 'intake:1'}}
        with closing(sqlite3.connect(self.config['state'])) as db, db:
            db.execute('INSERT INTO iterations VALUES (?, ?, ?)', ('product', 'iteration', json.dumps(self.item)))

    def database(self):
        db = sqlite3.connect(self.config['state'])
        self.addCleanup(db.close)
        return db

    def test_startup_persists_pause_before_reconciliation(self):
        def reconcile(db, *args):
            self.assertTrue(controls.blocked(db, 'product', 'iteration'))
            return {'status': 'ready', 'observations': {}, 'blockers': []}
        with patch.object(service.restart, 'reconcile', side_effect=reconcile) as observed:
            service.startup(self.config, Mock())
        observed.assert_called_once()
        with closing(sqlite3.connect(self.config['state'])) as db, db:
            self.assertTrue(controls.blocked(db, 'product', 'iteration'))

    def test_restart_never_clears_emergency_hold(self):
        db = self.database()
        controls.request(db, 'product', 'iteration', 'operator', 'emergency-stop', 'stop:1')
        with patch.object(service.restart, 'reconcile', return_value={'status': 'ready'}):
            service.startup(self.config, Mock())
        self.assertEqual(controls.read(db, 'product', 'iteration')['action'], 'emergency-stop')

    def test_cycle_is_silent_and_never_dispatches_while_paused(self):
        db = self.database()
        controls.request(db, 'product', 'iteration', 'operator', 'pause', 'pause:1')
        with patch.object(service, 'dispatch') as dispatch, patch('builtins.print') as output:
            result = service.cycle(self.config, Mock())
        self.assertEqual(result['status'], 'paused')
        dispatch.assert_not_called()
        output.assert_not_called()

    def test_install_renders_fixed_supervisor_without_start_or_enable(self):
        target = Path(self.temp.name) / 'units'
        target.mkdir(mode=0o700)
        with patch.object(service.subprocess, 'run') as run:
            unit = service.install(self.config, target)
        self.assertIn('KillMode=control-group', unit.read_text())
        self.assertIn('Restart=on-failure', unit.read_text())
        self.assertIn('-m factory_v1.service', unit.read_text())
        run.assert_called_once_with(['systemctl', '--user', 'daemon-reload'], check=True, timeout=30)

    def test_config_rejects_ephemeral_or_nonprivate_state(self):
        path = self.root / 'service.json'
        os.chmod(path, 0o644)
        with self.assertRaises(RepositoryError):
            service.load(path)

    def test_status_does_not_report_dispatch_ready_without_capabilities(self):
        result = service.status(self.config)
        self.assertFalse(result['dispatch_allowed'])
        self.assertIn('service_not_started', result['blockers'])
