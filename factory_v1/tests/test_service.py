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

    def test_initialize_rejects_source_root_before_writing(self):
        source = Path(self.temp.name) / 'source'
        source.mkdir(mode=0o700)
        package = source / 'factory_v1'
        package.mkdir(mode=0o700)
        root = source / 'forbidden'
        with patch.object(service, '__file__', str(package / 'service.py')):
            with self.assertRaises(RepositoryError):
                service.initialize(root, 'operator', 'product', 'iteration', 'https://api.github.com')
        self.assertFalse(root.exists())

    def test_healthy_observer_uses_existing_evidence_shape_and_no_agent(self):
        capability = self.root / 'delivery.json'
        service.atomic(capability, {})
        self.config['delivery_config'] = str(capability)
        with patch.object(service.delivery, 'load_config', return_value={}), \
             patch.object(service.delivery, 'sample', return_value={'evidence': {'status': 'healthy'}}) as sample, \
             patch.object(service, 'dispatch') as dispatch, patch('builtins.print') as output:
            result = service.cycle(self.config, Mock())
        self.assertEqual(result['blockers'], ['dispatch_capability_unavailable'])
        sample.assert_called_once()
        dispatch.assert_not_called()
        output.assert_not_called()

    def test_unknown_health_blocks_dispatch_but_delivers_existing_attention(self):
        capability = self.root / 'delivery.json'
        transport = self.root / 'transport.json'
        service.atomic(capability, {})
        service.atomic(transport, {})
        self.config.update(delivery_config=str(capability), transport_config=str(transport), dispatch_config='unavailable')
        with patch.object(service.delivery, 'load_config', return_value={}), \
             patch.object(service.delivery, 'sample', return_value={'evidence': {'status': 'unknown'}}), \
             patch.object(service.attention, 'records', return_value=[{'state': 'pending', 'event': {'event_id': 'incident:1'}}]), \
             patch.object(service.attention, 'deliver') as deliver, patch.object(service, 'dispatch') as dispatch:
            result = service.cycle(self.config, Mock())
        self.assertEqual(result['blockers'], ['delivery_not_healthy'])
        deliver.assert_called_once()
        dispatch.assert_not_called()

    def test_cycle_passes_shared_shutdown_event_to_stage_launcher(self):
        from factory_v1 import service_dispatch
        event = Mock()
        with patch.object(service_dispatch, 'tick', return_value={'status': 'progress'}) as dispatch:
            service.dispatch(self.config, Mock(), stop_event=event)
        self.assertIs(dispatch.call_args.kwargs['stop_event'], event)

    def test_supervisor_refuses_dropins_before_control(self):
        response = Mock(stdout='FragmentPath=/unused/agentic-forge.service\nDropInPaths=/unreviewed/override.conf\n')
        with patch.object(service.subprocess, 'run', return_value=response) as run:
            with self.assertRaises(RepositoryError):
                service.supervisor(self.config, 'start')
        self.assertEqual(run.call_count, 1)

    def test_supervisor_status_reads_actual_active_state_after_exact_unit_check(self):
        unit = self.root / service.UNIT
        unit.write_text(service.unit_text(self.config))
        unit.chmod(0o600)
        responses = [Mock(stdout='FragmentPath=' + str(unit) + '\nDropInPaths=\n'),
                     Mock(stdout='ActiveState=inactive\nSubState=dead\nMainPID=0\n')]
        with patch.object(service.subprocess, 'run', side_effect=responses):
            result = service.supervisor(self.config, 'status')
        self.assertEqual(result, {'ActiveState': 'inactive', 'SubState': 'dead', 'MainPID': '0'})

    def test_status_does_not_report_dispatch_ready_without_capabilities(self):
        result = service.status(self.config)
        self.assertFalse(result['dispatch_allowed'])
        self.assertIn('service_not_started', result['blockers'])
