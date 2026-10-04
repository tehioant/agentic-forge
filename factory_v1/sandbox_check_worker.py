"""Immutable check runner, executed only in the existing credential-free sandbox."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MAX_OUTPUT = 65536


def run_checks(commands, cwd, seconds, output_dir=None):
    deadline = time.monotonic() + seconds
    records = []
    for command in commands:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('verification deadline exceeded')
        with tempfile.TemporaryFile(dir=output_dir) as stdout, tempfile.TemporaryFile(dir=output_dir) as stderr:
            response = subprocess.run(['/bin/sh', '-c', command], cwd=cwd, stdin=subprocess.DEVNULL,
                                      stdout=stdout, stderr=stderr, timeout=remaining)
            stdout.seek(0)
            stderr.seek(0)
            out, err = stdout.read(MAX_OUTPUT + 1), stderr.read(MAX_OUTPUT + 1)
        truncated = len(out) > MAX_OUTPUT or len(err) > MAX_OUTPUT
        records.append({'command': command, 'exit_code': response.returncode,
                        'stdout': out[:MAX_OUTPUT].decode('utf-8', errors='replace'),
                        'stderr': err[:MAX_OUTPUT].decode('utf-8', errors='replace'),
                        'output_truncated': truncated})
        if response.returncode or truncated:
            break
    return records


if __name__ == '__main__':
    request = json.loads(Path('/inputs/checks.json').read_text())
    records = run_checks(request['commands'], '/workspace', request['seconds'], '/tmp')
    print(json.dumps({'run_id': request['run_id'], 'records': records}), flush=True)
    sys.exit(0 if len(records) == len(request['commands']) and
             all(record['exit_code'] == 0 and not record['output_truncated'] for record in records) else 1)
