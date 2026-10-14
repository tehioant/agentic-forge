# Main delivery observation (#18)

`observe-delivery`, `watch-delivery` and `inspect-delivery` are trusted **host-only**
operations. They use Python, SQLite, the existing state lock and durable attention
outbox; polling does not launch Hermes or use an LLM. No repair worker, deployment,
GitHub mutation, public listener or live message is launched by these commands.

## Configure the product contract explicitly

The operator/controller must resolve the environment, required CI/CD workflows,
app-bound checks, target endpoint and acceptance journey during product grilling.
A URL returning 200 or a completed workflow alone does **not** prove delivery.
Install a private regular controller-owned file (mode `0600`, outside every worker
workspace/profile/artifact mount), for example:

```json
{
  "operator_id": "42",
  "repository": "example/product",
  "repository_id": 123,
  "api_base": "https://api.github.com",
  "issue": 18,
  "required_checks": [
    {"context": "quality", "app_id": 99},
    {"context": "security", "app_id": 99}
  ],
  "required_workflows": [{"id": 11, "event": "push"}],
  "environment": "production",
  "contract": {
    "reference": "docs/product-health.md; explicit originating operator decision",
    "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "health_url": "https://product.example/acceptance-health",
    "acceptance": {"critical-journey": true}
  },
  "timeout": 10
}
```

These are **placeholders, not an approved production contract**. `reference` and
`revision` identify the reviewed immutable agreement; supplying text is not proof
of approval. The trusted host integration verifies its real provenance. Choose
checks/workflows covering *all* product-required quality/security and CI/CD gates;
the observer does not discover or relax product policy. The configured observer
is pinned by canonical SHA-256 in the iteration on the first sample. Rebinding a
running iteration, target, environment or health contract is refused; there is no
silent config migration or command to clear an incident manually.

There are no tokens in this config. For actual GitHub, the adapter invokes only
fixed `gh api --hostname github.com --method GET` reads with the host's existing
approved auth. Use a suitably read-scoped host identity, not worker credentials.
The only alternative API base is explicit loopback HTTP for labeled test metadata.
The health URL supports HTTPS or loopback HTTP, no URL credentials/query/fragment,
redirects or ambient HTTP proxies. Health GETs send no authorization header.
Timeouts are finite `(0, 30]`; JSON is bounded to 2 MiB of UTF-8 and rejects duplicate
keys, non-finite numbers (including numeric overflow), invalid Unicode strings/keys
at any nesting depth and parser-limit failures. No external diagnostic body or
credential is included in retained errors.

The health endpoint must execute the agreed acceptance checks against the deployed
application, and return exactly:

```json
{
  "repository": "example/product",
  "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "deployment_id": 21,
  "environment": "production",
  "contract_sha256": "<canonical SHA-256 of the entire contract object>",
  "acceptance": {"critical-journey": true}
}
```

Use `factory_v1.assignments.digest(contract)` for canonical JSON hashing. Booleans
are not interchangeable with integers. The SHA must equal freshly read `main`;
the deployment ID must equal the newest GitHub Deployment in that environment.
The selected deployment must reference `main` or that exact SHA, with its **latest**
status `success`. A newer failed/pending/stale deployment cannot hide behind an
older successful deployment. A self-reporting endpoint is only as trustworthy as
the product's agreed deployment/health implementation; this controller does not
invent acceptance semantics or execute arbitrary shell acceptance commands.

Required app-bound Check Runs must each be unambiguous, completed `success`, and
have that exact main SHA. Required workflows have explicit numeric workflow IDs
and `push` or `workflow_dispatch` event; they must report the exact repository ID,
name, main branch and SHA. The newest matching run must succeed. Branch/PR runs,
wrong apps, skipped, cancelled, pending, absent or malformed observations cannot
qualify. Reads cover at most 99 records per list and require exact total counts
for runs; saturation/pagination is conservatively unknown, never guessed healthy.
The coherent snapshot rechecks repository, main, checks, workflows, deployment
and status after health, then main once more. A moving snapshot is unknown.

## Operate continuously under an existing supervisor

From the repository root, using an already registered private state file:

```bash
mise exec -- python -m factory_v1 --state /private/controller/state.sqlite --operator-id 42 \
  observe-delivery --project product --iteration m1 \
  --delivery-config /private/controller/delivery.json

mise exec -- python -m factory_v1 --state /private/controller/state.sqlite --operator-id 42 \
  watch-delivery --project product --iteration m1 \
  --delivery-config /private/controller/delivery.json --samples 100 --interval 30

mise exec -- python -m factory_v1 --state /private/controller/state.sqlite --operator-id 42 \
  inspect-delivery --project product --iteration m1
```

`watch-delivery` repeats deterministically for `1..10000` samples, sleeping a finite
`1..3600` seconds **outside the state lock** between samples. Run this bounded
command under the existing trusted supervisor with restart-on-exit to observe
indefinitely; no service installation is needed or performed here. A restart
loads persisted pins and incident identity rather than starting another incident.
Logs are newline JSON, not chat messages. Supervisor/operator failures require
attention; a stopped watcher is not proof of healthy delivery. Merge admission
re-observes the installed contract, so stale successful polling cannot authorize
an unhealthy mutation while the watcher is offline.

A recorded healthy/failed/unknown observation exits **0**: it is a successfully
recorded observation, not a promise of healthy delivery. Inspect `evidence.status`
or `monitor.latest.status`. Invalid config/state, local persistence failure or
capability refusal exits **2**. Remote failures become durable `unknown` evidence
and an active incident. Missing state is not implicitly created by observation.

## Freeze, durable evidence and attention

Any failed **or unknown** configured observation starts one correlated incident.
The current snapshot, content-addressed evidence, active `main_incident`, incident
journal and notification intent commit atomically under the existing shared state
lock. Repeated equal evidence creates no additional observation row. Changing
failure evidence updates the same active incident; a new episode after verified
clearance gets a new deterministic iteration-scoped sequence/identity. The first
attention intent remains immutable with the original evidence pointer.

Retained remote identity/state fields are typed and bounded to 100 characters;
oversized or malformed fields produce bounded `unknown` evidence, not retained
raw remote strings. New serialized observations, incidents and monitor context
must satisfy the same size/semantic contract as their restart readers before
commit. Reusing a content-addressed observation validates the exact existing row
before pointer promotion or clearance; corruption refuses atomically and is never
silently repaired, even when a new remote sample is healthy.

`inspect-delivery` exposes the current monitor/freeze and durable incident and
observation records. Successful clearance archives the exact healthy evidence ID;
it removes only the active freeze pointer, not failure history or pending attention.
Malformed persisted pins, evidence, incident pointers or attention refuse sampling
before remote IO rather than silently replacing state. Host state/journals/backups
must remain private and outside worker mounts, as with the other controller commands.

Healthy routine observations enqueue nothing. Incident attention uses the existing
`notifications`/`deliver`/`attention-reconcile` contract and exact registered origin.
The watcher deliberately **does not send messages**. The trusted existing attention
driver must deliver the outbox using the approved transport; uncertain delivery is
reconciled there, never resent blindly. #15's accepted **mock-only notification
limitation is unchanged**; this issue does not claim a live Discord delivery.

Once configured, `merge-candidate` observes delivery at admission and again before
the exact-SHA merge PUT. It then revalidates the reviewed main baseline (including
the sampled main), host config, exact target, protection, candidate checks, branch,
issue and local admission. Pinned requirement publication is the **last remote
guard**, after delivery observation and all other admission reads, before durable
merge intent and PUT. Ordinary work is refused if
an incident is active or the boundary observation becomes unhealthy/unknown.
The lock excludes concurrent local watcher/control writes through intent and PUT.
Existing candidate, review, requirements, protection and quality/security gates
remain unchanged. Remote state can change after the final read; GitHub's own merge
gates remain authoritative and the watcher detects subsequent delivery failure.

Recovery uses [the host-owned recovery lifecycle](RECOVERY.md), the exact incident,
repair assignment and issue, and the *same* ordinary engineering gates. Registration
binds both incident pointers atomically. Once registered, healthy polling updates
evidence but cannot clear the freeze. `verify-recovery` requires the repair merge,
integrated checks, exact removed-behavior reopening/status/dependency readbacks and
actual configured current-main deployment health before atomic clearance. The
observer alone neither grants repair authority nor dispatches a worker. Unregistered
transient incidents retain healthy-sample clearance. Deterministic fixtures are
simulated evidence, not native execution or actual deployment proof.

For compatibility, an iteration **without an installed delivery monitor** retains
#17's existing CI-only merge boundary; it never gains a verified deployment claim.
A deployment-enabled product must install its explicit agreed observer **before**
ordinary admission. This issue provides that watcher lifecycle, not automatic product
configuration or scheduler/supervisor installation. It does not invent a production
URL for the factory repository. `verify-delivery` retains its existing ticket/check
semantics and never claims iteration completion or deployed health; delivery monitor
health is separate evidence for the later iteration-completion controller.

## Verification and limits

Focused command:

```bash
mise exec -- python -m unittest factory_v1.tests.test_delivery factory_v1.tests.test_merge factory_v1.tests.test_attention -v
mise exec -- python -m compileall -q factory_v1
```

The repeatable suite exercises public CLI healthy-silent → health/CI/CD failure →
atomic freeze + one outbox → repeated/new-process/outage → branch-green rejection →
verified exact-main/deployment clearance, a bounded multi-sample watcher, concurrent
controllers, malformed/config/persisted/request shapes, bounded/ambiguous/paginated
readbacks, observation races, transaction rollback on notification persistence failure,
and failure/outage immediately before merge mutation. The gated recovery fixture
uses the real public stages with **simulated** GitHub/native-worker metadata, then
proves CI-green alone cannot clear a stale deployment.

The #18 review corrections add public-CLI/parser and controller matrices for
numeric overflow/lone surrogates in all response families, UTF-8 responses whose
escaped persistence would exceed the limit, corrupt archived clearance evidence,
new-record/restart contract symmetry, requirement issue/document drift during the
final health read, and coherent healthy main/config/admission drift after the prior
guard. Each matrix reuses its fixture rather than launching another worker chain
per input. All six new regression methods failed against immutable production
bytes at `32b157ec75d899424f3541c933f1afc19bd1d7b6`; the passing correction evidence
is local/advisory, not real CD or native isolated execution qualification.

[Retained readback](evidence/issue18-delivery-readback.json) distinguishes:

- **Actual read-only GitHub main CI**: check `115230157012` and Actions run
  `38377711264`, workflow `373467855`, successful on exact main
  `0026bc459aafc50c2c477e818405e52d8e4680bd`.
- **Actual local HTTP health**: test-only loopback target; healthy → failed → repeat
  → unavailable → healthy, durable attention deduplication and verified clearance.
  Its GitHub CI/deployment metadata is **simulated**, not live CD.
- **Real-path gap**: no GitHub Deployment exists in the read-only test environment,
  and no agreed production deployment/acceptance contract was supplied. The live
  observer therefore recorded `unknown` and froze, despite actual green CI.
  No live deployment/CD recovery or native isolated whole-factory qualification is
  claimed. No infrastructure, repository mutation or live Discord send occurred.

The readback probe command was `mise exec -- python
/home/ops/.hermes/cache/scratch/issue18-readback.py`; the retained evidence records
its actual results. The public commands above can reproduce live reads once the
operator supplies the actual approved deployment contract. Parent-owned fresh
simplification, independent Standards/Spec reviews and the canonical stabilized
`mise exec -- python scripts/run_factory_tests.py` run are still required before
source acceptance. This builder is an assistant coding worker, not a demonstrated
native isolated factory worker execution.
