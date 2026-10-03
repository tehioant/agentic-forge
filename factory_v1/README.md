# Fresh factory v1 — intake, onboarding and conversational planning

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

## Verification and honest boundary

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s factory_v1/tests -v
PYTHONPYCACHEPREFIX=/scratch/factory-v1-pycache python -m compileall -q factory_v1
```

Tests exercise the public CLI in fresh processes with durable SQLite and deterministic loopback HTTP fixtures. Original integrated intake/onboarding and tempfile portability regressions remain unchanged. Planning tests cover actual pinned skill composition, pending answers, restart/idempotence, missing/ambiguous/changed skills, stale assignment/publication, repository/issue/endpoint identity, malformed input, unchanged parent issue, exact immutable docs/issue handoff and disabled ticket execution. Bundled skill snapshots keep tests independent of `/inputs` availability after integration.

Fixtures are **not live GitHub publication, conversation authentication, worker isolation, merge, deployment or external success evidence**. Live publication and controlled integration are supplied by the host parent. This slice implements no scheduler, worker launcher, spending admission, generalized publisher, Projects mutation, merge or notification service, and changes no existing gate.
