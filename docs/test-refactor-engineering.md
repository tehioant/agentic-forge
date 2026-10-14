# Engineering test refactor

## Scope and evidence

Only `factory_v1/tests/test_{assignments,review,simplification,diagnosis,merge}.py`
and this new document belong to this lane. Production, CI, shared documentation,
AGENTS, original source/spec fixtures and selected instruction pins are unchanged.
Starting HEAD: `b0196edd7a149b9097a9e3346579f2a6a900e891`; committed starting tree:
`0a8c8dac54e36d03eaa92e9928276028ce5e3bc2`.

The original five-module inventory contained 99 discovered methods. It is replaced
by 71 unique direct unit/component IDs: assignments 13, review 12, simplification
6, diagnosis 22, merge 18. This is not a before/after runtime comparison: the
prohibited old journeys were not executed. Existing direct report/validator
matrices remain; repeated HTTP/CLI/process/worker chains are no longer discovered.

The sibling-facing `AssignmentTests`, `ReviewTests`, `SimplificationTests`,
`DiagnosisLifecycleTests` and `MergeTests` fixture methods keep their original
names/signatures and method ASTs, but contain no discovered `test_*` methods.
They are compatibility fixtures, not part of the new execution paths. No imported
TestCase alias is introduced. `LocalStageEvidence` remains a local filesystem
validator fixture, not a launcher: its exited-owner fields let real durable
control inspection read a complete runtime record. No new shared worker framework
or repository runner is added.

## Coverage map

Names below are test suffixes in the named module's `*ComponentTests` class,
unless a retained validator/policy class is explicitly named.

### Assignments

| Replaced old coverage family | Real seam and replacement assertions |
| --- | --- |
| Prepare/inspect/restart/exact replay; repair closure order; deterministic dry run | `test_real_claim_dry_replay_restart_and_repair_order`: real `prepare`/`inspect`, file-backed SQLite reopen, zero dry writes, canonical skill ordering, stable identity/digest, execution disabled. |
| Startup fields, workspace/profile aliases, provider/model/role/capability restrictions | `test_validation_identity_profiles_paths_and_capabilities`: real bounded-request validator; malformed identity/path and out-of-role capabilities refuse before tracker access. |
| Missing/ambiguous/unknown/changed selected skills, dependencies and stage binding | `test_real_skill_closure_and_stage_bindings`: real snapshot/closure validation against unchanged original fixture pins for all seven roles; read-only review/diagnosis capabilities. |
| Repository/spec/issue identity/body; blockers, deferred/foreign/active work, partial publication | `test_scope_and_frontier_refuse_before_claim`: real admission checks against explicit externally-read frontier rows, including inadmissible rows and exact identity/body pin mismatches; no stored claim. Frontier construction itself is an external prerequisite here. |
| Missing/exhausted/expired/wrong-scope/approval-only grants; uncertain model operation and quota | `test_grants_exact_scope_expiry_exhaustion_and_uncertainty`: real grant persistence and preview, no reservation/model call; all refusals precede frontier reads. |
| One ticket/profile ownership; reservation conflicts/replay; concurrent preparation/reservation | `test_claims_reservations_profile_conflicts_and_diagnosis_independence` and `test_direct_concurrent_claims_share_real_state_lock_without_workers`: real claims/reservation correlation plus two direct admissions through the real SQLite state lock. Independent diagnosis does not steal ticket ownership. No reservation publication chain is replayed. |
| Changed issue/spec revision; stale inspection; distinct requirement/stuck holds; launch disabled | `test_revision_inspection_and_result_independent_holds`: real inspection and activity guards, no reuse of obsolete pins, public launch refusal. |
| Structurally stored results are not authority; exact replay and immutable prior result | `test_result_truthful_untrusted_replay_and_conflict`: real result save/readback; structurally valid but untrusted, no advance/close; replacement refuses and preserves the original. |
| Wrong result identity, selected loads, artifacts/work/tests/status | `test_result_identity_loads_artifacts_and_tests_matrix`: real correlated result validation; no submitted result on refusal. |
| Pause with inspectable original evidence, including final result admission race | `test_late_pause_blocks_claim_and_result_but_keeps_inspection`: a real pause sidecar is requested at external issue read-return, before final result save; private modes, preserved handoff and no accepted result. |
| Duplicate JSON/nested shapes, structured refusal | `test_direct_json_duplicate_hook_and_nested_request_shapes`: direct actual parser duplicate hook and actual bounded-request validator, no command dispatch. |
| Released direction bound to each affected issue's own body/identity | `test_released_direction_binds_each_affected_identity_and_body`: direct prepare from released-record inputs; mismatched identity/body never supplies direction. |

### Review

| Replaced old coverage family | Real seam and replacement assertions |
| --- | --- |
| Two independent read-only axes; preparation evidence and controller aggregation | Retained `ReviewValidatorTests.test_exact_axis_pin_schema_findings_stuckness_and_readonly_evidence`, plus `test_pair_independence_checks_and_resealed_pin_matrix`: actual axis/result schema, source/baseline/check pins, completion and same-candidate pair guard; altered pins are resealed so digest drift does not mask pairing refusal. |
| No worker assertion/host authority shortcut; private authorization, saved replay, transcript binding | `test_pair_authority_replay_and_private_exact_binding` and `test_authorization_mode_mount_and_pin_drift_refuse`: actual pair, evaluation, private host authorization and fingerprint checks; exact durable reopen/readback and byte drift refusal; accepted review still cannot merge or close. |
| Rejection feedback, fresh corrections, candidate supersession invalidating both axes | `test_exact_correction_findings_keep_scope_and_require_both_fresh_axes` and `test_candidate_receipts_supersession_and_independent_holds`: exact actual corrections handoff and required implementation-check/simplify/Standards/Spec rerun list; completed corrective record supersedes the old candidate. |
| Lone rejection, all completed adverse axes, old approvals/later passing retries cannot erase holds | `test_lone_adverse_axis_holds_old_and_later_passing_pairs` and `test_all_completed_reject_stuck_policy_and_correction_debug_remain_distinct`: actual atomic hold write/readback, candidate-wide scan, retained reject/stuck/policy findings and corrective debug evidence. Removing an adverse record does not clear its durable hold. |
| Candidate scope/private checks, legitimate test changes and policy direction | `test_real_staging_and_policy_test_path_derivation` and `test_exact_policy_path_direction_does_not_hide_test_changes`: actual local candidate receipt/tree reads, actual copy/staging, derived policy/test paths and exact operator policy scope. |
| Bounded shared execution fingerprints, missing response, unsafe source paths | `test_shared_execution_fingerprints_refuse_unbounded_symlink_or_missing_response`: actual review/merge fingerprint equivalence, response requirement, size bound and symlink refusal. Hash inputs are explicitly local, not native attestations. |
| Pause/stop cannot accept review output | `test_pause_and_stopped_axis_never_authorize_acceptance`: actual private pause sidecar and stop-file guard; no saved review authorization. |

Review pair/evaluation fixtures mock only already-admitted tracker/profile/spending
revalidation (`assignments.prepare`), outside the pairing/report/hold/authorization
invariant. They never build implementation → simplification → worker-review chains.

### Simplification

| Replaced old coverage family | Real seam and replacement assertions |
| --- | --- |
| Tested implementation required; exact handoff, candidate instead of host HEAD, dry/replay/no-op | Retained `SimplificationValidatorTests.test_private_receipt_implementation_status_and_drift_matrix` plus `test_real_staging_copies_verified_candidate_and_keeps_baseline`: real implementation reader, controller-derived preceding/diff/handoff and actual copy; unverified status/source/baseline refuses, no host-head leakage. |
| Four angles, behavior preservation, findings not approval, no-op versus cleanup/scope violation | Retained `SimplificationValidatorTests.test_result_pins_json_four_angles_and_behavior_matrix`: actual validator with source manifests, actual bounded JSON, all four angles and changed paths. |
| Private check receipts, malformed/failed/raw-byte drift | `test_resealed_receipt_semantics_not_only_raw_checksum` plus retained receipt matrix: semantic edits are checksum-resealed; wrong run/provenance, failed/boolean exits, missing records and truncated output refuse independently of raw-byte checksum drift. |
| Failed/malformed attempt or caller replacement cannot supply completed stage evidence | `test_stage_result_only_accepts_launcher_correlation_or_exact_completed_replay`: real result admission, validator and SQLite save; exact completed replay succeeds as untrusted, caller replacement and failed/running/unconfirmed/stopped runtime inputs refuse without changing retained evidence. Tracker eligibility alone is a mocked prerequisite here. |
| Stop/pause at admission and prelaunch, no accepted candidate | `test_controller_receipt_stop_and_sidecar_pause_refuse_without_staging`: actual private pause record, inspectable receipt and actual stop-file refusal; no worker launch or hanging checks. |

### Diagnosis

| Replaced old coverage family | Real seam and replacement assertions |
| --- | --- |
| Red-loop investigation is diagnosis, not repair; schema/pins/attempts/uncertainty/hypotheses/recommendations; raw terminal command/results | All 15 original `ReportPolicyTests` methods remain with unchanged ASTs. They call actual report/result/terminal-evidence validators, including forged green evidence against retained red output and read-only tree/baseline drift. |
| Responsible stuck result distinct from crash, declaration/restart and independent work | `test_responsible_stuck_source_retains_attempts_and_refuses_crash_or_forgery` and `test_declare_transitive_scope_durable_retirement_and_independent_progress`: actual source/declaration readers, actual transitive blocker closure and SQLite checkpoints; only affected descendants Blocked, independent ticket stays Ready; exact replay does not repeat status writes. |
| Independent diagnostic handoff and failed-candidate staging | `test_exact_independent_handoff_and_real_staging`: actual pinned failure handoff and filesystem copy; scope drift refuses, read-only/no native authority. |
| Operator retry/revise, silence/paused/stale replay; release and recovery | `test_direction_silence_revise_and_exact_replay_never_imply_retry`, `test_retry_release_intent_survives_partial_external_effect_then_reconciles`, `test_pause_blocks_declaration_without_retiring_or_status_mutation`: actual response validation, direction application, private control refusal and release checkpoints; release intent persists before an injected lost status response, exact re-read reconciliation avoids duplicate effects, old assignments are superseded. |
| Overlapping holds and each affected issue's exact body | `test_overlapping_release_preserves_other_hold_and_exact_issue_body`: actual status-release policy; another unresolved hold remains Blocked; altered body pin permits no writes. |

Diagnosis mocks only external board/issue/status IO. Direction application additionally
receives a labeled already-completed independent native-diagnosis prerequisite and
an authenticated transport read fixture; actual host-file binding, response correlation,
retry/revise policy, durable intent and retirement guards remain real. These inputs do
not establish native execution or a real operator decision.

### Merge

| Replaced old coverage family | Real seam and replacement assertions |
| --- | --- |
| Delivery-health/outage race and coherent final metadata movement | `test_delivery_incident_at_final_sample_freezes_ordinary_merge` and `test_final_branch_main_and_check_drift_never_admit_intent`: actual final lifecycle guards after injected read-return changes; no durable merge intent or PUT. |
| Requirement/spec/document drift at final protection read; exact issue identity/body | `test_final_publication_document_drift_blocks_intent_and_put` and `test_final_issue_identity_or_body_drift_refuses_unsafe_writes`: actual scoped requirement-publication equality and exact work-issue guard after final remote reads; no intent or writes. |
| Old approved pair cannot override rejection, including lone adverse axis | Actual candidate-wide adverse hold regressions live in the review module; `test_final_review_admission_cannot_use_earlier_pass` additionally isolates final merge review admission from an earlier accepted read. |
| Intent before effects, lost merge response, automatic closure and truthful reopened receipt | `test_intent_precedes_effect_and_lost_merge_autoclose_reopens_truthfully`: actual narrow `MergeGitHub` route, actual intent persistence, exact PR/issue reads, immediate reopen without delivery proof, one merge and one reopen despite lost responses/replay. |
| Pause at last read; uncertain open outcome after pause/restart; duplicate controller retry | `test_late_pause_after_last_protection_read_blocks_intent_and_put` and `test_pending_uncertain_intent_never_resends_even_while_paused`: actual private pause sidecar and pending journal; no new intent after pause and no blind resend of uncertain admitted effects. Shared-lock concurrency is isolated in assignment admission rather than replaying full controllers. |
| Changed requirements/identity during merge must not close or write wrong target | `test_changed_requirements_during_autoclosure_reopen_with_incomplete_receipt` and `test_identity_changed_after_merge_persists_hold_without_wrong_issue_write`: actual incomplete journal, retained integration evidence, exact issue reopening for requirement drift, zero unsafe PATCH on identity drift. |
| Branch-only/manual closure and branch-only checks are not delivery; integrated checks/closure/replay | `test_actual_merge_capability_denies_unscoped_write_and_branch_only_closure` and `test_integrated_checks_only_delivery_and_lost_closure_readback`: actual route restrictions, authoritative app/head checks and precise delivered readback; closure receipt only after integrated checks, no duplicate merge/closure. |
| Strict/changed/unsupported protection, failed/missing/unknown/stale checks | `test_checks_and_resealed_protection_semantic_matrix`: actual protection/check validators with checksum-resealed weakened protection; no transport or writes. |
| Native/source/tree/mode evidence; private config/controller state and fixed scope | `test_controller_config_private_scope_and_fixed_intent` plus actual shared fingerprint tests in review: private file/mount checks, fixed journal authority and retained file fingerprints. Native execution and Git byte/mode assembly are mocked prerequisites, not claimed coverage. |
| Failed main cannot be hidden by green candidate; exact incident repair only | `test_exact_repair_scope_keeps_checks_reviews_and_incident`: actual ordinary-merge freeze/recovery binding; repair retains candidate/integrated checks and final independent review requirements, never clears the incident or claims deployment. |
| Integrated tree/parents/main membership; later main drift versus removed delivery | `test_real_integrated_tree_parents_and_membership_are_not_branch_proof` and `test_prior_delivery_receipts_survive_unknown_main_drift_without_reopening`: actual integration validator and lifecycle; unknown drift preserves historical receipts in revalidation-required without reopening, proven baseline restoration reopens as incomplete. |
| Reopened delivered work cannot lose delivery status/receipts at an intermediate save | `test_reopened_delivery_checkpoint_keeps_prior_receipts_before_close_response`: actual durable checkpoint is read at PATCH return boundary; prior delivered status, integration and checks remain intact. |

Merge tests invoke one actual lifecycle seam from minimal persisted inputs. Native
chain authority, Git byte/mode assembly, previously admitted review and delivery
sampling are explicitly labeled prerequisite mocks. Exact configured HTTP reads/writes
are simulated at `GitHub.request` with no HTTP server; actual `MergeGitHub` capability,
config validation, requirement equality, checks/protection, controls, journal writes,
integration and receipt guards remain real. No worker/controller precursor chain runs.

## Verification commands

Canonical owned module command (no full discovery):

```sh
mise exec -- python -m unittest factory_v1.tests.test_assignments factory_v1.tests.test_review factory_v1.tests.test_simplification factory_v1.tests.test_diagnosis factory_v1.tests.test_merge -v
```

Executed instrumented equivalent under Python 3.14.8:

```sh
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 mise exec -- python /home/ops/.hermes/cache/scratch/engineering-focused.py
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 mise exec -- python /home/ops/.hermes/cache/scratch/engineering-mutations.py
```

The final focused run completed with exit 0: **71 unique tests passed in 2.775 s**
(2.832 s including module load/verification overhead), using mise Python 3.14.8.
The seven expected-red sensitivity probes completed with exit 0 in 0.391 s;
every mutant caused assertion failures without unrelated errors. Compilation of
the five owned files completed with exit 0, and their scoped `git diff --check`
passed. All 46 production blob/mode identities match the supplied starting
`b0196edd7a149b9097a9e3346579f2a6a900e891`, and before/after verification
fingerprints are identical.

The scratch-only verifier loads exactly these five modules, reconciles unique IDs,
blocks socket creation/networking, HTTP, subprocess/system/fork/spawn operations,
and compares pre/post content and modes of all 46 production paths and the five
owned modules. It records the full ID inventory and fingerprints in
`engineering-focused-result.json`. This wrapper is verification evidence, not a
new CI runner or test framework.

Seven separate in-memory sensitivity probes require assertion failures, with no
unrelated errors, after removal of: late durable-control admission; unresolved
candidate hold retention; semantic controller-check receipt validation; launcher
correlation/exact stage replay; unavailable diagnosis-loop blocker policy; final
scoped publication revalidation; and preservation of prior delivered checkpoint
status. They edit no production bytes. Their expected-red output lives separately
in `engineering-mutation-result.json`, and is not counted as additional green tests.

Full-suite, process-death, Docker/native-worker/model/socket termination, actual Git
publication and live GitHub/deployment layers remain unrun. Compatibility helpers
are retained but not executed by this lane. Missing real-worker/live acceptance is
not supplied by these component fixtures. Serialized integration and independent
Standards/Spec review belong to the parent lane; this work stages, commits, pushes
and merges nothing.
