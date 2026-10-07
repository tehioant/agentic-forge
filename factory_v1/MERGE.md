# Gated autonomous merge and integrated delivery (#17)

Standard-library Python 3.11+, in the existing `factory_v1` controller. This extends
[#13 independent review](../docs/fresh-independent-review.md) and
[#14 publication](PUBLICATION.md), not a second worker/scheduler framework. Scope:
fresh specification and interview pinned at
`cccb21e35cc989a1ecd5a3c88f0689a694d77d9c`.

## Public lifecycle

Run from the repository root, through the trusted host controller only. Set the
variables below to actual registered IDs and private paths; examples are not evidence.
The same existing `.onboarding.lock` serializes admission, publication, pause/revision
operations and reconciliation. Missing state/lock does not create replacement state.

```sh
mise exec -- python -m factory_v1 --state "$FACTORY_STATE" --operator-id "$FACTORY_OPERATOR" \
  merge-preflight --project "$FACTORY_PROJECT" --iteration "$FACTORY_ITERATION" \
  --merge-config "$FACTORY_MERGE_CONFIG"

mise exec -- python -m factory_v1 --state "$FACTORY_STATE" --operator-id "$FACTORY_OPERATOR" \
  merge-candidate --project "$FACTORY_PROJECT" --iteration "$FACTORY_ITERATION" \
  --merge-config "$FACTORY_MERGE_CONFIG"

mise exec -- python -m factory_v1 --state "$FACTORY_STATE" --operator-id "$FACTORY_OPERATOR" \
  verify-delivery --project "$FACTORY_PROJECT" --iteration "$FACTORY_ITERATION" \
  --merge-config "$FACTORY_MERGE_CONFIG"

mise exec -- python -m factory_v1 --state "$FACTORY_STATE" --operator-id "$FACTORY_OPERATOR" \
  inspect-merge --project "$FACTORY_PROJECT" --iteration "$FACTORY_ITERATION" \
  --assignment "$FACTORY_CANDIDATE_ASSIGNMENT"
```

`merge-preflight` is read-only: protection capability/pin verification, **not merge
admission**. `merge-candidate` mutates only the fixed PR using exact head SHA and
`merge_method=merge`. It returns `merged`, never delivery based on branch checks.
`verify-delivery` reads actual integration and checks before closing the exact issue.
It also reopens premature automatic/manual closures when delivery is incomplete.
Inspection is read-only and retains merge/refusal evidence even after a material
revision makes ordinary `inspect-assignment` refuse stale work. JSON success exits 0;
structured refusal exits 2. Bearer authentication is never persisted or printed.

## Deployment authority, not per-merge human approval

Workers cannot install configs, access controller state/authentication, call merge
or close issues. Keep state, backups, configs and the existing lock in a private,
controller-owned directory outside **every** assignment workspace, profile home and
artifact envelope. State/configs must be owner-owned, mode `0600`, regular physical
files, without symlinks/hardlinks. Execution envelopes must be private mode `0700`.
The API endpoint must equal the existing onboarding and stage capability endpoint;
only `https://api.github.com` and explicitly labeled loopback HTTP fixtures are supported.

The trusted execution verifier installs an exact binding config after independently
resolving the actual host/container/native model execution records. This can be an
automatic trusted-host step; **there is no blanket human merge approval**, approval
checkbox, administrator bypass, force update or protection change. Policy changes
still require their existing explicit scoped operator decision.

As in #13, scratch conversations/loads/events and worker reports remain untrusted.
`merge.execution_binding(assignment)` fingerprints retained records; computing it,
a catalog entry, `status=done`, model response presence or a worker-authored report
is **not attestation**. Deployment authority must resolve actual controller launch,
container removal/mounts, admitted model/native tool inputs/responses, real selected
instruction loads, substantive skill-guided work and check runs before installing
bindings. Do not manufacture native provenance or install a config solely from
worker assertions. Existing `native_execution_trusted=false` flags are not promoted.
There is no universal native-signature verifier or new credential bridge in this slice.

### Exact config fields

The private JSON has **exactly** these fields (no optional omission):

- `operator_id`: the already authenticated configured operator ID.
- `execution_reference`: durable independent host execution assessment, not a worker assertion.
- `executions`: four exact `merge.execution_binding` objects, ordered implementation/
  corrections/repair, simplification, Standards, Spec. Each binds assignment/claim/
  handoff/revision/scope/runtime, entire submitted result, selected source/hash/
  dependency closure, adaptation hash, retained execution files and admitted model
  evidence hashes. The review pair also needs its unchanged saved #13 authorization.
- `candidate_assignment`, `standards_assignment`, `spec_assignment`: exact 64-hex IDs.
  The candidate is a completed simplified artifact, not an arbitrary implementation result.
- `repository`, `repository_id`: configured name and immutable positive GitHub ID.
- `base`, `branch`: fixed default/main integration branch and separate candidate branch.
  `base` may be a product's explicitly configured default (validation uses
  `fresh-spec-validation`); a similarly named non-default branch is not main.
- `pr_number`, `issue`: exact positive fixed PR and work-item numbers.
- `head`, `baseline`: full lowercase 40-hex published candidate and integration baseline.
- `api_base`, `bearer`: existing pinned endpoint and host-only authentication.
- `required_checks`: nonempty array of exact `{"context": "<required name>", "app_id": <positive integer>}`
  objects, unique by name. Include all authoritative quality/security checks; not worker-selected tests.
- `protection_sha256`: `assignments.digest({"rules": effective_rules_response,
  "protection": classic_protection_response})`, installed from independently resolved
  actual readbacks. Changing policy invalidates admission, never silently repins it.
- `recovery`: `null` ordinarily, or exactly `{"incident_id": "<ID>",
  "implementation_assignment": "<64-hex repair ID>", "issue": <work-item number>}`.

Do not print config contents or auth headers. Use exclusive owner-only file creation
in the private controller directory. Bindings are installed by deployment authority,
not accepted through the public worker result interface.

## Admission and Git mapping

Both independent axes must pass the same exact candidate with no findings or policy
holds. All stages must retain completed/removed-container runtimes and actual private
controller checks. Admission revalidates selected source bytes/dependencies, adaptation,
result identities, instruction loads, work, source/baseline/check receipts and native
execution bindings. Changed evidence, cancelled runs, missing axes, generic role results
or superseded corrective work cannot qualify. Meaningful testing remains mandatory;
TDD ordering is not added as a new requirement.

Review's artifact manifest is not itself a Git commit. The verifier reuses #14's
bounded source assembly: every regular baseline file matches repository blob bytes
and mode; every candidate file's bytes/executable mode match the reviewed manifest;
opaque unchanged baseline entries are preserved. It recomputes the exact Git tree
and deterministic published commit, verifies its parent/tree/message and fixed PR
association. No worker Git/config/hooks are executed on the host.

Immediately before mutation, re-read repository/default-main identity, fixed PR/head,
issue identity/body, immutable spec documents and specification issue, revision,
installed inputs/native chain, current independent acceptance, protection, exact
app-bound branch checks and baseline. Main must still equal the reviewed baseline.
PR must be non-draft, open, unmerged, mergeable and `mergeable_state=clean`.
#14 intentionally publishes a **draft**; making it ready through an independently
scoped trusted path remains a prerequisite, not permission for this CLI to mutate readiness.

Ordinary candidates also require passing required checks on baseline main. Failed
or unknown main checks refuse ordinary admission. A durable iteration `main_incident`
freezes ordinary merges. Its exact trusted shape is `status=active`, `incident_id`,
`recovery_assignment`, `recovery_issue`; only the config-bound **repair-stage** builder
for that same incident/work item can use the exception. A caller boolean or ordinary
implementation using repair-looking prose does not qualify. Repair still needs the
same simplification, both independent review axes, protection and candidate/integrated
checks. This operation neither clears the incident nor declares deployment healthy.

### Supported protection/check capabilities

Both `GET /rules/branches/{base}` and `GET /branches/{base}/protection` must be readable.
Classic protection must enforce administrators, disallow force pushes/deletion, and
require strict app-bound checks. Effective rules may additionally be required status
checks, pull requests, non-fast-forward or deletion rules. Other rule types (including
merge queues) fail closed until a supported integration path exists. Configured checks
must equal the effective authoritative set, with exact positive app IDs. Missing,
inaccessible, changed, weak or ambiguous protection is a prerequisite failure, not an
invitation to purchase a plan, publicize a repo or bypass a gate.

Only completed `success` check runs qualify; skipped/neutral/pending/unknown/failed do
not. Readback uses `filter=latest`; duplicate matching required runs, missing apps,
wrong SHA, incomplete pagination or 100+ returned runs fail closed. This bounded first
slice does not accept unbound legacy commit statuses as authoritative checks.

## Durable reconciliation and delivery

Intent and gate evidence commit **before** merge IO. Any response is followed by exact
PR merged-state/merge-SHA readback, then actual merge commit/tree/parents and ancestry
membership in main. Merge parents must be the baseline and published candidate;
integrated tree must equal the reviewed artifact's mapped Git tree.

Lost responses reconcile that same PR; there is never a blind second PUT. If the PR
remains open after a persisted intent, `merge_uncertain` holds the attempt for trusted
investigation (including a crash between intent and send). The CLI deliberately does
not expose a reset/retry boolean for ambiguous mutations. Restart can reconcile an
already completed merge while paused, but cannot admit a new merge or silently resume.

Delivery requires required checks on the **actual integrated SHA**, unchanged current
requirements/review/execution pins, and verified main membership. Current main must
still have that exact reviewed tree; later changed code or a revert conservatively
requires delivery revalidation rather than relying on ancestry. Branch-only green
checks cannot close work. Verified integration/check receipts commit before closure;
lost closure responses read back the same issue before retry. Failure/refusal is durable
before corrective reopening IO. Changed requirements or failing integrated checks
reopen previously closed incomplete work rather than accepting GitHub closure as proof.

Every journal includes candidate/integrated/tree/main/parent membership evidence,
required branch/integrated check IDs/apps/SHAs, exact authority, protection and failures.
`iteration_complete` and `deployment_verified` remain **false**. Ticket delivery does
not manufacture CI/CD deployment observations or an iteration-completion report.
External recovery/health observation and iteration scheduling remain separate work.

## Tests and live prerequisite status

```sh
mise exec -- python -m unittest factory_v1.tests.test_merge -v
mise exec -- python -m unittest factory_v1.tests.test_assignments.AssignmentTests.test_repair_selected_closure_order_keeps_one_claim_on_exact_replay -v
mise exec -- python -m compileall -q factory_v1/merge.py factory_v1/__main__.py factory_v1/tests/test_merge.py
```

Tests exercise public CLI stages through fresh processes, real loopback HTTP and durable
SQLite, including concurrent admission, lost responses, independent axes, scoped repair,
pause/revision, wrong scope/stale head/baseline, inaccessible protection, failed/unknown/
branch-only checks, exact integration and premature closure. **Docker/model/GitHub
responses and host authority are labeled simulations; none prove live acceptance.**

Parent's actual read-only preflight on 2026-10-07 found the authorized private repository
`tehioant/agentic-forge-fresh-v1-validation`, default/base `fresh-spec-validation`, but
**both** effective-rules and classic-protection endpoints returned HTTP 403:
“Upgrade to GitHub Pro or make this repository public to enable this feature.”
Actual retained evidence is
`/home/ops/.hermes/fresh-forge-evidence/issue17/live-prerequisites.json` (outside worker mounts).
PR #3 remains an open draft on `factory/issue14-publication-acceptance`, head
`f378806276f3687cf092a38337ae9c9aa6a062db`: publication, **not delivery**.

Runnable read-only prerequisite checks, with already approved host authentication:

```sh
mise exec -- gh api repos/tehioant/agentic-forge-fresh-v1-validation/rules/branches/fresh-spec-validation
mise exec -- gh api repos/tehioant/agentic-forge-fresh-v1-validation/branches/fresh-spec-validation/protection
```

No upgrade/spending, visibility change, policy weakening, live merge or live issue
closure is authorized. Live gated merge and integrated-check readback therefore remain
**blocked**, not accepted through fixture evidence. After the prerequisite is resolved
through an explicit authorized decision, the parent must exercise the complete real
skill-guided chain, actual protections/quality/security gates, fixed candidate mapping,
live merge/membership and integrated checks in the disposable repository, retaining exact
readbacks and genuine refusal cases. Deployment observations are still needed for
iteration acceptance. This implementation worker performed no live mutations.
