# Fresh factory v1 — intake, onboarding, planning and attention

Standard-library Python controller, outside Hermes core. Run from the repository root; no installation is needed. The CLI records approved intake, reconciles repository onboarding, returns a same-conversation planning assignment, and verifies its GitHub handoff. It does not start workers, generate tickets or execute implementation.

## Trust and safety boundary

This is a **trusted operator/controller CLI**, not an unauthenticated worker API. The caller authenticates the operator and verifies actual approval/message provenance and originating conversation before supplying inputs. `--operator-id` is trusted configuration, not authentication. Neither JSON actor IDs nor Markdown provenance prove an actual user spoke; that verification belongs to the originating conversational integration.

Keep SQLite state, journals/backups, request files, receipts and output in a private operator-owned directory outside source (`/scratch` during sandbox validation). Do not use shared, attacker-writable or symlinked state paths. New state files are owner-only; existing permissions and directory ownership remain administrator responsibilities. No secrets belong in requests or evidence.

The configured GitHub endpoint must be an approved narrow controller capability. Use a credential-blind broker, not a worker's personal token. HTTPS and loopback HTTP endpoints are supported. URL credentials, query/fragment, remote plaintext HTTP, redirects, environment proxies and invalid timeouts are refused. Endpoint and immutable repository identity are pinned by onboarding. The adapter does not itself prove OS isolation or broker authorization. No publisher, credential bridge, paid fallback, protection bypass or gate weakening is introduced here. Host-side publication, independent review, required security/CI checks, controlled integration and integrated-main verification remain mandatory.

## Intake and repository onboarding

Example placeholders are not evidence:

```json
{
  "project_id": "approved-product",
  "iteration_id": "milestone-1",
  "repository": "example/approved-product",
  "idea": "The product explicitly requested by its operator",
  "approval": {"operator_id": "42", "reference": "operator-message-101"},
  "origin": {
    "platform": "discord", "chat_id": "123", "thread_id": "123",
    "parent_chat_id": "456", "scope_id": "789"
  },
  "work_item_number": 5
}
```

`work_item_number` is optional: it correlates the unchanged parent work/spec issue and is not evidence the issue exists. A planning spec must use a different issue. Select an exact `owner/repository`, never an inferred target or repaired identifier. The default intent is an explicitly selected **existing** repository. An approved new product additionally supplies `"repository_intent": {"mode": "new", "marker": "unique-controller-owned-creation-marker"}`.

```bash
python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  register --request /scratch/operator/intake.json
python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  onboard --project approved-product --iteration milestone-1 \
  --api-base <approved-endpoint> --timeout 10
python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  inspect --project approved-product --iteration milestone-1
```

Success prints JSON and exits 0. Refusals/operational failures print JSON to stderr and exit 2; argument errors use argparse help. Timeout must be finite in `(0, 300]`. Empty and `:memory:` state targets are refused. `inspect` is read-only and does not create absent state.

Intake rejects unknown/duplicate fields, invalid identities/origins, missing approval and parser-limit failures before creating state. Identical registration is idempotent; changed provenance/scope is a conflict. First product is active, others paused. Concurrency is serialized. Execution remains disabled and worker-run correlation stays null.

Existing onboarding reads metadata only. Exact owner/name/full-name, positive repository ID and boolean privacy must read back. Missing targets never authorize replacement creation. New creation checks `/user` against the selected owner, refuses pre-existing collisions, commits pending intent before POST and serializes the creation boundary with a POSIX advisory lock. POST explicitly sets private repository, name, marker description and `auto_init=false`. Returned immutable ID is durably pinned before exact readback. Unexpected metadata/credentials are not retained.

Lost responses and process death reconcile the exact target without another POST. An absent pending target stays uncertain. Definitive rejected creation remains blocked; invalid returned IDs cannot authorize later adoption. A successful POST with unavailable readback remains reconcilable. Verified repository ID cannot be replaced by the same name/marker. Endpoint migration requires controller review.

For genuine lost-state recovery only, `onboard --creation-receipt /scratch/operator/receipt.json` accepts exactly:

```json
{
  "repository": "example/approved-product",
  "repository_id": 123,
  "marker": "unique-controller-owned-creation-marker",
  "api_base": "https://approved-capability.example/github",
  "reference": "trusted-controller-creation-and-readback-evidence"
}
```

The administrator must independently validate this trusted receipt. It is not a worker assertion or authority to create/adopt an absent target, override blocked creation, change endpoint or replace a pinned identity. It requires approved new-product intent and exact live identity/privacy/marker readback.

## Planning (#7): grill-me → grilling → to-spec

### 1. Assign work to the agent in the originating conversation

After onboarding, supply `/scratch/operator/plan.json`:

```json
{
  "origin": {
    "platform": "discord", "chat_id": "123", "thread_id": "123",
    "parent_chat_id": "456", "scope_id": "789"
  },
  "expected_revision": 0,
  "skills": [
    {"name": "grill-me", "path": "/absolute/path/factory_v1/planning_skills/grill-me.md", "sha256": "caaf8b8de1684f96e26b28f3c29189db5c89cce4b73e1c93d86164f66ef88637"},
    {"name": "grilling", "path": "/absolute/path/factory_v1/planning_skills/grilling.md", "sha256": "10ff989e7498b23b5acb49d5048f11dcd906757d2f79c5cdf8a00001381296f2"},
    {"name": "to-spec", "path": "/absolute/path/factory_v1/planning_skills/to-spec.md", "sha256": "43ad9cf318e5e7d3d1fa360253a37021796dc87a0c2e595ad262661a10f85088"}
  ]
}
```

Replace the paths with explicit absolute regular files. The bundled files are exact copies of the selected `/inputs/skills/{grill-me,grilling,to-spec}/SKILL.md` snapshots; those original paths may also be used in this sandbox. Supplied-byte pins are fixed, not caller-selected arbitrary skill versions. There is no ambient discovery or future ticket/implementation/review catalog. Exactly these three unique skills must resolve; grill-me's sole dependency is grilling.

```bash
python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  plan --project approved-product --iteration milestone-1 \
  --request /scratch/operator/plan.json
```

Use the returned approved idea, origin and `planning` assignment **in that same conversational agent context**. The agent loads the attached exact instructions: grill-me calls grilling; grilling maps the design tree, asks numbered frontier questions with recommendations, and waits for actual user decisions before dependent rounds. It does not launch a noninteractive interview worker. The user owns product/technical choices; the agent owns sufficiency judgment. Preserve original question/recommendation/answer references and decision rationale in Markdown, not a custom settlement or categorized answer schema.

Local assignment instructions adapt upstream routine shared-understanding/spec/test-seam confirmation only: no additional routine approval after sufficient clarification. Actual unresolved test/technology choices are discussed during grilling. Existing required security, spending, independent review and CI gates are not adapted away.

Identical `plan` retries return the existing assignment, including pending/completed state. Onboarding, planning reassignment and completion share the existing POSIX state lock, acquired before the modifying read/SQLite transaction and held through commit; concurrent onboarding cannot restore an obsolete assignment or discard an acknowledged completion. The lock remains held across onboarding's durable-before-POST commits. Material new conversation requirements require an explicit new assignment with the current `planning.revision` as `expected_revision`; prior assignment remains in history, any old handoff is removed, and old completions are refused. Do not silently reuse stale documents for changed requirements.

### 2. Leave unanswered questions pending

`complete-planning` accepts a pending update with exactly these fields:

```json
{
  "assignment_id": "<returned-assignment-id>",
  "origin": {"platform": "discord", "chat_id": "123", "thread_id": "123", "parent_chat_id": "456", "scope_id": "789"},
  "sufficient": false,
  "rationale": "The user has not selected the storage approach.",
  "reference": "agent-message-reference",
  "pending_questions": ["Q3: local files or SQLite?"]
}
```

Pending updates persist locally without GitHub reads, issue creation or execution. They are idempotent and survive fresh CLI processes. Silence never becomes an answer. If publication is unavailable after synthesis, retain pending status and its actual blocker/question; host publication can resume later.

### 3. Synthesize prose and publish through the host

When the frontier is empty, the **conversational agent actually follows to-spec**, using the existing conversation and authorized repository context. It produces three adjacent Markdown documents under the returned `planning.docs_path`:

- `milestone.md`: real prose using to-spec's seven sections, finite scope, requirements, acceptance, technical/testing decisions, non-goals and definition of done.
- `vision.md`: whole-product intent and future capabilities clearly separated from this finite milestone.
- `provenance.md`: original questions, challenges/recommendations, actual answers with operator/message references, interpreted decisions, clarification and agent sufficiency provenance.

The host's controlled publication path publishes those exact snapshots in the onboarded repository and creates an open GitHub specification issue whose body equals `milestone.md` byte-for-byte and has `ready-for-agent`. Leave the parent spec issue unchanged. No to-tickets execution or ticket generation belongs to #7. The CLI never publishes externally; a returned assignment is not evidence an interview or publication occurred.

### 4. Verify completion and record GitHub handoff

Completion JSON uses the same six pending fields, with `sufficient: true`, an empty `pending_questions` list, and additionally:

```json
{
  "documents": {
    "milestone.md": "<exact synthesized Markdown snapshot>",
    "vision.md": "<exact vision Markdown snapshot>",
    "provenance.md": "<exact original conversation/decision Markdown snapshot>"
  },
  "commit": "<actual full 40-character lowercase Git commit SHA>",
  "issue_number": 123
}
```

These three extra fields are combined with the six identity/judgment fields, not submitted alone. No transcript/decision schema or routine approval receipt is required.

```bash
python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  complete-planning --project approved-product --iteration milestone-1 \
  --request /scratch/operator/completion.json --api-base <same-approved-endpoint>
```

Verification rechecks skill bytes, endpoint and repository identity, exact immutable commit, file paths/types, base64 encoding, UTF-8 snapshot bytes and Git blob hashes. It reads the exact open issue, verifies repository/number/immutable ID/URL, excludes PRs, checks the label and matches the issue body to the immutable milestone snapshot. A stale/mismatched file, issue or assignment cannot complete. Completed retries re-read external targets; issue edits and identity replacement are refused. Different completion snapshots require explicit reassignment, not silent replacement.

The verifier checks prose template structure and external equality; **it does not pretend to prove natural-language sufficiency, truth, or user authorization**. Those require the trusted same-origin conversational agent and actual provenance review, not another deterministic semantic engine. The stored completion digest binds the supplied snapshots without creating a hidden transcript for #8.

Success records `planning_complete` and `handoff` containing only GitHub repository, issue, immutable commit and document/blob references. #8 consumes those GitHub references and performs to-tickets later. `execution_allowed` remains false. This is planning completion, not delivered implementation or ticket closure.

## Model spending admission (#9)

This slice adds a durable controlled-operation boundary; it does **not** start a worker or change `execution_allowed` from `false`. The commands below are trusted controller/operator entry points. `--operator-id` is configuration, not authentication: the caller must verify the operator's actual allowance/approval provenance before recording it. Keep the state database, socket parent directory and broker process private as described above.

Record a narrow allowance (or explicit scoped approval) for one exact project/iteration/provider/model/operation. `--ceiling` is an integer unit count with semantics owned by the fixed adapter: **subscription requests** for the approved Codex route, or **labeled fixture units** for deterministic charge/ceiling tests. Units never stand in for a monetary ceiling. Paid API calls, purchases and financial commitments have no adapter and remain refused even with an arbitrary grant. Adding one requires an explicitly reviewed currency/pricing/maximum-charge contract and operator approval, not a new URL or provider flag:

```bash
python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  spend-grant --project approved-product --iteration milestone-1 \
  --provider fixture-provider --model fixture-model --operation generate \
  --kind allowance --ceiling 100 --expires <future-unix-seconds> \
  --reference <verified-allowance-or-approval-record>
```

Run a fixed-scope broker as the trusted host. It reserves the adapter-owned upper bound durably before the request leaves the process. `--fixture-url` permits only a labeled loopback test endpoint under `fixture-provider`; `--subscription-socket` permits only the approved `openai-codex/gpt-6.1-sol/responses` host capability. Live chargeable API routes are unavailable/fail-closed. The request cannot supply provider/model/operation/endpoint/credentials. Broker requests and model output are transient; durable rows contain only exact scope, idempotency key, request digest, reservation, decision, outcome, and trusted reconciliation reference. No credential is stored or passed to the worker. Known-iteration pre-admission request refusals and disabled-provider configuration attempts are also audited durably with the fixed host scope and bounded reason only; rejected request bodies and credentials are never retained. If durable refusal evidence cannot be written, the broker reports unavailable and still initiates no fallback.

```bash
python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  model-broker --socket /scratch/operator/model.sock --project approved-product \
  --iteration milestone-1 --provider fixture-provider --model fixture-model \
  --operation generate --fixture-url http://127.0.0.1:<fixture-port>/fixed-fixture --reservation 10
python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  model-call --socket /scratch/operator/model.sock --operation-id <stable-idempotency-key> \
  --reserve 10 --request /scratch/operator/model-request.json
```

The socket parent must be operator-owned mode `0700`. The socket is mode `0600` for the operator UID, or mode `0666` **inside that private directory only** for an explicitly selected container UID; Linux `SO_PEERCRED` enforces the exact `--worker-uid` on every connection. Bind-mount only that individual socket into its assigned container, never the parent directory. The broker pins scope/upstream, enforces `--ttl` (default 300 seconds, maximum 3600) and `--max-calls` (default 100, maximum 1000), rejects redirects/non-loopback fixture routes and ignores proxy environment settings. The worker's `--reserve` must match the host-owned adapter reservation (`--reservation`, default 3 for fixtures; always 1 for subscription calls), so a worker cannot under-reserve a charge. Admission requires a matching, unexpired grant with enough remaining units. Reservation is subtracted before outbound I/O. Quota denial/refusal and blocked state survive restart. Exact same-key replay never reissues a completed/failed operation; altered scope/request/reservation conflicts. Transport/protocol ambiguity leaves the operation pending and its reservation frozen. Only a trusted controller reconciliation with exact scope, outcome, bounded actual units, and evidence reference can resolve it; never retry an uncertain operation first.

This is an enforceable model-access seam, not full worker isolation: there is no worker launcher/container policy in this slice. Before a later launcher uses it, run the entire worker in a network-disabled container with no provider credentials and mount only its assigned socket; expose no host route, provider SDK credential, or direct network capability. Grant the socket only to that worker identity, pin a per-assignment scope, and verify the boundary with real container tests. Current deterministic tests prove CLI → real Unix socket → fixed loopback fixture execution, not containment of arbitrary workers or live provider billing. The approved subscription route uses a host-owned UDS HTTP capability, not a worker's provider credential. The trusted capability must be fixed to `https://chatgpt.com/backend-api/codex/responses`, `gpt-6.1-sol` and the standard/default tier, with refresh grants retained on the host. The adapter sends only `/v1/responses`, pins model/tier/store/stream and validates a completed exact-model SSE response. It never connects directly to a provider URL, proxies an arbitrary path or selects fallback. The controller verifies this capability's deployment and ownership; merely naming a socket is not proof of that deployment. Configure it with `model-broker ... --provider openai-codex --model gpt-6.1-sol --operation responses --subscription-socket <verified-host-capability.sock> --worker-uid <assigned-uid>`. Subscription payloads contain `input` and only the supported inference fields; there are no secrets in the CLI request. Do not enable chargeable providers until a host-owned adapter provides trustworthy pre-call reservations, exact upstream/model pinning, and authoritative currency/billing reconciliation.

`spend-reconcile` is trusted-controller-only and does not authorize another attempt; terminal idempotency keys stay terminal. A failed outcome releases unused reservation only after trusted confirmation. A successful receipt records actual units no greater than reservation and refunds only the difference. Uncertain outcomes block **all new keys** in the affected iteration until exact trusted reconciliation, not only retries of the same key. A subscription HTTP 429 persists quota exhaustion without fallback and freezes its reservation conservatively. After an exact billing/allowance receipt, `spend-resume --project ... --iteration ... --provider ... --model ... --operation ... --reference <verified-quota-restoration>` additionally requires the exact exhausted scope and an unexpired grant. Recording another grant alone cannot erase a quota block. Paused iterations cannot start a broker or admit operations; trusted reconciliation remains available while paused.

## Attention delivery (#15)

The trusted controller/operator CLI now provides `attention`, `notifications`,
`deliver`, `attention-reconcile` and `respond`. These are **not worker broker
routes**. Do not expose them, the state file, or transport configuration to workers.
The existing trusted intake boundary verifies the actual origin; no new lookup is
claimed. Every modifying attention operation reloads the iteration under the
shared onboarding/planning/spending lock, validates operator provenance and exact
Discord metadata, and retains the persisted destination. No destination override,
channel-name resolution, origin-token repair or home-channel fallback exists.
Missing/malformed origin and destination drift fail closed.

Routine intake/onboarding/planning transitions remain chat-silent. Other slices
supply justified attention content; this slice does not judge incidents or generate
completion claims. Submit an event with exactly these fields (illustrative only):

```json
{
  "event_id": "incident-episode-15", "kind": "incident",
  "message": "Delivery is unhealthy; evidence is available.",
  "issue_number": 15, "run_id": "run-15", "evidence_ids": ["evidence-15"]
}
```

Kinds are `incident`, `decision` and `completion`; decisions additionally require
`decision_id`. Use a stable event ID for repeated observations of the same episode;
new episodes receive distinct IDs, even if symptoms match. Identical event replays
reuse durable state; changed content conflicts. A decision ID cannot be rebound to
another event. Records retain project/iteration, repository/issue, lifecycle stage,
controller/worker run and evidence identities. A completion intent does not create
another iteration or authorize its interview.

All commands use the existing `--state` and `--operator-id` globals, and
`--project`/`--iteration` selectors. `attention --request <event.json>` persists intent
without sending. `notifications` inspects records read-only. Configure transport in
a private controller-owned file outside source:

```json
{
  "command": ["/absolute/path/to/hermes"],
  "approved_destinations": [
    {"platform": "discord", "chat_id": "123", "thread_id": "123", "parent_chat_id": "456", "scope_id": "789"}
  ]
}
```

`command` is a trusted executable prefix, not worker/request input. It must select
an already reviewed **messaging-scoped** Hermes deployment/profile, whose messaging
policy permits exactly the intended operator conversation. Do not use a broad
personal profile, inject unrelated credentials or bypass its messaging policy.
Profile/credential enforcement remains deployment responsibility; this adapter
adds an exact-origin allowlist, not an OS sandbox. No credentials are read or stored
by the controller adapter and no platform API is implemented.

`deliver --event <event-id> --transport-config <file>` actually invokes the supported
installed CLI contract:

```text
hermes send --to discord:<persisted-chat-id>:<persisted-thread-id> --file - --json
```

Only this explicit target is supplied; never `discord` alone or `--list`.
The body contains event and correlation JSON. Native `@` mentions, `MEDIA:`
attachment directives and `[[...]]` controls are rendered as literal JSON escapes;
original content stays in state. This disables mention/attachment interpretation
without inventing an unsupported Discord `allowed_mentions` CLI flag. No
`--mention` is passed. Keep bodies human-readable and concise.

Delivery states are truthful:

- `pending`: persisted, eligible to send. Missing/unauthorized configuration does
  not increment attempts. Failed process launch or installed CLI usage exit 2 is
  known no-effect and remains retryable.
- `uncertain`: committed **before** send, including crashes immediately before or
  after the external effect. Timeout, backend exit 1, malformed/duplicate JSON,
  unknown shapes and skipped results remain uncertain. Never retry these blindly.
- `accepted`: exit 0 and JSON `success: true`, with neither error nor skipped. This
  is a transport acknowledgement, **not verified external delivery**. Raw backend
  output, notes and errors are not persisted because they may contain secrets.
- `delivered`: an independently verified, exact-bound trusted receipt was supplied.

Repeated `deliver` on uncertain/accepted/delivered records returns the recorded
state without sending. Inspect `state`, not exit 0 alone, to determine outcome.
The shared lock spans the durable-before-send commit and send/final commit;
concurrent controllers cannot resend or overwrite a decision response. Process
death releases the lock, but never erases uncertainty. This is recoverable,
event-deduplicated delivery, **not an exactly-once network guarantee**.

The staged installed CLI exposes acknowledgement but no authoritative lookup or
receipt contract. None is invented here. If independent controller/operator
observation is available, `attention-reconcile --request <receipt.json>` accepts
exactly:

```json
{
  "event_id": "incident-episode-15", "attempt": 1,
  "delivery_key": ["approved-product", "milestone-1", "incident-episode-15"],
  "destination": {"platform": "discord", "chat_id": "123", "thread_id": "123", "parent_chat_id": "456", "scope_id": "789"},
  "content": {"event": "<exact persisted event object>", "correlation": "<exact persisted correlation object>"},
  "outcome": "delivered", "message_id": "1001",
  "reference": "<independently verified exact-target/content receipt evidence>"
}
```

Replace the two illustrative content strings with actual persisted objects. The
trusted caller must independently verify the exact rendered body, destination,
message identity and attempt; JSON strings alone do not prove delivery. `not_sent`
uses the same fields **without** `message_id` and requires positive evidence of
no effect, not merely absence in a search. It may reopen only an uncertain attempt,
not contradict a successful acknowledgement. Exact receipt replays are idempotent;
wrong content/destination/key/attempt, malformed message IDs, extra fields and
terminal receipt replacement are refused. A later send gets a new attempt;
stale receipts cannot bind it. Without authoritative evidence, keep uncertainty.

`respond --request <response.json>` is a separate trusted authenticated input
boundary, not a transport ack, free-form chat listener or semantic approval parser:

```json
{
  "event_id": "decision-event-15", "decision_id": "direction-15", "operator_id": "42",
  "origin": {"platform": "discord", "chat_id": "123", "thread_id": "123", "parent_chat_id": "456", "scope_id": "789"},
  "reference": "<verified operator message reference>", "response": "Investigate the outage"
}
```

The caller must verify who spoke and that this is an explicit answer to that exact
pending decision. Delivery, acknowledgement, unrelated chat and silence cannot
populate it. Exact response replay is idempotent; changed responses conflict.
Responses can arrive before notification delivery but do not change delivery
state. Neither response nor receipt changes planning/spending authorization,
`execution_allowed`, scheduling, merge gates or product scope.

### #15 acceptance trace (mock-only amendment)

| Criterion | Implementation and regression evidence |
| --- | --- |
| 1: exact verified origin, fail closed | `attention.lifecycle`/`get_record`; malformed origin/context, allowlist and origin-drift public CLI tests |
| 2: routine silence, correlated attention | `enqueue`; intake/planning/onboarding silence, durable correlation tests |
| 3: persisted intent, retry/restart/uncertainty | `deliver`/`reconcile`; actual mock subprocess death before/after effect, no-effect vs unknown tests, concurrent writes |
| 4: duplicate vs distinct events | event/decision identity conflict checks; duplicate/conflicting/new incident and decision tests |
| 5: exact pending replies, no unsolicited authority | `respond`; ack/silence/unrelated chat refusal and exact pre/post-delivery response tests |
| 6: supported Hermes transport | `HermesTransport`; mock process command/JSON-result tests; sandbox `/scratch/verify_hermes_reference.py` executes `/inputs/hermes-reference/send_cmd.py` with labeled mock credential loader and message service |

Antoine's amendment replaces **live verification only**. No live platform messages
are authorized or sent. Mock fixtures prove the functional command/result path,
not real Discord delivery, credentials, messaging-policy deployment or receipts.
Independent review and host-controlled integration remain later gates; this
sandbox implementation does not merge, close #15 or call any worker GitHub route.

## Verification and honest boundary

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s factory_v1/tests -v
PYTHONPYCACHEPREFIX=/scratch/factory-v1-pycache python -m compileall -q factory_v1
```

Tests exercise the public CLI in fresh processes with durable SQLite and deterministic loopback HTTP fixtures. Original integrated intake/onboarding and tempfile portability regressions remain unchanged. Planning tests cover actual pinned skill composition, pending answers, restart/idempotence, missing/ambiguous/changed skills, stale assignment/publication, repository/issue/endpoint identity, malformed input, unchanged parent issue, exact immutable docs/issue handoff and disabled ticket execution. Bundled skill snapshots keep tests independent of `/inputs` availability after integration.

Fixtures are **not live GitHub publication, conversation authentication, worker isolation, merge, deployment or external success evidence**. Live publication and controlled integration are supplied by the host parent. This controller implements no scheduler, worker launcher, chargeable provider adapter, generalized publisher, Projects mutation or merge service, and changes no existing gate. Attention delivery is the scoped supported Hermes CLI adapter described above, verified mock-only under the #15 amendment.
