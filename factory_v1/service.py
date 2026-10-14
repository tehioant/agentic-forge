"""Private systemd-user lifecycle; deterministic cycles never grant dispatch authority."""
import argparse
from contextlib import closing, contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid

from . import attention, assignments, controls, delivery, restart
from .errors import IntakeError
from .spending import SpendingError
from .planning import require
from .publication import read_json
from .repositories import GitHub, RepositoryError, state_lock
from .sandbox_source import physical

UNIT = 'agentic-forge.service'
FIELDS = {'version', 'state', 'operator_id', 'project', 'iteration', 'api_base', 'interval',
          'delivery_config', 'transport_config', 'dispatch_config'}


def isolated_root(root):
    source = Path(__file__).resolve().parent.parent
    profiles = Path.home() / '.hermes/profiles'
    require(all(not root.is_relative_to(scope) and not scope.is_relative_to(root) for scope in (source, profiles)),
            'Service state must be isolated from profiles and controller source.', 'invalid_service')


def initialize(root, operator, project, iteration, api_base):
    """Create an empty isolated envelope, not intake, grants or authorization."""
    root = Path(root)
    require(assignments.absolute(str(root)) and all(assignments.identifier(v) for v in (operator, project, iteration)),
            'Absolute fresh root and exact operator/project/iteration required.', 'invalid_service')
    isolated_root(root)
    GitHub(api_base, 'credential-blind-controller', 10)
    parent = physical(root.parent, directory=True)
    require(parent.stat().st_uid == os.getuid() and not parent.stat().st_mode & 0o022,
            'Use an operator-owned non-shared parent directory.', 'invalid_service')
    require(not root.exists() and not root.is_symlink(), 'Use a fresh service root.', 'invalid_service')
    root.mkdir(mode=0o700)
    config = {'version': 1, 'state': str(root / 'controller.sqlite'), 'operator_id': operator,
              'project': project, 'iteration': iteration, 'api_base': api_base, 'interval': 30,
              'delivery_config': None, 'transport_config': None, 'dispatch_config': None}
    with closing(sqlite3.connect(config['state'])) as db:
        db.execute('CREATE TABLE iterations (project_id TEXT, iteration_id TEXT, payload TEXT, PRIMARY KEY(project_id, iteration_id))')
        db.commit()
    os.chmod(config['state'], 0o600)
    atomic(root / 'service.json', config)
    return load(root / 'service.json')


def atomic(path, value):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.service-')
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load(path):
    path = physical(path)
    controls.private(path)
    root = path.parent
    controls.private(root, directory=True)
    require(path.name == 'service.json', 'Fixed private service configuration required.', 'invalid_service')
    config = read_json(path)
    require(isinstance(config, dict) and set(config) == FIELDS and config['version'] == 1 and
            all(assignments.identifier(config[k]) for k in ('operator_id', 'project', 'iteration')) and
            type(config['interval']) is int and 1 <= config['interval'] <= 3600 and
            config['state'] == str(root / 'controller.sqlite'), 'Malformed service scope/state.', 'invalid_service')
    controls.private(physical(config['state']))
    isolated_root(root)
    for key in ('delivery_config', 'transport_config', 'dispatch_config'):
        require(config[key] is None or (isinstance(config[key], str) and Path(config[key]).is_absolute()),
                'Use explicit private capability paths or null.', 'invalid_service')
    GitHub(config['api_base'], 'credential-blind-controller', 10)
    with connect(config) as db:
        from .merge import controller_paths
        controller_paths(db, str(path))
    return config


@contextmanager
def connect(config):
    uri = Path(config['state']).as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        yield db


def scope(config):
    return config['project'], config['iteration'], config['operator_id']


def startup(config, github):
    """Every new process closes admission before any read-only reconciliation."""
    with connect(config) as db:
        controls.request(db, *scope(config), 'pause', 'service-startup:explicit-resume-required')
        with state_lock(db):
            return restart.reconcile(db, *scope(config), github)


def dispatch(config, github, *, stop_event=None):
    from .service_dispatch import tick
    return tick(config, github, stop_event=stop_event)


def cycle(config, github, *, stop_event=None):
    with connect(config) as db:
        controls.owner(db, *scope(config))
        item = assignments.current(db, *scope(config), require_active=False)
        healthy = True
        if config['delivery_config'] is not None:
            with state_lock(db):
                from .merge import controller_paths
                controller_paths(db, config['delivery_config'])
                observed = delivery.sample(db, *scope(config), delivery.load_config(config['delivery_config'], config['operator_id']))
                healthy = observed['evidence']['status'] == 'healthy'
        if config['transport_config'] is not None:
            with state_lock(db):
                from .merge import controller_paths
                controller_paths(db, config['transport_config'])
                item = assignments.current(db, *scope(config), require_active=False)
                for record in attention.records(db, config['project'], config['iteration'], item):
                    if record['state'] == 'pending':
                        attention.deliver(db, item, record['event']['event_id'], config['transport_config'])
        if controls.blocked(db, config['project'], config['iteration']):
            return {'status': 'paused', 'dispatch_allowed': False, 'blockers': ['explicit_resume_required']}
        if not healthy:
            return {'status': 'blocked', 'dispatch_allowed': False, 'blockers': ['delivery_not_healthy']}
        if item['status'] != 'active':
            return {'status': 'waiting', 'dispatch_allowed': False, 'blockers': ['iteration_not_active']}
    if config['dispatch_config'] is None:
        return {'status': 'blocked', 'dispatch_allowed': False, 'blockers': ['dispatch_capability_unavailable']}
    return dispatch(config, github, stop_event=stop_event)


def status(config):
    path = Path(config['state']).parent / 'status.json'
    result = read_json(path) if path.exists() else {'status': 'not-started', 'blockers': ['service_not_started']}
    # Retained cycle status is not current execution authority or supervisor liveness.
    return {**result, 'dispatch_allowed': False, 'authority': 'existing-control-and-stage-gates'}


def unit_text(config):
    values = [str(Path(sys.executable).resolve()), str(Path(__file__).resolve().parent.parent),
              str(Path(config['state']).parent / 'service.json')]
    require(all(not any(c.isspace() or c in '\\%$\"\'' for c in value) for value in values),
            'Supervisor paths must be absolute without expansion/whitespace.', 'invalid_service')
    python, source, path = values
    return ('[Unit]\nDescription=Agentic Forge isolated controller\nAfter=network.target\n\n[Service]\n'
            'Type=simple\nWorkingDirectory=' + source + '\nExecStart=' + python +
            ' -m factory_v1.service --config ' + path + ' run\nRestart=on-failure\nRestartSec=5\n'
            'UMask=0077\nNoNewPrivileges=yes\nKillMode=control-group\nTimeoutStopSec=45\n'
            'Environment=PYTHONDONTWRITEBYTECODE=1\n\n[Install]\nWantedBy=default.target\n')


def install(config, directory):
    directory = physical(directory, directory=True)
    require(directory.stat().st_uid == os.getuid() and not directory.stat().st_mode & 0o022,
            'Operator-owned supervisor directory required.', 'invalid_service')
    path = directory / UNIT
    require(not os.path.lexists(path), 'Existing unit requires explicit operator reconciliation.', 'invalid_service')
    raw = unit_text(config)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, timeout=30)
    return path


def supervisor(config, action):
    require(action in {'start', 'stop', 'restart', 'status'}, 'Unsupported supervisor action.', 'invalid_service')
    result = subprocess.run(['systemctl', '--user', 'show', UNIT, '--property=FragmentPath', '--property=DropInPaths'],
                            check=True, capture_output=True, text=True, timeout=30)
    properties = dict(line.split('=', 1) for line in result.stdout.splitlines())
    require(set(properties) == {'FragmentPath', 'DropInPaths'} and not properties['DropInPaths'] and
            assignments.absolute(properties['FragmentPath']),
            'Missing unit or unreviewed supervisor overrides.', 'invalid_service')
    unit = physical(properties['FragmentPath'])
    require(unit.name == UNIT and unit.stat().st_uid == os.getuid() and not unit.stat().st_mode & 0o022 and
            unit.read_text() == unit_text(config), 'Installed source/config differs from selected unit.', 'invalid_service')
    if action == 'status':
        result = subprocess.run(['systemctl', '--user', 'show', UNIT, '--property=ActiveState', '--property=SubState', '--property=MainPID'],
                                check=True, capture_output=True, text=True, timeout=30)
        return dict(line.split('=', 1) for line in result.stdout.splitlines())
    if action in {'stop', 'restart'}:
        with connect(config) as db:
            controls.request(db, *scope(config), 'emergency-stop', 'supervisor-' + action)
    subprocess.run(['systemctl', '--user', action, UNIT], check=True, timeout=60)
    return None


def run(config, stop=None):
    stop = threading.Event() if stop is None else stop
    root = Path(config['state']).parent
    descriptor = os.open(root / 'service.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+') as lock:
        controls.private(root / 'service.lock')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RepositoryError('service_busy', 'Another service process owns this isolated root.') from error
        github = GitHub(config['api_base'], 'credential-blind-controller', 10)
        run_id = uuid.uuid4().hex
        previous = None
        signals = {s: signal.signal(s, lambda *_: stop.set()) for s in (signal.SIGTERM, signal.SIGINT)}
        try:
            startup(config, github)
            while not stop.is_set():
                try:
                    result = cycle(config, github, stop_event=stop)
                except (RepositoryError, IntakeError, SpendingError, OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError, sqlite3.Error) as error:
                    result = {'status': 'blocked', 'dispatch_allowed': False,
                              'blockers': [error.code if isinstance(error, (RepositoryError, IntakeError, SpendingError)) else 'service_observation_unavailable']}
                result = {**result, 'run_id': run_id, 'project': config['project'], 'iteration': config['iteration']}
                atomic(root / 'status.json', result)
                if result != previous:
                    descriptor = os.open(root / 'events.jsonl', os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(descriptor, 'a') as log:
                        controls.private(root / 'events.jsonl')
                        log.write(json.dumps(result, sort_keys=True) + '\n')
                    previous = result
                stop.wait(config['interval'])
        finally:
            # Existing cleanup owns containers/brokers; systemd additionally owns this cgroup.
            try:
                with connect(config) as db:
                    controls.request(db, *scope(config), 'emergency-stop', 'service-shutdown:' + run_id)
            finally:
                for signum, handler in signals.items():
                    signal.signal(signum, handler)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    for option in ('root', 'operator-id', 'project', 'iteration', 'api-base'):
        init.add_argument('--' + option, required=True)
    installer = commands.add_parser('install')
    installer.add_argument('--unit-directory', required=True)
    for name in ('start', 'stop', 'restart', 'status', 'run'):
        commands.add_parser(name)
    for name in ('pause', 'emergency-stop', 'resume'):
        commands.add_parser(name).add_argument('--reference', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'init':
            initialize(Path(args.root), args.operator_id, args.project, args.iteration, args.api_base)
            return
        require(args.config is not None, 'Select an isolated service.json.', 'invalid_service')
        config = load(args.config)
        if args.command == 'install':
            print(install(config, args.unit_directory))
        elif args.command in {'start', 'stop', 'restart'}:
            supervisor(config, args.command)
        elif args.command == 'status':
            print(json.dumps({**status(config), 'supervisor': supervisor(config, 'status')}, sort_keys=True))
        elif args.command == 'run':
            run(config)
        else:
            with connect(config) as db:
                result = controls.resume(db, *scope(config), args.reference, GitHub(config['api_base'], 'credential-blind-controller', 10)) if args.command == 'resume' else controls.request(db, *scope(config), args.command, args.reference)
            print(json.dumps({'action': result['action'], 'state': result['state'],
                              'dispatch_allowed': result['dispatch_allowed']}, sort_keys=True))
    except (RepositoryError, IntakeError, SpendingError, OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({'error': error.code if isinstance(error, (RepositoryError, IntakeError, SpendingError)) else 'invalid_service',
                          'message': 'Service operation refused; inspect isolated state and prerequisites.'}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
