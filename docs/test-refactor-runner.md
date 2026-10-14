# Complete unit/component conversion: runner and remaining local fixtures

## Operator scope

Antoine explicitly selected refactoring all remaining legacy journeys before delivery, rather than excluding legacy tests from CI. He approved merging PR #47 after fresh green CI and a current-main recheck, then closing #21 with the missing current-version real-worker/live verification documented. No E2E, native campaign, new spending, protection bypass or fabricated acceptance evidence is authorized.

The baseline inventory at `b0196edd7a149b9097a9e3346579f2a6a900e891` contains 40 modules and 545 declared test methods. Four disjoint implementation groups cover 25 modules; the parent audits the remaining 15. Module inventory and execution evidence are retained outside the repository. No module is excluded from discovery to obtain green CI.

## Runner

`scripts/run_factory_tests.py` now discovers and runs every unit/component test in one interpreter. It removes the inherited subprocess sharding rather than adding shards or extending time limits. Nested inventory remains nonempty and unique; discovery errors refuse before execution; failures do not omit later tests; completed count must equal the inventory. An audit boundary rejects real socket/process/shell/signal execution. An attempted boundary also makes the overall run fail if a test catches the refusal. File-backed SQLite and ordinary unit-test threads remain available. The hook retires after its scope so nested runner tests cannot leave active global restrictions behind.

`test_ci_shards.py` retains the runner coverage as seven direct tests without launching subprocess shards. No new framework or alternate test entrypoint is introduced. The workflow keeps its existing command, Python version, compile step and 15-minute limit; only the test-step label changes.

## Parent-owned replacement map

- `test_portability.py`: replace CLI/repository lifecycle execution with a temporary control component, real durable pause/readback and full private-fixture cleanup under configured TMPDIR.
- `test_ticket_portability.py`: replace synthesis/publication journey with direct real ticket reservation using the existing small `LeanControllerCase`, under both scratch and runner-temp layouts. Assert retained reservation and cleanup, without claiming controller-process durability.
- `test_ticket_status.py`: preserve all seven status variants using real contract/frontier/reservation functions and mocked tracker I/O. Refusal retains reservation/durable state and board fields, while independent work remains reservable. The initial frontier read is retained before reservation so its legitimate readback checkpoint is not confused with a new claim.
- `test_ticket_standards.py`: retain five oversized/deep-graph cases. Generated data exercises `validate_batch` directly; authoritative data exercises real frontier/reservation with explicit issues/members. The 1100-node chain remains meaningful recursion/graph coverage without a lifecycle or HTTP server. Invalid reads/claims do not mutate tracker or persisted claims; valid independent work survives unknown/out-of-scope blockers.
- `test_diagnosis_holds.py` and `test_native_handoff.py`: inspected and unchanged; their real hold bookkeeping, artifact encoding and isolated AST load checks already have mocked boundaries and no journey startup.
- The eight previously refactored interruption/lean/role/cleanup modules remain unchanged in this conversion and retain their earlier exact-candidate evidence until final whole-suite verification.

Parent check: 27 unique tests passed in 2.391 seconds (2.451 seconds including discovery), no external execution attempts, unchanged tested bytes/modes. An initial overly broad status-fixture baseline compared a legitimate new frontier readback against no frontier; the baseline read was corrected before reservation, preserving the full unchanged-state assertion. This is not a final-head or full-suite result.

The four group reports supply the other module-by-module replacement maps. Final independent review, candidate-bound complete discovery/run, publication/CI and administrative issue disposition are separate remaining delivery steps.
