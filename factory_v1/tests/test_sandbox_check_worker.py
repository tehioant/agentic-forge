"""Check-runner component tests; process execution is an explicit mock boundary."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory_v1.sandbox_check_worker import MAX_OUTPUT, run_checks


class CheckWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def process(self, code=0, stdout=b'', stderr=b''):
        def execute(argv, **kwargs):
            kwargs['stdout'].write(stdout)
            kwargs['stderr'].write(stderr)
            return subprocess.CompletedProcess(argv, code)
        return execute

    def test_records_actual_output_and_exit_status_without_rewriting_command(self):
        command = 'python -m unittest selected && python -m compileall selected.py'
        with patch('factory_v1.sandbox_check_worker.subprocess.run',
                   side_effect=self.process(stdout=b'labeled process output\n')) as run, \
                patch('factory_v1.sandbox_check_worker.time.monotonic', side_effect=[10, 11]):
            records = run_checks([command], self.root, 5)
        self.assertEqual(records, [{'command': command, 'exit_code': 0,
                                    'stdout': 'labeled process output\n', 'stderr': '',
                                    'output_truncated': False}])
        self.assertEqual(run.call_args.args[0], ['/bin/sh', '-c', command])
        self.assertEqual(run.call_args.kwargs['cwd'], self.root)
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs['timeout'], 4)

    def test_failure_stops_sequence_and_retains_stderr(self):
        with patch('factory_v1.sandbox_check_worker.subprocess.run',
                   side_effect=self.process(3, stderr=b'labeled failure\n')) as run:
            records = run_checks(['failing check', 'never-executed check'], self.root, 5)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['exit_code'], 3)
        self.assertEqual(records[0]['stderr'], 'labeled failure\n')
        run.assert_called_once()

    def test_large_output_is_bounded_and_cannot_count_as_verified(self):
        for stream in ('stdout', 'stderr'):
            with self.subTest(stream=stream), patch('factory_v1.sandbox_check_worker.subprocess.run',
                    side_effect=self.process(stdout=b'X' * (MAX_OUTPUT + 1) if stream == 'stdout' else b'',
                                             stderr=b'X' * (MAX_OUTPUT + 1) if stream == 'stderr' else b'')) as run:
                records = run_checks(['large check', 'never-run check'], self.root, 5)
            self.assertEqual(len(records), 1)
            self.assertEqual(len(records[0][stream]), MAX_OUTPUT)
            self.assertTrue(records[0]['output_truncated'])
            run.assert_called_once()

    def test_expired_deadline_starts_nothing(self):
        with self.assertRaises(TimeoutError):
            run_checks(['echo never-run'], self.root, 0)

    def test_following_check_uses_remaining_original_deadline(self):
        with patch('factory_v1.sandbox_check_worker.time.monotonic', side_effect=[10, 11, 14]), \
                patch('factory_v1.sandbox_check_worker.subprocess.run', side_effect=self.process()) as run:
            self.assertEqual(len(run_checks(['first', 'second'], self.root, 5)), 2)
        self.assertEqual([call.kwargs['timeout'] for call in run.call_args_list], [4, 1])

    def test_process_timeout_is_not_fabricated_as_success(self):
        with patch('factory_v1.sandbox_check_worker.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('labeled check', 1)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                run_checks(['timeout', 'never-run'], self.root, 1)
        run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
