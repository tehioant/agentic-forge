"""Fresh factory controller: trusted operator intake and durable inspection."""

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path


class IntakeError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_intake(item, operator_id):
    required = {'project_id', 'iteration_id', 'repository', 'idea', 'approval', 'origin'}
    if not isinstance(item, dict) or not required <= item.keys() or item.keys() - required - {'work_item_number'}:
        raise IntakeError('invalid_intake', 'Provide an explicit approved project and iteration.')
    for field in ['project_id', 'iteration_id']:
        if not matches(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', item[field]):
            raise IntakeError('invalid_intake', 'Project and iteration identifiers must be unambiguous.')
    if 'work_item_number' in item and (type(item['work_item_number']) is not int or item['work_item_number'] <= 0):
        raise IntakeError('invalid_intake', 'Work-item number must be a positive GitHub issue number.')
    repository = item['repository']
    if not matches(r'[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}/[A-Za-z0-9_.-]{1,100}', repository) or repository.split('/')[-1] in {'.', '..'}:
        raise IntakeError('invalid_intake', 'Select an exact GitHub owner/repository identifier.')
    approval = item['approval']
    if not isinstance(approval, dict) or set(approval) != {'operator_id', 'reference'} or approval.get('operator_id') != operator_id or not text(approval.get('reference')):
        raise IntakeError('invalid_intake', 'Explicit approval and provenance from the configured operator are required.')
    if not text(item['idea']):
        raise IntakeError('invalid_intake', 'An approved idea is required.')
    origin = item['origin']
    identifiers = ['chat_id', 'thread_id', 'parent_chat_id', 'scope_id']
    if not isinstance(origin, dict) or set(origin) != {'platform', *identifiers} or origin.get('platform') != 'discord':
        raise IntakeError('invalid_intake', 'Provide the verified originating Discord thread metadata.')
    if any(not matches(r'[1-9][0-9]*', origin[key]) for key in identifiers) or origin['chat_id'] != origin['thread_id']:
        raise IntakeError('invalid_intake', 'Originating thread identifiers must be exact and consistent.')


def unique_fields(pairs):
    item = {}
    for key, value in pairs:
        if key in item:
            raise IntakeError('invalid_intake', 'Duplicate JSON fields make intake ambiguous.')
        item[key] = value
    return item


def read_iteration(database, project_id, iteration_id):
    row = database.execute(
        'SELECT payload FROM iterations WHERE project_id=? AND iteration_id=?',
        (project_id, iteration_id),
    ).fetchone()
    return None if row is None else json.loads(row[0])


def register_iteration(database, item):
    database.execute('BEGIN IMMEDIATE')
    database.execute(
        'CREATE TABLE IF NOT EXISTS iterations '
        '(project_id TEXT, iteration_id TEXT, payload TEXT, PRIMARY KEY(project_id, iteration_id))'
    )
    existing = read_iteration(database, item['project_id'], item['iteration_id'])
    if existing is not None:
        if existing.get('work_item_number') != item.get('work_item_number') or any(existing.get(key) != value for key, value in item.items()):
            raise IntakeError('intake_conflict', 'This iteration is already registered with different provenance or scope.')
        return existing
    active = database.execute(
        "SELECT 1 FROM iterations WHERE json_extract(payload, '$.status')='active' LIMIT 1"
    ).fetchone()
    item['status'] = 'active' if active is None else 'paused'
    item['stage'] = 'intake_registered'
    item['execution_allowed'] = False
    evidence_id = str(uuid.uuid4())
    item['correlation'] = {
        'project_id': item['project_id'], 'iteration_id': item['iteration_id'],
        'stage': item['stage'],
        'work_item': {'repository': item['repository'], 'issue_number': item['work_item_number']} if 'work_item_number' in item else None,
        'control_run_id': str(uuid.uuid4()), 'worker_run_id': None,
        'evidence_id': evidence_id,
    }
    item['evidence'] = {'id': evidence_id, 'kind': 'approved_intake', 'approval': item['approval']}
    database.execute(
        'INSERT INTO iterations VALUES (?, ?, ?)',
        (item['project_id'], item['iteration_id'], json.dumps(item)),
    )
    return item


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--operator-id', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    register = commands.add_parser('register')
    register.add_argument('--request', required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--project', required=True)
    inspect.add_argument('--iteration', required=True)
    args = parser.parse_args()
    if args.state in {'', ':memory:'}:
        raise IntakeError('invalid_state', 'State must name a persistent SQLite file.')
    if args.command == 'register':
        with open(args.request, encoding='utf-8') as source:
            try:
                item = json.load(source, object_pairs_hook=unique_fields)
            except (ValueError, RecursionError) as error:
                raise IntakeError('invalid_intake', 'Intake must be unambiguous UTF-8 JSON within parser limits.') from error
        validate_intake(item, args.operator_id)
        connection = sqlite3.connect(args.state)
        with closing(connection) as database, database:
            result = register_iteration(database, item)
    else:
        uri = Path(args.state).resolve().as_uri() + '?mode=ro'
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.OperationalError as error:
            raise IntakeError('not_found', 'Iteration state is unavailable.') from error
        with closing(connection) as database:
            result = read_iteration(database, args.project, args.iteration)
            if result is None:
                raise IntakeError('not_found', 'No iteration exists with these exact identities.')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except IntakeError as error:
        print(json.dumps({'error': error.code, 'message': str(error)}), file=sys.stderr)
        raise SystemExit(2)
    except (json.JSONDecodeError, UnicodeError):
        print(json.dumps({'error': 'invalid_intake', 'message': 'Intake must be unambiguous UTF-8 JSON.'}), file=sys.stderr)
        raise SystemExit(2)
    except OSError:
        print(json.dumps({'error': 'io_error', 'message': 'Unable to access the configured intake or state files.'}), file=sys.stderr)
        raise SystemExit(2)
    except sqlite3.Error:
        print(json.dumps({'error': 'state_error', 'message': 'State storage is unavailable or incompatible; no execution was admitted.'}), file=sys.stderr)
        raise SystemExit(2)
