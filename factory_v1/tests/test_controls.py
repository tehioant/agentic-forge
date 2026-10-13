"""Public interruption races. All tracker/model/Docker metadata is LABELED simulated.

Process-tree fixtures execute real Linux processes; they do not prove container or
native inference isolation. No completion rows are injected by these tests.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from factory_v1 import controls, merge
from factory_v1.repositories import RepositoryError, state_lock
from factory_v1.sandbox_guard import process_identity
from factory_v1.tests import test_assignments, test_merge, test_sandbox, test_spending_cancel
from factory_v1.tests.test_model_access_cancel import StalledHTTPFixture, STALLS


def cli(state, action, operator='42', project='product', iteration='m1'):
    args = [sys.executable, '-m', 'factory_v1', '--state', str(state), '--operator-id', operator,
            action, '--project', project, '--iteration', iteration]
    if action != 'inspect-control':
        args += ['--reference', 'LABELED deterministic operator request']
    return subprocess.run(args, capture_output=True, text=True, timeout=10)


def value(case, result):
    case.assertEqual(result.returncode, 0, result.stderr)
    return json.loads(result.stdout)


def wait(case, condition, message, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(.01)
    case.fail(message)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.assignment = test_assignments.AssignmentTests()
        self.assignment.setUp()
        self.addCleanup(self.assignment.doCleanups)
        self.state = self.assignment.fixture.state

    def test_public_pause_pins_evidence_and_budget_until_explicit_resume(self):
        assignment = self.assignment.ok(self.assignment.command())
        before = self.state.read_bytes()
        paused = value(self, cli(self.state, 'pause'))
        self.assertEqual(paused['state'], 'completed')
        self.assertFalse(paused['dispatch_allowed'])
        self.assertEqual(paused['checkpoint']['assignments'][0]['handoff'], assignment['handoff'])
        self.assertEqual(self.state.read_bytes(), before, 'Control request must not overwrite product progress or budgets')
        self.assignment.refused(self.assignment.command(), 'iteration_paused')
        self.assignment.refused(self.assignment.tracker.control('reserve-ticket', issue=10), 'iteration_paused')
        restarted = value(self, cli(self.state, 'inspect-control'))
        self.assertEqual(restarted['request_id'], paused['request_id'])
        self.assertEqual(restarted['checkpoint']['assignments'][0]['assignment_id'], assignment['assignment_id'])
        self.assertEqual(self.assignment.tracker.inspect()['status'], 'paused')
        resumed = value(self, cli(self.state, 'resume'))
        self.assertTrue(resumed['dispatch_allowed'])
        self.assertEqual(self.assignment.ok(self.assignment.command())['assignment_id'], assignment['assignment_id'])
        self.assignment.assert_no_external_writes()

    def test_operator_scope_private_state_corruption_and_symlinks_fail_closed(self):
        before = self.state.read_bytes()
        for action in ('pause', 'emergency-stop', 'resume', 'inspect-control'):
            self.assignment.refused(cli(self.state, action, operator='99'), 'approval_required')
            self.assignment.refused(cli(self.state, action, project='other'), 'not_found')
        self.assertEqual(self.state.read_bytes(), before)
        paused = value(self, cli(self.state, 'pause'))
        with closing(sqlite3.connect(self.state)) as db:
            root, record = controls.paths(db, 'product', 'm1')
        assert root is not None and record is not None
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)
        raw = record.read_bytes()
        for content in ('{malformed', '{}'):
            record.write_text(content)
            self.assignment.refused(cli(self.state, 'resume'), 'invalid_control')
        for changed in (dict(paused, operator_id='99'), dict(paused, action='resume', state='requested'),
                        dict(paused, checkpoint={})):
            record.write_text(json.dumps(changed))
            self.assignment.refused(self.assignment.command(), 'invalid_control')
        record.write_bytes(raw)
        record.unlink()
        record.symlink_to(root / 'absent')
        self.assignment.refused(self.assignment.command(), 'invalid_control')
        record.unlink()
        record.write_bytes(raw)
        record.chmod(0o600)
        self.assertEqual(value(self, cli(self.state, 'inspect-control'))['request_id'], paused['request_id'])

    def test_pause_does_not_wait_for_long_sqlite_transaction_or_queued_admission(self):
        with ThreadPoolExecutor(max_workers=1) as pool, closing(sqlite3.connect(self.state)) as db:
            with state_lock(db):
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE iterations SET payload=payload')
                queued = pool.submit(self.assignment.command)
                started = time.monotonic()
                paused = value(self, cli(self.state, 'pause'))
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(paused['state'], 'requested')
                self.assertFalse(paused['dispatch_allowed'])
                self.assignment.refused(cli(self.state, 'resume'), 'control_busy')
                db.rollback()
            self.assignment.refused(queued.result(timeout=10), 'iteration_paused')
        self.assertEqual(value(self, cli(self.state, 'inspect-control'))['state'], 'completed')
        self.assignment.assert_no_external_writes()

    def test_pause_racing_final_assignment_claim_prevents_new_row(self):
        entered, release = threading.Event(), threading.Event()
        parent = self.assignment.fixture.server.RequestHandlerClass
        class Handler(parent):
            def do_GET(handler):
                if handler.path == '/repos/example/product/issues/10':
                    entered.set()
                    release.wait(5)
                super().do_GET()
        self.assignment.fixture.server.RequestHandlerClass = Handler
        self.addCleanup(release.set)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.assignment.command)
            self.assertTrue(entered.wait(3))
            paused = value(self, cli(self.state, 'pause'))
            self.assertEqual(paused['state'], 'requested')
            release.set()
            self.assignment.refused(pending.result(), 'iteration_paused')
        with closing(sqlite3.connect(self.state)) as db:
            self.assertEqual(self.assignment_rows(db), [])
        self.assignment.assert_no_external_writes()

    @staticmethod
    def assignment_rows(db):
        from factory_v1.assignments import rows
        return rows(db)

    def test_pause_racing_final_result_admission_does_not_accept_stage_evidence(self):
        prepared = self.assignment.ok(self.assignment.command())
        request = self.assignment.result(prepared)  # Existing explicitly untrusted structured fixture, not native evidence.
        entered, release = threading.Event(), threading.Event()
        parent = self.assignment.fixture.server.RequestHandlerClass
        class Handler(parent):
            def do_GET(handler):
                if handler.path == '/repos/example/product/issues/10':
                    entered.set()
                    release.wait(5)
                super().do_GET()
        self.assignment.fixture.server.RequestHandlerClass = Handler
        self.addCleanup(release.set)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.assignment.command, 'assignment-result', request, prepared['assignment_id'])
            self.assertTrue(entered.wait(3))
            value(self, cli(self.state, 'pause'))
            release.set()
            self.assignment.refused(pending.result(), 'iteration_paused')
        self.assertNotIn('submitted_result', self.assignment.ok(self.assignment.inspect(prepared)))
        self.assignment.assert_no_external_writes()

    def test_other_project_control_does_not_pause_this_project_or_reset_budget(self):
        item = self.assignment.tracker.inspect()
        request = {key: item[key] for key in ('repository', 'idea', 'approval', 'origin')}
        request.update(project_id='other', iteration_id='other-m1')
        request_path = self.assignment.root / 'other-intake.json'
        request_path.write_text(json.dumps(request))
        value(self, self.assignment.fixture.cli('register', '--request', str(request_path)))
        paused = value(self, cli(self.state, 'pause', project='other', iteration='other-m1'))
        self.assertFalse(paused['dispatch_allowed'])
        self.assertEqual(self.assignment.tracker.inspect()['status'], 'active')
        prepared = self.assignment.ok(self.assignment.command())
        self.assertEqual(prepared['ticket_scope']['project'], 'product')
        self.assignment.refused(cli(self.state, 'resume', project='other', iteration='other-m1'), 'control_conflict')
        self.assignment.assert_no_external_writes()

    def test_concurrent_pause_stop_cannot_downgrade_stop_or_autoresume(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(lambda action: cli(self.state, action), ['pause', 'emergency-stop', 'pause']))
        for result in responses:
            value(self, result)
        inspected = value(self, cli(self.state, 'inspect-control'))
        self.assertEqual(inspected['action'], 'emergency-stop')
        self.assertEqual(inspected['state'], 'completed')
        self.assertFalse(inspected['dispatch_allowed'])
        self.assignment.refused(self.assignment.command(), 'iteration_paused')


class ActiveControlTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = test_sandbox.SandboxTests()
        self.sandbox.setUp()
        self.addCleanup(self.sandbox.doCleanups)
        self.assignment = self.sandbox.assignment
        self.state = self.assignment.fixture.state

    def test_signal_failure_does_not_skip_actual_worker_removal(self):
        self.sandbox.mode('process-tree')
        prepared = self.assignment.ok(self.assignment.command())
        controller = self.sandbox.started(prepared)
        runtime = self.sandbox.wait_running(prepared)['runtime']
        root = Path(runtime['artifacts'])
        wait(self, lambda: (root / 'scratch/process-tree.json').exists(), 'Real process fixture not ready')
        tree = json.loads((root / 'scratch/process-tree.json').read_text())
        identities = {pid: process_identity(pid) for pid in tree.values()}
        with closing(sqlite3.connect(self.state)) as db, patch.object(os, 'pidfd_open', side_effect=OSError):
            result = controls.request(db, 'product', 'm1', '42', 'emergency-stop', 'LABELED signal failure fixture')
        cleanup = result['cleanup'][prepared['assignment_id']]
        self.assertTrue(cleanup['container_removed'])
        self.assertEqual(cleanup['controller_signal_error'], 'stop_unconfirmed')
        wait(self, lambda: all(process_identity(pid) != start for pid, start in identities.items()), 'Worker survived failed signal')
        controller.communicate(timeout=10)
        self.assertEqual(controller.returncode, 0)
        self.assertEqual(value(self, cli(self.state, 'inspect-control'))['state'], 'completed')

    def test_real_worker_child_and_guard_stop_despite_long_shared_lock(self):
        self.sandbox.mode('process-tree')
        prepared = self.assignment.ok(self.assignment.command())
        controller = self.sandbox.started(prepared)
        runtime = self.sandbox.wait_running(prepared)['runtime']
        root = Path(runtime['artifacts'])
        wait(self, lambda: (root / 'scratch/process-tree.json').exists(), 'Real process-tree fixture never started')
        tree = json.loads((root / 'scratch/process-tree.json').read_text())
        identities = {pid: process_identity(pid) for pid in tree.values()}
        self.assertTrue(all(identities.values()))
        guard = json.loads((root / 'guard-process.json').read_text())
        with closing(sqlite3.connect(self.state)) as db, state_lock(db):
            started = time.monotonic()
            response = value(self, cli(self.state, 'emergency-stop'))
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(response['state'], 'acknowledged')
            self.assertTrue(response['cleanup'][prepared['assignment_id']]['container_removed'])
            wait(self, lambda: all(process_identity(pid) != start for pid, start in identities.items()),
                 'Worker or child actually continued after emergency stop')
            wait(self, lambda: process_identity(guard['pid']) != guard['start'], 'Independent guard remained live')
            self.assertFalse((self.sandbox.bin / 'container.json').exists())
            self.assignment.refused(cli(self.state, 'resume'), 'control_busy')
        output, error = controller.communicate(timeout=10)
        self.assertEqual(controller.returncode, 0, error)
        stopped = json.loads(output)
        self.assertEqual(stopped['runtime']['status'], 'stopped')
        readback = value(self, cli(self.state, 'inspect-control'))
        self.assertEqual(readback['state'], 'completed')
        retained = readback['checkpoint']['assignments'][0]
        self.assertEqual(retained['runtime']['run_id'], runtime['run_id'])
        self.assertIn(readback['requested_checkpoint']['assignments'][0]['runtime']['status'], ('preparing', 'running'))
        self.assertEqual(retained['runtime']['status'], 'stopped')
        self.assertNotIn('submitted_result', retained)
        self.assertTrue((root / 'initial-source.json').is_file())
        self.assertTrue((root / 'inputs/handoff.json').is_file())
        self.assertTrue((root / 'scratch/process-tree.json').is_file())
        self.assertIn('retained real process fixture candidate', (root / 'workspace/hello.py').read_text())
        self.assertEqual(self.sandbox.model_calls, [])
        self.assignment.assert_no_external_writes()
        evidence = os.environ.get('FORGE_CONTROL_EVIDENCE')
        if evidence:
            destination = Path(evidence) / 'real-process-tree'
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copytree(root, destination / 'artifacts', dirs_exist_ok=True)
            with closing(sqlite3.connect(self.state)) as original, closing(sqlite3.connect(destination / 'state.sqlite')) as backup:
                original.backup(backup)
            (destination / 'state.sqlite').chmod(0o600)
            shutil.copytree(Path(str(self.state) + '.controls'), destination / 'state.sqlite.controls', dirs_exist_ok=True)
            (destination / 'public-stop.json').write_text(json.dumps(response, indent=2))
            (destination / 'fresh-readback.json').write_text(json.dumps(readback, indent=2))
            (destination / 'process-liveness.json').write_text(json.dumps({
                'label': 'Real Linux process-group fixture, not Docker/native worker isolation',
                'before': identities, 'after': {pid: process_identity(pid) for pid in identities},
                'controller_exit': controller.returncode, 'guard_after': process_identity(guard['pid'])}, indent=2))

    def test_public_pause_during_assignment_retains_artifacts_and_cannot_launch_again(self):
        self.sandbox.mode('process-tree')
        prepared = self.assignment.ok(self.assignment.command())
        controller = self.sandbox.started(prepared)
        runtime = self.sandbox.wait_running(prepared)['runtime']
        root = Path(runtime['artifacts'])
        wait(self, lambda: (root / 'scratch/process-tree.json').exists(), 'Process fixture not ready')
        requested = value(self, cli(self.state, 'pause'))
        self.assertFalse(requested['dispatch_allowed'])
        output, error = controller.communicate(timeout=10)
        self.assertEqual(controller.returncode, 0, error)
        result = json.loads(output)
        self.assertEqual(result['runtime']['status'], 'stopped')
        self.assertIn(result['runtime']['error'], ('iteration_paused', 'cancelled'))
        self.assertEqual(value(self, cli(self.state, 'inspect-control'))['state'], 'completed')
        self.assertTrue((root / 'scratch/process-tree.json').is_file())
        self.assignment.refused(self.sandbox.launch(prepared), 'iteration_paused')
        self.assertTrue(value(self, cli(self.state, 'resume'))['dispatch_allowed'])
        self.assignment.refused(self.sandbox.launch(prepared), 'run_conflict')


class ModelControlTests(unittest.TestCase):
    def test_public_stop_cancels_inflight_socket_and_joins_real_helper_preserving_uncertainty(self):
        seam = test_spending_cancel.SpendingCancellationTests()
        seam.setUp()
        self.addCleanup(seam.doCleanups)
        fixture = StalledHTTPFixture(seam.upstream, STALLS['body_close'])
        self.addCleanup(fixture.close)
        seam.start_broker(fixture)
        seam.client()
        self.assertTrue(fixture.entered.wait(2))
        helpers = [t for t in threading.enumerate() if t.name == 'model-broker-canceller']
        self.assertEqual(len(helpers), 1)
        stopped = value(self, cli(seam.state, 'emergency-stop'))
        self.assertFalse(stopped['dispatch_allowed'])
        self.assertIn(stopped['state'], ('acknowledged', 'completed'))
        assert seam.broker is not None
        seam.broker.join(2)
        self.assertFalse(seam.broker.is_alive(), seam.errors)
        self.assertFalse(helpers[0].is_alive())
        self.assertFalse(seam.path.exists())
        self.assertTrue(fixture.disconnected.wait(1))
        self.assertEqual(len(fixture.calls), 1)
        self.assignment_refusal(cli(seam.state, 'resume'), 'control_busy')
        fresh = value(self, cli(seam.state, 'inspect-control'))
        self.assertEqual(fresh['checkpoint']['pending_model_operations'][0][1], 'pending')
        with closing(sqlite3.connect(seam.state)) as db:
            self.assertEqual(db.execute('SELECT remaining FROM model_grants').fetchone()[0], 1)

    def assignment_refusal(self, result, code):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stderr)['error'], code)


class MergeControlTests(unittest.TestCase):
    def setUp(self):
        self.seam = test_merge.MergeTests()
        self.seam.setUp()
        self.addCleanup(self.seam.doCleanups)
        self.state = self.seam.fixture.state

    def test_public_pause_concurrent_with_merge_last_metadata_read_has_zero_writes(self):
        response = []
        self.seam.boundary_change = lambda: response.append(value(self, cli(self.state, 'pause')))
        self.seam.refused('iteration_paused')
        self.assertEqual(response[0]['state'], 'requested')
        self.assertEqual(self.seam.writes(), [])
        self.assertIsNone(self.seam.stored().get('merge'))
        self.assertEqual(value(self, cli(self.state, 'inspect-control'))['state'], 'completed')
        self.assertIn('publication', value(self, cli(self.state, 'inspect-control'))['checkpoint']['assignments'][1])

    def test_pause_after_final_requirement_read_is_caught_by_target_boundary_guard(self):
        original = merge.require_publication
        calls = []
        def final_read(*args):
            original(*args)
            calls.append(True)
            if len(calls) == 2:
                self.assertEqual(value(self, cli(self.state, 'pause'))['state'], 'requested')
        with closing(sqlite3.connect(self.state)) as db, state_lock(db), patch.object(merge, 'require_publication', final_read):
            with self.assertRaises(RepositoryError) as caught:
                merge.lifecycle(db, 'product', 'm1', '42', self.seam.config_path, 'merge-candidate')
        self.assertEqual(caught.exception.code, 'iteration_paused')
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.seam.writes(), [])

    def test_stop_during_inflight_merge_reconciles_actual_effect_without_closure_or_replay(self):
        response = []
        self.seam.after_merge = lambda: response.append(value(self, cli(self.state, 'emergency-stop')))
        journal = self.seam.ok(self.seam.cli())
        self.assertEqual(response[0]['state'], 'acknowledged')
        self.assertEqual(journal['status'], 'merged')
        self.assertEqual(journal['integrated_evidence']['integrated'], self.seam.merge_sha)
        self.assertEqual(self.seam.assignment.tracker.issues[10]['state'], 'open')
        self.assertEqual(len(self.seam.writes('PUT')), 1)
        self.seam.pass_checks(self.seam.merge_sha)
        self.seam.refused('iteration_paused', 'verify-delivery')
        self.assertEqual(len(self.seam.writes('PUT')), 1)
        self.assertEqual(self.seam.writes('PATCH'), [])
        checkpoint = value(self, cli(self.state, 'inspect-control'))
        self.assertEqual(checkpoint['state'], 'completed')
        self.assertFalse(checkpoint['external_effects_undone'])
        self.assertEqual(self.seam.stored()['merge']['integrated_evidence']['integrated'], self.seam.merge_sha)


if __name__ == '__main__':
    unittest.main()
