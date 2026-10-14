"""Durable operator interruption lane, independent of long controller state locks."""
import fcntl
import json
import os
import signal
import stat
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from .planning import require, text
from .repositories import RepositoryError, unambiguous_fields


def paths(database, project, iteration):
    from .assignments import digest, identifier
    require(identifier(project) and identifier(iteration), 'Exact control scope required.', 'invalid_control')
    state = database.execute('PRAGMA database_list').fetchone()[2]
    if not state:
        return None, None
    root = Path(state + '.controls')
    return root, root / (digest([project, iteration]) + '.json')


def private(path, directory=False):
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) and
            info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600) and
            (directory or info.st_nlink == 1), 'Private controller control state required.', 'invalid_control')


def read(database, project, iteration):
    try:
        return read_record(database, project, iteration)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
        raise RepositoryError('invalid_control', 'Malformed durable interruption; no execution authorized.') from error


def read_record(database, project, iteration):
    root, path = paths(database, project, iteration)
    if root is None or not os.path.lexists(root):
        return None
    assert path is not None
    private(root, directory=True)
    if not os.path.lexists(path):
        return None
    private(path)
    require(path.stat().st_size <= 32 * 1024 * 1024, 'Bounded control state required.', 'invalid_control')
    record = json.loads(path.read_text(), object_pairs_hook=unambiguous_fields)
    from .assignments import identifier
    item = json.loads(database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                                      (project, iteration)).fetchone()[0])
    require(isinstance(record, dict) and record.get('project') == project and record.get('iteration') == iteration and
            record.get('action') in {'pause', 'emergency-stop', 'resume'} and
            record.get('state') in {'requested', 'acknowledged', 'completed'} and
            record.get('operator_id') == item.get('approval', {}).get('operator_id') and text(record.get('reference')) and
            len(record['reference']) <= 500 and
            identifier(record.get('request_id')) and isinstance(record.get('history'), list) and
            isinstance(record.get('cleanup'), dict) and isinstance(record.get('checkpoint'), dict) and
            record['checkpoint'].get('iteration', {}).get('project_id') == project and
            record['checkpoint']['iteration'].get('iteration_id') == iteration and
            (record['action'] != 'resume' or record['state'] == 'completed'),
            'Malformed control state; no execution authorized.', 'invalid_control')
    return record


def blocked(database, project, iteration):
    record = read(database, project, iteration)
    return record is not None and record['action'] != 'resume'


def require_active(database, project, iteration):
    require(not blocked(database, project, iteration), 'Operator interruption prevents admission until explicit resume.', 'iteration_paused')


@contextmanager
def lane(database, project, iteration):
    root, _ = paths(database, project, iteration)
    if root is None:
        yield
        return
    root.mkdir(mode=0o700, exist_ok=True)
    private(root, directory=True)
    lock_path = root / 'lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+') as lock:
        private(lock_path)
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def admission(database, project, iteration, error_class: type[Exception] = RepositoryError):
    """Linearize short durable claims with requests; never hold across worker/network I/O."""
    try:
        with lane(database, project, iteration):
            require_active(database, project, iteration)
            yield
    except RepositoryError as error:
        if error_class is RepositoryError:
            raise
        raise error_class(error.code, str(error)) from error


def write(database, record):
    root, path = paths(database, record['project'], record['iteration'])
    require(root is not None and path is not None, 'Persistent controller state required.', 'invalid_control')
    assert root is not None and path is not None
    raw = json.dumps(record, sort_keys=True, allow_nan=False).encode()
    require(len(raw) <= 32 * 1024 * 1024, 'Bounded control checkpoint required.', 'invalid_control')
    fd, temporary = tempfile.mkstemp(prefix='checkpoint-', dir=root)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)


def snapshot(database, project, iteration):
    from .assignments import rows
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?', (project, iteration)).fetchone()
    require(row is not None, 'Exact registered iteration required.', 'not_found')
    item = json.loads(row[0])
    assignments = [a for a in rows(database) if a['ticket_scope']['project'] == project and a['ticket_scope']['iteration'] == iteration]
    pending = []
    if database.execute("SELECT 1 FROM sqlite_master WHERE name='model_operations'").fetchone():
        pending = [list(row) for row in database.execute(
            "SELECT operation_id,status,reservation,actual_units FROM model_operations WHERE project=? AND iteration=? AND status='pending'", (project, iteration))]
    root, _ = paths(database, project, iteration)
    brokers = []
    if root is not None and os.path.lexists(root):
        private(root, directory=True)
        for path in root.glob('broker-*.json'):
            private(path)
            require(path.stat().st_size <= 8192, 'Bounded broker ownership required.', 'invalid_control')
            record = json.loads(path.read_text(), object_pairs_hook=unambiguous_fields)
            require(isinstance(record, dict) and set(record) == {'project', 'iteration', 'pid', 'start', 'socket'} and
                    type(record['pid']) is int and record['pid'] > 0 and text(record['start']) and
                    isinstance(record['socket'], str) and Path(record['socket']).is_absolute(),
                    'Malformed broker ownership; cleanup unconfirmed.', 'invalid_control')
            if record['project'] == project and record['iteration'] == iteration:
                brokers.append(record)
    return {'iteration': item, 'assignments': assignments, 'pending_model_operations': pending, 'brokers': brokers}


@contextmanager
def broker_lease(database, project, iteration, socket_path):
    from .assignments import digest
    from .sandbox_guard import process_identity
    with admission(database, project, iteration):
        root, _ = paths(database, project, iteration)
        require(root is not None, 'Durable broker ownership required.', 'invalid_control')
        assert root is not None
        path = root / ('broker-' + digest(socket_path) + '.json')
        require(not os.path.lexists(path), 'Existing broker ownership requires explicit reconciliation.', 'control_busy')
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump({'project': project, 'iteration': iteration, 'pid': os.getpid(),
                       'start': process_identity(os.getpid()), 'socket': socket_path}, stream)
            stream.flush()
            os.fsync(stream.fileno())
    try:
        yield
    finally:
        # The caller exits only after all owned cancellation helpers are joined.
        with lane(database, project, iteration):
            path.unlink()


def validate_owner(checkpoint, operator):
    approval = checkpoint['iteration'].get('approval', {})
    require(approval.get('operator_id') == operator and text(approval.get('reference')),
            'Recorded configured operator approval required.', 'approval_required')


def owner(database, project, iteration, operator):
    paths(database, project, iteration)
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?', (project, iteration)).fetchone()
    require(row is not None, 'Exact registered iteration required.', 'not_found')
    validate_owner({'iteration': json.loads(row[0])}, operator)


@contextmanager
def quiescent_lock(database):
    state = database.execute('PRAGMA database_list').fetchone()[2]
    lock_path = state + '.onboarding.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True


def live_attempts(checkpoint):
    from .sandbox_guard import process_identity
    result = []
    for assignment in checkpoint['assignments']:
        runtime = assignment.get('runtime')
        if not runtime:
            continue
        live_controller = process_identity(runtime['controller_pid']) == runtime['controller_start']
        if (live_controller or runtime.get('container_removed') is not True or
                runtime.get('status') in {'preparing', 'running', 'unconfirmed'} or runtime.get('error') == 'stop_unconfirmed'):
            result.append(assignment)
    return result


def inspect(database, project, iteration, operator):
    owner(database, project, iteration, operator)
    with quiescent_lock(database) as quiet, lane(database, project, iteration):
        checkpoint = snapshot(database, project, iteration)
        validate_owner(checkpoint, operator)
        record = read(database, project, iteration)
        if record is None:
            return {'project': project, 'iteration': iteration, 'action': None, 'state': None,
                    'dispatch_allowed': checkpoint['iteration']['status'] == 'active', 'checkpoint': checkpoint}
        require(record['operator_id'] == operator, 'Control operator binding changed.', 'approval_required')
        if record['action'] != 'resume':
            record['checkpoint'] = checkpoint
            active = live_attempts(checkpoint)
            record['active_attempts'] = [a['assignment_id'] for a in active]
            if quiet and not active and not checkpoint['brokers']:
                record['state'] = 'completed'
            else:
                acknowledged = record['action'] == 'emergency-stop' or (active and all(
                    a['runtime']['status'] not in {'preparing', 'running'} for a in active))
                record['state'] = 'acknowledged' if acknowledged else 'requested'
            write(database, record)
        return {**record, 'dispatch_allowed': record['action'] == 'resume' and checkpoint['iteration']['status'] == 'active',
                'external_effects_undone': False}


def request(database, project, iteration, operator, action, reference):
    require(text(reference) and len(reference) <= 500, 'Auditable operator reference required.', 'invalid_control')
    owner(database, project, iteration, operator)
    with lane(database, project, iteration):
        checkpoint = snapshot(database, project, iteration)
        validate_owner(checkpoint, operator)
        old = read(database, project, iteration)
        require(old is None or old['operator_id'] == operator, 'Wrong control operator.', 'approval_required')
        if old and old['action'] == 'emergency-stop' and action == 'pause':
            record = old
        elif old and old['action'] == action:
            record = old
        else:
            record = {'project': project, 'iteration': iteration, 'operator_id': operator,
                      'reference': reference, 'request_id': uuid.uuid4().hex, 'action': action, 'state': 'requested',
                      'checkpoint': checkpoint, 'requested_checkpoint': checkpoint,
                      'history': (old.get('history', []) + [{k: v for k, v in old.items() if k != 'history'}]) if old else [],
                      'cleanup': {}}
            write(database, record)
    if action == 'emergency-stop':
        stop_attempts(database, project, iteration, operator)
    return inspect(database, project, iteration, operator)


def stop_attempts(database, project, iteration, operator):
    from .sandbox_guard import process_identity
    from .sandbox_policy import Docker, retain_logs
    from .sandbox_source import physical
    with lane(database, project, iteration):
        checkpoint = snapshot(database, project, iteration)
        validate_owner(checkpoint, operator)
    for assignment in live_attempts(checkpoint):
        runtime = assignment['runtime']
        outcome = {'run_id': runtime['run_id'], 'container_removed': False, 'error': None}
        root = None
        try:
            root = physical(runtime['artifacts'], directory=True)
            (root / 'stop').touch()
        except (RepositoryError, OSError):
            outcome['diagnostics_error'] = 'artifacts_unavailable'
        try:
            if process_identity(runtime['controller_pid']) == runtime['controller_start']:
                require(runtime['controller_pid'] != os.getpid(), 'Cannot stop the control process itself.', 'stop_unconfirmed')
                descriptor = os.pidfd_open(runtime['controller_pid'])
                try:
                    if process_identity(runtime['controller_pid']) == runtime['controller_start']:
                        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                finally:
                    os.close(descriptor)
        except (RepositoryError, OSError):
            outcome['controller_signal_error'] = 'stop_unconfirmed'
        try:
            docker = Docker()
            try:
                docker.call('kill', runtime['container'], optional=True)
                observed = docker.inspect(runtime['container'], optional=True)
                require(observed is None or observed.get('State', {}).get('Running') is False,
                        'Execution halt was not verified; remove without delaying for diagnostics.', 'stop_unconfirmed')
                if root is not None and observed is not None:
                    retain_logs(docker, runtime['container'], root)
            finally:
                docker.remove(runtime['container'])
                outcome['container_removed'] = True
        except (RepositoryError, OSError):
            outcome['error'] = 'stop_unconfirmed'
        with lane(database, project, iteration):
            record = read(database, project, iteration)
            assert record is not None
            record['cleanup'][assignment['assignment_id']] = outcome
            record['state'] = 'acknowledged'
            write(database, record)
    with lane(database, project, iteration):
        checkpoint = snapshot(database, project, iteration)
        root, _ = paths(database, project, iteration)
        assert root is not None
        from .assignments import digest
        for broker in checkpoint['brokers']:
            if process_identity(broker['pid']) != broker['start'] and not os.path.lexists(broker['socket']):
                # Explicit stop can retire a dead owner only after its capability is absent.
                (root / ('broker-' + digest(broker['socket']) + '.json')).unlink()


def resume(database, project, iteration, operator, reference):
    require(text(reference) and len(reference) <= 500, 'Explicit resume reference required.', 'invalid_control')
    owner(database, project, iteration, operator)
    with quiescent_lock(database) as quiet:
        require(quiet, 'Controller operation still in flight; reconcile before resume.', 'control_busy')
        with lane(database, project, iteration):
            checkpoint = snapshot(database, project, iteration)
            validate_owner(checkpoint, operator)
            old = read(database, project, iteration)
            require(old is not None and old['operator_id'] == operator and old['action'] != 'resume',
                    'Explicit interrupted scope required.', 'control_conflict')
            assert old is not None
            require(not live_attempts(checkpoint) and not checkpoint['brokers'] and not checkpoint['pending_model_operations'] and
                    not any(a.get('merge', {}).get('status') == 'pending' or a.get('publication', {}).get('status') == 'pending'
                            for a in checkpoint['assignments']),
                    'Reconcile live/unconfirmed workers and pending external intents first.', 'control_busy')
            item = checkpoint['iteration']
            iterations = (json.loads(row[0]) for row in database.execute('SELECT payload FROM iterations'))
            require(not any(other.get('status') == 'active' and
                            (other['project_id'], other['iteration_id']) != (project, iteration)
                            for other in iterations),
                    'Another iteration is active; resume cannot steal its scope.', 'control_conflict')
            with database:
                if item['status'] != 'waiting-for-operator':
                    item['status'] = 'active'
                from .repositories import save_iteration
                save_iteration(database, item)
            record = {**old, 'request_id': uuid.uuid4().hex, 'reference': reference, 'action': 'resume', 'state': 'completed',
                      'history': old.get('history', []) + [{k: v for k, v in old.items() if k != 'history'}]}
            write(database, record)
    return inspect(database, project, iteration, operator)
