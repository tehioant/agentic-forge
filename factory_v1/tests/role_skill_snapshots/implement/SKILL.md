---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

Implement the work described by the user in the spec or tickets.

Run typechecking and focused tests during implementation and after each review correction. Run the final full suite on the stabilized, reviewed candidate; record its exact HEAD, command, runtime and completed exit. An interrupted earlier-head run is not a pass. If review changes source during a full run, keep that run's evidence separate and verify the final candidate rather than transferring the result to a new HEAD.

When tightening an assignment or persisted-state contract, search the test tree for hand-built records and local validator fixtures before final review; focused feature tests may not exercise older consumers. Update their minimum identity/revision fields to the current contract without weakening production validation, removing assertions or bypassing the target guard.

If the full suite fails, read the complete failure trace and reproduce the exact failing test with `python -m unittest MODULE.CLASS.TEST -v` before changing code. For a fixture-only repair, retain the red/green evidence, verify production bytes and assertion ASTs are unchanged, obtain a narrow review continuation, then run the canonical full suite on the new final HEAD. Keep the original failed suite separate; a passing targeted repair does not turn it into a full-suite pass.

Keep it simple and commit often on pull request.

Once done, use /code-simplifier to make the code as simple as possible and then use /code-review to review the work.

Push the code and ship or show or ask.
