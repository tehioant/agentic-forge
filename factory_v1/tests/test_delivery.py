"""Delivery validators and atomic state checks with mocked readback boundaries."""
import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from collections.abc import Callable

from factory_v1 import assignments, delivery


def configuration(base, repository='example/product', repository_id=7, issue=18):
    return {'operator_id': '42', 'repository': repository, 'repository_id': repository_id,
            'api_base': base, 'issue': issue, 'required_checks': [{'context': 'quality', 'app_id': 99}],
            'required_workflows': [{'id': 11, 'event': 'push'}], 'environment': 'test', 'timeout': 2,
            'contract': {'reference': 'Explicit test-only acceptance agreement; NOT production approval', 'revision': 'a' * 40,
                         'health_url': base + '/health', 'acceptance': {'journey': True}}}


class DeliveryTarget:
    def __init__(self, config, sha='a' * 40):
        self.config = config
        self.sha = sha
        self.check_sha = sha
        self.deploy_sha = sha
        self.workflow_sha = sha
        self.check_conclusion = 'success'
        self.workflow_conclusion = 'success'
        self.deployment_state = 'success'
        self.healthy = True
        self.health_reads = 0
        self.health_outage = False
        self.github_outage = False
        self.after_health: Callable[[], None] | None = None
        self.responses = {}
        self.http_status: int | None = None
        self.calls = []

    def value(self, path):
        self.calls.append(path)
        suffix = path.removeprefix('/repos/' + self.config['repository'])
        if path == '/health':
            self.health_reads += 1
            if self.after_health:
                self.after_health()
            return {'repository': self.config['repository'], 'sha': self.sha, 'deployment_id': 21,
                    'environment': self.config['environment'], 'contract_sha256': assignments.digest(self.config['contract']),
                    'acceptance': {'journey': self.healthy}}
        if suffix == '':
            return {'id': self.config['repository_id'], 'full_name': self.config['repository'], 'default_branch': 'main'}
        if suffix == '/git/ref/heads/main':
            return {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': self.sha}}
        if '/check-runs?' in suffix:
            return {'total_count': 1, 'check_runs': [{'id': 31, 'name': 'quality', 'head_sha': self.check_sha,
                    'app': {'id': 99}, 'status': 'completed', 'conclusion': self.check_conclusion}]}
        if suffix.startswith('/actions/workflows/'):
            return {'total_count': 1, 'workflow_runs': [{'id': 41, 'workflow_id': 11, 'head_branch': 'main',
                    'repository': {'id': self.config['repository_id'], 'full_name': self.config['repository']},
                    'head_sha': self.workflow_sha, 'status': 'completed', 'conclusion': self.workflow_conclusion, 'event': 'push'}]}
        if suffix.startswith('/deployments?'):
            return [{'id': 21, 'sha': self.deploy_sha, 'ref': 'main', 'environment': self.config['environment']}]
        if suffix.startswith('/deployments/21/statuses?'):
            return [{'id': 51, 'state': self.deployment_state}]
        raise KeyError(path)

    def attach(self, server, extra_only=False):
        target = self
        parent = server.RequestHandlerClass

        class Handler(parent):
            def do_GET(self):
                if extra_only and self.path != '/health' and not any(part in self.path for part in ('/actions/workflows/', '/deployments')):
                    super().do_GET()
                    return
                if target.github_outage and self.path != '/health' or target.health_outage and self.path == '/health':
                    self.send_response(503)
                    self.end_headers()
                    return
                if target.http_status and self.path == '/health':
                    self.send_response(target.http_status)
                    self.send_header('Location', target.config['contract']['health_url'])
                    self.end_headers()
                    return
                if self.path in target.responses:
                    payload = target.responses[self.path]
                else:
                    try:
                        payload = json.dumps(target.value(self.path)).encode()
                    except KeyError:
                        super().do_GET()
                        return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server.RequestHandlerClass = Handler



# DeliveryTarget.attach remains compatible with legacy consumers; these tests use value only.
from unittest import mock
from factory_v1 import attention
from factory_v1.repositories import state_lock


class DeliveryComponentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / 'state.sqlite'
        self.config: dict = configuration('http://127.0.0.1:8765')
        self.target = DeliveryTarget(self.config)
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product',
                     'approval': {'operator_id': '42', 'reference': 'explicit component approval'},
                     'origin': {'platform': 'discord', 'chat_id': '123', 'thread_id': '123', 'parent_chat_id': '122', 'scope_id': '121'},
                     'execution_allowed': False, 'status': 'active',
                     'correlation': {'project_id': 'product', 'iteration_id': 'm1', 'stage': 'intake_registered',
                                     'control_run_id': 'control-1', 'evidence_id': 'intake-1', 'worker_run_id': None}}
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT)')
            db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(self.item)))
        self.config_path = self.root / 'delivery.json'
        self.write_config()
        for name in ('socket.socket', 'subprocess.Popen'):
            patcher = mock.patch(name, side_effect=AssertionError('external creation denied'))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.real_http_json = delivery.http_json
        patcher = mock.patch.object(delivery, 'http_json', side_effect=self.readback)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)

    def readback(self, url, timeout):
        path = url.removeprefix(self.config['api_base'])
        if self.target.github_outage and path != '/health' or self.target.health_outage and path == '/health':
            raise delivery.RepositoryError('delivery_unavailable', 'mock read unavailable')
        if path == '/health' and self.target.http_status:
            raise delivery.RepositoryError('delivery_unknown', 'non-200 readback')
        raw = self.target.responses.get(path)
        if raw is None:
            callback = self.target.after_health if path == '/health' else None
            if callback:
                self.target.after_health = None
            try:
                raw = json.dumps(self.target.value(path), ensure_ascii=False).encode()
            finally:
                if callback:
                    self.target.after_health = callback
            if callback:
                callback()
        return delivery.decode(raw)

    def sample(self):
        with closing(sqlite3.connect(self.state)) as db, state_lock(db):
            return delivery.sample(db, 'product', 'm1', '42', self.config)

    def inspect(self):
        with closing(sqlite3.connect(self.state)) as db:
            return delivery.inspect(db, 'product', 'm1', '42')

    def stored(self):
        with closing(sqlite3.connect(self.state)) as db:
            return delivery.context(db, 'product', 'm1', '42')

    def notifications(self):
        with closing(sqlite3.connect(self.state)) as db:
            return attention.records(db, 'product', 'm1', self.stored())

    def rows(self, table):
        with closing(sqlite3.connect(self.state)) as db:
            return db.execute('SELECT * FROM ' + table).fetchall()

    def test_explicit_non_deployment_acceptance_and_persistence_matrix(self):
        self.config.update(deployment_applicability='not-applicable', environment=None)
        self.config['contract'].update(health_url=None, acceptance={'cli-acceptance': 11})
        self.assertEqual(delivery.validate_config(self.config, '42'), self.config)
        for change in ({'deployment_applicability': 'unknown'}, {'deployment_applicability': []}, {'environment': 'invented'},
                       {'contract': {**self.config['contract'], 'health_url': 'https://example.com/health'}},
                       {'contract': {**self.config['contract'], 'acceptance': {'not-verified': True}}},
                       {'contract': {**self.config['contract'], 'acceptance': {'not-required': 12}}}):
            with self.subTest(change=change), self.assertRaises(delivery.RepositoryError):
                delivery.validate_config({**self.config, **change}, '42')
        sampled = self.sample()
        self.assertEqual(sampled['evidence']['status'], 'healthy')
        self.assertEqual(sampled['evidence']['deployment']['applicability'], 'not-applicable')
        self.assertEqual(self.inspect()['monitor']['config'], self.config)
        from factory_v1 import service
        self.write_config()
        scoped_service = {'state': str(self.state), 'project': 'product', 'iteration': 'm1', 'operator_id': '42',
                          'delivery_config': str(self.config_path), 'dispatch_config': '/private/dispatch', 'transport_config': None}
        with mock.patch.object(service, 'inspect_readiness', return_value={'iteration_runnable': True}), \
             mock.patch.object(service, 'dispatch', return_value={'status': 'component-dispatch'}) as dispatch, \
             mock.patch('factory_v1.merge.controller_paths'):
            self.assertEqual(service.cycle(scoped_service, mock.Mock())['status'], 'component-dispatch')
            dispatch.assert_called_once()
        self.assertFalse(any('/deployments' in path or path == '/health' for path in self.target.calls))
        for field in ('checks', 'workflows', 'deployment', 'health'):
            forged = copy.deepcopy(sampled['evidence'])
            forged[field] = [] if field in ('checks', 'workflows') else None
            with self.subTest(tamper=field), self.assertRaises(delivery.RepositoryError):
                delivery.validate_evidence(json.dumps(forged), assignments.digest(forged), self.config)
        for field in ('check_conclusion', 'workflow_conclusion'):
            setattr(self.target, field, 'failure')
            self.assertEqual(delivery.observe(self.config)['status'], 'failed')
            setattr(self.target, field, 'success')
        self.target.github_outage = True
        self.assertEqual(delivery.observe(self.config)['status'], 'unknown')
        self.target.github_outage = False
        self.config = configuration('http://127.0.0.1:8765')
        self.target.health_outage = True
        self.assertEqual(delivery.observe(self.config)['status'], 'unknown')
        with self.assertRaises(delivery.RepositoryError) as refused:
            self.sample()
        self.assertEqual(refused.exception.code, 'delivery_conflict')

    def test_incident_lifecycle_and_exact_clearance_matrix(self):
        from factory_v1 import __main__ as cli
        from factory_v1.errors import IntakeError
        for samples, interval in (('0', '1'), ('1', 'nan'), ('1', '0'), ('10001', '1')):
            with mock.patch.object(sys, 'argv', ['factory', '--state', str(self.state), '--operator-id', '42',
                'watch-delivery', '--project', 'product', '--iteration', 'm1', '--delivery-config', str(self.config_path),
                '--samples', samples, '--interval', interval]), self.assertRaises(IntakeError):
                cli.main()
        self.assertEqual(self.sample()['evidence']['status'], 'healthy')
        self.assertEqual(self.notifications(), [])
        self.target.healthy = False
        failed = self.sample()
        incident = failed['incident']['incident_id']
        self.assertEqual(failed['evidence']['failure'], 'delivery_unhealthy')
        self.assertEqual(failed['evidence']['status'], 'failed')
        self.assertEqual(self.stored()['main_incident']['incident_id'], incident)
        self.assertEqual(self.sample(), failed)
        self.assertEqual(len(self.inspect()['observations']), 2)
        record = self.notifications()[0]
        self.assertEqual(record['event']['evidence_ids'], [failed['monitor']['latest']['evidence_id']])
        self.assertEqual(record['destination'], self.item['origin'])
        self.target.github_outage = True
        unknown = self.sample()
        self.assertEqual(unknown['evidence']['status'], 'unknown')
        self.assertEqual(unknown['incident']['incident_id'], incident)
        self.target.github_outage = False
        self.target.healthy = True
        for field in ('check_sha', 'workflow_sha', 'deploy_sha'):
            setattr(self.target, field, 'b' * 40)
            self.assertEqual(self.sample()['incident']['incident_id'], incident)
            setattr(self.target, field, self.target.sha)
        self.target.health_outage = True
        self.assertEqual(self.sample()['incident']['incident_id'], incident)
        self.target.health_outage = False
        cleared = self.sample()
        self.assertIsNone(cleared['incident'])
        self.assertNotIn('main_incident', self.stored())
        old = self.inspect()['incidents'][0]['record']
        self.assertEqual(old['status'], 'cleared')
        self.assertEqual(old['clearance_evidence'], cleared['monitor']['latest']['evidence_id'])
        self.assertEqual(len(self.notifications()), 1)
        self.target.healthy = False
        self.assertNotEqual(self.sample()['incident']['incident_id'], incident)
        self.assertEqual(len(self.notifications()), 2)
        self.assertFalse(self.stored()['execution_allowed'])

    def test_failed_unknown_and_late_health_samples_matrix(self):
        cases = [('observation', field, value) for field, values in (
            ('check_conclusion', ('failure', 'cancelled', 'skipped', None)),
            ('workflow_conclusion', ('failure', 'cancelled', None)),
            ('deployment_state', ('failure', 'error', 'inactive', 'pending'))) for value in values]
        cases += [('late-read', field, 'b' * 40 if 'sha' in field else 'failure') for field in (
            'sha', 'deploy_sha', 'check_conclusion', 'workflow_conclusion', 'deployment_state')]
        for phase, field, value in cases:
            with self.subTest(phase=phase, field=field, value=value):
                original = getattr(self.target, field)
                if phase == 'late-read':
                    self.target.after_health = lambda field=field, value=value: setattr(self.target, field, value)
                else:
                    setattr(self.target, field, value)
                try:
                    evidence = delivery.observe(self.config)
                    self.assertNotEqual(evidence['status'], 'healthy')
                    self.assertIsNotNone(evidence['failure'])
                    if phase == 'late-read':
                        self.assertEqual(evidence['status'], 'unknown')
                        self.assertEqual(evidence['failure'], 'delivery_race')
                finally:
                    self.target.after_health = None
                    setattr(self.target, field, original)
        evidence = delivery.observe(self.config)
        self.assertEqual(evidence['status'], 'healthy')
        evidence['checks'] = []
        evidence_id = assignments.digest(evidence)
        with self.assertRaises(delivery.RepositoryError) as raised:
            delivery.validate_evidence(json.dumps(evidence), evidence_id, self.config)
        self.assertIn('incomplete', str(raised.exception))
        self.target.after_health = lambda: setattr(self.target, 'sha', 'b' * 40)
        self.assertEqual(delivery.observe(self.config)['failure'], 'delivery_race')
        self.target.sha = 'a' * 40
        import inspect
        source = inspect.getsource(delivery.observe)
        source = source.replace("require(all(api.read(suffix) == value for suffix, value in reads.items()) and\n                api.read('/git/ref/heads/main') == main,", 'require(True,')
        namespace = dict(vars(delivery))
        exec(compile(source, '<delivery-race-guard-probe>', 'exec'), namespace)
        broken = namespace['observe'](self.config)
        self.assertEqual(broken['status'], 'healthy')
        self.assertEqual(self.target.sha, 'b' * 40)

    def test_concurrent_sample_one_incident_matrix(self):
        self.target.github_outage = True
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(self.sample) for _ in range(3)]
            results = [future.result(timeout=3) for future in futures]
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.notifications()), 1)
        self.assertEqual(len(self.inspect()['observations']), 1)



    def test_wire_json_and_bounded_evidence_matrix(self):
        paths = ['/repos/example/product', '/repos/example/product/git/ref/heads/main',
                 '/repos/example/product/commits/' + self.target.sha + '/check-runs?per_page=100&filter=latest', '/health']
        for path in paths:
            for raw in (b'null', b'[]', b'{"id":1,"id":7}', b'[' * 1200 + b'0' + b']' * 1199,
                        b'{' + b' ' * delivery.LIMIT + b'}', b'{"nested":[1e309]}', b'{"nested":["\\ud800"]}'):
                self.target.responses[path] = raw
                self.assertNotEqual(delivery.observe(self.config)['status'], 'healthy')
                self.target.responses.clear()
        check_path = paths[2]
        value = self.target.value(check_path)
        value['total_count'] = 101
        self.target.responses[check_path] = json.dumps(value).encode()
        self.assertEqual(delivery.observe(self.config)['failure'], 'delivery_unknown')
        self.target.responses.clear()
        for raw in (b'{"nested":[1e309]}', b'{"nested":["\\ud800"]}', b'{"x":NaN}', b'{"x":1,"x":2}'):
            with self.assertRaises(delivery.RepositoryError):
                delivery.decode(raw)
        self.assertEqual(delivery.observe(self.config)['status'], 'healthy')
        initial_state = self.state.read_bytes()
        for field in ('check_conclusion', 'workflow_conclusion', 'deploy_sha', 'deployment_state'):
            self.state.write_bytes(initial_state)
            original = getattr(self.target, field)
            value = '😀' * 200000
            setattr(self.target, field, value)
            unknown = self.sample()
            self.assertEqual(unknown['evidence']['status'], 'unknown')
            self.assertNotIn(value, json.dumps(unknown, ensure_ascii=False))
            self.assertEqual(self.inspect()['monitor'], unknown['monitor'])
            for row in self.rows('delivery_observations'):
                self.assertLessEqual(len(row[-1].encode()), delivery.LIMIT)
            setattr(self.target, field, original)
            self.assertIsNone(self.sample()['incident'])
        opener = mock.Mock()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"ok":true}'
        opener.open.return_value = response
        with mock.patch.object(delivery.urllib.request, 'build_opener', return_value=opener):
            for status in (302, 401, 500):
                response.status = status
                with self.assertRaises(delivery.RepositoryError) as raised:
                    self.real_http_json(self.config['contract']['health_url'], 2)
                self.assertEqual(raised.exception.code, 'delivery_unknown')
            response.status = 200
            self.assertEqual(self.real_http_json(self.config['contract']['health_url'], 2), {'ok': True})
            response.read.assert_called_with(delivery.LIMIT + 1)
            response.read.return_value = b'{"ok":true,"ok":false}'
            with self.assertRaises(delivery.RepositoryError):
                self.real_http_json(self.config['contract']['health_url'], 2)
            opener.open.side_effect = OSError('unreachable')
            with self.assertRaises(delivery.RepositoryError) as raised:
                self.real_http_json(self.config['contract']['health_url'], 2)
            self.assertEqual(raised.exception.code, 'delivery_unavailable')

    def test_persisted_evidence_and_private_config_guards_matrix(self):
        original = copy.deepcopy(self.config)
        assert isinstance(original, dict) and isinstance(original['contract'], dict)
        cases = [None, [], dict(original, required_checks=[]), dict(original, required_workflows=[]),
                 dict(original, repository_id=True), dict(original, timeout=float('nan')),
                 dict(original, api_base='https://example.com'), dict(original, issue=0),
                 dict(original, contract=dict(original['contract'], revision='not-a-commit')),
                 dict(original, contract=dict(original['contract'], health_url='http://example.com/health')),
                 dict(original, contract=dict(original['contract'], acceptance={}))]
        for config in cases:
            with self.assertRaises(delivery.RepositoryError):
                delivery.validate_config(config, '42')
        for raw in (b'{"operator_id":"42","operator_id":"42"}', b'[' * 1200 + b'0' + b']' * 1199,
                    b' ' * (delivery.LIMIT + 1), b'null'):
            self.config_path.write_bytes(raw)
            with self.assertRaises(delivery.RepositoryError):
                delivery.load_config(self.config_path, '42')
        self.write_config()
        self.config_path.chmod(0o644)
        with self.assertRaises(delivery.RepositoryError):
            delivery.load_config(self.config_path, '42')
        self.target.calls.clear()
        api = delivery.DeliveryGitHub(original)
        for suffix in ('/issues/18', '/git/refs', '/actions/runs/1/rerun', '/deployments?environment=other', '/../../user', 'https://example.com'):
            with self.assertRaises(delivery.RepositoryError):
                api.read(suffix)
        self.assertEqual(self.target.calls, [])
        self.config_path.chmod(0o600)
        self.assertEqual(delivery.load_config(self.config_path, '42'), original)

        for status in (302, 401, 500):
            self.target.http_status = status
            self.assertNotEqual(delivery.observe(self.config)['status'], 'healthy')
        self.target.http_status = None
        for field, value in (('deployment_id', True), ('sha', 'b' * 40), ('contract_sha256', 'c' * 64),
                             ('environment', 'other'), ('acceptance', {'journey': 1})):
            health = self.target.value('/health')
            assert isinstance(health, dict)
            health[field] = value
            self.target.responses['/health'] = json.dumps(health).encode()
            self.assertEqual(delivery.observe(self.config)['failure'], 'delivery_unhealthy')
        self.target.responses.clear()
        self.assertEqual(delivery.observe(self.config)['status'], 'healthy')
        healthy = self.sample()
        self.target.healthy = False
        self.sample()
        before = self.stored()
        evidence_id = healthy['monitor']['latest']['evidence_id']
        original_rows = {t: self.rows(t) for t in ('iterations', 'delivery_observations', 'delivery_incidents', 'notifications')}
        archived = json.dumps(healthy['evidence'])
        cases = [('archive', 'delivery_observations', raw, evidence_id) for raw in (
            '[]', '{"status":"healthy"}', json.dumps(dict(healthy['evidence'], main='b' * 40)),
            ' ' * delivery.LIMIT + archived)]
        cases += [('current', table, raw, None) for table, raw in (
            ('iterations', json.dumps(dict(before, main_incident=None))),
            ('iterations', json.dumps(dict(before, main_incident=dict(before['main_incident'], recovery_assignment=True)))),
            ('iterations', json.dumps(dict(before, delivery_monitor=dict(before['delivery_monitor'], latest={})))),
            ('iterations', json.dumps(dict(before, delivery_monitor=dict(before['delivery_monitor'], sequence=True)))),
            ('iterations', json.dumps(dict(before, delivery_monitor=dict(before['delivery_monitor'], config_sha256='b' * 64)))),
            ('iterations', json.dumps(before)[:-1] + ',"approval":null}'),
            ('delivery_observations', '[]'), ('delivery_observations', '{"status":"healthy"}'),
            ('delivery_incidents', 'null'), ('notifications', '[]'))]
        for phase, table, raw, identity in cases:
            with self.subTest(phase=phase, table=table, raw=raw[:80]):
                self.target.healthy = phase == 'archive'
                with closing(sqlite3.connect(self.state)) as db, db:
                    suffix, args = (' WHERE id=?', (raw, identity)) if identity else ('', (raw,))
                    db.execute('UPDATE ' + table + ' SET payload=?' + suffix, args)
                snapshot = {t: self.rows(t) for t in original_rows}
                calls = len(self.target.calls)
                try:
                    with self.assertRaises((delivery.RepositoryError, attention.IntakeError)):
                        self.sample()
                    self.assertEqual({t: self.rows(t) for t in original_rows}, snapshot)
                    if phase == 'archive':
                        self.assertEqual(self.stored(), before)
                        self.assertEqual(self.rows('delivery_incidents'), original_rows['delivery_incidents'])
                        self.assertEqual(self.rows('notifications'), original_rows['notifications'])
                        self.assertEqual(next(row[-1] for row in self.rows('delivery_observations') if row[2] == evidence_id), raw)
                    else:
                        self.assertEqual(len(self.target.calls), calls)
                finally:
                    with closing(sqlite3.connect(self.state)) as db, db:
                        for name, rows in original_rows.items():
                            db.execute('DELETE FROM ' + name)
                            db.executemany('INSERT INTO ' + name + ' VALUES (' + ','.join('?' for _ in rows[0]) + ')', rows)
        self.target.healthy = False
        self.assertEqual(self.sample()['incident']['incident_id'], before['main_incident']['incident_id'])
        self.target.healthy = True
        self.assertIsNone(self.sample()['incident'])
        before = self.state.read_bytes()
        self.config = dict(original, environment='other')
        with self.assertRaises(delivery.RepositoryError) as raised:
            self.sample()
        self.assertEqual(raised.exception.code, 'delivery_conflict')
        self.assertEqual(self.state.read_bytes(), before)
        self.config = original

    def test_atomic_validate_and_notification_rollback_matrix(self):
        healthy = self.sample()['evidence']
        before = self.stored()
        cases = {'incomplete-healthy': dict(healthy, checks=[]),
                 'oversized-evidence': dict(healthy, status='unknown', failure='delivery_unknown', health=None, checks=['😀' * 200000]),
                 'oversized-iteration': dict(healthy, status='unknown', failure='delivery_unknown', health=None),
                 'notification-failure': None}
        for mode, evidence in cases.items():
            with self.subTest(case=mode):
                item = copy.deepcopy(before)
                if mode == 'oversized-iteration':
                    item['padding'] = ''
                    item['padding'] = 'x' * (delivery.LIMIT - len(json.dumps(item).encode()) - 1)
                with closing(sqlite3.connect(self.state)) as db, db:
                    db.execute('UPDATE iterations SET payload=?', (json.dumps(item),))
                    if mode == 'notification-failure':
                        db.execute("CREATE TRIGGER refuse_notification BEFORE INSERT ON notifications BEGIN SELECT RAISE(FAIL, 'injected outage'); END")
                tables = {t: self.rows(t) for t in ('delivery_observations', 'delivery_incidents', 'notifications')}
                try:
                    if mode == 'notification-failure':
                        self.target.healthy = False
                        with self.assertRaises(sqlite3.Error):
                            self.sample()
                    else:
                        with mock.patch.object(delivery, 'observe', return_value=evidence), self.assertRaises(delivery.RepositoryError):
                            self.sample()
                    self.assertEqual(self.stored(), item)
                    self.assertEqual({t: self.rows(t) for t in tables}, tables)
                    self.assertEqual(len(self.inspect()['observations']), 1)
                    self.assertEqual(self.notifications(), [])
                finally:
                    with closing(sqlite3.connect(self.state)) as db, db:
                        db.execute('DROP TRIGGER IF EXISTS refuse_notification')
                        db.execute('UPDATE iterations SET payload=?', (json.dumps(before),))
        self.assertEqual(self.inspect()['monitor']['latest']['status'], 'healthy')
        self.assertIsNotNone(self.sample()['incident'])
        self.assertEqual(len(self.notifications()), 1)
