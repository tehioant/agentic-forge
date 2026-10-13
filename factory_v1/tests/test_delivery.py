"""Public CLI/restart plus real loopback health; GitHub metadata is SIMULATED."""
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


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state.sqlite'

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.config = configuration('http://127.0.0.1:' + str(self.server.server_port))
        self.target = DeliveryTarget(self.config)
        self.target.attach(self.server)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.config_path = self.root / 'delivery.json'
        self.write_config()
        request = {'project_id': 'product', 'iteration_id': 'm1', 'repository': 'example/product', 'idea': 'test only',
                   'approval': {'operator_id': '42', 'reference': 'explicit mock-only intake'},
                   'origin': {'platform': 'discord', 'chat_id': '123', 'thread_id': '123', 'parent_chat_id': '122', 'scope_id': '121'}}
        path = self.root / 'intake.json'
        path.write_text(json.dumps(request))
        result = self.invoke('register', '--request', str(path), scope=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)

    def invoke(self, command, *args, scope=True):
        return subprocess.run([sys.executable, '-m', 'factory_v1', '--state', str(self.state), '--operator-id', '42', command,
                               *(['--project', 'product', '--iteration', 'm1'] if scope else []), *args],
                              cwd=Path(__file__).resolve().parents[2], text=True, capture_output=True, timeout=30)

    def sample(self):
        result = self.invoke('observe-delivery', '--delivery-config', str(self.config_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def inspect(self):
        result = self.invoke('inspect-delivery')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def notifications(self):
        result = self.invoke('notifications')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def stored(self):
        with closing(sqlite3.connect(self.state)) as db:
            return json.loads(db.execute('SELECT payload FROM iterations').fetchone()[0])

    def test_public_healthy_failure_restart_outage_branch_green_and_verified_clearance(self):
        self.assertEqual(self.sample()['evidence']['status'], 'healthy')
        self.assertEqual(self.notifications(), [])
        self.target.healthy = False
        first = self.sample()
        self.assertEqual(first['evidence']['failure'], 'delivery_unhealthy')
        incident = first['incident']['incident_id']
        self.assertEqual(self.stored()['main_incident']['incident_id'], incident)
        self.assertEqual(len(self.notifications()), 1)
        self.assertEqual(self.notifications()[0]['event']['evidence_ids'], [first['monitor']['latest']['evidence_id']])
        self.assertEqual(self.notifications()[0]['destination'], self.stored()['origin'])
        self.assertEqual(self.sample(), first)  # New CLI process, identical evidence + incident, no duplicate rows.
        self.assertEqual(len(self.inspect()['observations']), 2)
        self.target.github_outage = True
        outage = self.sample()
        self.assertEqual(outage['evidence']['status'], 'unknown')
        self.assertEqual(outage['incident']['incident_id'], incident)
        self.target.github_outage = False
        self.target.healthy = True
        self.target.check_sha = 'b' * 40  # Passing branch check is NOT healthy main.
        self.assertEqual(self.sample()['incident']['incident_id'], incident)
        self.target.check_sha = self.target.sha
        self.target.health_outage = True
        self.assertEqual(self.sample()['incident']['incident_id'], incident)
        self.target.health_outage = False
        self.target.deploy_sha = 'b' * 40  # Green workflows cannot prove actual exact deployment.
        self.assertEqual(self.sample()['incident']['incident_id'], incident)
        self.target.deploy_sha = self.target.sha
        cleared = self.sample()
        self.assertIsNone(cleared['incident'])
        self.assertNotIn('main_incident', self.stored())
        old = self.inspect()['incidents'][0]['record']
        self.assertEqual(old['status'], 'cleared')
        self.assertEqual(old['clearance_evidence'], cleared['monitor']['latest']['evidence_id'])
        self.assertEqual(len(self.notifications()), 1)
        self.target.healthy = False
        self.assertNotEqual(self.sample()['incident']['incident_id'], incident)  # New episode, same failure snapshot.
        self.assertEqual(len(self.notifications()), 2)

    def test_ci_cd_and_deployment_failures_pending_skipped_unknown_freeze(self):
        for field, values in [('check_conclusion', ['failure', 'cancelled', 'skipped', None]),
                              ('workflow_conclusion', ['failure', 'cancelled', None]),
                              ('deployment_state', ['failure', 'error', 'inactive', 'pending'])]:
            for value in values:
                with self.subTest(field=field, value=value):
                    original = getattr(self.target, field)
                    setattr(self.target, field, value)
                    failed = self.sample()
                    self.assertIsNotNone(failed['incident'])
                    self.assertNotEqual(failed['evidence']['status'], 'healthy')
                    setattr(self.target, field, original)
                    self.assertIsNone(self.sample()['incident'])

    def test_unknown_first_sample_freezes_once_concurrent_polling_is_idempotent(self):
        self.target.github_outage = True
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.sample(), range(8)))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.notifications()), 1)
        self.assertEqual(len(self.inspect()['observations']), 1)

    def test_bounded_watcher_repeats_after_state_transition(self):
        self.target.after_health = lambda: setattr(self.target, 'healthy', self.target.health_reads == 1)
        result = self.invoke('watch-delivery', '--delivery-config', str(self.config_path), '--samples', '3', '--interval', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        values = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([v['evidence']['status'] for v in values], ['healthy', 'failed', 'failed'])
        self.assertEqual(values[1]['incident']['incident_id'], values[2]['incident']['incident_id'])
        self.assertEqual(len(self.notifications()), 1)
        for args in [('0', '1'), ('1', 'nan'), ('1', '0'), ('10001', '1')]:
            refused = self.invoke('watch-delivery', '--delivery-config', str(self.config_path), '--samples', args[0], '--interval', args[1])
            self.assertEqual(refused.returncode, 2, refused.stderr)

    def test_main_deployment_and_check_races_fail_closed(self):
        for field in ['sha', 'deploy_sha', 'check_conclusion', 'workflow_conclusion', 'deployment_state']:
            with self.subTest(field=field):
                original = getattr(self.target, field)
                self.target.after_health = lambda: setattr(self.target, field, 'b' * 40 if 'sha' in field else 'failure')
                observed = self.sample()
                self.assertIsNotNone(observed['incident'])
                self.assertNotEqual(observed['evidence']['status'], 'healthy')
                self.target.after_health = None
                setattr(self.target, field, original)
                self.assertIsNone(self.sample()['incident'])

    def test_malformed_paginated_ambiguous_and_oversized_readback_never_healthy(self):
        suffixes = ['/repos/example/product', '/repos/example/product/git/ref/heads/main',
                    '/repos/example/product/commits/' + self.target.sha + '/check-runs?per_page=100&filter=latest', '/health']
        for path in suffixes:
            for raw in [b'null', b'[]', b'{"id":1,"id":7}', b'[' * 1200 + b'0' + b']' * 1200, b'{' + b' ' * delivery.LIMIT + b'}']:
                with self.subTest(path=path, prefix=raw[:30]):
                    self.target.responses[path] = raw
                    self.assertIsNotNone(self.sample()['incident'])
                    self.target.responses.clear()
                    self.assertIsNone(self.sample()['incident'])
        path = suffixes[2]
        value = self.target.value(path)
        value['total_count'] = 101
        self.target.responses[path] = json.dumps(value).encode()
        self.assertEqual(self.sample()['evidence']['failure'], 'delivery_unknown')

    def test_health_redirects_http_errors_and_wrong_contract_refused(self):
        for status in [302, 401, 500]:
            self.target.http_status = status
            self.assertIsNotNone(self.sample()['incident'])
        self.target.http_status = None
        for field, value in [('deployment_id', True), ('sha', 'b' * 40), ('contract_sha256', 'c' * 64),
                              ('environment', 'other'), ('acceptance', {'journey': 1})]:
            health = self.target.value('/health')
            health[field] = value
            self.target.responses['/health'] = json.dumps(health).encode()
            self.assertEqual(self.sample()['evidence']['failure'], 'delivery_unhealthy')
        self.target.responses.clear()
        self.assertIsNone(self.sample()['incident'])

    def test_configuration_shapes_pins_private_file_and_persisted_state_refused(self):
        self.sample()
        original = copy.deepcopy(self.config)
        cases = [None, [], dict(original, required_checks=[]), dict(original, required_workflows=[]),
                 dict(original, repository_id=True), dict(original, timeout=float('nan')),
                 dict(original, api_base='https://example.com'), dict(original, issue=0),
                 dict(original, contract=dict(original['contract'], revision='not-a-commit')),
                 dict(original, contract=dict(original['contract'], health_url='http://example.com/health')),
                 dict(original, contract=dict(original['contract'], acceptance={})),
                 dict(original, environment='other')]
        before = self.stored()
        calls = len(self.target.calls)
        for config in cases:
            self.config = config
            self.write_config()
            result = self.invoke('observe-delivery', '--delivery-config', str(self.config_path))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(self.stored(), before)
            self.assertEqual(len(self.target.calls), calls)
        self.config = original
        self.write_config()
        self.config_path.chmod(0o644)
        self.assertEqual(self.invoke('observe-delivery', '--delivery-config', str(self.config_path)).returncode, 2)
        self.config_path.chmod(0o600)
        invalid = [[], {'config': []}, dict(before['delivery_monitor'], sequence=True),
                   dict(before['delivery_monitor'], config_sha256='b' * 64)]
        for monitor in invalid:
            with closing(sqlite3.connect(self.state)) as db, db:
                db.execute('UPDATE iterations SET payload=?', (json.dumps(dict(before, delivery_monitor=monitor)),))
            result = self.invoke('observe-delivery', '--delivery-config', str(self.config_path))
            self.assertEqual(result.returncode, 2, result.stderr)
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('UPDATE iterations SET payload=?', (json.dumps(before)[:-1] + ',"approval":null}',))
        self.assertEqual(self.invoke('observe-delivery', '--delivery-config', str(self.config_path)).returncode, 2)
        self.assertEqual(len(self.target.calls), calls)

    def test_persisted_evidence_incident_freeze_and_attention_corruption_refuse_before_reads(self):
        self.target.healthy = False
        self.sample()
        before = self.stored()
        with closing(sqlite3.connect(self.state)) as db:
            evidence_row = db.execute('SELECT * FROM delivery_observations').fetchone()
            incident_row = db.execute('SELECT * FROM delivery_incidents').fetchone()
            notification_row = db.execute('SELECT * FROM notifications').fetchone()
        calls = len(self.target.calls)
        cases = [('iterations', json.dumps(dict(before, main_incident=None))),
                 ('iterations', json.dumps(dict(before, main_incident=dict(before['main_incident'], recovery_assignment=True)))),
                 ('iterations', json.dumps(dict(before, delivery_monitor=dict(before['delivery_monitor'], latest={})))),
                 ('delivery_observations', '[]'), ('delivery_observations', '{"status":"healthy"}'),
                 ('delivery_incidents', 'null'), ('notifications', '[]')]
        for table, raw in cases:
            with self.subTest(table=table, raw=raw[:100]):
                with closing(sqlite3.connect(self.state)) as db, db:
                    db.execute('UPDATE ' + table + ' SET payload=?', (raw,))
                result = self.invoke('observe-delivery', '--delivery-config', str(self.config_path))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertEqual(len(self.target.calls), calls)
                with closing(sqlite3.connect(self.state)) as db, db:
                    db.execute('UPDATE iterations SET payload=?', (json.dumps(before),))
                    for target, row in [('delivery_observations', evidence_row), ('delivery_incidents', incident_row), ('notifications', notification_row)]:
                        db.execute('UPDATE ' + target + ' SET payload=?', (row[-1],))
        self.assertEqual(self.sample()['incident']['incident_id'], before['main_incident']['incident_id'])

    def test_config_duplicate_parser_limits_and_read_capability_refuse(self):
        before = self.stored()
        for raw in [b'{"operator_id":"42","operator_id":"42"}', b'[' * 1200 + b'0' + b']' * 1200,
                    b' ' * (delivery.LIMIT + 1), b'null']:
            self.config_path.write_bytes(raw)
            refused = self.invoke('observe-delivery', '--delivery-config', str(self.config_path))
            self.assertEqual(refused.returncode, 2, refused.stderr)
            self.assertEqual(self.stored(), before)
            self.assertEqual(self.target.calls, [])
        api = delivery.DeliveryGitHub(self.config)
        for suffix in ['/issues/18', '/git/refs', '/actions/runs/1/rerun', '/deployments?environment=other', '/../../user', 'https://example.com']:
            with self.assertRaises(delivery.RepositoryError):
                api.read(suffix)
        self.assertEqual(self.target.calls, [])

    def test_atomic_attention_failure_does_not_commit_partial_monitor_incident_or_evidence(self):
        self.sample()
        before = self.stored()
        self.target.healthy = False
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute("CREATE TRIGGER refuse_notification BEFORE INSERT ON notifications BEGIN SELECT RAISE(FAIL, 'injected outage'); END")
        result = self.invoke('observe-delivery', '--delivery-config', str(self.config_path))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.stored(), before)
        self.assertEqual(len(self.inspect()['observations']), 1)
        self.assertEqual(self.notifications(), [])
        with closing(sqlite3.connect(self.state)) as db, db:
            db.execute('DROP TRIGGER refuse_notification')
        self.assertIsNotNone(self.sample()['incident'])
        self.assertEqual(len(self.notifications()), 1)


if __name__ == '__main__':
    unittest.main()
