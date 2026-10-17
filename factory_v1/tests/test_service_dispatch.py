"""Direct dispatch selection and owned-child boundaries; no process is started."""
from contextlib import nullcontext
from copy import deepcopy
import signal
import subprocess
import threading
import unittest
from unittest.mock import Mock, call, patch

from factory_v1 import service_dispatch as dispatch
from factory_v1.repositories import RepositoryError


class DispatchSelectionTests(unittest.TestCase):
    def setUp(self):
        self.config = {'implementation': {'claim_id': 'seed'}, 'roles': {}}
        self.item = {'planning': {'status': 'complete'},
                     'ticket_work': {'status': 'published', 'publication_complete': True}}
        self.seed = {'assignment_id': 'a' * 64, 'claim_id': 'seed', 'handoff': {'stage': 'implementation'}}

    def test_selection_interview_implementation_and_axes_matrix(self):
        completed = self.seed | {'runtime': {'status': 'complete'}}
        simplified = {'assignment_id': 'b' * 64, 'claim_id': 'simple', 'runtime': {'status': 'complete'},
                      'handoff': {'stage': 'simplify', 'simplification': {'implementation_assignment': self.seed['assignment_id']}}}
        standards = {'assignment_id': 'c' * 64, 'claim_id': 'standards', 'runtime': {'status': 'complete'},
                     'handoff': {'stage': 'review-standards', 'review': {'pins': {'candidate_assignment': simplified['assignment_id']}}}}
        cases = [
            ('origin', self.item | {'planning': {'status': 'pending'}}, [], ('blocked', 'origin_conversation_pending')),
            ('synthesis', {'planning': self.item['planning']}, [], ('synthesize', None)),
            ('untrusted-result', self.item, [self.seed | {'submitted_result': {'status': 'done'}}], ('launch', self.seed['assignment_id'])),
            ('simplify', self.item, [completed], ('simplify', self.seed['assignment_id'])),
            ('standards', self.item, [completed, simplified], ('review-standards', simplified['assignment_id'])),
            ('spec', self.item, [completed, simplified, standards], ('review-spec', simplified['assignment_id'])),
        ]
        for kind in ('correctness', 'behavior', 'out-of-scope'):
            findings = deepcopy(simplified)
            findings['handoff']['simplification']['scope'] = ['app.py']
            findings['simplification_result'] = {'outcome': 'findings', 'findings': [{'kind': kind, 'path': 'app.py'}]}
            cases.append((kind, self.item, [completed, findings], ('simplification-direction', findings['assignment_id'])))
        for label, item, rows, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(dispatch.select(deepcopy(item), deepcopy(rows), self.config), expected)

    def test_retained_or_ambiguous_claim_no_relaunch_matrix(self):
        other = self.seed | {'assignment_id': 'd' * 64}
        cases = [(status, [self.seed | {'runtime': {'status': status}}])
                 for status in ('preparing', 'running', 'failed', 'stopped', 'unconfirmed')]
        cases.append(('ambiguous', [self.seed, other]))
        for label, rows in cases:
            with self.subTest(case=label):
                if label == 'ambiguous':
                    with self.assertRaises(RepositoryError):
                        dispatch.select(self.item, rows, self.config)
                else:
                    self.assertEqual(dispatch.select(self.item, rows, self.config),
                                     ('blocked', 'attempt_requires_reconciliation'))


class DispatchBoundaryTests(unittest.TestCase):
    def test_installed_source_and_supported_capability_matrix(self):
        selected = [{'name': 'implement', 'source': '/installed/SKILL.md', 'path': '/snapshot/SKILL.md',
                     'sha256': 'a' * 64, 'dependencies': []}]
        config = {'operator_id': 'operator', 'project': 'p', 'iteration': 'i', 'reference': 'host:1',
                  'implementation': None, 'roles': {}, 'launchers': {}, 'tickets': None,
                  'synthesis_config': None, 'review_config': None, 'publication_config': None, 'merge_config': None,
                  'argv': ['/bin/true']}
        for label, seam, args, code in [
            ('source', dispatch.installed, ('implementation', selected), 'skill_blocked'),
            ('shell-capability', dispatch.validate, (config, {'operator_id': 'operator', 'project': 'p', 'iteration': 'i'}), None),
        ]:
            with self.subTest(case=label), self.assertRaises(RepositoryError) as error:
                seam(*args)
            if code is not None:
                self.assertEqual(error.exception.code, code)

    def test_review_requires_host_provenance_matrix(self):
        config = {'operator_id': 'operator', 'project': 'p', 'iteration': 'i', 'review_config': None}
        axes = {'standards_assignment': 'a' * 64, 'spec_assignment': 'b' * 64}
        with patch.object(dispatch.review, 'evaluate', return_value={'advance_allowed': False, 'status': 'execution-unverified'}) as evaluate:
            result = dispatch.evaluate(Mock(), config, Mock(), axes)
        self.assertEqual(result['blockers'], ['review_execution-unverified'])
        evaluate.assert_called_once()

    def test_public_launcher_owns_child_pid_matrix(self):
        process = Mock()
        process.wait.return_value = 0
        config = {'state': '/private/controller.sqlite', 'operator_id': 'operator', 'project': 'p', 'iteration': 'i',
                  'api_base': 'https://api.github.com'}
        with patch('factory_v1.service.connect', return_value=nullcontext(Mock())), \
             patch.object(dispatch.controls, 'admission', return_value=nullcontext()), \
             patch.object(dispatch.subprocess, 'Popen', return_value=process) as launch, \
             patch.object(dispatch.controls, 'require_active'), \
             patch.dict(dispatch.os.environ, {'HERMES_HOME': '/secret', 'GH_TOKEN': 'secret',
                 'HTTPS_PROXY': 'secret', 'PYTHONPATH': '/unrelated', 'DOCKER_HOST': 'unix:///approved'}, clear=True):
            dispatch.run_assignment(config, 'a' * 64, '/private/launcher.json')
        argv = launch.call_args.args[0]
        self.assertEqual(argv[1:3], ['-m', 'factory_v1'])
        self.assertEqual(argv[3:], ['--state', config['state'], '--operator-id', 'operator',
            'launch-assignment', '--project', 'p', '--iteration', 'i', '--assignment', 'a' * 64,
            '--launcher-config', '/private/launcher.json', '--api-base', config['api_base']])
        self.assertEqual(launch.call_args.kwargs['env'], {'DOCKER_HOST': 'unix:///approved'})
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        process.wait.assert_called_once()


class FocusedInvariantMatrices(unittest.TestCase):
    def test_pre_spawn_shutdown_matrix(self):
        event = threading.Event()
        event.set()
        config = {'state': '/private/state.sqlite', 'operator_id': 'operator',
                  'project': 'p', 'iteration': 'i', 'api_base': 'https://api.github.com'}
        for label, seam, args, kwargs, code in [
            ('synthesis', dispatch.requirement_execution.invoke,
             ({'argv': ['/approved/executor'], 'timeout': 10}, {}), {'cancelled': lambda: True}, None),
            ('assignment', dispatch.run_assignment, (config, 'a' * 64, '/private/launcher.json'),
             {'stop_event': event}, 'service_stopping'),
        ]:
            with self.subTest(case=label), patch.object(dispatch.subprocess, 'Popen') as launch, \
                 patch.object(dispatch.os, 'killpg') as kill, \
                 patch('factory_v1.service.connect', side_effect=lambda _: nullcontext(Mock())), \
                 patch.object(dispatch.controls, 'admission', side_effect=lambda *a, **kwargs: nullcontext()), \
                 patch.object(dispatch.controls, 'require_active'), patch.object(dispatch.controls, 'request'):
                with self.assertRaises(RepositoryError) as error:
                    seam(*args, **kwargs)
                if code:
                    self.assertEqual(error.exception.code, code)
                launch.assert_not_called()
                kill.assert_not_called()


class OwnedLauncherTests(unittest.TestCase):
    def setUp(self):
        self.service = {'state': '/private/state.sqlite', 'operator_id': 'operator',
                        'project': 'p', 'iteration': 'i', 'api_base': 'https://api.github.com'}
        self.event = threading.Event()
        self.process = Mock(pid=12345)
        self.process.wait.return_value = 0
        self.launch = self.enterContext(patch.object(dispatch.subprocess, 'Popen', return_value=self.process))
        self.kill = self.enterContext(patch.object(dispatch.os, 'killpg'))
        self.enterContext(patch('factory_v1.service.connect', side_effect=lambda _: nullcontext(Mock())))
        def admission(database, project, iteration, *, selection):
            self.assertEqual((project, iteration), ('p', 'i'))
            self.assertEqual(selection, {'assignment_id': 'a' * 64, 'run_id': None,
                                         'launcher': '/private/launcher.json'})
            return nullcontext()
        self.enterContext(patch.object(dispatch.controls, 'admission', side_effect=admission))
        self.active = self.enterContext(patch.object(dispatch.controls, 'require_active'))
        self.hold = self.enterContext(patch.object(dispatch.controls, 'request'))

    def run_child(self):
        dispatch.run_assignment(self.service, 'a' * 64, '/private/launcher.json', stop_event=self.event)

    def test_term_kill_join_deadline_and_uncertainty_matrix(self):
        expired = subprocess.TimeoutExpired('child', 70)
        cases = [
            ('shutdown', None, None, None, 'service_stopping', [signal.SIGTERM], 35),
            ('escalation', RepositoryError('iteration_paused', 'paused'), [expired, 0], None,
             'iteration_paused', [signal.SIGTERM, signal.SIGKILL], 5),
            ('unjoinable', RepositoryError('iteration_paused', 'paused'), expired, OSError('signal unavailable'),
             'execution_uncertain', [signal.SIGTERM, signal.SIGKILL], 5),
            ('deadline', None, None, None, 'execution_uncertain', [signal.SIGTERM], 35),
        ]
        for label, active_error, waits, signal_error, code, signals, timeout in cases:
            with self.subTest(case=label):
                self.event.clear()
                for mock in (self.process, self.launch, self.kill, self.active, self.hold):
                    mock.reset_mock(side_effect=True)
                self.process.wait.return_value = 0
                self.active.side_effect = active_error
                self.kill.side_effect = signal_error
                self.process.wait.side_effect = waits
                if label == 'shutdown':
                    def wait(**kwargs):
                        if not self.event.is_set():
                            self.event.set()
                            raise subprocess.TimeoutExpired('child', .1)
                        return 0
                    self.process.wait.side_effect = wait
                clock = patch.object(dispatch.time, 'monotonic', side_effect=[0, 3901]) if label == 'deadline' else nullcontext()
                with clock, self.assertRaises(RepositoryError) as error:
                    self.run_child()
                self.assertEqual(error.exception.code, code)
                self.assertEqual(self.kill.call_args_list, [call(12345, sig) for sig in signals])
                self.assertEqual(self.process.wait.call_args.kwargs, {'timeout': timeout})
                if label == 'deadline':
                    self.process.wait.assert_called_once_with(timeout=35)
                self.hold.assert_called_once()
                self.assertEqual(self.hold.call_args.args[4], 'pause')

    def test_nonzero_and_spawn_failure_matrix(self):
        for label, exit_code, spawn_error in [('nonzero', 1, None), ('spawn', 0, OSError('unavailable'))]:
            with self.subTest(case=label):
                self.launch.reset_mock(side_effect=True)
                self.kill.reset_mock()
                self.process.wait.return_value = exit_code
                self.launch.side_effect = spawn_error
                with self.assertRaises(RepositoryError) as error:
                    self.run_child()
                self.assertEqual(error.exception.code, 'execution_uncertain')
                self.kill.assert_not_called()
