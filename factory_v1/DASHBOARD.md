# Local Forge dashboard

Run from the checkout (Python stdlib):

```sh
mise exec -- python -m factory_v1.dashboard --database /absolute/path/to/forge.sqlite --project PROJECT_ID
```

Open **http://127.0.0.1:8787/** on the server. `--port 8788` selects another
loopback port. `PROJECT_ID` is an exact Forge project identifier, not a GitHub
Projects number. All recorded iterations and historical assignments are retained.
The database must already exist; a missing database is never created.

## Optional secondary GitHub Projects board

```sh
mise exec -- python -m factory_v1.dashboard --database /absolute/path/to/forge.sqlite --project PROJECT_ID --repository owner/repo --github-project PVT_EXACT_NODE_ID
```

Both board flags are required together. Neither flag means **unconfigured**, not
an inferred board. The repository must match a selected project's recorded
repository when observations exist. A mismatch refuses the board read. When the
Forge database is missing or has no repository observations, an explicitly
selected board can still be read independently, labelled **Forge project scope
unverified**. GitHub repository/project linkage is independently checked by the
board adapter. A linked old board is not evidence of current ticket membership.
An existing invalid/unreadable database refuses the board read rather than bypassing
an unverifiable repository scope.

The host needs `gh` installed and authenticated with read access to the selected
repository and existing ProjectV2. The bounded adapter performs only allowlisted
GraphQL reads via host `gh`; credentials never enter the browser. No board,
issue, status, layout or Forge state is written. Tests mock this external boundary.

## What is shown

The worker-first view shows role, issue link, stage, iteration/assignment and
sanitized recorded status/reason. Prepared assignments say **not launched**.
Running is explicitly **recorded; not live-verified**. Failure, stopped,
unconfirmed cleanup and unknown outcomes remain distinct. **Done** requires a
correlated admitted successful assignment result and confirmed cleanup; it never
means its ticket was merged, deployed or delivered. Finished rows remain visible.
Planning and synthesis are labelled records with unknown activity, not a live
roster or inferred parent/subagent tree. Known reasons and canonical blocker links
are rendered when provided by trusted projections, never from arbitrary worker text.

The compact secondary board preserves authoritative GitHub source statuses, with
Todo, In Progress, Blocked, Done and Unknown columns. Native dependency-derived
Blocked retains the source status and is labelled derived. Worker completion
never changes ticket status. Missing fields remain Unknown. Independent source
failures do not hide the other source. No logs, transcripts or controls are exposed.

## Safety and snapshot contract

This standalone application is independent of Hermes. It binds only to 127.0.0.1;
there is no public bind flag, authentication layer, proxy setup or deployment.
Do not expose it through an unauthenticated proxy. Local users can see the
sanitized selected observations. Ctrl-C stops the dashboard, not Forge workers.
Host validation and cross-site Fetch Metadata refusal protect the local response
boundary. Responses are no-store, nosniff and no-referrer, with restrictive CSP.

`factory_v1.dashboard.snapshot(database_path, project_id, *, now=None,
repository=None, github_project=None)` supplies both `GET /` and
`GET /api/snapshot`. Snapshot v1 contains:

- `project: {id, repositories}` from validated selected Forge iterations.
- `sources.forge: {status, fetched_at, observed_at, error}`.
- `sources.github`: the same metadata plus `scope` (`unverified`, `mismatch`,
  or `recorded_repository_match`); status is `ok`, `unavailable` or `unconfigured`.
- `workers`: allowlisted identities, kind, role, task, issue number/link, stage,
  run ID and `status: {state, label, reason, blocker}` only.
- `board`: sanitized board observation from the separate host read adapter.
- `fragments: {forge, github}`: escaped server-rendered source HTML, generated
  from those exact projections, consumed by a single shared poller.

Forge `observed_at` remains null: persisted records establish no heartbeat or
source observation time. GitHub observation time comes only from a complete
successful board read. Dashboard fetch time is separate per source, never a
worker heartbeat or start/finish timestamp. Source errors are fixed sanitized
messages inside HTTP 200 responses, not healthy empty results or raw exceptions.

SQLite reads use mode=ro, query_only, trusted_schema=OFF, bounded SELECTs and one
consistent transaction. Indexed/payload project, iteration, assignment, repository
and issue identities must agree. Historical records are not reconciled or advanced.
No controller, frontier, model, worker or control operations run on page reads.

## Automatic refresh

The page loads only `/assets/polling.js` and `/assets/bootstrap.js`. CSP permits
scripts and connections only from the same origin. One read-only GET poll starts
immediately, then waits five seconds after completion before the next request;
there is no second board loop. The 75-second timeout bounds client fetch/body
processing and allows the host adapter's bounded pagination (up to eight reads,
eight seconds each). No overlapping browser requests accumulate. Pagehide aborts
the current browser request, clears timers and suppresses late UI updates.
Aborting the browser does not cancel an already admitted bounded host read.

Each source independently retains its last successful sanitized HTML fragment,
source observation timestamp and visibly stale/unavailable notice on failure.
Initial failure never becomes a healthy empty project. Recovery replaces only
the recovered source. Forge's null source time stays **unknown / freshness
unverified (stale)** despite successful reads. Notices never display raw transport
errors. An unconfigured board stays unconfigured. The UI inserts HTML only from
the same-origin server's allowlisted escaped projections, not raw source records.

Unsupported routes (including other asset paths) are 404; non-GET methods are 405.
There is no generic file/asset server.

## Focused checks

```sh
mise exec -- python -B -m unittest factory_v1.tests.test_dashboard factory_v1.tests.test_dashboard_status factory_v1.tests.test_dashboard_board -v
node --test factory_v1/dashboard_web/polling.test.js
```

Six focused methods use direct records and mocked external boundaries; no real
server, service, worker, container, E2E or controller journey is required.
Two Node tests use a fake clock, mocked fetch and a minimal fake DOM, executing
the actual poller/bootstrap for partial failures, recovery, retention and pagehide.
