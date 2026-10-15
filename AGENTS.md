# AGENTS.md

Instructions for any AI agent (Warp/Oz, Claude, or equivalent) working in this repository.

## Development Loop
- run skills /implement, /code-simplifier, /code-review


## Delivery Process

- Break work into small, shippable increments; each iteration delivers working, testable functionality
- Get feedback early and often; small changes are easier to review, test, and rollback
- Commit as often as possible — step by step, small task to small task, one commit per task
- Always ask clarifying questions before proceeding on ambiguous requests. Auto-approve mode does not waive this — always ask.

## Verification Policy

- Keep routine tests simple, fast and deterministic: unit tests and small focused components with mocked external boundaries. Do not add or run E2E tests, full worker/controller journeys (including simulated chains), real services or containers unless Antoine explicitly changes this policy.
- Preserve core behavior and distinct safety regressions. Remove genuinely duplicated coverage and repeated setup, not just test names; do not hide tests, weaken assertions, add sharding/retries or raise/remove timeouts to obtain green checks. Use `mise run test` for the complete policy-compliant suite and report unrun or incomplete checks honestly.
- Keep the complete discovered suite at or below 500 test methods. Consolidate closely related cases into small, named matrices with isolated state and meaningful assertions, not wrapper tests or discovery filters. Report retained parameterized cases separately from discovered methods; a lower method count is not evidence of less execution work.
- For ticket completion and test planning, follow the implementation-ticket verification policy in `docs/specs/fresh-agentic-factory.md`. Use meaningful automated tests, independent review and integrated CI; configure deployment requirements in the project specs rather than adding a live deployment/outage/recovery exercise to each ticket.

## Plan Mode

- Make the plan extremely concise. Sacrifice grammar for the sake of concision.
- At the end of each plan, give me a list of unresolved questions to answer, if any.

## Simplicity & YAGNI

- Focus on delivering user value, not building frameworks
- Start with the feature; add infrastructure only when needed
- Avoid "future-proof" solutions for hypothetical needs
- YAGNI: don't build what you don't need today
- Infrastructure should emerge from feature needs, not precede them
- Always choose the simplest solution that fully solves the problem
- Avoid over-engineering and premature optimization
- Question complexity: "Is there a simpler way to do this?"

## Code Quality

- Write self-explanatory code; use descriptive variable, function, and class names
- Extract complex logic into well-named functions instead of adding comments
- Exception: docstrings for public API documentation
- Let the code speak for itself

### DRY

- Every piece of knowledge has a single, authoritative representation
- Extract repeated logic into reusable functions/modules
- Balance DRY with simplicity — don't over-abstract too early

### SOLID

- Single Responsibility: one reason to change per class/module
- Open/Closed: open for extension, closed for modification
- Liskov Substitution: subtypes must be substitutable for their base types
- Interface Segregation: many specific interfaces over one general interface
- Dependency Inversion: depend on abstractions, not concretions

## Error Handling

- Implement comprehensive error handling for all external calls (API, database)
- Use meaningful error types/classes; log errors with appropriate severity
- Never expose internal error details to end users
- If information is missing, state clearly what's needed instead of guessing
- Fail fast and fail clearly

## Architecture & Infrastructure

### Twelve-Factor App

- Store configuration in environment variables, never in code
- Treat backing services as attached resources
- Strictly separate build, release, and run stages
- Execute the app as stateless processes; export services via port binding
- Scale out via the process model
- Maximize robustness with fast startup and graceful shutdown
- Keep development, staging, and production as similar as possible

### Infrastructure as Code

- All infrastructure defined and version-controlled as code
- Use reusable modules for common patterns; keep infrastructure minimal
- Document infrastructure decisions and dependencies
- Apply changes through CI/CD pipelines, not manually
- Infrastructure must be reproducible and disposable

### Dependencies

- Keep dependencies to an absolute minimum — each one is a liability
- Prefer standard library solutions over third-party libraries
- Regularly audit and remove unused dependencies

## Guiding References

Strong references to follow: Martin Fowler, Kelsey Hightower, James Lewis.

