"""Operator-control CLI adapter; durable admission stays in controls."""

import sqlite3
from contextlib import closing
from pathlib import Path

from .errors import IntakeError


def run(args):
    from . import controls
    from .repositories import RepositoryError
    try:
        uri = Path(args.state).resolve().as_uri() + '?mode=rw'
        with closing(sqlite3.connect(uri, uri=True)) as database:
            if args.command == 'inspect-control':
                result = controls.inspect(database, args.project, args.iteration, args.operator_id)
            elif args.command == 'resume':
                from .repositories import GitHub
                result = controls.resume(database, args.project, args.iteration, args.operator_id, args.reference,
                                         GitHub(args.api_base, args.bearer, args.timeout))
            else:
                result = controls.request(database, args.project, args.iteration, args.operator_id, args.command, args.reference)
    except RepositoryError as error:
        raise IntakeError(error.code, str(error)) from error
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
        raise IntakeError('invalid_control', 'Malformed control state; no execution authorized.') from error
    return result
