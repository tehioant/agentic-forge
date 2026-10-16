# Conversational setup decisions (#68)

Opt in with the existing `enrollment` setting and `onboarding_decisions: {state: null}` before registration, or select the existing private absolute SQLite path in `state`. Nothing is activated or granted by importing this module. A configured `project_setup` additionally selects the concrete T2 `ProjectSetup` adapter (available after #67 integration); this module does not replace it. No additional notification transport is installed.

## Host producer API

`OnboardingDecisions(enrollment, config, setup=None)` reuses T1's DraftStore and admitted/native correlation. Public host-only methods:

- `request(draft_id, operation, blockers, notification=None)` retains one immutable operation. `operation` has exactly `operation_id`, `project`, `scope` (nonempty dict), `ceiling` (nonnegative integer). Every blocker has `decision_id`, `cause`, `scope`, `ceiling`, `question`. Causes: `product`, `platform`, `permission`, `allowance`, `unsupported`, `uncertain`. Blocker ceilings cannot exceed the operation ceiling. Repeating identical requests preserves responses; changing an operation under the same ID refuses. Empty blockers enqueue nothing and do not prove readiness.
- `status(draft_id, operation_id, session_id=...)` freshly reads producer authority, returns exact operation/ready/pending, retained independent responses, and a held message only when needed. A response is direction, never a grant. Answered questions are not repeated when another prerequisite remains unresolved.
- `resume(draft_id, operation_id, session_id=...)` revalidates, preserves existing controls/holds and calls only the retained operation. Intent is saved before the call; unknown outcomes cannot resend. Registered model scopes may include `model_operation: {provider, model, operation}`; existing spending admission preview must cover the operation ceiling, without creating a grant. The producer must also revalidate all authority at actual admission.
- `reconcile(draft_id, operation_id)` is a host-only concrete T2 readback: matching ready journal plus actual `ProjectSetup.route` validation. No conversational receipt or caller-supplied attestation clears an uncertain attempt.

For registered operations, `notification` has exactly `project`, `iteration`, `issue_number`, `run_id`, `evidence_ids`; the actual iteration must already have its real origin/operator/correlation. Existing attention.lifecycle enqueue/respond validates those identities. Deterministic event IDs deduplicate repeats. Existing outbox delivery/reconciliation owns transport; no sends are performed here. Before registration, questions go into the actual native turn instead of inventing an iteration/issue.

## Concrete repository adapter and later producers

When `setup` is an actual T2 ProjectSetup, the operation scope is exactly `{repository: 'owner/repo', expected_revision: <retained revision>}`. Fresh `ProjectSetup.capability` reads the real approved host configuration; `ProjectSetup.attach` performs its existing guarded exact-target retry. Only matching repository-permission blockers can be satisfied by that capability. Product, allowance, unsupported and uncertain blockers are not relabelled as repository authorization. The adapter never generates access, credentials, execution provenance or spend approval. A failed/unknown attempt remains uncertain until actual setup readback reconciles it.

Other T5/T7 producers use a fixed native seam, not a callback registry: `factory_project_setup_status({draft_id, operation}) -> {operation, ready: bool, pending: [decision_id], reason?: str}` and `factory_project_setup_resume({draft_id, operation}) -> {operation, status: complete|blocked|not_started|uncertain}`. These are trusted producer routes: status must read actual scoped host authority; resume must independently revalidate at admission. They are **not supplied by this ticket for arbitrary future operations**. Missing routes remain held, never fall back to another tool/model or paid provider.

## Native response contract

The optional registration wraps enrollment hooks once and keeps its draft-update tool. `factory_project_setup_response` accepts only `{operation_id}`; no operator, origin, answer, credential or grant can be supplied by model arguments. The actual latest admitted message must be:

`forge-decision {"operation": <exact operation>, "answers": [{"decision_id": "...", "scope": <exact blocker scope>, "ceiling": <exact integer>, "direction": "<explicit scoped instruction>"}]}`

Only the current authenticated correlated operator's raw input is parsed. Ordinary affirmative chat, acknowledgements, silence, other speakers, other projects/operations/scopes/ceilings, duplicate decisions and uncertain-effect approvals refuse. Independent answers can be batched or partial; prior answers persist. The raw structured response is deliberately explicit, not a model-generated approval. This bounded implementation does not yet offer native buttons or a natural-language approval parser.

## Qualification limits

The one focused test seeds pending records directly; transport, producer and host boundaries are mocked. No live activation, network, model, worker, end-to-end setup or full-suite execution is claimed. T2 integration must supply its module and coalesce optional plugin wiring; T5/T7 must register their exact status/resume producers. Independent parent simplification/Standards/Spec review remains required. Product-answer projection into T1's product fields remains the interview producer's responsibility, not permission authority.
