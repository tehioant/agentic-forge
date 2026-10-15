# Interrupted execution recovery (#22)

Deterministic controller reconciliation for US-35, US-37 and scenario 8 at
`cccb21e35cc989a1ecd5a3c88f0689a694d77d9c`. This uses the existing iteration,
assignment, tracker, attention and publication journals; it adds neither a backlog
nor a scheduler. The later verification policy in
`docs/specs/fresh-agentic-factory.md:168-174` applies. The component evidence is
simulated, not live deployment or whole-factory qualification.

## Public routes

Use the exact registered project/iteration and the recorded read-only GitHub
capability. The host authenticates the operator; a command-line ID is not itself
authentication. Configuration/credentials remain outside worker mounts.

```sh
mise exec -- python -m factory_v1 --state "$FACTORY_STATE" --operator-id "$FACTORY_OPERATOR" \
  reconcile-restart --project "$FACTORY_PROJECT" --iteration "$FACTORY_ITERATION" \
  --api-base "$FACTORY_GITHUB_CAPABILITY"

# Explicitly release only this retained, independently absent, failed/lost attempt.
# This is infrastructure continuation, not engineering stuckness or a retry grant.
mise exec -- python -m factory_v1 --state "$FACTORY_STATE" --operator-id "$FACTORY_OPERATOR" \
  reconcile-restart --project "$FACTORY_PROJECT" --iteration "$FACTORY_ITERATION" \
  --api-base "$FACTORY_GITHUB_CAPABILITY" --recover-assignment "$FACTORY_ASSIGNMENT" \
  --run "$FACTORY_RETAINED_RUN" --reference "$FACTORY_EXPLICIT_RECOVERY_MESSAGE"

# A separate intentional hold requires separate explicit resume authorization.
mise exec -- python -m factory_v1 --state "$FACTORY_STATE" --operator-id "$FACTORY_OPERATOR" \
  resume --project "$FACTORY_PROJECT" --iteration "$FACTORY_ITERATION" \
  --api-base "$FACTORY_GITHUB_CAPABILITY" --reference "$FACTORY_EXPLICIT_RESUME_MESSAGE"
```

Reconciliation returns an inspectable JSON report, including `status`,
`observations`, `blockers` and `dispatch_allowed`. A blocked observation report is
exit 0 (the observation completed, not the work); malformed commands/ownership and
refused resume/dispatch are structured exit 2. The durable report is also in the
iteration's `restart_reconciliation` field, exposed by `inspect`. Inspecting or
reconciling **never** resumes pause. Restored SQLite and its complete `.controls`
directory must remain together, as documented in [CONTROLS.md](CONTROLS.md).

## Readback and admission

- Persist an interrupted-reconciliation hold before external reads. Process loss
  during observation cannot leave an earlier ready receipt in force.
- Reuse repository onboarding, pinned requirement publication and the authoritative
  frontier (exact issue IDs/bodies, linked board/schema/membership/progress, native
  and textual dependencies). Run existing issue/bug publication reconcilers on a
  disposable SQLite copy behind a capability that rejects all external writes.
  Promote read-back receipts, not a second local backlog.
- Read physical worktree markers, detached or loose/packed branch HEADs, admitted
  source revisions and exact GitHub commit identities. Unknown source/HEAD drift
  preserves artifacts and holds admission; it is not silently repinned.
- Read exact PID/start/boot ownership and named-container liveness. A lost controller
  with verified worker absence becomes stopped/recoverable, **not done**. A present,
  unconfirmed, incorrectly named or contradictory runtime keeps ownership. Existing
  `reconcile-assignment`/emergency cleanup remains the explicit removal route; restart
  reconciliation does not kill live owners or erase their artifacts.
- Reuse exact publication plan/commit/ref/PR association validators. A full effect
  with a lost response is adopted. An absent/partial or duplicate effect is held;
  reconciliation never repeats object/ref/repository/issue/PR writes. A previously
  published PR may be ready-for-review without treating readiness as merge approval.
- Reuse exact merge target, integrated tree/parents/main ancestry, protection and
  app-bound candidate/integrated/main checks. Retain a proven merge effect even when
  its checks are failed/unknown. Do not merge again, close/reopen an issue, or grant
  delivery from a worker report. The existing controlled delivery lifecycle owns
  subsequent closure/reopening and independent approval.
- If a delivery monitor is configured, sample its pinned CI/CD, deployment and
  health contract again. Failed/unknown configured health remains a dispatch hold;
  no missing target is invented or treated as healthy. The existing incident and
  observation/outbox journals retain the actual evidence.
- Read and validate exact-origin attention intents/receipts. Unknown sends stay held
  for `attention-reconcile`; accepted/delivered sends are never resent. Reconciliation
  does not call a messaging transport. Billing reservations/broker leases and archived
  uncertain bug effects require their existing explicit scoped reconcilers.
- After publication/CI/health reads, refresh the authoritative frontier and each
  retained requirement authority again. Drift refuses release/resume, and the original
  operator hold survives a competing pause/stop during the reads.

The shared existing state lock serializes public reconciliation and ownership.
The short interruption lane protects final local release/resume, not network I/O.
Assignment preparation and ticket reservation consult the restart boundary once
attempt/observer state exists; their initial-admission paths retain their existing
exact tracker/requirement checks. Every worker launch forces the broad readback
before Docker/model admission. Local lifecycle admission also refuses a lost
nonterminal owner and any unresolved reconciliation report.

## Recoverable claims

Automatic absence observation does not steal ownership. Explicit recovery binds an
exact assignment, run and operator reference, requires complete fresh observations,
no competing ticket/profile owners and no candidate/merge journal, and archives the
old claim through `restart_disposition`. Only then can existing claim admission
accept a fresh claim/profile owner. The old runtime/run/artifacts and remaining
spending/attempt/deadline records remain unchanged except for truthful stopped/
absence metadata. Historical results cannot be resubmitted or launched. A fresh
claim still passes the original requirement, skill, frontier, quota and isolation
gates; recovery does not invent an engineering stuck result or reset a budget.

## Focused verification

```sh
mise exec -- python -m unittest \
  factory_v1.tests.test_restart factory_v1.tests.test_restart_effects -v
```

These are fast units/single-boundary components. Direct SQLite input rows, worktree
marker files and external responses are labeled simulated. Subprocess/network/model
and messaging seams are forbidden or mocked. No real service, container, model,
worker chain or multi-stage controller journey is run. A bounded old-`resume`
function substitution demonstrates the no-readback-resume regression fails on the
baseline behavior; the current function passes. The builder receipt records exact
HEAD/tree, commands/exits/runtime and before/after byte/mode fingerprints.

Independent simplification/Standards/Spec review and integrated CI are separate
handoff gates. The full suite is intentionally not run by this builder while the
parent audits active legacy helper reachability. This is not live GitHub, real
Docker recovery, health qualification or milestone-completion evidence.
