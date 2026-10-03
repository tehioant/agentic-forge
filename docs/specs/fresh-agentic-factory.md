# Fresh agentic factory loop specification

**Status:** Specification synthesized from the fresh grilling interview; not an implementation report.
**Operator:** Antoine.
**Target repository:** `tehioant/agentic-forge`, explicitly selected by the operator.
**Provenance boundary:** Use only the decisions from this conversation. Do not inspect, import, reuse, or derive behavior from the previous factory beta, its code, configuration, skills, plans, sessions, or state.

## Problem Statement

Antoine wants to build and continuously evolve products from his ideas without personally coordinating every engineering task. An idea must first be clarified through an adversarial interview; the resulting decisions must become useful specifications and executable work. After that collaboration, agents should implement, simplify, review, merge, and monitor delivery autonomously, returning to the originating conversation only when human input is needed, something requires attention, or the iteration has completed.

The product is never permanently finished. The useful completion boundary is a finite iteration: the milestone agreed during grilling has been delivered through completed tickets, integrated into `main`, and its delivery is healthy. Agent claims, green but inadequate tests, code that exists only on a branch, and a list of closed issues are not sufficient evidence of successful delivery.

## Solution

Build a fresh Python-based deterministic factory controller outside Hermes core, running as a supervised background service on the existing server. Hermes remains the conversational interface for grilling and human decisions. Dedicated Hermes role profiles launch fresh, isolated worker processes for bounded assignments.

The product-level cycle is:

1. Antoine supplies or explicitly approves a product idea or an existing repository.
2. Grilling happens in the originating conversation. The agent recommends options, Antoine decides product and technical choices, and the agent determines when the iteration is sufficiently clarified.
3. Synthesize a whole-product vision, a precise current-milestone specification, and an interview decision record. Keep specifications in the product's `docs/` area.
4. Create dependency-aware GitHub Issues for the current milestone and put them on the repository-linked GitHub Projects board.
5. Begin execution without a second batch-approval checkpoint.
6. Select an eligible ticket, implement and test it, simplify the changes, independently review them, iterate on findings, and autonomously merge only through the controlled merge path.
7. Verify the merge and required checks against the integrated code. Monitor `main` CI/CD and deployment; repair failures before normal work continues.
8. When the current iteration's required work is complete and delivery is healthy, send the outcome report and invitation to the next grilling in the originating thread.
9. Wait for Antoine's input before defining the next iteration of the same product.

Only one product iteration and one implementation ticket are active initially. Independent checks and review work may run in parallel within that assignment. Other approved products retain their state while paused.

## User Stories

1. As the operator, I want to begin with my own idea, so that the factory builds a product I actually intend to create.
2. As the operator, I want new products to require my idea or explicit approval, so that agents do not initiate unrelated projects.
3. As the operator, I want to explicitly select an existing repository, so that the factory never guesses its target.
4. As the operator, I want an adversarial grilling interview in the originating conversation, so that assumptions are challenged before implementation.
5. As the operator, I want the agent to recommend product and technical choices while leaving those choices to me, so that automation preserves my control over the product.
6. As the operator, I want the agent to decide when enough clarification has been reached, so that I do not manage every interview transition.
7. As the operator, I want whole-product vision and current-milestone specifications, so that long-term intent is preserved without overcommitting the backlog.
8. As the operator, I want the interview's questions, answers, and resolved decisions retained, so that specifications are traceable to our discussion.
9. As the operator, I want specifications versioned under the product's `docs/` area, so that workers can use durable project context rather than conversation memory alone.
10. As the operator, I want programming language, user-facing language, and stack choices resolved during grilling, so that implementation uses agreed technology.
11. As the operator, I want executable tickets only for the current milestone, so that the iteration stays finite.
12. As the operator, I want dependency-aware vertical-slice tickets, so that each piece of work is independently verifiable and ordered correctly.
13. As the operator, I want tickets and bugs represented as GitHub Issues on the repository-linked project board, so that work has one authoritative tracking surface.
14. As the operator, I want implementation to start immediately after specs and tickets are generated, so that routine approvals do not interrupt delivery.
15. As the operator, I want one active product iteration and one active implementation ticket initially, so that resource use and recovery remain understandable.
16. As the operator, I want fresh implementation, simplification, review, debug, and repair workers, so that roles do not share one persuasive conversation context.
17. As the operator, I want reviewers to inspect actual requirements and changes independently, so that the implementer's explanation does not substitute for evidence.
18. As the operator, I want simplification to preserve required behavior, so that cleaner code does not compromise the product.
19. As the operator, I want all required tests green and requirements satisfied, so that passing tests alone cannot hide missing functionality.
20. As the operator, I want agents to merge autonomously after validated gates, so that I do not manually deliver every ticket.
21. As the operator, I want a ticket closed only after its work is merged into `main`, so that branch-only work is never reported as delivered.
22. As the operator, I want verification against the integrated commit, so that stale branch checks cannot authorize the wrong result.
23. As the operator, I want agents to monitor CI/CD and deployment on `main`, so that they own the consequences of their merges.
24. As the operator, I want a dedicated repair agent when `main` breaks, so that restoring delivery takes priority over feature work.
25. As the operator, I want ordinary merges frozen during a `main` incident while recovery changes remain possible, so that roll-forward is not prevented by the freeze itself.
26. As the operator, I want recovery agents to choose a suitable roll-forward or safe revert, so that the incident response fits the failure.
27. As the operator, I want reopened tickets when a recovery removes their delivered behavior, so that the board stays honest.
28. As the operator, I want agents to create bug issues in the same format as tickets, so that discovered defects enter the normal delivery lifecycle.
29. As the operator, I want milestone-blocking bugs included now and unrelated bugs deferred, so that discovery does not make the current iteration infinite.
30. As the operator, I want ticket agents to attempt fixes and decide when they are stuck, so that normal debugging does not immediately become a human interruption.
31. As the operator, I want a separate debug investigation and evidence-backed report when work is stuck, so that I can choose the direction with useful information.
32. As the operator, I want blocked work and its dependents to wait while independent tickets can continue, so that one decision does not unnecessarily stall the whole factory.
33. As the operator, I want the factory to run in the background and remain quiet during routine success, so that it reduces rather than adds coordination overhead.
34. As the operator, I want decisions, problems, and iteration-completion messages in the originating thread, so that I can continue the discussion in context.
35. As the operator, I want normal pause to checkpoint work and prevent further merges, so that I can interrupt safely.
36. As the operator, I want emergency stop to terminate workers while preserving recoverable evidence, so that dangerous execution can be stopped quickly.
37. As the operator, I want restart reconciliation against GitHub, git, and CI/CD, so that crashes do not duplicate work or create false completion.
38. As the operator, I want material requirement changes to pause affected work, so that agents do not merge against obsolete requirements.
39. As the operator, I want role-specific filesystem and credential isolation, so that permissions are enforced rather than merely requested in prompts.
40. As the operator, I want required quality/security checks and branch protection to remain authoritative, so that agents cannot obtain success by weakening the gate.
41. As the operator, I want legitimate test changes independently reviewed against requirements, so that tests remain trustworthy.
42. As the operator, I want approval before chargeable usage or new financial commitments, so that autonomy does not create unapproved spending.
43. As the operator, I want existing approved subscription allowances usable without repetitive approvals, so that already authorized capacity remains useful.
44. As the operator, I want exhausted allowances to pause affected work rather than cause a paid fallback, so that the spending boundary is maintained.
45. As the operator, I want the factory validated in a private disposable repository, so that real delivery and failure handling can be exercised without risking a product.
46. As the operator, I want the next interview to concern the next iteration of the same product and wait for my input, so that the factory does not invent a new feature batch while I am absent.
47. As the operator, I want this factory designed from the fresh interview only, so that the previous beta cannot silently determine the new architecture.

## Implementation Decisions

### Product and specification boundaries

- The factory serves Antoine as the first and accountable operator, not a multi-user platform.
- The agent owns interview facilitation and the judgment that clarification is sufficient; Antoine owns product and technical decisions discussed in that interview.
- Retain a whole-product vision, current-milestone specification, and question/answer decision record. Include requirements, behavior, constraints, non-goals, acceptance criteria, technical choices, and testing decisions.
- Specs belong in the product repository's `docs/` area. The internal document structure is an implementation detail, not a mandate to copy prior beta conventions.
- Create executable tickets for the current milestone only. Record future capabilities as vision/backlog candidates for the next interview, not as an automatically executable full-product backlog.
- The transition from a completed interview to specs, tickets, and execution is automatic. Upstream skill prompts that require routine spec or ticket-batch approval must be adapted to this policy; they must not introduce an accidental extra approval checkpoint.
- Grilling for the following iteration begins with the existing product and updated specifications. It is invited by the completion report but cannot proceed without operator input.

### Project onboarding and work tracking

- GitHub is the default platform and the configured GitHub identity is the default account. Antoine must specify an alternative account/platform when required.
- New repositories are private by default. Existing repositories must be explicitly selected.
- GitHub Issues and the repository-linked GitHub Projects board are authoritative for work-item scope, dependencies, and progress. Pull requests deliver the code.
- A deterministic internal execution record may track stage, run, claim, artifact, pause, incident, and notification state. It is not a competing authoritative product backlog.
- Features and bugs use the same core work-item contract: title, desired behavior/change, requirement/spec reference, acceptance criteria, blockers, and status. Bugs add reproduction/evidence, expected behavior, actual behavior, and relevant environment information when available.
- Tickets are dependency-aware, verifiable vertical slices. A worker may claim only an eligible item whose blockers are complete and which belongs to the active iteration or approved recovery scope.
- Before creating a bug or other work item, check for duplicates using its observable symptoms and scope.

### Controller and module interfaces

- Use Python for a reusable deterministic controller outside Hermes core. It runs on the existing server under a supervisor with durable execution state and restart recovery.
- The controller schedules role assignments, verifies their outputs, enforces state transitions and permissions, tracks merge/deployment evidence, handles pause/recovery, and coordinates operator decisions.
- The controller does not use repeated model reasoning for routine polling, scheduling, or state bookkeeping. LLM workers perform the engineering and judgment tasks.
- Required conceptual interfaces are: conversation/intake, specification and ticket synthesis, GitHub work tracking, worker execution, artifact/result verification, controlled repository mutations, CI/CD and health observation, incident recovery, and scoped thread notification.
- The worker interface receives a role, work-item identity, pinned requirement/spec revision, repository/workspace identity, input artifacts, required skills, allowed capabilities, and expected result contract. It returns artifacts and structured evidence, not authoritative completion by assertion.
- GitHub and CI/CD adapters expose read-back verification of the exact repository, PR, issue, commit, check run, and deployment involved in a transition.
- Persistent records correlate project, iteration, issue, stage, worker/run, workspace, reviewed head commit, merged commit, and external observations. External mutations must be idempotent or reconciled before retry.
- Persist notification intent and delivery state so a transient messaging failure does not discard a required decision request. Do not promise exactly-once network delivery; deduplicate retries where possible.
- Internal worker scheduling may reuse suitable supported Hermes mechanisms, but no specific internal queue implementation is prescribed here. GitHub remains authoritative and ordinary dispatcher notifications must not violate the quiet-operation policy.

### Workers, profiles, and skill composition

- A profile is the persistent configuration/state home; a worker is a fresh running Hermes process for a bounded assignment using that setup. They are complementary, not alternatives.
- Use dedicated role profiles for implementation, simplification, independent review, debugging, and `main` repair. Do not inherit unrelated personal assistant memory or credential access.
- Profile setup contains model/provider, reasoning, skills, tools, role rules, approvals, and credential access configuration. Project conventions come from the selected repository and explicit handoff.
- Start with the existing approved provider/model setup. Make role selection configurable; introduce different models only through an explicit technical decision. No unapproved chargeable fallback.
- Initially allow one simultaneously active top-level worker per role profile. Parallel helpers must retain isolation and cannot become uncontrolled independent writers to the same profile home.
- Workers receive fresh context assembled from the current ticket, relevant specs and standards, precise code baseline/diff, and evidence from preceding stages. Do not preload the entire conversation or beta context.
- Compose grilling, specification synthesis, ticket generation, implementation, simplification, and independent code review into explicit stage contracts. Avoid duplicate implicit reviews or hidden approval loops inside nested skill invocations.
- Resolve and validate skill names and transitive dependencies at startup. Installing a skill does not prove it is unambiguous, available to a worker profile, or adapted to the factory's automation contract.
- Simplification is a scoped quality pass, not permission to redesign the product or alter public requirements. Behavior-affecting or out-of-scope decisions return to the appropriate review/operator path.

### Required execution of installed skills

The factory automates the skills already installed on the existing server, rather than replacing them with a custom workflow that merely produces similar artifacts. The following bindings are mandatory for the corresponding stages:

- **Grilling:** run Matt Pocock's `grill-me` entry point and its `grilling` dependency in the originating conversation. Challenge assumptions, recommend options, preserve actual answers and resolve the decision frontier before synthesis. Invoke the same binding for the next iteration only after Antoine supplies input.
- **Specification synthesis:** run Matt Pocock's `to-spec` on the resolved interview to synthesize the whole-product vision, finite milestone and traceable decision record. Merely storing categorized answers or accepting externally prepared specification documents does not demonstrate this stage.
- **Ticket synthesis:** run Matt Pocock's `to-tickets` on the pinned current milestone to produce verifiable vertical slices and their blocking graph. Apply its work-item contract to discovered bugs and run it when affected tickets need resynthesis after an agreed material revision. Controlled adapters perform publication and exact read-back.
- **Implementation and corrective work:** the fresh implementation worker must run Matt Pocock's `implement` for its assigned ticket or correction, including meaningful tests at the agreed seams. Do not substitute a generic coding prompt. The deterministic controller, not `implement-spec`, owns eligible-frontier scheduling and per-ticket integration.
- **Simplification:** the separate fresh simplifier must run the installed `simplify-code` skill. This is an existing server skill inspired by Claude Code's simplify workflow, not a Matt Pocock skill. Cover reuse, quality, efficiency and altitude, preserve behavior, and accept an evidence-backed no-op.
- **Independent review:** run Matt Pocock's `code-review` against the pinned baseline and exact candidate. Execute its **Standards** and **Spec** axes in separate fresh read-only contexts, parallel where permitted by enforced isolation, and preserve separate findings and verdicts. Both axes must pass before the candidate can advance.
- **Stuck-work investigation and incident diagnosis:** the dedicated debug or repair worker must run Matt Pocock's `diagnosing-bugs`. Retain the actual reproduction/feedback-loop evidence, hypotheses, diagnostic commands and conclusions, or honestly report the missing capability. Diagnosis is not permission for an otherwise read-only debug worker to edit the candidate.
- **Main repair:** use `diagnosing-bugs` for diagnosis and `implement` for modifying repair work; send the repair candidate through the same `simplify-code` and two-axis `code-review` stages and unchanged gates as ordinary work.

**Source selection and worker availability:** reuse the installed sources; do not rewrite these skills as parallel factory-specific copies or install another skill framework. The current explicit source selection for Matt's bindings is the local Hermes skill directory (`$HOME/.hermes/skills/<skill>/SKILL.md` for `grill-me`, `grilling`, `to-spec`, `to-tickets`, `implement`, `code-review` and `diagnosing-bugs`); `simplify-code` is selected from `$HOME/.hermes/skills/software-development/simplify-code/SKILL.md`. These are deployment source identities, not worker host mounts. Resolve each exact source and its real transitive dependencies/supporting inputs, record content hashes and the factory adaptation in the assignment manifest, and stage only those immutable snapshots in the appropriate worker context. Duplicate bare names in external directories do not authorize silently selecting another copy. Missing, ambiguous, altered or unavailable required skill inputs block admission or invalidate affected stage evidence. A reviewed explicit source update gets a new pin; unrelated installed skills are not modified.

**Factory adaptations:** load and follow the selected skill's substantive method, with a small explicit stage contract for these established policies:

- Resolve genuine product, technical and testing decisions with Antoine during grilling; remove only routine post-grilling spec/ticket-batch confirmation prompts. Unresolved choices remain pending, and silence never becomes approval.
- Move `implement`'s nested review to the dedicated post-simplification `code-review` stage instead of running a duplicate hidden review. Testing is mandatory; TDD/test-first ordering is not a factory-wide requirement and is used where appropriate at agreed seams.
- Run `simplify-code` in one dedicated simplifier context covering all four angles rather than automatically launching four additional cleanup agents. This does not collapse the independent Standards/Spec acceptance review.
- Supply exact tracker, standards, spec and baseline pointers through the handoff. Keep controller-owned commits/publication/merge operations within the existing capability boundaries; a skill's ordinary CLI instructions do not grant broad credentials or direct mutation authority.
- Keep routine diagnostic hypotheses/progress in evidence rather than emitting stage-success chat. Request missing capabilities and genuinely needed operator decisions through the existing exact-origin attention path. A read-only diagnostic run stops before modifying phases; authorized repair runs perform them within scope and retain regression/cleanup evidence.

**Execution evidence and automatic handoff:** each applicable completed stage must retain the resolved skill identity and hashes, adaptation identity, correlated stage/run and pinned inputs, evidence that the executing agent loaded the actual selected skill instructions, and observable work/results from following them. A catalog entry, supplied handoff, skill name in final prose or successful process exit alone is insufficient. The controller verifies this evidence and the required behavior/tests before advancing. Deterministic tests may use labeled fixtures; live acceptance must exercise actual skill-guided conversation/workers and cannot substitute fixture artifacts or manually supplied specs/ticket batches for synthesis.

After sufficient interview clarification, the controller automatically progresses through `to-spec`, `to-tickets`, eligible assignments, `implement`, `simplify-code`, two-axis `code-review`, controlled delivery verification and completion reporting. The live acceptance trace must demonstrate this without operator commands to advance each stage or manual injection of intermediate synthesis artifacts. Agreed decision, spending, stuckness and incident boundaries may still pause the affected work.

Scheduling, claims, polling, persistence, publication, merge validation, health observation and notification delivery remain deterministic controlled operations; they do not require gratuitous skill/LLM calls. This clarification binds the existing stages to the installed skills and adds execution proof; it does not authorize new spending, worker execution, protection changes or beta reuse.

### Ticket delivery and definition of done

- The delivery order is implementation/testing, simplification, independent review, corrective iterations, validated merge, and observation of integrated delivery.
- The reviewer examines the requirements, specs, repository standards, and actual changes independently of the implementation conversation. A persuasive implementation summary cannot substitute for the original sources.
- Tests and requirements must both pass. Review covers missing/partial behavior, incorrect implementation, scope creep, and code quality, rather than only check-run status.
- Pin review and check evidence to the candidate commit. Changed code invalidates stale review/check evidence as appropriate. Refresh the integration baseline before merge when it changed.
- Only the controlled merge path may merge, after required quality/security checks and independent review pass. Agents may request these merges autonomously.
- A ticket cannot close or be reported delivered before its changes are actually merged into `main`. Required tests/checks must be verified against integrated code; a worker exit code or branch-only artifact is insufficient.
- GitHub's automatic issue-closing behavior must not make the board misleading. Reconcile premature closures and reopen work whose acceptance conditions are not delivered.
- An iteration completes only when its required work is complete and `main` CI/CD and deployment health are verified. Product-specific deployment targets and acceptance journeys are decided in that product's grilling.

### Bugs, stuck work, and recovery

- The responsible ticket agents attempt fixes and iterate; they decide when they are stuck. Do not replace that judgment with an unagreed fixed retry count for engineering difficulty.
- Infrastructure/run limits and crash detection may bound individual executions, but exhaustion is not permission to weaken the requirements or falsely complete the ticket.
- A stuck ticket triggers a separate debug worker. The report includes evidence, attempted repairs, likely cause or remaining uncertainty, candidate directions, and a recommendation.
- Antoine chooses the direction for genuinely stuck work. The issue and affected dependents wait for that decision; independent eligible work may continue.
- Bugs that break `main` have immediate recovery priority. Bugs preventing the current milestone's requirements join the active iteration. Unrelated non-blocking bugs wait for the next grilling.
- When `main` CI/CD or deployment fails, freeze ordinary merges and dispatch a repair agent. Recovery changes remain eligible through the required gates.
- Repair agents choose an appropriate roll-forward or safe revert based on evidence. If repair becomes stuck, use the debug-report/operator-decision path.
- Reopen a ticket when a revert or other recovery removes its delivered requirements. Do not claim recovery healthy until the relevant checks and deployment observations prove it.

### Pause, restart, and requirement changes

- Normal pause stops admitting new work, checkpoints the active stage, and prevents further merges. Preserve in-progress artifacts and diagnostic state.
- Emergency stop terminates worker execution promptly while retaining available artifacts/state for recovery.
- On restart or resume, reconcile work-item state, repository/branch/worktree state, worker liveness, merge evidence, and CI/CD observations before dispatching further work.
- Detect external actions already completed before the crash; do not duplicate commits, issues, PRs, merges, or notifications blindly.
- Preserve an intentional paused state across restart rather than automatically treating a reboot as resume authorization.
- A material requirement or product-direction change pauses affected work. Resolve it with Antoine, version the updated specs/tickets, and invalidate obsolete assignments/reviews before resuming.
- Minor non-material clarifications may proceed, but workers must not silently reinterpret a changed requirement.

### Enforced security and spending

- Profiles, process boundaries, role prompts, and tool visibility alone are not a security sandbox.
- Run the entire worker process inside an isolated container. Keep the trusted controller outside worker containers.
- Worker containers receive only the assigned workspace, isolated scratch space, and necessary model access. Do not mount the Docker socket, host personal assistant home, unrelated credentials, or deployment secrets.
- Reviewers get read-only access to the submitted code and writable isolated scratch space to run checks. Implementers/simplifiers may change their assigned workspaces within scope.
- GitHub mutations flow through controlled operations that enforce role, repository, branch, issue, and incident scope. Workers must not receive a broad personal token as a substitute for an enforced capability boundary.
- CI/CD owns deployment credentials. Deployment is autonomous when required quality/security checks pass; agents monitor and repair the resulting delivery.
- Inspect default Hermes container mounts and credential propagation rather than assuming Docker mode is credential-free. Validate filesystem-tool access as well as shell access.
- Agents may add or legitimately update tests, subject to independent requirement-based review. They may not bypass checks, disable protection, weaken security policy, or relax gates merely to make a result green. Policy changes require an operator decision.
- Existing approved subscription allowances may be used autonomously. Chargeable API usage, purchases, new infrastructure, and financial commitments require explicit approval with scope and ceiling.
- Exhausted allowances pause the affected work. Do not silently switch to a paid alternative, buy capacity, or initiate a new billable service.
- Spending authorization must be checked at the point of the controlled chargeable operation; a prompt instruction alone is not enforcement.

### Communication and iteration completion

- Record the verified originating conversation/thread when the product iteration is initiated. Route decision requests and attention events back to that exact destination; do not guess a different chat.
- Routine successful stage transitions remain silent in chat. Detailed progress remains inspectable through stored execution state, work items, PRs, and logs.
- Notify for necessary input, blocked/stuck work, incidents requiring attention, and the iteration-completion report/invitation. Deduplicate repeating reports of the same event.
- Failure to obtain operator input leaves affected work blocked. Do not fabricate approval or convert silence into authorization.
- The completion report identifies delivered work and evidence, unresolved/deferred bugs, deployment state, and the invitation to discuss the next iteration.

### First implementation milestone

Deliver one complete factory iteration through the public lifecycle: conversation-derived specification, current-milestone work items on GitHub, isolated fresh workers, implementation/simplification/review, gated autonomous merge, observed delivery, and completion reporting. The milestone must also demonstrate the agreed safety and recovery paths, not just a happy-path agent commit.

A private disposable GitHub validation repository is authorized for this purpose. It is a test environment, not an independently invented product. Implementation should be split into small verifiable slices after this specification; this document does not itself start workers or publish execution tickets.

## Testing Decisions

### Primary seam

Test observable behavior through the controller's public lifecycle/control interface: provide a configured project and iteration, submit work and external observations, pause/resume, and inspect resulting work-item, repository, execution, and notification state. This is the highest useful shared seam for scheduling, stage sequencing, reconciliation, incident handling, and completion behavior. Avoid tests that depend on private function structure, source-code text, or implementation-specific call counts.

Use narrower integration seams only where necessary to verify a real trust boundary: worker container permissions, controlled GitHub writes, model/spending admission, CI/CD observation, and thread delivery. A fake controller or mocked sandbox cannot prove those boundaries.

### Modules and behavior to test

- Specification/ticket synthesis: whole-product vision and current-milestone separation, requirements and technical decisions, common feature/bug format, dependencies, interview traceability, and no extra approval loop after grilling.
- Scheduler/controller: one active product iteration and one implementation ticket, dependency eligibility, ordered stages, rejection/rework, genuine blocked states, and evidence-driven completion.
- Worker setup: fresh contexts, explicit role profiles, exact installed skill sources/dependencies/adaptations pinned and loaded in the executing context, observable skill-guided execution evidence, and no unrelated memory/context. Test refusals for missing/ambiguous/changed inputs and claims based only on catalog entries or externally supplied artifacts.
- Merge verifier: wrong repository/branch refusal, stale review/head refusal, missing/failed checks refusal, main-freeze refusal for ordinary work, authorized repair exception, and exact merged-commit read-back.
- Main watcher/recovery: failed CI/CD and unhealthy deployment detection, freeze behavior, repair priority, roll-forward/revert outcomes, reopened removed requirements, and verified return to healthy state.
- Bug/debug handling: duplicate avoidance, current-vs-future bug scope, agent-declared stuck work, independent debug evidence/report, operator decision admission, blocked dependents, and continued independent work when healthy.
- Persistence/pause: no new admission or merge after pause, saved artifacts, explicit resume, restart with lost workers, reconciliation of actions completed before failure, and no duplicate external effects.
- Requirement revisions: material changes invalidate affected assignments and stale review/check evidence; obsolete work cannot merge silently.
- Security: actual container filesystem/mount/network/credential boundaries, reviewer source write denial, absence of host/Docker/deploy access, and refusal of unauthorized controlled mutations.
- Spending: already approved allowance admitted, new charge without approval denied, scoped/ceiling-bound approval respected, and quota exhaustion blocked without paid fallback.
- Notifications: exact originating-thread routing, silent successful transitions, persisted attention requests, failure retry/deduplication, and blocked work remaining blocked without a real decision.

### Acceptance scenarios

1. A sufficiently resolved interview runs `grill-me`/`grilling`, then the factory automatically runs `to-spec`, `to-tickets`, ticket `implement`, `simplify-code` and independent Standards/Spec `code-review`, retaining actual skill-execution evidence without manually injected synthesis artifacts or per-stage operator advancement. A milestone work item is merged into `main` and closed only after verified delivery evidence; healthy CI/CD is observed and the iteration report is emitted.
2. Reviewer rejection returns work for corrections; the rejected candidate cannot merge using stale approval.
3. An unmerged branch with passing tests is never reported as a completed ticket.
4. A requirement omitted despite green tests is rejected by independent review.
5. Agent-declared stuck work produces a separate debug report and waits for an explicit operator direction; unaffected eligible work can continue when `main` is healthy.
6. A broken `main` freezes ordinary merges, admits gated recovery work, and resumes ordinary execution only after health verification.
7. A roll-forward restores delivery; a revert that removes delivered behavior reopens the corresponding work item.
8. A pause during active work prevents a subsequent merge and retains checkpointed evidence. Restart preserves pause and reconciles completed external actions before explicit resume.
9. A material ticket/spec revision prevents obsolete code and stale reviews from merging.
10. A newly discovered blocking bug enters the current iteration using the common format; a non-blocking unrelated bug remains deferred.
11. Worker attempts to write reviewer source, access host credentials/Docker socket/deploy secrets, merge directly, or operate outside assigned repository scope are refused by actual boundaries.
12. Attempts to weaken a required check or protection policy cannot silently produce a permitted merge.
13. Unapproved chargeable operations and paid quota fallbacks are denied; legitimate previously approved allowances work.
14. Routine stage success is silent in chat; a decision/incident reaches the exact originating thread without treating delivery alone as operator approval.
15. Completion invites the next iteration and then waits; no new product or feature batch is invented autonomously.

### Test execution and evidence

- Use deterministic test doubles for repeatable controller scenarios, including crash windows and API failures, but add real-path integration tests for external state and enforcement.
- Exercise the private disposable repository with real GitHub issue/board/PR/check/merge reads and writes, real isolated Hermes workers using approved capacity, and an actual CI/CD test target.
- Read back exact external targets after writes and attach evidence to the tested scenario. Test artifacts must distinguish simulated controller cases from live integration runs.
- Do not report isolation, merge, CI/CD, notification, or recovery as proven until the relevant real path has been exercised.
- No existing factory implementation or beta test conventions were inspected. Prior art from that codebase is therefore not asserted or imported.

## Out of Scope

- Loading or reusing the previous factory beta in any form.
- A multi-user hosted platform or customer-facing factory service.
- Multiple concurrently active product iterations or concurrent implementation tickets in the initial milestone.
- Planning and executing the entire long-term product vision as one immutable batch.
- Starting an unrelated product or selecting an existing repository without operator authorization.
- Self-inventing the next milestone when Antoine has not participated in the next grilling.
- Mandatory human approval of every ordinary ticket, merge, deployment, or completed spec/ticket batch.
- Bypassing required checks, weakening security policy, or disabling branch protection as a way to pass.
- Treating Hermes profiles or prompts as OS security enforcement.
- Automatic new spending, purchases, infrastructure allocation, or chargeable provider fallbacks.
- Requiring modifications to Hermes core when supported external interfaces can implement the behavior.
- Declaring a permanently finished product instead of a completed iteration.

## Further Notes

### Decision provenance

The companion interview decision record captures Q1-Q39, subsequent worker/profile clarification, and the operator's explicit selection of `tehioant/agentic-forge` as the publication target. It is a normalized record of the conversation, not a claim of word-for-word transcription. The original spoken/transcribed wording was sometimes ambiguous; settled meanings are recorded without inventing additional answers.

### Prerequisites still to verify during implementation

- GitHub Projects read/write permissions, board identity/schema, required check configuration, and protection capabilities for the selected repository/account.
- A concrete container/credential design compatible with the approved Hermes provider, worker file tools, and controlled GitHub operations.
- Supervision, lifecycle persistence, pause/restart enforcement, and current supported worker-launch interfaces.
- Reliable originating-thread delivery using supported Hermes mechanisms; delivery outages and duplicate events must remain explicit failure cases.
- Product-specific test and deployment contracts chosen during each product's grilling.

These are technical verification tasks, not permission to relax the agreed behavior. If a prerequisite requires a new financial commitment, policy change, or unresolved product/architecture decision, return to Antoine.

### Publication versus execution

This specification describes the intended first factory milestone. Publishing it does not mean workers have been started, profiles configured, tests passed, or implementation completed. A `ready-for-agent` specification label means the scope is prepared for later decomposition; the `/to-spec` action itself does not launch the factory.
