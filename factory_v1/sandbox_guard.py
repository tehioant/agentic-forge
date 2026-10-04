"""Independent controller-death/TTL guard. It owns no model credentials or worker mounts."""
import argparse
import json
import os
import shutil
import time
from pathlib import Path

from .sandbox_policy import Docker, retain_logs


def process_identity(pid):
    try:
        # Field 22 is starttime; account for parentheses/spaces in comm.
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('record')
    args = parser.parse_args()
    record_path = Path(args.record)
    record = json.loads(record_path.read_text())
    root = record_path.parent
    deadline = time.monotonic() + record['seconds'] + 10
    docker = Docker()
    reason = 'controller_finished'
    while not (root / 'guard-finish').exists():
        if (root / 'stop').exists():
            reason = 'cancelled'
            break
        if process_identity(record['controller_pid']) != record['controller_start']:
            reason = 'controller_lost'
            break
        if time.monotonic() >= deadline:
            reason = 'time_limit'
            break
        time.sleep(0.2)
    try:
        # Keep crash logs before removing the actual container/helper tree.
        retain_logs(docker, record['container'], root)
        docker.remove(record['container'])
        if reason == 'controller_lost':
            shutil.rmtree(record['capability'], ignore_errors=True)
        (root / 'guard-result.json').write_text(json.dumps({'status': 'stopped', 'reason': reason}))
    except Exception:
        (root / 'guard-result.json').write_text(json.dumps({'status': 'unconfirmed', 'reason': reason}))


if __name__ == '__main__':
    main()
