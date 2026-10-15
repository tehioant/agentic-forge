# Read-only readiness inventory (#26 slices 1–2)

This Python API is **not an execution gate**. Service/public-operation boundary
wiring is still outstanding; `dispatch_allowed` and `iteration_runnable` remain
false. Reports never grant permission, reserve capacity, run skills or prove
iteration completion.

```python
from factory_v1 import readiness
config = readiness.load_config(private_readiness_path)
report = readiness.collect(database, service_config, config, host_github,
                           running=independently_observed_service_running)
```

`load_config` requires an owned, single-link mode-0600 physical file and rejects
duplicate keys. Top-level keys are exactly `readiness.FIELDS`. Service and config
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
is reserved/granted during inventory. Unknown health still blocks. No-deployment
contracts remain unsupported by the existing delivery validator, never healthy by
default. Image/socket metadata do not prove actual sandbox/provider capability;
notification routing/executable identity do not prove platform send permission.

`input_identity` projects stable authority/config/source inputs, not the entire
mutable iteration, current liveness/control status, slot consumption or changing
health sample. Fresh health/control/selected-capacity checks remain mandatory;
identity stability never permits skipping them. Reports expose fixed safe codes,
static remediation and no arbitrary exception details.

## Verification and next source work

Focused `test_readiness` checks use explicit SQLite/files and mocked independent
metadata/header/source/image/socket/health boundaries, with no precursor stage,
worker/controller journey, real process/network/provider, grant or notification.
Native-source fixture success is not installed-runtime qualification. Known
installed skill drift remains denied; no skill pins were changed.

Next: wire service inspection/selection and direct public-operation checkpoints,
including native enqueue, synthesis/worker/model start, claim/result admission,
publication/merge final mutations and originating notifications. Recheck selected
capacity and existing global ownership/restart/controls at their real durable
boundaries. Preserve cleanup, stop/pause, reconciliation, pending incident reports
and exact existing hold-resolution operations even when ordinary readiness fails.
Parent owns integration, fresh simplification and independent Standards/Spec
reviews. Canonical/full CI, publication and real service/container/model/GitHub/
health/notification qualification remain held. This checkpoint does not finish #26.
