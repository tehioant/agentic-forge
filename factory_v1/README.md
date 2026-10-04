# Fresh factory v1 — intake, onboarding, planning, tickets, assignments and attention

Standard-library Python 3.11+ controller, outside Hermes core. Run from the repository root; no installation is needed. The CLI records approved intake, reconciles repository onboarding, verifies a same-conversation planning handoff, assigns selected `to-tickets` synthesis, controls ticket publication/frontier/reservation, and prepares bounded role handoffs. It never dispatches implementation workers.

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

## Bounded role assignments (#10)

These trusted controller commands prepare and inspect immutable handoffs, **not
workers**. Prerequisites are an active approved iteration, verified onboarding,
current pinned planning/ticket synthesis, complete publication, an eligible
GitHub frontier and a durable unexpired subscription allowance for the exact
`openai-codex/gpt-6.1-sol/responses` scope. No paid approval, fixture provider or
alternate model can authorize an assignment. Quota exhaustion and uncertain model
operations block admission; preparation never consumes or reserves allowance.
Admission must be checked again at any future actual model-call boundary.

```bash
python -m factory_v1 --state <private-state.sqlite> --operator-id 42 \
  prepare-assignment --project <project> --iteration <iteration> \
  --request <assignment-request.json> --api-base <same-approved-endpoint> --dry-run
python -m factory_v1 --state <private-state.sqlite> --operator-id 42 \
  prepare-assignment --project <project> --iteration <iteration> \
  --request <assignment-request.json> --api-base <same-approved-endpoint>
python -m factory_v1 --state <private-state.sqlite> --operator-id 42 \
  inspect-assignment --project <project> --iteration <iteration> \
  --assignment <returned-assignment-id>
```

### Exact request contract

The JSON object accepts **only** the fields below. Pins are full lowercase hashes,
not branches, abbreviated commits or caller assertions. Examples are placeholders,
not execution evidence.

| Field | Required value |
| --- | --- |
| `stage` | One exact stage from the binding table below |
| `repository`, `repository_id` | Exact onboarded `owner/repository` and positive immutable numeric ID |
| `workspace` | Explicit normalized absolute path, not `/`, distinct from profile home; no repository process is run to verify its contents |
| `issue` | Exactly `{number, id, node_id, body_sha256}`; positive numeric identities and SHA-256 of the exact current UTF-8 issue body |
| `spec_commit`, `baseline` | Full 40-character spec and code baseline commits; spec must match current lifecycle handoff |
| `candidate` | Full candidate commit for corrections/simplification/review; otherwise null or an explicit full commit |
| `standards` | Artifact list containing `AGENTS.md`, with relevant actual project conventions |
| `preceding` | Artifact list containing all stage-required inputs below |
| `profile` | Exactly `{name, home, role, provider, model, operation, reasoning}`; dedicated identifier (not `default`/`personal`), normalized absolute home, exact stage role, approved subscription scope, explicit `low`/`medium`/`high`/`xhigh` reasoning |
| `skills` | Exactly the selected stage closure, with descriptors as described below |
| `capabilities` | Ordered `['read_workspace', 'scratch', 'model']`, plus `'write_workspace'` only for implementation, corrections, simplify and repair |
| `result_contract` | `factory-bounded-result-v1` |
| `claim_id` | Stable unique identifier correlated with the exact bounded assignment |

Artifacts have exactly `{name, content, sha256}`. Names are unique identifiers
(`AGENTS.md` is permitted); content is nonempty UTF-8 text and the hash must match
its actual bytes. A preceding `candidate` artifact contains exactly the candidate
commit. Artifacts are snapshots, not paths to implicitly read or commands to run.
No ambient memory, sessions, credentials, unknown fields or broader capabilities
are accepted. Profile configuration binds identity and policy; it neither reads
nor validates a real Hermes profile home or proves isolation.

### Installed source bindings and adaptations

| Stage | Profile role | Entry points | Required preceding artifacts |
| --- | --- | --- | --- |
| `implementation` | `implementation` | `implement` | None |
| `corrections` | `implementation` | `implement` | `candidate`, `findings` |
| `simplify` | `simplification` | `simplify-code` | `candidate`, `implementation-evidence` |
| `review-standards` | `review` | `code-review` (Standards only) | `candidate`, `simplification-evidence` |
| `review-spec` | `review` | `code-review` (Spec only) | `candidate`, `simplification-evidence` |
| `diagnosis` | `debug` | `diagnosing-bugs` | `failure-evidence` |
| `repair` | `repair` | `diagnosing-bugs`, then `implement` | `incident-evidence` |

Each skill descriptor has exactly `{name, source, path, sha256, dependencies}`.
`source` is the reviewed installed identity; `path` is an explicit absolute regular
non-symlink local source/snapshot file. The entire path ancestry must be non-symlink.
The file's actual bytes must match the reviewed SHA-256 in `role_skills.SELECTED`;
caller-selected new pins are not accepted. For example, the implementation entry:

```json
{
  "name": "implement",
  "source": "/home/ops/.hermes/skills/implement/SKILL.md",
  "path": "/absolute/selected-snapshots/implement/SKILL.md",
  "sha256": "6d3fd9e83b8f36e5213854779db49b256a457a7ebb4a503e53fa7dcff696adc3",
  "dependencies": ["tdd"]
}
```

Supply the complete closure, not just this entry. `implement` requires `tdd`;
`tdd` requires `codebase-design`, `tdd/tests.md` and `tdd/mocking.md` (in that
order). Other selected entries and support files have empty dependency lists.
Engineering stages use those five inputs; repair additionally uses diagnosis;
simplification, each review axis and diagnosis use only their own entry.
Matt's installed identities are `/home/ops/.hermes/skills/<name>/SKILL.md`, with
the two support files under `tdd/`; the non-Matt simplifier is
`/home/ops/.hermes/skills/software-development/simplify-code/SKILL.md`.
`role_skills.py` retains all exact reviewed pins and dependency lists from the
selected sources; test snapshots retain those bytes unchanged. Unknown, duplicate,
unneeded, missing, changed or differently selected dependencies fail closed before
frontier queries or claims. There is no installed-directory scan or bare-name
fallback, no new framework and no change to shared installed skills. Reviewed
source updates require a new code/pin review; this slice has no dynamic updater.

The handoff retains actual instructions, content hashes, entry points, stage rules,
adaptation identity and selected support policy. `implement` keeps mandatory
meaningful tests at agreed seams; test-first ordering is optional, nested review
moves to later dedicated review, and the controller owns commits/publication.
`simplify-code` covers reuse, quality, efficiency and altitude inline in one fresh
context without fan-out, preserving behavior or retaining evidence-backed no-op.
Standards and Spec receive separate fresh read-only assignments and separate
verdicts; they cannot share a top-level profile name or home. Diagnosis is read-only
and stops before modifying phases. Repair diagnoses then implements within incident
scope and still requires later simplification, both review axes and unchanged gates.
Optional codebase-design deepening/design-it-twice workflows and the unavailable
HITL diagnostic template are not silently substituted; they require a reviewed
closure or an honest missing-capability report. Related-skill metadata is not an
implicit dependency or permission to launch helpers.

### Persistence, dry-run and fail-closed execution

Startup validates explicit configuration and skill bytes before GitHub reads and
claim persistence. Preparation reuses the authoritative eligible-frontier verifier
against an in-memory copy of state, allowing only metadata GETs and GraphQL reads.
It checks current issue identity/body, repository ID, requirements, publication,
blockers and conflicting active work. It does not execute git, tests, repository
subprocesses, model requests, workers or GitHub mutations. Baseline/candidate and
workspace are bound declared identities, **not verified checkout evidence**.
Workspace and profile home must be lexically canonical absolute paths: embedded
NULs and double-leading-slash aliases are refused before tracker reads or claims,
not silently normalized. This validation does not access or resolve those paths;
physical aliases through symlinks or mounts remain a later isolation/controller
verification concern, not a capability established by preparation.

Dry-run opens existing state read-only, uses the existing onboarding lock and writes
no schema, lock file, lifecycle checkpoint, spending reservation or assignment claim.
Configured read-only GitHub lookups are allowed. The state and its existing
`.onboarding.lock` must already be available; an absent state is not bootstrapped.
Its deterministic assignment/handoff identities match later unchanged preparation;
policy observations remain subject to current grant expiry and external revisions.

Non-dry preparation adds only local `role_assignments` storage, leaving concurrent
attention, spending and ticket lifecycle payloads unchanged. The shared lock
serializes claims: one ticket scope globally and one top-level assignment per
profile name/home. Ticket reservation checks the same durable assignment ownership
before frontier checkpoints, reservation persistence or tracker status mutations;
compatible same-ticket reservations remain allowed in either admission order.
Exact replay returns the original identity and retained result;
changed claim scope, profile collision or changed handoff conflicts. Claims survive
restart and are never silently stolen, released or replaced, including stale
requirements. There is deliberately no release/reconciliation command here; a
later reviewed controller lifecycle must resolve stale claims. Inspect is a local
immutable snapshot, not fresh GitHub/spending eligibility; it refuses changed local
spec/ticket revisions but remains available during pause or source-file changes.
Preparation and result submission revalidate live scope and selected source bytes.

A successful prepare means `assignment_ready=true`, **not** launch authorization:
`launchable=false`, `execution_allowed=false`, with refusal
`whole_process_isolation_unavailable`. Snapshot availability does not prove mounting,
credential policy, filesystem-tool restrictions or whole-process isolation.
`launch-assignment` still exits 2 with `isolation_unavailable` without a private
trusted launcher configuration, even with `--isolated`. The separately configured
full-process launch lifecycle below revalidates admission; no caller-provided
isolation assertion or worker result enables execution.

### Result input is retained, never accepted as delivery

```bash
python -m factory_v1 --state <private-state.sqlite> --operator-id 42 \
  assignment-result --project <project> --iteration <iteration> \
  --assignment <assignment-id> --request <result.json> \
  --api-base <same-approved-endpoint>
```

Result JSON has exactly `assignment_id`, `handoff_digest`, `claim_id`, `run_id`,
`status`, `loads`, `work`, `artifacts`, `tests`. Identities match the assignment;
`run_id` is an identifier and status is `done`, `blocked` or `stuck`. `loads` has
exactly one `{source, sha256, tool_reference}` for each selected skill source and
each returned `handoff.input_loads` source (issue, immutable documents, standards
and preceding artifacts). References must point to actual selected-instruction/input
loads, not just names/catalog entries. `work` is a nonempty list of observable
skill-guided work references/descriptions; `artifacts` includes a hashed
`stage-evidence` artifact; `tests` is a nonempty list of `{command, result}` with
actual outcomes or an explicit unavailable-capability explanation. A success
assertion alone, missing evidence, wrong scope/load hashes or stale inputs is
refused. Identical result replay is allowed; replacing retained evidence conflicts.

Structural validation cannot authenticate these references or prove execution.
Even a structurally valid `done` is only `submitted_result`: disposition stays
`trusted_execution=false`, `advance_allowed=false`, `close_allowed=false` because
trusted whole-process execution evidence is unavailable. It neither moves stages,
releases claims, closes issues, publishes, merges nor authorizes another model call.
The required later verifier must establish actual skill execution and behavior,
not infer them from worker prose or successful exit.

### Verification scope

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest factory_v1.tests.test_assignments -v
```

Fresh CLI process tests cover prepare/inspect/replay, deterministic non-writing
dry-run, exact source bytes and closures, startup/model/scope/capability/refusal,
frontier blockers and conflicting work, spending exhaustion/expiry/uncertainty,
concurrent ticket/profile claims, pause/stale revisions, result evidence/replay and
non-advancement, disabled launch including isolation assertions, and concurrent
attention preservation. Tests use portable `tempfile` defaults and labeled GitHub
and stage-evidence fixtures. They do **not** prove live GitHub/model behavior,
actual worker skill execution, container isolation or integrated delivery. No
isolation denial probes are repeated by these assignment tests. Dedicated
simplification, independent review, CI and host-controlled delivery remain later
gates, not permissions granted by this interface.

## Full-process sandbox lifecycle (#11)

Only the trusted operator/controller supplies `--launcher-config`. It is a
controller-owned mode-0600 regular JSON file outside the assignment workspace,
with exactly these fields (the artifact root must already exist, be mode 0700,
and belong to the controller):

```json
{
  "image": "nousresearch/hermes-agent@sha256:d4da4a40cd7a28aba983775d9fd31d94cbf153eeb0cb9e844d6d0f612b7c24db",
  "uid": 10000,
  "subscription_socket": "/absolute/private/approved-subscription.sock",
  "artifacts_root": "/absolute/private/factory-artifacts",
  "limits": {
    "seconds": 1800,
    "max_calls": 100,
    "memory_mb": 2048,
    "cpus": 2,
    "pids": 128,
    "scratch_mb": 128
  }
}
```

There are no configurable worker commands, mounts, providers, fallback models,
credentials or arbitrary network routes. Docker must already have the exact
inspected digest. The launcher checks image identity, the known original image
entrypoint/working directory and its declared volume; it deliberately bypasses
that root-oriented bootstrap with the image's Python running the immutable worker
harness. The entire `AIAgent` runs inside the container, not just its terminal.

```bash
python -m factory_v1 --state <private-state.sqlite> --operator-id 42 \
  launch-assignment --project <project> --iteration <iteration> \
  --assignment <persisted-assignment-id> --launcher-config <private-launcher.json> \
  --api-base <same-approved-read-only-tracker-route>

python -m factory_v1 --state <private-state.sqlite> --operator-id 42 \
  inspect-assignment --project <project> --iteration <iteration> \
  --assignment <persisted-assignment-id>

python -m factory_v1 --state <private-state.sqlite> --operator-id 42 \
  stop-assignment --project <project> --iteration <iteration> \
  --assignment <persisted-assignment-id>

# After controller death only; never steals a live controller's ownership.
python -m factory_v1 --state <private-state.sqlite> --operator-id 42 \
  reconcile-assignment --project <project> --iteration <iteration> \
  --assignment <persisted-assignment-id>
```

Launch is foreground and returns the assignment with structured runtime status;
run failure is a recoverable runtime result, not delivered work. The parent
supervisor may background this CLI. A separate guard monitors the controller's
PID/start identity and TTL; controller death removes the actual container and
retains logs. Stop and reconciliation read back container absence; an unavailable
Docker daemon is **not** treated as proof of removal. Claims remain held, including
failed, stopped and stale runs: retry/release/scheduling are not introduced here.

Launch consumes only the persisted handoff and rechecks operator/iteration,
spending allowance, frontier, exact ticket body/IDs, spec/input revisions, skills
and claims. Pinned baseline/candidate source is independently read from physical
Git objects in a clean bare reader containing no repository config, hooks,
alternates, credential files or inherited Git environment. No repository code
runs on the host. Loose and packed objects are supported; submodules, source
symlinks, symlink path aliases, missing objects and oversized source fail closed.
Only committed source is admitted: untracked secrets and the worktree's `.git`
file/directory never enter the worker. `/workspace` is an assignment-scoped
sanitized snapshot under the artifact root, **not** the original checkout; source
artifacts are returned for later controlled integration, never worker commits.

The complete inspected mount set is `/workspace`, writable isolated `/scratch`,
read-only `/inputs` (including exact baseline, handoff and installed-skill closure),
and a single per-attempt model socket. Review/diagnosis source is read-only;
engineering source is scoped writable. The worker home is freshly created under
that attempt's scratch, bound to the dedicated profile claim rather than copied
from the declared host home. An existing host profile home is refused. The image's
`/opt/data` volume is replaced by exact bounded tmpfs; `/tmp` is bounded tmpfs too.
All Docker binds **and** `HostConfig.Tmpfs` are inspected before start, rejecting
anonymous/unexpected volumes. The process is nonroot with network `none`, read-only
root, init, no added capabilities, all capabilities dropped, no-new-privileges,
no devices/port publishing, and explicit CPU/memory/PID/time/request bounds.

The container-local stdlib HTTP relay exposes only pinned-model `/v1/responses`.
It connects through the per-attempt UDS to the outside spending controller, never
to the approved host subscription socket directly. Admission rechecks the active
run, claims, revision and pause state atomically with each existing spending
reservation. Function-only tools are limited to terminal/file operations;
provider-executed network tools and model/route/credential/tier overrides are
refused. The sandbox request bound is 2 MB end-to-end (normal broker clients retain
their original 32 KB default); counts/TTL and the approved allowance remain
mandatory. Pending upstream work gets SSE keepalives, not fabricated model output.
Only actual approved completed response items are forwarded. No paid fallback.
The state lock is released during upstream I/O.

The harness probes shell/file/mount/network denials before constructing the agent,
loads every selected pinned skill/support/input using actual executing-process
Hermes `read_file` tools, and supplies those tool results plus the precise stage
adaptation to the fresh conversation. It retains `events.jsonl`, `loads.json`,
`probes.json`, the conversation, structured result and actual command outputs in
scratch. Durable artifacts additionally include exact input/skill/dependency
identities, initial and returned source manifests, model request/response evidence,
Docker command/inspection and crash/container logs. Result verification correlates
tool reads with exact pinned bytes and reported tests with retained actual
terminal calls. Worker-created symlinks/devices and malformed/oversized evidence
are recoverable refusals, never accepted work.

Even verified isolated execution leaves `execution_allowed=false`,
`advance_allowed=false` and `close_allowed=false`. It neither schedules a next
stage nor authorizes publication/merges. Separate simplification, independent
Standards/Spec review and controlled integration remain authoritative.

Default CI tests exercise the public lifecycle using **labeled deterministic
Docker, model and tracker fixtures**, plus real local HTTP/UDS protocol seams.
They cover pinned source, packed objects/config exclusion, fresh homes, scoped
permissions, admission/large request bounds, forbidden upstream tools, launch
replay, pause, cancellation/controller death, read-only review, bad mounts and
malformed/forged artifacts. They do **not** establish live container, approved
model or GitHub acceptance. A real pinned image `docker create` inspection has
been checked, but real Hermes/model/tool denial journeys remain a separate live
acceptance obligation; retain exact run artifact handles rather than claiming
fixture output proves isolation or delivery.

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

Verification is **operator-selected mock-only** in the existing credential-blind whole-process sandbox: network access and GitHub broker routes are denied; no host credentials, spending or fallback are authorized. No disposable live repository or board is required for this verification. Fixtures are **not live GitHub publication, conversation authentication, worker isolation, merge, deployment or external success evidence**, and passing them does not establish live compatibility. Ticket controls and model spending admission remain separate capabilities; neither starts workers or supplies merge or deployment execution. This controller implements no scheduler, worker launcher, chargeable provider adapter, generalized publisher or merge service, and changes no existing gate. Attention delivery is the scoped supported Hermes CLI adapter described above, verified mock-only under the #15 amendment. Controlled integration remains host-owned; functional ticket requirements, including the linked board and exact readback, remain unchanged.

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

`evidence/issue8-synthesis.json` retains the actual implementing worker's selected-skill-guided decomposition of the supplied pinned first milestone. Its output is unpublished evidence, not new implementation requirements for #8. It explicitly separates actual instruction loads/synthesis from fixture-only public control tests and unavailable live handoff/publication. Its historical live-validation limitations are not prerequisites for the operator-selected mock-only verification above. No board/issue/PR/merge/closure mutation is authorized in this validation; live compatibility is not claimed.

## Discovered bugs (#19): trusted synthesis → scoped tracking

After verified #8 publication, use `synthesize-bug`, `complete-bug` and `publish-bug`
with the same project/iteration/API options as ticket controls. These are trusted
controller commands, not incident detectors, repair agents or worker-launch APIs.
The selected skill and tracker are inherited from the current ticket assignment.

`synthesize-bug --request <discovery.json>` accepts exactly `discovery_id`, `skill`
(the same pinned descriptor as #8), and `discovery`. Discovery contains:

```json
{
  "symptoms": "Saved notes lose accents when reopened",
  "reproduction": ["Save café", "Reopen it"],
  "evidence": ["verified-public-CLI-output-reference"],
  "expected": "café is retained", "actual": "caf is displayed",
  "environment": null,
  "scope": "current", "scope_reason": "Prevents the pinned round-trip requirement",
  "affected": [123]
}
```

Examples are illustrative, not live observations. Provide reproduction or evidence;
unknown environment is explicitly recorded as unavailable. Scope is `current`,
`deferred` or `recovery`, justified by trusted defect assessment, not controller
semantic inference. Current scope names open contracted milestone issues in
`affected`; deferred/recovery cannot introduce ordinary downstream work.

The returned `bug_work` assignment attaches selected instruction bytes, empty
transitive dependency list, scoped adaptation/instructions, pinned documents,
discovery, full paginated exact-repository issue/dependency/board observations,
input digest and `load_sources`. The trusted synthesis agent actually follows
`to-tickets`: context gathering, symptom **and** scope comparison, one verifiable
complete defect slice and genuine blocker/affected edges. Only routine batch
approval is removed. No new milestone, product or executable future batch exists.

`complete-bug --request <result.json>` has exactly `assignment_id`, `input_digest`,
`adaptation`, `execution`, `bug`. Execution uses #8's `run_id`, `loads`,
`decomposition`, `result_digest`; additionally load every returned `load_sources`
entry with its exact digest and actual tool-log reference. Those entries cover
JSON snapshots of discovery, comparisons and `{adaptation, instructions}`. Hash
these with #8's canonical JSON digest, not a prose assertion. Bug has exactly:

- `title`, `desired_behavior`, `references`, `acceptance_criteria`, `triage_label`:
  the common ticket contract. Current references use the immutable milestone;
  deferred/recovery may also reference existing pinned vision requirements.
- `blockers`: unique observed current issue numbers; cycles are refused.
- `scope`, `affected`: unchanged from discovery.
- `status`: `ready`/`blocked` for current/recovery, `deferred` for deferred.
- `comparisons`: one `{number, symptoms_match, scope_match, rationale}` per exact
  observed issue. Same title alone is not duplicate evidence. The result digest
  binds this entire `bug` object.

The deterministic publisher consumes verified trusted-stage evidence, rather than
claiming JSON independently authenticates agent execution. It reads back exact
body/refs/triage/identity, native blockers, scope and linked board status. Current
bugs enter the existing milestone and add native **and textual** blocking edges
on named affected issues; only their Blocked-by section is patched. Deferred bugs
remain unscoped for next grilling. Recovery is unscoped with immediate-recovery
priority. Neither can be reserved through the ordinary frontier, even if someone
places it in the current milestone. No detection, merge freeze, repair dispatch,
recovery reservation or worker containment is established by this tracking slice.

Repeat discovery IDs cannot change content. New IDs with normalized equal symptoms,
expected/actual behavior and scope reuse the assignment. Reworded duplicates use
retained trusted symptom/scope comparisons; existing contracted issues are adopted,
not overwritten. Durable issue/edge/membership intent precedes every write. Lost
responses/process death reconcile exact targets; an absent uncertain target never
causes another creation. A pending uncertain bug creation also blocks new issue
creation under reworded discovery. Independent already eligible work remains
selectable. Pending current tracking holds affected work until verified native
edges exist; blocked bugs then hold dependents through GitHub authority.

`publish-bug --request <retry.json>` accepts only `{"assignment_id":"<exact-id>"}`.
Completed retries re-read evidence, scope, progress and affected edges without
resetting external progress. Operational publication failures persist `status:
blocked`/`blocker` and return inspectable JSON; validation/stale context errors exit
2. Restore exact authoritative observations before retry. Changed candidate scope,
additional affected edges, multiple duplicates or unresolved absent mutations need
explicit controller investigation; this slice supplies no automatic reassignment
or uncertainty override. It does not silently discard new affected work.

### Scoped #19 live-verification amendment

Antoine explicitly selected **mock-only GitHub verification** for #19: no test issue
creation or board mutations. Functional requirements remain unchanged. Public CLI
subprocess tests use real adapter calls to labeled loopback HTTP/Projects fixtures,
including crash/lost-response/restart and native/textual dependency readbacks. They
are not live GitHub compatibility or isolated-worker evidence. The builder is a
host worktree implementation; merge-qualifying credential-blind whole-process
isolated synthesis/review is parent-owned, not established by delegation. Required
independent review/integration gates remain in force. No push, PR, merge, external
bug publication, closure or new product/milestone is authorized by these tests.
`evidence/issue19-synthesis.json` retains this host builder's actual selected-skill
synthesis of the reproduced base frontier defect; it is unpublished, not isolated
or merge-qualifying execution evidence.
