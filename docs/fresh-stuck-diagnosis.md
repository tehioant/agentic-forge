# Stuck-work diagnosis and operator direction

Implements issue #16's public controller seam against the pinned fresh specification
and interview decisions `cccb21e35cc989a1ecd5a3c88f0689a694d77d9c` (US-16,
US-30–32; scenarios 5 and 14). This is not a live acceptance report.

## Lifecycle

Routine repair remains autonomous. A failed process, OOM, exhausted allowance,
iteration limit or deadline does **not** declare engineering work stuck.

1. The responsible isolated worker returns `status: "stuck"` after attempted fixes,
   with its exact artifacts, findings, command/results and `stuck-declaration`
   artifact. That artifact's JSON has `reason`, nonempty `attempted_fixes`
   (`change`, `command`, `result` strings), and nonempty `findings` strings.
   It is retained evidence, not a trusted worker receipt or verified fix.
2. `declare-stuck` takes `{"stuck_assignment": "<assignment SHA-256>"}`. It
   verifies a completed launcher run and exact result/source/baseline pins, then
   persists the hold before controlled GitHub writes. The issue and transitive
   dependents remain **open**, with board progress **Blocked**, read back from
   exact targets. No live related role may be abandoned; stop/reconcile it first.
   Only after verified blocking does the controller retire the old ticket's
   implementation ownership/reservation. Independent eligible work can then
   reserve/prepare through the existing one-ticket controls. Debugging does not
   occupy an implementation ticket slot. Existing iteration pause and spending
   controls still apply; this feature neither attests delivery health nor bypasses
   a main-incident pause imposed by the incident controller.
3. `prepare-diagnosis` takes the stuck identity plus dedicated fresh `profile`,
   exact installed `skills` descriptors and unique `claim_id`. `--dry-run` does
   not persist a claim. Generic assignment preparation cannot inject arbitrary
   failure prose: it must resolve the same declared stuck source and closure.
4. `launch-assignment` uses the existing whole-process isolated launcher. The
   failed **actual tree**, not an earlier git commit or implementation chat, is
   copied to read-only `/workspace`; baseline, original issue/specs/standards,
   declaration, artifacts and attempted repairs are immutable inputs. Only
   scratch and bounded approved model access are writable/available. Debug runs
   load `/home/ops/.hermes/skills/diagnosing-bugs/SKILL.md` with selected pin
   `9168404abda0967a5d32977e3498cd95fda6807018852f3de736a78357c82b40`,
   its reviewed dependency closure and existing read-only adaptation. The
   substantive feedback-loop method runs; diagnosis stops before modifying phases.
   Routine hypotheses remain in evidence. No debug fix, product choice,
   publication, merge or acceptance is authorized. A fresh debug attempt may
   replace a failed/stopped attempt only after confirmed container removal, with
   a new profile/claim; completed reports cannot be silently replaced.
5. The worker retains `diagnosis-report` alongside `stage-evidence` (contract
   `factory-stuck-diagnosis-v1`). Required report fields are:
   - `contract`, `adaptation`, exact `pins`;
   - `feedback_loop`: executed `command`, exact `symptom`, actual `result`;
   - `commands`: actual `command`/`result` pairs matching retained tests;
   - `hypotheses`: ranked `hypothesis`/falsifiable `prediction`/`evidence` objects;
   - `attempted_repairs`, `findings` and `cause_or_uncertainty`;
   - `directions`: unique `id`/`direction`/`tradeoff` objects, with
     `recommendation` naming one candidate ID;
   - `blockers`: explicit unavailable capabilities.

   If a loop cannot be built, it must say what was attempted: `feedback_loop:
   null`, `hypotheses: []`, nonempty `blockers`, and bounded result `status:
   "blocked"`. The installed skill's absent optional HITL template is not replaced
   with an invented script. Missing input goes through controller attention.
   A finished investigation uses `status: "done"`; a red reproduction is expected
   and **not** converted into passing check evidence. Stuck engineering and debug
   runs cannot advance to simplification, review acceptance, merge or closure.
6. `request-direction` takes the stuck identity, plus `--diagnosis-config` on first
   use. It checks the exact completed isolated report and independent host
   authorization, then atomically retains an inspectable decision request with
   original thread, issue/run, both assignments and report digest. The message
   includes uncertainty, blockers, candidate directions and recommendation; the
   full report is inspectable through `inspect-assignment`. Replays keep one
   deterministic event/decision identity. Delivery uses the existing `deliver`,
   `attention-reconcile`, `notifications` and `respond` lifecycle, not direct
   worker messaging. Delivery or acknowledgement is not a grant.
7. Antoine's authenticated `respond` input must correlate exact event, decision,
   operator, origin and reference. Its `response` is a **JSON string**:
   `{"action":"retry","direction":"Investigate X within the original requirements"}`
   or `{"action":"revise","direction":"Version the affected requirements to Y"}`.
   Unrelated/free-form input cannot be interpreted as permission.
8. `apply-direction` takes `{"stuck_assignment":"<SHA-256>","event_id":"stuck-<SHA-256>"}`.
   Silence, wrong thread/operator/event, changed requirements or execution
   evidence fail closed. `retry` restores only the exact original affected scope
   to its earlier progress (Active becomes Ready), preserving other holds. The
   original candidate and its previous acceptance evidence stay retired; a fresh
   scoped implementation receives Antoine's direction and must run all ordinary
   tests/simplification/two-axis review/delivery gates. An already active independent
   ticket still prevents a second implementation admission. `revise` records the
   correlated material direction and keeps work `revision-required`/Blocked:
   versioned spec/ticket resynthesis and revised-scope admission are a separate
   requirement-revision workflow, not permission to reinterpret old pins here.
   Exact duplicate responses/applications do not apply twice or reset progress.

## Private host authorization boundary

Native tool-load records, conversations, probes and worker claims are **untrusted**.
The controller validates their consistency but does not manufacture trusted
receipts. An independent trusted host verifier must supply a non-symlink,
host-owned, single-link `0600` JSON file outside **all** worker workspaces, homes
and artifact roots; the retained debug envelope is host-owned `0700`:

```json
{
  "operator_id": "<registered Antoine identity>",
  "execution_reference": "<independent actual execution verification reference>",
  "diagnosis": "<exact object from diagnosis.execution_binding(completed_assignment)>"
}
```

`diagnosis` above is an object, not a string. Its binding includes the exact stuck
assignment/run/requirements/result/source, debug assignment/run/handoff/report,
selected source/dependency pins, adaptation hash, instruction-load/work hashes,
and retained runner/input/container/model/conversation/command evidence hashes.
Computing a binding is **not** verification or authorization. A verifier must
independently check actual skill-guided investigation, reproduction and enforced
read-only execution before supplying this file. Neither a caller's `--isolated`
flag nor a worker-generated private-looking file is sufficient. Saved host
verification is rechecked against exact current evidence before direction or a
pending release; drift refuses advancement.

## Commands

All new controls require the same trusted `--state`, `--operator-id`, `--project`,
`--iteration`, `--request`, `--api-base` and configured read-only tracker route
as other factory controls. `declare-stuck` and `apply-direction` alone perform
scoped board-status writes and exact read-back; neither closes issues. Example:

```sh
python -m factory_v1 --state "$STATE" --operator-id "$ANTOINE" declare-stuck \
  --project "$PROJECT" --iteration "$ITERATION" --api-base "$TRACKER" --request stuck.json
python -m factory_v1 --state "$STATE" --operator-id "$ANTOINE" prepare-diagnosis \
  --project "$PROJECT" --iteration "$ITERATION" --api-base "$TRACKER" --request debug.json
# launch-assignment uses its existing private launcher config and approved model route.
python -m factory_v1 --state "$STATE" --operator-id "$ANTOINE" request-direction \
  --project "$PROJECT" --iteration "$ITERATION" --api-base "$TRACKER" --request stuck.json \
  --diagnosis-config "$INDEPENDENT_HOST_AUTHORIZATION"
# Existing deliver/respond controls preserve the exact registered thread and correlation.
python -m factory_v1 --state "$STATE" --operator-id "$ANTOINE" apply-direction \
  --project "$PROJECT" --iteration "$ITERATION" --api-base "$TRACKER" --request direction-event.json
```

Publication failure leaves durable `blocking` intent and does not free ownership.
Retry uses exact GitHub read-back before releasing the slot. A failed or partially
applied retry leaves `release-pending` intent and a local admission hold; its exact
response cannot be rebound, and replay reconciles board progress before release.
Stop/pause/source drift retain original evidence and prevent new admission.

## Evidence and remaining acceptance

`factory_v1.tests.test_diagnosis` has focused report/claim/pin/malformed-input
unit cases and a small public CLI smoke set. The HTTP tracker, Docker and model
seams and host authorization are explicitly **LABELED deterministic fixtures**.
The debug fixture actually executes a greeting assertion and retains its red
output, but this is **not native Hermes or real Docker isolation proof**.

Real native model/skill execution, actual read-only enforcement, live thread
receipt/operator input and GitHub board read-back in the private disposable repo
remain parent-owned live acceptance. No credentials, shared skills or live
provider helpers are changed by this implementation. Full suite, simplification,
independent review and PR delivery remain parent-owned as requested. This module
exposes deterministic public lifecycle transitions; it does not add a supervisor
or automatically drive every stage of the later factory service loop.
