# Issue #21: focused test refactoring

## Scope

The local #21 candidate was rebased onto main `67d9aa5e533e67b9c0d077214c87854a6d8d6e58`. The rebase added only main's three documentation changes; production modules retained the reviewed `cbf7553c2850e7c17786437d85a40cd131deb9c7` bytes/modes. The refactoring changes test code and fixtures, not runtime behavior, approved skill pins, CI configuration or time limits.

## Replaced coverage

- Planning/ticket pause regressions moved from nested lifecycle fixtures to `test_admission_boundaries`: real planning/synthesis/publication functions and durable admission, mocked source/network reads. Final-read refusal, no persisted claims/results/intents and explicit resume are retained. Iteration resume cannot release a requirement hold.
- `test_controls`: real control state, short admission lane, assignment/result and merge boundaries with mocked workers/tracker/model effects. Checkpoints, budgets, operator/scope/privacy, corrupt controls, lock independence, stop/cleanup ordering and uncertainty, receipt retention and explicit-resume restrictions remain checked.
- `test_bug_admission`: each distinct discovery, result, publication-intent and receipt boundary is retained. One focused recovery case replaces repeated full publication journeys; current/archived discovery, revision and uncertain-effect holds have direct checks.
- `test_revision_integration`: all thirteen prior test IDs retained as focused controller/publication/execution components. Final reads, malformed controls, untrusted synthesis receipts, no duplicate effects, exact-body preservation and independent holds remain checked. External synthesis `invoke` is mocked; no executor process runs.
- `test_lean_tickets`: real frontier/reservation, affected-body publication/replay and assignment preparation with mocked tracker I/O. Semantic formatting matrices remain local. Native/textual blocker disagreement has a distinct case rather than masking eligibility checks. Lost-response replay retains durable intents and proves no duplicate PATCH/edge mutation.
- Assignment review-dependency refusal moved from full setup into `test_role_skills`, exercising real `assignments.prepare` validation before tracker/state access on both axes.
- The two newly added socket/database-failure journeys moved into `test_broker_controls`: real broker cancellation and reservation handling with mocked socket/thread scheduling/transport. The injected failure occurs during an admitted operation, and the returned late response cannot settle uncertain billing or trigger retry.

Whole-process, actual socket/container termination and live GitHub evidence are no longer supplied by these rewritten tests. They are focused simulated checks, not native acceptance. Unrelated inherited legacy journey tests are unchanged; this report does not claim that the entire repository suite now contains no E2E tests or fits the CI budget.

## Immutable requirements and unchanged contracts

`fixtures/lean_original_spec.json` contains exact original specification/interview bytes from `cccb21e35cc989a1ecd5a3c88f0689a694d77d9c`. The previous test read mutable current docs while asserting an immutable original pin; main's documentation advance exposed that mismatch. Original issue body/links and both document hashes are preserved, not silently repinned.

Legacy `test_controls.cli/value/wait` helpers remain compatible for untouched callers. Existing assignment source/dependency fixture pins are unchanged. No production guard was replaced or weakened.

## Verification

Focused set: `test_admission_boundaries`, `test_broker_controls`, `test_controls`, `test_bug_admission`, `test_revision_integration`, `test_lean_tickets`, `test_role_skills`, `test_sandbox_cleanup`.

The initial implementation check, before review corrections, completed **82 unique tests, exit 0, 4.142 seconds**, with content/mode fingerprints unchanged. This is historical evidence for the initial refactor, not a final-head pass. The in-process test wrapper rejects unmocked socket creation/connections, subprocess launches, shell execution and process signaling. Test-local mocks replace intentional external seams. This is not the full suite.

Interpreter-only guard-removal probes separately confirmed that targeted late-admission/byte/span/replay assertions fail when the relevant guard is bypassed. No production file was modified for those probes. Sensitivity evidence is distinct from passing acceptance checks.

An early controls-fixture check deadlocked by injecting pause while its admission lane was already held; the injection was corrected to the released boundary. Other early fixture errors were corrected without changing production. No incomplete run is counted as a pass.

## Review corrections

The independent Standards and Spec reviews of `8243912bdb0e280df714c5a870bdc58b6fada8a2` identified two blockers, preserved in their original reports:

- **ST-1:** The lock-contention test could join a regressed worker before releasing its controller lock. Executor shutdown now encloses the writer/lock scope, and writer rollback is guaranteed by `finally`. All assertion ASTs and wait limits are unchanged. A controlled timeout probe, without launching a blocked thread, failed before correction with both locks held at shutdown and passed afterward with both released. The 17 controls tests passed in 0.894 seconds.
- **SPEC-1:** Database-failure cancellation alone did not prove the canceller responds to a readable durable hold. Two new broker tests inject real pause/emergency-stop sidecar requests during the admitted adapter call and run the captured real canceller. They assert the blocked boolean was read before cancellation, helper join and capability retirement, pending billing and retained allowance debit, and same/new-operation refusal without another outbound call. The three broker tests passed in 0.213 seconds. An interpreter-only mutant that ignores the true boolean produced exactly the two expected cancellation-assertion failures while database-error coverage passed. Controlled scheduling ends the mutant after one read, without waiting for TTL or launching a thread.

No production modules or CI settings changed during correction. Retained probes live outside the repository at `/home/ops/.hermes/fresh-forge-evidence/issue21/test-refactor-review/`. Final candidate-bound continuation reviews and focused verification are reported with delivery rather than transferring these earlier working-tree runs to a later commit.

The original CI run `38550256750` remains cancelled at its 15-minute limit: one 269-test shard passed, the other did not report completion, and compile was skipped. This refactoring is not evidence that CI passed. No new remote run, publication, merge or native/model execution has been performed. Fresh simplification/review and final exact-candidate focused verification are recorded separately in the delivery response.
