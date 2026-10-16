# Conversational setup decisions (#68)

The normal interface is conversation: Forge explains the held operation, recommends an understandable product choice when needed, and asks only for decision-changing direction. For a single displayed choice reply **approve** or **reject**. For a displayed batch reply, for example, **1 approve, 2 reject**; partial numbered answers are retained. No operation IDs, decision IDs, scopes, ceilings, JSON or private configuration edits are required from the user. A generic “yes”, acknowledgement or unrelated affirmative chat is not approval.

## Host producer API

Opt in through the existing enrollment setting and `onboarding_decisions: {state: null}` (or the existing private absolute controller SQLite path). This is administrator configuration, not per-project onboarding work. Imports activate nothing and grant nothing. A configured `project_setup` selects concrete T2 `ProjectSetup`; no extra notification transport is installed.

`OnboardingDecisions(enrollment, config, setup=None)` reuses the admitted DraftStore. Host-only methods:

- `request(draft_id, operation, blockers, notification=None)` retains an immutable operation with exactly `operation_id`, `project`, nonempty `scope`, and nonnegative integer `ceiling`. Each blocker has `decision_id`, `cause`, `scope`, `ceiling`, and a plain-language `question` describing the actual choice. Causes are product, platform, permission, allowance, unsupported and uncertain. Recommend a specific product direction in the question rather than asking the user to edit configuration. Internal IDs and exact scopes remain server-side. Identical requests retain partial answers; rebinding refuses. Empty blockers are quiet, not proof of readiness.
- `status(..., session_id=...)` freshly reads actual producer authority, retaining independent responses without repeating answered questions. Direction is never a credential, spend grant or execution authority.
- `resume(..., session_id=...)` retries only the retained operation after fresh producer admission. Rejections hold that operation even if host authority later becomes available. Registered operations preserve independent pause/model/spending holds; optional `model_operation` uses existing spending preview against the retained ceiling, never a new grant. Intent is persisted before dispatch; unknown effects cannot blindly resend.
- `reconcile(draft_id, operation_id)` requires the exact ready T2 journal and actual `ProjectSetup.route` validation, not a conversational attestation.

Registered notification metadata is exactly `project`, `iteration`, `issue_number`, `run_id`, `evidence_ids`. Existing attention lifecycle owns exact-origin deduplicated enqueue/respond and delivery/reconciliation. Before registration the native turn presents the choices. No transport sends occur in this component.

## Current-prompt response binding

The pre-LLM hook retains the numbered prompt mapping, exact operation/blocker snapshots, draft revision and originating admitted message. The next authenticated native raw input is bound to that current mapping by the post-admission hook. Response fields cannot be authored by the model: `factory_project_setup_response` accepts only `{}` and parses the actual correlated operator input. A response-turn context refresh retains the old displayed mapping instead of renumbering it. Answers consume that prompt once; another response requires a newly presented prompt. Replay, intervening unprompted chat, changed draft revision/operation/scope/ceiling, unknown or duplicate choice numbers, other speakers and mismatched message references refuse. A current pending response is processed before automatically reusing restored host capability, so a rejection cannot race with an automatic retry.

Only explicit approve/reject syntax is accepted. The server reconstructs exact decision scopes and ceilings from retained records. Partial answers remain durable; declined operations require a new supported proposal, not automatic reversal. Uncertain-effect holds are explanatory only: they are never approvable choices. Native context supplies the faithful presentation contract; actual platform delivery/reconnect behavior is not qualified by these mocked component checks.

## Concrete repository adapter and missing producers

The inspected final T2 API is `capability(draft, repository)`, `read(draft_id)`, `attach(draft_id, expected_revision, repository, *, session_id)`, and `route(draft_id)`. No signature adaptation was necessary. Concrete operation scope is exactly `{repository, expected_revision}` matching the retained draft revision. Only matching repository-permission blockers can be satisfied by that real host capability. Attach performs its existing guarded exact-target operation and revalidates at admission. This component never supplies credentials or execution authority.

T5/T7 non-repository operations still require concrete trusted `factory_project_setup_status({draft_id, operation}) -> {operation, ready: bool, pending: [decision_id], reason?: str}` and `factory_project_setup_resume({draft_id, operation}) -> {operation, status: complete|blocked|not_started|uncertain}` producers. Their status must read the real host policy/capability and their resume must enforce it again at mutation. These routes are not implemented here; missing routes remain held. T2/T5/T7 must actually call `request` from their blockers. Service/configuration readiness, native routing/reconnect refresh, product-answer projection and supported-alternative construction still belong to their real producers. No automatic grant, fallback model or paid-provider switch is supplied.

## Verification limits

One existing focused method now covers conversational single/batch/partial answers, stale/replay/ambiguous/model-field refusals, rejection/restored-authority ordering, independent pause, exact-origin attention, narrow signature-constrained T2 mocks, uncertain-response no-resend and concrete journal/route reconciliation. Records are seeded directly; no worker/setup/E2E journey or real service/model/GitHub/native activation is run. Canonical external-execution guard, exact selected IDs and byte/mode fingerprints are retained separately. This is a bounded source correction, not a claim that all #68 acceptance criteria or the missing T5/T7 producer integrations are complete.
