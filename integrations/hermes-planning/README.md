# Native originating-conversation planning

This opt-in plugin is source only, **not installed/enabled**. It requires
`factory_v1` in the approved native Hermes runtime. No provider/profile/auth
settings are changed by this change.

Host settings: `binding` contains exact durable SQLite `state`, `operator_id`,
`project`, `iteration`, existing native `session_id`, original factory `origin`
and approval `reference`; `api_base` is the existing readback route.
`publication_config` is the absolute path to a separately approved private,
controller-owned, single-link mode-0600 JSON file. No ambient credential lookup.
Its exact fields are:

- `operator_id`, `reference`, `repository`, positive immutable `repository_id`,
  Boolean `private`, exact `api_base`, `bearer` for that approved HOST route;
- `assignment_id`, native `session_id`, immutable full-SHA `parent`.

The bearer stays in host memory/configuration, never durable iteration state,
model context or tool output. Deployment must supply native sender/operator
mapping, actual session ownership and a scoped GitHub capability. This code
neither invents credentials nor authorizes a model call.

## Same-origin route

1. Documented native `pre_llm_call` supplies actual session/task/turn/sender,
   original user message and host conversation history. The binding retains
   host-observed inputs separately from model-authored work claims, source pins,
   adaptation, a generated run ID and exact native loader results. History
   references are host-generated run-local indexes, not invented platform IDs.
   Repeated hooks and a new adapter instance preserve the same correlated run,
   sufficiency and loaded to-spec across tool iterations. A genuinely new user
   turn starts fresh correlation; an unresolved publication must reconcile first.
2. `factory_planning_sufficient` takes `assignment_id`, `rationale`, empty
   `pending_questions`; it loads the selected installed to-spec into the same
   running agent. Loading alone cannot publish or complete planning.
3. `factory_planning_publish` receives actual three-document output plus
   `assignment_id`, `run_id`, `reference`, `answers` (each exact host-observed
   `reference`/`answer`) and ordered `work` entries (`skill`/`result` for grill-me,
   grilling, to-spec). All original host-observed answers and references must
   remain verbatim in provenance.md. Work summaries are explicitly model claims,
   **not independent HOST attestations**. The executing native tool route and
   host-observed loads/input/output correlation are retained independently.
4. HOST publishes only adjacent milestone.md/vision.md/provenance.md under the
   assignment's fixed docs path, on `factory/planning/ITERATION/ASSIGNMENT` based
   on the approved immutable parent. Exact tree readback proves no other leaves
   changed. It creates one deterministic-title specification issue whose exact
   body equals milestone.md and explicitly labels it ready-for-agent. No parent
   issue, main/base ref, merge, protection, shell, arbitrary repository/ref/path
   or model-supplied issue identity is writable.
5. Every write has a committed intent before I/O. Exact blobs, tree, commit/
   parent, branch and issue/docs read back before `planning.complete` is called.
   Unknown blob/tree/commit outcomes remain held, never blindly repeated. Lost
   branch/issue-create responses reconcile only their exact intended effect;
   absent effects or duplicates remain held. Authority/privacy/source/native
   correlation and durable controls are rechecked at mutation and success gates.
6. Native-bound planning cannot use caller-supplied publication pointers or the
   legacy complete tool as execution proof. The existing public planning API
   remains compatible for non-native assignments. A successful publish call
   completes the durable handoff, so the existing scheduler can select synthesis.

## Supervised initial S1 wakeup

The service dispatch JSON optionally supplies `planning_config`; plugin settings
supply the **same** private absolute path as `wakeup_config`. Its exact JSON fields:
`binding` (the exact binding above), `assignment_id`, `profile_name`,
`source_digest` (existing `assignments.digest` of selected `skills` and `adaptation`),
and `allowance`. The allowance has only `reference`, explicitly approved
`provider_route`, native `model`, integer Unix `expires_at`, and `max_wakeups: 1`.
Plugin setting `provider_route` must independently match that approved route.
These are authorization inputs, not discovered personal credentials or permission
to install/enable a profile. The separately approved HOST publisher is unchanged.

The initial native turn must already have retained genuine original observations.
One service tick commits an idempotent request in the existing SQLite iteration.
A native `ctx.spawn_task` subscriber consumes this fixed mailbox, records an
unknown enqueue outcome **before** calling `ctx.inject_message` with the exact
origin, and never repeats any attempted enqueue. True means only accepted,
False is rejected, exceptions/crashes remain unknown. No endpoint, shell callback,
custom framework, plugin-created process or thread is introduced.

This bounded subscriber targets the installed Hermes **isolated plugin host**:
`RemotePluginContext.spawn_task` → `HostRuntime.spawn`, using its existing loop.
In-process loading without the required independently joinable native lifecycle
reports `native_lifecycle_unavailable` rather than creating an unmanaged loop.
Unload retains a cancellation flag, cancels the owned native future and waits at
most two seconds for coroutine-finally confirmation; an unconfirmed join remains
`shutdown_unconfirmed`. Mailbox locking uses the existing controller lock with
nonblocking acquisition so an occupied controller does not wedge that host loop.

The tagged synthetic turn must match approved session, host sender, native model,
request and retained original host history. It preserves the original run and
observations; the wakeup text is **never an operator answer**. Sufficient/publish
and every publisher guard recheck current wakeup approval/cancellation. The
supervisor consumes only an exact native run with verified HOST publication and
completed immutable handoff, freshly reads that publication, then rereads the
SQLite completion before retaining its receipt. Consumption occupies one tick;
only a later tick can select synthesis. Unknown/changed scopes stay held.

Initial publication and wakeup are implemented here; continuation/revision use
their existing separate contracts. S2/frontier progression is not changed.

Tests exercise real publisher/admission functions using explicitly labeled fake
GitHub/native boundaries and retained durable state. No real native model
execution or live GitHub publication is claimed. Plugin installation, native
runtime/provider credentials, live qualification and external publication remain
separately authorized deployment work.

References: official Hermes developer-guide plugins/programmatic-integration and
user-guide features/hooks; installed `agent/turn_context.py` and
`hermes_cli/plugins.py` pre_llm_call/register_tool/dispatch_tool interfaces.
