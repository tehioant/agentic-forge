"""One deterministic stage at a time; precursor reads and external work are mocked."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

import threading
import signal
import subprocess

from factory_v1 import service_dispatch as dispatch
from factory_v1.repositories import RepositoryError


class DispatchSelectionTests(unittest.TestCase):
    def setUp(self):
        self.config = {'implementation': {'claim_id': 'seed'}, 'roles': {}}
        self.item = {'planning': {'status': 'complete'},
                     'ticket_work': {'status': 'published', 'publication_complete': True}}
        self.seed = {'assignment_id': 'a' * 64, 'claim_id': 'seed', 'handoff': {'stage': 'implementation'}}

    def test_pending_interview_reports_missing_conversational_executor(self):
        self.item['planning']['status'] = 'pending'
        self.assertEqual(dispatch.select(self.item, [], self.config), ('blocked', 'same_conversation_executor_unavailable'))

    def test_complete_interview_automatically_selects_ticket_synthesis(self):
        self.item.pop('ticket_work')
        self.assertEqual(dispatch.select(self.item, [], self.config), ('synthesize', None))

    def test_prepared_implementation_selects_launch_not_worker_claim(self):
        self.seed['submitted_result'] = {'status': 'done'}
        self.assertEqual(dispatch.select(self.item, [self.seed], self.config), ('launch', self.seed['assignment_id']))

    def test_any_retained_attempt_prevents_automatic_relaunch(self):
        for status in ('preparing', 'running', 'failed', 'stopped', 'unconfirmed'):
            with self.subTest(status=status):
                self.seed['runtime'] = {'status': status}
                self.assertEqual(dispatch.select(self.item, [self.seed], self.config), ('blocked', 'attempt_requires_reconciliation'))

    def test_completed_implementation_selects_real_simplification_seam(self):
        self.seed['runtime'] = {'status': 'complete'}
        self.assertEqual(dispatch.select(self.item, [self.seed], self.config), ('simplify', self.seed['assignment_id']))

    def test_each_review_axis_is_separate(self):
        self.seed['runtime'] = {'status': 'complete'}
        simplified = {'assignment_id': 'b' * 64, 'claim_id': 'simple', 'runtime': {'status': 'complete'},
                      'handoff': {'stage': 'simplify', 'simplification': {'implementation_assignment': self.seed['assignment_id']}}}
        selected = dispatch.select(self.item, [self.seed, simplified], self.config)
        self.assertEqual(selected, ('review-standards', simplified['assignment_id']))
        standards = {'assignment_id': 'c' * 64, 'claim_id': 'standards', 'runtime': {'status': 'complete'},
                     'handoff': {'stage': 'review-standards', 'review': {'pins': {'candidate_assignment': simplified['assignment_id']}}}}
        self.assertEqual(dispatch.select(self.item, [self.seed, simplified, standards], self.config),
                         ('review-spec', simplified['assignment_id']))

    def test_ambiguous_seed_claim_fails_closed(self):
        other = deepcopy(self.seed)
        other['assignment_id'] = 'd' * 64
        with self.assertRaises(RepositoryError):
            dispatch.select(self.item, [self.seed, other], self.config)


class DispatchBoundaryTests(unittest.TestCase):
    def test_installed_source_cannot_be_replaced_by_pinned_snapshot(self):
        selected = [{'name': 'implement', 'source': '/installed/SKILL.md', 'path': '/snapshot/SKILL.md',
                     'sha256': 'a' * 64, 'dependencies': []}]
        with self.assertRaises(RepositoryError) as error:
            dispatch.installed('implementation', selected)
        self.assertEqual(error.exception.code, 'skill_blocked')

    def test_review_refuses_worker_claim_without_independent_host_provenance(self):
        db = Mock()
        config = {'operator_id': 'operator', 'project': 'p', 'iteration': 'i', 'review_config': None}
        axes = {'standards_assignment': 'a' * 64, 'spec_assignment': 'b' * 64}
        with patch.object(dispatch.review, 'evaluate', return_value={'advance_allowed': False, 'status': 'execution-unverified'}) as evaluate:
            result = dispatch.evaluate(db, config, Mock(), axes)
        self.assertEqual(result['blockers'], ['review_execution-unverified'])
        evaluate.assert_called_once()

    def test_public_launcher_uses_an_owned_child_not_the_service_pid(self):
        process = Mock()
        process.wait.return_value = 0
        config = {'state': '/private/controller.sqlite', 'operator_id': 'operator', 'project': 'p', 'iteration': 'i',
                  'api_base': 'https://api.github.com'}
        from contextlib import nullcontext
        with patch('factory_v1.service.connect', return_value=nullcontext(Mock())), \
             patch.object(dispatch.controls, 'admission', return_value=nullcontext()), \
             patch.object(dispatch.subprocess, 'Popen', return_value=process) as launch, \
             patch.object(dispatch.controls, 'require_active'), \
             patch.dict(dispatch.os.environ, {'HERMES_HOME': '/secret', 'GH_TOKEN': 'secret',
                 'HTTPS_PROXY': 'secret', 'PYTHONPATH': '/unrelated', 'DOCKER_HOST': 'unix:///approved'}, clear=True):
            dispatch.run_assignment(config, 'a' * 64, '/private/launcher.json')
        argv = launch.call_args.args[0]
        self.assertEqual(argv[1:3], ['-m', 'factory_v1'])
        self.assertIn('launch-assignment', argv)
        self.assertIn('/private/launcher.json', argv)
        self.assertEqual(launch.call_args.kwargs['env'], {'DOCKER_HOST': 'unix:///approved'})
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        self.assertEqual(argv[3:], ['--state', config['state'], '--operator-id', 'operator',
            'launch-assignment', '--project', 'p', '--iteration', 'i', '--assignment', 'a' * 64,
            '--launcher-config', '/private/launcher.json', '--api-base', config['api_base']])
        process.wait.assert_called_once()

    def test_host_synthesis_cancellation_joins_owned_process_group(self):
        process = Mock(pid=12345)
        process.communicate.side_effect = subprocess.TimeoutExpired('approved-executor', 0.1)
        config = {'argv': ['/approved/executor'], 'timeout': 10}
        with patch.object(dispatch.requirement_execution.subprocess, 'Popen', return_value=process), \
             patch.object(dispatch.requirement_execution.os, 'killpg') as kill:
            with self.assertRaises(RepositoryError) as error:
                dispatch.requirement_execution.invoke(config, {}, cancelled=Mock(side_effect=[False, False, True]))
        self.assertEqual(error.exception.code, 'execution_uncertain')
        kill.assert_called_once_with(12345, signal.SIGKILL)
        process.wait.assert_called_once()

    def test_already_cancelled_synthesis_never_creates_a_process(self):
        with patch.object(dispatch.requirement_execution.subprocess, 'Popen') as launch:
            with self.assertRaises(RepositoryError):
                dispatch.requirement_execution.invoke({'argv': ['/approved/executor'], 'timeout': 10}, {}, cancelled=lambda: True)
        launch.assert_not_called()

    def test_unsupported_capability_is_not_a_shell_plugin(self):
        config = {'operator_id': 'operator', 'project': 'p', 'iteration': 'i', 'reference': 'host:1',
                  'implementation': None, 'roles': {}, 'launchers': {}, 'tickets': None,
                  'synthesis_config': None, 'review_config': None, 'publication_config': None, 'merge_config': None,
                  'argv': ['/bin/true']}
        with self.assertRaises(RepositoryError):
            dispatch.validate(config, {'operator_id': 'operator', 'project': 'p', 'iteration': 'i'})

class OwnedLauncherTests(unittest.TestCase):
    def setUp(self):
        from contextlib import nullcontext
        self.service = {'state': '/private/state.sqlite', 'operator_id': 'operator',
                        'project': 'p', 'iteration': 'i', 'api_base': 'https://api.github.com'}
        self.event = threading.Event()
        self.process = Mock(pid=12345)
        self.process.wait.return_value = 0
        self.launch = self.enterContext(patch.object(dispatch.subprocess, 'Popen', return_value=self.process))
        self.kill = self.enterContext(patch.object(dispatch.os, 'killpg'))
        self.enterContext(patch('factory_v1.service.connect', side_effect=lambda _: nullcontext(Mock())))
        self.enterContext(patch.object(dispatch.controls, 'admission', side_effect=lambda *a: nullcontext()))
        self.active = self.enterContext(patch.object(dispatch.controls, 'require_active'))
        self.hold = self.enterContext(patch.object(dispatch.controls, 'request'))

    def run_child(self):
        dispatch.run_assignment(self.service, 'a' * 64, '/private/launcher.json', stop_event=self.event)

    def test_shutdown_before_spawn_refuses_without_child(self):
        self.event.set()
        with self.assertRaises(RepositoryError) as error:
            self.run_child()
        self.assertEqual(error.exception.code, 'service_stopping')
        self.launch.assert_not_called()
        self.kill.assert_not_called()

    def test_shutdown_joins_child_after_term(self):
        def waiting(**kwargs):
            self.event.set()
            raise subprocess.TimeoutExpired('child', .1)
        def wait(**kwargs):
            if not self.event.is_set():
                return waiting(**kwargs)
            return 0
        self.process.wait.side_effect = wait
        with self.assertRaises(RepositoryError) as error:
            self.run_child()
        self.assertEqual(error.exception.code, 'service_stopping')
        self.kill.assert_called_once_with(12345, signal.SIGTERM)
        self.assertEqual(self.process.wait.call_args.kwargs, {'timeout': 35})

    def test_term_timeout_escalates_to_kill_and_join(self):
        self.active.side_effect = RepositoryError('iteration_paused', 'paused')
        self.process.wait.side_effect = [subprocess.TimeoutExpired('child', 70), 0]
        with self.assertRaises(RepositoryError) as error:
            self.run_child()
        self.assertEqual(error.exception.code, 'iteration_paused')
        self.assertEqual([c.args for c in self.kill.call_args_list],
                         [(12345, signal.SIGTERM), (12345, signal.SIGKILL)])
        self.assertEqual(self.process.wait.call_args.kwargs, {'timeout': 5})

    def test_unjoinable_child_overrides_pause_with_uncertainty(self):
        self.active.side_effect = RepositoryError('iteration_paused', 'paused')
        self.process.wait.side_effect = subprocess.TimeoutExpired('child', 70)
        self.kill.side_effect = OSError('signal unavailable')
        with self.assertRaises(RepositoryError) as error:
            self.run_child()
        self.assertEqual(error.exception.code, 'execution_uncertain')
        self.hold.assert_called_once()
        self.assertEqual(self.hold.call_args.args[4], 'pause')
        self.assertEqual([c.args for c in self.kill.call_args_list],
                         [(12345, signal.SIGTERM), (12345, signal.SIGKILL)])
        self.assertEqual(self.process.wait.call_args.kwargs, {'timeout': 5})

    def test_overall_deadline_terminates_and_joins(self):
        with patch.object(dispatch.time, 'monotonic', side_effect=[0, 3901]):
            with self.assertRaises(RepositoryError) as error:
                self.run_child()
        self.assertEqual(error.exception.code, 'execution_uncertain')
        self.kill.assert_called_once_with(12345, signal.SIGTERM)
        self.process.wait.assert_called_once_with(timeout=35)

    def test_nonzero_child_exit_is_not_success(self):
        self.process.wait.return_value = 1
        with self.assertRaises(RepositoryError) as error:
            self.run_child()
        self.assertEqual(error.exception.code, 'execution_uncertain')
        self.kill.assert_not_called()

    def test_spawn_failure_is_structured_and_never_signals(self):
        self.launch.side_effect = OSError('unavailable')
        with self.assertRaises(RepositoryError) as error:
            self.run_child()
        self.assertEqual(error.exception.code, 'execution_uncertain')
        self.kill.assert_not_called()
