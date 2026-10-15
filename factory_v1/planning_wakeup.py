"""One approved native wakeup via a durable SQLite mailbox, never completion by enqueue."""
import asyncio
import copy
import fcntl
import concurrent.futures
import threading
import time
from contextlib import contextmanager

from . import assignments, controls, planning, publication, skills
from .planning import require, text
from .repositories import RepositoryError, save_iteration
from .service import connect

TAG = '[factory-planning-wakeup:'
FIELDS = {'binding', 'assignment_id', 'profile_name', 'source_digest', 'allowance'}


def configuration(path):
    source = publication.physical(path)
    controls.private(source)
    config = publication.read_json(source)
    require(isinstance(config, dict) and set(config) == FIELDS and
            all(text(config[k]) for k in FIELDS - {'binding', 'allowance'}) and
            isinstance(config['binding'], dict), 'Exact private native wakeup approval required.', 'approval_required')
    grant = config['allowance']
    require(isinstance(grant, dict) and set(grant) == {'reference', 'provider_route', 'model', 'expires_at', 'max_wakeups'} and
            text(grant['reference']) and text(grant['provider_route']) and text(grant['model']) and
            type(grant['expires_at']) is int and grant['expires_at'] > time.time() and
            type(grant['max_wakeups']) is int and grant['max_wakeups'] == 1,
            'Explicit unexpired native route and single-wakeup allowance required.', 'approval_required')
    return config


def scope(item, config):
    binding = config['binding']
    work = item.get('planning', {})
    native = work.get('native_execution', {})
    selected = skills.resolve_installed(work['request']['skills'])
    require(binding.get('project') == item['project_id'] and binding.get('iteration') == item['iteration_id'] and
            binding.get('origin') == item['origin'] == work.get('origin') and
            config['assignment_id'] == work.get('assignment_id') == native.get('assignment_id') and
            binding.get('session_id') == native.get('session_id') and
            binding.get('operator_id') == native.get('operator_id') and
            binding.get('reference') == native.get('binding_reference') and
            native.get('sources') == selected == work['skills'] and
            native.get('adaptation') == work['instructions'] == planning.INSTRUCTIONS and
            config['source_digest'] == assignments.digest({'skills': selected, 'adaptation': work['instructions']}) and
            native.get('observations') and text(native.get('run_id')),
            'Exact original native observations, assignment, source and session required.', 'scope_mismatch')
    return {'approval': copy.deepcopy(config), 'run_id': native['run_id'],
            'observations_digest': assignments.digest(native['observations'])}


def message(request):
    return TAG + request['request_id'] + '] Resume the approved planning assignment using ONLY retained original observations; this is a controller wakeup, not an operator answer.'


def execution_allowed(item):
    request = item['planning'].get('native_wakeup')
    if request is not None:
        config = configuration(request['config_path'])
        identity = scope(item, config)
        require(not request.get('cancelled') and request['status'] == 'running' and request.get('execution_turn') and
                all(request.get(k) == v for k, v in identity.items()) and
                request['request_id'] == assignments.digest(identity),
                'Only the exact admitted wakeup turn may publish.', 'execution_required')


def save(database, item):
    save_iteration(database, item)
    database.commit()


@contextmanager
def mailbox_lock(database):
    """Use the existing controller lock, but never block the native host loop."""
    state = database.execute('PRAGMA database_list').fetchone()[2]
    with open(state + '.onboarding.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RepositoryError('mailbox_busy', 'Controller owns the mailbox; observe next cycle.') from error
        yield


def supervise(database, item, path, github, *, stop_event=None):
    """One tick requests or consumes one exact receipt; never sends or retries a native turn."""
    config = configuration(path)
    binding = config['binding']
    require(binding.get('state') == database.execute('PRAGMA database_list').fetchone()[2],
            'Physical durable state required.', 'approval_required')
    identity = scope(item, config)
    controls.require_active(database, item['project_id'], item['iteration_id'])
    require(stop_event is None or not stop_event.is_set(), 'Service is stopping.', 'service_stopping')
    work = item['planning']
    request = work.get('native_wakeup')
    if request is None:
        require(work['status'] == 'pending' and not work.get('host_publication'),
                'Wakeup requires unpublished pending native work.', 'execution_required')
        request = {**identity, 'request_id': assignments.digest(identity), 'config_path': path, 'status': 'requested'}
        work['native_wakeup'] = request
        planning.checkpoint(database, item)  # Commit before any transport can observe the request.
        return 'requested'
    require(request.get('config_path') == path and all(request.get(k) == v for k, v in identity.items()) and
            request.get('request_id') == assignments.digest(identity),
            'Retained wakeup scope changed; reconcile, never retry.', 'execution_uncertain')
    if request.get('cancelled'):
        return 'cancelled'
    if request['status'] == 'consumed':
        require(request.get('receipt') == receipt(item, request), 'Completion receipt changed.', 'execution_required')
        return 'consumed'
    if work.get('status') != 'complete':
        return request['status']
    expected = receipt(item, request)
    fresh_handoff = planning.read_publication(database, item, work['docs_path'], work['host_publication']['documents'],
                                             expected['commit'], expected['issue_number'], github)
    fresh = assignments.current(database, binding['project'], binding['iteration'], binding['operator_id'])
    require(scope(fresh, configuration(path)) == identity and receipt(fresh, request) == expected and
            fresh['handoff'] == fresh_handoff and fresh['planning'].get('native_wakeup') == request,
            'Fresh immutable publication/native completion changed.', 'execution_required')
    fresh['planning']['native_wakeup'].update(status='consumed', receipt=expected)
    planning.checkpoint(database, fresh)
    return 'consumed'


def receipt(item, request):
    work = item['planning']
    native = work.get('native_execution', {})
    published = work.get('host_publication', {})
    handoff = item.get('handoff', {})
    require(request.get('status') in {'running', 'enqueued', 'enqueue_unknown', 'consumed'} and
            work.get('status') == 'complete' and text(work.get('completion_digest')) and
            native.get('run_id') == request['run_id'] and
            native.get('assignment_id') == request['approval']['assignment_id'] and
            native.get('session_id') == request['approval']['binding']['session_id'] and
            assignments.digest(native.get('observations')) == request['observations_digest'] and
            request.get('execution_turn') and
            published.get('status') == 'verified' and published.get('execution', {}).get('native') == native and
            handoff.get('commit') == published.get('commit') and
            handoff.get('issue') == published.get('issue') and handoff.get('repository') == item['repository'],
            'Actual correlated native publisher completion and immutable handoff required.', 'execution_required')
    return {'request_id': request['request_id'], 'run_id': native['run_id'],
            'completion_digest': work['completion_digest'], 'handoff_digest': assignments.digest(handoff),
            'commit': handoff['commit'], 'issue_number': handoff['issue']['number']}


class NativeWakeup:
    """Host-supervised coroutine only; no threads, sockets, shell callbacks or ambient auth."""
    def __init__(self, binding, path):
        self.binding, self.path = binding, path
        self.approved = configuration(path)
        self.task = None
        self.stopped = False
        self.joined = threading.Event()
        self.status = 'not_started'

    def step(self):
        if self.stopped:
            return
        config = configuration(self.path)
        require(config == self.approved and config['binding'] == self.binding.config and self.binding.ctx.profile_name == config['profile_name'] and
                self.binding.ctx.get_config('provider_route') == config['allowance']['provider_route'],
                'Wrong native profile or binding.', 'scope_mismatch')
        with connect(self.binding.config) as database, mailbox_lock(database):
            item, _ = self.binding.current(database, self.binding.config['session_id'])
            identity = scope(item, config)
            request = item['planning'].get('native_wakeup')
            if request is None or request.get('cancelled') or request.get('status') != 'requested':
                return
            require(request.get('config_path') == self.path and all(request.get(k) == v for k, v in identity.items()) and
                    request.get('request_id') == assignments.digest(identity),
                    'Mailbox request scope changed.', 'scope_mismatch')
            with controls.admission(database, item['project_id'], item['iteration_id']):
                require(not self.stopped and configuration(self.path) == config,
                        'Plugin is stopping or native allowance changed.', 'approval_required')
                request['status'] = 'enqueue_unknown'
                save(database, item)  # Unknown survives a crash/exception; no blind re-enqueue.
                require(not self.stopped and configuration(self.path) == config,
                        'Native allowance changed before enqueue.', 'approval_required')
                try:
                    accepted = self.binding.ctx.inject_message(message(request), role='user', origin=config['binding']['origin'])
                except Exception:
                    self.status = 'enqueue_unknown'
                    return
            fresh, _ = self.binding.current(database, self.binding.config['session_id'])
            retained = fresh['planning'].get('native_wakeup')
            require(retained == request, 'Wakeup changed during enqueue.', 'execution_uncertain')
            retained['status'] = 'enqueued' if accepted is True else 'rejected'
            save(database, fresh)  # Truthful receipt only, not planning progress.
            self.status = retained['status']

    async def run(self):
        try:
            while not self.stopped:
                try:
                    self.step()
                except RepositoryError as error:
                    if error.code == 'mailbox_busy':
                        await asyncio.sleep(1)
                        continue
                    self.status = 'held'
                    return
                except (OSError, KeyError, ValueError):
                    self.status = 'held'
                    return
                await asyncio.sleep(1)
        finally:
            if self.status != 'held':
                self.status = 'stopped'
            self.joined.set()

    def stop(self):
        self.stopped = True
        try:
            with connect(self.binding.config) as database, mailbox_lock(database):
                item = assignments.current(database, self.binding.config['project'], self.binding.config['iteration'],
                                           self.binding.config['operator_id'], require_active=False)
                request = item.get('planning', {}).get('native_wakeup')
                if request is not None and request.get('approval') == self.approved and item['planning']['status'] != 'complete':
                    request['cancelled'] = True
                    save(database, item)
        except (RepositoryError, OSError):
            self.status = 'shutdown_unconfirmed'
        if self.task is not None:
            self.task.cancel()
            # Supported isolated plugin host runs the coroutine on its own host-owned
            # loop. Never manufacture a loop/thread to make in-process loading work.
            if isinstance(self.task, concurrent.futures.Future) and not self.joined.wait(2):
                self.status = 'shutdown_unconfirmed'

    def start(self):
        configuration(self.path)
        self.binding.ctx.on_unload(self.stop)
        coro = self.run()
        try:
            self.task = self.binding.ctx.spawn_task(coro, name='factory-planning-wakeup')
            if not isinstance(self.task, concurrent.futures.Future):
                self.task.cancel()
                self.stopped = True
                self.status = 'native_lifecycle_unavailable'
            else:
                self.status = 'started'
        except RuntimeError:
            coro.close()
            self.stopped = True
            self.status = 'native_lifecycle_unavailable'
        return self


def observe_wakeup(work, config, user_message, task_id, turn_id, sender_id, history, model):
    """Treat synthetic input as a wakeup, never as a genuine answer or host attestation."""
    request = work.get('native_wakeup')
    require(isinstance(request, dict), 'No approved synthetic turn.', 'scope_mismatch')
    approval = configuration(work['native_wakeup']['config_path'])
    require(not request.get('cancelled') and approval == request['approval'] and
            model == approval['allowance']['model'] and user_message == message(request) and
            request['status'] in {'enqueue_unknown', 'enqueued', 'running'} and
            request['approval']['binding'] == config and text(task_id) and text(turn_id) and
            sender_id == config['operator_id'] and work['native_execution']['run_id'] == request['run_id'],
            'Uncorrelated synthetic native turn.', 'scope_mismatch')
    originals = [m['content'] for m in work['native_execution']['observations'] if m['role'] == 'user']
    observed = [m.get('content') for m in (history or []) if isinstance(m, dict) and m.get('role') == 'user']
    require(all(value in observed for value in originals), 'Original host history missing on wakeup.', 'execution_required')
    turn = {'task_id': task_id, 'turn_id': turn_id}
    require(request.get('execution_turn', turn) == turn, 'Wakeup turn changed.', 'scope_mismatch')
    request.update(status='running', execution_turn=turn)
