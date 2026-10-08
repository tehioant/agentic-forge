"""Bounded CI sharding preserves the test inventory and real subprocess failures."""
from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_factory_tests


class ShardRunnerTests(unittest.TestCase):
    def test_partition_covers_nested_suite_exactly_once_without_reordering_identity(self):
        cases = [unittest.FunctionTestCase(lambda: None, description=str(i)) for i in range(7)]
        for index, case in enumerate(cases):
            case.id = lambda index=index: f'fixture.Tests.test_{index}'
        suite = unittest.TestSuite([unittest.TestSuite(cases[:3]), unittest.TestSuite(cases[3:])])
        shards = run_factory_tests.partition(suite)
        flattened = [identity for shard in shards for identity in shard]
        self.assertCountEqual(flattened, [case.id() for case in cases])
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertEqual([len(shard) for shard in shards], [4, 3])
        self.assertEqual(shards, run_factory_tests.partition(unittest.TestSuite(reversed(cases))))

    def test_empty_and_duplicate_inventories_fail_instead_of_silently_dropping_tests(self):
        with self.assertRaises(ValueError):
            run_factory_tests.partition(unittest.TestSuite())
        case = unittest.FunctionTestCase(lambda: None)
        with self.assertRaises(ValueError):
            run_factory_tests.partition(unittest.TestSuite([case, case]))

    def test_discovery_errors_refuse_before_subprocess_execution(self):
        loader = unittest.TestLoader()
        with patch.object(loader, 'errors', ['LABELED discovery error']), \
                patch.object(run_factory_tests.unittest, 'TestLoader', return_value=loader), \
                patch.object(loader, 'discover', return_value=unittest.TestSuite()), \
                patch.object(run_factory_tests, 'run_shards') as run, redirect_stderr(io.StringIO()) as output:
            self.assertEqual(run_factory_tests.main(), 1)
        run.assert_not_called()
        self.assertIn('LABELED discovery error', output.getvalue())

    def test_real_shards_run_both_groups_and_propagate_failure_without_omission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.joinpath('test_fixture.py').write_text(
                'import unittest\n'
                'class Cases(unittest.TestCase):\n'
                '    def test_pass(self):\n'
                '        self.assertTrue(True)\n'
                '    def test_fail(self):\n'
                '        self.fail("LABELED expected shard failure")\n')
            with redirect_stdout(io.StringIO()) as output:
                result = run_factory_tests.run_shards(
                    [['test_fixture.Cases.test_fail'], ['test_fixture.Cases.test_pass']], root, root)
            self.assertEqual(result, 1)
            self.assertIn('LABELED expected shard failure', output.getvalue())
            self.assertIn('=== Shard 1; exit 1 ===', output.getvalue())
            self.assertIn('=== Shard 2; exit 0 ===', output.getvalue())
            self.assertEqual(output.getvalue().count('Ran 1 test'), 2)

    def test_real_passing_shards_and_single_test_empty_shard_do_not_autodiscover(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.joinpath('test_fixture.py').write_text(
                'import unittest\n'
                'class Cases(unittest.TestCase):\n'
                '    def test_pass(self):\n'
                '        self.assertTrue(True)\n'
                '    def test_unselected_fail(self):\n'
                '        self.fail("Must not be automatically discovered")\n')
            for shards in ([['test_fixture.Cases.test_pass'], []],
                           [['test_fixture.Cases.test_pass'], ['test_fixture.Cases.test_pass']]):
                with redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(run_factory_tests.run_shards(shards, root, root), 0)
                self.assertNotIn('test_unselected_fail', output.getvalue())
                self.assertEqual(output.getvalue().count('Ran 1 test'), sum(bool(s) for s in shards))
