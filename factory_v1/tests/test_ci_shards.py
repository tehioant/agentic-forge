"""The serial component runner preserves inventory, failures and execution boundaries."""
from contextlib import redirect_stderr, redirect_stdout
import io
import tomllib
import unittest
from unittest.mock import Mock, patch

from scripts import run_factory_tests


class ComponentRunnerTests(unittest.TestCase):
    def suite(self, *, fail=False):
        ran = []
        def first():
            ran.append('first')
            if fail:
                raise AssertionError('LABELED expected component failure')
        def second():
            ran.append('second')
        cases = [unittest.FunctionTestCase(first), unittest.FunctionTestCase(second)]
        return unittest.TestSuite([unittest.TestSuite(cases[:1]), unittest.TestSuite(cases[1:])]), ran

    def test_nested_inventory_covers_every_identity_exactly_once(self):
        suite, _ = self.suite()
        self.assertEqual(run_factory_tests.inventory(suite), list(run_factory_tests.test_ids(suite)))
        self.assertEqual(len(run_factory_tests.inventory(suite)), 2)

    def test_empty_and_duplicate_inventories_fail_without_dropping_tests(self):
        with self.assertRaises(ValueError):
            run_factory_tests.inventory(unittest.TestSuite())
        case = unittest.FunctionTestCase(lambda: None)
        with self.assertRaises(ValueError):
            run_factory_tests.inventory(unittest.TestSuite([case, case]))
        for count in (500, 501):
            identities = [f'case-{index}' for index in range(count)]
            with self.subTest(count=count), patch.object(run_factory_tests, 'test_ids', return_value=iter(identities)):
                if count == 500:
                    self.assertEqual(run_factory_tests.inventory(unittest.TestSuite()), identities)
                else:
                    with self.assertRaisesRegex(ValueError, 'maximum is 500'):
                        run_factory_tests.inventory(unittest.TestSuite())

    def test_discovery_errors_refuse_before_execution(self):
        loader = Mock(errors=['LABELED discovery error'])
        loader.discover.return_value = unittest.TestSuite()
        with patch.object(run_factory_tests.unittest, 'TestLoader', return_value=loader), \
                patch.object(run_factory_tests.unittest, 'TextTestRunner') as runner, \
                redirect_stderr(io.StringIO()) as output:
            self.assertEqual(run_factory_tests.main(), 1)
        runner.assert_not_called()
        self.assertIn('LABELED discovery error', output.getvalue())

    def test_all_tests_run_after_failure_and_return_nonzero(self):
        suite, ran = self.suite(fail=True)
        loader = Mock(errors=[], discover=Mock(return_value=suite))
        with patch.object(run_factory_tests.unittest, 'TestLoader', return_value=loader), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(run_factory_tests.main(), 1)
        self.assertEqual(ran, ['first', 'second'])
        self.assertIn('2 unique tests', output.getvalue())
        self.assertIn('LABELED expected component failure', errors.getvalue())
        self.assertIn('Ran 2 tests', errors.getvalue())

    def test_single_passing_test_runs_without_empty_shards_or_autodiscovery(self):
        config = tomllib.loads((run_factory_tests.ROOT / '.mise.toml').read_text())
        self.assertEqual(config['tasks']['test']['run'], 'python scripts/run_factory_tests.py')
        self.assertEqual(config['tasks']['test']['env']['PYTHONDONTWRITEBYTECODE'], '1')
        ran = []
        suite = unittest.TestSuite([unittest.FunctionTestCase(lambda: ran.append(True))])
        loader = Mock(errors=[], discover=Mock(return_value=suite))
        with patch.object(run_factory_tests.unittest, 'TestLoader', return_value=loader), \
                redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(run_factory_tests.main(), 0)
        self.assertEqual(ran, [True])
        self.assertIn('1 unique tests', output.getvalue())
        self.assertIn('Ran 1 test', errors.getvalue())
        loader.discover.assert_called_once_with(str(run_factory_tests.TESTS))

    def test_execution_guard_denies_boundaries_and_retires_after_scope(self):
        hooks = []
        with patch.object(run_factory_tests.sys, 'addaudithook', side_effect=hooks.append):
            with run_factory_tests.no_external_execution() as attempts:
                for event in ('socket.__new__', 'socket.connect', 'subprocess.Popen', 'os.system',
                              'os.posix_spawn', 'os.fork', 'os.forkpty', 'os.exec', 'os.kill', 'os.killpg'):
                    with self.subTest(event=event), self.assertRaisesRegex(RuntimeError, 'mock external execution'):
                        hooks[0](event, ())
                hooks[0]('open', ())
                self.assertEqual(len(attempts), 10)
            hooks[0]('subprocess.Popen', ())
            self.assertEqual(len(attempts), 10)

    def test_caught_external_attempt_still_fails_the_run(self):
        hooks = []
        def catches_guard():
            try:
                hooks[-1]('socket.connect', ())
            except RuntimeError:
                pass
        suite = unittest.TestSuite([unittest.FunctionTestCase(catches_guard)])
        loader = Mock(errors=[], discover=Mock(return_value=suite))
        with patch.object(run_factory_tests.sys, 'addaudithook', side_effect=hooks.append), \
                patch.object(run_factory_tests.unittest, 'TestLoader', return_value=loader), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(run_factory_tests.main(), 1)
        self.assertIn('Unmocked external execution attempted: socket.connect', errors.getvalue())
