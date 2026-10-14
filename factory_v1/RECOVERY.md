# Main recovery (fresh factory v1)

`recovery.py` connects the existing delivery watcher, bounded repair role,
simplification, independent Standards/Spec reviews, host merge verifier and stuck
work investigation. It does not introduce a second execution or merge engine.

## Admission and authority

1. `observe-delivery` records failed/unknown configured delivery evidence and an
   incident. Ordinary ticket preparation, launch/result revalidation, reservation
   and merges are held. A repair-looking role or worker-written incident artifact
   supplies no exception.
2. The trusted controller independently resolves an incident scope and installs a
   private, owner-only, physical recovery configuration **outside all worker
   workspaces, profile homes and artifacts**. The durable SQLite file has the same
   exclusion. This is host capability installation, not a new routine human
   approval step and not a worker attestation generator.
3. `register-recovery` binds exact evidence, issue identity/body and allowed paths.
   Existing workers must already be terminal with confirmed cleanup; this command
   neither launches nor kills workers. It parks their claims/results and retains
   the old reservation. No other project's claims are released. Role profiles
   remain unique, including against parked roles.
4. `prepare-recovery` derives the incident artifact from retained host evidence,
   ignoring caller-supplied artifact assertions. The exact main SHA, check runs,
   workflows, deployment and health/failure readbacks enter the fresh repair
   handoff. A registered out-of-milestone recovery issue is admitted without
   admitting unrelated/deferred work into the ordinary milestone. Current
   requirements, discovery holds, native/textual dependency agreement, tracker
   identity, spending and isolation checks still apply.

The recovery configuration contains exactly:

```json
{
  "operator_id": "42",
  "reference": "independently verified incident scope receipt",
  "incident_id": "main-<incident digest>",
  "evidence_id": "<64-hex current failed observation digest>",
  "issue": {
    "number": 20,
    "id": 200,
    "node_id": "<exact GitHub issue node>",
    "body_sha256": "<64-hex issue body digest>"
  },
  "paths": ["src/affected.py", "tests/test_affected.py"],
  "removable_assignments": ["<64-hex previously delivered candidate assignment>"]
}
```

`removable_assignments` is the maximum permitted removal scope, not proof that
these requirements were removed. It may be empty. Each entry resolves to exact
retained delivered work and its original issue. Paths are exact repository-relative
file names, including tests; directory prefixes/globs do not grant authority.
Registration is immutable and idempotent; changed scope, unavailable exact main,
changed requirements or moved main require reconciliation, never silent repinning.

## Public lifecycle

All commands use the existing persistent state and shared lock. Illustrative paths
below refer to controller-owned inputs, not credentials or files supplied by a worker.

```sh
python -m factory_v1 --state /controller/state.sqlite --operator-id 42 \
  register-recovery --project product --iteration m1 \
  --recovery-config /controller/recovery.json --api-base https://api.github.com

python -m factory_v1 --state /controller/state.sqlite --operator-id 42 \
  prepare-recovery --project product --iteration m1 \
  --request /controller/repair-request.json --api-base https://api.github.com

python -m factory_v1 --state /controller/state.sqlite --operator-id 42 \
  inspect-recovery --project product --iteration m1
```

The repair request is the ordinary complete bounded assignment request with
`stage=repair`, a fresh dedicated `role=repair` profile, the selected reviewed
`diagnosing-bugs` and `implement` closure, exact incident baseline and
`candidate=null`. `preceding` is controller-derived. `--dry-run` prepares no claim,
incident update or spending operation. Use existing `launch-assignment` with the
private launcher configuration: registration/preparation alone is **not** execution.

The repair executes diagnosis first, then modifying implementation. Its
`recovery-report` artifact records the pinned scope, evidence-backed
`roll-forward`/`safe-revert` rationale and tradeoffs, structured diagnosis,
`removed_assignments`, regression and cleanup evidence. Diagnostic commands and
results must match retained executing-context terminal output verbatim. A missing
feedback loop/capability cannot be called successful recovery. Worker conversations
and these reports remain untrusted; transcript consistency is not native provenance.

The candidate follows unchanged `prepare-simplification`, fresh separate
`prepare-review` Standards/Spec, review authorization and controlled publication.
Correction ancestry resolves to the registered repair; rejection recovery still
requires corrective work, a new candidate and both fresh axes. No direct main
mutation, check/protection bypass or reduced security policy is granted.

Recovery corrections retain `recovery-choice`: the same report fields except
`diagnosis`, with the final mode/rationale/removals/regression/cleanup reassessed
after implementing independent findings. This permits correcting a rejected
removal assessment without mutating an immutable original result or inventing
engineering stuckness. The original repair's diagnosis remains separately verified.

The private merge configuration's `recovery` now contains exactly:

```json
{
  "incident_id": "main-<incident digest>",
  "implementation_assignment": "<registered root repair assignment>",
  "issue": 20,
  "removed_assignments": ["<exact delivered candidate assignment actually removed>"]
}
```

When a corrective descendant is the modifying builder, `recovery` additionally
requires `root_execution`, the independently verified `merge.execution_binding`
of the original repair. The ordinary four execution bindings cover the current
correction, simplifier and both axes; this additional binding prevents them from
silently substituting for original diagnosis provenance. Original root checks
are also revalidated. Computing fingerprints alone does not attest native work.

The host independently resolves the **actual final candidate's** removal list
before installing merge authority, as it independently verifies native execution
bindings. Copying a worker list is not that verification. The list must match the
repair report and stay within registration's permitted removals. Ordinary
`recovery=null` configurations are unchanged. Scope and original repair ancestry
are checked again after the last remote admission reads, immediately before the
merge intent/PUT. The final simplified byte/mode diff may contain **only** exact
authorized incident paths. Required checks, branch protections, independent review
and current publication/revision gates are unchanged. Lost merge responses retain
the ordinary durable reconciliation behavior; no blind second merge is sent.

## Clearance and removed behavior

`merge-candidate` and `verify-delivery` retain their existing responsibilities.
Neither closes an incident. Once a recovery is registered, even a fully healthy
watcher sample updates evidence **without clearing the freeze**.

```sh
python -m factory_v1 --state /controller/state.sqlite --operator-id 42 \
  verify-recovery --project product --iteration m1 \
  --merge-config /controller/merge.json --api-base https://api.github.com
```

This command reuses the ordinary merge/delivery verifier, including host execution
bindings, exact merged commit/tree/membership, protections and integrated checks.
It reopens every independently authorized removed-behavior ticket, returns its
board status from Done to the configured ready option (preserving an independently
Blocked/deferred status), retains native dependency
readbacks, and marks its prior delivery incomplete with `delivery_removed`.
Existing dependency edges are not deleted: open work no longer satisfies them.
The authoritative frontier refresh rechecks native/textual edges and preserves
independent requirement, discovery and diagnosis holds. Removed work is held even
if externally closed until this reconciliation finishes.

Each reopening/status intent commits before its write. A lost response is resolved
by exact issue/body/membership readback. An unknown no-effect outcome stays held
rather than blindly repeating the write. Changed identity/body cannot redirect the
operation. No ticket creation, duplicate issue, new board or inferred edge is used.

Only actual configured health and required check/workflow/deployment evidence on
the **exact repair merge** allow clearance; health is sampled again after affected
tracker readbacks. The clearance record retains merge/check/evidence/frontier and
reopening receipts. Incident journal clearance, parked-result release and pointer
removal commit atomically. Historical clearance replay does not repeat external
writes; it is a retained receipt, not a newly sampled live-health claim. The old
reservation remains retained in recovery history; ordinary work must reconcile
and reserve through the normal current-frontier path rather than restoring a stale
reservation automatically.

## Stuckness

A repair's genuine `status=stuck`/`stuck-declaration` uses existing `declare-stuck`,
`prepare-diagnosis`, isolated read-only debug, `request-direction`, exact-origin
response and `apply-direction`. A crash/limit is not engineering stuckness. Silence,
a merely declared stuck record, a pending release or a worker recommendation
cannot authorize another repair claim. A fresh retry requires an applied explicit
retry direction and fully released debug hold. Material revisions still follow the
existing requirement-revision lifecycle. The main incident remains frozen through
all these states, including healthy polling while direction is pending.

## Verification and limitations

Focused deterministic commands (no services, containers, workers or model calls):

```sh
mise exec -- python -m unittest factory_v1.tests.test_recovery factory_v1.tests.test_recovery_components -v
mise exec -- python -m compileall -q factory_v1
```

Tests use minimum durable records, mocked tracker/health/execution boundaries and
small real SQLite/filesystem/CLI components. They cover priority and out-of-scope
admission, independent holds, host authority, exact evidence/skills handoff,
replay/stuck retry, report/output validation, final-mutation piggyback refusal,
removed ticket reopening/status/dependencies and uncertain-write reconciliation,
and health-gated durable clearance. They do not demonstrate native isolated role
execution, live GitHub delivery or actual deployment health. The configured runtime
gates remain strict. Per-ticket completion follows the implementation-ticket policy
in the fresh specification; whole-factory live validation is separate.

Legacy fixtures that treated raw incident fields or a repair role label as host
registration must migrate to this contract. The canonical existing whole-suite
runner also contains policy-prohibited heavy journeys/real HTTP fixtures; a focused
pass does not establish whole-suite/remote CI success. CI discovery/gates are not
changed by this slice. Fresh simplification, independent review, integration and CI
remain delivery gates handled outside this local builder continuation.
