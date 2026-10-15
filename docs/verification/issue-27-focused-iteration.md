# Issue #27: focused iteration evidence, not live qualification

This records the delivery scope for [issue #27](https://github.com/tehioant/agentic-forge/issues/27) under the [no-live amendment](../specs/no-live-verification-amendment.md) and its [operator confirmation](https://github.com/tehioant/agentic-forge/issues/27#issuecomment-6187443505). Historical requirements are unchanged.

**Live interview-to-delivery qualification: NOT EXECUTED; waived for ticket delivery.** No disposable repository, worker/model campaign, deployment or platform notification was started. No live handles are invented. Runtime permissions, provenance, installed-source pins, isolation, spending, review, protection, health and cleanup requirements remain enforced; unknown runtime health remains unknown.

## Criterion-by-criterion scope

The [machine-readable component map](issue-27-component-evidence.json) lists seven criteria, their production entry points, adapter seams, 52 unique direct test IDs, and specific limitations. It is a coverage map, not a live execution trace or a self-attestation of passing tests.

1. **Conversation and scoped publication.** Components check sufficient-input skill loading, controlled versioned publication, current-milestone dependency ordering, native dependency/board readbacks and completed-blocker scope. Native context and GitHub responses are mocked; publication and verified-completion inputs are separate components, not a chained interview journey.
2. **Stage, isolation and provenance authority.** Components reject source/dependency/adaptation drift, caller repinning, forged execution and reused profiles/artifacts; selection binds the exact assignment/run/launcher. These checks do not prove actual isolated workers or installed skills executed.
3. **Automatic routing and continuation.** A new public `tick` matrix checks preparation, synthesis execution and ticket publication using three independently seeded states and the real selector. It verifies the exact downstream arguments, shutdown-event forwarding, unrelated-operation silence and unchanged durable inputs. Existing checks cover original completion before a coordinator request, correlated requested completion, fresh next-ticket ownership and explicit wait. No case consumes the previous case's output; downstream operations are mocked, not executed.
4. **Review, merge and delivery timing.** Components check exact candidate/review bindings, late holds, integrated-main checks, lost closure responses and verified ownership retirement. Tracker transport and independent review/deployment prerequisites are mocked. Source-delivery CI is real but is not product-campaign evidence.
5. **Deployment and workload health.** Components reject failed/pending/unknown CI/deployment states, unhealthy responses and last-read drift. HTTP responses are in-memory fixtures. No workload was deployed or independently observed healthy; workflow success alone is not health.
6. **Exact-origin reporting and next-input wait.** Components check routine silence, correlated completion outbox, private transport, uncertain/no-resend handling and explicit same-product input. A mocked command acknowledgement is not a delivery receipt. No platform message was sent.
7. **Truthful evidence and capability blockers.** Components preserve correlated durable evidence and refuse missing proof or unverified authority. The map deliberately leaves live worker, repository, deployment, health and notification handles null. No new evidence runner or campaign infrastructure is introduced.

## Verification and interpretation

Run the repository's unchanged unit/component and compilation commands:

```sh
python scripts/run_factory_tests.py
python -m compileall -q factory_v1
```

The canonical runner discovers every test once and rejects unmocked external execution. The new routing matrix adds one test method with three independent subcases; no production code, existing assertions, discovery rules, workflow or timeout changes are required.

Actual passing results must come from candidate-bound local receipts and the exact-head CI linked in the delivery PR. Those results establish focused component behavior only. They do not turn any waived live criterion into a passed live journey or authorize runtime execution.
