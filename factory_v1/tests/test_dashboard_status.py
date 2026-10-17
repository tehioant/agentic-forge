"""Focused pure projections; no database, launcher or controller setup."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from factory_v1.dashboard_status import project_worker_status


REPOSITORY = 'owner/product'


def completed():
    return {
        'assignment_id': 'assignment-1', 'status': 'prepared',
        'handoff': {'repository': REPOSITORY}, 'ticket_scope': {'issue_number': 62},
        'runtime': {'run_id': 'run-1', 'status': 'complete', 'container_removed': True, 'error': None},
        'submitted_result': {'assignment_id': 'assignment-1', 'run_id': 'run-1', 'status': 'done'},
        'result_disposition': {'structurally_valid': True, 'isolated_execution': True,
                               'checks_verified': False, 'trusted_execution': False,
                               'advance_allowed': False, 'close_allowed': False},
    }


class WorkerStatusTests(unittest.TestCase):
    def test_recorded_state_matrix(self):
        successful = completed()
        cases = [
            ('missing', {}, 'unknown', 'Unknown', None),
            ('malformed-runtime', {'status': 'prepared', 'runtime': []}, 'unknown', 'Unknown', None),
            ('prepared-not-launched', {'status': 'prepared'}, 'waiting', 'Waiting (prepared; not launched)',
             'Prepared assignment; no recorded worker attempt.'),
            ('profile-not-worker', {'handoff': {'profile': {'role': 'builder'}}, 'status': 'running'}, 'unknown', 'Unknown', None),
            ('successful-history-not-ticket-delivery', successful, 'done', 'Done (recorded assignment)',
             'Assignment completed; ticket delivery is separate.'),
        ]
        for status, state, label, reason in [
            ('running', 'running', 'Running (recorded; not live-verified)', None),
            ('preparing', 'waiting', 'Waiting (recorded preparation)', 'Attempt preparation recorded; worker start unconfirmed.'),
            ('failed', 'failed', 'Failed (recorded)', None),
            ('stopped', 'stopped', 'Stopped (recorded)', None),
            ('unconfirmed', 'unconfirmed', 'Unconfirmed', 'Worker cleanup is unconfirmed.'),
            ('future-status', 'unknown', 'Unknown', None),
        ]:
            row = deepcopy(successful)
            row['runtime'].update(status=status, container_removed=status in ('failed', 'stopped'))
            cases.append((status, row, state, label, reason))
        for outcome in ('blocked', 'stuck'):
            row = deepcopy(successful)
            row['submitted_result']['status'] = outcome
            row['result_disposition']['isolated_execution'] = False
            cases.append((outcome, row, 'waiting', f'Waiting ({outcome} result)', f'Worker reported a {outcome} outcome.'))
        for name, field, changes, state, label, reason in [
            ('cleanup-missing', 'runtime', {'container_removed': None}, 'unconfirmed', 'Unconfirmed', 'Worker cleanup is unconfirmed.'),
            ('cleanup-truthy-not-boolean', 'runtime', {'container_removed': 1}, 'unconfirmed', 'Unconfirmed', 'Worker cleanup is unconfirmed.'),
            ('cleanup-error', 'runtime', {'error': 'stop_unconfirmed'}, 'unconfirmed', 'Unconfirmed', 'Worker cleanup is unconfirmed.'),
            ('wrong-run', 'submitted_result', {'run_id': 'other'}, 'unknown', 'Unknown', 'Successful assignment outcome is not confirmed.'),
            ('wrong-assignment', 'submitted_result', {'assignment_id': 'other'}, 'unknown', 'Unknown', 'Successful assignment outcome is not confirmed.'),
            ('manual-result-not-execution', 'result_disposition', {'isolated_execution': False, 'trusted_execution': True, 'advance_allowed': True, 'close_allowed': True}, 'unknown', 'Unknown', 'Successful assignment outcome is not confirmed.'),
            ('not-admitted', 'result_disposition', {'structurally_valid': False}, 'unknown', 'Unknown', 'Successful assignment outcome is not confirmed.'),
            ('unknown-error-not-leaked', 'runtime', {'status': 'failed', 'error': 'TOKEN /private/log <script>'}, 'failed', 'Failed (recorded)', None),
            ('known-stopped-reason', 'runtime', {'status': 'stopped', 'error': 'iteration_paused'}, 'stopped', 'Stopped (recorded)', 'Iteration paused.'),
            ('missing-run-not-running', 'runtime', {'status': 'running', 'container_removed': False, 'run_id': None}, 'unknown', 'Unknown', None),
            ('removed-not-running', 'runtime', {'status': 'running'}, 'unknown', 'Unknown', None),
            ('running-with-error-not-useful-activity', 'runtime', {'status': 'running', 'container_removed': False, 'error': 'worker_failed'}, 'unknown', 'Unknown', 'Worker execution failed.'),
        ]:
            row = deepcopy(successful)
            row[field].update(changes)
            cases.append((name, row, state, label, reason))
        for name, row, state, label, reason in cases:
            with self.subTest(name=name), patch('builtins.open', side_effect=AssertionError('No file reads')), patch('socket.socket', side_effect=AssertionError('No network')):
                original = deepcopy(row)
                self.assertEqual(project_worker_status(row, repository=REPOSITORY),
                                 {'state': state, 'label': label, 'reason': reason, 'blocker': None})
                self.assertEqual(row, original)

    def test_blocker_identity_and_absent_parentage_matrix(self):
        waiting = completed()
        waiting['submitted_result']['status'] = 'blocked'
        dependency = {'repository_url': 'https://api.github.com/repos/' + REPOSITORY,
                      'number': 10, 'id': 100, 'node_id': 'I_100', 'state': 'open',
                      'html_url': 'https://evil.example/credential', 'body': 'TOKEN', 'title': '<script>'}
        canonical = {'number': 10, 'url': 'https://github.com/owner/product/issues/10'}
        cases: list[tuple[str, dict, list[dict], str, dict | None]] = [
            ('native-exact-identity', waiting, [dependency], REPOSITORY, canonical)]
        for name, delta in [
            ('foreign-repo', {'repository_url': 'https://api.github.com/repos/other/product'}),
            ('lookalike-repo', {'repository_url': 'https://api.github.com/repos/owner/product/../other'}),
            ('number-not-integer', {'number': '10'}), ('boolean-number', {'number': True}),
            ('self-block', {'number': 62}), ('closed', {'state': 'closed'}),
            ('missing-id', {'id': None}), ('missing-native-node', {'node_id': None}),
            ('pull-request', {'pull_request': {}}),
        ]:
            cases.append((name, waiting, [dependency | delta], REPOSITORY, None))
        wrong_handoff = deepcopy(waiting)
        wrong_handoff['handoff']['repository'] = 'other/product'
        traversal_handoff = deepcopy(waiting)
        traversal_handoff['handoff']['repository'] = 'owner/..'
        cases.extend([
            ('wrong-assignment-repository', wrong_handoff, [dependency], REPOSITORY, None),
            ('repository-path-traversal', traversal_handoff, [dependency | {'repository_url': 'https://api.github.com/repos/owner/..'}], 'owner/..', None),
            ('invalid-selected-repository', waiting, [dependency], 'owner/product?secret', None),
            ('no-inferred-blocker-from-text', waiting, [], REPOSITORY, None),
            ('successful-history-has-no-current-blocker', completed(), [dependency], REPOSITORY, None),
            ('deterministic-lowest-number', waiting, [dependency | {'number': 11}, dependency], REPOSITORY, canonical),
        ])
        for name, row, dependencies, repository, expected in cases:
            with self.subTest(name=name):
                row = deepcopy(row)
                row.update(parent_assignment_id='untrusted-parent', parent_run_id='untrusted-run',
                           parent={'id': 'untrusted', 'verified': True}, native_execution={'parent': 'untrusted'})
                row['handoff'].update(review={'builder_assignment': 'builder'},
                                      simplification={'builder_assignment': 'builder'},
                                      preceding=[{'url': 'https://evil.example/TOKEN'}])
                row['submitted_result']['work'] = ['Blocked by https://evil.example/TOKEN #123']
                row['result_disposition']['reason'] = 'TOKEN /host/private'
                before = deepcopy((row, dependencies))
                projection = project_worker_status(row, repository=repository, native_blockers=dependencies)
                self.assertEqual(set(projection), {'state', 'label', 'reason', 'blocker'})
                self.assertEqual(projection['blocker'], expected)
                self.assertNotIn('TOKEN', str(projection))
                self.assertNotIn('untrusted', str(projection))
                self.assertNotIn('evil.example', str(projection))
                self.assertEqual((row, dependencies), before)
