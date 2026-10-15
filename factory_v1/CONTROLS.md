# Durable pause and emergency stop (#21)

Trusted host/operator controls for US-35/US-36 and pinned scenario 8 at
`cccb21e35cc989a1ecd5a3c88f0689a694d77d9c`. This slice interrupts existing
assignments; it does not introduce a scheduler, live provider capability, rollback,
issue closure or a new spending grant. The later deterministic restart boundary
and fresh-readback resume contract are documented in [RESTART.md](RESTART.md) (#22).

## Public commands

Examples use actual registered identifiers and controller-owned paths, not worker
input. The caller authenticates the operator and verifies the referenced message;
`--operator-id` alone is not authentication.

```sh
mise exec -- python -m factory_v1 --state "$FACTORY_STATE" --operator-id "$FACTORY_OPERATOR" \
  pause --project "$FACTORY_PROJECT" --iteration "$FACTORY_ITERATION" \
  --reference "$FACTORY_OPERATOR_MESSAGE"

mise exec -- python -m factory_v1 --state "$FACTORY_STATE" --operator-id "$FACTORY_OPERATOR" \
  emergency-stop --project "$FACTORY_PROJECT" --iteration "$FACTORY_ITERATION" \
  --reference "$FACTORY_OPERATOR_MESSAGE"

mise exec -- python -m factory_v1 --state "$FACTORY_STATE" --operator-id "$FACTORY_OPERATOR" \
  inspect-control --project "$FACTORY_PROJECT" --iteration "$FACTORY_ITERATION"

mise exec -- python -m factory_v1 --state "$FACTORY_STATE" --operator-id "$FACTORY_OPERATOR" \
  resume --project "$FACTORY_PROJECT" --iteration "$FACTORY_ITERATION" \
  --api-base "$FACTORY_GITHUB_CAPABILITY" --reference "$FACTORY_EXPLICIT_RESUME_MESSAGE"
```

Success returns JSON/exit 0; structured refusals exit 2. Unknown scope, a different
operator, malformed control records, unsafe private files and missing persistent
state never authorize work. Ordinary `inspect` also exposes `operator_control` and
the effective paused status; `inspect-control` refreshes the checkpoint and reports
actual current ownership. Readbacks survive a fresh controller process.

## State, checkpoints and concurrency

The durable operator hold lives in `<state>.controls/`, mode 0700, outside all
worker mounts. Records and the short control lock are owner-owned mode 0600;
symlinks, hardlinks, malformed and ambiguous records are rejected. Scope filenames
are digests of the exact project/iteration identifiers. Control replacement uses a
same-directory temporary file, file fsync, atomic replace and directory fsync.
Back up **SQLite, its journals, the complete controls directory, and referenced
artifact envelopes together**. Restoring only SQLite can lose an operator hold.
Deleting controller-owned control records is not a resume procedure.

A request preserves `requested_checkpoint` unchanged. `checkpoint` is the latest
readback, with iteration stage/correlation, all scoped assignments and run IDs,
immutable handoffs/skills/specifications/baselines, submitted stage evidence,
publication/merge journals and reserved-but-uncertain model operations. Runtime
paths retain the actual candidate workspace, inputs, scratch diagnostics, source
manifests and guard/check evidence; control does not clean these directories.
Repeated same-action requests retain the original request identity. Transition
history preserves prior controls. Emergency stop cannot be downgraded to pause.

The existing `.onboarding.lock` continues to serialize ordinary controller mutations
and merge reconciliation. Pause requests **do not wait for it or a SQLite writer**:
they use the separate short control lane, so a long worker/network operation cannot
hide an interruption. Assignment/model admission and the durable merge intent
share that short lane with requests; it is never held across worker or network I/O.
Queued admissions recheck the hold after their normal lock wait and at their final
durable claim. A pause during admission cannot be overwritten by an older iteration
payload. Ticket reservation and stage/model operations consult the same durable hold.

The merge path rechecks after the final pinned-publication reads, at the serialized
intent boundary and immediately before the exact SHA-bound PUT. A request preceding
that boundary prevents a new merge. An operation already admitted/in flight may
complete; its real PR/integrated commit is read back under the existing journal.
A response lost after merge remains uncertain until exact readback proves it.
Neither interruption claims an external operation was undone or sends a blind retry.
Pause does not cause an issue closure, branch deletion, PR deletion or evidence loss;
existing recovery can reopen premature closure without pretending to roll back Git.

## Requested, acknowledged and completed

- **requested:** the hold and request checkpoint are durable and new admission is
  denied, but a controller operation/worker has not yet reached its interruption
  boundary. This is not a claim that a worker has stopped.
- **acknowledged:** emergency cleanup has been attempted, or the active assignment
  has reached a stopped/unconfirmed state while its controller still owns teardown.
  Per-run `cleanup` distinguishes verified container removal from `stop_unconfirmed`.
- **completed:** the shared mutation boundary is quiescent, no scoped attempt has a
  live controller or unconfirmed/container-present lifecycle, and all recorded model
  broker leases have been retired after their cancellation helpers were joined.
  Completion describes **interruption**, not delivery or reconciliation of every
  pending external outcome. Uncertain billing/merge intents remain visible.

These are fresh observations, not time-based promises. A successful removal alone
cannot claim completed stop while a launcher/helper or unconfirmed lifecycle remains.
An unrelated controller can temporarily keep a scope's response conservative because
the initial factory mutation lock is shared; it does not pause that other scope.

Normal pause is cooperative: it blocks new dispatch/model calls and stops the
bounded attempt at its existing polling/result boundaries, retaining useful artifacts.
A stalled broker independently observes the hold and cancels its owned sockets, so
it does not need the long controller lock to interrupt upstream I/O. No new inference
or chargeable fallback is used for interruption. Cancellation of an admitted model
request preserves its pending reservation and uncertain outcome.

Emergency stop persists the hold first, touches each scoped attempt's stop marker,
signals the exact PID/start-time-bound launcher, then kills container execution and
verifies it has halted before bounded best-effort log capture. Container removal
and independent absence verification run regardless of halt/capture failures;
optional diagnostics cannot delay the execution halt or suppress removal. The
existing launcher, guard, relay
and cancellable broker own teardown; a race, Docker failure, stale controller or
failed helper join remains recoverable/unconfirmed rather than falsely completed.
All cleanup is scope-bound. Shared provider services and unrelated processes are
not killed. Docker removal/inspection failures retain their existing bounded timeout;
this is not a hard real-time kill guarantee under daemon/host failure.

## Explicit recovery

Reboot, inspection, quota restoration and worker exit never release the operator
hold. `resume` requires the exact configured operator and an explicit reference,
quiescent mutations, no live/unconfirmed attempts or broker leases, no uncertain
model reservation, and no pending candidate publication/merge intent. It cannot
steal another iteration's active scope or reset budgets, deadlines, input pins,
claims or worker evidence. Resolve existing uncertainty with the existing exact
`spend-reconcile`, `reconcile-assignment`, publication and merge readbacks first.
An explicit emergency stop can retire a dead broker lease only after its socket is
absent; a live or orphaned capability stays held for reconciliation. `resume` now
performs the fresh deterministic [restart reconciliation](RESTART.md) before releasing
the hold. Reconciliation itself supplies no implicit resume authorization.

Resume releases only the iteration hold. It does not replay a stopped attempt;
`launch-assignment` still refuses an existing runtime. Prepare a valid fresh
assignment through existing ownership/gate rules rather than erasing old runs.
Other holds (quota, adverse review, stuckness, requirement drift, main incidents)
remain authoritative after resume.

## Verification and acceptance boundary

`mise exec -- python -m unittest factory_v1.tests.test_controls -v` covers public
scope/ownership, persistence and explicit resume, interruption concurrent with a long
SQLite/controller lock, final assignment admission, final merge reads, and a real
in-flight loopback merge with truthful readback/no closure or duplicate PUT.
The last-publication-read regression also uses the real merge lifecycle seam to
isolate the late pause guard rather than relying on an earlier status check.

Two deterministic public assignment tests run real Linux worker/child process
groups through the existing Docker seam. They verify actual PID/start identities,
worker and child termination, independent guard exit, launcher completion, retained
candidate/input/scratch artifacts and fresh control readback. A stalled local UDS
transport verifies actual socket cancellation, broker/cancellation-helper joins and
retained uncertain billing. These are explicitly labeled deterministic fixtures,
**not native model, real Docker isolation or live GitHub acceptance**.

The exact pinned Hermes image was unavailable on the builder host. No alternate
image was silently admitted, no image was pulled, and no live inference or external
GitHub mutation was performed. Real isolated Docker/native assignment interruption
and live GitHub pause/merge acceptance therefore remain gaps for an authorized
private disposable environment. Run `scripts/run_factory_tests.py` for the complete
suite after simplification/review of the final candidate.
