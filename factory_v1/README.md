# Fresh factory v1 — approved intake and repository onboarding

This standard-library Python controller implements issue #5 intake/inspection, issue #6 repository onboarding, and issue #7 interview/specification preparation and publication verification. Run from the repository root on Python 3.11 or 3.13; no installation or Hermes core changes are required. No beta implementation, contents, profiles or state are loaded. Issue #7's real documentation publication remains blocked until a reviewed controller capability is supplied; deterministic read-back tests are not live publication evidence.

## Trust boundary

The CLI is a **trusted operator/controller interface**, not an unauthenticated API or a worker capability. The caller must authenticate the operator and verify approval provenance and conversation metadata before supplying them. `--operator-id` is trusted configuration; matching a self-reported ID is not authentication. The CLI durably records and validates that handoff, not Discord messages themselves.

Keep state, SQLite journals/backups, request files, receipts and output in a private operator-owned directory outside source (use `/scratch` for validation). Do not use shared, attacker-writable or symlinked state paths. New files have owner-only POSIX permissions; administrators remain responsible for existing permissions and directories. No secrets belong in intake or receipts.

`onboard` uses an explicitly configured GitHub adapter/capability endpoint. Supply a credential-blind broker with narrow permissions, not a worker's personal token. The adapter does not implement OS isolation, role authentication or the trusted broker's authorization policy. HTTPS endpoints and loopback HTTP brokers are supported; URL credentials, query strings, fragments, remote plaintext HTTP, redirects, environment proxies and invalid timeouts are refused. Creation/reconciliation is pinned to the recorded endpoint. There is no bootstrap GitHub bypass, fallback account, provider or paid plan purchase.

## Public lifecycle

An example request follows. IDs and approval references are placeholders, not evidence:

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

`work_item_number` is optional and correlates the exact repository/issue number; it is not evidence an issue exists or is eligible. Repository selection is an exact `owner/repository`, never a URL, inferred unrelated target, repaired identifier or guessed platform. For a new approved product, add:

```json
"repository_intent": {"mode": "new", "marker": "unique-controller-owned-creation-marker"}
```

The trusted handoff must associate a unique ownership marker with this creation. Omitting `repository_intent` preserves prior intake semantics: explicitly selected **existing** repository, never authority to create a replacement. Intake approval also applies to the new-product intent; missing or wrong-operator approval is refused.

```bash
PYTHONDONTWRITEBYTECODE=1 python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  register --request /scratch/operator/approved-intake.json

PYTHONDONTWRITEBYTECODE=1 python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  inspect --project approved-product --iteration milestone-1

PYTHONDONTWRITEBYTECODE=1 python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  onboard --project approved-product --iteration milestone-1 \
  --api-base http://127.0.0.1:8655/github --bearer harmless-dummy --timeout 10
```

Successful commands print one JSON record and exit 0. Refusals/operational failures print JSON on stderr and exit 2; argument errors use argparse help/stderr. `--timeout` must be finite and in `(0, 300]` seconds. A real token is unnecessary for the supplied credential-blind broker; never put genuine credentials in published evidence.

### Durable behavior

- Intake rejects unknown/duplicate fields, missing provenance, ambiguous origins, invalid owner syntax and parser-limit failures before state creation. Original approved intake behavior and exact identifiers are retained.
- First intake is active; additional products are paused. Concurrent registration is serialized. Identical re-registration is idempotent; changed provenance, intent or work-item scope is a conflict.
- `inspect` is read-only, does not create absent state, and exposes durable correlations. Empty and `:memory:` state targets are refused. Control-run IDs denote intake, not worker/model execution; worker ID remains null.
- Existing onboarding requests only repository **metadata**, never contents, git clones or beta files. Exact owner/name/full-name, positive integer repository ID and boolean privacy must read back. Existing public repositories may be reused as explicitly selected; they are not made private silently. Missing/mismatched targets never trigger replacement creation.
- New creation resolves `/user` against the exact selected owner. A different owner requires a reviewed account-scoped capability, not guessed organization endpoints. A pre-existing name is a collision even when its marker matches. The configured identity is the default GitHub account, but intake continues to require an explicit exact target.
- Before POST, commit a durable `pending` intent with marker/endpoint. A POSIX advisory lock serializes onboarding for this state file across CLI processes, including the committed-pending crash window. Use one trusted durable state store per controller; separate unrelated stores are not a distributed creation lock.
- POST `/user/repos` sets `private=true`, exact name/description and `auto_init=false` explicitly. A successful response must supply a positive integer immutable repository ID; that ID is durably committed before read-back. Invalid/missing IDs become persisted investigation blockers, never authority to adopt a later target. The response alone is not success evidence: GET of the exact target must verify the pinned ID, owner, name, full identity, privacy and marker, including on restart after unavailable read-back. Only allowlisted metadata is retained; unexpected credentials are excluded.
- A timeout/lost response/process death reconciles the exact target before retry. A pending target that is absent stays `creation_uncertain`; 404 cannot prove a delayed write will never complete. No automatic second POST occurs. A definitive rejected POST is durably blocked and cannot adopt a subsequent collision. A successful POST followed by denied read-back stays pending/reconcilable, not falsely definitively rejected.
- A verified repository ID cannot be replaced by the same name/marker. Wrong privacy/marker/identity or ambiguous JSON refuses verification. Successful JSON `null` is unavailable metadata, not proof of HTTP 404 absence, and cannot admit creation. HTTP protocol/framing failures produce structured actionable blockers; truncated creation/read-back responses preserve pending intent for exact reconciliation. Network, permission, conflict and plan/protection errors give actionable capability/investigation requests; no purchase, weakening or bypass is attempted.

### Explicit trusted lost-state recovery

If durable state is lost but the trusted controller has genuine prior creation evidence, it may provide `onboard --creation-receipt /scratch/operator/receipt.json`. This is **not** routine onboarding or authority for workers to adopt a collision. The caller must independently validate the receipt against actual controlled creation evidence. The interface trusts the administrator, not an unsigned worker assertion.

```json
{
  "repository": "example/approved-product",
  "repository_id": 123,
  "marker": "unique-controller-owned-creation-marker",
  "api_base": "http://127.0.0.1:8655/github",
  "reference": "trusted-controller-creation-and-readback-evidence"
}
```

Exactly these fields are required. Restore needs a recorded approved new-product intake and binds the exact target, positive ID, approved marker, endpoint and nonempty provenance reference. It **never creates** an absent target. Live identity/privacy/marker/ID must match before the receipt and verified metadata are persisted. Receipt recovery cannot override blocked creation, endpoint pinning or previously verified ID continuity. Regular onboarding still refuses a colliding target without prior durable intent or explicit trusted creation evidence. Endpoint migration is not implemented; request controller review instead of modifying records silently.

## Interview and pinned specification lifecycle (#7)

After verified onboarding, a trusted conversation/agent integration supplies only the current fresh interview through `interview --request`. It must authenticate actual operator messages before handing them to this CLI; the controller cannot authenticate conversation messages from self-reported provenance. The agent supplies questions, assumption challenges, recommendations and its sufficiency judgment. The controller records that judgment, never invents operator answers, and refuses unrelated top-level context fields.

The request has these exact fields:

- `origin`: the registered originating-thread object, unchanged.
- `expected_revision`: `0` for the first submission, otherwise the current integer interview revision from `inspect`. Identical retries are idempotent; changed stale submissions are conflicts.
- `questions`: a nonempty list of `{id, question, challenge, recommendation, answer}`. An unanswered question uses JSON `null`. An actual answer is `{operator_id, reference, text}`, from the configured operator with its original message reference.
- `decisions`: a list of `{id, category, value, question_id, scope}`. Each decision cites an answered question and retains its exact answer text. Scope is `current` or `future`. Categories are `vision`, `milestone`, `programming_language`, `user_facing_language`, `stack`, `requirements`, `constraints`, `non_goals`, `acceptance`, and `testing`.
- `judgment`: `{sufficient, rationale, reference}` records the agent's boolean judgment and its provenance, not operator batch approval.
- Optional `skills`: an explicit fresh pinned catalog and stage roots (below). It is mandatory before publication handoff.

Missing answers/categories keep the interview pending even when the agent says clarification is sufficient. Distinct current answers for a language choice also keep that choice unresolved; the operator can explicitly settle a combined language choice in one answer where appropriate. All original answers, challenges, recommendations and earlier interview revisions remain inspectable. Future choices cannot substitute for current choices, except the whole-product vision.

Sufficient clarification prepares three canonical JSON documents under `docs/factory/<iteration>/<sha256-revision>/`: `vision.json` retains the full vision and decisions; `milestone.json` contains only current decisions; `decisions.json` preserves the full fresh interview with provenance. Changing the interview removes any obsolete handoff and retains prior specifications in history. Specifications are not published by an assertion or by this worker's direct GitHub mutation.

```bash
PYTHONDONTWRITEBYTECODE=1 python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  interview --project approved-product --iteration milestone-1 --request /scratch/operator/interview.json

PYTHONDONTWRITEBYTECODE=1 python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  export-spec --project approved-product --iteration milestone-1 --revision <exact-sha256-revision>

# Only after authorized controller publication returns its actual immutable commit:
PYTHONDONTWRITEBYTECODE=1 python -m factory_v1 --state /scratch/operator/state.sqlite --operator-id 42 \
  verify-spec --project approved-product --iteration milestone-1 --revision <exact-sha256-revision> \
  --commit <exact-40-character-commit-sha> --api-base <reviewed-controller-endpoint>
```

`export-spec` is read-only and returns exact file paths/content plus the concrete publication capability request. The reviewed controller must restrict writes to these documentation paths in the selected repository and reconcile uncertain outcomes before retrying. `verify-spec` rechecks the recorded repository identity, reads the exact Git commit and documentation files at that commit, verifies UTF-8 bytes and Git blob hashes, and revalidates skill pins. Branch names, wrong files, symlinks, content/identity mismatches and missing skills cannot admit handoff. Successful verification automatically records `ticket_synthesis_ready`, with immutable repository/commit/path/spec-revision references and only current decision IDs. No extra batch-approval prompt occurs. It does not synthesize tickets or start execution; those are later slices.

Skill configuration is `{catalog, stages}`. Catalog entries are `{name, path, sha256, dependencies, implicit_loops}` using explicit absolute regular files, exact names and SHA-256 pins. Stage roots name `grilling`, `specification`, `tickets`, `implementation`, `simplification`, and `review`. Missing or ambiguous names, cyclic transitive dependencies and changed content fail closed. Stage contracts adapt only `routine_spec_approval`, `routine_ticket_batch_approval`, and `nested_duplicate_review`; required tests, behavior preservation, independent requirement review and security gates remain authoritative. No ambient skill or memory discovery is performed. A descriptor is a trusted controller input, not permission to inspect unrelated files. These contracts are bounded handoff data, not proof of worker integration or skill execution.

The supplied broker permits repository metadata only for this role. Consequently real documentation publication/read-back cannot be established here, and issue #7 remains blocked rather than reported delivered. Python 3.13 execution and alternate-`TMPDIR` tests pass locally; Python 3.11 is not installed in this sandbox and needs controller/CI execution.

## Verification and scope

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s factory_v1/tests -v
PYTHONPYCACHEPREFIX=/scratch/factory-v1-pycache python -m compileall -q factory_v1
```

Tests invoke the actual CLI in fresh subprocesses with SQLite/HTTP fixtures under tempfile's configured temporary directory (`TMPDIR` in validation and CI), without requiring a `/scratch` mount. A portability regression selects a different temporary root, exercises both lifecycle fixtures through the CLI, and verifies cleanup. Deterministic tests cover previous intake, metadata-only reuse, exact private creation, name/identity conflicts, successful POST/GET immutable-ID continuity across restart, invalid creation IDs, lost responses, killed-process restart, concurrency, read-back outages, blocked permission/conflict cases, endpoint continuity, credential exclusion, ambiguous/deeply nested metadata and explicit receipt restoration. Those fixtures do not prove live external enforcement.

The supplied `/inputs/live-receipts.json` is empty in this continuation. No prior creation receipt was reconstructed or assumed, and the validation repository was not recreated. A current metadata-only read through the approved loopback broker observed `tehioant/agentic-forge-fresh-v1-validation`, ID `1402450718`, private, with description `fresh-forge-m1-validation:origin-1555635434036396065`. This is not evidence of creation, receipt recovery, documentation publication, or delivery. Execution output, not this document, is the evidence authority.

`execution_allowed` remains false. This slice does not establish scheduling, worker isolation, spending admission, Projects/Issues/PR writes, checks, merge protection, deployment, notifications or a self-running factory. It does not change ticket #17's no-bypass gate. Independent review, controller publication/controlled merge and integrated-main evidence remain required before ticket closure. Future slices are not missing repository-onboarding behavior.
