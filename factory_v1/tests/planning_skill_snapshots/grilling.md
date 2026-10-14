---
name: grilling
description: Grill the user relentlessly about a plan, decision, or idea. Use when the user wants to stress-test their thinking, or uses any 'grill' trigger phrases.
---

Interview the user relentlessly until you reach a shared understanding. Map this as a **design tree**: every decision branches into the decisions that hang off it.

Work the tree in **rounds**. The **frontier** is every decision whose prerequisites are already settled: the questions you can ask _now_ without guessing at answers you haven't heard yet. Keep the tree scoped to the requested deliverable rather than every adjacent topic. Ask the whole frontier in one round: number each question and give your recommended answer. Then wait for the user's answers before the next round.

## Answer cadence

Let Antoine answer questions sequentially. When he requests an explicit round-completion gate, do not ask follow-ups, advance rounds, or produce the final synthesis until he says the round is complete—even if every numbered question appears answered. Acknowledge each answer briefly and neutrally; avoid a full restatement after every response. Record partial answers and unresolved points without interrupting with new questions. Background research completion does not override this gate.

Separate recommended decisions from factual claims. Do not steer the user into a preferred identity or treat an interpretation as something they have established.

Format a round like so:

```
❓ **Q1** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>

---

❓ **Q2** - **<question title>**: <question body, might be multiple paragraphs, including multiple choices>

➡️ <your recommended answer>
```

Each round the user answers reshapes the tree: settled decisions push the frontier outward and unblock questions that depended on them. Recompute the frontier and ask the next round. A question whose answer depends on another question still open in this round belongs to a _later_ round, not this one.

Find retrievable _facts_ yourself. When a frontier question needs a fact from the environment or existing source material, dispatch a sub-agent to find it; don't ask the user to repeat information you could look up. Verify relevant source passages before relying on the child's summary. Don't block on discovery: a running exploration is an unsettled prerequisite, so only downstream questions wait; ask the rest of the frontier now. Ask the user for undocumented personal experiences, reasoning, preferences, or measurements that cannot be retrieved. The _decisions_ are the user's: put each to them and wait.

The session is done when no decision required for the agreed deliverable remains open. Track unresolved points explicitly; defer or exclude optional details rather than extending the interview indefinitely. Summarise the agreed understanding, distinguish current evidence from aspirations, and state any exclusions or assumptions. Do not draft or implement the deliverable until the user confirms that understanding. Once confirmed, produce the requested artifact instead of continuing to interview.

## Career and personal-profile interviews

Apply the same method to career positioning, resumes, portfolios, LinkedIn profiles, and spoken introductions; a software project is not required. Follow [Career positioning](references/career-positioning.md) for source discovery, leadership evidence, metrics, and profile-writing guidance. Keep profile improvement separate from vacancy searches or public profile edits unless those actions are requested.
