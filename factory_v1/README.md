# Fresh factory v1 — intake, planning and ticket control

Standard-library Python controller, outside Hermes core. Run from the repository root; no installation is needed. The CLI records approved intake, reconciles repository onboarding, verifies a same-conversation planning handoff, assigns selected `to-tickets` synthesis, and controls ticket publication/frontier/reservation. It never dispatches implementation workers.

## Trust and safety boundary

This is a **trusted operator/controller CLI**, not an unauthenticated worker API. The caller authenticates the operator and verifies actual approval/message provenance and originating conversation before supplying inputs. `--operator-id` is trusted configuration, not authentication. Neither JSON actor IDs nor Markdown provenance prove an actual user spoke; that verification belongs to the originating conversational integration.

Keep SQLite state, journals/backups, request files, receipts and output in a private operator-owned directory outside source (`/scratch` during sandbox validation). Do not use shared, attacker-writable or symlinked state paths. New state files are owner-only; existing permissions and directory ownership remain administrator responsibilities. No secrets belong in requests or evidence.

The configured GitHub endpoint must be an approved narrow controller capability. Use a credential-blind broker, not a worker's personal token. HTTPS and loopback HTTP endpoints are supported. URL credentials, query/fragment, remote plaintext HTTP, redirects, environment proxies and invalid timeouts are refused. Endpoint and immutable repository identity are pinned by onboarding. The adapter does not itself prove OS isolation or broker authorization. Ticket publication requires a separately authorized Issues/dependencies/Projects capability at that same endpoint; metadata-only bootstrap access does not grant mutation authority. No credential bridge, paid fallback, protection bypass or gate weakening is introduced here. Independent review, required security/CI checks, controlled integration and integrated-main verification remain mandatory.

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

## Verification and honest boundary

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s factory_v1/tests -v
PYTHONPYCACHEPREFIX=/scratch/factory-v1-pycache python -m compileall -q factory_v1
```

Tests exercise the public CLI in fresh processes with durable SQLite and deterministic loopback HTTP fixtures. Original integrated intake/onboarding and tempfile portability regressions remain unchanged. Planning tests cover actual pinned skill composition, pending answers, restart/idempotence, missing/ambiguous/changed skills, stale assignment/publication, repository/issue/endpoint identity, malformed input, unchanged parent issue, exact immutable docs/issue handoff and disabled ticket execution. Bundled skill snapshots keep tests independent of `/inputs` availability after integration.

Fixtures are **not live GitHub publication, conversation authentication, worker isolation, merge, deployment or external success evidence**. Ticket controls and model spending admission remain separate capabilities. Neither starts workers or supplies merge, deployment or notification execution; no existing gate is changed.

## Tickets (#8): selected to-tickets → controlled publication → frontier → one reservation

The same trusted conversational/stage integration used for planning executes ticket synthesis. The deterministic controller is not an LLM and does not replace the installed skill with templated decomposition or spawn implementation workers. A returned assignment is not proof of synthesis. An authenticated trusted integration must attach actual load-tool references and skill-guided results; a JSON assertion cannot independently authenticate tool logs. Fixtures are labeled `fixture:*`, not live execution.

After `complete-planning`, run:

```bash
python -m factory_v1 --state <private-state.sqlite> --operator-id 42 \
  synthesize-tickets --project <project> --iteration <iteration> \
  --api-base <same-approved-endpoint> --request <synthesis-request.json>
```

The request has exactly `skill` and `tracker`:

```json
{
  "skill": {
    "name": "to-tickets",
    "path": "/absolute/path/factory_v1/ticket_skills/to-tickets.md",
    "sha256": "5c9fba69845c2519b9b35b9af42ae5142c21f8ca15ac2123dc2722002c8058ae"
  },
  "tracker": {
    "project_id": null,
    "status_field": "Status",
    "statuses": {"ready": "Todo", "active": "In Progress", "done": "Done"},
    "triage_label": "ready-for-agent"
  }
}
```

`project_id` is an explicit Projects v2 node ID or null for exactly one open repository-linked board. No name guessing, board creation/linking, optional-board fallback or missing-scope bypass exists. The named Status field must be unambiguous and contain the three distinct configured progress options. Without `scope_field`, scope is the existing open repository milestone titled exactly the registered `iteration_id`; native milestone ID/number/title are pinned, assigned at issue creation and verified on readback. Missing, inaccessible, ambiguous or changed milestone identities fail closed. Blocked/deferred issue-body status keeps work ineligible without inventing extra board options. Legacy configurations with an explicit single-select `scope_field` and all five progress roles remain supported without changing their behavior. Resolved project/field/option and scope identities are durably pinned and revalidated. An inaccessible/ambiguous/missing/changed schema blocks publication before any issue writes.

The controller re-reads the exact immutable planning documents and current spec issue and attaches their contents, SHA-256s, immutable pointers and input digest, plus actual selected instruction bytes, adaptation identity and assignment ID. `to-tickets` has no transitive skill dependencies. The bundled source is an unchanged selected snapshot, not a parallel factory-specific skill. The agent follows context gathering, complete tracer-bullet decomposition, verifiable acceptance and blocker graph drafting. Only the routine ticket quiz/batch confirmation is adapted away. Missing product decisions, capability and spending restrictions remain authoritative.

Return `complete-tickets --request <result.json>` with the same project/iteration/endpoint. The result contains exactly:

- `assignment_id`, `input_digest`, `adaptation`: exact returned stage identities.
- `execution`: `run_id`, `loads`, `decomposition`, `result_digest`. `loads` contains one `{source, sha256, tool_reference}` per selected skill path and immutable document URL. Each tool reference points to an actual trusted execution log. `decomposition` retains how the agent produced the vertical slices and genuine blocker edges. `result_digest` is SHA-256 of UTF-8 `json.dumps(tickets, sort_keys=True, ensure_ascii=False)`.
- `tickets`: nonempty generated contracts, each with exactly `key`, `title`, `desired_behavior`, `references`, `acceptance_criteria`, `blockers`, `status`, `iteration`, `triage_label`. Keys are unique lowercase identifiers; blockers reference keys in this batch. `references` is a nonempty list of `{spec, requirement}`, where spec is the exact returned immutable milestone URL and requirement is a defined `US-N` or exact named text from that milestone. Criteria are nonempty strings. Initial status is `ready`; iteration/label match configuration. Future/deferred executable output, cycles and unknown references are refused.

A supplied batch without a correlated stage, actual selected-source/input load references, substantive execution rationale and matching result digest is refused. Material planning reassignment archives ticket evidence, removes the old handoff and invalidates any existing reservation; it never silently releases active work or admits a replacement under old requirements.

`complete-tickets` persists synthesis and **immediately attempts controlled publication**, without a batch-approval command. `ticket_work.status=blocked` and its exact `blocker` are durable JSON outcomes (exit 0 for an inspectable blocked transition). Input/refusal errors exit 2. Restore authorized access, then use `publish-tickets` to reconcile, not inject another batch. A missing board retains generated slices and evidence rather than dropping the board requirement.

Publication uses GitHub REST `/repos/<exact-repository>/issues` and native `/issues/<number>/dependencies/blocked_by`, plus GraphQL Projects v2 linked-board/schema/item reads and scoped add-item/field-value mutations. It creates blockers first, renders readable blocker numbers and current spec/criteria/status/label/iteration in issue bodies, and never modifies the parent. Every created issue identity/title/body/label/state, native edge, exact membership and scope/progress option is read back. GitHub list connections are paginated; truncated or ambiguous metadata fails closed.

A shared POSIX state lock serializes onboarding, planning and ticket controls across durable commits. Intent is committed before each issue, edge, membership and progress mutation. Stable assignment/key markers plus full repository issue reads reconcile lost issue responses. Returned immutable issue identity is pinned before readback. Lost membership/edge responses reconcile exact targets first. An absent target after an uncertain issue/edge/member write blocks rather than blindly retrying. Absolute field updates are safely reconciled by exact current readback. Repeating completed publication observes current authoritative progress and does not reset externally progressed/closed work.

```bash
python -m factory_v1 --state <state> --operator-id 42 \
  frontier --project <project> --iteration <iteration> --api-base <same-endpoint>
python -m factory_v1 --state <state> --operator-id 42 \
  reserve-ticket --project <project> --iteration <iteration> \
  --api-base <same-endpoint> --issue <eligible-number>
```

`frontier` refreshes exact repository-linked board membership, iteration, native/textual blockers, issue identity/contract/label and progress from GitHub. It is not the local generated batch as a competing backlog: independently added current contracted issues are considered too. A blocker is complete only when its issue is closed for `completed` and its authoritative board status is Done in this scope; unknown/out-of-scope blockers remain blocking. Cycles, mismatched native/textual references and malformed metadata fail closed. Ready/open/contracted/current work with complete blockers is eligible; deferred/blocked/foreign work is not. `inspect` is only a durable local snapshot of the last observation, **not a fresh GitHub claim**.

Reservation first refreshes GitHub and refuses partial publication, paused iterations, existing active work (including foreign iteration work) and an existing different global reservation. It persists one pending reservation before setting the exact board progress to active, then verifies the exact eligible active issue. Lost responses preserve the reservation and reconcile the same run on retry. Competing commands are serialized. No release, dispatch, implementation worker, merge or issue-close operation is supplied here; `execution_allowed` stays false and worker-run correlation stays null.

`evidence/issue8-synthesis.json` retains the actual implementing worker's selected-skill-guided decomposition of the supplied pinned first milestone. Its output is unpublished evidence, not new implementation requirements for #8. It explicitly separates actual instruction loads/synthesis from fixture-only public control tests and unavailable live handoff/publication. Live acceptance remains blocked: the operator-reported authorized private validation repository has no linked board, and the reviewed bootstrap capability is metadata-GET-only. No board/issue/PR/merge/closure mutation was attempted in validation.


