# Supervised readiness (#26)

The collector remains read-only and never grants execution authority. The service
probe composes it with a durable private service selection and liveness. Shared
`controls.admission` now recollects capabilities outside its short interruption
lane and compares local policy/state/ownership inside the lane. Boundary coverage
is incremental; the remaining source criteria below must be completed before
claiming #26 complete. Reports never reserve capacity, run skills or prove
iteration completion.

```python
from factory_v1 import readiness
config = readiness.load_config(private_readiness_path)
report = readiness.collect(database, service_config, config, host_github,
                           running=independently_observed_service_running)
```

`load_config` requires an owned, single-link mode-0600 physical file and rejects
duplicate keys. Required top-level keys are `readiness.FIELDS`; the only optional
key is `runtime_receipts` (the sealed issuer policy below). Service and config
must identify the approved registered operator/project/iteration; repository ID,
privacy and endpoint come from its immutable onboarding. Explicit board and
`board_pin` bind the exact linked ProjectV2/status-field/option IDs. `protection`
contains base=main, exact app-bound required_checks and protection_sha256.
`synthesis_config` is the existing private executable/timeout/provenance contract.
Delivery and originating notification configuration come from the service.

## Concrete supported GitHub route

Default `HostReaders` uses the existing `repositories.GitHub` instance's fixed
credential and no-redirect/no-proxy opener. It does not resolve credentials on
import, request extra scopes, issue tokens or test permissions with writes.

Supported: direct `https://api.github.com`, classic OAuth (`gho_`) or classic PAT
(`ghp_`), **observed** `X-OAuth-Scopes` containing both `repo` and `project` on
all three reads. Accepted scopes are not evidence of authorized scopes. It reads:

1. `/user`: authenticated immutable node ID and login.
2. Exact `/repos/OWNER/NAME`: immutable ID, private/unarchived/enabled repository,
   effective `permissions.pull=True` **and** `permissions.push=True`.
3. A fixed GraphQL query: same viewer, same repository ID/name/privacy and
   viewerPermission WRITE/MAINTAIN/ADMIN, exact open ProjectV2 ID with
   `viewerCanUpdate=True`.

The combination proves the narrow repository/Projects infrastructure route;
role alone or successful reads do not. Endpoint, credential and opener must stay
unchanged during the observation; principal is in the resulting input identity.
Each boundary must recollect and compare that identity, not cache a green result.
Actual candidate-specific policy, workflow-write permissions, checks/protection,
publication and merge authorization remain independent existing guards. This is
not permission to bypass branch rules or modify arbitrary workflows.

Fine-grained PAT, GitHub App, proxy and unknown token routes are explicitly denied
with `github_route_unsupported`: this adapter cannot safely introspect their
complete effective permissions. Missing OAuth headers produce
`github_scopes_unavailable`, never an inference that the header ought to exist.
Insufficient scopes, effective permissions and identity drift have fixed separate
codes/remediation. No raw headers, credentials or exception messages are returned.

References: [OAuth scope metadata](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps),
[ProjectV2 viewerCanUpdate](https://docs.github.com/en/graphql/reference/projects).
No real GitHub call qualified this route in this slice; deterministic response and
header fixtures cover its admission and refusals.

## Native and engineering support

The default reader loads the service's existing private `dispatch_config`, exact
scope/synthesis path and `planning_config`. It uses
`planning_wakeup.configuration`/`scope` to validate original assignment, session,
operator, origin, source/adaptation, original observations, binding reference and
existing allowance; the binding must name the actual durable SQLite file.
Completed original planning producers need no new allowance or coordinator
request. No mailbox record is created and nothing is queued.

Native API source files must match the existing reviewed issue25 installed-source
contracts. Discovery uses `find_spec('hermes_cli')` without importing native or
provider modules. A trusted host Python caller running outside that installation
may explicitly construct `HostReaders(..., native_root=actual_installed_root)`;
this is not a request-configurable plugin or caller hash proof. Missing/drifted
source yields `native_contract_unavailable`; absent original binding/config yields
`native_planning_unavailable` (malformed bindings fail closed).

The reader inspects actual built-in launcher, host synthesis invocation, sandbox
provenance and independent review/diagnosis authorization/binding source files,
plus the existing private synthesis executable contract. Source files must be
host-owned and non-group/world-writable; it never fixes their permissions.
`execution_source_unavailable` means installed host source ownership/support needs
repair, not that future engineering work must already be complete. This inventory
proves source/config support, not execution, provider forwardability, automatic
issuance of future independent authorizations or a live verifier campaign.

## Policy inventory versus finite executable capacity

`slots` retain the existing finite stage/profile/skills/claim_id/launcher shape.
All seven stage policies and planning/ticket installed source closures/adaptations
are inspected, including consumed slots. Profiles/claims/homes remain distinct.
Consumed history does **not** make installed policy unavailable or fence remaining
stages. `slot_capacity` reports available stage/claim IDs and exact active
assignment/run IDs separately; existing unowned homes are unavailable, not fresh.

`readiness.slot_capacity(..., stage=...)` requires a fresh remaining approved slot
for that stage. For already admitted work supply stage, assignment_id and run_id;
claim, stage, complete profile/home, handoff digest and repository/iteration scope
must match. Terminal, retired, delivered and uncertain attempts cannot become new
capacity. Historical claims are never reused. This read-only selection check does
not replace global one-iteration/one-ticket admission, ownership/restart validation
or the actual claim under the existing controls lane.

`spending.capacity_preview` reuses existing exact-scope allowance/expiry/budget,
pending-outcome and quota validation without requiring active controls. Thus a
paused iteration can have ready infrastructure but remain ineligible. Existing
`admission_preview` and per-call `_prepare` still enforce active controls; nothing
is reserved/granted during inventory. Unknown health still blocks. Explicit pinned
no-deployment contracts require deployment/health applicability set to N/A plus
exact-main CI/workflow-backed acceptance; absence never means healthy by default.
Image/socket metadata do not prove actual sandbox/provider capability;
notification routing/executable identity do not prove platform send permission.

`input_identity` projects stable authority/config/source inputs, not the entire
mutable iteration, current liveness/control status, slot consumption or changing
health sample. Fresh health/control/selected-capacity checks remain mandatory;
identity stability never permits skipping them. Reports expose fixed safe codes,
static remediation and no arbitrary exception details.

## Narrow planning and continuation admissions

The first authenticated native hook may checkpoint its proposed host observations
through `planning-bootstrap`. The gate compares the entire proposed row with the
fresh durable row and permits only an initial planning assignment or the first
native observation/binding of that assignment or an admitted successor. Successors
retain the exact completed predecessor/report, delivered invitation, later
authenticated input, updated-spec pins and separately pinned continuation
adaptation; their own scoped sealed policy and allowance remain required.
It validates the approved session/operator/origin,
assignment, installed sources/adaptation and actual native API/executor support.
The default execution reader sees this validated proposal, not an invented prior
execution row. Initial supervised `plan` uses the already host-approved assignment
ID. No completion, publication, model call, sufficient judgment, unrelated state
change, independent hold or existing execution can use this route. Ordinary
planning/publication and wakeup evidence requirements remain unchanged.

`next_iteration` has a distinct `continuation` admission: exact immutable completed
report, delivered invitation, later authenticated same-origin operator message,
and unchanged predecessor. Ordinary work on a waiting predecessor still refuses.
Successor insertion, monitor inheritance, predecessor linkage and supervision
fence are one transaction. The successor's inherited binding is deliberately
unsealed; its own scoped service/readiness configuration must be selected before
engineering dispatch. A predecessor configuration never authorizes a successor.
This does not install/start a service or renew any spending allowance.

The native subscriber waits quietly through ordinary pause and missing first
observations, then rereads current authority on each tick. Source/scope drift and
uncertain enqueue remain terminal holds; explicit resume never reinjects an
unknown, rejected or already accepted message. Unload still cancels/joins its owned
task. No new native loop, thread or socket is created by this lifecycle.

## Sealed issuer policy versus exact issued receipts

Legacy authority files remain recursively sealed. To allow future exact receipts,
select an optional `runtime_receipts` object while paused:

- `scope`: the exact `readiness_gate.scope_identity(item)` digest (approved
  project/iteration/operator/repository/origin, not a caller-provided readiness bit).
- `verifier`: exactly `path` and `sha256` for a reviewed host-owned executable.
  The executable and all declared policy inputs stay immutable. The approved
  implementation must independently verify actual retained execution/evidence;
  echoing its input is not a verifier. None is installed or generated here.
- `receipts`: a finite map of `review`, `diagnosis`, `publication` or `merge` to
  exact absolute JSON paths, each mapped to nonempty immutable verifier constraints.
  Parents must be physical host-private mode-0700 directories, outside worker
  mounts. Files must be host-private mode-0600, single-link and bounded.

Only these declared issued-file bytes may change from absence to an exact receipt,
including aliases in fixed review/publication/merge dispatch configurations;
paths, parent ownership/modes, scope, constraints, verifier bytes and unrelated
configuration remain sealed. The verifier runs with no shell/ambient environment,
a five-second timeout and JSON stdin containing `scope`, `kind`, `path`, exact raw
`sha256` and `constraints`. It must return zero and exactly `{"verified": REQUEST}`.
Controller checks ownership and bytes again afterward. Each ordinary admission
freshly verifies every materialized slot outside the interruption lane and checks
its unchanged accepted identity locally at the final lane boundary. Notification
admission does not execute engineering verifiers.

`readiness_gate.runtime_receipt(database, project, iteration, kind, path,
missing_ok=False)` exposes this fresh exact read for actual consumer/selector
seams. `missing_ok=True` returns None only for an approved not-yet-issued slot.
Consumer-specific run/candidate/evidence and policy validation remains mandatory;
this API is not provenance issuance or generic execution permission. First accepted
identity is retained atomically under private `STATE.receipt-identities`; replacing
or deleting an accepted receipt refuses even after restart. New executions need
new preapproved finite paths, never replacement of historical receipts. Back up
that append-only identity directory with the controller state.

## Host-controlled selection and public probe

Service JSON now requires `readiness_config` and `github_config` (absolute private
paths or null) in addition to the existing fields. New envelopes initialize both
to null. Existing envelopes require these explicit fields; no automatic credential
discovery occurs. `github_config` is a physical owned mode-0600 JSON file with
exactly `api_base` and `bearer`. Select an **already approved** classic OAuth/PAT
with the least supported scopes (`repo`, `project`), effective repository push and
the exact board's update authority. No grant, scope refresh or mutation probe is
performed. Do not put secrets on command lines, in reports or in the repository.
Null uses the credential-blind client and cannot pass the supported OAuth reader.

```sh
mise exec -- python -m factory_v1.service --config /private/service.json pause --reference thread:select
# Operator supplies the reviewed private configurations, not a report/hash Boolean.
mise exec -- python -m factory_v1.service --config /private/service.json select-readiness
mise exec -- python -m factory_v1.service --config /private/service.json readiness
```

These commands document the operational interface; no live commands were executed
for ticket verification. Selection pins local configuration, source bytes/full
modes and immutable allowance terms while paused. Remaining counters, current
health/main samples and slot consumption are not sealed. Each operation freshly
checks capacity/permission/health; its short-lived snapshot detects local changes
during the reads. Installed source drift still fails; selecting policy is not
permission to repin a skill or repair source permissions silently.

Service initialization/loading persists the service path in the selected SQLite
iteration scope. A direct CLI caller using that state cannot omit readiness to
escape shared admission. Missing selection refuses with fixed
`readiness_configuration_required`; policy/source drift refuses with
`readiness_inputs_changed`; external capability denial uses `readiness_blocked`.
The probe distinguishes stopped/unknown/running from capability availability and
active/paused/waiting iteration eligibility. Retained status is not authority.

## Verification and remaining source criteria

Focused `test_readiness` checks use explicit SQLite/files and mocked independent
metadata/header/source/image/socket/health boundaries, with no precursor stage,
worker/controller journey, real process/network/provider, grant or notification.
Native-source fixture success is not installed-runtime qualification. Known
installed skill drift remains denied; no skill pins were changed.

Implemented: service cycle and direct tick/continuation entry, shared durable
admission used by claims, native wakeup, synthesis spawn, sandbox/model admission,
results and completion; exact configured stage/profile/claim/source selection on
role preparation and assignment result save; final candidate publication writes,
merge PUT and reservation status write. Existing
pause/restart/ownership guards remain. Stop, cleanup, reconciliation and incident/
decision attention are not routed through ordinary readiness; truthful existing
effect receipts retain their original save paths.

Core admission now binds prepared/running/terminal assignment and run selection,
launcher path, claim/profile/stage/source, native wakeup identity and actual model
forward. Terminal results may use their consumed slots only for effects, never as
fresh worker capacity. Public runnable reporting projects the current existing
selector and revision/rejection eligibility instead of requiring aggregate spare
slots for a final effect.

`operation='work'` remains the default. `effect` exempts new-worker capacity and a
second allowance for already-prepared publication/delivery/completion; it does not
waive exact downstream effect authority, current policy, permission, revision,
ownership or controls. `notification` requires only the current scope and
notification-route capability checks in both fresh observation and nested local
admission: an inaccessible repository/board, denied GitHub/Projects authority or
unavailable engineering stage cannot suppress an already-recorded incident.
It still requires the exact sealed operator/project/iteration/repository identity,
origin, private host configuration, code integrity and approved executable route;
changed reviewed local inputs fail closed. It needs no fresh worker capacity,
allowance, executor or healthy delivery, including waiting/completed incident
reports. Ordinary work/effects retain their engineering capability requirements.
Unknown operation values refuse.
Exact existing EffectResolution tokens retain only their existing lane-specific
restart/ownership exceptions. Registered repair health admission reuses durable
incident/evidence/host authority and fresh registered-recovery readback; ordinary
work remains frozen and unrelated interruption/revision/uncertainty holds survive.

Every nested checkpoint rechecks local immutable policy/full modes, origin,
controls, ownership, exact selection and operation scope without remote I/O under
the interruption lane. It does not compare a broad old snapshot against its own
legitimate receipt/accounting writes. Mutable homes/artifacts and allowance
counters are excluded from immutable policy; actual per-call quota still refuses.

Parent integration must verify sibling publisher pre-write/final-success and
notification callers use the explicit operations, retain their own exact effect
authority/receipt protocols, and run combined focused checks. Core tests alone do
not establish those sibling-family mutation matrices or final #26 acceptance.

Under the [operator amendment](../docs/specs/no-live-verification-amendment.md), live
acceptance is waived for delivery and **not executed**. No live campaign or live
approval is a next step. Parent owns fresh simplification, independent Standards/
Spec review, policy-compliant CI and autonomous publication/verified integration.
