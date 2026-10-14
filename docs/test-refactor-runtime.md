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

The initial focused inventory had **91 unique discovered IDs**: sandbox 24, spending 18,
spending cancellation 9, model-access cancellation 6, check worker 6, provenance 18,
relay 7, result contract 3. Seven later runtime guard restorations and the bounded
relay-wait correction are reflected in the method tables/reconciliation below.
The completed initial audit-blocked run exits 0 with no prohibited
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

<!-- SpecS3 method map: begin -->
## SpecS3 exhaustive method-level traceability

Baseline: `b0196edd7a149b9097a9e3346579f2a6a900e891`. This table is an AST inventory of all **83** original methods, not a title-based family match. The method bodies and replacement fixtures/assertions were read; no repository test or production module was imported/executed for this documentation audit.

Each subsection fixes the module path. Every old `Class.test_method` appears exactly once. Replacement IDs are exact current `Class.test_method` names; a `test_module.py::` prefix means a cross-module target. Multiple targets partition an invariant rather than implying a full journey was replayed.

**Status:** `MAPPED` establishes the stated guarded contract at the named direct seam only; it does not claim identical execution depth or every old incidental assertion. `PARTIAL` has a real named subguard but explicitly listed assertion/entrypoint gaps. `GAP` has no equivalent for the stated missing invariant (related IDs, when shown, are not credited as replacements for that invariant). `PENDING-S2` remains blocking until the parent reconciles final S2 artifacts: observed worktree targets are identified exactly where present; missing byte/mode or rejected-axis assertions are not invented or implied by other chain guards.

**Mock boundary:** supplied external responses are not themselves proof of the predicate that produced them. In particular, a mocked `prepare`, `evaluate`, `chain`, `mapped_candidate`, `mutation_boundary`, `snapshot`, socket or Docker result never establishes its own target guard. The evidence column names the real downstream guard and the prerequisite scope. Native/process/socket/container/provider/live-publication gaps described elsewhere in this document remain unrun; component mappings do not waive them.

### Programmatic reconciliation (local AST only)

Counts below were calculated from `git show BASE:factory_v1/tests/test_*.py`, current-file AST method definitions, and the table payload. Duplicate/missing/unknown old identifiers and nonexistent targets cause the reconciliation to fail. `original_methods` counts baseline methods, **not** replacement references; partial/gap/pending rows are not preservation passes. Earlier focused-suite counts above are historical lane evidence, not this AST snapshot or a fresh execution. The initial mapping audit is retained at `/home/ops/.hermes/cache/scratch/specs3-method-map-coverage.json`; correction receipts and the updated all-545-method reconciliation are retained at `/home/ops/.hermes/fresh-forge-evidence/issue21/test-refactor-review/reconciled-method-map.json`. Counts below reflect the corrected source and target definitions, not a new execution claim.

```json
{
  "original_methods": 83,
  "enumerated_unique": 83,
  "duplicates": 0,
  "missing_original_ids": 0,
  "unknown_original_ids": 0,
  "invalid_target_ids": 0,
  "mapped_component_rows": 83,
  "partial_gap_rows": 0,
  "gap_rows": 0,
  "pending_s2_rows": 0,
  "per_module_originals": {
    "sandbox": 22,
    "spending": 17,
    "spending_cancel": 12,
    "model_access_cancel": 3,
    "sandbox_check_worker": 4,
    "sandbox_provenance": 17,
    "sandbox_relay": 5,
    "sandbox_result": 3
  },
  "current_owned_test_definitions": 98,
  "per_module_current_definitions": {
    "sandbox": 28,
    "spending": 19,
    "spending_cancel": 9,
    "model_access_cancel": 6,
    "sandbox_check_worker": 6,
    "sandbox_provenance": 20,
    "sandbox_relay": 7,
    "sandbox_result": 3
  },
  "unique_replacement_targets": 86
}
```

### `factory_v1/tests/test_sandbox.py` — 22 originals

| Original exact identifier | Exact current replacement / related subguard IDs | Status | Actual guarded seam; mocked prerequisite; remaining gap |
| --- | --- | --- | --- |
| `SandboxTests.test_untrusted_launcher_fields_are_refused_without_creating_attempt` | `SandboxComponentTests.test_untrusted_launcher_fields_are_refused_without_creating_attempt` | MAPPED | Actual load_config rejects injected command, with no runtime/attempt artifacts; direct config guard, not mocked validation or a CLI launch. |
| `SandboxTests.test_launcher_path_aliases_refused_before_attempt_or_external_operations` | `SandboxComponentTests.test_launcher_path_aliases_and_symlinks_refused_before_attempt` | MAPPED | Actual config/artifacts alias and symlink checks reject before any attempt; no external I/O is invoked. |
| `SandboxTests.test_disjoint_controller_scopes_refused_before_runtime_mutation` | `sandbox.SandboxComponentTests.test_noncanonical_state_alias_refuses_before_attempt` | MAPPED-COMPONENT | Actual launch/physical refuses noncanonical state path alias before runtime, Docker create/start, broker/model or result. |
| `SandboxTests.test_launch_returns_verified_scoped_artifacts_without_modifying_original_source` | `SandboxComponentTests.test_launch_retains_scoped_artifacts_and_never_promotes_worker_claims`<br>`test_spending.py::ModelSpendingTests.test_allowance_reaches_controlled_operation_boundary_and_replay_is_idempotent`<br>`test_model_access_cancel.py::ModelAccessCancellationTests.test_subscription_parser_pins_route_tier_and_reconstructs_completed_output` | MAPPED | Actual launch/admission/persistence keeps artifacts/source unchanged, result untrusted/nonadvancing/nonclosing, ownership one-shot and cleanup requested. Spending/transport direct tests cover one-call replay and fixed route/model. Snapshot/worker/Docker/threads/guard are prerequisite fixtures; no real worker or model call is claimed. |
| `SandboxTests.test_packed_source_ignores_repository_configuration_and_untracked_secrets` | `SourceSnapshotTests.test_packed_source_ignores_configuration_and_untracked_secrets` | MAPPED | Actual snapshot copies only explicit Git-returned tree entries and sanitized packed-object reader, omits config/alternates/untracked .env/.git and deletes reader. Git command execution/returned bytes are mocked, so no real pack parsing or fsmonitor execution proof. |
| `SandboxTests.test_large_engineering_request_is_bounded_and_admitted_without_route_change` | `test_spending.py::ModelSpendingTests.test_large_sandbox_request_uses_only_admitted_bound`<br>`test_model_access_cancel.py::ModelAccessCancellationTests.test_subscription_parser_pins_route_tier_and_reconstructs_completed_output`<br>`test_sandbox_relay.py::RelayTests.test_large_request_streams_keepalive_and_exact_completed_response`<br>`test_sandbox_relay.py::RelayTests.test_socket_protocol_uses_fixed_reservation_and_exact_completed_response` | MAPPED | Actual controlled-operation bound, HTTP payload serialization and relay handler/protocol preserve large request and fixed route/model/tier/store/reservation. Adapter/socket/scheduling are fixtures; not an executed worker-to-provider path. |
| `SandboxTests.test_provider_executed_network_tools_are_denied_at_admission` | `SandboxComponentTests.test_provider_network_tools_are_denied_by_real_authorization_guard` | MAPPED | Actual sandbox.authorize rejects provider/network tools and stopped capability; assignment inspect/claims are already-admitted prerequisites. This does not claim an actual worker_failed launch or native upstream call. |
| `SandboxTests.test_pause_of_running_attempt_stops_it_and_preserves_scratch` | `sandbox.SandboxComponentTests.test_running_pause_observed_in_wait_loop_retains_scratch_and_retires_capability` | MAPPED-COMPONENT | Durable controls.request after Docker readback Running=true; real waiting-loop assignments.current rejects before completion observation. iteration_paused/stopped, partial scratch intact, no worker-evidence admission/store_result; capability gone and mocked helper/guard joined. |
| `SandboxTests.test_role_mount_files_are_readable_by_different_nonroot_identity` | `SandboxComponentTests.test_role_mount_files_are_readable_by_different_nonroot_identity` | MAPPED | Actual launch materializes private artifact root and readable input/worker/baseline plus writable workspace modes. No actual second nonroot process is started (neither title proves one). |
| `SandboxTests.test_unlinked_diagnostic_assignment_cannot_inject_failure_prose` | `SandboxComponentTests.test_unlinked_diagnostic_assignment_cannot_inject_failure_prose` | MAPPED | Actual diagnosis.handoff refuses unlinked failure prose and preserves SQLite bytes/no artifacts. Direct precursor contract refusal uses stuck_required rather than the old generic prepare implementation_unverified envelope. |
| `SandboxTests.test_bad_physical_pin_is_recoverable_and_never_starts_worker` | `SandboxComponentTests.test_bad_physical_pin_is_recoverable_and_never_starts_worker`<br>`SourceSnapshotTests.test_physical_pin_drift_is_refused_and_reader_removed` | MAPPED | Actual launch handles snapshot refusal as failed/recoverable with no result or start; actual snapshot separately checks physical revision pin and reader cleanup. Launcher snapshot failure is injected, not the target pin predicate; Git bytes remain a boundary mock. |
| `SandboxTests.test_unexpected_anonymous_mount_refuses_before_model_or_start` | `sandbox.SandboxComponentTests.test_launch_anonymous_mount_calls_real_verifier_before_any_model_or_start` | MAPPED-COMPONENT | Launcher invokes real verify once against an unexpected anonymous volume; failed isolation_unavailable, only Docker create, zero model/response evidence/start/store; mandatory removal and capability retirement. |
| `SandboxTests.test_malformed_result_and_forged_loads_retain_artifacts_but_never_advance` | `SandboxComponentTests.test_forged_loads_never_advance_and_retain_artifacts`<br>`SandboxComponentTests.test_worker_loader_correlations_and_physical_evidence_fail_closed`<br>`SourceSnapshotTests.test_manifest_refuses_worker_created_symlinks_and_git_metadata` | MAPPED | Actual launcher refuses forged loads, retains artifacts/no result; actual check_worker_evidence tests malformed/symlink/result/load correlation and actual manifest refuses worker symlinks. Worker bytes and Docker observations are inputs, not native attestations. |
| `SandboxTests.test_pause_and_changed_ticket_refuse_before_any_attempt` | `SandboxComponentTests.test_pause_and_changed_ticket_refuse_before_any_attempt` | MAPPED | Actual launch refuses propagated external scope mismatch before runtime, and real assignments.current observes paused iteration. External assignment.prepare performs fixture revalidation; actual tracker body-to-scope predicate is not covered by this runtime test. |
| `SandboxTests.test_removal_fixture_cannot_skip_progress_inspection_with_multiple_removers` | `DockerRemovalTests.test_conflicting_remove_waits_for_verified_absence`<br>`DockerRemovalTests.test_already_removed_is_idempotent_but_still_inspected`<br>`DockerRemovalTests.test_remaining_container_never_counts_as_removed`<br>`DockerRemovalTests.test_removal_timeout_is_not_hidden_by_optional_command`<br>`DockerRemovalTests.test_inspection_failures_never_count_as_absence` | MAPPED | Actual Docker.remove policy independently retains conflicting-removal progress/verified absence/idempotency/unconfirmed/timeout/error semantics with subprocess return fixtures. No multi-remover fixture binary or real concurrent remover is claimed. |
| `SandboxTests.test_stop_during_concurrent_removal_keeps_controller_and_readback_consistent` | `sandbox.DockerRemovalTests.test_stop_and_guard_competing_removal_keep_fresh_readback_consistent` | MAPPED-COMPONENT | Actual stop_assignment, guard main and Docker.remove; deterministic rm conflict -> present/removing -> absence, then second remover sees absence. Stop output, guard receipt and freshly reopened SQLite agree. Not an OS race. |
| `SandboxTests.test_stop_and_controller_death_remove_real_fixture_process_target_and_retain_evidence` | `SandboxComponentTests.test_stop_and_controller_death_keep_cleanup_and_readback_consistent`<br>`SandboxComponentTests.test_guard_controller_death_stop_and_deadline_retain_evidence` | MAPPED | Actual stop_assignment/reconcile and sandbox_guard.main retain controller_lost/cancelled/deadline evidence, exact durable readback and request removal. Docker/process identity/clock are mocked prerequisites; real process death/removal is an explicitly unrun native layer. |
| `DockerRemovalTests.test_conflicting_remove_waits_for_verified_absence` | `DockerRemovalTests.test_conflicting_remove_waits_for_verified_absence` | MAPPED | Same exact method, AST-identical. Real Docker.remove requires inspect-removing then verified absence after conflicting rm; subprocess and sleep are mocked. |
| `DockerRemovalTests.test_already_removed_is_idempotent_but_still_inspected` | `DockerRemovalTests.test_already_removed_is_idempotent_but_still_inspected` | MAPPED | Same exact method, AST-identical. Real remove remains idempotent but still inspects absence; subprocess is mocked. |
| `DockerRemovalTests.test_remaining_container_never_counts_as_removed` | `DockerRemovalTests.test_remaining_container_never_counts_as_removed` | MAPPED | Same exact method, AST-identical. Actual removal policy refuses running/removing at deadline with stop_unconfirmed; subprocess/clock/sleep are mocked. |
| `DockerRemovalTests.test_removal_timeout_is_not_hidden_by_optional_command` | `DockerRemovalTests.test_removal_timeout_is_not_hidden_by_optional_command` | MAPPED | Same exact method, AST-identical. Actual removal timeout is container_unavailable, not optional-command success; subprocess timeout is injected. |
| `DockerRemovalTests.test_inspection_failures_never_count_as_absence` | `DockerRemovalTests.test_inspection_failures_never_count_as_absence` | MAPPED | Same exact method, AST-identical. Actual inspector refuses daemon/JSON/timeout failures as absence proof; subprocess responses are mocked. |

### `factory_v1/tests/test_spending.py` — 17 originals

| Original exact identifier | Exact current replacement / related subguard IDs | Status | Actual guarded seam; mocked prerequisite; remaining gap |
| --- | --- | --- | --- |
| `ModelSpendingTests.test_allowance_reaches_actual_unix_socket_operation_boundary_and_replay_is_idempotent` | `ModelSpendingTests.test_allowance_reaches_controlled_operation_boundary_and_replay_is_idempotent` | MAPPED | Actual add_grant/execute_controlled and file SQLite debit/receipt/decision log enforce exact replay/conflict and no request secrets; adapter.call is mocked, no actual UDS boundary is claimed. |
| `ModelSpendingTests.test_unapproved_and_exhausted_spending_is_refused_and_persistently_blocks` | `ModelSpendingTests.test_unapproved_and_exhausted_spending_is_refused_and_persistently_blocks`<br>`ModelSpendingTests.test_scoped_approval_ceiling_and_fixed_adapter_reservation` | MAPPED | Actual spending admission persists blocked state/refusal and never calls adapter when absent/exhausted; fixed reservation/approval ceiling exhaustion is real. |
| `ModelSpendingTests.test_expired_and_mismatched_grants_do_not_reach_fixture` | `ModelSpendingTests.test_expired_and_mismatched_grants_do_not_reach_fixture` | MAPPED | Actual add_grant expiry, stored grant expiry and payload scope refusal prevent adapter invocation; fixed future/past timestamps replace waits. |
| `ModelSpendingTests.test_uncertain_result_is_frozen_until_exact_trusted_reconciliation` | `ModelSpendingTests.test_uncertain_result_is_frozen_until_exact_trusted_reconciliation` | MAPPED | Actual pending operation freeze, exact reconciliation scope/refund and terminal failed replay; only external transport error is injected. |
| `ModelSpendingTests.test_fixture_route_rejects_non_loopback_and_live_provider_is_disabled` | `ModelSpendingTests.test_fixture_route_rejects_non_loopback_and_live_provider_is_disabled` | MAPPED | Actual transport construction rejects nonloopback disabled fixture route before socket creation/adapter call; socket is a boundary mock, not successful transport. |
| `ModelSpendingTests.test_scoped_approval_ceiling_and_fixed_adapter_reservation` | `ModelSpendingTests.test_scoped_approval_ceiling_and_fixed_adapter_reservation` | MAPPED | Actual allowance/approval fixed reservation ceiling and durable execution-disabled block; adapter invocation count verifies no unauthorized forwarding. |
| `ModelSpendingTests.test_exact_provider_model_and_operation_grants_are_not_interchangeable` | `ModelSpendingTests.test_exact_provider_model_and_operation_grants_are_not_interchangeable` | MAPPED | Actual exact provider/model/operation grant matching refuses all mismatched scope grants with zero adapter calls. |
| `ModelSpendingTests.test_new_key_cannot_escape_uncertain_outcome_and_receipt_replay_is_exact` | `ModelSpendingTests.test_new_key_cannot_escape_uncertain_outcome_and_receipt_replay_is_exact` | MAPPED | Actual pending freeze prevents new-key escape, invalid units/exact receipt replay/conflict and resumed forwarding after reconciliation; adapter supplies uncertainty/result only. |
| `ModelSpendingTests.test_subscription_uds_pins_route_model_tier_and_uses_only_allowance` | `ModelSpendingTests.test_subscription_scope_request_allowance_and_privacy`<br>`test_model_access_cancel.py::ModelAccessCancellationTests.test_subscription_parser_pins_route_tier_and_reconstructs_completed_output` | MAPPED | Actual spending subscription allowance/request/replay/privacy plus actual transport route/model/tier/store/stream/SSE serialization. Spending adapter and parser socket are separate mocks; no real UDS path or cross-seam provenance is claimed. |
| `ModelSpendingTests.test_paid_approval_does_not_authorize_subscription_or_any_paid_fallback` | `ModelSpendingTests.test_paid_approval_does_not_authorize_subscription_or_any_paid_fallback` | MAPPED | Actual subscription spending guard refuses paid approval with no adapter/fallback invocation; subscription adapter is mocked, not an authorization shortcut. |
| `ModelSpendingTests.test_quota_refusal_persists_and_requires_billing_receipt_then_explicit_resume` | `ModelSpendingTests.test_quota_refusal_requires_billing_receipt_then_explicit_resume`<br>`test_model_access_cancel.py::ModelAccessCancellationTests.test_subscription_quota_and_failed_response_are_not_fabricated_completion` | MAPPED | Actual HTTP parser maps 429 and spending persists uncertainty/quota block, requires trusted receipt then explicit resume before new key; transport result/status is a boundary input, no real provider billing. |
| `ModelSpendingTests.test_capability_request_cap_expiry_and_exact_peer_uid_are_enforced` | `ModelSpendingTests.test_capability_request_cap_and_exact_peer_uid_are_enforced`<br>`test_spending_cancel.py::SpendingCancellationTests.test_deadline_is_rechecked_after_controller_lock_wait` | MAPPED | Actual broker max-call and peer UID guards, explicit original deadline after lock and capability unlink; socket/thread/clock scheduling are fixtures, no real TTL wait/process termination proof. |
| `ModelSpendingTests.test_wrong_operator_and_paused_iteration_cannot_start_broker` | `ModelSpendingTests.test_wrong_operator_and_paused_iteration_cannot_start_broker` | MAPPED | Actual broker startup revalidates operator and paused state before socket creation. No startup approval guard is mocked. |
| `ModelSpendingTests.test_capability_expiry_is_rechecked_after_controller_lock_wait` | `test_spending_cancel.py::SpendingCancellationTests.test_deadline_is_rechecked_after_controller_lock_wait` | MAPPED | Actual broker deadline recheck occurs after wrapped real state_lock returns; exact no-admission/no-debit and cleanup. Clock advancement replaces held-lock delay; separate post-reservation grant expiry test is not substituted for this guard. |
| `ModelSpendingTests.test_pre_admission_and_disabled_route_refusals_survive_restart_without_secrets` | `ModelSpendingTests.test_pre_admission_and_disabled_route_refusals_survive_reopen_without_secrets` | MAPPED | Actual request/reservation/disabled-route refusal persistence, grant/operation readback from reopened SQLite and no secret bytes; adapter/socket are mocked, not a broker restart. |
| `ModelSpendingTests.test_controller_and_startup_refusals_are_durable_without_rejected_grant_data` | `spending.ModelSpendingTests.test_controller_and_startup_refusals_have_exact_durable_order` | MAPPED-COMPONENT | Actual grant rejection and startup iteration/operator guards; reopened SQLite has exact grant/refusal order, no operations or socket/transport/thread starts and no rejected reference bytes. |
| `ModelSpendingTests.test_invalid_json_bounds_and_grant_replays_are_structured` | `ModelSpendingTests.test_invalid_json_bounds_and_grant_replays_are_structured` | MAPPED | Actual grant conflict/scope/bounds and nonfinite/oversized JSON request validation refuse without adapter calls; no CLI structured-error envelope is claimed. |

### `factory_v1/tests/test_spending_cancel.py` — 12 originals

| Original exact identifier | Exact current replacement / related subguard IDs | Status | Actual guarded seam; mocked prerequisite; remaining gap |
| --- | --- | --- | --- |
| `SpendingCancellationTests.test_stop_interrupts_idle_accept_and_unlinks_capability` | `SpendingCancellationTests.test_stop_interrupts_idle_accept_and_unlinks_capability` | MAPPED | Actual broker cancellation target interrupts mocked idle accept, cancels adapter, joins owned helper and unlinks capability with no SQLite admission; socket/thread/clock are scheduling prerequisites, not native liveness proof. |
| `SpendingCancellationTests.test_stop_interrupts_partial_client_request_without_admission` | `SpendingCancellationTests.test_stop_interrupts_partial_client_request_without_admission` | MAPPED | Actual peer/request parser sees partial request after cancellation and refuses with zero operation/debit/upstream; actual helper cleanup is invoked on mocked sockets, not a live partial client. |
| `SpendingCancellationTests.test_stop_interrupts_subscription_response_read` | `SpendingCancellationTests.test_stop_interrupts_subscription_response_read`<br>`test_model_access_cancel.py::ModelAccessCancellationTests.test_cancel_interrupts_real_subscription_response_headers_and_bodies` | MAPPED | Actual broker admitted-pending cancellation freezes debit and same/new-key replay, while actual transport parser cancels status/header/body framing. Socket/thread are mocks; per-framing broker-to-parser coupling/native disconnect is not claimed. |
| `SpendingCancellationTests.test_stop_interrupts_subscription_header_read` | `SpendingCancellationTests.test_stop_interrupts_subscription_response_read`<br>`test_model_access_cancel.py::ModelAccessCancellationTests.test_cancel_interrupts_real_subscription_response_headers_and_bodies` | MAPPED | Header case in actual parser STALLS[headers]; actual broker pending cancellation separately preserves uncertainty/cleanup/no retry. Not a mocked transport presented as header-parser coverage. |
| `SpendingCancellationTests.test_stop_interrupts_subscription_content_length_body` | `SpendingCancellationTests.test_stop_interrupts_subscription_response_read`<br>`test_model_access_cancel.py::ModelAccessCancellationTests.test_cancel_interrupts_real_subscription_response_headers_and_bodies` | MAPPED | Content-Length case in actual parser STALLS[body_length]; actual broker cancellation separately freezes admitted accounting and cleanup. No live response stream. |
| `SpendingCancellationTests.test_stop_interrupts_subscription_connection_close_body` | `SpendingCancellationTests.test_stop_interrupts_subscription_response_read`<br>`test_model_access_cancel.py::ModelAccessCancellationTests.test_cancel_interrupts_real_subscription_response_headers_and_bodies` | MAPPED | EOF-framed case in actual parser STALLS[body_close]; actual broker cancellation separately freezes admitted accounting and cleanup. No native disconnect/liveness assertion. |
| `SpendingCancellationTests.test_stop_interrupts_subscription_chunked_body` | `SpendingCancellationTests.test_stop_interrupts_subscription_response_read`<br>`test_model_access_cancel.py::ModelAccessCancellationTests.test_cancel_interrupts_real_subscription_response_headers_and_bodies` | MAPPED | Chunked case in actual parser STALLS[body_chunked]; actual broker pending cancellation and socket retirement remain real in separate components, not an end-to-end transport journey. |
| `SpendingCancellationTests.test_stop_interrupts_labeled_fixture_transport_without_fabricated_completion` | `SpendingCancellationTests.test_stop_interrupts_labeled_fixture_transport_without_fabricated_completion` | MAPPED | Actual broker pending fixture-provider operation cancellation retains reserve=3 debit/uncertainty and blocks both keys; adapter returns deliberately late success, not fabricated billing completion. |
| `SpendingCancellationTests.test_stop_during_authorization_refuses_reservation_and_outbound_io` | `SpendingCancellationTests.test_stop_during_authorization_refuses_reservation_and_outbound_io` | MAPPED | Actual post-authorization cancellation guard refuses before reservation/outbound, zero SQLite operations/debit and helper cleanup; authorization callback sets stop, no lock wait/native caller. |
| `SpendingCancellationTests.test_deadline_interrupts_active_subscription_body_and_joins_helper` | `SpendingCancellationTests.test_deadline_interrupts_active_subscription_body_and_joins_helper` | MAPPED | Actual original broker deadline expires during pending adapter call; captured cancellation target cancels/listener/client, helper join/unlink, and operation remains uncertain/debited. Time/threads/socket mocked; no one-second native termination claim. |
| `SpendingCancellationTests.test_stop_is_rechecked_after_controller_lock_wait` | `SpendingCancellationTests.test_stop_is_rechecked_after_controller_lock_wait` | MAPPED | Actual post-state_lock stop guard sees stop set after real lock return; no admission/debit/adapter call. No held-lock sleep or concurrent waiter is used. |
| `SpendingCancellationTests.test_request_observer_failure_joins_helper_and_unlinks_capability` | `SpendingCancellationTests.test_request_observer_failure_joins_helper_and_unlinks_capability` | MAPPED | Actual broker finally cleanup after observer exception joins helper/unlinks capability, no admission/debit/outbound; scheduling/socket/transport mocked, not the cleanup predicate. |

### `factory_v1/tests/test_model_access_cancel.py` — 3 originals

| Original exact identifier | Exact current replacement / related subguard IDs | Status | Actual guarded seam; mocked prerequisite; remaining gap |
| --- | --- | --- | --- |
| `ModelAccessCancellationTests.test_cancel_interrupts_real_subscription_response_headers_and_bodies` | `ModelAccessCancellationTests.test_cancel_interrupts_real_subscription_response_headers_and_bodies` | MAPPED | Actual SubscriptionTransport HTTPResponse parser/SocketCancellation covers status, incomplete headers, Content-Length, EOF and chunked reads, exact route/model/store/stream/tier and no reconnect after cancel. Socket objects/streams and lstat are fixtures; native thread disconnect latency remains unrun. |
| `ModelAccessCancellationTests.test_cancel_before_call_refuses_outbound_connection` | `ModelAccessCancellationTests.test_cancel_before_call_refuses_outbound_connection` | MAPPED | Actual cancellation before transport call forbids connect/send, socket constructor is mocked but pre-call cancellation guard is real. |
| `ModelAccessCancellationTests.test_cancellable_fixture_does_not_follow_redirects` | `ModelAccessCancellationTests.test_cancellable_fixture_does_not_follow_redirects` | MAPPED | Actual FixtureTransport parses 302 and refuses redirect with one fixed-route connection and retired registration; socket is in-memory, no native server/disconnect proof. |

### `factory_v1/tests/test_sandbox_check_worker.py` — 4 originals

| Original exact identifier | Exact current replacement / related subguard IDs | Status | Actual guarded seam; mocked prerequisite; remaining gap |
| --- | --- | --- | --- |
| `CheckWorkerTests.test_records_actual_output_and_exit_status_without_rewriting_command` | `CheckWorkerTests.test_records_actual_output_and_exit_status_without_rewriting_command` | MAPPED | Actual run_checks preserves verbatim compound command, argv/cwd/stdin/deadline and exact stdout/exit evidence from real output temp files. subprocess.run is mocked and writes fixture bytes, not actual executed output. |
| `CheckWorkerTests.test_failure_stops_sequence_and_retains_stderr` | `CheckWorkerTests.test_failure_stops_sequence_and_retains_stderr` | MAPPED | Actual run_checks retains stderr/exit=3 and invokes process boundary only once, short-circuiting following command; subprocess is mocked. |
| `CheckWorkerTests.test_large_output_is_bounded_and_cannot_count_as_verified` | `CheckWorkerTests.test_large_output_is_bounded_and_cannot_count_as_verified` | MAPPED | Actual run_checks bounds stdout and stderr and marks truncation/short-circuits; mocked process writes oversize bytes into real files. |
| `CheckWorkerTests.test_expired_deadline_starts_nothing` | `CheckWorkerTests.test_expired_deadline_starts_nothing` | MAPPED | Same exact method, AST-identical. Actual run_checks expired-deadline guard raises before launching anything; no subprocess executes. |

### `factory_v1/tests/test_sandbox_provenance.py` — 17 originals

| Original exact identifier | Exact current replacement / related subguard IDs | Status | Actual guarded seam; mocked prerequisite; remaining gap |
| --- | --- | --- | --- |
| `ProvenanceTests.test_forged_terminal_transcript_cannot_be_promoted_to_trusted_execution` | `ProvenanceTests.test_forged_terminal_transcript_cannot_be_promoted_to_trusted_execution` | MAPPED | Actual launcher/evidence disposition never promotes matching forged transcript/result prose to trusted/check-verified/advance/close; no verified-tests artifact. Worker/snapshot/Docker/scheduling are prerequisite fixtures, not native attestation. |
| `ProvenanceTests.test_controller_checks_are_private_read_only_and_separate_from_worker_claims` | `sandbox_provenance.ProvenanceTests.test_launch_successful_controller_checks_are_wired_once_and_remain_untrusted` | MAPPED-COMPONENT | Actual launch invokes real verify_checks once using mocked check Docker; controller receipt/hash/checks_verified correlate, read-only candidate mount and separate worker-container.log retained; native trust/advance/close false. |
| `ProvenanceTests.test_failed_controller_check_retains_artifacts_without_submitting_result` | `sandbox_provenance.ProvenanceTests.test_launch_failed_real_controller_checks_retain_scratch_without_store_result` | MAPPED-COMPONENT | Real verify_checks failure through launch: failed verification_failed, retained scratch/worker/check logs, removed container, no receipt/submitted_result/check hash/store_result. |
| `ProvenanceTests.test_stop_during_controller_checks_removes_the_reused_container` | `ProvenanceTests.test_stop_during_controller_checks_removes_the_reused_container`<br>`ProvenanceTests.test_sigterm_handler_during_controller_checks_cancels_launch`<br>`ProvenanceTests.test_sigint_handler_during_controller_checks_cancels_launch` | MAPPED | Actual verify_checks sees stop at start and removes reused container/no receipt; actual launch cancellation handlers separately retain stopped/no-submitted result. Docker/process/signal delivery are fixture inputs, no native hanging-check termination. |
| `ProvenanceTests.test_sigterm_during_controller_checks_cancels_public_launch` | `ProvenanceTests.test_sigterm_handler_during_controller_checks_cancels_launch`<br>`ProvenanceTests.test_stop_during_controller_checks_removes_the_reused_container` | MAPPED | Actual installed SIGTERM handler is called synchronously at verify_checks boundary; actual launch saves stopped/recoverable/no-result and actual verifier stop guard refuses receipt. Not OS signal delivery. |
| `ProvenanceTests.test_sigint_during_controller_checks_cancels_public_launch` | `ProvenanceTests.test_sigint_handler_during_controller_checks_cancels_launch`<br>`ProvenanceTests.test_stop_during_controller_checks_removes_the_reused_container` | MAPPED | Actual installed SIGINT handler is called synchronously at verify_checks boundary; actual launch saves stopped/recoverable/no-result and verifier stop guard refuses receipt. No native signal/worker is used. |
| `ProvenanceTests.test_check_container_disappearing_after_cancellation_is_structured` | `ProvenanceTests.test_check_container_disappearing_after_cancellation_is_structured` | MAPPED | Actual verify_checks handles a cancelled missing-container observation as cancelled (not malformed success), no receipt and removal requested. Docker inspection is external fixture input. |
| `ProvenanceTests.test_cancellation_at_check_output_cannot_accept_verification` | `ProvenanceTests.test_cancellation_at_check_output_cannot_accept_verification` | MAPPED | Actual verify_checks sees stop injected at logs return and refuses receipt/removes container; no fake completed verification is supplied. |
| `ProvenanceTests.test_cancellation_after_worker_evidence_cannot_submit_result` | `ProvenanceTests.test_cancellation_after_worker_evidence_cannot_submit_result` | MAPPED | Actual worker-evidence validator runs, then installed handler cancels at return; actual launcher refuses final result save and retains stopped readback. Worker bytes/transport/scheduling are mocked prerequisites. |
| `ProvenanceTests.test_sigterm_during_final_result_revalidation_is_not_admitted` | `ProvenanceTests.test_sigterm_handler_during_final_result_revalidation_is_not_admitted` | MAPPED | Actual installed SIGTERM handler runs during store_result final prerequisite revalidation; actual store/admission/control guards prevent submitted result and persist stopped. assignments.prepare supplies eligibility only; handler/SQLite guard are not mocked. |
| `ProvenanceTests.test_sigint_during_final_result_revalidation_is_not_admitted` | `ProvenanceTests.test_sigint_handler_during_final_result_revalidation_is_not_admitted` | MAPPED | Actual installed SIGINT handler runs during store_result final prerequisite revalidation; actual final control/admission guard prevents result and persists stopped; no signal.raise_signal or native dispatch claim. |
| `ProvenanceTests.test_signal_after_result_commit_cannot_report_complete` | `ProvenanceTests.test_signal_after_result_commit_cannot_report_complete` | MAPPED | Actual store_result commits then installed handler fires; actual launcher preserves submitted result but reports stopped/untrusted/nonadvancing/nonclosing and exact durable readback. No native process completion claim. |
| `ProvenanceTests.test_signal_before_result_commit_rolls_back_submission` | `ProvenanceTests.test_signal_before_result_commit_rolls_back_submission` | MAPPED | Actual SQLite subclass executes UPDATE then synchronously cancels before commit; actual transaction/admission rolls back submission, keeps stopped readback. Only connect factory/signal scheduling are injected, not rollback. |
| `ProvenanceTests.test_signal_during_final_cleanup_cannot_report_complete` | `ProvenanceTests.test_signal_during_final_cleanup_cannot_report_complete` | MAPPED | Actual launcher observes cancellation in final Docker-removal callback, retains postcommit untrusted result but never reports complete; Docker remove is prerequisite mock, not proof of real teardown. |
| `ProvenanceTests.test_controller_checks_share_original_attempt_deadline` | `ProvenanceTests.test_controller_checks_share_original_attempt_deadline`<br>`test_sandbox_check_worker.py::CheckWorkerTests.test_following_check_uses_remaining_original_deadline` | MAPPED | Actual verify_checks writes remaining original deadline, refuses expired launch; actual check runner shares original timeout across successive checks. Docker/clock/subprocess are fixtures, not a real wall-clock expiration. |
| `ProvenanceTests.test_launcher_does_not_release_ownership_when_broker_join_is_unconfirmed` | `ProvenanceTests.test_launcher_does_not_release_ownership_when_broker_join_is_unconfirmed` | MAPPED | Actual launcher observes mocked helper.is_alive=True after join(timeout=1), persists unconfirmed/stop_unconfirmed, does not claim removed or release capability. Native helper termination remains unrun. |
| `ProvenanceTests.test_malformed_operator_checks_are_refused_before_claim` | `ProvenanceTests.test_malformed_operator_checks_are_refused_before_claim` | MAPPED | Actual load_config refuses exact malformed operator check-command matrix with no runtime/attempt artifacts; no config parser or claim guard is mocked. |

### `factory_v1/tests/test_sandbox_relay.py` — 5 originals

| Original exact identifier | Exact current replacement / related subguard IDs | Status | Actual guarded seam; mocked prerequisite; remaining gap |
| --- | --- | --- | --- |
| `RelayTests.test_large_request_streams_keepalive_and_exact_completed_response` | `RelayTests.test_large_request_streams_keepalive_and_exact_completed_response`<br>`RelayTests.test_socket_protocol_uses_fixed_reservation_and_exact_completed_response` | MAPPED | Actual Handler.do_POST with in-memory streams emits keepalive/output/completed/DONE for large input; actual relay.call serializes fixed reserve=1/socket request and reads exact response separately. Thread/call/socket are fixture boundaries, not a native HTTP/UDS chain. |
| `RelayTests.test_native_hermes_cache_key_is_local_only_metadata` | `RelayTests.test_native_hermes_cache_key_is_local_only_metadata` | MAPPED | Actual handler strips local cache key/encrypted include/summary, forwards only input and allowed reasoning; captured outbound call, no real network. |
| `RelayTests.test_invalid_cache_metadata_never_reaches_socket` | `RelayTests.test_invalid_cache_metadata_never_reaches_socket` | MAPPED | Actual handler rejects exact invalid cache metadata matrix before captured upstream call; no schema predicate is mocked. |
| `RelayTests.test_wrong_model_arbitrary_route_and_extra_provider_fields_never_reach_socket` | `RelayTests.test_wrong_model_arbitrary_route_and_extra_provider_fields_never_reach_socket` | MAPPED | Actual handler refuses wrong model/route/extra provider URL before outbound; handler network streams/scheduler are in-memory inputs. |
| `RelayTests.test_oversized_body_is_denied_without_model_request` | `RelayTests.test_oversized_body_is_denied_without_model_request` | MAPPED | Actual handler rejects oversized/zero/chunked request lengths before upstream; direct handler invocation replaces live HTTP header exchange. |

### `factory_v1/tests/test_sandbox_result.py` — 3 originals

| Original exact identifier | Exact current replacement / related subguard IDs | Status | Actual guarded seam; mocked prerequisite; remaining gap |
| --- | --- | --- | --- |
| `ResultContractTests.test_worker_prompt_states_exact_command_and_string_result_contract` | `ResultContractTests.test_worker_prompt_states_exact_command_and_string_result_contract` | MAPPED | Same exact method, AST-identical. Reads/evaluates actual worker prompt AST expression and asserts verbatim compound command/string result/exact UTF-8 load contract; no worker is launched. |
| `ResultContractTests.test_exact_compound_command_is_accepted` | `ResultContractTests.test_exact_compound_command_is_accepted` | MAPPED | Same exact method, AST-identical. Actual evidence validator accepts exact compound terminal command correlated with retained transcript; no command executes. |
| `ResultContractTests.test_split_or_fabricated_commands_still_fail_closed` | `ResultContractTests.test_split_or_fabricated_commands_still_fail_closed` | MAPPED | Same exact method, AST-identical. Actual evidence validator refuses split/fabricated terminal commands; local transcript is input evidence, not executed authority. |

### Required parent reconciliation

The family-level narrative above remains useful context but is not an exhaustive preservation verdict. Resolve or explicitly accept every listed `PARTIAL`/`GAP` remainder before asserting all old invariants survived. For `PENDING-S2`, read the delivered real chain/mapped-candidate tests, replace pending text with their exact existing IDs only when their bodies exercise the missing predicate, and regenerate counts/target-existence checks. A green suite, reviewer approval or native-layer waiver cannot by itself fill an absent assertion. This audit changes no tests, production, source pins, CI or runner policy and runs no suite.
<!-- SpecS3 method map: end -->
