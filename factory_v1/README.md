# Fresh factory v1 — approved intake and repository onboarding

This standard-library Python controller implements issue #5 intake/inspection and issue #6 repository onboarding. Run from the repository root on Python 3.11 or 3.13; no installation or Hermes core changes are required. No beta implementation, contents, profiles or state are loaded.

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

## Verification and scope

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s factory_v1/tests -v
PYTHONPYCACHEPREFIX=/scratch/factory-v1-pycache python -m compileall -q factory_v1
```

Tests invoke the actual CLI in fresh subprocesses with SQLite/HTTP fixtures under tempfile's configured temporary directory (`TMPDIR` in validation and CI), without requiring a `/scratch` mount. A portability regression selects a different temporary root, exercises both lifecycle fixtures through the CLI, and verifies cleanup. Deterministic tests cover previous intake, metadata-only reuse, exact private creation, name/identity conflicts, successful POST/GET immutable-ID continuity across restart, invalid creation IDs, lost responses, killed-process restart, concurrency, read-back outages, blocked permission/conflict cases, endpoint continuity, credential exclusion, ambiguous/deeply nested metadata and explicit receipt restoration. Those fixtures do not prove live external enforcement.

For this continuation, `/inputs/live-receipts.json` provides prior creation/read-back evidence for `tehioant/agentic-forge-fresh-v1-validation`, ID `1402450718`, private, with description `fresh-forge-m1-validation:origin-1555635434036396065`. Do not recreate it. The current CLI restored from that explicit trusted receipt and independently read the exact live target back through the approved loopback broker. Validation scratch's parent/scope conversation IDs are local fixtures, **not** notification/routing evidence. Execution output, not this document, is the evidence authority.

`execution_allowed` remains false. This slice does not establish scheduling, worker isolation, spending admission, Projects/Issues/PR writes, checks, merge protection, deployment, notifications or a self-running factory. It does not change ticket #17's no-bypass gate. Independent review, controller publication/controlled merge and integrated-main evidence remain required before ticket closure. Future slices are not missing repository-onboarding behavior.
