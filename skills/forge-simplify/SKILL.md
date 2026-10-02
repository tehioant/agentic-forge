---
name: forge-simplify
description: "Simplify a Forge ticket diff without changing behavior."
---
# Forge simplifier
One dedicated pass, not a four-agent swarm. Read AGENTS.md, ticket, baseline-to-HEAD diff and adjacent code. Check reuse, quality, efficiency and altitude: search for existing helpers, remove unnecessary complexity, avoid wasted work, distinguish deeper refactors from useful scoped cleanup. Search before claiming duplication; blame existing code before proposing removal. Uncertainty means no removal. Skip style nits.
Preserve behavior, public interfaces, security and tests. Only SAFE or test-verified CAREFUL changes within approved paths. Flag correctness bugs and RISKY/deeper refactors instead of expanding scope. No-op is valid when nothing materially improves. Run tests after edits. No delegation, Git metadata writes, commits, publication or deployment. Controller records cleanup separately after checks. End with requested JSON verdict and exact Git HEAD.
