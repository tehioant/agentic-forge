# Native originating-conversation planning

## Repository-free conversation enrollment (#66)

Optional host setting `enrollment` enables intake before any existing `binding`:

```json
{
  "root": "/absolute/private/controller-drafts",
  "operators": {"discord": ["actual-approved-user-id"]},
  "skills": [
    {"name": "grill-me", "path": "/approved/installed/grill-me/SKILL.md", "sha256": "existing-reviewed-pin"},
    {"name": "grilling", "path": "/approved/installed/grilling/SKILL.md", "sha256": "existing-reviewed-pin"},
    {"name": "to-spec", "path": "/approved/installed/to-spec/SKILL.md", "sha256": "existing-reviewed-pin"}
  ]
}
```

Paths and hashes above are explanatory placeholders, not a deployable approval.
Use the existing exact installed paths/pins enforced by `skills.resolve_installed`;
this feature cannot repin. The existing private mode-0700 root must be outside
source and native profiles; draft JSON and lock files are mode 0600. It creates
no product checkout, repository, iteration, board, allowance or service.

`post_gateway_admission` records the exact host source/user/message/raw text,
subject to the separate platform/operator policy and actual plugin profile.
Every authorized nonempty message can provisionally retain intake observations;
the existing native agent decides whether it is a project request/answer, not a
keyword recognizer or new interview engine. Unrelated chat is not a decision.
Blank/missing/unapproved identity refuses enrollment; display names never grant
authority. Stable full origin + operator + host profile identifies the draft.
Reconnect can change routing/native session IDs without creating another draft.

`pre_llm_call` requires actually bound task-local ContextVars (no environment
fallback), matching admitted routing key/message/source/principal, and actual
native session/task/turn/sender. Plain user text or exact host-attributed Discord
and Slack text must match the retained raw input. The hook dispatches the existing
native `skill_view` and returns verified grill-me/grilling instructions into this
same ongoing turn, together with retained product interpretation and questions.
The agent recommends choices and asks only unresolved decision-changing questions.
A loader result is not proof the model followed instructions or finished work.

Public contracts for downstream T2/T6:

- `DraftStore(root).read(draft_id) -> dict | None`: **host-only** independent
  snapshot. `draft_id` is the SHA-256 digest of exact `origin`, `operator_id`,
  `host_profile`. Snapshot retains `observations` (raw text, exact source,
  message_id and routing key), `product`, `pending_questions`, `sufficient`,
  model `rationale`, optimistic `revision`, latest native correlation and transfer.
- Registered `factory_project_draft_update(args, *, session_id=host_value)`:
  args are exactly `expected_revision`, `product` (nonempty string values for
  name/intent/objectives/users/milestone/constraints/acceptance),
  `pending_questions` (strings), Boolean `sufficient`, nonempty `rationale`.
  Product fields merge; questions replace; revision increments. Sufficiency
  needs all dimensions retained and no pending questions. Updates require the
  current admitted message/native load and unchanged sources. There is no
  model-supplied operator, origin, draft ID or authority. Old message/stale revision
  and another shared-session speaker cannot overwrite. Raw observations are not
  model-writable. Interpretations/sufficiency are model judgments, not approvals.
- `DraftStore.transfer(draft_id, expected_revision, binding) -> dict`: **host-only**
  T2 receipt association after T2 independently verifies real identities.
  `binding` has exactly nonempty `project`, `iteration`, `assignment_id`,
  `reference`. Requires sufficient current revision; same receipt is idempotent,
  a different target refuses. This method does not verify repository/native
  assignment existence, create them, or grant mutation/model authority. Consumers
  must revalidate the receipt through existing onboarding/planning validators.
- T6 can consume exact origin/principal/observations/questions but must keep
  permission decisions in its existing scoped approval contracts. Starting or
  completing this interview grants no repository, spending or execution rights.

When both options are configured, the existing approved planning origin/session
is excluded from enrollment and keeps its prior route. This is source-only and
requires the documented native hooks/task-local context in the hosting runtime;
missing correlation fails closed for draft writes even though host hook errors
may fail open to ordinary chat. No live gateway/model execution is qualified.

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

## Existing repository setup (#67)

Optional host `project_setup` is read by enrollment registration; no plugin
entrypoint change or pre-created planning binding is required. Its exact keys:

- `root`: existing private mode-0700 setup journal directory.
- `workspace_root`: existing private mode-0700 product-checkout parent, disjoint
  from journal, draft, controller state, source, profiles and capability directories.
- `state`: absolute controller SQLite path under an existing mode-0700 parent.
  May be fresh; setup uses registered intake's existing schema. Existing state is
  preserved. **Do not call service.initialize/load before lawful per-project
  readiness/configuration exists:** that prematurely binds supervised admission.
- `capability_config`: explicitly selected existing private single-link mode-0600
  host JSON with exactly `operator_id`, `repository`, `reference`, `api_base`,
  `bearer`. Operator/repository must exactly match the admitted draft and explicit
  choice; reference is separately approved setup authority, not interview consent.
  Current checkout adapter supports `https://api.github.com` / GitHub HTTPS only.
- `native_project_db`: null, or explicit existing private native Project DB path.
  Only when selected, use supported `hermes_cli.projects_db.connect`,
  `find_by_primary_path`, `create_project` and `get_project`. Create with the actual
  checkout as primary folder and no board_slug; verify ID/folder after writes.
  Existing unrelated Project settings and backlogs are never modified.

Registered `factory_project_attach` accepts only `repository` (literal
owner/repository) and `expected_revision`. Current authenticated admitted context
supplies draft/principal/session, never model-provided internal identities. Only
a sufficient current draft can attach. The host capability scopes repository
metadata access and checkout; no ambient gh login, secrets in argv, publication,
allowance, service activation, worker or creation permission is inferred.

Public host API available as `enrollment.setup`:

- `attach(draft_id, expected_revision, repository, *, session_id) -> snapshot`:
  T3 can reuse this **after** independently reconciling approved repository
  creation/baseline. Host-only IDs must come from enrollment, not product input.
  Uses `validate_intake/register_iteration`, `repositories.onboard`, exact Git
  clone/detached immutable checkout and `planning.plan`. Returns actual planning
  assignment ID, deterministic per-draft project/initial iteration, verified
  repository ID, workspace and native binding. No fabricated execution receipt.
- `read(draft_id) -> snapshot | None`: private journal, safe of credentials.
  T4/T5 consume `repository`, `repository_id`, `commit`, `checkout`, `project`,
  `iteration`, `binding.state`, optional `native_project`, and `transfer`.
  They extend existing tracker/readiness configuration through their own approved
  operations; this ticket does not initialize a service or require execution
  readiness in order to create planning. Existing source is never overwritten.
- `route(draft_id) -> ConversationBinding`: freshly validates durable current
  project/operator/origin/planning IDs and interruption state. T7 consumes this
  dynamically and wires its same-turn context/tools/publication configuration;
  no product user must prepare the binding. T2 creates no native execution or
  review/approval proof, and does not yet dispatch or install T7's router.

Journal checkpoints are atomic private JSON, serialized by the draft-store lock:
selection -> repository identity/commit -> checkout intent **before** process I/O
-> exact root/origin/HEAD readback -> optional native Project readback -> actual
planning/registered-state readback -> binding -> `DraftStore.transfer` association.
Raw draft observations/source are retained verbatim. Intake origin is only the
existing exact Discord-thread five-field projection; unsupported or malformed
origins block rather than normalizing displayed/raw inputs into authentication.
Replay rechecks onboarding and checkout and uses idempotent intake/planning.
A persisted checkout intent is never recloned, reset, cleaned or overwritten:
a completed exact checkout is reused; missing/partial/different checkout remains
blocked for host reconciliation of that exact intent. Lost native creation
responses reconcile by exact primary folder; no native backlog is created.
A crash before draft transfer can replay the ready checkpoint and associate it.
Changing repository or selected revision refuses instead of substituting targets.

T6 consumes `status: blocked`, `blocker.code`, `blocker.operation:
attach-existing`, `consequence`, `next_decision`, and retained checkpoints. Codes
include approval_required, github_blocked, repository_mismatch, baseline_required,
checkout_blocked/workspace_mismatch, native_project_unavailable/mismatch,
iteration_paused, setup_unavailable. A blocked result means **no worker dispatched**,
not execution readiness. Restore exact capability/access or reconcile the known
workspace effect, then retry the same attach; never blindly remove intent or
clear pause/restart/revision/incident/readiness/spending holds. T6 approval is not
implemented by this journal and cannot be synthesized from interview sufficiency.

Focused tests seed minimal state and mock intake/onboarding/planning/native/Git
boundaries independently. Adapter argv and source-contract mocks are not live Git,
GitHub or installed-native qualification. Production external operations remain
unrun here; capability installation and T7 routing are separately owned work.

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

## Automatic conversational composition checkpoint

`project_handoff` opts into one enrollment/setup/creation/tracker/execution/decision
owner; static `binding` cannot also be configured. Its exact host settings are
`publication_config` and `wakeup_config` (private absolute paths or explicit null).
The user provides product answers and a literal existing repository choice, not
configuration or stage-advance commands. The same `ProjectSetup` instance owns
creation, tracker and execution dependencies. Native tools preserve the original
schemas and use `setup.route` for actual binding authority.

See [project-handoff.md](../../docs/project-handoff.md) for the public adapter API,
focused evidence scope and outstanding source gaps. **This checkpoint is not a
claim of complete fresh-project automatic activation.** T5 preplanning and actual
ticket synthesis/publication are now called before execution registration. Optional
`project_native` has exactly `root` and `capability_config`; the plugin constructs
the real NativeProject helper and reuses its bound publisher/subscriber lifecycle.
The concurrent helper must still be integrated by the parent. First registration
still needs the narrow first-seal adapter documented in project-handoff.md; this
coordinator never fabricates a pause or resumes intentional holds. Unknown effects
use the exact producer's retained-effect readback, never conversational approval.

## Supervised initial S1 wakeup

The service dispatch JSON optionally supplies `planning_config`; plugin settings
supply the **same** private absolute path as `wakeup_config`. Its exact JSON fields:
`binding` (the exact binding above), `assignment_id`, `profile_name`,
`source_digest` (existing `assignments.digest` of selected `skills` and `adaptation`),
and `allowance`. The allowance has only `reference`, explicitly approved
`provider_route`, native `model`, integer Unix `expires_at`, and `max_wakeups: 1`.
Plugin setting `provider_route` must independently match that approved route.
An optional private `input_envelope` explicitly approves the native hook rendering:
omit it (or use `{"kind":"plain"}`) for the unchanged exact plain-input route;
shared sessions use `{"kind":"sender_attribution","platform":"discord",
"user_id":"42","user_name":"Operator"}`. Platform must equal the bound
origin, user_id must equal the bound operator, and the name is a conservative
1–80-character ASCII canonical name (starts alphanumeric; remaining characters
are alphanumerics, spaces, `.`, `_`, `'`, `(`, `)`, `-`). No control characters,
line breaks, brackets, arbitrary wrappers or backfill are admitted. The hook
requires byte-for-byte `[Operator] ` plus the exact durable request message;
Slack instead requires `[Operator | Slack user <@U123ABC>] `, with the exact
bound typed ID (`U`/`W` followed by 2–31 uppercase letters/digits).
This grants only one explicitly selected rendering, not normalization, identity
or execution authority. Actual callback sender/session/model/task/turn/history,
native profile/route and durable request/source correlation remain mandatory.
The name/prefix never comes from model/tool arguments, never becomes an answer,
and carries no invented plugin metadata or native-internal provenance claim.
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

If the genuine original active native turn already completed HOST publication
before any scheduler request, the supervisor instead adopts that existing
completion. A `mode: origin_turn`, `status: consumed` receipt retains its exact
original task/turn/run, native/publication/completion/handoff digests and current
exact private binding approval. It invents no request ID, enqueue, running state
or execution turn, and performs no model call or allowance consumption. Entry
and final immutable publication readbacks plus fresh durable reads and real
control admission must agree; source, approval, completion or publication drift,
pause and stop refuse. The future wakeup expiry need not still be live for this
accounting-only adoption; its schema/scope must remain valid, and every actual
wakeup or tool-publication admission still requires an unexpired allowance.

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
