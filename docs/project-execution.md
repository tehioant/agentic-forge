# Approved project execution preparation

The product user describes a project and its acceptance requirements. They do not write launcher JSON, manage profiles, issue grants or operate systemd. Infrastructure approval and installation remain host-administrator responsibilities, not interview questions. This module does not install or qualify missing infrastructure.

## Host composition API

`ProjectExecution(setup, tracker, config)` consumes the existing `ProjectSetup` and `ProjectTracker` objects and an administrator-selected configuration with exactly `root` (existing private isolated execution journal directory) and `capability_config` (private reviewed host inventory path).

- `prepare(draft_id, expected_revision)`: freshly validate T2/T4 scope and reviewed capabilities; derive immutable private launcher, finite profile-slot, continuation and readiness files; observe actual readiness. Returns `prepared` or `blocked`.
- `read(draft_id)`: retained record, not fresh health or permission.
- `status(draft_id)`: fresh prepare/readiness observation, never dispatch permission.
- `register(draft_id, expected_revision)`: fresh preparation, exact installed-service readback, selection under the persistent interruption lane, then existing readiness registration/policy seal/readback. Requires an already-paused iteration. Returns `registered` or `blocked` and never starts/resumes a service.
- `resume(draft_id, expected_revision)`: retry preparation/registration only. It does **not** call `controls.resume`, replenish spending, reset attempts or recreate homes.

T7 owns construction and shared native plugin/enrollment wiring. T6 currently resumes T2 setup only; it must explicitly add this producer. A blocker includes `code`, `operation` (`prepare-execution` or `register-execution`), `cause`, exact draft/revision `scope`, `consequence`, `next_decision`, and `reconciliation_required`. Detailed readiness checks distinguish source support, write authority, spending, image/socket, notification and deployment health. A conversation reply cannot edit the inventory or supply independent execution/review authority.

## Reviewed inventory contract

`INVENTORY_FIELDS` and `POLICY_FIELDS` in the module are the exact host schema. The inventory pins the authenticated operator, literal repository, immutable repository ID and draft revision with a nonempty host review reference. It selects:

- An **existing scoped** `service_config`, using exactly the original T2 state and already configured host-only GitHub, notification and delivery capability references. The actual existing `service.supervisor(..., 'status')` validates the installed unit/source/config; an inactive, missing or overridden installation blocks preparation. There is no invented supervisor callback and no service start.
- A separate private `role_root`; exactly one reviewed template per existing engineering stage, each with a finite `count` in 1–100 (total at most 1000), installed reviewed skill descriptors, a private validated launcher template, and only provider/model/operation/reasoning profile inputs. Names/homes/claims are controller-derived stable dedicated identities; homes are created only by the original assignment lifecycle, never by preparation. Existing homes and historical claims stay consumed.
- Actual native `planning_config` and pinned `synthesis_config` executable capabilities, reviewed planning/ticket sources, main protection/check contract, and finite existing host `authorities` path lists for review, diagnosis, publication and merge.
- `policy`: current draft `acceptance`, agreed nonempty `verification_commands`, explicit `merge` and `production` values `human-approved`, and `deployment: true`. Commands are validated by the actual derived launcher validator and inherited by all slots. Approval strings do not authorize a candidate; only existing exact trusted candidate authorizations can do so. No-deployment contracts are explicitly unsupported by the current delivery implementation, not interpreted as healthy.

Private artifacts roots and launcher files are derived under the project execution root (0700 directories, 0600 files). Existing validators run on **derived** files as well as templates. Actual `assignments.validate`, `service_dispatch.validate`/continuation validation, installed source/dependency validators, `readiness.collect` and default concrete `HostReaders` are used. No caller can inject a hash-reader to claim executor/verifier support. Credential values never enter derived configuration or journals; only host capability paths are retained. Workspaces, source, profiles, drafts, controller state and capability directories remain disjoint.

## Smallest supported production route and limits

An approved installation must already expose the original public native planning/wakeup contract, the pinned host synthesis executable, fixed-image launcher, independent review/diagnosis verifiers and exact notification/GitHub/delivery routes. Concrete readiness readers inspect these sources and actual scoped permissions/capacity without executing workers. The continuation seed takes its issue identity from the real delivery contract's issue readback and its initial repository baseline/AGENTS.md from the verified T2 checkout. It is a schema seed, not an admitted assignment or a claim that specification synthesis has happened. Existing `continuation.request` overwrites issue, current main and requirement authority from each actual eligible ticket before admission. A missing real delivery anchor or AGENTS.md is a precise prerequisite, not an invented issue or source.

The existing service supports one selected scoped configuration, not a new multi-project supervisor registry. This deliverable supports already scoped approved host installations; provisioning a fresh service installation for a new project is not implemented here and remains an installation prerequisite. The source includes the real registration route for a supported installation, not a configurable missing executor. T7 must still compose automatic planning-to-continuation invocation.

T4 `tracker_config(draft_id)` is called freshly while active, with its actual prepare(draft_id, revision) contract (no session keyword). Its prepare guard intentionally refuses pause. For an already-paused registration, this adapter freshly reads the retained completed T4 scope and reuses its concrete host read-only board/schema/milestone validators instead; no tracker effect/creation is admitted while paused.

Candidate-specific review, diagnosis, publication and merge authorizations are never generated from worker reports. `continuation.authority` retains original exact binding selection. If future candidate authorization files are added/changed, the existing explicit host policy-selection route may need resealing while paused. Preparation never repins a sealed policy, reviewed skill source or changed retained inventory.

Replay compares private derived files and rejects drift rather than overwriting. Registration does not reset budgets, consumed slots, state or intentional interruption; explicit existing controls resume remains a separate authenticated operation. A running supervisor, prepared configuration and healthy readiness are three distinct facts, none of which supplies execution/review trust.

## Focused evidence

One discovered invariant method contains ten isolated cases: supported registration/replay, unavailable installation, unsupported no-deployment, changed agreement, exhausted allowance, failing configured health, source drift, retained-launcher drift, consumed home and active preparation. It reuses a direct readiness-record fixture, mocks only installed-source/host observation boundaries, exercises real derivation/configuration/readiness/registration validators and never executes predecessor stages or workers. Synthetic exact-byte fixture source catalogs and mocked host authority readers are component evidence, **not** installed-runtime or independent native qualification. Canonical external-execution guard and before/after source byte/mode fingerprints surround the focused run; the full suite is discovered only. Parent owns independent review and integration/publication. No live network, model, container, service or profile qualification is claimed.
