"""Run the complete discovered factory suite in two isolated subprocess shards."""
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'factory_v1' / 'tests'


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


def partition(suite):
    identities = sorted(test_ids(suite))
    if not identities or len(set(identities)) != len(identities):
        raise ValueError('Discovery must contain a nonempty, unique test inventory.')
    return [identities[::2], identities[1::2]]


def run_shards(shards, root, tests):
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join([str(tests), str(root), env.get('PYTHONPATH', '')])
    processes = []
    with tempfile.TemporaryDirectory(prefix='factory-tests-') as temporary, ExitStack() as files:
        try:
            for index, identities in enumerate(shards, 1):
                if not identities:
                    continue
                path = Path(temporary) / f'shard-{index}.log'
                log = files.enter_context(path.open('w'))
                print(f'Shard {index}: {len(identities)} tests', flush=True)
                process = subprocess.Popen([sys.executable, '-m', 'unittest', '-v', *identities],
                                           cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
                processes.append((index, process, path, log))
            success = True
            for index, process, path, log in processes:
                code = process.wait()
                log.flush()
                print(f'=== Shard {index}; exit {code} ===', flush=True)
                print(path.read_text(), end='', flush=True)
                success = success and code == 0
            return 0 if success else 1
        finally:
            for _, process, _, _ in processes:
                if process.poll() is None:
                    process.terminate()
                    process.wait()


def main():
    sys.path.insert(0, str(ROOT))
    loader = unittest.TestLoader()
    suite = loader.discover(str(TESTS))
    if loader.errors:
        print('\n'.join(map(str, loader.errors)), file=sys.stderr)
        return 1
    shards = partition(suite)
    print(f'Discovered {sum(map(len, shards))} unique tests; every test runs in exactly one shard.', flush=True)
    return run_shards(shards, ROOT, TESTS)


if __name__ == '__main__':
    sys.exit(main())
