"""Run every discovered factory unit/component test, without external execution."""
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'factory_v1' / 'tests'


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


def inventory(suite):
    identities = list(test_ids(suite))
    if not identities or len(set(identities)) != len(identities):
        raise ValueError('Discovery must contain a nonempty, unique test inventory.')
    return identities


@contextmanager
def no_external_execution():
    active, attempts = True, []
    def audit(event, args):
        if active and (event.startswith('socket.') or event in {
            'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.fork',
            'os.forkpty', 'os.exec', 'os.kill', 'os.killpg',
        }):
            attempts.append(event)
            raise RuntimeError('Unit/component tests must mock external execution: ' + event)
    sys.addaudithook(audit)
    try:
        yield attempts
    finally:
        active = False


def main():
    sys.path.insert(0, str(ROOT))
    with no_external_execution() as attempts:
        loader = unittest.TestLoader()
        suite = loader.discover(str(TESTS))
        if loader.errors:
            print('\n'.join(map(str, loader.errors)), file=sys.stderr)
            return 1
        try:
            identities = inventory(suite)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f'Discovered {len(identities)} unique tests; every test runs exactly once.', flush=True)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if attempts:
            print('Unmocked external execution attempted: ' + ', '.join(attempts), file=sys.stderr)
        return int(not result.wasSuccessful() or result.testsRun != len(identities) or bool(attempts))


if __name__ == '__main__':
    sys.exit(main())
