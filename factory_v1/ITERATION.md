# Healthy finite iterations and the next interview

`complete-iteration` is the public, read-only-external completion verifier. It never
merges, closes an issue, starts workers, synthesizes work or sends a message itself.
Run it with the trusted controller's persistent state and operator identity:

```sh
python -m factory_v1 --state /private/factory.sqlite --operator-id 42 \
  complete-iteration --project product --iteration m1 \
  --api-base https://api.github.com --request /private/complete.json
```

`complete.json` contains only `{"merge_configs":["/private/issue1-merge.json"]}`.
Supply one existing private [merge configuration](MERGE.md) for **every required
milestone issue**, not a caller-chosen completed subset. It retains the established
execution/review authority and repository-scoped read route. Completion uses that
route only for reads; normal publication/merge/closure commands remain separate.

## Admission and durable result

- Read the authoritative linked board, issue identities/bodies, milestone scope,
  native/textual blockers and current progress through the existing frontier.
  Original published slices cannot disappear from scope. Newly discovered current
  bugs are required too; explicitly deferred, non-blocking bugs stay non-executable.
- Refuse open/reopened, blocked, invalid, unmerged or incompletely published work,
  live/unreconciled workers, requirement/diagnosis/discovery holds and required
  operator decisions. GitHub issue closure alone supplies no delivery proof.
- Revalidate original implementation, simplification, both independent review axes,
  source/dependency/adaptation pins, host execution authority, candidate bytes,
  publication, exact PR/merge parents/tree, main ancestry and integrated check runs.
  A saved `delivered` flag is necessary but never sufficient. Required acceptance is
  established by the independent Spec axis and the configured main acceptance
  contract, not an implementer's summary or branch checks.
- Require the existing pinned [delivery monitor](DELIVERY.md), no unresolved main
  incident and **fresh actual observations** of configured required CI/CD,
  deployment identity/status and the product-agreed health/acceptance response.
  Missing observations remain unknown. Test fixtures cannot clear real incidents.
  Older ticket merge trees need not equal today's cumulative main tree: their
  exact merge identity must remain an ancestor and current configured acceptance
  must be healthy. A removed requirement cannot be justified by ancestry alone.
- Recheck external evidence after health reads, then compare the exact durable
  iteration under `BEGIN IMMEDIATE` before admitting completion. The caller holds
  the shared controller lock throughout. A new local pause/hold cannot be replaced
  by the earlier snapshot. Report, exact-origin attention intent, archived reservation
  and `waiting-for-operator` state commit atomically. Completion and next-interview
  admission use the short [interruption lane](CONTROLS.md) through their local
  save/commit, never across external reads. Pause/stop sidecars are checked against
  the real persistent state even when tracker observations use a disposable copy.
  Explicit resume preserves an already-completed wait; it does not restart work.

Inspect the normal `inspect` output's `completion.report`: issue/PR URLs, candidate
and integrated commits/tree/parents, per-item requirements/reviews/checks, current
CI/CD/deployment/acceptance state, current and archived bug discoveries, deferred
issues and pending future decisions. A deterministic report digest is its identity.
Loopback external capabilities produce an explicit `simulation` report, not live
verification. The report is immutable; changed evidence is a refusal, not a second
success notification. Identical retries reuse the same outbox event without sending.

Use the existing `deliver`, `notifications` and `attention-reconcile` lifecycle to
send and **read back the exact originating thread/message**. Accepted/uncertain
sends are not verified delivery and are never blindly retried. The invitation
contains the report identity and delivered issue/PR/commit/deployment summary; the
full report stays in durable state. Its delivery is not operator approval.

## Only later explicit input admits the same product's next interview

```sh
python -m factory_v1 --state /private/factory.sqlite --operator-id 42 \
  next-iteration --project product --iteration m1 --request /private/next.json
```

The trusted conversation adapter supplies only:

```json
{
  "iteration_id": "m2",
  "event_id": "completion-<report-sha256>",
  "operator_id": "42",
  "origin": {"platform":"discord","chat_id":"123","thread_id":"123","parent_chat_id":"122","scope_id":"121"},
  "reference": "authenticated-originating-message-reference",
  "message_id": "a-real-discord-snowflake-later-than-the-delivered-report",
  "input": "Antoine's actual next-iteration input",
  "skills": [
    {"name":"grill-me","path":"/home/ops/.hermes/skills/grill-me/SKILL.md","sha256":"caaf8b8de1684f96e26b28f3c29189db5c89cce4b73e1c93d86164f66ef88637"},
    {"name":"grilling","path":"/home/ops/.hermes/skills/grilling/SKILL.md","sha256":"b4846894c01ea1b216019c5ab83c4d9ab286ff6f5faf78e1c7edc965a365489e"},
    {"name":"to-spec","path":"/home/ops/.hermes/skills/to-spec/SKILL.md","sha256":"f3baf5f84dd2e47cb000bdb67a2794e714a59826acb50514b2aa80e802e525a9"}
  ]
}
```

The message ID above is a descriptive placeholder, intentionally not an admissible
identifier. The adapter must authenticate actual user input; workers cannot assert
it. Its numeric Discord message ID must be later than the verified report's message
ID. A different actor/origin, stale invitation, silence or delivery acknowledgement
cannot start the interview. No other iteration may already be active. A duplicate
identical request is idempotent; conflicting input cannot silently replace it.

The new record preserves project/repository/origin/onboarding identities and the
previous completed report/spec pointers. It starts only `planning_pending`, with
no new handoff, tickets, reservation, model operation or worker. Deferred bugs remain
in the preceding report for discussion, not auto-promoted into executable work.
Ordinary `plan`, `complete-planning` on the old iteration, or a fresh `register` for
the waiting product cannot bypass this boundary. A transient, unregistered main
incident and its monitor carry forward without clearing the freeze. A registered
recovery must instead finish its exact owning-iteration clearance before the next
interview: copying only its incident pointer would strand the host authority,
assignments and affected-ticket receipts. Pending registration also refuses
completion even if its incident pointer is missing. Terminal verified old engineering
claims are released; their full assignment/evidence rows remain inspectable.

### Selected installed sources and execution contract

Next interviews pin the explicitly selected local Hermes sources above (paths are
relative to the controller account's home). They do not accept arbitrary identical
copies, symlink aliases, caller repins or ambient duplicate bare names. Legacy
first-interview snapshots remain unchanged. Test snapshots are independent frozen
fixtures, never an installed-source fallback. The applicable dependency closure is
`grill-me -> grilling`; `to-spec` has no required skill dependency. Grilling's
conditional career-profile reference is inapplicable to this same software product.

The versioned adaptation is `factory-next-interview-v1:same-product;explicit-input;preserve-round-gates;automatic-synthesis;host-publication`.
It preserves frontier rounds, numbered recommendations, actual operator answers and
explicit round-completion gates. It adapts only routine post-grilling spec/batch
approval to the agreed automatic handoff and keeps controlled publication host-side.

`complete-planning` on a continuation additionally requires `execution` with exactly:

- `assignment_id`, `run_id`, exact `origin`, `adaptation`;
- `loads`: `{source, sha256, tool_reference}` for selected instruction paths
  (`grill-me`, `grilling`, plus `to-spec` when sufficient), previous specification
  `continuation.input_loads`, `planning:input` (canonical request digest) and
  `planning:adaptation` (UTF-8 adaptation SHA-256);
- `rounds`: `{reference, complete, questions}`. Each actual question has
  `{number, question, recommendation, answer, operator_id, reference}`. Pending
  unanswered questions use null answer/actor/reference. Sufficient completion
  requires actual operator answers and completed round gates;
- `work`: ordered `{skill, tool_reference, result}` entries for `grill-me`,
  `grilling`, and, only on sufficient completion, `to-spec`;
- `result_digest`: canonical digest of `documents` for sufficient completion or
  `pending_questions` otherwise (same JSON digest as `assignments.digest`).

A catalog, successful exit or externally supplied docs without correlated loads
and work is refused. These are trusted conversational receipts, not proof of
native worker isolation or a live acceptance campaign. Installed bytes are checked
again at completion. Missing answers keep planning pending without publication
reads. When sufficient, the existing exact immutable-doc/issue readback verifier
records new commit/issue/docs pointers, preserving the old milestone. It does not
invent specifications, publish documents itself or create a feature batch.

## Verification policy

Routine ticket verification is focused deterministic unit/component tests with
mocked external boundaries, independent Standards/Spec review and integrated CI.
A separate live deployment/outage/recovery or real thread send is not a per-ticket
implementation gate. Whole-factory live milestone qualification remains separate;
this implementation does not claim it ran. Runtime completion still requires the
actual configured health contract. No deployment target, new spending, native
worker campaign or services are authorized by these commands' documentation.

The focused suite is `python -m unittest factory_v1.tests.test_iteration -v`.
It uses no HTTP server, Docker, native worker or complete controller journey.
