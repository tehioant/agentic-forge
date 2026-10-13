# Material requirement changes (#23)

This is a **trusted host/controller lifecycle**, not a worker API. The conversational
integration must authenticate Antoine's actual message and exact origin before supplying
`direction`; an actor ID or reference string is not proof. No worker, model allowance,
GitHub capability, source merge or production action is authorized by these commands alone.

## Lifecycle

All five commands take `--project`, `--iteration`, `--request`, `--api-base` and the
existing optional `--bearer`/`--timeout` flags. Use the onboarded narrow capability.
Every operation uses the existing state lock and durable SQLite record. `inspect` exposes
`requirement_changes`, `requirement_authority`, retirement history and validity receipts.

1. **`record-requirement-change`** records authenticated direction, explicit requirements,
   affected issue identities/bodies and exact old revision pins. Material changes include
   all transitive dependents from the authoritative native blocking graph. The hold and
   evidence invalidation commit atomically **before** board changes. Open or delivered
   affected issues become open/Blocked with exact readbacks. Obsolete assignments,
   candidate/skill/review/check/completion evidence remain historical, never admissible.
   Only terminal, confirmed-cleaned old attempts release their claims; live/unconfirmed
   ownership is not stolen. Independent work retains its old authority only after explicit
   requirement, issue, installed-skill, candidate, result and check/review validity checks.
2. **`resolve-requirement-change`** binds Antoine's agreed direction to every held old
   revision. Silence, a recommendation or a worker's replacement choice cannot resolve it.
3. **`resynthesize-requirements`** invokes the separately approved, byte-pinned **host
   synthesis capability** for `to-spec`, then `to-tickets` automatically. It uses the
   existing planning source resolver, selected ticket skill and executing-context
   load/work/result contracts, not an externally prepared batch. Input includes original
   documents and ticket bodies, the agreed direction, affected requirements/identities and
   blocking graph. Only these tickets are synthesized; identities and dependency graph
   stay fixed. Expanding/changing that graph requires another explicitly resolved scope.
4. **`publish-requirement-change`** accepts only `change_id`, revalidates the actual invoked
   synthesis outputs/installed sources, and publishes versioned documents under
   `docs/factory/<iteration>/revisions/<change_id>/`. Controlled Git object writes and the
   fixed `factory/revision/<iteration>/<change_id>` ref never modify main. A separate open
   `ready-for-agent` specification issue contains the exact milestone bytes; the old spec
   and parent stay unchanged. Exact affected issues keep their identities, labels,
   memberships, scope and native dependencies while title/behavior/references/acceptance
   are updated. Other body sections/identity markers stay intact. Immutable document
   bytes/blob identities, specification and ticket bodies, dependencies and board scope
   read back before publication completes. Publication **does not resume** work.
5. **`resume-requirement-change`** requires a fresh explicit operator direction and exact
   `new_revision`. It rereads publication and unchanged evidence, refuses live/unconfirmed
   old workers, restores previously Ready/Active work to Ready, preserves independent
   Blocked/Deferred progress, and retains the local hold throughout partial restoration.
   Revised delivered work reopens but stays Blocked: old completion cannot restore Done
   or silently authorize fresh dispatch. Independently changed progress refuses release.
   Retries reconcile exact effects; only a release whose publication is reverified
   **after** restoration becomes `resumed`. Fresh workers receive current scoped inputs,
   original/new pins, agreed direction and explicit resume context as staged load evidence.
   All ordinary implementation, simplification, independent review, publication, merge,
   integrated-check and delivery gates still apply.
   Local diagnosis holds retain underlying progress rather than snapshotting another
   hold's Blocked status; only the explicitly correlated revise hold can release here.

A hold is cooperative at existing execution/admission boundaries: it denies launch,
model admission, results, review, candidate publication and merge for obsolete assignments.
The existing sandbox controller detects stale/held assignments and cleans up; explicit
`stop-assignment`/`reconcile-assignment` remain available through raw ownership lookup.
It is not #21's separate iteration-wide pause/emergency-stop implementation, and does not
provide an interruption lane independent of the ordinary controller lock.

## Requests

Read the old per-ticket pins from `requirement_authority[NUMBER]`, or, for unchanged
original work, `{spec: handoff, ticket_assignment: ticket_work.assignment_id,
ticket_inputs: ticket_work.input_digest}`. Preserve identifiers exactly.

```json
{
  "change_id": "durable-notes",
  "kind": "material",
  "direction": {
    "operator_id": "<configured Antoine identity>",
    "origin": {"<exact registered origin fields>": "<exact registered values>"},
    "reference": "<authenticated operator message>",
    "content": "<actual direction; no agent-invented product decision>"
  },
  "requirements": ["US-1"],
  "issues": [10],
  "old_revision": {"10": {"spec": "<exact handoff object>", "ticket_assignment": "<pin>", "ticket_inputs": "<pin>"}}
}
```

These are schema illustrations, **not admissible fixture identities or authorization**.
Resolution has exactly `change_id`, `direction`, `old_revision` (including the expanded
held dependents). Synthesis has exactly `change_id`, `skills` (the existing three planning
source descriptors) and additionally requires `--synthesis-config`. Publication has only
`change_id`. Resume has exactly `change_id`, `direction`, `new_revision`.

An optional `stuck_assignment` on the material change explicitly links #16's existing
`revision-required` disposition. The record revalidates its completed diagnosis, selected
skill and independent host authorization, exact attention decision and actual Antoine
`revise` response. The supplied direction/reference must match that response. Only this
linked hold can release after versioned synthesis and explicit resume; unrelated
stuckness/policy/incident holds remain authoritative. Resolution cannot implicitly replace
that approved direction. Overlapping material revision holds are refused rather than
silently combined.

## Installed-skill execution capability

A private owner-only, single-link config outside every worker mount contains exactly:

```json
{
  "operator_id": "<configured identity>",
  "reference": "<approved exact capability scope>",
  "execution_reference": "<independent verification of the adapter's actual conversational execution/provenance>",
  "argv": ["/absolute/path/to/reviewed-host-synthesis-adapter"],
  "sha256": "<approved executable bytes>",
  "timeout": 300
}
```

The executable is operator-owned, not group/other-writable, byte-pinned and outside
worker mounts. Only one executable is allowed, not an interpreter plus an unpinned
script argument. It receives the correlated stage assignment as stdin JSON and emits
exactly `{result, execution}` as bounded stdout JSON. No broad credential/env copy or
shell interpolation is introduced. The adapter is a **trusted execution capability**,
not an untrusted engineering worker: deploying it requires independent verification that
it invokes the actual selected installed skill in the executing conversational context,
retains authoritative native load/work provenance, and enforces the existing approved
model/spending boundary. A config string, executable exit or forged transcript cannot
supply that trust. There is **no bundled production adapter or default model fallback**.

- `to-spec.result`: the three actual Markdown document snapshots, using the existing
  seven-section milestone template, vision/milestone separation and provenance record.
- `to-tickets.result`: only affected tickets, each with `number`, `title`,
  `desired_behavior`, `references`, `acceptance_criteria`, `blockers`. Synthesis references
  use the provided `revision-document:<change_id>:milestone.md` source; publication replaces
  those with the exact immutable URL. Blockers retain their original numeric graph.
- `execution`: the existing `run_id`, `loads`, `decomposition`, `result_digest` contract.
  Loads cover the actual selected skill source, document hashes, original document sources,
  exact agreed direction, affected ticket/requirement/dependency scope and stage adaptation;
  each needs executing-context tool references. Retain observable skill-guided work in
  `decomposition`, not merely a catalog or name. Source/dependency/adaptation pins and the
  complete executing assignment/result/evidence are persisted per stage. The selected
  `to-spec` and `to-tickets` snapshots have no transitive dependencies; the planning resolver
  still verifies the original `grill-me`/`grilling` closure without rerunning the interview.

The CLI exposes **no manual synthesis-artifact completion command** for revisions. A
missing/unapproved/changed adapter or skill refuses before execution. An admitted failed,
malformed or interrupted synthesis attempt remains uncertain and cannot automatically
retry, reset allowance, infer success or publish. Restore/reconcile the exact trusted
capability/attempt before any separately authorized repair; this slice does not invent an
uncertain-execution retry policy. Publication intents precede ref/spec/ticket writes;
lost responses reconcile exact readbacks, and absent effects stay uncertain rather than
blindly creating duplicates. Unknown commit outcomes likewise require investigation.

## Minor clarification and merge safety

`kind: minor` records the authenticated clarification and explicit validity receipts
without changing authoritative specs/tickets, statuses or existing revision pins. It is a
traceable annotation, **not** permission to rewrite requirements or bless stale evidence.
Already edited ticket/spec bytes or changed review/check/candidate bytes refuse retention;
use the material lifecycle for changed authoritative inputs.

Independent tickets retain their original immutable spec issue/documents. Revised tickets
have scoped authority rather than replacing a global spec pin and invalidating unrelated
work. The public frontier revalidates the appropriate scoped publication. Existing stale
assignment/issue/head/baseline/skill/review/check guards are reused. Controlled merge
rechecks scoped requirement publication and exact work issue at the last mutation boundary,
after protection/check/health reads and before durable intent/merge. A scheduled or formerly
approved candidate cannot cross a material hold or use old revisions after resumption.
Global planning reassignment cannot bypass these scoped holds.

Material revisions also retire affected discovered-bug synthesis. Discovery/diagnosis,
old synthesis evidence and uncertain publication intents remain inspectable; a replay
creates a fresh selected-skill assignment rather than accepting obsolete completion.
Each affected issue contributes its current scoped inputs and revision pins to the load
contract. The bug's own references/authority use the first affected issue's specification;
downstream body updates preserve each affected issue's own references. Independent bugs
keep their authority. Retirement invalidates assignment/result IDs, not the underlying
current discovery: the frontier retains its affected scope (including unchanged siblings)
and any known bug identity across both `bug_work` and `bug_work_history`. Requirement-only
resume cannot settle it. A later fully read-back publication for the same normalized
discovery and every affected issue settles discovery-only predecessors; native blockers
then govern ordinary eligibility. That known settlement remains historical if its execution
is later retired, but cannot settle a newer pending discovery. Unverified issue, edge,
membership, field or affected-body intents remain held independently even when fresh
synthesis adopts a duplicate successfully. Retired uncertain creation still denies blind
duplicate writes and requires correlated investigation; retirement or a replacement
assignment is not reconciliation. This slice adds no uncertainty override/discard command.

## Verification and remaining acceptance

`factory_v1.tests.test_requirements` and `test_requirement_merge` use real public CLI
processes, SQLite and HTTP adapters, **SIMULATED** tracker/native precursors and a
clearly labeled deterministic executable fixture. They cover transitive holds/claim
retirement, independent work/evidence retention, actual adapter invocation sequencing,
manual-artifact and missing-load refusal, source drift, controlled version/readback,
explicit fresh-context resume, lost ticket responses, partial release, old unconfirmed
workers, minor validity, correlated #16 revise handoff, revisions before/after review and
at final merge admission. Existing pinned-document/spec-issue final-boundary regressions
and ordinary delivery regression are retained, not replaced.

These tests prove deterministic lifecycle/contracts, **not native selected-skill work,
authenticated user provenance, real GitHub writes, Docker interruption or live gated
merge**. Those require a separately approved real synthesis adapter/conversational
integration, exact installed-skill source review, allowed native capacity and live
capability campaign. None was deployed or run for this ticket. Full-suite and independent
simplifier/Standards/Spec review evidence belong to the parent stabilization workflow.
