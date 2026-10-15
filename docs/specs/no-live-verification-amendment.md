# No-live verification amendment — issues #26–29

## Explicit operator direction

> No live tests

> You must implement and merge all tickets independently

This amendment applies to delivery of issues #26–29. It supersedes their live
delivery exercises without modifying historical immutable specifications or issue
bodies.

## Delivery contract

Implement autonomously; run focused deterministic automated behavior checks;
obtain fresh simplification and independent Standards and Spec review; publish a
PR, verify green policy-compliant CI, merge and verify the integrated result, then
set the issue and board item to Done. Parent integration owns publication and
tracker updates for the current delegated source lane.

Live acceptance is explicitly waived for ticket delivery and must be labeled
**not executed**. For original live-validation-only issues #27–29, focused coverage
of the underlying behaviors plus honest evidence and limitations replaces the
delivery exercise. This is not a claim that the live iteration, security or fault
campaign passed.

Do not run real services, containers, native-model or product campaigns,
deployments, live faults or notification tests. Do not request live approval or
add live acceptance infrastructure to satisfy these tickets.

## Unchanged boundaries

Product runtime guards, permission checks, configured-health requirements and
execution provenance remain enforced. No paid fallback, permission expansion or
source-policy bypass is authorized. Unknown runtime health remains unknown.

CI must use focused component checks, not simulated whole-worker/controller
journeys or multistage precursor chains. Parent independently audits minimal
fixture conversion of remaining chains in a separate lane. This amendment does
not authorize changing test discovery, excluding safety coverage or weakening CI
gates.
