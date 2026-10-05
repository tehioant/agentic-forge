# Fresh simplification stage (#12)

This implements only the implementation/testing → fresh simplification seam in
`factory_v1`. It uses #11's whole-process launcher and its controller-owned
read-only check runner. It does not add scheduling, review, integration, host Git
writes or worker approval authority. Requirements are pinned to issue #12 and
[`cccb21e35cc989a1ecd5a3c88f0689a694d77d9c`](https://github.com/tehioant/agentic-forge/blob/cccb21e35cc989a1ecd5a3c88f0689a694d77d9c/docs/specs/fresh-agentic-factory.md)
and the companion interview decisions at that revision.

## Preparation and production boundary

The trusted controller invokes:

```sh
python -m factory_v1 --state /controller/state.sqlite --operator-id 42 \
  prepare-simplification --project product --iteration m1 \
  --request /controller/simplifier.json --api-base http://127.0.0.1:8000
```

The request has exactly these fields:

```json
{
  "implementation_assignment": "<64-hex completed implementation assignment ID>",
  "profile": {
    "name": "factory-simplifier-attempt-1",
    "home": "/controller/fresh-simplifier-home-1",
    "role": "simplification",
    "provider": "openai-codex",
    "model": "gpt-6.1-sol",
    "operation": "responses",
    "reasoning": "high"
  },
  "skills": [
    {
      "name": "simplify-code",
      "source": "/home/ops/.hermes/skills/software-development/simplify-code/SKILL.md",
      "path": "/controller/reviewed-inputs/simplify-code/SKILL.md",
      "sha256": "9ee977c3362a1c07f2e327198e066ddb3c0ed67709706fc410165ee820b2ca01",
      "dependencies": []
    }
  ],
  "claim_id": "simplify-attempt-1"
}
```

`path` must contain the exact reviewed installed bytes; it is not a worker host
mount. Revalidate the actual selected installed source before a live attempt.
The source/dependency pins and single-context adaptation remain those in
`role_skills.py`. No fan-out or nested acceptance review is introduced.

Preparation requires a completed implementation/correction/repair sandbox
assignment on the same registered iteration, `status=done`, confirmed container
removal and passing controller checks. Its private check receipt must match the
exact retained source tree and durable receipt/tree/baseline pins. Worker test
claims, arbitrary preceding prose and older outputs lacking these new pins do
not qualify. Run implementation with meaningful controller-admitted
`verification_commands`; no receipt may be manually manufactured to qualify an
old attempt.

The controller builds the handoff from the original issue/spec/standards, exact
Git baseline, sanitized tested implementation tree, path/content/mode diff,
preceding structured result and actual controller check outputs. It does not
inherit the implementation conversation. Generated evidence/diff JSON is formatted
across lines for the pinned native file tool. A long JSON string cannot itself be
split across physical lines, so inputs exceeding its 2,000-column limit use the
lossless `factory-json-chunks-v1` envelope: decode with
`json.loads(''.join(chunks))`. These are transport bytes, pinned before staging;
only precursor evidence uses the decoder, not worker result contracts. Startup
refuses `truncated_lines` as well as page truncation before inference. Exact
native-read consistency verification is unchanged.

`--dry-run` writes no claim or state;
exact replay preserves the original assignment. The generic
`prepare-assignment` path enforces the same precursor contract, not a bypass.

Launch/inspection/stop/reconcile use the existing public assignment commands.
The private launcher configuration **must retain the exact ordered implementation
verification commands**. Absence or substitution refuses before a new attempt
or model call. `/workspace` starts from the pinned sanitized implementation
artifact, not the original host Git HEAD; `/inputs/baseline` remains the pinned
original baseline. The worker still receives no Git metadata or host write
capability. Only paths changed by the implementation relative to its baseline
are admitted for cleanup; minimal surrounding edits within those files are
possible, edits to other files are findings. Checks and test caches must not
pollute the candidate: use scratch for outputs and disable bytecode generation
where appropriate.

## Result contract

In addition to the ordinary bounded result and `stage-evidence`, the worker
returns an artifact named `simplification-result`, with a SHA-256 over its exact
UTF-8 `content`. That content is JSON with **exactly**:

```json
{
  "contract": "factory-simplification-v1",
  "adaptation": "factory-simplify-v1:inline-reuse-quality-efficiency-altitude;preserve-behavior;evidence-backed-no-op",
  "input_candidate_sha256": "<handoff simplification.input_candidate_sha256>",
  "candidate_sha256": "<actual resulting tree digest>",
  "outcome": "no-op",
  "behavior_preservation": "preserved",
  "angles": {
    "reuse": "Concrete searches, existing utilities inspected and conclusion.",
    "quality": "Concrete source inspection and conclusion.",
    "efficiency": "Concrete work/waste inspection and conclusion.",
    "altitude": "Concrete surrounding-code inspection and conclusion; unavailable blame noted."
  },
  "findings": []
}
```

Allowed outcomes: `cleanup`, `no-op`, `findings`. Allowed behavior-preservation
values: `preserved`, `not-established`. Findings have exactly `kind`, `path`,
`evidence`, `proposal`; kinds are `behavior`, `out-of-scope`, `correctness`.
All four angle values and each finding's text fields must be nonempty. Keep
concrete skill-guided work and native read/terminal records, not only category
labels or a catalog entry. Native `tests.command` must match the complete
terminal invocation verbatim; `tests.result` is a nonempty actual-output/status
string. Preserve malformed/rejected raw output rather than normalizing it.

Only the trusted launcher may make the initial simplification submission, after
validating the native output and candidate checks for its correlated running
attempt. Public `assignment-result` permits only an exact replay of the already
admitted result after confirmed successful completion/removal. Failed, running,
stopped or unconfirmed attempts cannot accept caller-authored replacement
reports, even when private candidate checks passed. The internal launcher run
correlation is not a public request field or a general execution-attestation
capability; worker reports remain untrusted and delivery gates remain disabled.

The resulting revision is **not an invented Git commit**. It is SHA-256 over the
canonical source manifest, including file bytes and executable status:

```python
import hashlib, json
from pathlib import Path
root = Path('/workspace')
files = {}
for path in root.rglob('*'):
    if path.is_file():
        raw = path.read_bytes()
        files[str(path.relative_to(root))] = {
            'sha256': hashlib.sha256(raw).hexdigest(),
            'bytes': len(raw),
            'executable': bool(path.stat().st_mode & 0o111),
        }
print(hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False,
    separators=(',', ':'), allow_nan=False).encode()).hexdigest())
```

Use a native terminal tool to compute this; the controller independently
recomputes it and rejects links/devices/Git metadata. Baseline and initial Git
commit pins remain distinct from the resulting content-addressed artifact.
The handoff `candidate`/`preceding:candidate` identifies the implementation's
original Git snapshot; `simplification.input_candidate_sha256` identifies the
actual implemented source being simplified.

A no-op must leave the source manifest unchanged and include work at all four
angles plus tests. Cleanup must actually change only admitted paths, assert
preservation and include no unresolved findings. A finding is never approval:
its source must remain unchanged, so behavior-affecting, out-of-scope and
correctness proposals cannot be silently applied. Failing checks, malformed
JSON, duplicate evidence fields, wrong source/adaptation/revision pins, missing
angles, false no-op and scope violations cannot produce an admitted stage
result. Raw attempt evidence remains available for diagnosis/rework.

## What is verified, and what is still blocked

The controller reruns the exact implementation verification commands against the
resulting source in #11's read-only check container, without a model socket,
within the original attempt deadline. The private receipt pins the unchanged
candidate and command exit/output records. No code/check runs on the host.
The durable `simplification_result` retains the exact input/result digests,
changed paths, check verification, preservation **claim**, findings and pending
Standards/Spec axes. `runtime.candidate_sha256`, `baseline_sha256` and
`checks_sha256` bind successful sandbox artifacts for a later controller seam.
A failed/stopped/unconfirmed runtime cannot be reused as implementation.

**Worker-owned transcripts are not authenticated native execution proof.**
The existing harness really invokes Hermes input reads and the selected skill in
its fresh worker prompt, retaining source/dependency/adaptation identities,
conversation, read events and work. Consistency validation still cannot attest
that a malicious worker did not forge those writable files. This slice does not
invent attestation from them or equate passing checks to complete behavior
preservation. `native_execution_trusted=false`, `approval=false`,
`advance_allowed=false` and `close_allowed=false` remain authoritative. A valid
candidate is retained for independent review; no automatic review/delivery gate
is satisfied by this result alone. Independent requirement-based review must
also catch behavior changes or weakened tests that a passing command cannot
establish. A later native-execution provenance boundary must fail closed, not
promote an assertion or fixture into authenticated proof.

## Smallest live acceptance harness (parent/operator-owned)

1. Use an approved registered iteration and read-only fixture tracker route; a
   small local implementation ticket is sufficient. Preserve exact installed
   skill pins and approved subscription scope. No new paid route/fallback.
2. Launch one implementation via the existing whole-process launcher with a
   tiny behavior test as a private verification command; keep its actual
   controller-pinned completed artifact/receipt. Do not inject completion rows
   or manually construct the preceding stage's evidence.
3. Call `prepare-simplification`, then launch one fresh simplifier with the same
   checks. Choose an implementation allowing harmless cleanup or a justified
   no-op. Preserve Docker/guard cleanup, actual selected-instruction reads,
   four-angle work, native terminal commands, check receipt and exact source
   manifests. Read back the durable assignment in a new process.
4. Compare the before/after behavior and source. Keep the provenance limitation
   explicit; the parent performs separate isolated single-worker cleanup and
   independent Standards/Spec reviews of this implementation as requested.
   No worker verdict permits merge/closure. Live attestation, remote delivery
   and issue integration remain parent-owned work.

The implementing agent did not launch real model workers. Public CLI tests use
labeled Docker/model/tracker doubles and actual local HTTP/UDS/control processes;
they are not live isolation, native skill execution, GitHub, CI/CD or delivery
proof. Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest factory_v1.tests.test_simplification -v
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s factory_v1/tests -v
python -m compileall -q factory_v1
```
