# Sandbox and model-runtime test refactor audit

## Scope and evidence level

Audited all eight assigned modules. Seven are rewritten at unit/component seams;
`test_sandbox_result.py` stays byte-for-byte unchanged because its three tests already
exercise the prompt expression and evidence validator without execution. The five
`DockerRemovalTests` methods in `test_sandbox.py` and the already-pure expired-deadline
method in `test_sandbox_check_worker.py` also stay unchanged.

This lane changes tests and this document only. It does not change production,
source/skill pins, CI, runner policy, `AGENTS.md`, `test_sandbox_cleanup.py`, or
`test_broker_controls.py`. Concurrent lanes own their separate changes.

The old `SandboxTests` setup/launcher helper API remains for compatibility with
other lanes, but that class has **no discovered test methods**. None of the rewritten
tests call it. There are no skipped tests or replacement E2E fixtures. The component
launcher calls the actual `sandbox.launch` function directly: snapshot acquisition,
external assignment revalidation, Docker operations, broker scheduling and the guard
process are mocked boundaries. It never runs a CLI, worker, broker thread or native
process. Real artifact validators, source manifests, cancellation handlers,
SQLite persistence/rollback and final result-admission guards remain active.

## Coverage mapping

| Audited module | Former execution cost | Direct replacement and preserved invariants |
| --- | --- | --- |
| `test_sandbox.py` | Git-created repository, tracker HTTP server, CLI subprocesses, model UDS server, fixture Docker/worker/guard processes, polling | Explicit private state/config/artifacts and launcher component; disjoint controller/source scopes, path aliases/symlinks, config ownership/privacy/limits, nonroot mount modes, recovery before worker start, one-attempt ownership, untrusted disposition, stopped/reconciled durable readback. Real Docker policy verifier covers unexpected mounts, network, writable source, credentials and bounded tmpfs; real authorization covers provider tools and stopped capabilities. Real source snapshot with mocked Git return bytes covers packed-object copying, ignored repository config/alternates/untracked secrets, exact physical pins, symlink/submodule/escape rejection and reader cleanup. Real evidence validator covers selected-load pins, executing-context read correlation, contents/truncation, run identity, symlink/malformed artifacts and missing response. Unlinked diagnostic prose is refused directly by the precursor guard. Existing removal conflict/absence/idempotency/error tests are unchanged. |
| `test_spending.py` | HTTP/UDS servers, broker/client CLI processes, actual network calls, TTL waits | Real `add_grant`, `execute_controlled`, `reconcile`, `resume_quota`, startup/admission functions, private file-backed SQLite and mocked adapter. Exact-scope allowance/approval, expiry, fixed reservations, durable refusals, secret-free receipts, grant/operation/receipt idempotency/conflicts, debit/refund, paid fallback refusal, quota restoration only after billing reconciliation and explicit resume, bounded JSON/request sizes, peer UID and request-count limits. Real readback through reopened connections replaces process restart. |
| `test_spending_cancel.py` | Native broker threads, socket clients/stalled HTTP transports, real lock waits and TTL expiry | Actual broker loop with mocked sockets/transport and captured cancellation-thread target. Cancellation is injected **during an already admitted pending operation**, preserving one outbound call, cancellation of registered listener/client/adapter, helper join and capability unlink. Late returned success remains pending/uncertain, allowance remains debited, and same/new-key retries cannot forward. Idle accept, partial request, authorization, original deadline, post-lock stop/deadline and observer-failure cleanup are separately exercised. No executor joins or held-lock failure waits; the captured helper's wait is bounded to one iteration even under mutation. |
| `test_model_access_cancel.py` | Stalled native TCP/UDS servers and caller threads | Actual HTTP parsing and `SocketCancellation` over in-memory socket streams. The response/status, incomplete headers, content-length, EOF-framed and chunked-body matrix cancels at the corresponding read; sockets are shut down/retired and retries cannot connect. Pre-call cancellation, idempotent registration/removal, ignored shutdown errors and redirect refusal remain covered. Positive subscription SSE assembly, fixed route/model/tier/store/stream and 429/failed-response behavior are also direct parser checks. |
| `test_sandbox_check_worker.py` | Shell/Python check subprocesses | Actual `run_checks` with mock process writes into the runner's real temporary output files. Verbatim compound command, argv/cwd/stdin, output and exit retention, first-failure short circuit, stdout/stderr truncation, timeout propagation, no launch after expiry and shared original deadline are asserted. |
| `test_sandbox_provenance.py` | Whole worker/CLI launches, controller-check containers, real signals, waits, broker threads | Explicit controller-check reports and mocked Docker at real `verify_checks`; private read-only mounts, independent provenance, exact report/command/run identity, output limits, failure cleanup, candidate content/mode mutations, cancellation at start/inspection/output, original deadline. Launcher components keep forged worker transcripts untrusted and cover cancellation after evidence, both installed SIGTERM/SIGINT handlers at final revalidation, rollback after SQL UPDATE before commit, untrusted retention after commit, final teardown interruption and unconfirmed broker ownership. Handlers are invoked synchronously, not via OS signals. |
| `test_sandbox_relay.py` | HTTP server/client, UDS server, real background threads and fixed sleep | Real handler methods with in-memory streams and captured upstream scheduling, plus direct socket-protocol function with mocked socket. Large-body SSE keepalive/completion, exact allowance reservation, fixed route/model, local-only cache metadata, filtered reasoning/provider fields, invalid route/body/cache metadata and protocol request/response bounds remain covered. |
| `test_sandbox_result.py` | Already fast | Unchanged: actual worker prompt expression, verbatim compound terminal command acceptance, split/fabricated command refusal. |

The former separately named broker header/body cancellation cases are consolidated
at the admitted broker boundary; every HTTP framing variant remains in the transport
parser matrix. Removal-race and controller-death cases now assert real cleanup policy
and durable readback against deterministic external responses, not fake process liveness.
External tracker scope-refusal propagation is mocked in the launcher; detailed tracker
admission remains the responsibility of the assignment/ticket-focused modules.

`test_sandbox_cleanup.py` and `test_broker_controls.py` were read for compatibility
and left unchanged. In particular, durable readable blocked-state cancellation in
`test_broker_controls.py` remains distinct from database connect/read exceptions;
this refactor does not replace that boolean regression with an exception fixture.
They are excluded from this lane's executed-test count.

## Verification

Starting HEAD: `b0196edd7a149b9097a9e3346579f2a6a900e891`.
Starting tree: `0a8c8dac54e36d03eaa92e9928276028ce5e3bc2`.
Use the repository's mise Python (`3.14.8`). The focused invocation below loads only
these modules; no full-suite discovery or E2E is authorized:

```sh
mise exec -- python -c '
import sys, unittest
blocked = []
def deny(event, args):
    if event in {"subprocess.Popen", "socket.__new__", "socket.connect", "socket.bind", "os.system", "os.fork", "os.posix_spawn", "os.exec", "os.kill", "os.killpg"}:
        blocked.append(event)
        raise AssertionError("prohibited native execution: " + event)
sys.addaudithook(deny)
modules = ["factory_v1.tests.test_" + name for name in ("sandbox", "spending", "spending_cancel", "model_access_cancel", "sandbox_check_worker", "sandbox_provenance", "sandbox_relay", "sandbox_result")]
result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(modules))
assert not blocked, blocked
sys.exit(not result.wasSuccessful())
'
```

The focused inventory has **91 unique discovered IDs**: sandbox 24, spending 18,
spending cancellation 9, model-access cancellation 6, check worker 6, provenance 18,
relay 7, result contract 3. The completed audit-blocked run exits 0 with no prohibited
execution attempts; a retained run completed in 2.299 seconds. Eight owned modules
also compile successfully. All 33 production Python files retain their original
byte/mode fingerprints. `pyright` is unavailable locally, so no standalone typecheck
is claimed.

Completed command, runtime, unique IDs, per-file byte/mode fingerprints and mutation
receipts are retained by the integrating parent. No discovered fixture starts a native subprocess,
socket, container, HTTP service, actual worker or model operation. The audit guard is
an invocation-time safety check, **not** a new repository runner, shard or framework.

Six bounded in-memory sensitivity probes remove only the targeted guards: post-lock
stop, post-lock deadline, admitted transport cancellation, source-kind rejection,
check candidate mutation and precommit result cancellation. Each produces the intended
assertion failure with no unrelated error. Production files are never edited for probes.

## Remaining acceptance gap

These are unit/component results, not proof of native process/socket/container
termination, native worker provenance, real provider billing, or live GitHub
publication. Those layers were deliberately not run. CI/integration/publication and
any explicitly authorized administrative issue closure belong to the parent lane;
this audit does not represent the missing native/live layers as passed.
