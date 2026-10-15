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

    def test_check_command_output_and_verification_matrix(self):
        command = 'python -m unittest selected && python -m compileall selected.py'
        cases = (
            ('success', command, 0, b'labeled process output\n', b'', False),
            ('failure', 'failing check', 3, b'', b'labeled failure\n', False),
            ('stdout-limit', 'large check', 0, b'X' * (MAX_OUTPUT + 1), b'', True),
            ('stderr-limit', 'large check', 0, b'', b'X' * (MAX_OUTPUT + 1), True),
        )
        for label, actual_command, code, stdout, stderr, truncated in cases:
            with self.subTest(case=label), patch('factory_v1.sandbox_check_worker.subprocess.run',
                    side_effect=self.process(code, stdout, stderr)) as run, \
                    patch('factory_v1.sandbox_check_worker.time.monotonic', side_effect=[10, 11]):
                commands = [actual_command] if label == 'success' else [actual_command, 'never-executed check']
                records = run_checks(commands, self.root, 5)
                self.assertEqual(records, [{'command': actual_command, 'exit_code': code,
                    'stdout': stdout[:MAX_OUTPUT].decode(), 'stderr': stderr[:MAX_OUTPUT].decode(),
                    'output_truncated': truncated}])
                run.assert_called_once()
                self.assertEqual(run.call_args.args[0], ['/bin/sh', '-c', actual_command])
                self.assertEqual(run.call_args.kwargs['cwd'], self.root)
                self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
                self.assertEqual(run.call_args.kwargs['timeout'], 4)



    def test_original_deadline_and_timeout_matrix(self):
        for label, seconds, clock, failure, expected in (
            ('expired', 0, [10, 10], TimeoutError, []),
            ('remaining', 5, [10, 11, 14], None, [4, 1]),
            ('process-timeout', 1, [10, 10], subprocess.TimeoutExpired, [1]),
        ):
            with self.subTest(case=label), patch('factory_v1.sandbox_check_worker.time.monotonic', side_effect=clock), \
                    patch('factory_v1.sandbox_check_worker.subprocess.run', side_effect=(
                        subprocess.TimeoutExpired('labeled check', 1) if label == 'process-timeout' else self.process())) as run:
                if failure:
                    with self.assertRaises(failure):
                        run_checks(['first', 'second'], self.root, seconds)
                else:
                    self.assertEqual(len(run_checks(['first', 'second'], self.root, seconds)), 2)
                self.assertEqual([call.kwargs['timeout'] for call in run.call_args_list], expected)




if __name__ == '__main__':
    unittest.main()
