# Native originating-conversation planning seam (source candidate)

This opt-in native Hermes plugin is **not installed or enabled** by this change.
It requires `factory_v1` to be importable in the separately approved Hermes host
runtime. No provider/auth/profile configuration is modified.

The host supplies plugin settings `binding` (exact `state`, `operator_id`,
`project`, `iteration`, native `session_id`, unchanged factory `origin`, and
explicit approval `reference`) and `api_base`. The SQLite file must already
exist. The binding maps an existing origin session; it must not be inferred from
model text or a newly created worker session. Enablement/session mapping and any
host model allowance are separate operator-authorized deployment operations.

## Native boundary

- `register(ctx)` registers documented `pre_llm_call` and two native tools.
- `pre_llm_call` reads the existing planning assignment and current controls,
  verifies all three reviewed installed source pins, and calls the actual native
  `ctx.dispatch_tool('skill_view', ...)` for `grill-me` and `grilling`. The exact
  returned instructions become current-turn context. Unrelated sessions get no
  context. No helper, agent, subprocess or model is started.
- The already-running origin agent judges clarification. Missing decisions stay
  in this conversation. `factory_planning_sufficient` requires no pending
  questions, loads the actual selected installed `to-spec`, and returns its
  instructions to that same tool loop. The agent continues synthesis without a
  new operator confirmation; its genuine conversation supplies the answers.
- `factory_planning_complete` is available only after that same native-turn load.
  It takes the existing complete-planning request (`assignment_id`, `origin`,
  `sufficient`, `rationale`, `reference`, `pending_questions`, `documents`,
  `commit`, `issue_number`; continuation assignments retain their existing
  additional `execution` contract). It calls `planning.complete`, which freshly
  verifies repository identity, exact immutable docs/commit and issue bytes.
  Merely loading instructions does not admit completion. The scheduler's existing
  selector then selects ticket synthesis from the completed durable handoff.

## Deliberate remaining limits

This seam does **not** turn a native loader receipt into authoritative behavioral
execution provenance. The native model run and real user-answer provenance have
not been qualified. The unit tests mock the native loader boundary, not synthesis
execution, and label documents/readbacks as explicit fixtures.

Initial `planning.INSTRUCTIONS` still delegates publication to the HOST. There
is no initial-spec controlled publisher exposed by this plugin. If the origin
host lacks its separately approved publication route, synthesized output remains
pending; the plugin cannot complete the automatic published handoff by itself.
The plugin's readback client is credential-blind like the supervisor; private
repository readback needs the approved host route. These are explicit remaining
integration gaps, not credentials that this code invents or execution it claims.
A supervisor with no active origin turn does not inject a new model turn. Native
`inject_message` is supported, but its `True` is enqueue-only and is deliberately
not treated as completion or permission to start a model.

Consequently this is a tested source seam, **not full S1 or #25 acceptance**. S2
next-ticket/recovery sequencing and #26 aggregate readiness remain separate.

References: official Hermes developer-guide `plugins`, `programmatic-integration`,
and user-guide `features/hooks`; installed `hermes_cli/plugins.py` methods
`register_hook`, `register_tool`, `dispatch_tool`, `inject_message` and
`hermes_cli/plugins_injection.py`. The retained offline contract probe executes
actual installed routing/dispatch function bodies with the native external
registry/host mocked; it does not import auth, install anything or invoke models.
