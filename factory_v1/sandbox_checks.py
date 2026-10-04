"""Controller-owned checks: reuse the admitted sandbox/guard, never worker transcripts."""
import json
import math
import shutil
import time
from pathlib import Path

from .planning import require
from .repositories import RepositoryError
from .sandbox_policy import Docker, command, retain_logs, verify
from .sandbox_source import manifest


def verify_checks(root, config, runtime, deadline):
    commands = config.get('verification_commands', [])
    if not commands:
        return False
    workspace = root / 'workspace'
    candidate = manifest(workspace)
    checks = root / 'checks'
    checks.mkdir(mode=0o700)
    inputs, scratch = checks / 'inputs', checks / 'scratch'
    inputs.mkdir(mode=0o755)
    scratch.mkdir(mode=0o777)
    inputs.chmod(0o755)
    scratch.chmod(0o777)
    remaining = deadline - time.monotonic()
    require(remaining > 0, 'Attempt deadline reached before independent checks.', 'time_limit')
    request = {'run_id': runtime['run_id'], 'commands': commands, 'seconds': remaining}
    (inputs / 'checks.json').write_text(json.dumps(request))
    shutil.copyfile(Path(__file__).with_name('sandbox_check_worker.py'), inputs / 'worker.py')
    for path in inputs.iterdir():
        path.chmod(0o444)
    limits = {**config['limits'], 'seconds': math.ceil(remaining)}
    check_config = {**config, 'limits': limits}
    mounts = {'/workspace': (str(workspace), False), '/scratch': (str(scratch), True),
              '/inputs': (str(inputs), False)}
    docker = Docker()
    name = runtime['container']
    image_id = docker.image()
    argv = command(check_config, name, mounts)
    (checks / 'launch-command.json').write_text(json.dumps(argv))
    try:
        require(not (root / 'stop').exists(), 'Attempt cancelled before independent checks.', 'cancelled')
        docker.call(*argv)
        inspection = docker.inspect(name)
        verify(inspection, check_config, name, image_id, mounts)
        (checks / 'container-inspection.json').write_text(json.dumps(inspection))
        require(not (root / 'stop').exists(), 'Attempt cancelled before check start.', 'cancelled')
        docker.call('start', name)
        while True:
            require(not (root / 'stop').exists(), 'Attempt cancelled during checks.', 'cancelled')
            require(time.monotonic() < deadline, 'Independent checks reached attempt deadline.', 'time_limit')
            observed = docker.inspect(name)
            assert observed is not None
            if not observed['State']['Running']:
                require(observed['State']['ExitCode'] == 0 and not observed['State'].get('OOMKilled'),
                        'Independent checks failed; retained worker claims are not verification.', 'verification_failed')
                break
            time.sleep(.1)
        output = docker.call('logs', name)
        require(output is not None and len(output) <= 2_000_000,
                'Independent check output unavailable or exceeds its bound.', 'verification_failed')
        assert output is not None
        report = json.loads(output)
        require(isinstance(report, dict), 'Independent check response must be an object.', 'verification_failed')
        records = report.get('records')
        require(report.get('run_id') == runtime['run_id'] and isinstance(records, list) and
                len(records) == len(commands) and all(isinstance(record, dict) and
                record.get('command') == expected and type(record.get('exit_code')) is int and
                record['exit_code'] == 0 and record.get('output_truncated') is False and
                all(isinstance(record.get(key), str) and len(record[key]) <= 65536 for key in ('stdout', 'stderr'))
                for expected, record in zip(commands, records)),
                'Independent check record does not match controller-admitted commands.', 'verification_failed')
        require(manifest(workspace) == candidate, 'Candidate changed during independent checks.', 'invalid_result')
        (root / 'controller-checks.json').write_text(json.dumps(
            {'run_id': runtime['run_id'], 'candidate': candidate, 'records': records,
             'provenance': 'controller_admitted_read_only_sandbox_checks'}))
        return True
    except RepositoryError:
        if (root / 'stop').exists():
            raise RepositoryError('cancelled', 'Attempt cancelled during independent verification.')
        raise
    finally:
        retain_logs(docker, name, checks)
        docker.remove(name)
