"""Small interruption components; external tracker/worker/model effects are mocked.

Durable records are direct test inputs, never native execution/acceptance evidence.
The legacy cli/value/wait helpers remain available to untouched journey tests.
"""
import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from factory_v1 import assignments, controls, delivery, merge, spending, tickets
from factory_v1.repositories import RepositoryError, state_lock


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


def artifact(name, content):
    return {'name': name, 'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest()}


class ControlFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='controls-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'state.sqlite'
        self.db = sqlite3.connect(self.state)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT, PRIMARY KEY(project_id, iteration_id))')
        self.db.execute('CREATE TABLE role_assignments (assignment_id TEXT PRIMARY KEY, payload TEXT)')
        self.item = {'project_id': 'product', 'iteration_id': 'm1', 'status': 'active',
                     'execution_allowed': False,
                     'approval': {'operator_id': '42', 'reference': 'LABELED approval'},
                     'repository': 'example/product',
                     'repository_onboarding': {'status': 'verified', 'metadata': {'id': 123},
                                               'api_base': 'https://api.github.com'},
                     'handoff': {'commit': 'a' * 40},
                     'ticket_work': {'assignment_id': 'tickets', 'input_digest': 'd' * 64,
                                     'inputs': {'documents': {}}},
                     'budget': {'attempts': 3, 'deadline': 12345}}
        self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('product', 'm1', json.dumps(self.item)))
        spending.initialize(self.db)
        self.scope = ('product', 'm1', 'openai-codex', 'gpt-6.1-sol', 'responses')
        with self.db:
            spending.add_grant(self.db, *self.scope, 'allowance', 10, 2**40, 'LABELED existing allowance', now=100)
        # Rewritten tests must never silently fall back to real external execution.
        for target in ('subprocess.run', 'subprocess.Popen', 'socket.socket', 'os.pidfd_open',
                       'factory_v1.spending.transport', 'factory_v1.repositories.GitHub.request'):
            patcher = patch(target, side_effect=AssertionError('Unexpected external boundary: ' + target))
            patcher.start()
            self.addCleanup(patcher.stop)

    def pause(self, action='pause', database=None):
        return controls.request(database or self.db, 'product', 'm1', '42', action, 'LABELED interruption')

    def inspect(self):
        return controls.inspect(self.db, 'product', 'm1', '42')

    def resume(self):
        # This component isolates interruption; fresh restart observation has its
        # own exact-readback matrix in test_restart (no native precursors here).
        with patch('factory_v1.restart.reconcile', return_value={'status': 'ready'}):
            return controls.resume(self.db, 'product', 'm1', '42', 'LABELED explicit resume',
                                   Mock(base='https://api.github.com'))

    def refused(self, code, function, *args, error_class: type[RepositoryError] | type[spending.SpendingError] = RepositoryError, **kwargs):
        with self.assertRaises(error_class) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def store_assignment(self, assignment):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO role_assignments VALUES (?,?)',
                            (assignment['assignment_id'], json.dumps(assignment)))

    def stored_assignment(self, assignment_id):
        return json.loads(self.db.execute('SELECT payload FROM role_assignments WHERE assignment_id=?',
                                          (assignment_id,)).fetchone()[0])


class ControlTests(ControlFixture):
    def test_pause_checkpoint_restart_and_explicit_resume_preserve_progress_and_budget(self):
        retained = {'assignment_id': 'retained', 'ticket_scope': {'project': 'product', 'iteration': 'm1'},
                    'handoff': {'input': 'immutable'}, 'publication': {'status': 'published'}}
        self.store_assignment(retained)
        before = self.state.read_bytes()
        paused = self.pause()
        self.assertEqual(paused['state'], 'completed')
        self.assertFalse(paused['dispatch_allowed'])
        self.assertEqual(paused['checkpoint']['assignments'], [retained])
        self.assertEqual(self.state.read_bytes(), before)
        self.refused('iteration_paused', assignments.current, self.db, 'product', 'm1', '42')
        self.refused('iteration_paused', tickets.active, self.item, self.db)
        with closing(sqlite3.connect(self.state)) as fresh:
            self.assertEqual(controls.inspect(fresh, 'product', 'm1', '42')['request_id'], paused['request_id'])
        resumed = self.resume()
        self.assertTrue(resumed['dispatch_allowed'])
        self.assertEqual(resumed['history'][-1]['request_id'], paused['request_id'])
        self.assertEqual(assignments.current(self.db, 'product', 'm1', '42'), self.item)
        self.assertEqual(assignments.rows(self.db), [retained])
        self.assertEqual(self.db.execute('SELECT remaining FROM model_grants').fetchone()[0], 10)
        self.refused('control_conflict', self.resume)

    def test_operator_scope_and_private_controls_fail_closed(self):
        for function, args in ((controls.request, ('pause', 'reference')), (controls.resume, ('reference',)),
                               (controls.inspect, ())):
            self.refused('approval_required', function, self.db, 'product', 'm1', '99', *args)
            self.refused('not_found', function, self.db, 'other', 'm1', '42', *args)
        paused = self.pause()
        root, record = controls.paths(self.db, 'product', 'm1')
        assert root is not None and record is not None
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)
        raw = record.read_bytes()
        malformed = ['{malformed', '{}', '{"action":"pause","action":"resume"}']
        malformed += [json.dumps(dict(paused, **change)) for change in (
            {'operator_id': '99'}, {'project': 'other'}, {'action': 'resume', 'state': 'requested'},
            {'checkpoint': {}}, {'request_id': '../unsafe'}, {'history': None}, {'cleanup': []})]
        for content in malformed:
            with self.subTest(content=content):
                record.write_text(content)
                self.refused('invalid_control', self.resume)
                self.refused('invalid_control', self.inspect)
                self.refused('invalid_control', assignments.current, self.db, 'product', 'm1', '42')
                self.refused('invalid_control', spending.admission_preview, self.db, self.scope,
                             error_class=spending.SpendingError)
        record.write_bytes(raw)
        for mode in (0o644, 0o666):
            record.chmod(mode)
            self.refused('invalid_control', controls.require_active, self.db, 'product', 'm1')
        record.chmod(0o600)
        record.unlink()
        record.symlink_to(root / 'absent')
        self.refused('invalid_control', controls.require_active, self.db, 'product', 'm1')
        record.unlink()
        record.write_bytes(raw)
        record.chmod(0o600)
        root.chmod(0o755)
        self.refused('invalid_control', controls.require_active, self.db, 'product', 'm1')
        root.chmod(0o700)
        self.assertEqual(self.inspect()['request_id'], paused['request_id'])

    def test_pause_and_stop_do_not_wait_for_sqlite_writer_or_controller_lock(self):
        for action, state in (('pause', 'requested'), ('emergency-stop', 'acknowledged')):
            with self.subTest(action=action), ThreadPoolExecutor(max_workers=1) as pool:
                with closing(sqlite3.connect(self.state)) as writer, state_lock(writer):
                    writer.execute('BEGIN IMMEDIATE')
                    writer.execute('UPDATE iterations SET payload=payload')
                    entered = threading.Event()
                    def request():
                        with closing(sqlite3.connect(self.state, timeout=.1)) as independent:
                            entered.set()
                            return self.pause(action, independent)
                    try:
                        pending = pool.submit(request)
                        self.assertTrue(entered.wait(1))
                        response = pending.result(timeout=1)
                        self.assertEqual(response['state'], state)
                        self.assertFalse(response['dispatch_allowed'])
                        self.refused('control_busy', self.resume)
                        self.refused('iteration_paused', controls.require_active, writer, 'product', 'm1')
                    finally:
                        writer.rollback()
            self.assertEqual(self.inspect()['state'], 'completed')
            self.assertTrue(self.resume()['dispatch_allowed'])

    def test_stop_cannot_be_downgraded_or_autoresumed(self):
        first = self.pause()
        self.assertEqual(self.pause()['request_id'], first['request_id'])
        stopped = self.pause('emergency-stop')
        repeated = self.pause()
        self.assertEqual(repeated['action'], 'emergency-stop')
        self.assertEqual(repeated['request_id'], stopped['request_id'])
        self.assertEqual(self.inspect()['action'], 'emergency-stop')
        self.refused('iteration_paused', controls.require_active, self.db, 'product', 'm1')
        self.assertTrue(self.resume()['dispatch_allowed'])

    def test_broker_ownership_survives_pause_until_capability_teardown(self):
        socket_path = str(self.root / 'unused-capability.sock')
        with patch('factory_v1.sandbox_guard.process_identity', return_value='LABELED owner'), \
                controls.broker_lease(self.db, 'product', 'm1', socket_path):
            paused = self.pause()
            self.assertEqual(paused['state'], 'requested')
            self.assertEqual(paused['checkpoint']['brokers'][0]['socket'], socket_path)
            self.refused('control_busy', self.resume)
            self.refused('iteration_paused', controls.require_active, self.db, 'product', 'm1')
            self.assertFalse(Path(socket_path).exists(), 'Ownership record does not create a real socket')
        self.assertEqual(self.inspect()['checkpoint']['brokers'], [])
        self.assertTrue(self.resume()['dispatch_allowed'])

    def test_other_scope_is_independent_and_resume_cannot_steal_active_iteration(self):
        other = dict(self.item, project_id='other', iteration_id='other-m1')
        with self.db:
            self.db.execute('INSERT INTO iterations VALUES (?,?,?)', ('other', 'other-m1', json.dumps(other)))
        controls.request(self.db, 'other', 'other-m1', '42', 'pause', 'LABELED other scope')
        self.assertEqual(assignments.current(self.db, 'product', 'm1', '42'), self.item)
        self.refused('control_conflict', controls.resume, self.db, 'other', 'other-m1', '42', 'explicit resume')
        self.assertEqual(self.db.execute('SELECT remaining FROM model_grants').fetchone()[0], 10)

    def test_pending_model_receipt_is_retained_while_new_admission_and_resume_are_refused(self):
        # Only an already-admitted local reservation; no adapter/model call.
        with self.db:
            spending._prepare(self.db, self.scope, 'pending-op', b'{}', 1, 100, allowance_only=True)
        paused = self.pause('emergency-stop')
        self.assertEqual(paused['checkpoint']['pending_model_operations'], [['pending-op', 'pending', 1, None]])
        self.refused('control_busy', self.resume)
        self.refused('iteration_paused', spending.admission_preview, self.db, self.scope,
                     error_class=spending.SpendingError)
        self.assertEqual(self.db.execute('SELECT remaining FROM model_grants').fetchone()[0], 9)
        with self.db:
            receipt = spending.reconcile(self.db, *self.scope, 'pending-op', 'complete', 1, 'LABELED trusted billing receipt')
        self.assertEqual(receipt['status'], 'complete')
        self.assertFalse(self.inspect()['dispatch_allowed'])
        self.assertEqual(self.db.execute('SELECT remaining FROM model_grants').fetchone()[0], 9)
        self.assertTrue(self.resume()['dispatch_allowed'])

    def test_resume_requires_reconciled_publication_and_merge_intents(self):
        self.pause()
        for kind in ('publication', 'merge'):
            with self.subTest(kind=kind):
                record = {'assignment_id': 'pending', 'ticket_scope': {'project': 'product', 'iteration': 'm1'},
                          kind: {'status': 'pending'}}
                self.store_assignment(record)
                self.refused('control_busy', self.resume)
                record[kind]['status'] = 'published' if kind == 'publication' else 'merged'
                self.store_assignment(record)
        self.assertTrue(self.resume()['dispatch_allowed'])


class AdmissionControlTests(ControlFixture):
    def setUp(self):
        super().setUp()
        pin = '136a8987143241a0229952a56d9c41042646073769fcad466d808bd5e40bb4f0'
        self.request = {'stage': 'implementation', 'repository': 'example/product', 'repository_id': 123,
                        'workspace': str(self.root / 'workspace'), 'spec_commit': 'a' * 40,
                        'baseline': 'a' * 40, 'candidate': None, 'claim_id': 'controls-claim',
                        'issue': {'number': 10, 'id': 1010, 'node_id': 'ISSUE10',
                                  'body_sha256': hashlib.sha256(b'LABELED issue').hexdigest()},
                        'profile': {'name': 'controls-worker', 'home': str(self.root / 'profile'),
                                    'role': 'implementation', 'provider': 'openai-codex',
                                    'model': 'gpt-6.1-sol', 'operation': 'responses', 'reasoning': 'high'},
                        'standards': [artifact('AGENTS.md', 'LABELED standards')], 'preceding': [],
                        'skills': [{'name': 'implement', 'source': '/home/ops/.hermes/skills/implement/SKILL.md',
                                    'path': str(Path(__file__).parent / 'role_skill_snapshots/implement/SKILL.md'),
                                    'sha256': pin, 'dependencies': []}],
                        'capabilities': ['read_workspace', 'scratch', 'model', 'write_workspace'],
                        'result_contract': assignments.CONTRACT}
        self.github = Mock(base='https://api.github.com')
        observed = copy.deepcopy(self.item)
        observed['ticket_work'].update(publication_complete=True, board={'id': 'BOARD'},
            frontier={'foreign_active': [], 'active': [], 'items': [dict(self.request['issue'], admissible=True)]})
        patcher = patch.object(tickets, 'frontier', return_value=observed)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.issue = {'body': 'LABELED issue', 'html_url': 'https://github.com/example/product/issues/10'}

    def prepare(self, dry_run=False):
        return assignments.prepare(self.db, 'product', 'm1', '42', self.request, self.github, dry_run=dry_run)

    def result(self, prepared):
        h = prepared['handoff']
        expected = {s['source']: s['sha256'] for s in h['skills']} | h['input_loads']
        return {'assignment_id': prepared['assignment_id'], 'handoff_digest': prepared['handoff_digest'],
                'claim_id': prepared['claim_id'], 'run_id': 'controls-run', 'status': 'done',
                'loads': [{'source': source, 'sha256': pin, 'tool_reference': 'LABELED read'} for source, pin in expected.items()],
                'work': ['LABELED untrusted component input'],
                'artifacts': [artifact('stage-evidence', 'LABELED no native execution')],
                'tests': [{'command': 'LABELED command', 'result': 'LABELED input, not executed evidence'}]}

    def test_pause_on_last_issue_read_prevents_final_assignment_claim(self):
        with patch.object(tickets.gh, 'read_issue', return_value=self.issue):
            self.assertFalse(self.prepare(dry_run=True)['claim_persisted'])
        def final_read(*args):
            self.pause()
            return self.issue
        with patch.object(tickets.gh, 'read_issue', side_effect=final_read) as read:
            self.refused('iteration_paused', self.prepare)
        read.assert_called_once()
        self.assertEqual(assignments.rows(self.db), [])
        self.assertEqual(self.db.execute('SELECT remaining FROM model_grants').fetchone()[0], 10)
        self.assertTrue(self.resume()['dispatch_allowed'])
        with patch.object(tickets.gh, 'read_issue', return_value=self.issue):
            self.assertTrue(self.prepare()['claim_persisted'])

    def test_pause_on_result_revalidation_prevents_accepting_stage_evidence(self):
        from factory_v1.tests.test_assignments import AssignmentComponentTests
        # Same normalized claim constructor; only the explicit tracker input differs.
        self.item['ticket_work']['board'] = {'id': 'BOARD'}
        self.item['ticket_work'].setdefault('inputs', {'documents': {}})
        prepared = AssignmentComponentTests.seed_assignment(self, issue=self.issue)
        result = self.result(prepared)
        def final_read(*args):
            self.pause()
            return self.issue
        with patch.object(tickets.gh, 'read_issue', side_effect=final_read) as read:
            self.refused('iteration_paused', assignments.store_result, self.db, 'product', 'm1', '42',
                         prepared['assignment_id'], result, self.github)
        read.assert_called_once()
        self.assertNotIn('submitted_result', self.stored_assignment(prepared['assignment_id']))
        self.assertTrue(self.resume()['dispatch_allowed'])
        with patch.object(tickets.gh, 'read_issue', return_value=self.issue):
            stored = assignments.store_result(self.db, 'product', 'm1', '42', prepared['assignment_id'], result, self.github)
        self.assertEqual(stored['submitted_result'], result)
        self.assertFalse(stored['result_disposition']['trusted_execution'])
        self.assertFalse(stored['result_disposition']['advance_allowed'])


class CleanupControlTests(ControlFixture):
    def setUp(self):
        super().setUp()
        self.assignment = {'assignment_id': 'attempt', 'ticket_scope': {'project': 'product', 'iteration': 'm1'},
                           'runtime': {'run_id': 'controls-run', 'status': 'running', 'container': 'LABELED-container',
                                       'artifacts': str(self.root), 'controller_pid': 123,
                                       'controller_start': 'LABELED-start', 'container_removed': False}}
        self.store_assignment(self.assignment)
        patcher = patch('factory_v1.sandbox_guard.process_identity', return_value=None)
        self.identity = patcher.start()
        self.addCleanup(patcher.stop)

    def test_stop_halts_before_logs_and_removal_retains_artifacts(self):
        docker = Mock()
        docker.inspect.return_value = {'State': {'Running': False}}
        docker.call.return_value = b'LABELED retained logs'
        (self.root / 'candidate.txt').write_text('retained candidate')
        with patch('factory_v1.sandbox_policy.Docker', return_value=docker):
            response = self.pause('emergency-stop')
        self.assertEqual([c[0] for c in docker.method_calls], ['call', 'inspect', 'call', 'remove'])
        self.assertEqual(docker.call.call_args_list[0].args[0], 'kill')
        self.assertEqual(docker.call.call_args_list[1].args[0], 'logs')
        self.assertTrue(response['cleanup']['attempt']['container_removed'])
        self.assertEqual((self.root / 'container.log').read_bytes(), b'LABELED retained logs')
        self.assertEqual((self.root / 'candidate.txt').read_text(), 'retained candidate')
        self.assertTrue((self.root / 'stop').exists())
        self.assertEqual(response['state'], 'acknowledged')
        self.refused('control_busy', self.resume)

    def test_cleanup_failures_never_skip_removal_and_uncertainty_blocks_resume(self):
        for defect in ('kill', 'inspect', 'still-running', 'logs', 'artifact', 'signal', 'remove'):
            with self.subTest(defect=defect), ExitStack() as stack:
                docker = Mock()
                docker.inspect.return_value = {'State': {'Running': defect == 'still-running'}}
                docker.call.return_value = b'LABELED logs'
                if defect == 'kill':
                    docker.call.side_effect = RepositoryError('container_unavailable', 'LABELED kill error')
                elif defect == 'inspect':
                    docker.inspect.side_effect = OSError('LABELED inspect error')
                elif defect == 'logs':
                    def fail_logs(command, *args, **kwargs):
                        if command == 'logs':
                            raise RepositoryError('container_unavailable', 'LABELED log timeout')
                    docker.call.side_effect = fail_logs
                elif defect == 'artifact':
                    stack.enter_context(patch.object(Path, 'write_bytes', side_effect=PermissionError('LABELED diagnostics error')))
                elif defect == 'signal':
                    self.identity.return_value = 'LABELED-start'
                    stack.enter_context(patch.object(os, 'pidfd_open', side_effect=OSError('LABELED signal error')))
                elif defect == 'remove':
                    docker.remove.side_effect = RepositoryError('stop_unconfirmed', 'LABELED removal uncertain')
                stack.enter_context(patch('factory_v1.sandbox_policy.Docker', return_value=docker))
                response = self.pause('emergency-stop')
                outcome = response['cleanup']['attempt']
                docker.remove.assert_called_once_with('LABELED-container')
                self.assertEqual(outcome['container_removed'], defect != 'remove')
                self.assertEqual(outcome['error'], 'stop_unconfirmed' if defect in {'kill', 'inspect', 'still-running', 'remove'} else None)
                if defect in {'kill', 'inspect', 'still-running'}:
                    self.assertNotIn('logs', [c.args[0] for c in docker.call.call_args_list])
                if defect == 'signal':
                    self.assertEqual(outcome['controller_signal_error'], 'stop_unconfirmed')
                self.refused('control_busy', self.resume)
                self.assertNotIn('submitted_result', self.stored_assignment('attempt'))
                self.identity.return_value = None

    def test_unconfirmed_runtime_requires_explicit_cleanup_readback_before_resume(self):
        self.assignment['runtime'].update(status='unconfirmed', error='stop_unconfirmed')
        self.store_assignment(self.assignment)
        self.pause()
        self.refused('control_busy', self.resume)
        self.assignment['runtime'].update(status='stopped', container_removed=True)
        self.store_assignment(self.assignment)
        self.refused('control_busy', self.resume)
        self.assignment['runtime']['error'] = 'iteration_paused'
        self.store_assignment(self.assignment)
        self.assertEqual(self.inspect()['state'], 'completed')
        self.assertTrue(self.resume()['dispatch_allowed'])


class MergeControlTests(ControlFixture):
    def setUp(self):
        super().setUp()
        self.assignment = {'assignment_id': 'c' * 64, 'revision': {'spec': self.item['handoff']},
                           'ticket_scope': {'project': 'product', 'iteration': 'm1'},
                           'handoff': {'repository': 'example/product', 'repository_id': 123,
                                       'issue': {'number': 10, 'id': 1010, 'node_id': 'ISSUE10',
                                                 'body_sha256': hashlib.sha256(b'LABELED issue').hexdigest()},
                                       'workspace': str(self.root / 'workspace'),
                                       'profile': {'home': str(self.root / 'profile')}}}
        self.store_assignment(self.assignment)
        self.protection = {'required_status_checks': {'strict': True, 'checks': [{'context': 'unit', 'app_id': 1}]},
                           'enforce_admins': {'enabled': True}, 'allow_force_pushes': {'enabled': False},
                           'allow_deletions': {'enabled': False}}
        self.config = {'operator_id': '42', 'execution_reference': 'LABELED mocked prerequisite chain',
                       'executions': [{}, {}, {}, {}], 'candidate_assignment': 'c' * 64,
                       'standards_assignment': 'd' * 64, 'spec_assignment': 'e' * 64,
                       'repository': 'example/product', 'repository_id': 123, 'base': 'main', 'branch': 'candidate',
                       'pr_number': 7, 'head': 'b' * 40, 'baseline': 'a' * 40, 'issue': 10,
                       'api_base': 'https://api.github.com', 'bearer': 'LABELED inert token',
                       'required_checks': [{'context': 'unit', 'app_id': 1}],
                       'protection_sha256': assignments.digest({'rules': [], 'protection': self.protection}), 'recovery': None}
        self.config_path = self.root / 'merge.json'
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)
        self.state.chmod(0o600)
        self.pr = {'number': 7, 'head': {'ref': 'candidate', 'sha': 'b' * 40,
                                      'repo': {'id': 123, 'full_name': 'example/product'}},
                   'base': {'ref': 'main', 'sha': 'a' * 40, 'repo': {'id': 123, 'full_name': 'example/product'}},
                   'state': 'open', 'merged': False, 'draft': False, 'mergeable': True, 'mergeable_state': 'clean'}
        self.api = Mock(prefix='/repos/example/product')
        self.api.github.base = 'https://api.github.com'
        self.api.read.side_effect = self.read_metadata
        self.api.request.side_effect = self.merge_effect
        self.boundary = None
        self.after_merge = None
        # Antecedent engineering/requirement/delivery evidence is outside this
        # controls test. Keep real target/check/protection and final controls guards.
        for target, result in ((merge, ('MergeGitHub', self.api)),
                               (merge, ('chain', (self.assignment, {}, self.root))),
                               (merge, ('mapped_candidate', {'tree': 'f' * 40, 'base_tree': '0' * 40})),
                               (merge.review, ('evaluate', {'status': 'accepted'})),
                               (assignments, ('ticket_claims', [])),
                               (delivery, ('mutation_boundary', self.item))):
            patcher = patch.object(target, result[0], return_value=result[1])
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(merge, 'require_publication', side_effect=self.requirement_read)
        self.publication_reads = patcher.start()
        self.addCleanup(patcher.stop)

    def requirement_read(self, *args):
        if self.publication_reads.call_count == 2 and self.boundary:
            self.boundary()

    def read_metadata(self, suffix):
        if suffix == '':
            return {'id': 123, 'full_name': 'example/product', 'default_branch': 'main'}
        if suffix == '/pulls/7':
            return copy.deepcopy(self.pr)
        if suffix == '/issues/10':
            return {'id': 1010, 'node_id': 'ISSUE10', 'number': 10, 'state': 'open',
                    'body': 'LABELED issue', 'repository_url': 'https://api.github.com/repos/example/product'}
        if suffix == '/rules/branches/main':
            return []
        if suffix == '/branches/main/protection':
            return self.protection
        if suffix.startswith('/commits/'):
            sha = suffix.split('/')[2]
            return {'total_count': 1, 'check_runs': [{'id': 1, 'name': 'unit', 'app': {'id': 1},
                                                   'head_sha': sha, 'status': 'completed', 'conclusion': 'success'}]}
        if suffix.startswith('/git/ref/heads/'):
            branch = suffix.rsplit('/', 1)[1]
            sha = ('9' * 40 if self.pr['merged'] else 'a' * 40) if branch == 'main' else 'b' * 40
            return {'ref': 'refs/heads/' + branch, 'object': {'type': 'commit', 'sha': sha}}
        if suffix == '/git/commits/' + '9' * 40:
            return {'sha': '9' * 40, 'tree': {'sha': 'f' * 40}, 'parents': [{'sha': 'a' * 40}, {'sha': 'b' * 40}]}
        if suffix.startswith('/compare/'):
            return {'status': 'identical', 'base_commit': {'sha': '9' * 40}, 'merge_base_commit': {'sha': '9' * 40}}
        raise AssertionError('Unexpected metadata request: ' + suffix)

    def merge_effect(self, path, body, method):
        self.assertEqual((path, body, method), ('/repos/example/product/pulls/7/merge',
                                               {'sha': 'b' * 40, 'merge_method': 'merge'}, 'PUT'))
        self.pr.update(merged=True, state='closed', merge_commit_sha='9' * 40)
        if self.after_merge:
            self.after_merge()
        return {'merged': True}

    def lifecycle(self, action='merge-candidate'):
        return merge.lifecycle(self.db, 'product', 'm1', '42', self.config_path, action)

    def test_pause_after_last_requirement_read_prevents_intent_and_merge_write(self):
        self.boundary = self.pause
        self.refused('iteration_paused', self.lifecycle)
        self.assertEqual(self.publication_reads.call_count, 2)
        self.api.request.assert_not_called()
        stored = self.stored_assignment(self.assignment['assignment_id'])
        self.assertNotIn('merge', stored)
        self.assertEqual(stored['merge_refusal']['code'], 'iteration_paused')
        self.assertTrue(self.resume()['dispatch_allowed'])
        self.boundary = None
        self.publication_reads.reset_mock()
        self.assertEqual(self.lifecycle()['status'], 'merged')
        self.api.request.assert_called_once()

    def test_pause_after_intent_commit_prevents_final_put_without_erasing_intent(self):
        from factory_v1 import readiness_gate
        original = readiness_gate.admission
        interrupted = []
        @contextmanager
        def after_admission(*args, **kwargs):
            with original(*args, **kwargs):
                yield
            # Commit has completed and the real interruption lane is released.
            journal = self.stored_assignment(self.assignment['assignment_id']).get('merge')
            if journal and journal['status'] == 'pending' and journal['failure'] is None and not interrupted:
                interrupted.append(self.pause())
        with patch.object(readiness_gate, 'admission', after_admission):
            self.refused('iteration_paused', self.lifecycle)
        self.assertEqual(len(interrupted), 1)
        self.assertFalse(interrupted[0]['dispatch_allowed'])
        self.api.request.assert_not_called()
        stored = self.stored_assignment(self.assignment['assignment_id'])['merge']
        self.assertEqual(stored['status'], 'pending')
        self.assertEqual(stored['failure']['code'], 'iteration_paused')
        self.assertIsNone(stored['integrated_evidence'])
        self.refused('control_busy', self.resume)

    def test_pause_during_last_issue_read_prevents_merge_mutation(self):
        original = merge.issue
        interrupted = []
        def final_issue(*args):
            observed = original(*args)
            if self.publication_reads.call_count == 2 and not interrupted:
                interrupted.append(self.pause())
            return observed
        with patch.object(merge, 'issue', side_effect=final_issue):
            self.refused('iteration_paused', self.lifecycle)
        self.assertEqual(len(interrupted), 1)
        self.assertFalse(interrupted[0]['dispatch_allowed'])
        self.api.request.assert_not_called()
        self.assertNotIn('merge', self.stored_assignment(self.assignment['assignment_id']))

    def test_stop_during_admitted_merge_retains_receipt_without_closure_or_replay(self):
        self.after_merge = lambda: self.pause('emergency-stop')
        journal = self.lifecycle()
        self.assertEqual(journal['status'], 'merged')
        self.assertEqual(journal['integrated_evidence']['integrated'], '9' * 40)
        self.assertFalse(journal['close_allowed'])
        self.assertFalse(self.inspect()['external_effects_undone'])
        self.assertEqual(self.stored_assignment(self.assignment['assignment_id'])['merge']['integrated_evidence'],
                         journal['integrated_evidence'])
        self.refused('iteration_paused', self.lifecycle, 'verify-delivery')
        self.api.request.assert_called_once()
        self.api.issue_state.assert_not_called()
        self.assertFalse(self.inspect()['dispatch_allowed'])


if __name__ == '__main__':
    unittest.main()
