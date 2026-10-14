# Core test refactoring coverage map

Scope: only the five assigned test modules and this new map. Production, CI, existing documentation, AGENTS, original skill/document pins and source identities are unchanged by this lane.

Starting HEAD: `b0196edd7a149b9097a9e3346579f2a6a900e891`; tree: `0a8c8dac54e36d03eaa92e9928276028ce5e3bc2`. Verification binds the dirty owned-file fingerprints, not another lane’s mutable worktree.

Legacy helper APIs (`IntakeLifecycleTests`, `RepositoryLifecycleTests`, `PublicationTests`, `configuration`, `DeliveryTarget.attach`) are retained without discoverable legacy tests. Rewritten tests do not call those legacy setup/CLI/HTTP helpers. `DeliveryTarget.value` is only an explicit metadata data source. No imported TestCase aliases, broad skips, services, native workers/model operations, Git subprocesses, or timeout/CI changes were added.

## Verification scope

Canonical focused command:

```sh
mise exec -- python -m unittest factory_v1.tests.test_intake factory_v1.tests.test_repositories factory_v1.tests.test_publication factory_v1.tests.test_attention factory_v1.tests.test_delivery -v
```

Each component setup denies socket and child-process creation. The retained verification runner additionally denies socket/process creation before discovery, blocks legacy helper setup, reconciles unique test IDs, captures exact owned-file byte/mode fingerprints before/after, and writes actual exit/runtime evidence outside the repository. This is focused unit/component evidence, not full-suite/CI, real interruption, Docker-worker, platform send, live GitHub or deployment acceptance. Independent review and serialized integration remain the parent lane’s responsibility.

## Per-file old → new map

Old names below are the complete original discoverable inventory; new names are methods of the corresponding `*ComponentTests` class unless fully qualified. Multiple old journeys collapse into one explicit invariant matrix rather than losing negative coverage.

### `factory_v1/tests/test_intake.py` — 15 legacy tests → 10 focused tests

Real `validate_intake`, `register_iteration` and `read_iteration`; reopened file-backed SQLite. A few wrapper-only input/state/permission checks call `main` in-process, without a successful lifecycle (registration is mocked only for the permission-specific file-opening test). No process-start/restart claim.

| Old test(s) | New guarded seam test(s) |
| --- | --- |
| `test_approved_iteration_survives_a_fresh_controller_process`<br>`test_public_correlations_survive_restart_without_claiming_a_worker_run` | `test_approved_intake_reopens_with_exact_provenance_and_no_execution` |
| `test_unapproved_or_ambiguous_intake_is_refused_without_creating_state`<br>`test_invalid_github_owner_syntax_is_refused_without_creating_state` | `test_invalid_approval_scope_origin_and_owner_are_refused_before_storage` |
| `test_repeated_registration_is_idempotent_and_conflicting_replay_is_refused`<br>`test_adding_a_work_item_to_a_registered_intake_is_a_conflict_not_a_crash`<br>`test_removing_a_work_item_from_a_registered_intake_is_a_conflict` | `test_idempotent_registration_and_all_optional_scope_conflicts` |
| `test_concurrent_approved_products_keep_one_active_and_one_paused` | `test_serialized_products_have_one_active_iteration` |
| `test_missing_iteration_inspection_is_read_only_and_reports_not_found` | `test_missing_inspection_is_read_only`<br>`test_cli_input_and_persistence_refusals_in_process_without_journey` |
| `test_malformed_or_missing_intake_has_a_structured_error_and_no_state` | `test_duplicate_and_parser_limit_json_are_not_admitted`<br>`test_cli_input_and_persistence_refusals_in_process_without_journey` |
| `test_new_state_is_private_even_when_the_caller_umask_is_permissive` | `test_cli_private_state_creation_seam_under_permissive_umask` |
| `test_oversized_json_integer_has_a_structured_refusal_without_state`<br>`test_deeply_nested_json_has_a_structured_refusal_without_state` | `test_duplicate_and_parser_limit_json_are_not_admitted` |
| `test_valid_github_owner_boundaries_preserve_the_exact_repository` | `test_valid_owner_boundaries_keep_exact_case_and_bytes` |
| `test_nonpersistent_state_targets_are_refused_without_creating_state` | `test_cli_input_and_persistence_refusals_in_process_without_journey` |

### `factory_v1/tests/test_repositories.py` — 33 legacy tests → 13 focused tests

Real `onboard`/`reconcile` and metadata adapter/parser with a mocked GitHub request or opener. Pending creation and returned IDs are read from a separately reopened connection at the mutation/readback seam. Lost-response and SystemExit effect-return interruption replace kill/server journeys; no claim of real process-death verification.

| Old test(s) | New guarded seam test(s) |
| --- | --- |
| `test_null_successful_preflight_never_authorizes_creation`<br>`test_truncated_preflight_is_an_actionable_blocker`<br>`test_ambiguous_or_parser_limit_metadata_is_a_structured_blocker`<br>`test_deep_external_json_is_an_actionable_blocker`<br>`test_invalid_adapter_configuration_is_refused_before_network` | `test_adapter_configuration_and_raw_response_refusals_without_network` |
| `test_nonpositive_metadata_identity_is_refused`<br>`test_missing_selected_existing_target_never_creates_a_replacement`<br>`test_malformed_repository_metadata_is_a_structured_refusal` | `test_existing_absent_or_invalid_identity_never_creates_replacement` |
| `test_truncated_creation_response_reconciles_without_second_post`<br>`test_nonpositive_identity_cannot_reconcile_lost_creation_response`<br>`test_process_death_after_creation_reconciles_committed_intent`<br>`test_reconciliation_cannot_recreate_a_missing_uncertain_target`<br>`test_lost_creation_response_is_durable_and_reconciled_before_retry` | `test_lost_or_interrupted_creation_retains_intent_and_never_reposts` |
| `test_truncated_readback_preserves_returned_creation_id`<br>`test_successful_post_id_must_match_readback_and_restart`<br>`test_successful_creation_with_denied_readback_remains_reconcilable`<br>`test_created_target_requires_exact_private_owner_name_marker_readback` | `test_successful_post_identity_survives_failed_readback_and_replacement` |
| `test_trusted_creation_receipt_recovers_lost_state_with_exact_id_readback`<br>`test_receipt_requires_exact_trusted_scope_before_network`<br>`test_receipt_recovery_never_creates_an_absent_target`<br>`test_receipt_cannot_adopt_another_repository_id` | `test_receipt_shape_binding_absence_and_immutable_id` |
| `test_invalid_successful_post_id_cannot_authorize_later_adoption`<br>`test_definitive_creation_rejection_cannot_adopt_a_colliding_repository` | `test_invalid_post_identity_and_definitive_rejection_remain_blocked` |
| `test_colliding_new_target_is_not_adopted_even_with_matching_marker`<br>`test_alternative_account_is_not_guessed_or_created` | `test_collision_and_unresolved_account_refuse_before_mutation` |
| `test_reconciliation_refuses_a_different_capability_endpoint`<br>`test_verified_repository_cannot_be_replaced_by_same_name_and_marker` | `test_verified_identity_and_endpoint_cannot_be_replaced` |
| `test_only_required_metadata_is_retained_not_server_credentials`<br>`test_selected_existing_repository_is_read_back_without_creation` | `test_existing_exact_metadata_reopens_without_creation_or_secret_retention` |
| `test_removing_creation_intent_on_replay_is_a_scope_conflict` | `factory_v1.tests.test_intake.IntakeComponentTests.test_idempotent_registration_and_all_optional_scope_conflicts` |
| `test_concurrent_onboarding_serializes_the_creation_boundary` | `test_concurrent_direct_onboarding_has_one_creation` |
| `test_wrong_operator_cannot_use_recorded_creation_approval` | `test_wrong_operator_onboarding_refuses_before_capability_use` |
| `test_approved_new_product_creates_private_in_resolved_account` | `test_created_private_account_and_intent_are_durable_before_write` |

### `factory_v1/tests/test_publication.py` — 41 legacy tests → 21 focused tests

Real `publish`, configuration, assembly, adapter, commit/PR validators and durable journal. Two explicit baseline/source files and one admitted assignment replace nested assignment/worker/tracker setup. Only GitHub request is mocked; real object bytes are independently hashed with hashlib, retaining the terminal LF and opaque tree/gitlink/symlink identities. Host attestation is explicitly component-only, never native provenance.

| Old test(s) | New guarded seam test(s) |
| --- | --- |
| `test_happy_restart_published_never_done_and_no_token_leak` | `test_happy_publication_durable_exact_commit_pr_and_no_delivery_authority` |
| `test_exact_commit_message_readback_with_and_without_terminal_lf`<br>`test_git_independently_verifies_tree_with_untouched_unsupported_entries` | `test_exact_git_wire_hashes_preserve_terminal_lf_and_opaque_entries` |
| `test_commit_readback_mismatch_keeps_pending_intent_before_ref_or_pr` | `test_commit_readback_matrix_retains_intent_and_prevents_ref_pr` |
| `test_lost_ref_response_reconciles_same_target_after_restart`<br>`test_lost_pr_response_reconciles_same_target_after_restart` | `test_lost_ref_or_pr_response_reconciles_without_second_effect` |
| `test_update_same_branch_pr_is_nonforce` | `test_second_candidate_uses_same_pr_nonforce_update` |
| `test_concurrent_publications_serialize_one_intent` | `test_concurrent_publishers_share_one_durable_intent_and_pr` |
| `test_private_config_missing_or_worker_mounted_refused`<br>`test_authorization_in_each_excluded_scope_refused_before_http_or_state`<br>`test_authorization_hardlinks_in_each_excluded_scope_refused_before_http_or_state` | `test_private_config_and_all_worker_mount_exclusions` |
| `test_artifact_authorization_dotdot_alias_refused_before_http_or_state`<br>`test_protected_scope_aliases_refused_before_http_or_state`<br>`test_private_outside_authorization_valid_but_aliases_refused` | `test_authorization_and_protected_scope_aliases_refuse_before_reads` |
| `test_scope_role_repository_branch_issue_pin_denials_before_writes` | `test_scope_repository_branch_issue_candidate_and_binding_pin_refusals` |
| `test_worker_report_is_not_trusted_execution`<br>`test_review_role_is_readonly_even_with_private_approval`<br>`test_profile_denial_and_execution_pin_denial`<br>`test_complete_runtime_flags_do_not_replace_private_execution_attestation` | `test_worker_reports_review_roles_execution_and_profile_pins_are_not_authority` |
| `test_paused_stopped_stale_and_competing_scope_denied`<br>`test_stop_marker_refused`<br>`test_conflicting_persisted_ticket_claim_is_not_stolen` | `test_pause_stop_stale_assignment_and_competing_claim_refuse` |
| `test_manifest_bytes_modes_and_links_denied` | `test_manifest_bytes_modes_and_symlink_refuse_before_remote_writes` |
| `test_policy_held_but_implementation_tests_are_reviewable`<br>`test_explicit_operator_policy_path_can_be_planned`<br>`test_gate_configurations_and_tooling_require_operator_path`<br>`test_legitimate_behavior_and_test_delta_can_publish` | `test_policy_gates_are_held_but_product_behavior_and_tests_are_publishable` |
| `test_secret_and_suid_detection`<br>`test_source_and_json_bounds_refuse_before_remote_writes` | `test_secret_suid_source_and_json_bounds_are_refused` |
| `test_remote_repository_issue_base_and_branch_replacement_refused`<br>`test_pr_mismatch_refused_before_new_mutations`<br>`test_pr_nested_malformed_metadata_and_base_sha_drift_are_refused` | `test_remote_repository_body_issue_base_branch_and_pr_refusals` |
| `test_pending_approval_cannot_be_replaced`<br>`test_journal_candidate_and_pr_identity_drift_are_held_without_rewrite`<br>`test_published_branch_and_commit_drift_are_not_replayed` | `test_pending_approval_journal_and_published_identity_cannot_be_rewritten` |
| `test_deletion_and_addition_are_exact_scope`<br>`test_addition_cannot_replace_opaque_tree_or_link` | `test_add_delete_scope_and_unsupported_entry_replacement` |
| `test_adapter_denies_arbitrary_operations_and_payload_before_network` | `test_adapter_denies_arbitrary_operation_and_nonapproved_payload` |
| `test_malformed_config_fields_and_json_refuse_without_state_changes`<br>`test_malformed_persistent_json_and_shapes_refuse_without_mutations` | `test_malformed_configuration_persistent_json_and_invalid_database_no_effect` |
| `test_invalid_state_never_creates_sqlite_or_lock` | `test_nonpersistent_missing_corrupt_and_empty_state_refuse_without_creation` |

### `factory_v1/tests/test_attention.py` — 23 legacy tests → 17 focused tests

Real lifecycle/enqueue/deliver/reconcile/respond/load_record and real HermesTransport rendering/parsing. Only subprocess.run is mocked; intent is reopened before the mocked send returns or raises SystemExit. Typed receipts/response consistency is tested directly as well as at durable lifecycle seams. Direct planner/grant calls exercise shared-lock preservation; the verified onboarding record is explicit state, not a third onboarding journey.

| Old test(s) | New guarded seam test(s) |
| --- | --- |
| `test_intent_correlations_restart_and_silent_routine_operations` | `test_enqueue_exact_correlations_reopen_and_routine_silence` |
| `test_duplicate_conflict_and_new_event_or_decision` | `test_idempotence_event_conflict_and_unique_decision_identity` |
| `test_malformed_event_json_and_context_fail_closed` | `test_event_negative_matrix_preserves_empty_outbox`<br>`test_origin_approval_and_correlation_contracts_fail_closed`<br>`test_corrupt_iteration_json_is_rejected_before_send_or_outbox_mutation` |
| `test_missing_persisted_repository_refused_and_valid_context_recovers`<br>`test_null_persisted_repository_refused_and_valid_context_recovers`<br>`test_non_string_empty_and_malformed_persisted_repository_refused`<br>`test_valid_repository_grammar_is_shared_by_intake_and_attention` | `test_invalid_persisted_repository_refuses_existing_and_new_event_then_recovers` |
| `test_origin_contract_preserves_intake_and_attention_errors_without_effects`<br>`test_malformed_persisted_identity_and_approval_refuse_without_send` | `test_origin_approval_and_correlation_contracts_fail_closed` |
| `test_exact_supported_command_inert_mentions_ack_is_not_receipt` | `test_transport_exact_command_privacy_ack_is_not_receipt` |
| `test_unavailable_or_unapproved_config_never_sends` | `test_unapproved_or_absent_transport_never_spends_attempt` |
| `test_known_no_effect_can_retry_after_restart`<br>`test_no_effect_reconciliation_survives_retry_with_new_attempt` | `test_no_effect_retry_new_attempt_and_uncertain_effect_no_blind_retry` |
| `test_unknown_and_malformed_results_do_not_retry_or_leak` | `test_transport_known_no_effect_unknown_and_malformed_are_distinct`<br>`test_no_effect_retry_new_attempt_and_uncertain_effect_no_blind_retry` |
| `test_receipt_shape_binding_conflicts_and_origin_drift`<br>`test_ack_silence_unrelated_chat_cannot_authorize_pending_decision`<br>`test_completion_and_exact_pending_reply_do_not_start_another_iteration` | `test_receipt_and_response_negative_matrices_do_not_authorize_execution` |
| `test_malformed_persisted_notifications_refuse_all_lifecycle_operations` | `test_corrupt_durable_notifications_fail_all_real_lifecycle_seams` |
| `test_ambiguous_and_parser_limit_iteration_json_refuse_all_attention_actions` | `test_corrupt_iteration_json_is_rejected_before_send_or_outbox_mutation` |
| `test_inconsistent_persisted_receipts_and_responses_refuse_all_actions` | `test_corrupt_durable_notifications_fail_all_real_lifecycle_seams`<br>`test_semantic_receipt_and_decision_consistency_without_unrelated_guards` |
| `test_actual_process_death_before_and_after_effect_requires_reconciliation` | `test_interruption_at_send_return_preserves_uncertain_intent_before_and_after_effect` |
| `test_concurrent_enqueue_send_response_and_spending_writes` | `test_concurrent_enqueue_send_and_response_share_one_durable_send`<br>`test_planning_onboarding_and_grant_writes_preserve_attention_and_remain_silent` |
| `test_planning_and_onboarding_remain_silent_and_concurrent_writes_preserve_both` | `test_planning_onboarding_and_grant_writes_preserve_attention_and_remain_silent` |

### `factory_v1/tests/test_delivery.py` — 15 legacy tests → 15 focused tests

Real observe/sample/inspect, decoders, evidence/incident validators and HTTP adapter status handling. Only external HTTP readback/open is mocked. Exact-health read-return drift targets the last coherence guard. An SQLite trigger proves rollback of evidence, incident and outbox. Repeated direct observations replace sleeping watcher polls; watcher argument refusal remains an in-process wrapper check.

| Old test(s) | New guarded seam test(s) |
| --- | --- |
| `test_public_healthy_failure_restart_outage_branch_green_and_verified_clearance` | `test_durable_incident_unknown_holds_idempotence_and_exact_clearance` |
| `test_ci_cd_and_deployment_failures_pending_skipped_unknown_freeze` | `test_ci_workflow_deployment_failure_pending_skipped_and_unknown_matrix` |
| `test_unknown_first_sample_freezes_once_concurrent_polling_is_idempotent` | `test_concurrent_direct_sampling_creates_one_unknown_incident` |
| `test_bounded_watcher_repeats_after_state_transition` | `test_repeated_observations_replace_sleeping_watcher` |
| `test_main_deployment_and_check_races_fail_closed` | `test_late_main_check_workflow_and_deployment_races_never_claim_health` |
| `test_malformed_paginated_ambiguous_and_oversized_readback_never_healthy`<br>`test_nonfinite_and_invalid_unicode_readbacks_freeze_and_recover` | `test_raw_json_boundaries_ambiguity_nonfinite_unicode_and_pagination` |
| `test_large_external_evidence_fields_remain_bounded_and_restart_clearable` | `test_large_untrusted_evidence_remains_bounded_and_restart_clearable` |
| `test_corrupt_archived_evidence_cannot_be_promoted_or_clear_an_incident` | `test_corrupt_archived_evidence_cannot_promote_pointer_or_clear_incident` |
| `test_new_durable_records_must_satisfy_reader_before_atomic_save` | `test_new_records_validate_before_atomic_save` |
| `test_health_redirects_http_errors_and_wrong_contract_refused` | `test_health_status_and_exact_contract_refusals`<br>`test_http_adapter_status_decode_and_unreachable_boundary_without_socket` |
| `test_configuration_shapes_pins_private_file_and_persisted_state_refused`<br>`test_config_duplicate_parser_limits_and_read_capability_refuse` | `test_configuration_private_file_parser_pin_and_read_capability_refusals`<br>`test_persisted_monitor_incident_evidence_and_outbox_corruption_refuse_before_reads` |
| `test_persisted_evidence_incident_freeze_and_attention_corruption_refuse_before_reads` | `test_persisted_monitor_incident_evidence_and_outbox_corruption_refuse_before_reads` |
| `test_atomic_attention_failure_does_not_commit_partial_monitor_incident_or_evidence` | `test_notification_failure_rolls_back_monitor_incident_and_evidence` |

## Guard sensitivity (separate from native acceptance)

The five extra sensitivity methods first exercise normal refusal/safety, then use an isolated in-memory replacement/code object removing only the named guard; production files are never edited:

- Intake: malformed external repository admitted when only `valid_repository` is ungated.
- Repository creation: a pending returned ID 123 is wrongly replaced by ID 999 when only the created-ID comparison is removed.
- Publication: exact SHA/tree/parent with a drifted message is admitted when only message equality is removed; byte hashing retains the real final LF.
- Attention: accepted intent sends twice when only the no-automatic-resend guard is removed.
- Delivery: drift during the final health-read return is incorrectly marked healthy when only last coherent readback is removed. The semantic healthy-evidence matrix recomputes its digest before removing checks, preventing a checksum guard from masking missing checks.

These mutation observations demonstrate guard sensitivity, not a passing mutated product or real external execution. Refusal/error matrices retain exact scope/body/pin/identity, no new write/intents where guarded, truthful uncertain effects, private attention destinations/content, durable receipts, and no automatic execution/merge/closure authority.

## Retained execution evidence

- Guarded focused command: `PYTHONDONTWRITEBYTECODE=1 mise exec -- python /home/ops/.hermes/cache/scratch/verify-core-refactor.py` — 76 unique tests, exit 0; all five assigned modules completed, no skips, zero calls to denied socket/process/action boundaries. The receipt captures runtime and before/after owned-file byte/mode equality: `/home/ops/.hermes/cache/scratch/core-refactor-final-receipt.json`.
- Separate mutation command: `PYTHONDONTWRITEBYTECODE=1 mise exec -- python /home/ops/.hermes/cache/scratch/probe-core-guards.py` — each of five targeted tests passes normally and has exactly one expected assertion failure (zero execution/validation errors) when only its guard is removed. Receipt: `/home/ops/.hermes/cache/scratch/core-guard-sensitivity.json`. These re-exercised IDs are not added to the focused unique-test count.
- Five assigned Python files compile in the mise interpreter; `git diff --check` passes for the owned tests. No project type-checker is configured in mise and neither mypy nor pyright is installed there; no new checker/framework was installed.
- Retained compatibility methods and `configuration` have identical ASTs to the starting snapshot. The guard runner prevents any retained legacy setup/HTTP attach from running.

## Replaced and unrun layers

Real subprocess restart/kill, loopback HTTP servers, worker/controller fixture chains and Git hash subprocess checks have been replaced by reopened connections, synchronous effect-return interruption, external-boundary mocks and independent hashes over actual object bytes. OS kill/transport termination, services, native/model workers and live external readback are not proved by these tests. Full suite and CI are intentionally not run by this lane; no earlier full-suite/CI result is attributed to this changed candidate.
