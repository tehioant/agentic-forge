# Fresh factory interview decision record

This is a normalized question-and-answer record of the fresh interview in the originating conversation. It preserves settled answers and accepted recommendations; it is not a verbatim audio transcript. No previous factory-beta context was used.

## Q1 — What does the factory produce from an idea?

**Answer:** Antoine gives it an idea; it grills him; it produces specifications capturing the questions and decisions established through the `grill-me` interview. Later clarification establishes that this is the first stage of the complete delivery loop, not the whole factory.

## Q2 — Who is it for?

**Answer:** Antoine operates it to build his products. He is the first operator and accountable decision-maker.

## Q3 — Initial limits on autonomy

**Answer:** It must not start a new project without Antoine's idea or approval. Other boundaries were clarified in later questions rather than inferred as unrestricted authorization.

## Q4 — Where does the factory end?

**Answer:** It includes product development, not only specification generation. The intended sequence is grilling, specs, tickets, selecting a ticket, implementation, simplification, review, merge, and repeating until the ticket batch is done, followed by another grilling. Q9 and Q10 replace the notion of a permanently finished product with completed iterations.

## Q5 — What belongs in the specs?

**Answer:** The whole product, its first step, and decisions about its language/technology belong in the specs. Capture the interview's settled questions and decisions as traceability alongside buildable requirements. Q8 resolves who chooses language/technology; Q26 limits executable tickets to the current milestone.

## Q6 — Who decides grilling is finished?

**Answer:** The agent collaborates with Antoine to determine what it needs and decides when grilling has enough clarity to finish. This does not transfer product and technical choices away from Antoine.

## Q7 — Spending and deployment consequences

**Answer:** Spending must always be asked for approval. Deployments must always be secure. Q14 defines autonomous gated deployment; Q29 defines the boundary between existing approved allowance and a new charge.

## Q8 — Language and technology choices

**Answer:** The agent recommends; the decision is made with the user. This covers programming/stack and user-facing language as relevant to each product's grilling.

## Q9 — What is the next grilling for?

**Answer:** The next iteration of the same product. A product is never permanently finished. A different product requires its own idea or approval.

## Q10 — What does completion mean?

**Answer:** The step/iteration is finished when all tickets created from the specs and grilling are finished. A ticket satisfies its description, feature, changes, and requirements, with all tests green. Q12 adds merge into `main` as the delivery boundary; the agreed iteration report also verifies healthy CI/CD and deployment.

## Q11 — What authorizes implementation after grilling?

**Answer:** Implementation starts immediately after specs/tickets. Antoine can stop it if needed; there is no additional batch-approval checkpoint.

## Q12 — Does ticket completion include merge?

**Answer:** A ticket closes only when merged into `main`; unmerged work is not considered delivered. Agents must be able to merge autonomously. Requirements, tests, simplification, and review still gate that merge.

## Q13 — What happens when a ticket is stuck?

**Answer:** A debug agent investigates and writes a report. Antoine decides the direction needed to resolve the stuck ticket. Q15 specifies who declares it stuck; Q24 specifies which work can continue.

## Q14 — How is secure deployment enforced?

**Answer:** Required quality gates and security checks must pass in CI, followed by CD. Agents monitor deployment from `main` and fix failures. This replaces the earlier proposed requirement for routine production-deployment approval.

## Q15 — Who decides that work is stuck?

**Answer:** The agents responsible for the ticket decide. They must still attempt repairs and iterate before escalating. No fixed engineering retry threshold was selected.

## Q16 — What happens while `main` is broken?

**Answer:** A repair agent handles `main`; ordinary work cannot merge. Roll-forward can be a quick solution, and agents choose according to the situation. Q19 explicitly permits gated recovery commits during the freeze.

## Q17 — Where is authoritative project state?

**Answer:** Tickets are on the repository's projects board. Specs are in the code repository under `docs/`. The factory's internal execution record is separate from, and does not replace, this project-work authority.

## Q18 — How independent are the agents?

**Answer:** Accept the recommendation: separate implementation and review contexts, a separate debug agent, and a deterministic controller verifying stage results. Subsequent clarification establishes fresh role workers, including simplification and `main` repair, launched from dedicated Hermes profiles.

## Additional decision — Bugs are work items

**Answer:** Agents may create issues for bugs and must fix the accepted issues as tasks like tickets. Bug issues must respect the same ticket format. Q23 determines whether they belong to the current iteration or the following one.

## Q19 — Does the `main` freeze exempt repairs?

**Answer:** Accept the recommendation: freeze ordinary ticket merges, but allow recovery changes through required gates. Resume ordinary merges only when `main` and deployment are healthy.

## Q20 — Serial or parallel ticket implementation?

**Answer:** Accept the recommendation: one active implementation ticket initially; choose from work whose dependencies are satisfied. Independent review work may be parallel. Q35 applies the initial implementation/iteration limit across the factory.

## Q21 — Where does execution run?

**Answer:** Grilling starts in conversation. The factory runs in the background. Hermes returns to the originating thread when it needs something or there is an issue; otherwise no routine interaction. Q22 permits the iteration-completion report/invitation.

## Q22 — How does the next iteration start?

**Answer:** Accept the recommendation: report the completed iteration and invite another grilling, then wait for Antoine's input. Do not invent a feature batch while he is absent.

## Q23 — Do new bugs extend the current iteration?

**Answer:** Accept the recommendation: fix broken `main` immediately; include bugs blocking current-milestone requirements; defer unrelated non-blocking bugs to the next grilling.

## Q24 — What continues during a human decision?

**Answer:** Accept the recommendation: block the affected ticket and its dependents while independent work may continue. If `main` is broken, ordinary execution stays paused until recovery is resolved.

## Q25 — Stop, pause, and restart semantics

**Answer:** Pause is the normal interruption; accept safe checkpointing and prevention of further merges. Preserve work/evidence, retain emergency-stop capability, and reconcile repository/board/CI state before resuming after a restart. An intentional pause is not canceled by reboot.

## Q26 — Which part of the vision becomes executable tickets?

**Answer:** Accept the recommendation: keep the whole-product vision in `docs/`, but generate and execute current-milestone tickets only. Future capabilities remain documented for later iteration decisions.

## Q27 — Repository platform and account

**Answer:** GitHub is the default platform. Antoine must specify a different account or platform when required. GitHub Issues represent work, the repository-linked Projects board tracks it, and PRs deliver changes.

## Q28 — Agent execution runtime

**Answer:** After clarification, accept dedicated Hermes role profiles launching fresh workers for each assignment rather than introducing Codex CLI as the initial separate executor.

**Clarification:** A profile holds persistent setup; a worker is a running agent using that setup. Per-run assignments include role, ticket, workspace, relevant specs/standards, skills, and expected outputs. Profiles and separate processes are not security sandboxes. Avoid multiple concurrent independent top-level writers to one profile; fresh worker conversations do not require unrelated personal memory.

## Q29 — Spending boundary

**Answer:** Accept the recommendation: existing approved subscription allowances may be used autonomously. New chargeable API usage, purchases, infrastructure allocations, or financial commitments require explicit scoped/ceiling-bound approval. Exhausted allowance pauses work; no silent paid fallback.

## Q30 — Factory implementation language

**Answer:** Accept the recommendation: Python deterministic controller using supported Hermes/GitHub interfaces, outside Hermes core. Product-specific languages are still decided during each product's grilling.

## Q31 — Execution host

**Answer:** Accept the recommendation: the existing server, supervised with persistent state and restart recovery. Keep grilling/decisions in the originating thread; no unapproved new infrastructure spending.

## Q32 — Roles and credentials

**Answer:** Accept enforced role-specific capabilities: reviewers inspect/check; implementers change assigned workspaces and deliver branches; only controlled merge operations merge. CI/CD owns deployment credentials. Exclude unrelated personal-assistant credentials. Enforce with real isolation, not prompts.

## Q33 — Initial model policy

**Answer:** Accept the recommendation: the existing approved model/provider setup initially, configurable per role. Introduce alternatives through explicit decisions, not automatic paid fallback.

## Q34 — New or existing repositories?

**Answer:** Accept both: approved new products and explicitly identified existing repositories. New repositories are private by default. Never infer that an unrelated repository or the beta is a target.

## Q35 — Concurrent products?

**Answer:** Accept the recommendation: one active product iteration across the factory initially. Other products retain their state while paused.

## Q36 — Requirements changed during execution

**Answer:** Accept the recommendation: pause affected work for material changes; resolve with Antoine, update specs/tickets, and resume with current requirements. Preserve useful completed work. Minor clarifications can proceed without silently reinterpreting changed requirements.

## Q37 — Whole-worker container isolation

**Answer:** Accept whole-worker isolation. The trusted controller remains outside containers. Workers get assigned workspace, scratch, and necessary model access, but no Docker socket, unrelated personal credentials, or deployment credentials. Reviewers get read-only submitted code plus writable scratch. Controlled GitHub operations enforce role scope. Actual enforcement must be tested.

## Q38 — Authority to change gates

**Answer:** Agents may add or legitimately update tests with independent requirement-based review. They may not bypass required checks, weaken security policy, or disable branch protection to get green results. Policy changes need Antoine's decision.

## Q39 — Factory validation

**Answer:** Accept a private disposable GitHub validation repository alongside the factory repository. It is an authorized test environment, not a new product. Exercise real delivery plus rejection, stuck-ticket diagnosis, broken-`main` recovery, pause/restart, and spending approval. A successful commit alone is inadequate proof.

## Subsequent publication request

**Answer:** Antoine invoked `/to-spec`, selected an existing repository, then supplied `agentic-forge` and the exact URL `https://github.com/tehioant/agentic-forge`.

**Scope:** Synthesize and publish the fresh specification. Do not begin implementation as part of this request. Publishing into this explicitly selected repository does not authorize reading or reusing its previous beta code/documents.

## Unselected engineering details

Exact controller storage/schema, supervisor configuration, GitHub Projects board identity/permissions, worker image/mount/credential wiring, role model overrides, supported notification integration, and product-specific CI/CD targets remain implementation verification/design details. They must satisfy the decisions above. No existing beta solution was selected by implication.

## Subsequent operator clarification — Installed-skill execution

**Source:** Originating Discord review thread `1555860658455846964`; explicit operator instruction following the specs/tickets alignment review.

**Operator wording:**

> Add into the relevant specs that the steps in the loop are linked to skills that we already have on the server.
> For relevant tickets, specify the skill must be ran

**Settled clarification:** The factory must run the existing server skills for their corresponding stages, not merely reproduce similar outcomes or validate their availability. Bind grilling to `grill-me`/`grilling`, specification synthesis to `to-spec`, ticket synthesis to `to-tickets`, implementation/corrections to `implement`, simplification to the installed non-Matt `simplify-code`, independent Standards/Spec review to `code-review`, and diagnosis to `diagnosing-bugs`. Repair uses these same diagnosis and engineering stages. Select exact installed sources/dependencies, retain pinned inputs and actual execution evidence, and apply the explicit factory stage adaptations in the specification.

**Scope:** Update the specification and relevant ticket acceptance requirements. This instruction does not resume workers, merge implementation PRs, close delivery tickets, modify shared skill files, authorize spending or weaken gates. Original Q1–Q39 answers are preserved.
