"""Public launcher tests; Docker/model fixtures are NOT live isolation evidence."""
import json
import os
import signal
import subprocess
import shutil
import socketserver
import threading
import time
import sys
from http.server import BaseHTTPRequestHandler
import unittest
from pathlib import Path
from unittest.mock import patch

from factory_v1.repositories import RepositoryError
from factory_v1.sandbox_policy import Docker
from factory_v1.tests import test_assignments

IMAGE = 'nousresearch/hermes-agent@sha256:d4da4a40cd7a28aba983775d9fd31d94cbf153eeb0cb9e844d6d0f612b7c24db'


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.assignment = test_assignments.AssignmentTests()
        self.assignment.setUp()
        self.addCleanup(self.assignment.doCleanups)
        self.root = self.assignment.root
        self.source = Path(self.assignment.request['workspace'])
        self.source.mkdir()
        for command in [['git', 'init', '-q'], ['git', 'config', 'user.email', 'fixture@example.invalid'],
                        ['git', 'config', 'user.name', 'Fixture']]:
            subprocess.run(command, cwd=self.source, check=True, capture_output=True)
        (self.source / 'hello.py').write_text('print("baseline")\n')
        subprocess.run(['git', 'add', '.'], cwd=self.source, check=True)
        subprocess.run(['git', 'commit', '-qm', 'test: baseline'], cwd=self.source, check=True)
        self.assignment.request['baseline'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=self.source, text=True).strip()
        self.artifacts = self.root / 'artifacts'
        self.artifacts.mkdir(mode=0o700)
        self.config = self.root / 'launcher.json'
        self.config.write_text(json.dumps({'image': IMAGE, 'uid': os.getuid() or 10000,
            'subscription_socket': str(self.root / 'subscription.sock'), 'artifacts_root': str(self.artifacts),
            'limits': {'seconds': 30, 'max_calls': 10, 'memory_mb': 512, 'cpus': 1, 'pids': 64, 'scratch_mb': 64}}))
        self.config.chmod(0o600)
        self.bin = self.root / 'docker-fixture'
        self.bin.mkdir()
        shutil.copyfile(Path(__file__).parent / 'docker_fixture.py', self.bin / 'docker')
        (self.bin / 'docker').chmod(0o755)
        old_path = os.environ['PATH']
        os.environ['PATH'] = str(self.bin) + os.pathsep + old_path
        self.addCleanup(os.environ.__setitem__, 'PATH', old_path)
        case = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                case.model_calls.append((self.path, payload))
                response = {'id': 'resp_fixture', 'object': 'response', 'status': 'completed',
                    'model': 'gpt-6.1-sol', 'output': [{'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'output_text', 'text': 'Labeled deterministic model fixture'}]}]}
                data = ('data: ' + json.dumps({'type': 'response.completed', 'response': response}) + '\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.model_calls = []
        self.upstream = socketserver.UnixStreamServer(str(self.root / 'subscription.sock'), Handler)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        self.addCleanup(self.stop_upstream)

    def stop_upstream(self):
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join()

    def mode(self, mode):
        (self.bin / 'mode').write_text(mode)

    def started(self, prepared):
        args = [sys.executable, '-m', 'factory_v1', '--state', str(self.assignment.fixture.state), '--operator-id', '42',
            'launch-assignment', '--project', 'product', '--iteration', 'm1', '--assignment', prepared['assignment_id'],
            '--launcher-config', str(self.config), '--api-base', self.assignment.fixture.base]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        return process

    def wait_running(self, prepared):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            assignment = self.assignment.ok(self.assignment.inspect(prepared))
            if assignment.get('runtime', {}).get('status') == 'running':
                return assignment
            time.sleep(.05)
        self.fail('Fixture attempt did not become running')

    def launch(self, prepared, **kwargs):
        return self.assignment.command('launch-assignment', assignment=prepared['assignment_id'],
            extra=('--launcher-config', str(self.config), '--api-base', self.assignment.fixture.base), **kwargs)

    def test_untrusted_launcher_fields_are_refused_without_creating_attempt(self):
        prepared = self.assignment.ok(self.assignment.command())
        config = json.loads(self.config.read_text())
        config['command'] = ['sh', '-c', 'unrestricted']
        self.config.write_text(json.dumps(config))
        self.assignment.refused(self.launch(prepared), 'invalid_launcher')
        observed = self.assignment.ok(self.assignment.inspect(prepared))
        self.assertNotIn('runtime', observed)
        self.assertEqual(list(self.artifacts.iterdir()), [])


    def test_launcher_path_aliases_refused_before_attempt_or_external_operations(self):
        prepared = self.assignment.ok(self.assignment.command())
        outside = self.root / 'outside'
        outside.mkdir()
        canonical = self.config
        original = json.loads(canonical.read_text())
        before = self.assignment.fixture.state.read_bytes()
        self.config = outside / '..' / canonical.name
        self.assignment.refused(self.launch(prepared), 'unsafe_path')
        self.config = canonical
        changed = dict(original, artifacts_root=str(outside / '..' / self.artifacts.name))
        canonical.write_text(json.dumps(changed))
        self.assignment.refused(self.launch(prepared), 'unsafe_path')
        canonical.write_text(json.dumps(original))
        self.assertEqual(self.assignment.fixture.state.read_bytes(), before)
        self.assertEqual(list(self.artifacts.iterdir()), [])
        self.assertEqual(self.model_calls, [])
        self.assignment.assert_no_external_writes()

    def test_disjoint_controller_scopes_refused_before_runtime_mutation(self):
        from factory_v1 import sandbox
        from factory_v1.repositories import GitHub

        prepared = self.assignment.ok(self.assignment.command())
        outside = self.root / 'outside'
        outside.mkdir()
        state = self.assignment.fixture.state
        original = json.loads(self.config.read_text())
        mounted_config = self.source / 'launcher.json'
        mounted_config.write_text(json.dumps(original))
        mounted_config.chmod(0o600)
        nested_artifacts = self.source / 'artifacts'
        nested_artifacts.mkdir(mode=0o700)
        before = state.read_bytes()
        # Only prerequisite Docker/provider discovery is simulated; no fake binary
        # execution or sandbox receipt is needed to test refusal before launch.
        with patch.object(Docker, 'image', return_value=IMAGE), patch('factory_v1.model_access.transport'):
            for state_path, config_path, artifacts in (
                    (outside / '..' / state.name, self.config, self.artifacts),
                    (state, mounted_config, self.artifacts),
                    (state, self.config, nested_artifacts)):
                with self.subTest(state=str(state_path), config=str(config_path), artifacts=str(artifacts)):
                    self.config.write_text(json.dumps(dict(original, artifacts_root=str(artifacts))))
                    with self.assertRaises(RepositoryError) as refusal:
                        sandbox.launch(str(state_path), 'product', 'm1', '42', prepared['assignment_id'],
                                       str(config_path), GitHub(self.assignment.fixture.base, 'fixture', 10))
                    self.assertEqual(refusal.exception.code, 'unsafe_path')
                    self.assertEqual(state.read_bytes(), before)
                    self.assertEqual(list(self.artifacts.iterdir()), [])
                    self.assertEqual(list(nested_artifacts.iterdir()), [])
        self.assertEqual(self.model_calls, [])
        self.assignment.assert_no_external_writes()

    def test_launch_returns_verified_scoped_artifacts_without_modifying_original_source(self):
        prepared = self.assignment.ok(self.assignment.command())
        launched = self.assignment.ok(self.launch(prepared))
        self.assertEqual(launched['runtime']['status'], 'complete', launched)
        self.assertTrue(launched['runtime']['container_removed'])
        self.assertFalse(launched['result_disposition']['trusted_execution'])
        self.assertTrue(launched['result_disposition']['isolated_execution'])
        self.assertFalse(launched['result_disposition']['checks_verified'])
        self.assertFalse(launched['result_disposition']['advance_allowed'])
        self.assertFalse(launched['result_disposition']['close_allowed'])
        root = Path(launched['runtime']['artifacts'])
        self.assertEqual((root / 'workspace/hello.py').read_text(), 'print("candidate fixture")\n')
        self.assertEqual((self.source / 'hello.py').read_text(), 'print("baseline")\n')
        self.assertFalse((root / 'workspace/.git').exists())
        self.assertEqual(len(self.model_calls), 1)
        self.assertEqual(self.model_calls[0][0], '/v1/responses')
        self.assertEqual(self.model_calls[0][1]['model'], 'gpt-6.1-sol')
        observed = self.assignment.ok(self.assignment.inspect(prepared))
        self.assertEqual(observed['submitted_result'], launched['submitted_result'])
        self.assignment.refused(self.launch(prepared), 'run_conflict')
        self.assertFalse((self.bin / 'container.json').exists())
        self.assignment.assert_no_external_writes()

    def test_packed_source_ignores_repository_configuration_and_untracked_secrets(self):
        subprocess.run(['git', 'gc', '--prune=now'], cwd=self.source, check=True, capture_output=True)
        marker = self.root / 'untrusted-host-command-ran'
        with (self.source / '.git/config').open('a') as stream:
            stream.write('\n[core]\n\tfsmonitor = touch ' + str(marker) + '\n')
        (self.source / '.env').write_text('UNTRACKED_SECRET_FIXTURE=not-real\n')
        prepared = self.assignment.ok(self.assignment.command())
        launched = self.assignment.ok(self.launch(prepared))
        self.assertEqual(launched['runtime']['status'], 'complete', launched)
        root = Path(launched['runtime']['artifacts'])
        self.assertFalse((root / 'workspace/.env').exists())
        self.assertFalse((root / 'inputs/baseline/.git').exists())
        self.assertFalse(marker.exists())

    def test_large_engineering_request_is_bounded_and_admitted_without_route_change(self):
        self.mode('large-request')
        prepared = self.assignment.ok(self.assignment.command())
        launched = self.assignment.ok(self.launch(prepared))
        self.assertEqual(launched['runtime']['status'], 'complete', launched)
        self.assertGreater(len(self.model_calls[0][1]['input']), 32768)
        self.assertEqual(self.model_calls[0][1]['service_tier'], 'default')
        self.assertFalse(self.model_calls[0][1]['store'])

    def test_provider_executed_network_tools_are_denied_at_admission(self):
        self.mode('server-tool')
        prepared = self.assignment.ok(self.assignment.command())
        launched = self.assignment.ok(self.launch(prepared))
        self.assertEqual(launched['runtime']['status'], 'failed')
        self.assertEqual(launched['runtime']['error'], 'worker_failed')
        self.assertEqual(self.model_calls, [])
        self.assertNotIn('submitted_result', launched)

    def test_pause_of_running_attempt_stops_it_and_preserves_scratch(self):
        self.mode('hang')
        prepared = self.assignment.ok(self.assignment.command())
        process = self.started(prepared)
        running = self.wait_running(prepared)
        self.assignment.mutate_iteration(lambda item: item.update(status='paused'))
        output, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, error)
        result = json.loads(output)
        self.assertEqual(result['runtime']['error'], 'iteration_paused')
        self.assertFalse((self.bin / 'container.json').exists())
        self.assertTrue(Path(running['runtime']['artifacts']).is_dir())

    def test_role_mount_files_are_readable_by_different_nonroot_identity(self):
        prepared = self.assignment.ok(self.assignment.command())
        launched = self.assignment.ok(self.launch(prepared))
        root = Path(launched['runtime']['artifacts'])
        self.assertEqual((root / 'inputs').stat().st_mode & 0o007, 0o005)
        self.assertEqual((root / 'inputs/worker.py').stat().st_mode & 0o004, 0o004)
        self.assertEqual((root / 'inputs/baseline/hello.py').stat().st_mode & 0o004, 0o004)
        self.assertEqual((root / 'workspace/hello.py').stat().st_mode & 0o002, 0o002)
        self.assertEqual(root.stat().st_mode & 0o077, 0)

    def test_unlinked_diagnostic_assignment_cannot_inject_failure_prose(self):
        # Actual read-only diagnosis now needs the declared stuck precursor in test_diagnosis.
        request = self.assignment.configuration(stage='diagnosis')
        request['baseline'] = self.assignment.request['baseline']
        before = self.assignment.fixture.state.read_bytes()
        self.assignment.refused(self.assignment.command(request=request), 'implementation_unverified')
        self.assertEqual(self.assignment.fixture.state.read_bytes(), before)
        self.assertEqual(self.model_calls, [])
        self.assertEqual(list(self.artifacts.iterdir()), [])

    def test_bad_physical_pin_is_recoverable_and_never_starts_worker(self):
        self.assignment.request['baseline'] = 'a' * 40
        prepared = self.assignment.ok(self.assignment.command())
        launched = self.assignment.ok(self.launch(prepared))
        self.assertEqual(launched['runtime']['status'], 'failed')
        self.assertEqual(launched['runtime']['error'], 'source_blocked')
        self.assertEqual(self.model_calls, [])
        self.assertNotIn('submitted_result', launched)

    def test_unexpected_anonymous_mount_refuses_before_model_or_start(self):
        self.mode('extra-mount')
        prepared = self.assignment.ok(self.assignment.command())
        launched = self.assignment.ok(self.launch(prepared))
        self.assertEqual(launched['runtime']['error'], 'isolation_unavailable')
        self.assertEqual(self.model_calls, [])
        self.assertFalse((self.bin / 'container.json').exists())

    def test_malformed_result_and_forged_loads_retain_artifacts_but_never_advance(self):
        for mode in ('malformed', 'bad-load', 'symlink'):
            with self.subTest(mode=mode):
                # Separate fresh role claims on the same ticket; no retry steals old ownership.
                self.mode(mode)
                self.assignment.request['profile']['name'] = 'factory-' + mode
                self.assignment.request['profile']['home'] = str(self.root / ('profile-' + mode))
                self.assignment.request['claim_id'] = 'claim-' + mode
                prepared = self.assignment.ok(self.assignment.command())
                launched = self.assignment.ok(self.launch(prepared))
                self.assertEqual(launched['runtime']['status'], 'failed')
                self.assertIn(launched['runtime']['error'], ('invalid_result', 'source_blocked', 'unsafe_path'))
                self.assertNotIn('submitted_result', launched)
                self.assertTrue(Path(launched['runtime']['artifacts']).is_dir())
                self.assertFalse((self.bin / 'container.json').exists())

    def test_pause_and_changed_ticket_refuse_before_any_attempt(self):
        prepared = self.assignment.ok(self.assignment.command())
        self.assignment.tracker.issues[10]['body'] += '\nMaterial scope change'
        self.assignment.refused(self.launch(prepared), 'scope_mismatch')
        self.assertNotIn('runtime', self.assignment.ok(self.assignment.inspect(prepared)))
        self.assertEqual(self.model_calls, [])
        self.assignment.mutate_iteration(lambda item: item.update(status='paused'))
        self.assignment.refused(self.launch(prepared), 'iteration_paused')

    def test_removal_fixture_cannot_skip_progress_inspection_with_multiple_removers(self):
        from factory_v1.repositories import RepositoryError
        self.mode('stop-race')
        state = self.bin / 'container.json'
        state.write_text(json.dumps({'State': {'Running': True}}))
        docker = Docker()
        for _ in range(2):
            with self.assertRaises(RepositoryError):
                docker.call('rm', '-f', 'fixture-container')
            self.assertEqual((self.bin / 'removal-in-progress').read_text(), 'pending')
            self.assertTrue(state.exists())
        present = docker.inspect('fixture-container')
        self.assertIsNotNone(present)
        assert present is not None
        self.assertEqual(present['State']['Status'], 'removing')
        self.assertEqual((self.bin / 'removal-in-progress').read_text(), 'observed')
        self.assertIsNone(docker.inspect('fixture-container', optional=True))
        self.assertFalse(state.exists())
        docker.remove('fixture-container')

    def test_stop_during_concurrent_removal_keeps_controller_and_readback_consistent(self):
        self.mode('stop-race')
        prepared = self.assignment.ok(self.assignment.command())
        process = self.started(prepared)
        running = self.wait_running(prepared)
        process.send_signal(signal.SIGTERM)
        output, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, error)
        launched = json.loads(output)
        root = Path(running['runtime']['artifacts'])
        self.assertEqual((self.bin / 'removal-in-progress').read_text(), 'observed')
        self.assertFalse((self.bin / 'container.json').exists())
        self.assertEqual(launched['runtime']['status'], 'stopped')
        self.assertEqual(launched['runtime']['error'], 'cancelled')
        self.assertTrue(launched['runtime']['container_removed'])
        guarded = json.loads((root / 'guard-result.json').read_text())
        self.assertEqual(guarded['status'], 'stopped')
        self.assertIn(guarded['reason'], ('cancelled', 'controller_finished'))
        observed = self.assignment.ok(self.assignment.inspect(prepared))
        self.assertEqual(observed['runtime'], launched['runtime'])
        stopped = self.assignment.ok(self.assignment.command('stop-assignment', assignment=prepared['assignment_id']))
        self.assertEqual(stopped['runtime'], launched['runtime'])
        self.assertEqual(self.model_calls, [])
        self.assignment.assert_no_external_writes()

    def test_stop_and_controller_death_remove_real_fixture_process_target_and_retain_evidence(self):
        self.mode('hang')
        prepared = self.assignment.ok(self.assignment.command())
        process = self.started(prepared)
        running = self.wait_running(prepared)
        response = self.assignment.command('stop-assignment', assignment=prepared['assignment_id'])
        stopped = self.assignment.ok(response)
        self.assertEqual(stopped['runtime']['status'], 'stopped')
        process.communicate(timeout=10)
        self.assertFalse((self.bin / 'container.json').exists())
        self.assertTrue((Path(running['runtime']['artifacts']) / 'container.log').is_file())
        # Fresh owned profile for the separate crash case.
        self.assignment.request['profile']['name'] = 'factory-crash'
        self.assignment.request['profile']['home'] = str(self.root / 'profile-crash')
        self.assignment.request['claim_id'] = 'claim-crash'
        prepared = self.assignment.ok(self.assignment.command())
        process = self.started(prepared)
        running = self.wait_running(prepared)
        process.kill()
        process.communicate(timeout=5)
        root = Path(running['runtime']['artifacts'])
        deadline = time.monotonic() + 10
        while not (root / 'guard-result.json').exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertEqual(json.loads((root / 'guard-result.json').read_text())['reason'], 'controller_lost')
        self.assertFalse((self.bin / 'container.json').exists())
        reconciled = self.assignment.ok(self.assignment.command('reconcile-assignment', assignment=prepared['assignment_id']))
        self.assertEqual(reconciled['runtime']['error'], 'controller_lost')
        self.assertTrue(reconciled['runtime']['recoverable'])


class DockerRemovalTests(unittest.TestCase):
    def setUp(self):
        with patch('factory_v1.sandbox_policy.shutil.which', return_value='/fixture/docker'):
            self.docker = Docker()
        self.name = 'factory-run-removal-fixture'

    def response(self, args, code=0, stdout=b'', stderr=b''):
        return subprocess.CompletedProcess(['/fixture/docker', *args], code, stdout, stderr)

    def test_conflicting_remove_waits_for_verified_absence(self):
        responses = [
            self.response(['rm', '-f', self.name], 1, stderr=b'removal is already in progress'),
            self.response(['inspect', self.name], stdout=b'[{"State": {"Status": "removing"}}]'),
            self.response(['inspect', self.name], 1, stderr=b'Error: No such object'),
        ]
        with patch('factory_v1.sandbox_policy.subprocess.run', side_effect=responses) as run, \
                patch('time.sleep'):
            self.docker.remove(self.name)
        self.assertEqual([call.args[0][1:] for call in run.call_args_list],
                         [['rm', '-f', self.name], ['inspect', self.name], ['inspect', self.name]])

    def test_already_removed_is_idempotent_but_still_inspected(self):
        responses = [self.response(['rm', '-f', self.name], 1, stderr=b'No such container'),
                     self.response(['inspect', self.name], 1, stderr=b'Error: No such container')]
        with patch('factory_v1.sandbox_policy.subprocess.run', side_effect=responses) as run:
            self.docker.remove(self.name)
        self.assertEqual(run.call_count, 2)

    def test_remaining_container_never_counts_as_removed(self):
        for state in ('running', 'removing'):
            with self.subTest(state=state):
                responses = [self.response(['rm', '-f', self.name], 1),
                             self.response(['inspect', self.name], stdout=json.dumps([{'State': {'Status': state}}]).encode()),
                             self.response(['inspect', self.name], stdout=json.dumps([{'State': {'Status': state}}]).encode())]
                with patch('factory_v1.sandbox_policy.subprocess.run', side_effect=responses), \
                        patch('time.monotonic', side_effect=[0, 0, 30]), \
                        patch('time.sleep') as sleep, \
                        self.assertRaises(RepositoryError) as caught:
                    self.docker.remove(self.name)
                self.assertEqual(caught.exception.code, 'stop_unconfirmed')
                sleep.assert_called_once_with(.1)

    def test_removal_timeout_is_not_hidden_by_optional_command(self):
        with patch('factory_v1.sandbox_policy.subprocess.run', side_effect=
                   subprocess.TimeoutExpired('docker rm', 30)) as run, \
                self.assertRaises(RepositoryError) as caught:
            self.docker.remove(self.name)
        self.assertEqual(caught.exception.code, 'container_unavailable')
        self.assertEqual(run.call_count, 1)

    def test_inspection_failures_never_count_as_absence(self):
        failures = [self.response(['inspect', self.name], 1, stderr=b'Cannot connect to Docker daemon'),
                    self.response(['inspect', self.name], stdout=b'not JSON'),
                    subprocess.TimeoutExpired('docker inspect', 30)]
        for failure in failures:
            with self.subTest(failure=failure):
                with patch('factory_v1.sandbox_policy.subprocess.run', side_effect=[
                        self.response(['rm', '-f', self.name]), failure]), \
                        self.assertRaises(RepositoryError) as caught:
                    self.docker.remove(self.name)
                self.assertEqual(caught.exception.code, 'container_unavailable')


if __name__ == '__main__':
    unittest.main()
