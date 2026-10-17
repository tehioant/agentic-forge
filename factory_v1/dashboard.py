"""Standalone local dashboard: scoped observations only, never controller actions."""
import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3

from factory_v1.dashboard_status import project_worker_status
from factory_v1.dashboard_board import read_board, render_board


IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')
STAGES = {'implementation', 'corrections', 'simplify', 'review-standards',
          'review-spec', 'diagnosis', 'repair', 'to-tickets', 'planning'}
ROLES = {'implementation', 'simplification', 'review', 'debug', 'repair'}
MAX_ROWS = 5000
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024


class SourceUnavailable(ValueError):
    """Malformed, ambiguous or oversized observation; never expose source details."""


def identifier(value):
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None


def object_record(value):
    if not isinstance(value, dict):
        raise SourceUnavailable()
    return value


def optional_record(value):
    return {} if value is None else object_record(value)


def decode(payload):
    if not isinstance(payload, str) or len(payload.encode()) > MAX_PAYLOAD_BYTES:
        raise SourceUnavailable()
    return object_record(json.loads(payload))


def bounded_rows(cursor):
    rows = cursor.fetchmany(MAX_ROWS + 1)
    if len(rows) > MAX_ROWS:
        raise SourceUnavailable()
    return rows


def row(identity, iteration, kind, role=None, stage=None, number=None, run_id=None):
    repository = iteration['repository']
    return {
        'id': identity, 'iteration_id': iteration['iteration_id'], 'kind': kind,
        'role': role, 'stage': stage, 'issue_number': number,
        'issue_url': f'https://github.com/{repository}/issues/{number}' if number else None,
        'task': f'Issue #{number}' if number else None, 'run_id': run_id,
        'status': {'state': 'unknown', 'label': 'Record (activity unknown)',
                   'reason': None, 'blocker': None},
    }


def recorded_work(iteration):
    """Conversation planning and synthesis are records, not a spawned-agent roster."""
    records = []
    for key, kind, role, stage in (
        ('planning', 'planning_record', 'planning', 'planning'),
        ('ticket_work', 'synthesis_record', 'ticket synthesis', 'to-tickets'),
    ):
        work = optional_record(iteration.get(key))
        if work:
            identity = work.get('assignment_id')
            if not identifier(identity):
                raise SourceUnavailable()
            records.append(row(identity, iteration, kind, role, stage))
    for work in optional_record(iteration.get('bug_work')).values():
        work = object_record(work)
        identity = work.get('assignment_id')
        if not identifier(identity):
            raise SourceUnavailable()
        records.append(row(identity, iteration, 'synthesis_record', 'bug synthesis', 'to-tickets'))
    return records


def assignment_row(indexed_id, assignment, iterations):
    scope = object_record(assignment.get('ticket_scope'))
    iteration = iterations.get(scope.get('iteration'))
    if (not identifier(indexed_id) or assignment.get('assignment_id') != indexed_id or
            iteration is None or scope.get('repository') != iteration['repository']):
        raise SourceUnavailable()
    handoff = optional_record(assignment.get('handoff'))
    if handoff.get('repository', iteration['repository']) != iteration['repository']:
        raise SourceUnavailable()
    issue = optional_record(handoff.get('issue'))
    number = scope.get('issue_number')
    if number is not None and (type(number) is not int or number <= 0):
        raise SourceUnavailable()
    if issue.get('number', number) != number:
        raise SourceUnavailable()
    runtime = optional_record(assignment.get('runtime'))
    run_id = runtime.get('run_id')
    if run_id is not None and not identifier(run_id):
        raise SourceUnavailable()
    kind = 'worker_attempt' if runtime else 'prepared_assignment'
    role = optional_record(handoff.get('profile')).get('role')
    stage = handoff.get('stage')
    projected = row(indexed_id, iteration, kind, role if role in ROLES else None,
                    stage if stage in STAGES else None, number, run_id)
    projected['status'] = project_worker_status(assignment, repository=iteration['repository'])
    return projected


def read_workers(database_path, project_id):
    """Read one project in one query-only transaction; retain historical attempts."""
    uri = Path(database_path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as database:
        database.execute('PRAGMA query_only=ON')
        database.execute('PRAGMA trusted_schema=OFF')
        # Bound work on corrupt or unexpectedly large databases, not just output size.
        budget = 1000
        def progress():
            nonlocal budget
            budget -= 1
            return budget <= 0
        database.set_progress_handler(progress, 1000)
        database.execute('BEGIN')
        records = bounded_rows(database.execute(
            'SELECT iteration_id, payload FROM iterations WHERE project_id=? ORDER BY iteration_id',
            (project_id,)))
        iterations = {}
        workers = []
        for indexed_id, payload in records:
            item = decode(payload)
            repository = item.get('repository')
            if (not identifier(indexed_id) or item.get('project_id') != project_id or
                    item.get('iteration_id') != indexed_id or indexed_id in iterations or
                    not isinstance(repository, str) or not REPOSITORY.fullmatch(repository)):
                raise SourceUnavailable()
            iterations[indexed_id] = item
            workers.extend(recorded_work(item))
        exists = database.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='role_assignments'").fetchone()
        if exists:
            # Invalid JSON cannot be safely scoped, so fail closed rather than hide corruption.
            records = bounded_rows(database.execute(
                "SELECT assignment_id, payload FROM role_assignments WHERE "
                "CASE WHEN json_valid(payload) THEN json_extract(payload, '$.ticket_scope.project')=? "
                "ELSE 1 END ORDER BY assignment_id", (project_id,)))
            identities = set()
            for indexed_id, payload in records:
                assignment = decode(payload)
                if (optional_record(assignment.get('ticket_scope')).get('project') != project_id or
                        indexed_id in identities):
                    raise SourceUnavailable()
                identities.add(indexed_id)
                workers.append(assignment_row(indexed_id, assignment, iterations))
        if len(workers) > MAX_ROWS:
            raise SourceUnavailable()
        return sorted({item['repository'] for item in iterations.values()}), workers


def validate_board_selection(repository, github_project):
    if repository is None and github_project is None:
        return
    if (not isinstance(repository, str) or not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}', repository) or
            not isinstance(github_project, str) or not re.fullmatch(
                r'PVT_[A-Za-z0-9_-]{1,150}', github_project)):
        raise ValueError('Select both --repository owner/repo and --github-project PVT_... .')


def snapshot(database_path, project_id, *, now=None, repository=None, github_project=None):
    """Independent source projections; fetch time is never a worker heartbeat."""
    if not identifier(project_id):
        raise ValueError('Select an exact project identifier.')
    validate_board_selection(repository, github_project)
    fetched_at = (now or datetime.now(timezone.utc)).isoformat()
    source = {'status': 'ok', 'fetched_at': fetched_at, 'observed_at': None, 'error': None}
    repositories, workers = [], []
    try:
        repositories, workers = read_workers(database_path, project_id)
    except (sqlite3.Error, OSError, ValueError, TypeError, RecursionError):
        source.update(status='unavailable', error='Forge records unavailable or invalid.')
    github = {'status': 'unconfigured', 'fetched_at': None, 'observed_at': None,
              'error': None, 'scope': 'unverified'}
    board = {'status': 'unconfigured', 'observed_at': None, 'project': None,
             'tickets': [], 'error': None}
    if repository is not None:
        if source['status'] != 'ok' and Path(database_path).exists():
            github.update(status='unavailable', error='Forge repository scope unavailable; board selection cannot be verified.')
            board.update(status='unavailable', error=github['error'])
        elif repositories and repository not in repositories:
            github.update(status='unavailable', scope='mismatch',
                          error='Selected repository does not match recorded Forge project.')
            board.update(status='unavailable', error=github['error'])
        else:
            # The adapter alone owns host gh reads; never infer a board from Forge state.
            board = read_board(repository, github_project)
            github.update(status='ok' if board['status'] == 'available' else 'unavailable',
                          observed_at=board['observed_at'], error=board['error'],
                          fetched_at=(now or datetime.now(timezone.utc)).isoformat(),
                          scope='recorded_repository_match' if repositories else 'unverified')
    data = {'version': 1, 'project': {'id': project_id, 'repositories': repositories},
            'sources': {'forge': source, 'github': github}, 'workers': workers, 'board': board}
    data['fragments'] = render_fragments(data)
    return data


KINDS = {'prepared_assignment': 'Prepared assignment (not launched)',
         'worker_attempt': 'Recorded worker attempt',
         'planning_record': 'Planning record (originating conversation)',
         'synthesis_record': 'Synthesis record (not a worker roster)'}
STYLE = '''body{margin:0;background:#131518;color:#e6e7e9;font:14px system-ui,sans-serif}
main{max-width:1100px;margin:36px auto;padding:0 24px}h1{font-size:24px;margin-bottom:8px}
h2{font-size:18px;margin-top:30px}.muted,small{color:#a9aeb7}a{color:#a8c6ff}
table{width:100%;border-collapse:collapse;text-align:left}th,td{padding:12px 10px;
border-bottom:1px solid #343840;vertical-align:top}th{color:#b4bac4;font-weight:500}
small{display:block;margin-top:5px}.notice{padding:16px;background:#24282e;border:1px solid #454c57}
.table-wrap{overflow-x:auto}code{font-size:12px}
#ticket-board{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
#ticket-board>h2,#ticket-board>p{grid-column:1/-1}.board-column{background:#20242a;padding:12px}
.board-column ul{padding-left:18px}.board-column li{margin-bottom:14px}.board-column p{font-size:12px}'''


def text(value):
    return escape(str(value)) if value is not None else 'Unknown'


def render_workers(data):
    """Render only allowlisted projected worker fields."""
    source = data['sources']['forge']
    rows = []
    for worker in data['workers']:
        task = text(worker['task'])
        if worker['issue_url']:
            task = f'<a href="{escape(worker["issue_url"], quote=True)}" rel="noreferrer">{task}</a>'
        status = worker['status']
        activity = text(status['label'])
        if status['reason']:
            activity += f'<small>{text(status["reason"])}</small>'
        if status['blocker']:
            blocker = status['blocker']
            activity += (f'<small>Blocked by <a rel="noreferrer" href="{escape(blocker["url"], quote=True)}">'
                         f'#{text(blocker["number"])}</a></small>')
        rows.append(f'<tr><td>{text(worker["role"])}<small>{text(KINDS[worker["kind"]])}</small></td>'
                    f'<td>{task}</td><td>{text(worker["stage"])}</td><td>{activity}</td>'
                    f'<td>{text(worker["iteration_id"])}<small>{text(worker["id"])}</small></td></tr>')
    if source['status'] != 'ok':
        content = '<p class="notice" role="status">Forge source unavailable. No healthy empty view can be inferred.</p>'
    elif not rows:
        content = '<p class="notice">No recorded assignments or worker attempts for this project.</p>'
    else:
        content = ('<div class="table-wrap"><table><thead><tr><th>Role / record</th><th>Task</th>'
                   '<th>Stage</th><th>Recorded activity</th><th>Iteration / assignment</th></tr></thead><tbody>' +
                   ''.join(rows) + '</tbody></table></div>')
    repositories = ', '.join(data['project']['repositories']) or 'Repository unknown'
    return (f'<p>{text(repositories)}</p><h2>Recorded workers &amp; assignments</h2>'
            '<p class="muted">Recorded attempts do not establish live activity. Planning and synthesis '
            'are conversation/workflow records, not a complete agent tree. Worker Done does not '
            'mean ticket delivery.</p>' + content +
            f'<p class="muted">Dashboard fetched: {text(source["fetched_at"])}<br>'
            f'Source observation time: {text(source["observed_at"])}</p>')


def render_fragments(data):
    github = data['sources']['github']
    if github['status'] == 'unconfigured':
        board = '<h2>GitHub Projects tickets</h2><p>Board unconfigured. Select an explicit repository and GitHub Project.</p>'
    else:
        scope = {'recorded_repository_match': 'Repository matches recorded Forge project.',
                 'mismatch': 'Selected repository does not match recorded Forge project.',
                 'unverified': 'Independent selected board; Forge project scope unverified.'}[github['scope']]
        board = (render_board(data['board']) + f'<p class="muted">{scope}<br>'
                 f'GitHub dashboard fetched: {text(github["fetched_at"])}</p>')
    return {'forge': render_workers(data), 'github': board}


def render_page(data):
    """Page and JSON share exact sanitized per-source rendered fragments."""
    project = data['project']
    fragments = data.get('fragments') or render_fragments(data)
    sections = []
    for name in ('forge', 'github'):
        source = data['sources'][name]
        sections.append(f'<p class="muted" role="status" id="{name}-notice"></p>'
                        f'<div id="{name}-source" data-source-status="{text(source["status"])}" '
                        f'data-observed-at="{escape(source["observed_at"] or "", quote=True)}">'
                        f'{fragments[name]}</div>')
    return (f'<!doctype html><html lang="en"><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{text(project["id"])} · Forge</title><style>{STYLE}</style><main>'
            f'<p class="muted">FORGE / READ-ONLY</p><h1>{text(project["id"])}</h1>' +
            ''.join(sections) +
            '</main><script src="/assets/polling.js" defer></script>'
            '<script src="/assets/bootstrap.js" defer></script></html>')


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str


class Dashboard:
    def __init__(self, database_path, project_id, *, repository=None, github_project=None):
        if not identifier(project_id):
            raise ValueError('Select an exact project identifier.')
        validate_board_selection(repository, github_project)
        self.database_path = database_path
        self.project_id = project_id
        self.repository = repository
        self.github_project = github_project

    def route(self, method, path):
        """Explicit read resources only; never a generic file server."""
        if method != 'GET':
            return Response(405, b'Method not allowed', 'text/plain; charset=utf-8')
        assets = {'/assets/polling.js': 'polling.js', '/assets/bootstrap.js': 'bootstrap.js'}
        if path in assets:
            body = (Path(__file__).with_name('dashboard_web') / assets[path]).read_bytes()
            return Response(200, body, 'text/javascript; charset=utf-8')
        if path not in {'/', '/api/snapshot'}:
            return Response(404, b'Not found', 'text/plain; charset=utf-8')
        data = snapshot(self.database_path, self.project_id, repository=self.repository,
                        github_project=self.github_project)
        if path == '/api/snapshot':
            return Response(200, json.dumps(data, allow_nan=False).encode(), 'application/json; charset=utf-8')
        return Response(200, render_page(data).encode(), 'text/html; charset=utf-8')


def handler_for(application, port):
    allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            hosts = self.headers.get_all('Host', [])
            if (len(hosts) != 1 or hosts[0] not in allowed_hosts or
                    self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                response = Response(403, b'Local origin required', 'text/plain; charset=utf-8')
            else:
                response = application.route(self.command, self.path)
            self.send_response(response.status)
            self.send_header('Content-Type', response.content_type)
            self.send_header('Content-Length', str(len(response.body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            if response.status == 405:
                self.send_header('Allow', 'GET')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(response.body)

        do_GET = respond
        do_POST = respond
        do_PUT = respond
        do_PATCH = respond
        do_DELETE = respond
        do_HEAD = respond
        do_OPTIONS = respond

        def log_message(self, format, *args):
            pass
    return Handler


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument('--database', type=Path, required=True, help='Existing Forge SQLite database')
    command.add_argument('--project', required=True, help='Exact registered project_id (all its iterations)')
    command.add_argument('--port', type=int, default=8787)
    command.add_argument('--repository', help='Explicit owner/repo for the optional GitHub board')
    command.add_argument('--github-project', help='Explicit existing ProjectV2 node ID (PVT_...)')
    return command


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    if not identifier(args.project) or not 1 <= args.port <= 65535:
        command.error('Use an exact project identifier and a port from 1 to 65535.')
    try:
        application = Dashboard(args.database, args.project, repository=args.repository,
                                github_project=args.github_project)
    except ValueError as error:
        command.error(str(error))
    with ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(application, args.port)) as server:
        print(f'Forge dashboard: http://127.0.0.1:{args.port} (read-only)')
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
