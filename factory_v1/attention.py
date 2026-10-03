"""Durable exact-origin attention, with explicit uncertain delivery reconciliation."""
import json
import re

from .errors import IntakeError
from .messaging import HermesTransport
from .origin import validate_origin as validate_thread_origin
from .repositories import state_lock, unambiguous_fields


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value) is not None


def validate_origin(origin):
    try:
        validate_thread_origin(origin)
    except ValueError as error:
        raise IntakeError('invalid_origin', 'A verified exact originating Discord thread is required.') from error


def validate_event(event):
    fields = {'event_id', 'kind', 'message', 'issue_number', 'run_id', 'evidence_ids'}
    if (not isinstance(event, dict) or not isinstance(event.get('kind'), str)
            or event['kind'] not in {'decision', 'incident', 'completion'}):
        raise IntakeError('invalid_attention', 'Only decision, incident and completion attention is admitted.')
    if event['kind'] == 'decision':
        fields.add('decision_id')
    if (set(event) != fields or not identifier(event.get('event_id')) or not identifier(event.get('run_id'))
            or not isinstance(event.get('message'), str) or not event['message'].strip()
            or len(event['message']) > 16000 or type(event.get('issue_number')) is not int
            or event['issue_number'] <= 0 or not isinstance(event.get('evidence_ids'), list)
            or not event['evidence_ids'] or any(not identifier(e) for e in event['evidence_ids'])
            or len(set(event['evidence_ids'])) != len(event['evidence_ids'])
            or (event['kind'] == 'decision' and not identifier(event.get('decision_id')))):
        raise IntakeError('invalid_attention', 'Attention requires exact event, issue, run and evidence identities.')


def load_iteration(database, project, iteration):
    """Decode exact attention context without choosing among duplicate fields."""
    row = database.execute('SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
                           (project, iteration)).fetchone()
    if row is None:
        raise IntakeError('not_found', 'No exact iteration exists.')
    try:
        item = json.loads(row[0], object_pairs_hook=unambiguous_fields)
        if (not isinstance(item, dict) or item.get('project_id') != project
                or item.get('iteration_id') != iteration):
            raise ValueError('inconsistent identity')
    except (ValueError, TypeError, RecursionError, IntakeError):
        raise IntakeError('invalid_context', 'Recorded iteration JSON is malformed or ambiguous.') from None
    return item


def validate_receipt_shape(receipt):
    fields = {'event_id', 'attempt', 'delivery_key', 'destination', 'content', 'outcome', 'reference'}
    if (not isinstance(receipt, dict) or not isinstance(receipt.get('outcome'), str)
            or receipt['outcome'] not in {'delivered', 'not_sent'}
            or set(receipt) != fields | ({'message_id'} if receipt['outcome'] == 'delivered' else set())
            or not identifier(receipt.get('event_id')) or type(receipt.get('attempt')) is not int
            or not isinstance(receipt.get('reference'), str) or not receipt['reference'].strip()
            or (receipt['outcome'] == 'delivered' and
                (not isinstance(receipt['message_id'], str) or re.fullmatch(r'[1-9][0-9]*', receipt['message_id']) is None))):
        raise IntakeError('invalid_receipt', 'Supply an exact trusted reconciliation receipt, not a worker assertion.')


def validate_receipt_binding(receipt, record):
    if (receipt['event_id'] != record['event']['event_id']
            or receipt['delivery_key'] != [record['project_id'], record['iteration_id'], record['event']['event_id']]
            or receipt['destination'] != record['destination']
            or receipt['content'] != {'event': record['event'], 'correlation': record['correlation']}):
        raise IntakeError('invalid_receipt', 'Receipt must bind exact target, content, event and attempt.')


def validate_response(response, record, operator_id):
    fields = {'event_id', 'decision_id', 'operator_id', 'origin', 'reference', 'response'}
    if (not isinstance(response, dict) or set(response) != fields
            or record['event']['kind'] != 'decision'
            or response.get('event_id') != record['event']['event_id']
            or response.get('decision_id') != record['event'].get('decision_id')
            or response.get('operator_id') != operator_id
            or response.get('origin') != record['destination']
            or any(not isinstance(response.get(k), str) or not response[k].strip() for k in ['reference', 'response'])):
        raise IntakeError('invalid_decision', 'Authenticated input must bind the exact pending decision and origin.')


def load_record(payload, project, iteration, event_id, item=None):
    """Validate durable notification shape and identities before any lifecycle use."""
    required = {'project_id', 'iteration_id', 'event', 'destination', 'state',
                'attempts', 'receipt', 'decision_response', 'correlation'}
    try:
        record = json.loads(payload, object_pairs_hook=unambiguous_fields)
        if (not isinstance(record, dict) or not required <= record.keys()
                or record.keys() - required - {'last_error', 'reconciliation'}
                or record['project_id'] != project or record['iteration_id'] != iteration
                or not isinstance(record['state'], str)
                or record['state'] not in {'pending', 'uncertain', 'accepted', 'delivered'}
                or type(record['attempts']) is not int or record['attempts'] < 0
                or (record['state'] != 'pending' and record['attempts'] == 0)
                or any(record[key] is not None and not isinstance(record[key], dict)
                       for key in ['receipt', 'decision_response'])
                or ('reconciliation' in record and not isinstance(record['reconciliation'], dict))
                or (record.get('last_error') is not None and not isinstance(record['last_error'], str))):
            raise ValueError('invalid record')
        validate_event(record['event'])
        validate_origin(record['destination'])
        if record['event']['event_id'] != event_id:
            raise ValueError('inconsistent event identity')
        correlation = record['correlation']
        if (not isinstance(correlation, dict) or correlation.get('project_id') != project
                or correlation.get('iteration_id') != iteration
                or any(not identifier(correlation.get(key))
                       for key in ['control_run_id', 'evidence_id', 'stage'])
                or not isinstance(correlation.get('work_item'), dict)
                or not isinstance(correlation['work_item'].get('repository'), str)
                or not correlation['work_item']['repository'].strip()
                or type(correlation['work_item'].get('issue_number')) is not int
                or correlation['work_item']['issue_number'] != record['event']['issue_number']
                or correlation.get('worker_run_id') != record['event']['run_id']
                or correlation.get('evidence_ids') != record['event']['evidence_ids']):
            raise ValueError('invalid correlation')
        reconciliation = record.get('reconciliation')
        if reconciliation is not None:
            validate_receipt_shape(reconciliation)
            validate_receipt_binding(reconciliation, record)
            if not 0 < reconciliation['attempt'] <= record['attempts']:
                raise ValueError('future reconciliation')
            if reconciliation['outcome'] == 'delivered':
                if (record['state'] != 'delivered' or reconciliation['attempt'] != record['attempts']
                        or record['receipt'] != reconciliation or record.get('last_error') is not None):
                    raise ValueError('inconsistent delivered reconciliation')
            elif (record['state'] == 'delivered' or record['receipt'] is not None
                    or (reconciliation['attempt'] == record['attempts'] and record['state'] != 'pending')):
                raise ValueError('inconsistent no-effect reconciliation')
        if record['state'] == 'delivered':
            if reconciliation is None or reconciliation['outcome'] != 'delivered':
                raise ValueError('missing delivery evidence')
        elif record['receipt'] is not None:
            raise ValueError('receipt without delivery')
        if record['decision_response'] is not None:
            approval = item.get('approval') if isinstance(item, dict) else None
            if (not isinstance(approval, dict) or not isinstance(approval.get('operator_id'), str)
                    or not approval['operator_id'] or not isinstance(approval.get('reference'), str)
                    or not approval['reference'].strip() or item.get('origin') != record['destination']):
                raise ValueError('missing response provenance')
            validate_response(record['decision_response'], record, approval['operator_id'])
        return record
    except (ValueError, TypeError, RecursionError, IntakeError):
        raise IntakeError('state_error',
                         'Persisted attention state is malformed or inconsistent; no delivery was attempted.') from None


def records(database, project, iteration, item=None):
    exists = database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notifications'").fetchone()
    if not exists:
        return []
    return [load_record(payload, project, iteration, event_id, item) for event_id, payload in database.execute(
        'SELECT event_id, payload FROM notifications WHERE project=? AND iteration=? ORDER BY rowid', (project, iteration))]


def save(database, record):
    database.execute('INSERT OR REPLACE INTO notifications VALUES (?, ?, ?, ?)',
                     (record['project_id'], record['iteration_id'], record['event']['event_id'], json.dumps(record)))


def get_record(database, item, event_id):
    for record in records(database, item['project_id'], item['iteration_id'], item):
        if record['event']['event_id'] == event_id:
            if record['destination'] != item['origin']:
                raise IntakeError('invalid_origin', 'Persisted attention does not match the registered origin.')
            return record
    raise IntakeError('not_found', 'No attention event exists with these exact identities.')


def enqueue(database, item, event):
    validate_event(event)
    existing_records = records(database, item['project_id'], item['iteration_id'], item)
    for existing in existing_records:
        if existing['event']['event_id'] == event['event_id']:
            if existing['event'] != event or existing['destination'] != item['origin']:
                raise IntakeError('attention_conflict', 'Event identity already has different content or origin.')
            return existing
        if (event['kind'] == 'decision' and existing['event']['kind'] == 'decision'
                and existing['event']['decision_id'] == event['decision_id']):
            raise IntakeError('attention_conflict', 'A decision identity cannot be rebound to another event.')
    correlation = item.get('correlation')
    if (not isinstance(correlation, dict) or correlation.get('project_id') != item['project_id']
            or correlation.get('iteration_id') != item['iteration_id']
            or not identifier(correlation.get('control_run_id'))
            or not identifier(correlation.get('evidence_id')) or not identifier(correlation.get('stage'))):
        raise IntakeError('invalid_context', 'Recorded lifecycle correlation is missing or inconsistent.')
    record = {'project_id': item['project_id'], 'iteration_id': item['iteration_id'],
              'event': event, 'destination': item['origin'], 'state': 'pending', 'attempts': 0,
              'receipt': None, 'decision_response': None,
              'correlation': {**correlation,
                  'work_item': {'repository': item['repository'], 'issue_number': event['issue_number']},
                  'worker_run_id': event['run_id'], 'evidence_ids': event['evidence_ids']}}
    save(database, record)
    return record


def deliver(database, item, event_id, config_path):
    record = get_record(database, item, event_id)
    if record['state'] != 'pending':
        return record  # No automatic resend of accepted, verified or uncertain effects.
    try:
        transport = HermesTransport(config_path, record['destination'])
    except IntakeError as error:
        record['last_error'] = error.code
        save(database, record)
        database.commit()
        raise
    record['state'] = 'uncertain'
    record['attempts'] += 1
    save(database, record)
    database.commit()  # Intent survives death both before and after the external send.
    outcome = transport.send(record)
    database.execute('BEGIN IMMEDIATE')
    # Keep the shared lifecycle lock across commits and send; no concurrent resender.
    record['state'] = 'pending' if outcome == 'not_sent' else outcome
    record['last_error'] = None if outcome == 'accepted' else 'transport_' + outcome
    save(database, record)
    database.commit()
    if outcome != 'accepted':
        raise IntakeError(record['last_error'], 'Attention retained; known no-effect may retry, uncertain delivery requires trusted reconciliation.')
    return record


def reconcile(database, item, receipt):
    validate_receipt_shape(receipt)
    record = get_record(database, item, receipt['event_id'])
    validate_receipt_binding(receipt, record)
    if receipt['attempt'] != record['attempts'] or receipt['attempt'] <= 0:
        raise IntakeError('invalid_receipt', 'Receipt must bind exact target, content, event and attempt.')
    if record.get('reconciliation') == receipt:
        return record
    if record['state'] not in {'uncertain', 'accepted'} or (record['state'] == 'accepted' and receipt['outcome'] == 'not_sent'):
        raise IntakeError('receipt_conflict', 'Reconciliation cannot replace a terminal receipt or contradict acknowledgement.')
    record['reconciliation'] = receipt
    record['receipt'] = receipt if receipt['outcome'] == 'delivered' else None
    record['state'] = 'delivered' if receipt['outcome'] == 'delivered' else 'pending'
    record['last_error'] = None
    save(database, record)
    return record


def respond(database, item, response, operator_id):
    fields = {'event_id', 'decision_id', 'operator_id', 'origin', 'reference', 'response'}
    if (not isinstance(response, dict) or set(response) != fields
            or not identifier(response.get('event_id')) or not identifier(response.get('decision_id'))
            or response.get('operator_id') != operator_id
            or response.get('origin') != item['origin']
            or any(not isinstance(response.get(k), str) or not response[k].strip() for k in ['reference', 'response'])):
        raise IntakeError('invalid_decision', 'Authenticated input must bind the exact pending decision and origin.')
    record = get_record(database, item, response['event_id'])
    validate_response(response, record, operator_id)
    if record['decision_response'] is not None:
        if record['decision_response'] != response:
            raise IntakeError('decision_conflict', 'This decision already has a different response.')
        return record
    record['decision_response'] = response
    save(database, record)
    return record


def lifecycle(database, project, iteration, operator_id, action, request=None, event_id=None, config_path=None):
    """Trusted public lifecycle, serialized with planning/onboarding/spending writes."""
    with state_lock(database), database:
        database.execute('BEGIN IMMEDIATE')
        item = load_iteration(database, project, iteration)
        approval = item.get('approval')
        if (not isinstance(approval, dict) or approval.get('operator_id') != operator_id
                or not isinstance(approval.get('reference'), str) or not approval['reference'].strip()):
            raise IntakeError('approval_required', 'Recorded trusted operator provenance is required.')
        validate_origin(item.get('origin'))
        database.execute('CREATE TABLE IF NOT EXISTS notifications '
                         '(project TEXT, iteration TEXT, event_id TEXT, payload TEXT, PRIMARY KEY(project, iteration, event_id))')
        if action == 'attention':
            return enqueue(database, item, request)
        if action == 'deliver':
            return deliver(database, item, event_id, config_path)
        if action == 'respond':
            return respond(database, item, request, operator_id)
        if action == 'attention-reconcile':
            return reconcile(database, item, request)
        raise IntakeError('invalid_attention', 'Unsupported attention lifecycle action.')
