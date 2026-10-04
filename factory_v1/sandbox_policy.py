"""Trusted fixed-image full-process launch policy and Docker inspection."""
import json
import math
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

from .planning import require
from .repositories import RepositoryError
from .sandbox_source import physical

IMAGE = 'nousresearch/hermes-agent@sha256:d4da4a40cd7a28aba983775d9fd31d94cbf153eeb0cb9e844d6d0f612b7c24db'
IMAGE_ENTRYPOINT = ['/opt/hermes/docker/entrypoint-dispatch.sh']
PYTHON = '/opt/hermes/.venv/bin/python'
LIMITS = {'seconds': (1, 3600), 'max_calls': (1, 1000), 'memory_mb': (256, 8192),
          'cpus': (0.1, 8), 'pids': (16, 512), 'scratch_mb': (16, 1024)}


def load_config(path):
    try:
        path = physical(path)
        info = path.stat()
        require(info.st_uid == os.getuid() and not info.st_mode & 0o077 and info.st_size <= 8192,
                'Launcher config must be a private controller-owned file.', 'invalid_launcher')
        from .repositories import unambiguous_fields
        config = json.loads(path.read_text(), object_pairs_hook=unambiguous_fields)
        require(isinstance(config, dict) and set(config) == {'image', 'uid', 'subscription_socket', 'artifacts_root', 'limits'} and
                config['image'] == IMAGE and type(config['uid']) is int and 0 < config['uid'] < 2**31,
                'Only the reviewed image, identity, resource limits, artifact root and subscription socket are configurable.', 'invalid_launcher')
        require(isinstance(config['limits'], dict) and set(config['limits']) == set(LIMITS),
                'Supply explicit bounded launcher limits.', 'invalid_launcher')
        for key, (low, high) in LIMITS.items():
            value = config['limits'][key]
            require(type(value) in ((int, float) if key == 'cpus' else (int,)) and math.isfinite(value) and low <= value <= high,
                    'Launcher limit is invalid or outside the reviewed bound.', 'invalid_launcher')
        root = physical(config['artifacts_root'], directory=True)
        require(root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) == 0o700,
                'Artifacts root must be private and controller-owned.', 'invalid_launcher')
        socket_path = Path(config['subscription_socket'])
        require(socket_path.is_absolute() and not any(p.is_symlink() for p in (socket_path, *socket_path.parents)),
                'Subscription route must be an exact physical Unix socket.', 'invalid_launcher')
        return config
    except (OSError, ValueError, TypeError, RecursionError) as error:
        raise RepositoryError('invalid_launcher', 'Invalid trusted launcher configuration.') from error


class Docker:
    def __init__(self):
        binary = shutil.which('docker')
        if binary is None:
            raise RepositoryError('isolation_unavailable', 'Docker is required for whole-process isolation.')
        self.binary = binary
        self.env = {key: os.environ[key] for key in ('PATH', 'HOME', 'DOCKER_HOST', 'DOCKER_CONTEXT', 'XDG_RUNTIME_DIR') if key in os.environ}

    def call(self, *args, optional=False, timeout=30):
        try:
            response = subprocess.run([self.binary, *args], env=self.env, capture_output=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as error:
            raise RepositoryError('container_unavailable', 'Docker operation unavailable; reconcile the exact container.') from error
        if optional and response.returncode:
            return None
        require(response.returncode == 0, 'Docker operation failed; no unsafe fallback.', 'container_unavailable')
        return response.stdout

    def inspect(self, name, optional=False):
        try:
            response = subprocess.run([self.binary, 'inspect', name], env=self.env, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as error:
            raise RepositoryError('container_unavailable', 'Docker inspection unavailable; stop is unconfirmed.') from error
        if response.returncode:
            if optional and (b'no such object' in response.stderr.lower() or b'no such container' in response.stderr.lower()):
                return None
            raise RepositoryError('container_unavailable', 'Docker inspection failed; absence was not verified.')
        raw = response.stdout
        try:
            result = json.loads(raw)
            require(isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict),
                    'Malformed Docker inspection.', 'container_unavailable')
            return result[0]
        except (ValueError, TypeError) as error:
            raise RepositoryError('container_unavailable', 'Malformed Docker inspection.') from error

    def image(self):
        try:
            image = json.loads(self.call('image', 'inspect', IMAGE))[0]
            require(IMAGE in image['RepoDigests'] and image['Config']['Entrypoint'] == IMAGE_ENTRYPOINT and
                    image['Config']['WorkingDir'] == '/opt/hermes' and set(image['Config'].get('Volumes') or {}) == {'/opt/data'},
                    'Installed image does not match the inspected runtime contract.', 'image_blocked')
            return image['Id']
        except (ValueError, KeyError, TypeError, IndexError) as error:
            raise RepositoryError('image_blocked', 'Unable to verify the installed image contract.') from error

    def remove(self, name):
        self.call('rm', '-f', name, optional=True)
        deadline = time.monotonic() + 30
        while self.inspect(name, optional=True) is not None:
            require(time.monotonic() < deadline,
                    'Container removal was not verified; ownership remains held.', 'stop_unconfirmed')
            time.sleep(.1)


def retain_logs(docker, name, root):
    """Diagnostic failures must never prevent independent container removal."""
    try:
        logs = docker.call('logs', name, optional=True)
        if logs is not None:
            (root / 'container.log').write_bytes(logs)
    except (RepositoryError, OSError) as error:
        try:
            (root / 'container-log-error.json').write_text(json.dumps(
                {'error': 'log_unavailable', 'type': type(error).__name__}))
        except OSError:
            pass  # Even an unwritable artifact root cannot prevent termination.


def mount_args(mounts):
    result = []
    for destination, value in mounts.items():
        source, writable = value
        require(',' not in source and '\n' not in source, 'Mount paths cannot contain delimiters.', 'unsafe_path')
        result += ['--mount', f'type=bind,src={source},dst={destination}' + ('' if writable else ',readonly')]
    return result


def command(config, name, mounts):
    limit = config['limits']
    writable = mounts['/workspace'][1]
    env = {'HOME': '/scratch/home', 'HERMES_HOME': '/scratch/home/.hermes', 'TMPDIR': '/scratch/tmp',
           'TERMINAL_ENV': 'local', 'TERMINAL_CWD': '/workspace', 'HERMES_DISABLE_LAZY_INSTALLS': '1',
           'HERMES_WRITE_SAFE_ROOT': '/workspace:/scratch' if writable else '/scratch',
           'HERMES_YOLO_MODE': '1', 'PYTHONPATH': '/opt/hermes', 'PYTHONDONTWRITEBYTECODE': '1',
           'FACTORY_MODEL_SOCKET': '/model/capability.sock'}
    args = ['create', '--name', name, '--label', 'factory-v1.sandbox=' + name, '--pull', 'never',
            '--network', 'none', '--read-only', '--init', '--user', f"{config['uid']}:{config['uid']}",
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', str(limit['pids']),
            '--memory', str(limit['memory_mb']) + 'm', '--memory-swap', str(limit['memory_mb']) + 'm',
            '--cpus', str(limit['cpus']), '--ulimit', 'nofile=1024:1024', '--ulimit', 'core=0:0',
            '--restart', 'no', '--log-driver', 'local', '--log-opt', 'max-size=10m', '--log-opt', 'max-file=2',
            '--tmpfs', f"/opt/data:rw,noexec,nosuid,nodev,size={limit['scratch_mb']}m,mode=700,uid={config['uid']},gid={config['uid']}",
            '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777',
            '--entrypoint', PYTHON, '--workdir', '/opt/hermes', *mount_args(mounts)]
    for key, value in env.items():
        args += ['--env', key + '=' + value]
    return [*args, IMAGE, '/inputs/worker.py']


def verify(container, config, name, image_id, mounts):
    try:
        host, actual = container['HostConfig'], container['Config']
        limit = config['limits']
        require(actual['Image'] == IMAGE and container['Image'] == image_id and
                actual['User'] == f"{config['uid']}:{config['uid']}" and actual['Entrypoint'] == [PYTHON] and
                actual['Cmd'] == ['/inputs/worker.py'] and actual['WorkingDir'] == '/opt/hermes' and
                actual['Labels'].get('factory-v1.sandbox') == name and
                host['NetworkMode'] == 'none' and host['ReadonlyRootfs'] is True and host['Init'] is True and
                host['Privileged'] is False and host['CapDrop'] == ['ALL'] and not host.get('CapAdd') and
                host['SecurityOpt'] == ['no-new-privileges'] and not host.get('Devices') and
                not host.get('DeviceRequests') and not host.get('PortBindings') and
                host['PidMode'] == '' and host['IpcMode'] == 'private' and
                host['Memory'] == limit['memory_mb'] * 1024 * 1024 and host['MemorySwap'] == host['Memory'] and
                host['NanoCpus'] == int(limit['cpus'] * 1_000_000_000) and host['PidsLimit'] == limit['pids'] and
                host['RestartPolicy']['Name'] == 'no',
                'Docker did not enforce the reviewed process, isolation and resource policy.', 'isolation_unavailable')
        seen = {}
        for mount in container['Mounts']:
            require(mount['Destination'] not in seen, 'Duplicate mount target.', 'isolation_unavailable')
            seen[mount['Destination']] = mount
        # Docker reports --tmpfs separately in HostConfig, not in Mounts. Inspect both;
        # an image's anonymous volume would still appear in Mounts and must be refused.
        require(set(seen) == set(mounts),
                'Unexpected mount, including anonymous image volumes.', 'isolation_unavailable')
        for target, (source, writable) in mounts.items():
            mount = seen[target]
            require(mount['Type'] == 'bind' and mount['Source'] == source and mount['RW'] is writable and
                    mount.get('Propagation') in ('rprivate', ''), 'Mount identity/permission mismatch.', 'isolation_unavailable')
        expected = command(config, name, mounts)
        expected_tmpfs = dict(expected[i + 1].split(':', 1) for i, value in enumerate(expected) if value == '--tmpfs')
        require(host['Tmpfs'] == expected_tmpfs,
                'Image data must use exact bounded tmpfs, never an anonymous volume.', 'isolation_unavailable')
        environment = {expected[i + 1] for i, value in enumerate(expected) if value == '--env'}
        require(environment <= set(actual['Env']) and not any(value.split('=', 1)[0].endswith(('TOKEN', 'API_KEY', 'SECRET')) for value in actual['Env']),
                'Unexpected credential propagation.', 'isolation_unavailable')
    except (KeyError, TypeError, ValueError) as error:
        raise RepositoryError('isolation_unavailable', 'Docker inspection is incomplete; launch refused.') from error
