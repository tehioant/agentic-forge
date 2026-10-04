"""Public bounded sandbox lifecycle; no scheduling, commit, merge or stage advancement."""
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import threading
import tempfile
import time
import uuid
from contextlib import closing
from pathlib import Path

from . import assignments, spending
from .planning import require
from .repositories import RepositoryError, state_lock
from .sandbox_guard import process_identity
from .sandbox_policy import Docker, command, load_config, verify
from .sandbox_source import manifest, physical, snapshot

TOOLS = {'terminal', 'process_manage', 'read_file', 'write_file', 'patch', 'search_files'}


def save(database, assignment):
    database.execute('BEGIN IMMEDIATE')
    database.execute('UPDATE role_assignments SET payload=? WHERE assignment_id=?',
                     (json.dumps(assignment, sort_keys=True), assignment['assignment_id']))
    database.commit()


def original(assignment):
    handoff = assignment['handoff']
    request = {key: handoff[key] for key in assignments.REQUEST_FIELDS}
    request['skills'] = [{key: value for key, value in s.items() if key != 'instructions'} for s in handoff['skills']]
    return request


def correlated(database, project, iteration, operator, assignment_id, run_id):
    assignment = assignments.inspect(database, project, iteration, operator, assignment_id)
    require(assignment.get('runtime', {}).get('run_id') == run_id,
            'Run identity changed; do not reuse an old capability.', 'stale_assignment')
    return assignment


def inputs_for(assignment, root):
    handoff = assignment['handoff']
    inputs = []
    values = [(s['source'], s['sha256'], s['instructions']) for s in handoff['skills']]
    values += [(handoff['tracker']['issue_url'], handoff['issue']['body_sha256'], handoff['issue_body'])]
    values += [(d['url'], d['sha256'], d['content']) for d in handoff['specification']['documents'].values()]
    for group in ('standards', 'preceding'):
        values += [('handoff:' + group + ':' + a['name'], a['sha256'], a['content']) for a in handoff[group]]
    for index, (source, pin, content) in enumerate(values):
        require(hashlib.sha256(content.encode()).hexdigest() == pin,
                'Pinned worker input bytes changed.', 'stale_assignment')
        name = f'instruction-{index}.txt'
        (root / name).write_text(content)
        inputs.append({'source': source, 'sha256': pin, 'path': '/inputs/' + name})
    # Retain the exact adaptation/support policy and original source/dependency identities.
    (root / 'handoff.json').write_text(json.dumps(handoff, sort_keys=True))
    return inputs


def authorize(database, project, iteration, operator, assignment_id, run_id, request):
    assignment = correlated(database, project, iteration, operator, assignment_id, run_id)
    assignments.current(database, project, iteration, operator)
    assignments.claims(database, original(assignment), assignment['ticket_scope'], assignment_id)
    if assignment['runtime']['status'] != 'running' or (Path(assignment['runtime']['artifacts']) / 'stop').exists():
        raise spending.SpendingError('capability_exhausted', 'Stopped run cannot admit operations.')
    payload = request.get('payload')
    if not isinstance(payload, dict):
        raise spending.SpendingError('invalid_request', 'Only bounded function-tool Responses input is supported.')
    tools = payload.get('tools', [])
    if (not isinstance(tools, list) or len(tools) > len(TOOLS) or
            any(not isinstance(tool, dict) or tool.get('type') != 'function' or tool.get('name') not in TOOLS for tool in tools) or
            payload.get('reasoning') != {'effort': assignment['handoff']['profile']['reasoning']}):
        raise spending.SpendingError('invalid_request', 'Server-side tools and reasoning/model changes are not admitted.')


def check_worker_evidence(assignment, root, inputs):
    """Check retained tool loads/commands against exact files, not names or success flags."""
    scratch = root / 'scratch'
    for name in ('result.json', 'loads.json', 'events.jsonl', 'conversation.json', 'probes.json'):
        path = physical(scratch / name)
        require(path.stat().st_size <= spending.SANDBOX_MAX_REQUEST * 10,
                'Worker evidence exceeds its bound.', 'invalid_result')
    result = json.loads((scratch / 'result.json').read_text())
    loads = json.loads((scratch / 'loads.json').read_text())
    require(isinstance(result, dict) and result.get('loads') == loads and result.get('run_id') == assignment['runtime']['run_id'],
            'Result does not match actual run loads.', 'invalid_result')
    events = [json.loads(line) for line in (scratch / 'events.jsonl').read_text().splitlines()]
    require(isinstance(loads, list) and len(loads) == len(inputs), 'Missing actual input loads.', 'invalid_result')
    for expected in inputs:
        load = next((value for value in loads if isinstance(value, dict) and value.get('source') == expected['source']), None)
        require(load is not None and load.get('sha256') == expected['sha256'], 'Missing pinned input load.', 'invalid_result')
        event = next((value for value in events if value.get('reference') == load.get('tool_reference')), None)
        require(event is not None and event.get('tool') == 'read_file' and event.get('args', {}).get('path') == expected['path'],
                'No executing-context tool record for selected input.', 'invalid_result')
        content = json.loads(event['result'])
        require(not content.get('error') and not content.get('truncated') and isinstance(content.get('content'), str),
                'Incomplete selected tool read.', 'invalid_result')
        actual = '\n'.join(line.split('|', 1)[1] for line in content['content'].splitlines())
        raw = (root / 'inputs' / Path(expected['path']).name).read_text()
        require(actual.rstrip('\n') == raw.rstrip('\n'), 'Tool read differs from pinned instruction bytes.', 'invalid_result')
    conversation = json.loads((scratch / 'conversation.json').read_text())
    require(isinstance(conversation, dict) and isinstance(conversation.get('messages'), list),
            'Actual conversation/tool results are required.', 'invalid_result')
    calls, executions = {}, []
    for message in conversation['messages']:
        if message.get('role') == 'assistant':
            for call in message.get('tool_calls', []):
                function = call.get('function', {})
                if function.get('name') == 'terminal':
                    args = json.loads(function['arguments'])
                    calls[call['id']] = args.get('command')
        if message.get('role') == 'tool' and message.get('tool_call_id') in calls:
            executions.append({'command': calls[message['tool_call_id']], 'output': message.get('content')})
    tests = result.get('tests')
    require(executions and isinstance(tests, list) and tests and all(isinstance(test, dict) and
            any(test.get('command') == execution['command'] and execution['output'] for execution in executions) for test in tests),
            'Reported tests must match retained actual terminal command/results.', 'invalid_result')
    require(any((root / 'model-evidence').glob('response-*.json')),
            'No admitted model response evidence.', 'invalid_result')
    (root / 'verified-tests.json').write_text(json.dumps(executions))
    return result


def launch(state, project, iteration, operator, assignment_id, config_path, github):
    config = load_config(config_path)
    docker = Docker()
    image_id = docker.image()
    # Validate fixed host route before claiming any runtime; never mount this upstream socket.
    from .model_access import SUBSCRIPTION_SCOPE, transport, ModelAccessError
    scope = (project, iteration, *SUBSCRIPTION_SCOPE)
    try:
        transport(scope, 180, subscription_socket=config['subscription_socket'])
    except (OSError, ModelAccessError) as error:
        raise RepositoryError('provider_unavailable', 'Approved subscription socket unavailable; no fallback.') from error
    with closing(sqlite3.connect(state)) as database:
        with state_lock(database):
            assignment = assignments.inspect(database, project, iteration, operator, assignment_id)
            assignments.prepare(database, project, iteration, operator, original(assignment), github, dry_run=True)
            require('runtime' not in assignment, 'Attempt already owns this assignment; inspect/stop/reconcile, never duplicate launch.', 'run_conflict')
            home = Path(assignment['handoff']['profile']['home'])
            require(not home.exists() and not home.is_symlink(), 'Role home must be fresh; no ambient profile is mounted or copied.', 'profile_blocked')
            artifact_root = Path(config['artifacts_root'])
            workspace = Path(assignment['handoff']['workspace'])
            require(not artifact_root.is_relative_to(workspace) and not workspace.is_relative_to(artifact_root) and
                    not Path(config_path).is_relative_to(workspace) and not Path(state).is_relative_to(workspace),
                    'Controller state/config, artifacts and source scopes must be disjoint.', 'unsafe_path')
            run_id = 'run-' + uuid.uuid4().hex
            root = artifact_root / run_id
            root.mkdir(mode=0o700)
            assignment['runtime'] = {'run_id': run_id, 'status': 'preparing', 'container': 'factory-' + run_id,
                                     'artifacts': str(root), 'image': config['image'], 'limits': config['limits'],
                                     'controller_pid': os.getpid(), 'controller_start': process_identity(os.getpid()),
                                     'advance_allowed': False, 'close_allowed': False}
            save(database, assignment)
    runtime = assignment['runtime']
    stop = threading.Event()
    broker = None
    guard = None
    signals = {}
    error_code = None
    status = 'failed'
    mounts = {}
    capability = None
    def cancel(signum, frame):
        stop.set()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signals[signum] = signal.signal(signum, cancel)
    try:
        inputs = root / 'inputs'
        inputs.mkdir(mode=0o755)
        pins = snapshot(workspace, {'baseline': assignment['handoff']['baseline'],
                                   'workspace': assignment['handoff']['candidate'] or assignment['handoff']['baseline']}, root)
        shutil.move(root / 'baseline', inputs / 'baseline')
        (root / 'initial-source.json').write_text(json.dumps(pins))
        selected_inputs = inputs_for(assignment, inputs)
        for name, target in [('sandbox_worker.py', 'worker.py'), ('sandbox_relay.py', 'sandbox_relay.py')]:
            shutil.copyfile(Path(__file__).parent / name, inputs / target)
        envelope = {'assignment': assignment, 'run_id': run_id, 'inputs': selected_inputs, 'limits': config['limits']}
        (inputs / 'assignment.json').write_text(json.dumps(envelope))
        scratch = root / 'scratch'
        scratch.mkdir(mode=0o777)
        scratch.chmod(0o777)
        # Only assignment-scoped scratch and sanitized source become writable; enclosing artifacts stay private.
        writable = 'write_workspace' in assignment['handoff']['capabilities']
        # Controller umask is 077; explicitly make only these scoped mounts readable
        # by the reviewed nonroot worker UID (which need not equal the host UID).
        for tree, mutable in ((root / 'workspace', writable), (inputs, False)):
            for parent, directories, files in os.walk(tree):
                Path(parent).chmod(0o777 if mutable else 0o755)
                for name in files:
                    path = Path(parent) / name
                    executable = bool(path.stat().st_mode & 0o111)
                    path.chmod((0o777 if executable else 0o666) if mutable else (0o755 if executable else 0o644))
        capability = Path(tempfile.mkdtemp(prefix='f1-'))
        require(len(str(capability / 'capability.sock').encode()) < 108, 'Unix capability path is too long.', 'unsafe_path')
        (root / 'capability-path.json').write_text(json.dumps(str(capability)))
        evidence = root / 'model-evidence'
        evidence.mkdir(mode=0o700)
        def observe(kind, number, value):
            (evidence / f'{kind}-{number}.json').write_text(json.dumps(value))
        def admit(database, request):
            try:
                authorize(database, project, iteration, operator, assignment_id, run_id, request)
            except RepositoryError as error:
                raise spending.SpendingError(error.code, str(error)) from error
        broker_errors = []
        def serve():
            try:
                spending.serve_unix_socket(str(capability / 'capability.sock'), state, scope, timeout=180,
                    operator_id=operator, subscription_socket=config['subscription_socket'], reservation=1,
                    ttl=config['limits']['seconds'], max_calls=config['limits']['max_calls'], worker_uid=config['uid'],
                    max_request=spending.SANDBOX_MAX_REQUEST, stop_event=stop, authorize=admit, observe=observe)
            except Exception as error:
                broker_errors.append(type(error).__name__)
        broker = threading.Thread(target=serve, daemon=True)
        broker.start()
        ready_deadline = time.monotonic() + 5
        while not (capability / 'capability.sock').exists():
            require(broker.is_alive() and time.monotonic() < ready_deadline and not stop.is_set(),
                    'Attempt model capability could not start.', 'provider_unavailable')
            time.sleep(.02)
        mounts = {'/workspace': (str(root / 'workspace'), writable), '/scratch': (str(scratch), True),
                  '/inputs': (str(inputs), False), '/model/capability.sock': (str(capability / 'capability.sock'), False)}
        (root / 'launch-command.json').write_text(json.dumps(command(config, runtime['container'], mounts)))
        (root / 'guard.json').write_text(json.dumps({'container': runtime['container'], 'seconds': config['limits']['seconds'],
            'controller_pid': os.getpid(), 'controller_start': runtime['controller_start'], 'capability': str(capability)}))
        guard_log = (root / 'guard.log').open('wb')
        guard = subprocess.Popen([sys.executable, '-m', 'factory_v1.sandbox_guard', str(root / 'guard.json')],
                                 cwd=Path(__file__).parent.parent, stdout=guard_log, stderr=guard_log, start_new_session=True)
        guard_log.close()
        docker.call(*command(config, runtime['container'], mounts))
        observed = docker.inspect(runtime['container'])
        verify(observed, config, runtime['container'], image_id, mounts)
        (root / 'container-inspection.json').write_text(json.dumps(observed))
        with closing(sqlite3.connect(state)) as database, state_lock(database):
            assignment = correlated(database, project, iteration, operator, assignment_id, run_id)
            assignments.prepare(database, project, iteration, operator, original(assignment), github, dry_run=True)
            require(not stop.is_set() and not (root / 'stop').exists(), 'Attempt was cancelled before start.', 'cancelled')
            assignment['runtime']['status'] = 'running'
            save(database, assignment)
        docker.call('start', runtime['container'])
        deadline = time.monotonic() + config['limits']['seconds']
        while True:
            if stop.is_set() or (root / 'stop').exists():
                error_code = 'cancelled'
                status = 'stopped'
                break
            if time.monotonic() >= deadline:
                error_code = 'time_limit'
                break
            require(broker.is_alive() and not broker_errors and guard.poll() is None,
                    'Controller helper failed; worker must stop.', 'helper_failed')
            with closing(sqlite3.connect(state)) as database, state_lock(database):
                correlated(database, project, iteration, operator, assignment_id, run_id)
                assignments.current(database, project, iteration, operator)
            observed = docker.inspect(runtime['container'])
            if not observed['State']['Running']:
                require(observed['State']['ExitCode'] == 0 and not observed['State'].get('OOMKilled'),
                        'Worker exited without a successful bounded result.', 'worker_failed')
                status = 'exited'
                break
            time.sleep(.1)
        # Stop helpers/container BEFORE inspecting worker-controlled artifacts.
        stop.set()
        logs = docker.call('logs', runtime['container'], optional=True)
        if logs is not None:
            (root / 'container.log').write_bytes(logs)
        docker.remove(runtime['container'])
        if status == 'exited':
            source = manifest(root / 'workspace')
            (root / 'source-artifacts.json').write_text(json.dumps(source))
            if not writable:
                require(source == pins['workspace'], 'Read-only source changed.', 'invalid_result')
            require(not (root / 'workspace' / '.git').exists(), 'Worker Git metadata is not a source artifact.', 'invalid_result')
            with closing(sqlite3.connect(state)) as database, state_lock(database):
                assignment = correlated(database, project, iteration, operator, assignment_id, run_id)
                result = check_worker_evidence(assignment, root, selected_inputs)
                assignment = assignments.store_result(database, project, iteration, operator, assignment_id, result, github)
                assignment['result_disposition'].update(trusted_execution=True, advance_allowed=False,
                    close_allowed=False, reason='isolated_artifacts_require_separate_simplification_and_two_axis_review')
                save(database, assignment)
            status = 'complete'
    except RepositoryError as error:
        error_code = error.code
        if status != 'stopped':
            status = 'failed'
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError, subprocess.SubprocessError, sqlite3.Error):
        error_code = 'recoverable_run_error'
        if status != 'stopped':
            status = 'failed'
    finally:
        stop.set()
        try:
            docker.remove(runtime['container'])
        except RepositoryError:
            error_code, status = 'stop_unconfirmed', 'unconfirmed'
        (root / 'guard-finish').touch()
        if guard:
            try:
                guard.wait(timeout=35)
            except subprocess.TimeoutExpired:
                # Do not kill the only independent cleanup guard. Preserve ownership for reconciliation.
                error_code, status = 'stop_unconfirmed', 'unconfirmed'
        if broker:
            broker.join(timeout=1)
        if capability and (not broker or not broker.is_alive()):
            shutil.rmtree(capability, ignore_errors=True)
        for signum, handler in signals.items():
            signal.signal(signum, handler)
        with closing(sqlite3.connect(state)) as database, state_lock(database):
            assignment = owned_assignment(database, project, iteration, operator, assignment_id)
            require(assignment['runtime']['run_id'] == run_id, 'Run changed during cleanup.', 'run_conflict')
            assignment['runtime'].update(status=status, error=error_code, source_artifacts=str(root / 'source-artifacts.json') if (root / 'source-artifacts.json').is_file() else None,
                                         container_removed=status != 'unconfirmed', recoverable=status != 'complete')
            save(database, assignment)
    return assignment


def owned_assignment(database, project, iteration, operator, assignment_id):
    # Termination must remain possible after requirement drift; it never admits work.
    assignments.current(database, project, iteration, operator, require_active=False)
    assignment = next((row for row in assignments.rows(database) if row['assignment_id'] == assignment_id), None)
    require(assignment is not None and assignment['ticket_scope']['project'] == project and
            assignment['ticket_scope']['iteration'] == iteration, 'Exact assignment ownership required.', 'not_found')
    return assignment


def stop_assignment(database, project, iteration, operator, assignment_id):
    with state_lock(database):
        assignment = owned_assignment(database, project, iteration, operator, assignment_id)
        runtime = assignment.get('runtime')
        require(runtime is not None, 'Assignment has no running attempt.', 'not_found')
        root = physical(runtime['artifacts'], directory=True)
        (root / 'stop').touch()
    docker = Docker()
    logs = docker.call('logs', runtime['container'], optional=True)
    if logs is not None:
        (root / 'container.log').write_bytes(logs)
    docker.remove(runtime['container'])
    with state_lock(database):
        assignment = owned_assignment(database, project, iteration, operator, assignment_id)
        require(assignment['runtime']['run_id'] == runtime['run_id'], 'Run changed during stop.', 'run_conflict')
        assignment['runtime'].update(status='stopped', error='cancelled', container_removed=True, recoverable=True)
        save(database, assignment)
    return assignment


def reconcile(database, project, iteration, operator, assignment_id):
    with state_lock(database):
        assignment = owned_assignment(database, project, iteration, operator, assignment_id)
        runtime = assignment.get('runtime')
        require(runtime is not None, 'Assignment has no attempt to reconcile.', 'not_found')
        require(process_identity(runtime['controller_pid']) != runtime['controller_start'],
                'Live controller still owns this attempt; stop it instead.', 'run_conflict')
    result = stop_assignment(database, project, iteration, operator, assignment_id)
    result['runtime']['error'] = 'controller_lost'
    with state_lock(database):
        save(database, result)
    return result
