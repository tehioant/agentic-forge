# Read-only readiness collector (issue #26, slice 1)

This is an inventory API, **not an execution gate**. No service or public operation
currently consults it. It must not be used to authorize dispatch or a mutation.

```python
from factory_v1 import readiness

config = readiness.load_config(private_readiness_path)
report = readiness.collect(database, service_config, config, host_github,
                           readers=trusted_read_only_capability_readers,
                           running=independently_observed_service_running)
```

`load_config` uses the existing controller-owned single-link mode-0600 file
validator and rejects duplicate JSON fields. `collect` is also a directly usable
Python API for an already trusted host configuration. Do not expose arbitrary
Python reader objects through request JSON. Never pass credentials in the report.

## Input contract

The exact top-level keys are `readiness.FIELDS`. Operator/project/iteration must
match the service and registered approved iteration. The repository comes from
that iteration's immutable onboarding ID and endpoint, not a caller-selected
replacement. Inspection refuses `private=False` for existing **and** new targets.
It never creates a target, changes permissions or adopts another board.

- `slots`: existing finite continuation slot shape (`stage`, `profile`, `skills`,
  `claim_id`, `launcher`), covering all seven engineering stages. Profile names,
  homes and claims must be distinct and unused. Installed source/path identity,
  reviewed hashes and dependency closures use the existing validators. Native
  planning uses `planning_skills` (installed grill-me/grilling/to-spec); installed
  to-tickets uses `ticket_skill`. Existing adaptations are included in identity.
- `board`: existing explicit linked Projects configuration; null selection is
  refused. `board_pin` is the exact previously selected resolver result, including
  field and option IDs. Reads cannot establish write permission.
- `protection`: existing protection validator inputs (`base=main`, exact app-bound
  `required_checks`, `protection_sha256`), not a candidate merge authorization.
- `synthesis_config`: existing private executable/hash/timeout configuration.
- `reference`: explicit configuration reference. Delivery and notification paths
  come from the existing service configuration.

## Observations and unsupported capabilities

The report independently checks repository privacy/ID, Projects schema, complete
installed stage inventory, fresh slots, reviewed image/launcher limits, owned
subscription socket metadata, read-only spending preview, protection, current
main checks/workflows/deployment health and the exact originating notification
route/executable. Image inspection and socket metadata do **not** prove actual
container isolation or provider forwardability. Notification routing does **not**
prove platform permission or successful delivery; no probe message is sent.

`UnsupportedReaders.github_authority(scope, operations)` and
`execution_support(scope, stages)` deliberately deny by default. Existing
candidate-specific publication/merge authorization files, successful metadata
reads, worker declarations and user booleans cannot prove these general
capabilities. A trusted host adapter must independently validate the exact scoped
Issues/Projects/contents/PR/merge route and native planning/host synthesis/worker/
verifier capacity, then return a hash of those validated inputs. This is a
read-only adapter seam, not a token issuance or attestation framework. No usable
live implementation of those two readers is supplied in this slice. Passing a
hash from JSON is not an implementation of the adapter.

Current delivery validation requires an explicit deployment-health contract.
Deployment-not-applicable is unsupported, never silently healthy. Spending
preview currently requires an active iteration, so a paused report includes a
spending refusal; splitting installed capacity from runnable spending admission
is remaining work, not an assertion that paused infrastructure is broken.

## Output and limits

Each check has `state`, a fixed family-specific `code` and static `remediation`.
Raw exception messages/codes, config contents, URLs, headers and worker artifacts
are never returned. `input_identity` hashes the exact validated observations and
configuration. Recollect at sensitive boundaries and compare the identity;
retained reports are never authority. No freshness lease is issued.

`service_running` is a separate bool/unknown observation supplied by the caller;
it does not make readiness green. `capability_ready` means every inventoried
capability passed. `iteration_eligible` only observes existing active controls;
it is not a complete concurrency/restart/health admission decision.
`iteration_runnable` and `dispatch_allowed` remain **false** and
`boundary_wiring=not_implemented`, even for the fully mocked positive report.

## Deterministic evidence / outstanding work

`factory_v1/tests/test_readiness.py` seeds direct SQLite/file records and mocks
only independent external observations. It exercises real profile, source,
launcher, spending, protection, delivery and origin validators without preparing
or executing prior engineering stages. Fixture source catalogs are explicit
mocked installed-source evidence, not qualification of the host's installation.
Known installed implement/code-review drift stays denied; no production pins or
shared skills were changed.

Remaining: actual trusted authority readers, more precise capability/runnable
separation while paused, global iteration/ticket and profile ownership admission,
service/CLI composition, exact-input revalidation at existing sensitive
boundaries, native planning session/allowance authority, notification platform
capability, live ready/denied qualification, fresh simplification and independent
Standards/Spec review. Preserve pause/stop/cleanup/reconciliation and scoped
hold-resolution entry even when ordinary readiness is blocked. Report collection
itself must never fence those operations.

Publication, canonical/full-CI execution and real service/container/model/GitHub/
health/notification probes remain held for this slice. Focused mocked results do
not complete #26 or qualify the live factory.
