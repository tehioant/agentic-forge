"""One deterministic stage at a time; precursor reads and external work are mocked."""
from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

import os
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
