---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

Implement the work described by the user in the spec or tickets.

Run typechecking and focused tests during implementation and after each review correction. Run the final full suite on the stabilized, reviewed candidate; record its exact HEAD, command, runtime and completed exit. An interrupted earlier-head run is not a pass. If review changes source during a full run, keep that run's evidence separate and verify the final candidate rather than transferring the result to a new HEAD.

Keep it simple and commit often on pull request.

Once done, use /code-simplifier to make the code as simple as possible and then use /code-review to review the work.

Push the code and ship or show or ask.
