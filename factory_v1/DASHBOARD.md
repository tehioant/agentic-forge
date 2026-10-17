# Local Forge dashboard

Run from the repository checkout with its Python runtime (stdlib only):

```sh
python -m factory_v1.dashboard --database /absolute/path/to/forge.sqlite --project PROJECT_ID
```

With the repository's pinned tools, prefix that command with `mise exec --`.
Open **http://127.0.0.1:8787/** on the server. `--port 8788` selects another port.
The database must already exist; `PROJECT_ID` is the exact registered `project_id`,
not a GitHub Projects number. All recorded iterations for that project are included,
including historical attempts. The project ID and recorded repositories appear at
the top of the page. Stop the dashboard with Ctrl-C; this does not stop Forge workers.
The application is independent of Hermes and does not read service credentials or
service configuration. It binds only to `127.0.0.1`; there is no public bind flag,
authentication layer, remote access setup, or deployment in this increment. Do not
publish it behind an unauthenticated proxy. Local users with browser/server access
can see the sanitized selected-project observations.

## What is shown

The compact worker-first page displays role, linked issue when recorded, stage and
iteration/assignment identity. Prepared assignments explicitly say **not launched**.
A recorded runtime becomes a **recorded worker attempt**, not proof of a live or
successfully launched worker (even a preparing/finished attempt is retained).
Planning in the originating conversation and ticket/bug synthesis have separate
record labels; these are not a complete live-agent roster or parent/subagent tree.
Missing tasks, roles and stages stay **Unknown**. No logs, transcripts or controls
are available. Missing/corrupt sources show **unavailable**, distinct from a healthy
project with no recorded assignments. Unsupported schemas or ambiguous scope fail
closed instead of silently showing an empty project.

## Reader and extension contract

`factory_v1.dashboard.snapshot(database_path, project_id, *, now=None)` is the single
public projection used by both `GET /` (server-rendered HTML) and
`GET /api/snapshot` (JSON). `Dashboard.route(method, path)` returns a `Response`
with `status`, UTF-8 `body` bytes and `content_type`, without a server/socket.
All other paths are 404; non-GET methods are 405. Snapshot source errors are
represented inside a 200 snapshot/page, so consumers must inspect source status.
Responses are non-cacheable and same-local-origin only (Host validation plus
cross-site Fetch Metadata refusal); there is no CORS access. HTML is escaped.

Snapshot v1:

- `version`: `1`
- `project`: `{id, repositories}` (repositories are from validated selected iterations)
- `sources.forge`: `{status: "ok" | "unavailable", fetched_at, observed_at, error}`
- `workers`: records with exactly `{id, iteration_id, kind, role, stage,
  issue_number, issue_url, task, run_id}`
- `kind`: `prepared_assignment`, `worker_attempt`, `planning_record` or `synthesis_record`
- Unavailable optional scalar values are `null`; `observed_at` is currently always
  `null`, because these persisted records do not establish an observation timestamp.
  `fetched_at` is UTC dashboard fetch time, **not a worker heartbeat**. `now` is a
  deterministic clock seam. An unavailable source has no projected rows and a
  constant sanitized error, never the database exception or host path.

The reader opens an SQLite URI with `mode=ro`, sets `query_only=ON` and
`trusted_schema=OFF`, and uses one deferred consistent read transaction. It never
uses `immutable=1`, so normal WAL observations remain visible. It runs bounded
SELECTs, validates indexed iteration/assignment identity against payload identity,
requires exact payload project/iteration/repository/issue scope, and allowlists
output fields. Role-assignment absence is normal before the first preparation.
Malformed JSON that cannot safely be scoped fails closed. Limits (5,000 records,
4 MiB per selected payload, SQLite operation budget and one-second lock timeout)
produce unavailable, not a misleading truncated roster. No controller imports,
state/control locks, reconciliation, frontier, model, GitHub or subprocess calls
are involved. This does not promise zero SQLite-internal read coordination or
replace normal database permissions.

Later slices extend this same snapshot/view: #62 supplies a separate structured
status adapter (raw runtime/results stay host-side), #63 supplies bounded refresh
with independent source freshness/retention, #64 adds a separate `sources.github`
and secondary board. This increment deliberately does not claim live status,
worker completion semantics, polling, or a ticket board.

## Focused checks

```sh
mise exec -- python -m unittest factory_v1.tests.test_dashboard -v
```

Two methods use explicit minimal SQLite records and mocked HTTP/request boundaries.
They exercise the actual reader, HTML renderer, router and HTTP response adapter;
no server, worker, controller journey, installed Hermes home or container is used.
