# Supervised controller (local candidate for #25)

The supported supervisor is **systemd --user** on the existing Linux server.
`factory_v1.service` is a runnable entry point, not an agent or a privileged HTTP
endpoint. It renders a fixed, version-controlled unit, uses the current selected
Python interpreter/source directory, and keeps its state/configuration/logs in a
fresh private directory. It never reads another assistant profile or discovers
credentials. No new dependency is required beyond the repository's Python 3.14.

**Qualification:** this change has only focused, mocked local verification. It
has not been installed, enabled, started or restarted on the server. Do not infer
live readiness, native skill execution or permission to publish from these tests.
For #26–29, the [no-live verification amendment](../docs/specs/no-live-verification-amendment.md)
authorizes autonomous reviewed delivery with focused checks and policy-compliant
CI. Live acceptance is waived and not executed; runtime safety is unchanged.

## Readiness selection (#26)

See [READINESS.md](READINESS.md) for the exact private `readiness_config` and
`github_config` fields, supported existing classic OAuth route and public
`select-readiness` / `readiness` commands. Missing selection blocks shared
supervised admission even through direct CLI routes. A running service or retained
green report is never authorization. Unknown configured health is not healthy.
Readiness uses the current selected action: completed effects need no unused
worker slot, while a selected new worker still needs its exact approved capacity.
Registered repair and exact-origin notifications have narrow documented operation
contracts, not an ordinary dispatch bypass. Parent integration still owns the
combined sibling-effect checks and final review. Do not change permissions, grants,
installed skill pins or host source modes merely to make the probe green.

## Install and control (requires separate operational authorization)

Run from a reviewed persistent checkout, not an ephemeral implementation worktree.
Use an existing systemd user manager. Persist its host/boot configuration through
the server's authoritative infrastructure repository; this feature does not
install host provisioning, enable lingering or change another service.

```sh
# Existing private parent directory; choose a new, non-profile service root.
ROOT="$HOME/.local/state/agentic-forge"
CONFIG="$ROOT/service.json"
mise exec -- python -m factory_v1.service init --root "$ROOT" \
  --operator-id OPERATOR --project PRODUCT --iteration M1 \
  --api-base https://api.github.com

# Register approved intake against the NEW database using the existing CLI.
# This creates no grants and no permission to run a worker.
mise exec -- python -m factory_v1 --state "$ROOT/controller.sqlite" \
  --operator-id OPERATOR register --request /private/approved-intake.json

# Ensure this is the actual user manager's unit directory (XDG_CONFIG_HOME if set).
# Install refuses to replace any existing agentic-forge.service.
mise exec -- python -m factory_v1.service --config "$CONFIG" install \
  --unit-directory "$HOME/.config/systemd/user"
mise exec -- python -m factory_v1.service --config "$CONFIG" start
mise exec -- python -m factory_v1.service --config "$CONFIG" status
mise exec -- python -m factory_v1.service --config "$CONFIG" pause --reference thread:pause
mise exec -- python -m factory_v1.service --config "$CONFIG" emergency-stop --reference thread:stop
mise exec -- python -m factory_v1.service --config "$CONFIG" restart
# Inspect/reconcile exact effects and missing capabilities BEFORE explicit resume.
mise exec -- python -m factory_v1 --state "$ROOT/controller.sqlite" \
  --operator-id OPERATOR reconcile-restart --project PRODUCT --iteration M1 \
  --api-base https://api.github.com
mise exec -- python -m factory_v1.service --config "$CONFIG" resume --reference thread:resume
mise exec -- python -m factory_v1.service --config "$CONFIG" stop
```

`init` requires an existing parent directory, creates an empty SQLite schema and
`service.json`, and leaves all capabilities null. It is not product onboarding or
planning. Use the existing `onboard`, `plan` and `complete-planning` interfaces to
supply their actual admitted context. A private repository needs an already
approved controller API capability; the default credential-blind bearer cannot
read private GitHub resources. This feature neither reads OAuth grants nor builds
a replacement credential broker. Select that exact API endpoint at initialization.

`install` writes only the named unit and runs `daemon-reload`; it does **not** start
or enable it. Install fails rather than overwriting an existing unit. If a manager
reload fails, retain the written file and reconcile it explicitly. Before control
or live status reads, the CLI checks the exact loaded fragment bytes, selected
source/configuration and absence of drop-ins, refusing unrelated units. Enabling
boot startup is a separate reviewed infrastructure action. The unit uses
`Restart=on-failure`, `UMask=0077`, `NoNewPrivileges=yes`, `KillMode=control-group`
and a finite stop deadline. Supervisor paths must not contain whitespace or unit
expansion characters; no shell interpretation is used.

## Pause, restart and failure behavior

Every service process takes an exclusive lock for its root, **persists a pause
before reconciliation**, and calls the existing `restart.reconcile` under the
shared controller state lock. A stronger emergency-stop hold remains stronger.
A successful start/restart or reconciliation never clears interruption. Only the
existing explicit `controls.resume` path can do that, after a fresh exact tracker,
worker, pending-effect and configured-health reconciliation. No attempt/grant or
budget is reset by the scheduler. Unknown observations block, not retry execution.

Pause/emergency-stop/resume use the existing independent short control lane, not
the service-loop lock. Stop/restart first persist emergency stop. On shutdown the
service invokes existing emergency cleanup; Docker/broker ownership and uncertain
removal remain governed by `CONTROLS.md` and `RESTART.md`. During a sandbox stage,
signal cancellation also notifies the supervisor loop, so a cancelled stage cannot
swallow service termination and proceed to another stage. The existing pinned
host synthesis executor gains bounded cancellation checks and process-group
kill/wait on interruption; an interrupted admitted synthesis remains uncertain,
never silently retried. Whole-process absence still needs live qualification.

The service root contains `controller.sqlite` plus the existing control/lock
files, `service.lock`, an atomic `status.json`, and transition-only `events.jsonl`.
Events carry service run, project, iteration and fixed stage/error codes, not
credentials, raw model output, tracker bodies or transport errors. Repeated
unchanged healthy/paused/blocked cycles write no extra events and print nothing.
`status` includes live systemd state separately from the retained last-cycle
snapshot; it always reports `dispatch_allowed: false` because inspection itself
cannot authorize execution. Full authoritative evidence remains in the existing
stage artifacts and SQLite journals, never a second backlog.

## Deterministic stage dispatch

`service.json` has a fixed schema: `version`, `state`, `operator_id`, `project`,
`iteration`, `api_base`, `interval` (integer seconds, 1–3600), `delivery_config`,
`transport_config`, `dispatch_config`, `readiness_config`, `github_config`.
Initialization writes null for unselected configuration paths; a supervised runtime
must supply and validate the required readiness and GitHub capability paths.
State is fixed to `controller.sqlite` adjacent to the private, controller-owned
0600 configuration; its directory is 0700, outside controller source and assistant
profiles. Existing worker-mount exclusions are rechecked. Do not put tokens here.

A reviewed host can explicitly select existing private delivery and transport
configurations. Delivery observation and pending exact-origin notification
handling are deterministic even while work is paused. Failed/unknown configured
health prevents stage dispatch without suppressing pending incident attention.
No poll, health observation, bookkeeping or notification calls an agent.

`dispatch_config` selects a separate 0600 host-owned JSON object with exactly:

- `operator_id`, `project`, `iteration`, `reference`: explicit matching scope.
- `tickets`: the existing `synthesize-tickets` request (`skill`, `tracker`), or null.
- `synthesis_config`: the existing independently approved, pinned host synthesis
  executor configuration from `requirement_execution.configuration`, or null.
  No new executable/plugin protocol is introduced.
- `implementation`: one complete existing `prepare-assignment` request with
  `stage: implementation`, or null. This is a capability envelope for an issue in
  the **live authoritative frontier**, not permission to invent work.
- `roles`: optional `simplify`, `review-standards`, `review-spec` entries, each with
  exactly `profile`, `skills`, `claim_id`. Fresh isolated profiles are mandatory;
  dependent assignment IDs and evidence are constructed by existing public seams.
- `launchers`: those four stage names mapped to existing private launcher configs.
- `review_config`, `publication_config`, `merge_config`: existing exact host
  authorities, or null. The service never synthesizes these from worker claims.

One admitted operation runs per deterministic tick. After the existing completed
planning handoff it selects ticket synthesis, invokes the actual selected
`to-tickets` through the approved host executor, validates load/input/result
bindings, publishes through existing ticket publication, reserves the exact
frontier issue and prepares implementation. Existing sandbox launch runs the
mapped skill and controller checks; dedicated simplification and independent
Standards/Spec review follow automatically. Dependency IDs come from retained
assignments, not manual per-stage advancement. Fresh restart/control checks run
before effects; a changed stage during reconciliation is reobserved rather than
using the stale selection. Launcher and publication entry points acquire their
own locks without a nested service lock. Every retained attempt, even a failed
or stopped one, prevents blind launch replay. Host synthesis intent is retained
before invocation and raw receipts remain untrusted until validation.

Engineering source paths must be the actual installed `source == path`, with the
reviewed source hash/dependency closure and stage adaptation. Test snapshots,
caller repinning, a `done` string, worker-authored tests or nominal skill
availability are not authority. Existing implementation/simplification validators,
two-axis execution authorization, controlled publication/merge and integrated
delivery checks remain authoritative. Missing host authority returns its specific
capability name; it does not invoke a fallback agent or fabricate a ready result.
An exact reviewed host merge authority is still required. The runtime consumes
that authorization; it does not implement its independent issuer. Whether the
approved issuer requires an operator decision is a deployment policy, not proof
that arbitrary worker output can authorize integration.

## Optional finite continuation capacity

Add `continuation` to the legacy dispatch object, with exactly:

- `reference`: explicit host approval to select the current frontier within this
  project/iteration and implementation envelope's repository/workspace. It permits
  rebinding only the selected full issue pin, current scoped specification commit,
  authoritative current-main baseline, and literal unused execution slot. The
  envelope must explicitly permit a fresh candidate (`candidate: null`).
- `slots`: a nonempty finite list (maximum 1000) of objects containing exactly
  `stage`, `profile`, `skills`, `claim_id`, `launcher`. Stage is one of
  `implementation`, `corrections`, `simplify`, `review-standards`, `review-spec`,
  `diagnosis`, `repair`. Profiles are complete existing dedicated role profiles;
  claims, names and homes are unique and never reused, including failed/retired
  history. Skills must be the actual reviewed installed stage closure. Launchers
  are explicit existing private configs, with all limits supplied and validated by
  `sandbox_policy.load_config`; inherited checks must match exactly. Slot order is
  capacity preference, not ticket order. Exhaustion holds, never declares stuck.
- `authorities`: exactly `review`, `diagnosis`, `publication`, `merge`, each a finite
  explicit list of existing private host-authority paths (maximum 1000 each).
  Selectors bind actual runs, candidate ancestry, skill/check identities and scope;
  existing consumers revalidate full independent approval immediately before use.
  Missing, stale or ambiguous authority holds. No approval is manufactured from a
  worker report, no path is discovered by glob, and no model grant is created.

Each tick chooses current eligible work from GitHub, not the old implementation
claim. Corrective modifiers restart simplification and both review axes. Genuine
responsible-worker stuck declarations use the existing separate diagnosis and
stored authenticated attention response; silence grants nothing. A revise
response leaves publication to the existing versioned requirement lifecycle, not
an automatic required/deferred choice. Infrastructure failures need the existing
exact-attempt host retry/recovery authority; no synthetic stuck result is created.

Ordinary verified delivery is retired before fresh health/restart admission.
Retirement can resolve only its exact retained old-reservation/ownership blocker,
leaves the restart report intact, and requires fresh reconciliation afterward.
Nonhealthy delivery reaches dispatch only for an existing registered repair;
ordinary work and unrelated holds remain frozen. Exact integrated repair delivery
uses the existing recovery verifier, including removed-requirement reopening and
fresh configured health, before ownership retirement. Neither a healthy sample
nor a delivered journal alone clears the incident. Completion revalidates every
required delivered authority and persists the immutable report/outbox/wait state;
`await_next_interview` never invokes `next_iteration`.

## Explicit remaining gaps (not a #25 Done claim)

1. **Origin conversation binding:** `integrations/hermes-planning` provides an
   opt-in native `pre_llm_call`/tool adapter for an explicitly host-bound existing
   session. It loads the reviewed installed interview sources into actual origin
   turns. Sufficient clarification returns the installed `to-spec` instructions
   directly to that same native tool loop, without another operator advancement.
   The native `factory_planning_publish` tool accepts actual same-origin synthesis
   and uses a separately approved HOST capability for controlled initial docs/issue
   publication and exact durable readback. Native-bound completion cannot substitute
   loader receipts or supplied GitHub pointers. Optional `planning_config` selects
   the private approved native wakeup route; it may coexist with `continuation`.
   Request, enqueue and verified asynchronous receipt consumption are separate
   ticks; ticket synthesis and continuation wait until that receipt is consumed.
   A load or enqueue is not completion. The plugin has not been installed/enabled
   or exercised with a model. Only plain exact-origin hook input is qualified by
   component probes: shared-session sender attribution remains unsupported because
   trusted injection metadata is absent at the hook. No prefix normalization is
   performed. See the integration README for authorization/runtime contracts.
2. **Bounded scope:** the optional `continuation` configuration now drives the
   authoritative frontier through finite approved fresh execution slots, corrective
   descendants, separate diagnosis/operator direction, registered repair and
   verified ticket retirement. Without it, legacy fixed-ticket envelopes retain
   their prior behavior. Required completion uses `iteration.complete` and waits
   for explicit next-interview input; no next iteration is auto-approved. Missing
   slots, allowances or exact independent authorities remain runtime holds.
3. **Unavailable installed source revisions:** read-only local preflight found
   installed `implement`, `code-review` and `to-tickets` bytes different from the
   repository's reviewed pins. They were not repinned or edited. Live dispatch
   must remain blocked until their changed instructions/dependencies/adaptations
   are separately reviewed and authorized. Skill files being installed is not
   proof that an approved native execution capability exists.
4. **Unrun operational criteria:** real supervised install/start/stop/restart,
   paused-state survival across that restart, exact live reconciliation/resume,
   active worker/helper cancellation and independent absence readback, secret-free
   correlated live logs, and genuine installed-skill behavior/provenance across
   the automatically dispatched stages. No service, worker, container, model or
   host helper was started for this candidate.
5. **Delivery process pending:** fresh-main/#22 integration, fresh dedicated
   simplification, independent Standards/Spec review, permitted exact-candidate
   final checks and eventual approved publication/CI. Aggregate readiness remains
   #26; these local checks do not qualify the whole factory.

These gaps must not be relabeled as passing mocks, live failures or completed
acceptance. See `docs/issue25-focused-verification.md` for the focused test graph.
