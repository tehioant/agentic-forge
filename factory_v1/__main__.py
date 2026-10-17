"""Fresh factory controller: trusted operator intake and durable inspection."""

import json
import os
import re
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path


from .errors import IntakeError
from .origin import validate_origin
from .repositories import valid_repository


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_intake(item, operator_id):
    required = {'project_id', 'iteration_id', 'repository', 'idea', 'approval', 'origin'}
    if not isinstance(item, dict) or not required <= item.keys() or item.keys() - required - {'work_item_number', 'repository_intent'}:
        raise IntakeError('invalid_intake', 'Provide an explicit approved project and iteration.')
    for field in ['project_id', 'iteration_id']:
        if not matches(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', item[field]):
            raise IntakeError('invalid_intake', 'Project and iteration identifiers must be unambiguous.')
    if 'work_item_number' in item and (type(item['work_item_number']) is not int or item['work_item_number'] <= 0):
        raise IntakeError('invalid_intake', 'Work-item number must be a positive GitHub issue number.')
    repository = item['repository']
    if not valid_repository(repository):
        raise IntakeError('invalid_intake', 'Select an exact GitHub owner/repository identifier.')
    if 'repository_intent' in item:
        intent = item['repository_intent']
        if not isinstance(intent, dict) or set(intent) != {'mode', 'marker'} or intent.get('mode') != 'new' or not text(intent.get('marker')):
            raise IntakeError('invalid_intake', 'New-product intent requires an explicit ownership marker.')
    approval = item['approval']
    if not isinstance(approval, dict) or set(approval) != {'operator_id', 'reference'} or approval.get('operator_id') != operator_id or not text(approval.get('reference')):
        raise IntakeError('invalid_intake', 'Explicit approval and provenance from the configured operator are required.')
    if not text(item['idea']):
        raise IntakeError('invalid_intake', 'An approved idea is required.')
    try:
        validate_origin(item['origin'])
    except ValueError as error:
        raise IntakeError('invalid_intake', str(error)) from error


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
        if any(existing.get(key) != item.get(key) for key in ['work_item_number', 'repository_intent']) or any(existing.get(key) != value for key, value in item.items()):
            raise IntakeError('intake_conflict', 'This iteration is already registered with different provenance or scope.')
        return existing
    waiting = database.execute("SELECT 1 FROM iterations WHERE project_id=? AND "
                               "json_extract(payload, '$.status')='waiting-for-operator'", (item['project_id'],)).fetchone()
    if waiting:
        raise IntakeError('iteration_wait_required', 'Use authenticated next-iteration input for this same product, not fresh intake.')
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
    from .cli_parser import build_parser
    args = build_parser(__doc__).parse_args()
    if args.state in {'', ':memory:'}:
        raise IntakeError('invalid_state', 'State must name a persistent SQLite file.')
    if args.command in {'complete-iteration', 'next-iteration'}:
        from . import iteration
        from .repositories import GitHub, RepositoryError, state_lock
        try:
            with open(args.request, encoding='utf-8') as source:
                request = json.load(source, object_pairs_hook=unique_fields)
            uri = Path(args.state).resolve().as_uri() + '?mode=rw'
            with closing(sqlite3.connect(uri, uri=True)) as database, state_lock(database), database:
                if args.command == 'complete-iteration':
                    result = iteration.complete(database, args.project, args.iteration, args.operator_id, request,
                                                GitHub(args.api_base, args.bearer, args.timeout))
                else:
                    result = iteration.next_iteration(database, args.project, args.iteration, args.operator_id, request)
        except RepositoryError as error:
            raise IntakeError(error.code, str(error)) from error
        except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
            raise IntakeError('invalid_completion', 'Malformed or unavailable iteration input/evidence; no completion or continuation admitted.') from error
        print(json.dumps(result, sort_keys=True))
        return
    if args.command in {'register-recovery', 'continue-recovery', 'prepare-recovery', 'verify-recovery', 'inspect-recovery'}:
        from . import recovery, assignments
        from .repositories import GitHub, RepositoryError, state_lock
        try:
            read_only = args.command == 'inspect-recovery' or getattr(args, 'dry_run', False)
            uri = Path(args.state).resolve().as_uri() + ('?mode=ro' if read_only else '?mode=rw')
            with closing(sqlite3.connect(uri, uri=True)) as database, state_lock(database):
                if args.command == 'inspect-recovery':
                    result = recovery.inspect(database, args.project, args.iteration, args.operator_id)
                else:
                    github = GitHub(args.api_base, args.bearer, args.timeout)
                    if args.command == 'register-recovery':
                        result = recovery.register(database, args.project, args.iteration, args.operator_id, args.recovery_config, github)
                    elif args.command == 'continue-recovery':
                        result = recovery.continue_recovery(database, args.project, args.iteration, args.operator_id, args.recovery_config, github)
                    elif args.command == 'verify-recovery':
                        result = recovery.verify(database, args.project, args.iteration, args.operator_id, args.merge_config, github)
                    else:
                        with open(args.request, encoding='utf-8') as source:
                            request = json.load(source, object_pairs_hook=unique_fields)
                        result = recovery.prepare(database, args.project, args.iteration, args.operator_id, request, github, args.dry_run)
        except RepositoryError as error:
            raise IntakeError(error.code, str(error)) from error
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
            raise IntakeError('invalid_recovery', 'Malformed recovery evidence; freeze remains in force.') from error
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == 'reconcile-restart':
        from .restart import reconcile
        from .repositories import GitHub, RepositoryError, state_lock
        try:
            recovery = (args.recover_assignment, args.run, args.reference)
            if any(value is not None for value in recovery) and not all(value is not None for value in recovery):
                raise IntakeError('invalid_restart', 'Recovery needs exact assignment, run and explicit operator reference together.')
            uri = Path(args.state).resolve().as_uri() + '?mode=rw'
            with closing(sqlite3.connect(uri, uri=True)) as database, state_lock(database):
                result = reconcile(database, args.project, args.iteration, args.operator_id,
                                   GitHub(args.api_base, args.bearer, args.timeout),
                                   recover=recovery if recovery[0] is not None else None)
            print(json.dumps(result, sort_keys=True))
        except RepositoryError as error:
            raise IntakeError(error.code, str(error)) from error
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
            raise IntakeError('invalid_restart', 'Malformed restart state; no dispatch permitted.') from error
        return
    if args.command in {'observe-delivery', 'watch-delivery', 'inspect-delivery'}:
        from . import delivery
        from .repositories import RepositoryError, state_lock
        import math
        import time
        try:
            samples = args.samples if args.command == 'watch-delivery' else 1
            interval = args.interval if args.command == 'watch-delivery' else 1
            if not 1 <= samples <= 10000 or not math.isfinite(interval) or not 1 <= interval <= 3600:
                raise IntakeError('invalid_delivery', 'Watcher requires 1..10000 samples and finite interval in [1, 3600].')
            config = None if args.command == 'inspect-delivery' else delivery.load_config(args.delivery_config, args.operator_id)
            uri = Path(args.state).resolve().as_uri() + ('?mode=ro' if args.command == 'inspect-delivery' else '?mode=rw')
            with closing(sqlite3.connect(uri, uri=True)) as database:
                for index in range(samples):
                    with state_lock(database):
                        # Apply the same worker-mount exclusion as controlled merge configuration.
                        if config is not None:
                            from .merge import controller_paths
                            controller_paths(database, args.delivery_config)
                        result = delivery.inspect(database, args.project, args.iteration, args.operator_id) if config is None else delivery.sample(
                            database, args.project, args.iteration, args.operator_id, config)
                    print(json.dumps(result, sort_keys=True), flush=True)
                    if index + 1 < samples:
                        time.sleep(interval)
        except RepositoryError as error:
            raise IntakeError(error.code, str(error)) from error
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
            raise IntakeError('invalid_delivery', 'Malformed delivery state/configuration; no health claim permitted.') from error
        return
    if args.command in {'pause', 'emergency-stop', 'resume', 'inspect-control'}:
        from .cli_controls import run
        print(json.dumps(run(args), sort_keys=True))
        return
    if args.command in {'merge-preflight', 'merge-candidate', 'verify-delivery', 'inspect-merge'}:
        from .merge import lifecycle, inspect_merge
        from .repositories import RepositoryError
        import fcntl
        try:
            uri = Path(args.state).resolve().as_uri() + ('?mode=ro' if args.command in {'merge-preflight', 'inspect-merge'} else '?mode=rw')
            with closing(sqlite3.connect(uri, uri=True)) as database:
                with open(str(Path(args.state).resolve()) + '.onboarding.lock', 'r') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    if args.command == 'inspect-merge':
                        result = inspect_merge(database, args.project, args.iteration, args.operator_id, args.assignment)
                    else:
                        result = lifecycle(database, args.project, args.iteration, args.operator_id, args.merge_config, args.command)
        except RepositoryError as error:
            raise IntakeError(error.code, str(error)) from error
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
            raise IntakeError('invalid_merge', 'Malformed merge state or evidence; no admission permitted.') from error
        print(json.dumps(result, sort_keys=True))
        return
    if args.command in {'record-requirement-change', 'resolve-requirement-change', 'resynthesize-requirements',
                        'publish-requirement-change', 'resume-requirement-change'}:
        from . import requirements, requirement_execution, requirement_publication
        from .repositories import GitHub, RepositoryError, state_lock
        try:
            with open(args.request, encoding='utf-8') as source:
                request = json.load(source, object_pairs_hook=unique_fields)
            uri = Path(args.state).resolve().as_uri() + '?mode=rw'
            with closing(sqlite3.connect(uri, uri=True)) as database, state_lock(database):
                github = GitHub(args.api_base, args.bearer, args.timeout)
                operation = {'record-requirement-change': requirements.record_change,
                             'resolve-requirement-change': requirements.resolve_change,
                             'resynthesize-requirements': requirement_execution.execute,
                             'publish-requirement-change': requirement_publication.publish,
                             'resume-requirement-change': requirements.resume_change}[args.command]
                extra = {'config_path': args.synthesis_config} if args.command == 'resynthesize-requirements' else {}
                result = operation(database, args.project, args.iteration, args.operator_id, request, github, **extra)
                database.commit()
        except RepositoryError as error:
            raise IntakeError(error.code, str(error)) from error
        except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
            raise IntakeError('invalid_change', 'Malformed or unavailable requirement change; no resumption or evidence validity granted.') from error
        print(json.dumps(result, sort_keys=True))
        return
    if args.command in {'declare-stuck', 'prepare-diagnosis', 'request-direction', 'apply-direction'}:
        from . import diagnosis
        from .repositories import GitHub, RepositoryError, state_lock
        try:
            with open(args.request, encoding='utf-8') as source:
                request = json.load(source, object_pairs_hook=unique_fields)
            read_only = getattr(args, 'dry_run', False)
            uri = Path(args.state).resolve().as_uri() + ('?mode=ro' if read_only else '?mode=rw')
            with closing(sqlite3.connect(uri, uri=True)) as database, state_lock(database):
                github = GitHub(args.api_base, args.bearer, args.timeout)
                operation = {'declare-stuck': diagnosis.declare, 'prepare-diagnosis': diagnosis.prepare,
                             'request-direction': diagnosis.request_direction, 'apply-direction': diagnosis.apply_direction}[args.command]
                extra = {'dry_run': args.dry_run} if args.command == 'prepare-diagnosis' else (
                    {'config_path': args.diagnosis_config} if args.command == 'request-direction' else {})
                result = operation(database, args.project, args.iteration, args.operator_id, request, github, **extra)
                database.commit()
        except RepositoryError as error:
            raise IntakeError(error.code, str(error)) from error
        except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
            raise IntakeError('invalid_diagnosis', 'Malformed or unavailable debug input/state; no direction granted.') from error
        print(json.dumps(result, sort_keys=True))
        return
    if args.command in {'prepare-review', 'prepare-corrections', 'review-candidate'}:
        from . import review
        from .repositories import GitHub, RepositoryError, state_lock
        try:
            with open(args.request, encoding='utf-8') as source:
                request = json.load(source, object_pairs_hook=unique_fields)
            read_only = getattr(args, 'dry_run', False) or (args.command == 'review-candidate' and not args.review_config)
            uri = Path(args.state).resolve().as_uri() + ('?mode=ro' if read_only else '?mode=rw')
            with closing(sqlite3.connect(uri, uri=True)) as database, state_lock(database):
                github = GitHub(args.api_base, args.bearer, args.timeout)
                if args.command == 'review-candidate':
                    result = review.evaluate(database, args.project, args.iteration, args.operator_id, request, github, args.review_config)
                else:
                    operation = review.prepare if args.command == 'prepare-review' else review.prepare_corrections
                    result = operation(database, args.project, args.iteration, args.operator_id, request, github, args.dry_run)
        except RepositoryError as error:
            raise IntakeError(error.code, str(error)) from error
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
            raise IntakeError('invalid_review', 'Malformed review input/state; no advancement permitted.') from error
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == 'publish-candidate':
        from .publication import publish
        from .repositories import RepositoryError
        try:
            uri = Path(args.state).resolve().as_uri() + '?mode=rw'
            with closing(sqlite3.connect(uri, uri=True)) as database:
                result = publish(database, args.project, args.iteration, args.operator_id, args.assignment,
                                 args.publication_config, args.plan_only)
        except RepositoryError as error:
            raise IntakeError(error.code, str(error)) from error
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
            raise IntakeError('invalid_publication', 'Malformed publication inputs or persistent state; reconcile with the operator.') from error
        print(json.dumps(result, sort_keys=True))
        return
    if args.command in {'prepare-assignment', 'prepare-simplification', 'inspect-assignment', 'assignment-result', 'launch-assignment', 'stop-assignment', 'reconcile-assignment'}:
        from . import assignments
        from .repositories import GitHub, RepositoryError
        import fcntl
        try:
            if args.command == 'launch-assignment':
                if not args.launcher_config:
                    assignments.launch()
                from .sandbox import launch
                if not args.api_base:
                    raise IntakeError('invalid_launcher', 'Launch requires the trusted read-only tracker route.')
                result = launch(str(Path(args.state).resolve()), args.project, args.iteration, args.operator_id,
                                args.assignment, args.launcher_config, GitHub(args.api_base, args.bearer, args.timeout))
                print(json.dumps(result, sort_keys=True))
                return
            if args.command in {'stop-assignment', 'reconcile-assignment'}:
                from .sandbox import stop_assignment, reconcile
                uri = Path(args.state).resolve().as_uri() + '?mode=rw'
                with closing(sqlite3.connect(uri, uri=True)) as database:
                    operation = stop_assignment if args.command == 'stop-assignment' else reconcile
                    result = operation(database, args.project, args.iteration, args.operator_id, args.assignment)
                print(json.dumps(result, sort_keys=True))
                return
            request = None
            if hasattr(args, 'request'):
                with open(args.request, encoding='utf-8') as source:
                    try:
                        request = json.load(source, object_pairs_hook=unique_fields)
                    except (ValueError, RecursionError, IntakeError) as error:
                        raise IntakeError('invalid_assignment', 'Provide unambiguous bounded assignment JSON.') from error
            read_only = args.command == 'inspect-assignment' or getattr(args, 'dry_run', False)
            uri = Path(args.state).resolve().as_uri() + ('?mode=ro' if read_only else '?mode=rw')
            with closing(sqlite3.connect(uri, uri=True)) as database:
                # A dry-run must not create a lock, schema, claim, or lifecycle checkpoint.
                with open(str(Path(args.state).resolve()) + '.onboarding.lock', 'r') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    if args.command == 'inspect-assignment':
                        result = assignments.inspect(database, args.project, args.iteration, args.operator_id, args.assignment)
                    else:
                        github = GitHub(args.api_base, args.bearer, args.timeout)
                        if args.command in {'prepare-assignment', 'prepare-simplification'}:
                            from . import simplification
                            prepare = simplification.prepare if args.command == 'prepare-simplification' else assignments.prepare
                            result = prepare(database, args.project, args.iteration, args.operator_id, request, github, args.dry_run)
                        else:
                            result = assignments.store_result(database, args.project, args.iteration, args.operator_id, args.assignment, request, github)
        except RepositoryError as error:
            raise IntakeError(error.code, str(error)) from error
        print(json.dumps(result, sort_keys=True))
        return
    if args.command in {'attention', 'notifications', 'deliver', 'respond', 'attention-reconcile'}:
        from .attention import lifecycle, records, load_iteration
        request = None
        if hasattr(args, 'request'):
            with open(args.request, encoding='utf-8') as source:
                try:
                    request = json.load(source, object_pairs_hook=unique_fields)
                except (ValueError, RecursionError, IntakeError) as error:
                    raise IntakeError('invalid_decision' if args.command == 'respond' else
                                      'invalid_receipt' if args.command == 'attention-reconcile' else
                                      'invalid_attention', 'Provide unambiguous lifecycle JSON.') from error
        uri = Path(args.state).resolve().as_uri() + ('?mode=ro' if args.command == 'notifications' else '?mode=rw')
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.OperationalError as error:
            raise IntakeError('not_found', 'Iteration state is unavailable.') from error
        with closing(connection) as database:
            if args.command == 'notifications':
                item = load_iteration(database, args.project, args.iteration)
                result = records(database, args.project, args.iteration, item)
            else:
                result = lifecycle(database, args.project, args.iteration, args.operator_id, args.command,
                                   request, getattr(args, 'event', None), getattr(args, 'transport_config', None))
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == 'model-call':
        from .spending import SpendingError, broker_call
        with open(args.request, encoding='utf-8') as source:
            try:
                request = json.load(source, object_pairs_hook=unique_fields)
            except (ValueError, RecursionError) as error:
                raise IntakeError('invalid_request', 'Model request must be unambiguous UTF-8 JSON.') from error
        if not isinstance(request, dict):
            raise IntakeError('invalid_request', 'Model request must be a JSON object.')
        try:
            result = broker_call(args.socket, args.operation_id, args.reserve, request)
        except SpendingError as error:
            raise IntakeError(error.code, str(error)) from error
        print(json.dumps(result, sort_keys=True))
        return
    if args.command == 'model-broker':
        from .spending import SpendingError, serve_unix_socket
        try:
            serve_unix_socket(args.socket, args.state, (args.project, args.iteration, args.provider, args.model, args.operation), args.fixture_url, args.timeout, args.operator_id, args.subscription_socket, args.reservation, args.ttl, args.max_calls, args.worker_uid)
        except SpendingError as error:
            raise IntakeError(error.code, str(error)) from error
        return
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
        uri = Path(args.state).resolve().as_uri() + ('?mode=ro' if args.command == 'inspect' else '?mode=rw')
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.OperationalError as error:
            raise IntakeError('not_found', 'Iteration state is unavailable.') from error
        with closing(connection) as database:
            result = read_iteration(database, args.project, args.iteration)
            if result is None:
                raise IntakeError('not_found', 'No iteration exists with these exact identities.')
            if args.command == 'inspect':
                from .controls import read
                from .repositories import RepositoryError
                try:
                    control = read(database, args.project, args.iteration)
                except RepositoryError as error:
                    raise IntakeError(error.code, str(error)) from error
                if control is not None:
                    result['operator_control'] = {k: control[k] for k in ('request_id', 'action', 'state', 'reference')}
                    if control['action'] != 'resume':
                        result['status'] = 'paused'
            if args.command not in {'inspect', 'spend-grant', 'spend-reconcile', 'spend-resume'}:
                if result.get('approval', {}).get('operator_id') != args.operator_id or not text(result.get('approval', {}).get('reference')):
                    raise IntakeError('approval_required', 'Recorded approval from the configured operator is required for this lifecycle operation.')
            if args.command in {'spend-grant', 'spend-reconcile', 'spend-resume'}:
                from .spending import SpendingError, add_grant, initialize, record_refusal, resume_quota, reconcile as reconcile_spend
                from .repositories import state_lock
                try:
                    with state_lock(database), database:
                        initialize(database)
                        if result.get('approval', {}).get('operator_id') != args.operator_id or not text(result.get('approval', {}).get('reference')):
                            raise SpendingError('approval_required', 'Recorded approval from the configured operator is required for this lifecycle operation.')
                        if args.command == 'spend-grant':
                            result = add_grant(database, args.project, args.iteration, args.provider,
                                               args.model, args.operation, args.kind, args.ceiling,
                                               args.expires, args.reference)
                        elif args.command == 'spend-resume':
                            result = resume_quota(database, (args.project, args.iteration, args.provider, args.model, args.operation), args.reference)
                        else:
                            result = reconcile_spend(database, args.project, args.iteration,
                                args.provider, args.model, args.operation, args.operation_id,
                                args.outcome, args.actual_units, args.reference)
                except SpendingError as error:
                    if error.code != 'invalid_scope':
                        record_refusal(database, (args.project, args.iteration, args.provider, args.model, args.operation), getattr(args, 'operation_id', None), error)
                    raise IntakeError(error.code, str(error)) from error
            if args.command in {'plan', 'complete-planning'}:
                from .planning import plan, complete
                from .repositories import GitHub, RepositoryError, state_lock
                with open(args.request, encoding='utf-8') as source:
                    try:
                        request = json.load(source, object_pairs_hook=unique_fields)
                    except (ValueError, RecursionError) as error:
                        raise IntakeError('invalid_planning', 'Provide unambiguous planning JSON within parser limits.') from error
                try:
                    with state_lock(database), database:
                        if args.command == 'plan':
                            result = plan(database, result, request)
                        else:
                            result = complete(database, result, request,
                                              GitHub(args.api_base, args.bearer, args.timeout))
                except RepositoryError as error:
                    raise IntakeError(error.code, str(error)) from error
            if args.command in {'synthesize-tickets', 'complete-tickets', 'publish-tickets', 'frontier', 'reserve-ticket',
                                'synthesize-bug', 'complete-bug', 'publish-bug'}:
                from . import tickets
                from .repositories import GitHub, RepositoryError, state_lock
                request = None
                if args.command in {'synthesize-tickets', 'complete-tickets', 'synthesize-bug', 'complete-bug', 'publish-bug'}:
                    with open(args.request, encoding='utf-8') as source:
                        try:
                            request = json.load(source, object_pairs_hook=unique_fields)
                        except (ValueError, RecursionError) as error:
                            raise IntakeError('invalid_tickets', 'Provide unambiguous ticket-stage JSON.') from error
                try:
                    github = GitHub(args.api_base, args.bearer, args.timeout)
                    with state_lock(database), database:
                        if args.command in {'synthesize-bug', 'complete-bug', 'publish-bug'}:
                            from . import bugs
                            operation = {'synthesize-bug': bugs.synthesize, 'complete-bug': bugs.complete, 'publish-bug': bugs.publish}
                            result = operation[args.command](database, result, request, github)
                        elif args.command == 'synthesize-tickets':
                            result = tickets.synthesize(database, result, request, github)
                        elif args.command == 'complete-tickets':
                            result = tickets.complete_synthesis(database, result, request, github)
                        elif args.command == 'publish-tickets':
                            result = tickets.publish(database, result, github)
                        elif args.command == 'frontier':
                            result = tickets.frontier(database, result, github)
                        else:
                            result = tickets.reserve(database, result, github, args.issue)
                except RepositoryError as error:
                    raise IntakeError(error.code, str(error)) from error
            if args.command == 'onboard':
                if result.get('approval', {}).get('operator_id') != args.operator_id or not text(result.get('approval', {}).get('reference')):
                    raise IntakeError('approval_required', 'Recorded approval from the configured operator is required before repository access.')
                receipt = None
                if args.creation_receipt:
                    with open(args.creation_receipt, encoding='utf-8') as source:
                        try:
                            receipt = json.load(source, object_pairs_hook=unique_fields)
                        except (ValueError, RecursionError) as error:
                            raise IntakeError('invalid_receipt', 'Provide an unambiguous trusted controller creation receipt.') from error
                from .repositories import GitHub, RepositoryError, onboard
                try:
                    with database:
                        result = onboard(database, result, GitHub(args.api_base, args.bearer, args.timeout), receipt)
                except RepositoryError as error:
                    raise IntakeError(error.code, str(error)) from error
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
