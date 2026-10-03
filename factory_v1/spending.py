"""Durable exact-scope admission at the credential-blind model-operation boundary."""
import hashlib
import json
import os
import re
import socket
import sqlite3
import stat
import struct
import time
import uuid
from contextlib import closing
from pathlib import Path

from .model_access import MAX_RESPONSE, ModelAccessError, transport
from .repositories import state_lock

MAX_REQUEST = 32768
MAX_UNITS = 2**63 - 1


class SpendingError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SpendingError('invalid_request', 'Duplicate JSON fields are refused.')
        result[key] = value
    return result


def _scope(project, iteration, provider, model, operation):
    values = (project, iteration, provider, model, operation)
    if any(not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value) is None for value in values):
        raise SpendingError('invalid_scope', 'Exact unambiguous project, iteration, provider, model and operation are required.')
    return values


def initialize(database):
    database.executescript('''
      CREATE TABLE IF NOT EXISTS model_grants (
        grant_id TEXT PRIMARY KEY, project TEXT NOT NULL, iteration TEXT NOT NULL,
        provider TEXT NOT NULL, model TEXT NOT NULL, operation TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('allowance','approval')),
        ceiling INTEGER NOT NULL CHECK(ceiling > 0), remaining INTEGER NOT NULL CHECK(remaining >= 0),
        expires INTEGER NOT NULL, reference TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS model_operations (
        operation_id TEXT PRIMARY KEY, project TEXT NOT NULL, iteration TEXT NOT NULL,
        provider TEXT NOT NULL, model TEXT NOT NULL, operation TEXT NOT NULL,
        grant_id TEXT, reservation INTEGER NOT NULL, status TEXT NOT NULL,
        request_hash TEXT NOT NULL, actual_units INTEGER, created INTEGER NOT NULL,
        receipt TEXT);
      CREATE TABLE IF NOT EXISTS model_decisions (
        decision_id TEXT PRIMARY KEY, project TEXT NOT NULL, iteration TEXT NOT NULL,
        provider TEXT NOT NULL, model TEXT NOT NULL, operation TEXT NOT NULL,
        operation_id TEXT, decision TEXT NOT NULL, reason TEXT NOT NULL, created INTEGER NOT NULL);
    ''')


def _iteration(database, project, iteration, operator_id=None, require_active=False):
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                           (project, iteration)).fetchone()
    if row is None:
        raise SpendingError('not_found', 'Exact registered project iteration was not found.')
    item = json.loads(row[0])
    if item.get('execution_allowed') is not False:
        raise SpendingError('unsafe_iteration', 'This slice cannot enable factory execution.')
    if operator_id is not None and (item.get('approval', {}).get('operator_id') != operator_id or not item.get('approval', {}).get('reference')):
        raise SpendingError('approval_required', 'Broker requires the configured operator and registered approval.')
    if require_active and item.get('status') != 'active':
        raise SpendingError('iteration_paused', 'Paused iterations cannot admit model operations.')
    return item


def _save(database, item):
    database.execute('UPDATE iterations SET payload=? WHERE project_id=? AND iteration_id=?',
                     (json.dumps(item, sort_keys=True), item['project_id'], item['iteration_id']))


def add_grant(database, project, iteration, provider, model, operation, kind, ceiling, expires, reference, now=None):
    scope = _scope(project, iteration, provider, model, operation)
    now = int(time.time()) if now is None else now
    if (kind not in {'allowance', 'approval'} or type(ceiling) is not int or not 0 < ceiling <= MAX_UNITS
            or type(expires) is not int or not now < expires <= MAX_UNITS
            or not isinstance(reference, str) or not reference.strip() or len(reference) > 500):
        raise SpendingError('invalid_grant', 'Grant requires a bounded integer ceiling, future expiry and auditable reference.')
    database.execute('BEGIN IMMEDIATE')
    _iteration(database, project, iteration)
    existing = database.execute('''SELECT grant_id,kind,ceiling,expires FROM model_grants
      WHERE project=? AND iteration=? AND provider=? AND model=? AND operation=? AND reference=?''',
      (*scope, reference)).fetchone()
    if existing:
        if existing[1:] != (kind, ceiling, expires):
            raise SpendingError('grant_conflict', 'Grant reference was already recorded with conflicting terms.')
        return {'grant_id': existing[0], 'kind': kind, 'scope': list(scope), 'ceiling': ceiling, 'expires': expires, 'replay': True}
    grant_id = str(uuid.uuid4())
    database.execute('INSERT INTO model_grants VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (grant_id, *scope, kind, ceiling, ceiling, expires, reference))
    _decision(database, scope, None, 'grant_recorded', kind, now)
    return {'grant_id': grant_id, 'kind': kind, 'scope': list(scope), 'ceiling': ceiling, 'expires': expires}


def _decision(database, scope, operation_id, decision, reason, now):
    database.execute('INSERT INTO model_decisions VALUES (?,?,?,?,?,?,?,?,?,?)',
                     (str(uuid.uuid4()), *scope, operation_id, decision, reason, now))


def _block(database, item, reason, scope):
    if item.get('model_access', {}).get('reason') == 'quota_exhausted':
        return
    item['model_access'] = {'status': 'blocked', 'reason': reason, 'scope': list(scope)}
    _save(database, item)


def _prepare(database, scope, operation_id, request_bytes, reserve, now, allowance_only=False):
    if not isinstance(operation_id, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', operation_id) is None:
        raise SpendingError('invalid_operation_id', 'A bounded stable idempotency key is required.')
    if type(reserve) is not int or not 0 < reserve <= MAX_UNITS:
        raise SpendingError('invalid_reservation', 'Reservation must be a bounded positive integer.')
    digest = hashlib.sha256(request_bytes).hexdigest()
    database.execute('BEGIN IMMEDIATE')
    item = _iteration(database, scope[0], scope[1], require_active=True)
    old = database.execute('SELECT * FROM model_operations WHERE operation_id=?', (operation_id,)).fetchone()
    if old:
        if old[1:6] != scope or old[9] != digest or old[7] != reserve:
            _decision(database, scope, operation_id, 'refused', 'idempotency_conflict', now)
            database.commit()
            raise SpendingError('idempotency_conflict', 'Operation key already has a different scope, request or reservation.')
        if old[8] in {'complete', 'failed'}:
            return {'status': old[8], 'actual_units': old[10], 'replay': True}
        _block(database, item, 'outcome_uncertain', scope)
        _decision(database, scope, operation_id, 'refused', 'outcome_uncertain', now)
        database.commit()
        raise SpendingError('outcome_uncertain', 'Reconcile the exact pending operation before any retry.')
    pending = database.execute("SELECT 1 FROM model_operations WHERE project=? AND iteration=? AND status='pending' LIMIT 1", scope[:2]).fetchone()
    if pending or item.get('model_access', {}).get('reason') == 'quota_exhausted':
        reason = 'outcome_uncertain' if pending else 'quota_exhausted'
        _block(database, item, reason, scope)
        _decision(database, scope, operation_id, 'refused', reason, now)
        database.commit()
        raise SpendingError(reason, 'Model access is blocked; exact trusted reconciliation or quota-resume evidence is required.')
    grant = database.execute('''SELECT grant_id FROM model_grants
      WHERE project=? AND iteration=? AND provider=? AND model=? AND operation=?
      AND remaining>=? AND expires>? AND (?=0 OR kind='allowance') ORDER BY expires, grant_id LIMIT 1''',
      (*scope, reserve, now, int(allowance_only))).fetchone()
    if not grant:
        _decision(database, scope, operation_id, 'refused', 'authorization_unavailable_or_exhausted', now)
        _block(database, item, 'authorization_unavailable_or_exhausted', scope)
        database.commit()
        raise SpendingError('spending_blocked', 'No unexpired exact-scope grant covers the controlled reservation.')
    database.execute('UPDATE model_grants SET remaining=remaining-? WHERE grant_id=?', (reserve, grant[0]))
    database.execute('INSERT INTO model_operations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (operation_id, *scope, grant[0], reserve, 'pending', digest, None, now, None))
    item['model_access'] = {'status': 'admitted', 'operation_id': operation_id}
    _save(database, item)
    _decision(database, scope, operation_id, 'admitted', 'scoped_reservation', now)
    return {'status': 'pending'}


def _finish(database, operation_id, status, actual_units, receipt):
    database.execute('BEGIN IMMEDIATE')
    row = database.execute('SELECT project,iteration,provider,model,operation,grant_id,reservation,status,actual_units,receipt FROM model_operations WHERE operation_id=?', (operation_id,)).fetchone()
    if row is None:
        raise SpendingError('not_found', 'Pending operation was not found.')
    if status not in {'complete', 'failed'} or type(actual_units) is not int or not 0 <= actual_units <= row[6] or (status == 'failed' and actual_units != 0):
        raise SpendingError('invalid_receipt', 'Receipt needs a terminal outcome and bounded actual units; failure requires confirmed zero charge.')
    if row[7] != 'pending':
        if (row[7], row[8], row[9]) == (status, actual_units, receipt):
            return {'status': status, 'actual_units': actual_units, 'replay': True}
        raise SpendingError('reconciliation_conflict', 'Operation already has a conflicting terminal receipt.')
    database.execute('UPDATE model_grants SET remaining=remaining+? WHERE grant_id=?', (row[6] - actual_units, row[5]))
    database.execute('UPDATE model_operations SET status=?,actual_units=?,receipt=? WHERE operation_id=?',
                     (status, actual_units, receipt, operation_id))
    _decision(database, row[:5], operation_id, 'reconciled_' + status, 'trusted_controller_receipt', int(time.time()))
    item = _iteration(database, row[0], row[1])
    pending = database.execute("SELECT 1 FROM model_operations WHERE project=? AND iteration=? AND status='pending' LIMIT 1", row[:2]).fetchone()
    if pending:
        _block(database, item, 'outcome_uncertain', row[:5])
    elif item.get('model_access', {}).get('reason') != 'quota_exhausted':
        item['model_access'] = {'status': 'ready'}
        _save(database, item)
    return {'status': status, 'actual_units': actual_units}


def execute_controlled(database, scope, operation_id, payload, reserve, fixture_url=None, timeout=10, now=None, adapter=None):
    """Reserve before outbound I/O; uncertain outcomes freeze the whole iteration."""
    scope = _scope(*scope)
    if not isinstance(payload, dict):
        raise SpendingError('invalid_request', 'Request must be a bounded JSON object.')
    if set(payload) & {'provider', 'model', 'operation', 'url', 'endpoint', 'authorization', 'api_key', 'service_tier', 'stream', 'store'}:
        raise SpendingError('invalid_request', 'Worker requests cannot override identity, route, tier or credentials.')
    try:
        body = json.dumps(payload, separators=(',', ':'), sort_keys=True, allow_nan=False).encode()
    except (ValueError, RecursionError):
        raise SpendingError('invalid_request', 'Request must be finite JSON within parser limits.')
    if len(body) > MAX_REQUEST:
        raise SpendingError('invalid_request', 'Model request exceeds the fixed limit.')
    try:
        adapter = adapter or transport(scope, timeout, fixture_url=fixture_url, reservation=reserve)
    except (ModelAccessError, OSError) as error:
        raise SpendingError('provider_unavailable', 'Configure an approved fixed-scope subscription socket or labeled loopback fixture; paid routes are disabled.') from error
    if reserve != adapter.reservation or type(reserve) is not int:
        raise SpendingError('invalid_reservation', 'Reservation is fixed by the trusted adapter, never by the worker.')
    if adapter.payload_fields is not None and (set(payload) - adapter.payload_fields or 'input' not in payload):
        raise SpendingError('invalid_request', 'Subscription payload has unsupported fields or no input.')
    allowance_only = scope[2] != 'fixture-provider'
    with state_lock(database), database:
        admitted = _prepare(database, scope, operation_id, body, reserve, int(time.time()) if now is None else now, allowance_only)
    if admitted['status'] in {'complete', 'failed'}:
        return admitted
    try:
        units, result = adapter.call(payload)
        if type(units) is not int or not 0 <= units <= reserve:
            raise ModelAccessError('outcome_uncertain')
    except Exception as error:
        reason = 'quota_exhausted' if isinstance(error, ModelAccessError) and error.code == 'quota_exhausted' else 'outcome_uncertain'
        with state_lock(database), database:
            database.execute('BEGIN IMMEDIATE')
            item = _iteration(database, scope[0], scope[1])
            _block(database, item, reason, scope)
            _decision(database, scope, operation_id, 'blocked', reason, int(time.time()))
        raise SpendingError(reason, 'Model access blocked; reservation stays frozen until exact trusted billing reconciliation. No fallback.') from error
    with state_lock(database), database:
        _finish(database, operation_id, 'complete', units, 'model_response')
    return {'status': 'complete', 'actual_units': units, 'result': result}


def reconcile(database, project, iteration, provider, model, operation, operation_id, outcome, actual_units, reference):
    scope = _scope(project, iteration, provider, model, operation)
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 500:
        raise SpendingError('invalid_receipt', 'Trusted reconciliation evidence reference is required.')
    row = database.execute('SELECT project,iteration,provider,model,operation FROM model_operations WHERE operation_id=?', (operation_id,)).fetchone()
    if row is None or row != scope:
        raise SpendingError('scope_mismatch', 'Receipt must match the exact pending operation scope.')
    return _finish(database, operation_id, outcome, 0 if outcome == 'failed' and actual_units is None else actual_units, reference)


def resume_quota(database, scope, reference):
    scope = _scope(*scope)
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 500:
        raise SpendingError('invalid_receipt', 'Quota resume requires verified restoration evidence.')
    database.execute('BEGIN IMMEDIATE')
    item = _iteration(database, scope[0], scope[1])
    if (item.get('model_access', {}).get('reason') != 'quota_exhausted'
            or item['model_access'].get('scope') != list(scope)):
        raise SpendingError('quota_conflict', 'Only an explicitly quota-blocked iteration can resume.')
    if database.execute("SELECT 1 FROM model_operations WHERE project=? AND iteration=? AND status='pending'", scope[:2]).fetchone():
        raise SpendingError('outcome_uncertain', 'Reconcile uncertain billing before quota resume.')
    if not database.execute('SELECT 1 FROM model_grants WHERE project=? AND iteration=? AND provider=? AND model=? AND operation=? AND remaining>0 AND expires>?', (*scope, int(time.time()))).fetchone():
        raise SpendingError('spending_blocked', 'Quota restoration is not a new spending grant.')
    item['model_access'] = {'status': 'ready'}
    _save(database, item)
    _decision(database, scope, None, 'quota_resumed', reference, int(time.time()))
    return {'status': 'ready'}


def serve_unix_socket(path, database_path, scope, fixture_url=None, timeout=10, operator_id=None, subscription_socket=None, reservation=3, ttl=300, max_calls=100, worker_uid=None):
    """Fixed per-attempt capability; host state/upstream never enter the worker mount."""
    scope = _scope(*scope)
    if (type(ttl) is not int or not 0 < ttl <= 3600 or type(max_calls) is not int or not 0 < max_calls <= 1000):
        raise SpendingError('invalid_capability', 'Use a bounded capability TTL and request count.')
    if not os.path.isabs(path) or os.path.lexists(path):
        raise SpendingError('socket_exists', 'Use an absent absolute socket path in a private operator-owned directory.')
    try:
        adapter = transport(scope, timeout, fixture_url, subscription_socket, reservation)
    except (ModelAccessError, OSError) as error:
        raise SpendingError('provider_unavailable', 'Paid/unknown routes are disabled; use the exact approved subscription capability or labeled fixture.') from error
    worker_uid = os.getuid() if worker_uid is None else worker_uid
    parent = Path(path).parent.lstat()
    if (type(worker_uid) is not int or worker_uid < 0 or not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700):
        raise SpendingError('invalid_capability', 'Socket parent must be private operator-owned mode 0700; select one exact worker UID.')
    uri = Path(database_path).resolve().as_uri() + '?mode=rw'
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as database:
        _iteration(database, scope[0], scope[1], operator_id, require_active=True)
        initialize(database)
    deadline = time.monotonic() + ttl
    calls = 0
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(path)
        os.chmod(path, 0o600 if worker_uid == os.getuid() else 0o666)
        server.listen(8)
        try:
            while time.monotonic() < deadline:
                server.settimeout(min(1, max(.001, deadline - time.monotonic())))
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    connection.settimeout(min(5, max(.001, deadline - time.monotonic())))
                    try:
                        _, peer_uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                        if peer_uid != worker_uid:
                            raise SpendingError('worker_identity_mismatch', 'This socket capability belongs to one exact worker UID.')
                        data = bytearray()
                        while b'\n' not in data and len(data) <= MAX_REQUEST:
                            part = connection.recv(min(4096, MAX_REQUEST + 1 - len(data)))
                            if not part:
                                break
                            data.extend(part)
                        if len(data) > MAX_REQUEST or not data.endswith(b'\n'):
                            raise SpendingError('invalid_request', 'Request exceeds the fixed broker protocol limit.')
                        request = json.loads(data, object_pairs_hook=_json)
                        if not isinstance(request, dict) or set(request) != {'operation_id', 'reserve', 'payload'}:
                            raise SpendingError('invalid_request', 'Broker protocol fields are exact and limited.')
                        if time.monotonic() >= deadline or calls >= max_calls:
                            raise SpendingError('capability_exhausted', 'This attempt capability expired or exhausted its request count.')
                        calls += 1
                        with closing(sqlite3.connect(uri, uri=True, timeout=30)) as database:
                            _iteration(database, scope[0], scope[1], operator_id, require_active=True)
                            result = execute_controlled(database, scope, request['operation_id'], request['payload'], request['reserve'], adapter=adapter)
                        reply = {'result': result}
                    except SpendingError as error:
                        reply = {'error': error.code, 'message': str(error)}
                    except (ValueError, UnicodeError, RecursionError, OSError, sqlite3.Error):
                        reply = {'error': 'broker_unavailable', 'message': 'Broker request/state unavailable; no fallback or unchecked retry.'}
                    try:
                        connection.sendall(json.dumps(reply).encode() + b'\n')
                    except OSError:
                        pass  # Disconnected clients cannot cause a second upstream operation.
        finally:
            os.unlink(path)


def broker_call(path, operation_id, reserve, payload):
    try:
        request = json.dumps({'operation_id': operation_id, 'reserve': reserve, 'payload': payload}, separators=(',', ':'), allow_nan=False).encode() + b'\n'
    except (ValueError, RecursionError) as error:
        raise SpendingError('invalid_request', 'Broker request must be finite JSON within parser limits.') from error
    if len(request) > MAX_REQUEST:
        raise SpendingError('invalid_request', 'Broker request is too large.')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(180)
        client.connect(path)
        client.sendall(request)
        response = bytearray()
        while not response.endswith(b'\n') and len(response) <= MAX_RESPONSE:
            part = client.recv(4096)
            if not part:
                break
            response.extend(part)
    try:
        reply = json.loads(response, object_pairs_hook=_json)
        if len(response) > MAX_RESPONSE or not isinstance(reply, dict):
            raise ValueError()
        if 'error' in reply:
            raise SpendingError(reply['error'], reply['message'])
        return reply['result']
    except (ValueError, KeyError, RecursionError) as error:
        raise SpendingError('broker_unavailable', 'Broker returned no valid response; reconcile before any retry.') from error
