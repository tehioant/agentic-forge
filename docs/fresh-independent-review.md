# Fresh independent review (#13)

This is the deterministic simplification → independent Standards/Spec review →
corrections seam in `factory_v1`. It uses the existing assignment claims, sandbox,
read-only check runner, installed skill pins and approved model/spending route.
There is no new scheduler, worker framework, Git mutation, merge or closure.
Requirements are issue #13 and the fresh spec/interview at
`cccb21e35cc989a1ecd5a3c88f0689a694d77d9c`.

## Public commands

All examples use the same globals and exact registered scope:

```sh
python -m factory_v1 --state /controller/state.sqlite --operator-id 42 \
  prepare-review --project product --iteration m1 \
  --request /controller/standards.json --api-base http://127.0.0.1:8000
```

`prepare-review` takes **exactly**:

```json
{
  "candidate_assignment": "<64-hex completed simplification assignment ID>",
  "axis": "review-standards",
  "profile": {
    "name": "review-standards-attempt-1",
    "home": "/controller/fresh-standards-home-1",
    "role": "review",
    "provider": "openai-codex",
    "model": "gpt-6.1-sol",
    "operation": "responses",
    "reasoning": "high"
  },
  "skills": [{
    "name": "code-review",
    "source": "/home/ops/.hermes/skills/code-review/SKILL.md",
    "path": "/home/ops/.hermes/skills/code-review/SKILL.md",
    "sha256": "47f4e52c21694def9c7c11cbfbf891ca35eac7a93e395797515be3c8a409ae50",
    "dependencies": []
  }],
  "claim_id": "standards-attempt-1"
}
```

Prepare the other axis with `axis=review-spec` and a **different fresh profile,
physical home and claim**. Both contexts load the actual selected immutable skill
instructions, original issue/specification/standards, baseline, precise diff,
simplification outcome and controller checks. Neither receives the implementation
conversation or its persuasive summary. Each runs only its assigned code-review
axis, using the full skill instructions (including Standards' smell baseline).
Serial execution is supported; separate read-only assignments may run concurrently
when the host's enforced resources permit it. This slice does not add scheduling.

`--dry-run` writes no state/claim; exact preparation replay keeps its identity.
`prepare-assignment` enforces these same contracts, not a bypass. The precursor
must have completed its sandbox run, removed the container, passed exact private
controller checks, preserved its source/baseline/check pins and have no unresolved
simplifier findings. A caller-authored result cannot create that precursor.

Use the existing `launch-assignment`, `inspect-assignment`, `stop-assignment` and
`reconcile-assignment`. Launch config must retain the exact ordered precursor
verification commands. No absence/substitution of checks is admitted. Source is
copied from the **checked simplified artifact**, not silently from host Git HEAD.
Review mounts `/workspace` and `/inputs` read-only, with separate writable scratch,
no Git metadata, no host credentials/Docker socket and no external network. Both
reviewer and controller checks must leave submitted bytes/modes unchanged.

### Revision identity

`handoff.review.pins` contains exactly:

- `candidate_assignment`, `candidate_run`: the completed simplifier identity;
- `head`: the original submitted Git snapshot pin;
- `tree_sha256`: the actual simplified source manifest digest;
- `baseline`, `baseline_sha256`: the Git baseline and sanitized baseline manifest;
- `spec_commit`, `issue_sha256`: original immutable requirements;
- `checks_sha256`: the exact private precursor check-receipt bytes.

A modified artifact is **not a manufactured Git commit**. The Git head and resulting
content address remain distinct, as in #12. Later publication must map those exact
bytes to an independently verified Git tree/commit; this slice cannot authorize
merge. Files, executable status, baseline and check receipts are revalidated at
prepare, launch and aggregate acceptance. New corrections produce a new candidate
identity, so old axes cannot qualify it even when the tree happens to be identical.

## Worker result

Keep the ordinary bounded result, actual selected input loads, observable work,
native terminal commands/results and `stage-evidence`. Also return `review-result`,
whose SHA-256 hashes its **exact UTF-8 content**. That content is JSON with exactly:

```json
{
  "contract": "factory-independent-review-v1",
  "axis": "Standards",
  "adaptation": "<exact handoff.adaptation>",
  "pins": "<replace with the complete handoff.review.pins OBJECT>",
  "verdict": "pass",
  "findings": [],
  "test_assessment": "Requirement-based assessment of actual test changes and coverage, not only green checks.",
  "stuckness": null
}
```

`axis` is `Standards` or `Spec`, never combined. Verdicts:

- `pass`: ordinary result `status=done`, no findings, `stuckness=null`;
- `reject`: ordinary `status=done`, nonempty findings, `stuckness=null`;
- `stuck`: ordinary `status=stuck`; stuckness is an object with nonempty strings
  `attempts`, `evidence`, `uncertainty`, `recommendation`.

Every finding has exactly `id`, `kind`, `path`, `requirement`, `evidence`,
`correction`. IDs are unique within their axis. Kinds: `missing`, `partial`,
`incorrect`, `scope`, `standards`, `harmful-simplification`, `test-weakening`,
`gate-weakening`, `security-weakening`. Cite the original requirement/standard and
actual source evidence; provide an actionable correction. Passing tests do not
excuse missing behavior. Legitimate test changes are assessed against requirements,
not prohibited just because they modify tests. Gate/security weakening requires an
explicit operator path, even when both reviewers otherwise claim pass.

Wrong/malformed/duplicate fields, pins, axis or adaptation, false pass, missing
work/loads/checks or changed review source fail closed. Raw results remain in
attempt scratch for diagnosis. Only the correlated running launcher can submit the
initial report. Public `assignment-result` allows only exact completed replay,
never caller-authored replacement after a failed or stopped attempt.

## Aggregate and host authority

```sh
python -m factory_v1 --state /controller/state.sqlite --operator-id 42 \
  review-candidate --project product --iteration m1 \
  --request /controller/pair.json --api-base http://127.0.0.1:8000 \
  --review-config /controller/private-independent-verification.json
```

Pair request has exactly `standards_assignment` and `spec_assignment` (64-hex IDs).
Both axes must be completed, independently scoped, unchanged and correlated to the
same candidate. Results retain `axes.Standards` and `axes.Spec` separately;
findings are not reranked or replaced with one persuasive combined verdict.

**Worker-writable loads/events/conversation/results are untrusted.** Their internal
consistency is checked, but this is not authenticated execution proof. Without
host verification, two claimed passes return `status=execution-unverified`,
`execution_verified=false`, `advance_allowed=false`. `review_result` always retains
`native_execution_trusted=false`. A malicious worker can forge those scratch
files; they can never mint the authorization below.

The optional private authorization is analogous to #11's publication boundary:
operator/controller-owned regular file, exactly mode `0600`, no symlinks/hardlinks,
outside all workspace/profile/artifact mounts. Artifact envelopes must remain
private host-owned `0700`. It has exactly:

```json
{
  "operator_id": "42",
  "execution_reference": "Durable reference to independently checked Docker/model/native-execution evidence",
  "axes": ["<replace with exact Standards execution_binding OBJECT>", "<replace with exact Spec execution_binding OBJECT>"],
  "policy_decision": null
}
```

`factory_v1.review.execution_binding(axis)` returns the exact **binding**, not an
approval. Its fields: `assignment_id`, `run_id`, `handoff_digest`, `pins`, `axis`,
`report_sha256`, `skills` (name/source/hash/dependencies), `adaptation_sha256`,
`loads_sha256`, `work_sha256`, `evidence`, `model_evidence`. `evidence` hashes
scratch result/loads/events/conversation/probes and private host
container-inspection/launch-command/controller-checks/source-manifest files plus
immutable staged worker/relay/assignment/handoff inputs. `model_evidence`
hashes all private admitted request/response records and requires a response.

**Only compute/sign off this config after independently verifying the execution.**
Hashing worker logs, calling the helper or finding a model response is not that
verification. The host/operator is trusted to check the actual reviewed container
launch/read-only mounts, admitted native model inputs/responses and tool/load/work
records against the immutable inputs. `execution_reference` must point to that
real assessment. There is no universal native attestation capability here; owner
filesystem permissions and trusted host/operator verification are the narrow
boundary. Do not claim model execution for labeled test doubles.

The controller verifies all binding hashes and pins before persisting the exact
host authorization on both axes. Subsequent `review-candidate` calls without
`--review-config` revalidate the saved authorization/evidence, supporting fresh-
process restart readback. Changes invalidate it; stopped/paused attempts cannot
advance. Only two passes plus exact host verification and cleared policy holds
return `status=accepted`, `advance_allowed=true`. This means **review seam ready**,
not delivery: `merge_allowed=false`, `close_allowed=false` always.

### Policy holds

Changes to deterministic policy paths reuse `publication.policy_path` (CI,
security, hooks, protection-related/configuration files). They are conservatively
held even when they might be legitimate. Ordinary test file edits are not blanket
holds. Semantic gate/security weakening in any source is reported by either axis.

To clear a hold, `policy_decision` must be exactly an object with nonempty
`reference`, `paths` equal to the sorted aggregate policy path list, and `findings`
equal to the sorted `Axis:ID` gate/security finding list. Wrong/missing/superset
coverage does not clear the hold. This is an explicit operator policy decision,
not automatic gate relaxation. It **never erases rejection findings**; a rejected
candidate still requires corrections and both new axes. No protection or check
configuration is changed by this command.

## Corrections, stuckness, bounds

`prepare-corrections` takes exactly the pair IDs plus `profile`, `skills` (the
selected `implement` closure), `claim_id`; supports `--dry-run`. It assembles exact
axis-tagged actionable feedback, not caller prose, with the same original
requirements/standards/baseline and the rejected source. Corrections execute
`implement`, preserve the original check commands and must then rerun checks,
simplification and **both fresh axes**. Completed corrections supersede the old
review cycle even for an unchanged source manifest. Preparing arbitrary generic
corrections cannot bypass the same feedback contract.

Policy-held or agent-stuck work refuses ordinary correction preparation.
Agent-declared reviewer stuckness returns `status=diagnosis`, retaining its explicit
report. A stuck corrective attempt retains its complete correlated result/runtime
under aggregate `debug_evidence`, requires diagnosis, and cannot be mistaken for a
new qualifying implementation. Separate debug investigation remains the existing
read-only `diagnosis` role/`diagnosing-bugs` stage; this seam retains its inputs but
does not invent a debug report or operator direction.

Each attempt remains bounded by existing time/call/resource and durable allowance
limits. Replays do not reset attempts or budgets; a new attempt needs a new claim
and profile, and spending is rechecked at the model operation. Infrastructure
exhaustion never declares engineering stuckness or allows deficient acceptance.
There is no arbitrary fixed engineering retry count. Pause/stop/reconciliation are
the existing cooperative deterministic lifecycle; restart never resumes a paused
iteration merely because it can read old reports.

## Verification and live acceptance ownership

Run targeted public-path deterministic tests:

```sh
mise exec -- python -m unittest factory_v1.tests.test_review -v
mise exec -- python -m compileall -q factory_v1
```

Tests label Docker/model/tracker doubles explicitly. They exercise real CLI
processes, local tracker/UDS admission, durable readback, isolated profile claims,
corrective iterations, stale evidence, malformed outputs, policy/test weakening,
stuckness and fail-closed host authorization. They are **not** live isolation,
selected native skill execution, GitHub, CI/CD, spending or delivery proof.
The parent performs the final full suite, fresh simplifier, independent code
reviews, actual Docker/model acceptance and authorized remote delivery.

For the negative live REVIEW case, a labeled deterministic precursor may be
created through public implementation/simplification lifecycle with original
fixture requirements and green checks, then reviewed by real isolated workers.
Never inject SQL completion rows, construct private receipts manually or mutate a
completed artifact to introduce the omission. Fixture source/spec/ticket/standards
must be chosen **before** those public launches and explicitly labeled. Do not
reuse the default French-note tracker fixtures as greeting requirements. This
proves real independent REVIEW acceptance/rejection, not native precursor work or
the whole end-to-end factory. A genuine simplifier returning unresolved behavior
findings correctly blocks normal review; do not bypass it for the demo.
