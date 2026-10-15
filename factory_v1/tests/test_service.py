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

    def test_startup_pause_and_stop_persist_matrix(self):
        db = self.database()
        for action in (None, 'emergency-stop'):
            with self.subTest(action=action):
                if action:
                    controls.request(db, 'product', 'iteration', 'operator', action, 'stop:1')
                def reconcile(observed, *args):
                    self.assertTrue(controls.blocked(observed, 'product', 'iteration'))
                    return {'status': 'ready', 'observations': {}, 'blockers': []}
                with patch.object(service.restart, 'reconcile', side_effect=reconcile) as observed:
                    service.startup(self.config, Mock())
                observed.assert_called_once()
                with closing(sqlite3.connect(self.config['state'])) as fresh:
                    self.assertTrue(controls.blocked(fresh, 'product', 'iteration'))
                    if action:
                        record = controls.read(fresh, 'product', 'iteration')
                        self.assertIsNotNone(record)
                        assert record is not None
                        self.assertEqual(record['action'], action)

    def test_paused_cycle_is_silent_matrix(self):
        db = self.database()
        controls.request(db, 'product', 'iteration', 'operator', 'pause', 'pause:1')
        with patch.object(service, 'dispatch') as dispatch, patch('builtins.print') as output:
            result = service.cycle(self.config, Mock())
        self.assertEqual(result['status'], 'paused')
        dispatch.assert_not_called()
        output.assert_not_called()

    def test_fixed_unit_and_dropin_status_matrix(self):
        target = Path(self.temp.name) / 'units'
        target.mkdir(mode=0o700)
        with patch.object(service.subprocess, 'run') as run:
            unit = service.install(self.config, target)
        text = unit.read_text()
        for directive in ('KillMode=control-group', 'Restart=on-failure', '-m factory_v1.service'):
            self.assertIn(directive, text)
        run.assert_called_once_with(['systemctl', '--user', 'daemon-reload'], check=True, timeout=30)
        cases = [
            ('dropin', 'start', [Mock(stdout='FragmentPath=/unused/agentic-forge.service\nDropInPaths=/unreviewed/override.conf\n')], None),
            ('actual-status', 'status', [Mock(stdout='FragmentPath=' + str(unit) + '\nDropInPaths=\n'),
                                       Mock(stdout='ActiveState=inactive\nSubState=dead\nMainPID=0\n')],
             {'ActiveState': 'inactive', 'SubState': 'dead', 'MainPID': '0'}),
        ]
        for label, command, responses, expected in cases:
            with self.subTest(case=label), patch.object(service.subprocess, 'run', side_effect=responses) as run:
                if expected is None:
                    with self.assertRaises(RepositoryError):
                        service.supervisor(self.config, command)
                    self.assertEqual(run.call_count, 1)
                else:
                    self.assertEqual(service.supervisor(self.config, command), expected)

    def test_private_durable_state_and_initialization_matrix(self):
        source = Path(self.temp.name) / 'source'
        source.mkdir(mode=0o700)
        package = source / 'factory_v1'
        package.mkdir(mode=0o700)
        forbidden = source / 'forbidden'
        path = self.root / 'service.json'
        os.chmod(path, 0o644)
        for label, seam, args in [
            ('nonprivate', service.load, (path,)),
            ('source-root', service.initialize, (forbidden, 'operator', 'product', 'iteration', 'https://api.github.com')),
        ]:
            with self.subTest(case=label), patch.object(service, '__file__', str(package / 'service.py')):
                with self.assertRaises(RepositoryError):
                    seam(*args)
        self.assertFalse(forbidden.exists())

    def test_observer_health_attention_and_shutdown_matrix(self):
        capability, transport = self.root / 'delivery.json', self.root / 'transport.json'
        service.atomic(capability, {})
        service.atomic(transport, {})
        baseline = dict(self.config)
        original_dispatch = service.dispatch
        for health, attention, dispatch_config, blockers in [
            ('healthy', [], None, ['dispatch_capability_unavailable']),
            ('unknown', [{'state': 'pending', 'event': {'event_id': 'incident:1'}}], 'unavailable', ['delivery_not_healthy']),
            (None, [], '/private/config.json', None),
        ]:
            with self.subTest(health=health):
                config = baseline | {'dispatch_config': dispatch_config}
                if health:
                    config['delivery_config'] = str(capability)
                if attention:
                    config['transport_config'] = str(transport)
                event = Mock()
                with patch.object(service.delivery, 'load_config', return_value={}), \
                     patch.object(service.delivery, 'sample', return_value={'evidence': {'status': health}}) as sample, \
                     patch.object(service.attention, 'records', return_value=attention), \
                     patch.object(service.attention, 'deliver') as deliver, \
                     patch.object(service, 'inspect_readiness', return_value={'iteration_runnable': True}), \
                     patch.object(service, 'dispatch', return_value={'status': 'progress'}) as dispatch, \
                     patch('builtins.print') as output:
                    if health is None:
                        from factory_v1 import service_dispatch
                        with patch.object(service_dispatch, 'tick', return_value={'status': 'progress'}) as tick:
                            # Public dispatch seam independently of cycle health/readiness admission.
                            service_dispatch_result = original_dispatch(config, Mock(), stop_event=event)
                        self.assertIs(tick.call_args.kwargs['stop_event'], event)
                        result = service_dispatch_result
                    else:
                        result = service.cycle(config, Mock(), stop_event=event)
                if blockers is None:
                    dispatch.assert_not_called()
                    sample.assert_not_called()
                else:
                    self.assertEqual(result['blockers'], blockers)
                    sample.assert_called_once()
                    dispatch.assert_not_called()
                if attention:
                    deliver.assert_called_once()
                else:
                    deliver.assert_not_called()
                output.assert_not_called()

    def test_status_requires_capabilities_matrix(self):
        result = service.status(self.config)
        self.assertFalse(result['dispatch_allowed'])
        self.assertIn('service_not_started', result['blockers'])
