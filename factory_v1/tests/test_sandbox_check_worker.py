"""Exercise the actual bounded check subprocess implementation, not an output stub."""
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

from factory_v1.sandbox_check_worker import MAX_OUTPUT, run_checks


class CheckWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def python(self, code):
        return shlex.quote(sys.executable) + ' -c ' + shlex.quote(code)

    def test_records_actual_output_and_exit_status_without_rewriting_command(self):
        command = self.python('print("verified real subprocess")')
        records = run_checks([command], self.root, 5)
        self.assertEqual(records, [{'command': command, 'exit_code': 0,
                                    'stdout': 'verified real subprocess\n', 'stderr': '',
                                    'output_truncated': False}])

    def test_failure_stops_sequence_and_retains_stderr(self):
        marker = self.root / 'never-executed'
        failing = self.python('import sys; sys.stderr.write("actual failure\\n"); sys.exit(3)')
        following = self.python('from pathlib import Path; Path("never-executed").touch()')
        records = run_checks([failing, following], self.root, 5)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['exit_code'], 3)
        self.assertEqual(records[0]['stderr'], 'actual failure\n')
        self.assertFalse(marker.exists())

    def test_large_output_is_bounded_and_cannot_count_as_verified(self):
        records = run_checks([self.python('print("X" * 70000)'), 'echo not-run'], self.root, 5)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]['stdout']), MAX_OUTPUT)
        self.assertTrue(records[0]['output_truncated'])

    def test_expired_deadline_starts_nothing(self):
        with self.assertRaises(TimeoutError):
            run_checks(['echo never-run'], self.root, 0)


if __name__ == '__main__':
    unittest.main()
