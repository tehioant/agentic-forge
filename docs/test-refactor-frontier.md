# Frontier test-only component refactor

## Scope and boundaries

Only the seven assigned test modules and this new evidence file belong to this stream. Production, CI, immutable original skill/spec pins, AGENTS and existing focused admission/lean-ticket/bug-admission/revision-integration/control modules are not edited. No staging, commit or push occurs.

The old inventory contains **127 test methods**; the replacement inventory contains **100 unique discovered test IDs**. Rows below map every old method, including behavior consolidated into subtests or a sibling component. Helpers import test modules, not `TestCase` aliases, so discovery does not silently duplicate tests.

`FrontierFixture` is a frontier-only explicit three-issue SQLite fixture with mocked external GitHub reads/writes. It does not start onboarding/HTTP/CLI/worker/model lifecycles. Document/skill bytes are read or copied as immutable inputs, never repinned. Legacy callable `TicketTests` helpers (`setUp`, `control`, `publish`, `completion`, `inspect`, `ok`) remain; `control` is a function facade and `publish` returns the already-declared precursor. The focused runner does not validate unassigned legacy fixture consumers.

Crash/response-loss tests check durable intent **before** the external effect, then read exact objects and replay without duplicate mutation. Old scheduler/process-concurrency journeys are replaced by deterministic locked stale-snapshot reload or durable replay/one-reservation invariants, not operating-system scheduling coverage. Declared external precursor records do not purport to be native execution or source-publication evidence.

## Verified command

```text
mise exec -- python /home/ops/.hermes/cache/scratch/frontier-refactor/run_focused.py
```

The scratch runner loads only `test_planning`, `test_tickets`, `test_bugs`, `test_ticket_native_scope`, `test_requirements`, `test_requirement_bugs`, and `test_requirement_merge`, verifies discovery uniqueness, and denies `socket.*`, `subprocess.Popen`, shell execution, process spawning and forks with a Python audit hook. Result: **exit 0; 100 tests; 3.166s; 0 failures; 0 errors; 0 skips**. No full suite, services, E2E, real Git operations, workers or native/model operations are exercised.

Raw command/ID/result evidence: `/home/ops/.hermes/cache/scratch/frontier-refactor/focused-result.json`.

## Per-file old → new map

Replacement identifiers below are relative to `factory_v1.tests`. A mapping is a behavioral seam replacement, not a claim that the old transport/controller journey still runs.

### `factory_v1/tests/test_planning.py`

`planning.assign/complete`, selected-skill resolution and exact publication read-back, with explicit onboarding/SQLite rows and API responses. Stale concurrent snapshots are interleaved deterministically at real locked reload seams, not with HTTP threads.

Old methods: 15; new unique methods: 12.

| Old method | Direct replacement method(s) |
| --- | --- |
| `PlanningTests.test_assignment_uses_actual_selected_skills_in_same_conversation` | `test_planning.PlanningTests.test_assignment_uses_actual_selected_skills_in_same_conversation` |
| `PlanningTests.test_missing_answers_remain_pending_without_publication_access` | `test_planning.PlanningTests.test_missing_answers_remain_pending_without_publication_access` |
| `PlanningTests.test_readback_hands_off_only_github_references_and_is_restart_idempotent` | `test_planning.PlanningTests.test_readback_hands_off_only_github_references_and_is_restart_idempotent` |
| `PlanningTests.test_missing_ambiguous_wrong_name_and_changed_skill_pins` | `test_planning.PlanningTests.test_missing_ambiguous_wrong_name_and_changed_skill_pins` |
| `PlanningTests.test_docs_issue_and_commit_mismatches_never_complete` | `test_planning.PlanningTests.test_docs_issue_and_commit_mismatches_never_complete` |
| `PlanningTests.test_new_assignment_invalidates_old_handoff_and_stale_completion` | `test_planning.PlanningTests.test_new_assignment_invalidates_handoff_ticket_stage_and_reservation` |
| `PlanningTests.test_concurrent_onboarding_cannot_restore_superseded_assignment_or_handoff` | `test_planning.PlanningTests.test_locked_reload_ignores_stale_onboarding_snapshot_for_assignment_and_completion` |
| `PlanningTests.test_concurrent_onboarding_cannot_discard_acknowledged_completion` | `test_planning.PlanningTests.test_locked_reload_ignores_stale_onboarding_snapshot_for_assignment_and_completion` |
| `PlanningTests.test_identity_endpoint_and_completed_issue_continuity` | `test_planning.PlanningTests.test_identity_endpoint_and_completed_issue_continuity` |
| `PlanningTests.test_malformed_inputs_origin_and_stale_request_are_refused` | `test_planning.PlanningTests.test_malformed_origin_stale_revision_parent_and_completed_replacement_refused` |
| `PlanningTests.test_assignment_requires_onboarding_and_exact_revision` | `test_planning.PlanningTests.test_assignment_requires_onboarding_and_exact_revision` |
| `PlanningTests.test_changed_skill_file_after_assignment_blocks_completion_without_reads` | `test_planning.PlanningTests.test_changed_skill_after_assignment_blocks_completion_without_reads` |
| `PlanningTests.test_wrong_endpoint_and_parent_issue_are_refused` | `test_planning.PlanningTests.test_malformed_origin_stale_revision_parent_and_completed_replacement_refused` |
| `PlanningTests.test_completed_snapshot_cannot_be_overwritten_or_reset_by_pending` | `test_planning.PlanningTests.test_malformed_origin_stale_revision_parent_and_completed_replacement_refused` |
| `PlanningTests.test_wrong_operator_cannot_assign_or_complete` | `test_planning.PlanningTests.test_wrong_operator_is_refused_at_current_state_seam` |

### `factory_v1/tests/test_tickets.py`

`validate_batch`, `validate_execution`, `complete_synthesis`, `pinned_inputs`, `publish`, `frontier`, and `reserve`. Independent direct inputs replace the old synthesize→complete→publish→reserve controller chain. One-ticket publisher checks are distinct from batch graph validation. Intent/receipt durability is read using a fresh SQLite connection.

Old methods: 30; new unique methods: 18.

| Old method | Direct replacement method(s) |
| --- | --- |
| `TicketTests.test_synthesis_publication_frontier_and_one_reservation` | `test_tickets.TicketTests.test_batch_orders_blockers_and_rejects_invalid_scope_and_cycles`<br>`test_tickets.TicketTests.test_single_ticket_publication_retains_exact_body_and_no_dispatch`<br>`test_tickets.TicketTests.test_one_reservation_replays_same_run_and_refuses_sibling` |
| `TicketTests.test_repeated_completion_and_publication_do_not_reset_progress` | `test_tickets.TicketTests.test_repeat_completion_retains_publication_and_external_progress` |
| `TicketTests.test_authoritative_changes_block_defer_and_remove_scope` | `test_ticket_native_scope.NativeScopeTests.test_textual_holds_and_foreign_scope_leave_independent_frontier` |
| `TicketTests.test_github_board_not_local_generated_batch_is_backlog_authority` | `test_tickets.TicketTests.test_unknown_dependency_and_membership_intents_block_without_repeated_effect` |
| `TicketTests.test_missing_ambiguous_inaccessible_or_changed_board_is_durable_blocker` | `test_ticket_native_scope.NativeScopeTests.test_board_selection_schema_and_access_fail_closed` |
| `TicketTests.test_lost_issue_response_reconciles_without_duplicate` | `test_tickets.TicketTests.test_lost_creation_response_preserves_intent_before_effect_and_replay` |
| `TicketTests.test_absent_uncertain_issue_is_not_retried` | `test_tickets.TicketTests.test_unknown_creation_is_never_retried_and_known_marker_is_adopted` |
| `TicketTests.test_lost_native_dependency_membership_and_field_responses_reconcile` | `test_tickets.TicketTests.test_late_pause_after_status_receipt_refuses_reserved_advancement`<br>`test_ticket_native_scope.NativeScopeTests.test_native_single_issue_publication_and_lost_membership_receipt` |
| `TicketTests.test_reservation_lost_response_is_durable_and_reconciles` | `test_tickets.TicketTests.test_lost_reservation_status_response_reconciles_durable_run` |
| `TicketTests.test_catalog_prebuilt_or_stale_synthesis_is_refused` | `test_tickets.TicketTests.test_execution_requires_complete_actual_loads_and_result`<br>`test_tickets.TicketTests.test_stale_completion_and_changed_inputs_leave_durable_state_unchanged` |
| `TicketTests.test_bad_contract_unknown_blockers_and_cycles_are_refused` | `test_tickets.TicketTests.test_batch_orders_blockers_and_rejects_invalid_scope_and_cycles` |
| `TicketTests.test_exact_issue_and_native_relationship_readbacks` | `test_tickets.TicketTests.test_exact_issue_body_and_identity_readback_fail_closed`<br>`test_ticket_native_scope.NativeScopeTests.test_native_textual_disagreement_and_cycles_refuse_durable_frontier` |
| `TicketTests.test_concurrent_reservations_are_serialized` | `test_tickets.TicketTests.test_one_reservation_replays_same_run_and_refuses_sibling` |
| `TicketTests.test_skill_changes_and_material_planning_revision_invalidate_stage` | `test_tickets.TicketTests.test_stale_completion_and_changed_inputs_leave_durable_state_unchanged`<br>`test_tickets.TicketTests.test_selected_skill_bytes_are_revalidated_without_repinning`<br>`test_planning.PlanningTests.test_new_assignment_invalidates_handoff_ticket_stage_and_reservation` |
| `TicketTests.test_explicit_linked_board_resolves_ambiguity_but_unlinked_is_refused` | `test_ticket_native_scope.NativeScopeTests.test_board_selection_schema_and_access_fail_closed` |
| `TicketTests.test_missing_schema_never_omits_board_or_creates_issues` | `test_tickets.TicketTests.test_missing_board_retains_execution_and_prevents_issue_intent`<br>`test_ticket_native_scope.NativeScopeTests.test_board_selection_schema_and_access_fail_closed` |
| `TicketTests.test_changed_selected_instructions_block_completion` | `test_tickets.TicketTests.test_selected_skill_bytes_are_revalidated_without_repinning` |
| `TicketTests.test_unknown_requirement_is_refused` | `test_tickets.TicketTests.test_batch_orders_blockers_and_rejects_invalid_scope_and_cycles` |
| `TicketTests.test_pending_publication_cannot_reserve_already_created_slice` | `test_tickets.TicketTests.test_partial_publication_and_foreign_active_refuse_reservation` |
| `TicketTests.test_external_active_scope_and_changed_reserved_blockers_refuse_admission` | `test_tickets.TicketTests.test_partial_publication_and_foreign_active_refuse_reservation`<br>`test_tickets.TicketTests.test_unknown_dependency_and_membership_intents_block_without_repeated_effect` |
| `TicketTests.test_unchanged_parent_and_exact_scope_progress_readback` | `test_ticket_native_scope.NativeScopeTests.test_completed_blocker_requires_closed_completed_done_current_scope`<br>`test_ticket_native_scope.NativeScopeTests.test_native_scope_missing_ambiguous_inaccessible_and_replaced_identity` |
| `TicketTests.test_process_death_after_issue_write_reconciles_committed_intent` | `test_tickets.TicketTests.test_lost_creation_response_preserves_intent_before_effect_and_replay` |
| `TicketTests.test_linked_board_pagination_finds_unambiguous_selected_board` | `test_ticket_native_scope.NativeScopeTests.test_connection_pagination_and_project_shape_validation` |
| `TicketTests.test_malformed_execution_load_shapes_are_safe_cli_refusals` | `test_tickets.TicketTests.test_execution_requires_complete_actual_loads_and_result` |
| `TicketTests.test_malformed_project_and_issue_shapes_are_safe_cli_refusals` | `test_ticket_native_scope.NativeScopeTests.test_connection_pagination_and_project_shape_validation` |
| `TicketTests.test_external_contract_requires_substance_and_current_references` | `test_tickets.TicketTests.test_external_contract_requires_substance_current_refs_and_unambiguous_sections` |
| `TicketTests.test_generated_contract_rejects_the_same_missing_substance` | `test_tickets.TicketTests.test_batch_orders_blockers_and_rejects_invalid_scope_and_cycles` |
| `TicketTests.test_native_blocker_identity_mismatch_refuses_completed_board_credit` | `test_ticket_native_scope.NativeScopeTests.test_completed_blocker_requires_closed_completed_done_current_scope`<br>`test_ticket_native_scope.NativeScopeTests.test_exact_native_blocker_identity_checked_outside_board_scope` |
| `TicketTests.test_native_blocker_identity_is_checked_even_outside_board_scope` | `test_ticket_native_scope.NativeScopeTests.test_exact_native_blocker_identity_checked_outside_board_scope` |
| `TicketTests.test_wrong_endpoint_refused_before_external_access` | `test_tickets.TicketTests.test_authoritative_board_additions_and_changed_reserved_blockers_refuse` |

### `factory_v1/tests/test_bugs.py`

Discovery/result/comparison/render validators plus `synthesize`, `complete_synthesis`, `affected_edges`, `verify_affected`, `publish_locked`, `published_observation`, `assigned`, and `frontier`/reservation refusals. Predeclared ready work replaces the feature-planning→ticket→bug lifecycle. Exact body bytes, CRLF/whitespace/footer preservation, unknown writes, revised/original scoped authority and immutable replay are separate components.

Old methods: 32; new unique methods: 21.

| Old method | Direct replacement method(s) |
| --- | --- |
| `BugTests.test_current_bug_common_contract_native_downstream_and_independent_frontier` | `test_bugs.BugTests.test_single_current_bug_publication_then_read_only_replay_and_completed_release` |
| `BugTests.test_affected_heading_whitespace_completion_and_replay` | `test_bugs.BugTests.test_affected_body_whitespace_crlf_and_footer_bytes_survive_edge_update` |
| `BugTests.test_affected_heading_leading_whitespace_completion_and_replay` | `test_bugs.BugTests.test_affected_body_whitespace_crlf_and_footer_bytes_survive_edge_update` |
| `BugTests.test_affected_content_whitespace_completion_and_replay` | `test_bugs.BugTests.test_affected_body_whitespace_crlf_and_footer_bytes_survive_edge_update` |
| `BugTests.test_affected_section_crlf_lost_issue_response_replay` | `test_bugs.BugTests.test_affected_body_whitespace_crlf_and_footer_bytes_survive_edge_update`<br>`test_bugs.BugTests.test_creation_effect_crash_and_membership_field_lost_receipts_reconcile_once` |
| `BugTests.test_affected_body_crlf_lost_patch_response_replay` | `test_bugs.BugTests.test_affected_body_whitespace_crlf_and_footer_bytes_survive_edge_update`<br>`test_bugs.BugTests.test_lost_body_effect_replay_retains_intent_without_repeated_patch` |
| `BugTests.test_stale_affected_text_intent_refuses_native_mutation_on_replay` | `test_bugs.BugTests.test_stale_affected_intent_refuses_native_mutation` |
| `BugTests.test_affected_scope_checked_before_publication_complete_and_retry` | `test_bugs.BugTests.test_final_affected_readback_requires_body_native_identity_membership_scope`<br>`test_bugs.BugTests.test_new_observation_and_missing_board_refuse_creation_without_discarding_result` |
| `BugTests.test_ready_current_bug_is_required_and_completion_releases_affected_work` | `test_bugs.BugTests.test_single_current_bug_publication_then_read_only_replay_and_completed_release` |
| `BugTests.test_deferred_and_recovery_never_join_normal_scope_or_reserve` | `test_bugs.BugTests.test_noncurrent_unknown_and_duplicate_normalized_scope_never_reserve` |
| `BugTests.test_held_bug_scope_whitespace_and_crlf_never_allow_public_reservation` | `test_bugs.BugTests.test_noncurrent_unknown_and_duplicate_normalized_scope_never_reserve` |
| `BugTests.test_malformed_bug_scope_never_allows_public_reservation` | `test_bugs.BugTests.test_noncurrent_unknown_and_duplicate_normalized_scope_never_reserve` |
| `BugTests.test_duplicate_normalized_bug_scope_fails_closed_through_public_cli` | `test_bugs.BugTests.test_noncurrent_unknown_and_duplicate_normalized_scope_never_reserve` |
| `BugTests.test_repeat_and_new_discovery_ids_use_same_assignment_and_no_duplicate` | `test_bugs.BugTests.test_synthesis_replay_new_ids_and_added_scope_refusal_are_durable` |
| `BugTests.test_lost_issue_response_and_new_id_reconcile_same_issue` | `test_bugs.BugTests.test_creation_effect_crash_and_membership_field_lost_receipts_reconcile_once` |
| `BugTests.test_absent_uncertain_issue_cannot_be_recreated_under_new_id` | `test_bugs.BugTests.test_unknown_creation_current_or_archived_prevents_duplicate_write` |
| `BugTests.test_scope_comparison_and_semantic_duplicate_reuse` | `test_bugs.BugTests.test_semantic_duplicates_require_symptoms_and_scope_not_title`<br>`test_bugs.BugTests.test_fingerprint_normalizes_defect_but_keeps_scope` |
| `BugTests.test_malformed_contract_evidence_and_cycles_leave_state_unchanged` | `test_bugs.BugTests.test_result_contract_scope_references_comparisons_and_cycles` |
| `BugTests.test_discovery_validation_id_conflict_and_changed_skill` | `test_bugs.BugTests.test_discovery_requires_observation_and_justified_scope`<br>`test_bugs.BugTests.test_completion_revalidates_adaptation_load_before_publication` |
| `BugTests.test_new_observation_between_synthesis_and_create_blocks_no_blind_duplicate` | `test_bugs.BugTests.test_new_observation_and_missing_board_refuse_creation_without_discarding_result` |
| `BugTests.test_lost_board_body_and_dependency_responses_reconcile` | `test_bugs.BugTests.test_lost_body_effect_replay_retains_intent_without_repeated_patch`<br>`test_bugs.BugTests.test_edge_intent_durable_and_unknown_absent_edge_not_retried`<br>`test_bugs.BugTests.test_creation_effect_crash_and_membership_field_lost_receipts_reconcile_once` |
| `BugTests.test_exact_body_scope_status_and_affected_patch_readbacks` | `test_bugs.BugTests.test_final_affected_readback_requires_body_native_identity_membership_scope` |
| `BugTests.test_crash_after_issue_effect_reuses_committed_intent` | `test_bugs.BugTests.test_creation_effect_crash_and_membership_field_lost_receipts_reconcile_once` |
| `BugTests.test_uncertain_creation_blocks_reworded_new_discovery` | `test_bugs.BugTests.test_unknown_creation_current_or_archived_prevents_duplicate_write` |
| `BugTests.test_explicit_bug_blockers_and_affected_edges_are_both_native` | `test_bugs.BugTests.test_edge_intent_durable_and_unknown_absent_edge_not_retried` |
| `BugTests.test_published_readbacks_refuse_drift_and_recover_without_writes` | `test_bugs.BugTests.test_published_observation_drift_and_huge_text_blocker_are_read_only` |
| `BugTests.test_oversized_numeric_blocker_readback_blocks_and_recovers_without_writes` | `test_bugs.BugTests.test_published_observation_drift_and_huge_text_blocker_are_read_only` |
| `BugTests.test_evidence_only_unknown_environment_and_future_vision_reference` | `test_bugs.BugTests.test_discovery_requires_observation_and_justified_scope`<br>`test_bugs.BugTests.test_render_retains_contract_evidence_environment_and_priority` |
| `BugTests.test_synthesis_pins_empty_dependencies_and_actual_adaptation_load` | `test_bugs.BugTests.test_completion_revalidates_adaptation_load_before_publication` |
| `BugTests.test_additional_affected_scope_is_not_silently_discarded` | `test_bugs.BugTests.test_synthesis_replay_new_ids_and_added_scope_refusal_are_durable` |
| `BugTests.test_missing_projects_keeps_synthesis_and_refuses_writes` | `test_bugs.BugTests.test_new_observation_and_missing_board_refuse_creation_without_discarding_result` |
| `BugTests.test_concurrent_publish_retries_serialize` | `test_bugs.BugTests.test_single_current_bug_publication_then_read_only_replay_and_completed_release`<br>`test_bugs.BugTests.test_creation_effect_crash_and_membership_field_lost_receipts_reconcile_once` |

### `factory_v1/tests/test_ticket_native_scope.py`

Direct `ticket_github` schema/board/membership/blocker/pagination validators, and real frontier/reserve/single-issue publication. Fixtures declare GitHub option IDs explicitly; closed/completed/Done and exact node identity are independent native conditions.

Old methods: 10; new unique methods: 9.

| Old method | Direct replacement method(s) |
| --- | --- |
| `NativeScopeTests.test_three_state_publication_frontier_and_one_reservation` | `test_ticket_native_scope.NativeScopeTests.test_three_state_frontier_and_one_reservation` |
| `NativeScopeTests.test_blocked_deferred_foreign_work_does_not_hide_independent_frontier` | `test_ticket_native_scope.NativeScopeTests.test_textual_holds_and_foreign_scope_leave_independent_frontier` |
| `NativeScopeTests.test_done_requires_closed_completed_and_current_native_scope` | `test_ticket_native_scope.NativeScopeTests.test_completed_blocker_requires_closed_completed_done_current_scope` |
| `NativeScopeTests.test_missing_inaccessible_ambiguous_scope_blocks_before_issue_writes` | `test_ticket_native_scope.NativeScopeTests.test_native_scope_missing_ambiguous_inaccessible_and_replaced_identity` |
| `NativeScopeTests.test_missing_ambiguous_inaccessible_board_preserves_synthesis` | `test_ticket_native_scope.NativeScopeTests.test_board_selection_schema_and_access_fail_closed` |
| `NativeScopeTests.test_lost_acknowledgements_reconcile_exact_mock_targets` | `test_ticket_native_scope.NativeScopeTests.test_native_single_issue_publication_and_lost_membership_receipt`<br>`test_tickets.TicketTests.test_lost_creation_response_preserves_intent_before_effect_and_replay` |
| `NativeScopeTests.test_membership_dependency_and_progress_lost_acknowledgements` | `test_ticket_native_scope.NativeScopeTests.test_native_single_issue_publication_and_lost_membership_receipt`<br>`test_tickets.TicketTests.test_late_pause_after_status_receipt_refuses_reserved_advancement`<br>`test_bugs.BugTests.test_edge_intent_durable_and_unknown_absent_edge_not_retried` |
| `NativeScopeTests.test_crash_after_board_write_reconciles_persisted_intent` | `test_ticket_native_scope.NativeScopeTests.test_native_single_issue_publication_and_lost_membership_receipt` |
| `NativeScopeTests.test_crash_after_issue_write_reconciles` | `test_tickets.TicketTests.test_lost_creation_response_preserves_intent_before_effect_and_replay` |
| `NativeScopeTests.test_reservation_lost_response_is_durable` | `test_tickets.TicketTests.test_lost_reservation_status_response_reconciles_durable_run` |

### `factory_v1/tests/test_requirements.py`

`record_change`, `resume_change`, `sync_blocks`, `verify_publication`, `updated_body`, host `configuration`, `run_stage`, and the publication component with prerecorded document-versioning/spec-issue prerequisites. Synthesis validation/document publication prerequisites are stubbed only in the targeted final-publication and lost-update tests; their final tracker verification and admission remain real. Stage execution is an inert external function seam, never a program/model invocation. A late pause and a genuine SQLite dependency exception are separate tests, both retaining committed receipts without admitting execution.

Old methods: 19; new unique methods: 20.

| Old method | Direct replacement method(s) |
| --- | --- |
| `RequirementChangeTests.test_material_hold_invalidates_old_assignment_and_dependents_but_releases_independent_claim` | `test_requirements.RequirementChangeTests.test_material_hold_retires_assignments_dependents_bugs_and_reservation` |
| `RequirementChangeTests.test_selected_execution_versions_readbacks_and_explicit_resume_feed_fresh_inputs` | `test_requirements.RequirementChangeTests.test_scope_authority_and_input_selection_keep_independent_original_inputs`<br>`test_requirements.RequirementChangeTests.test_stage_records_validated_execution_and_reuses_exact_inputs`<br>`test_requirements.RequirementChangeTests.test_unknown_commit_intent_never_creates_another_version`<br>`test_requirements.RequirementChangeTests.test_updated_issue_effect_lost_response_replays_without_repeated_patch`<br>`test_requirements.RequirementChangeTests.test_release_preserves_prior_blocked_deferred_done_and_normalizes_active` |
| `RequirementChangeTests.test_minor_trace_revalidates_unchanged_evidence_without_status_mutations` | `test_requirements.RequirementChangeTests.test_minor_trace_requires_validity_without_status_writes_or_material_release` |
| `RequirementChangeTests.test_unresolved_manual_artifacts_and_wrong_operator_cannot_synthesize_or_resume` | `test_requirements.RequirementChangeTests.test_wrong_operator_stale_revision_and_unresolved_artifacts_refuse` |
| `RequirementChangeTests.test_lost_updated_ticket_response_reconciles_exact_effect_without_repeated_patch` | `test_requirements.RequirementChangeTests.test_updated_issue_effect_lost_response_replays_without_repeated_patch` |
| `RequirementChangeTests.test_missing_changed_skill_and_executor_refuse_without_publication_or_fallback` | `test_requirements.RequirementChangeTests.test_host_executor_byte_permissions_and_arguments_are_checked_without_execution`<br>`test_tickets.TicketTests.test_selected_skill_bytes_are_revalidated_without_repinning` |
| `RequirementChangeTests.test_missing_execution_receipts_persist_uncertain_attempt_without_automatic_retry` | `test_requirements.RequirementChangeTests.test_missing_execution_evidence_retains_receipt_and_denies_automatic_retry` |
| `RequirementChangeTests.test_published_revision_cannot_resume_an_unconfirmed_old_worker` | `test_requirements.RequirementChangeTests.test_unconfirmed_old_worker_cleanup_prevents_resume` |
| `RequirementChangeTests.test_partial_status_restoration_keeps_local_hold_until_explicit_resume_replay` | `test_requirements.RequirementChangeTests.test_partial_status_restore_replays_same_pending_hold_without_repeat_write` |
| `RequirementChangeTests.test_final_ready_write_membership_drift_retains_resume_pending_hold` | `test_requirements.RequirementChangeTests.test_post_status_membership_body_native_and_spec_drift_retains_pending_hold` |
| `RequirementChangeTests.test_final_ready_write_native_and_textual_drift_retains_resume_pending_hold` | `test_requirements.RequirementChangeTests.test_post_status_membership_body_native_and_spec_drift_retains_pending_hold` |
| `RequirementChangeTests.test_final_blocked_write_publication_drift_cannot_become_published` | `test_requirements.RequirementChangeTests.test_final_blocked_status_write_cannot_finalize_drifted_publication` |
| `RequirementChangeTests.test_requirement_only_resume_preserves_prior_blocked_progress` | `test_requirements.RequirementChangeTests.test_release_preserves_prior_blocked_deferred_done_and_normalizes_active` |
| `RequirementChangeTests.test_requirement_only_resume_preserves_prior_deferred_progress` | `test_requirements.RequirementChangeTests.test_release_preserves_prior_blocked_deferred_done_and_normalizes_active` |
| `RequirementChangeTests.test_revised_delivered_work_is_reopened_without_restoring_done_or_admitting_it` | `test_requirements.RequirementChangeTests.test_record_reopens_delivered_work_but_keeps_it_blocked` |
| `RequirementChangeTests.test_requirement_resume_does_not_overwrite_new_external_deferred_progress` | `test_requirements.RequirementChangeTests.test_requirement_release_cannot_overwrite_independent_new_progress` |
| `RequirementChangeTests.test_requirement_resume_normalizes_prior_active_to_ready_not_active_dispatch` | `test_requirements.RequirementChangeTests.test_release_preserves_prior_blocked_deferred_done_and_normalizes_active` |
| `RequirementChangeTests.test_publication_drift_prevents_resume_and_retains_hold_across_restart` | `test_requirements.RequirementChangeTests.test_post_status_membership_body_native_and_spec_drift_retains_pending_hold` |
| `StuckRequirementRevisionTests.test_correlated_revise_direction_resynthesizes_and_releases_only_linked_hold` | `test_requirements.StuckRequirementRevisionTests.test_correlated_resume_releases_only_linked_revise_hold` |

### `factory_v1/tests/test_requirement_bugs.py`

Scoped discovery synthesis/input selection, `assigned`, `settle_retired`, `held_numbers`, frontier/reservation and independently declared current/archived history. Published revised/original authority behavior is shared by the direct bug publication component, not a repeated requirements→worker→bug chain. Four already-small history tests remain as their own class.

Old methods: 13; new unique methods: 11.

| Old method | Direct replacement method(s) |
| --- | --- |
| `RequirementBugTests.test_revision_retires_old_bug_assignment_and_replay_requires_fresh_synthesis` | `test_requirement_bugs.RequirementBugTests.test_obsolete_result_ids_refused_current_and_archived_after_restart` |
| `RequirementBugTests.test_mixed_pending_discovery_keeps_unrevised_sibling_held` | `test_requirement_bugs.RequirementBugTests.test_archived_and_current_pending_discoveries_hold_every_affected_issue` |
| `RequirementBugTests.test_fresh_publication_settles_archived_discovery_without_reusing_old_result` | `test_requirement_bugs.RequirementBugTests.test_retired_refresh_preserves_history_scope_and_uses_each_scoped_authority`<br>`test_requirement_bugs.RequirementBugTests.test_fresh_settlement_releases_discovery_only_not_older_unknown_effect` |
| `RequirementBugTests.test_new_bug_for_revised_issue_completes_and_publishes_with_current_inputs` | `test_bugs.BugTests.test_revised_and_original_affected_scopes_publish_under_their_own_authority` |
| `RequirementBugTests.test_mixed_original_and_revised_affected_issues_keep_each_scoped_authority` | `test_requirement_bugs.RequirementBugTests.test_retired_refresh_preserves_history_scope_and_uses_each_scoped_authority`<br>`test_bugs.BugTests.test_revised_and_original_affected_scopes_publish_under_their_own_authority` |
| `RequirementBugTests.test_independent_bug_synthesis_is_not_retired_by_unrelated_revision` | `test_requirement_bugs.RequirementBugTests.test_independent_current_assignment_is_not_invalidated_by_unrelated_authority` |
| `RequirementBugTests.test_retired_uncertain_creation_remains_a_no_duplicate_write_guard` | `test_requirement_bugs.RequirementBugTests.test_archived_unknown_creation_survives_later_settlement_and_closed_blocker` |
| `RequirementBugTests.test_fresh_duplicate_settlement_cannot_erase_archived_unknown_creation` | `test_bugs.BugTests.test_fresh_duplicate_adoption_cannot_erase_archived_unknown_creation` |
| `RequirementBugTests.test_retired_discovery_cannot_silently_drop_an_affected_issue_on_refresh` | `test_requirement_bugs.RequirementBugTests.test_retired_refresh_cannot_drop_affected_scope` |
| `BugHistoryHoldTests.test_archived_discovery_holds_without_a_current_record` | `test_requirement_bugs.BugHistoryHoldTests.test_archived_discovery_holds_without_a_current_record` |
| `BugHistoryHoldTests.test_every_archived_pending_effect_survives_fresh_settlement` | `test_requirement_bugs.BugHistoryHoldTests.test_every_archived_pending_effect_survives_fresh_settlement` |
| `BugHistoryHoldTests.test_fresh_settlement_cannot_drop_uncovered_scope` | `test_requirement_bugs.BugHistoryHoldTests.test_fresh_settlement_cannot_drop_uncovered_scope` |
| `BugHistoryHoldTests.test_known_settlement_survives_retirement_but_cannot_settle_newer_discovery` | `test_requirement_bugs.BugHistoryHoldTests.test_known_settlement_survives_retirement_but_cannot_settle_newer_discovery` |

### `factory_v1/tests/test_requirement_merge.py`

`review.authority`, `requirements.validity/released_claim`, `merge.inspect`, and `assignments.prepare` final protection-read admission. Local controller-check bytes and declared SQLite assignment evidence replace worker/CLI/review/merge journeys; no native candidate, actual Git, worker, or merge publication runs.

Old methods: 8; new unique methods: 9.

| Old method | Direct replacement method(s) |
| --- | --- |
| `RequirementReviewTests.test_material_revision_before_review_refuses_old_candidate` | `test_requirement_merge.RequirementReviewTests.test_material_revision_before_review_refuses_old_candidate_on_durable_reload` |
| `RequirementMergeTests.test_material_revision_after_approval_refuses_merge_even_after_restart` | `test_requirement_merge.RequirementMergeTests.test_material_revision_after_approval_refuses_result_and_merge_inspection_retains_history` |
| `RequirementMergeTests.test_independent_approved_candidate_retains_evidence_after_explicit_validity_checks` | `test_requirement_merge.RequirementMergeTests.test_independent_candidate_retains_exact_revision_evidence_when_other_issue_held` |
| `RequirementMergeTests.test_minor_prose_never_revalidates_changed_review_checks` | `test_requirement_merge.RequirementMergeTests.test_minor_prose_cannot_revalidate_changed_controller_check_bytes` |
| `RequirementMergeTests.test_native_blocker_drift_at_protection_read_refuses_merge` | `test_requirement_merge.RequirementMergeTests.test_native_textual_blocker_drift_at_protection_read_refuses_admission` |
| `RequirementMergeTests.test_board_scope_drift_at_protection_read_refuses_merge` | `test_requirement_merge.RequirementMergeTests.test_board_scope_drift_at_protection_read_refuses_admission` |
| `RequirementMergeTests.test_board_progress_drift_at_last_protection_read_refuses_merge` | `test_requirement_merge.RequirementMergeTests.test_board_progress_drift_at_last_protection_read_refuses_admission` |
| `RequirementMergeTests.test_exact_ticket_revision_at_last_protection_read_refuses_approved_candidate` | `test_requirement_merge.RequirementMergeTests.test_exact_ticket_body_drift_at_protection_read_refuses_admission` |

## Mutation sensitivity

```text
mise exec -- python /home/ops/.hermes/cache/scratch/frontier-refactor/mutate.py
```

Exit 0. In-memory AST mutations only; production files never change. Every missing-guard mutation caused assertion failures, with zero test errors. This checks sensitivity rather than weakening expectations.

| Removed/weakened real guard | Tests | Assertion failures | Errors |
| --- | ---: | ---: | ---: |
| `factory_v1.planning.complete:Published docs differ` | 1 | 14 | 0 |
| `factory_v1.tickets.frontier:Native/textual blockers disagree.` | 1 | 1 | 0 |
| `factory_v1.bugs.affected_edges:Affected text intent does not contain` | 1 | 1 | 0 |
| `factory_v1.bugs.assigned:Material revision retired` | 1 | 2 | 0 |
| `factory_v1.bugs.held_numbers:empty-holds` | 1 | 1 | 0 |
| `factory_v1.requirements.resume_change:last-publication` | 1 | 3 | 0 |
| `factory_v1.requirement_publication.publish:last-publication` | 1 | 1 | 0 |
| `factory_v1.requirement_execution.run_stage:receipt-only` | 2 | 2 | 0 |
| `factory_v1.assignments.prepare:Issue is blocked, wrong identity` | 2 | 2 | 0 |

Raw guard/test-ID/traceback evidence: `/home/ops/.hermes/cache/scratch/frontier-refactor/mutation-result.json`. In particular, removing final publication verification lets drift become published; bypassing final stage admission lets both a late pause and a late database error admit execution; dropping history holds makes affected work eligible; and dropping post-protection progress/scope validation permits assignment. The normal tests assert durable refusal states or unchanged assignment state in addition to the error.

## Unique discovery evidence

- `factory_v1.tests.test_planning.PlanningTests.test_assignment_requires_onboarding_and_exact_revision`
- `factory_v1.tests.test_planning.PlanningTests.test_assignment_uses_actual_selected_skills_in_same_conversation`
- `factory_v1.tests.test_planning.PlanningTests.test_changed_skill_after_assignment_blocks_completion_without_reads`
- `factory_v1.tests.test_planning.PlanningTests.test_docs_issue_and_commit_mismatches_never_complete`
- `factory_v1.tests.test_planning.PlanningTests.test_identity_endpoint_and_completed_issue_continuity`
- `factory_v1.tests.test_planning.PlanningTests.test_locked_reload_ignores_stale_onboarding_snapshot_for_assignment_and_completion`
- `factory_v1.tests.test_planning.PlanningTests.test_malformed_origin_stale_revision_parent_and_completed_replacement_refused`
- `factory_v1.tests.test_planning.PlanningTests.test_missing_ambiguous_wrong_name_and_changed_skill_pins`
- `factory_v1.tests.test_planning.PlanningTests.test_missing_answers_remain_pending_without_publication_access`
- `factory_v1.tests.test_planning.PlanningTests.test_new_assignment_invalidates_handoff_ticket_stage_and_reservation`
- `factory_v1.tests.test_planning.PlanningTests.test_readback_hands_off_only_github_references_and_is_restart_idempotent`
- `factory_v1.tests.test_planning.PlanningTests.test_wrong_operator_is_refused_at_current_state_seam`
- `factory_v1.tests.test_tickets.TicketTests.test_authoritative_board_additions_and_changed_reserved_blockers_refuse`
- `factory_v1.tests.test_tickets.TicketTests.test_batch_orders_blockers_and_rejects_invalid_scope_and_cycles`
- `factory_v1.tests.test_tickets.TicketTests.test_exact_issue_body_and_identity_readback_fail_closed`
- `factory_v1.tests.test_tickets.TicketTests.test_execution_requires_complete_actual_loads_and_result`
- `factory_v1.tests.test_tickets.TicketTests.test_external_contract_requires_substance_current_refs_and_unambiguous_sections`
- `factory_v1.tests.test_tickets.TicketTests.test_late_pause_after_status_receipt_refuses_reserved_advancement`
- `factory_v1.tests.test_tickets.TicketTests.test_lost_creation_response_preserves_intent_before_effect_and_replay`
- `factory_v1.tests.test_tickets.TicketTests.test_lost_reservation_status_response_reconciles_durable_run`
- `factory_v1.tests.test_tickets.TicketTests.test_missing_board_retains_execution_and_prevents_issue_intent`
- `factory_v1.tests.test_tickets.TicketTests.test_one_reservation_replays_same_run_and_refuses_sibling`
- `factory_v1.tests.test_tickets.TicketTests.test_partial_publication_and_foreign_active_refuse_reservation`
- `factory_v1.tests.test_tickets.TicketTests.test_real_pinned_inputs_reject_endpoint_repository_and_raw_document_drift`
- `factory_v1.tests.test_tickets.TicketTests.test_repeat_completion_retains_publication_and_external_progress`
- `factory_v1.tests.test_tickets.TicketTests.test_selected_skill_bytes_are_revalidated_without_repinning`
- `factory_v1.tests.test_tickets.TicketTests.test_single_ticket_publication_retains_exact_body_and_no_dispatch`
- `factory_v1.tests.test_tickets.TicketTests.test_stale_completion_and_changed_inputs_leave_durable_state_unchanged`
- `factory_v1.tests.test_tickets.TicketTests.test_unknown_creation_is_never_retried_and_known_marker_is_adopted`
- `factory_v1.tests.test_tickets.TicketTests.test_unknown_dependency_and_membership_intents_block_without_repeated_effect`
- `factory_v1.tests.test_bugs.BugTests.test_affected_body_whitespace_crlf_and_footer_bytes_survive_edge_update`
- `factory_v1.tests.test_bugs.BugTests.test_completion_revalidates_adaptation_load_before_publication`
- `factory_v1.tests.test_bugs.BugTests.test_creation_effect_crash_and_membership_field_lost_receipts_reconcile_once`
- `factory_v1.tests.test_bugs.BugTests.test_discovery_requires_observation_and_justified_scope`
- `factory_v1.tests.test_bugs.BugTests.test_edge_intent_durable_and_unknown_absent_edge_not_retried`
- `factory_v1.tests.test_bugs.BugTests.test_final_affected_readback_requires_body_native_identity_membership_scope`
- `factory_v1.tests.test_bugs.BugTests.test_fingerprint_normalizes_defect_but_keeps_scope`
- `factory_v1.tests.test_bugs.BugTests.test_fresh_duplicate_adoption_cannot_erase_archived_unknown_creation`
- `factory_v1.tests.test_bugs.BugTests.test_lost_body_effect_replay_retains_intent_without_repeated_patch`
- `factory_v1.tests.test_bugs.BugTests.test_new_observation_and_missing_board_refuse_creation_without_discarding_result`
- `factory_v1.tests.test_bugs.BugTests.test_noncurrent_unknown_and_duplicate_normalized_scope_never_reserve`
- `factory_v1.tests.test_bugs.BugTests.test_obsolete_current_and_archived_assignment_ids_never_admit_result`
- `factory_v1.tests.test_bugs.BugTests.test_published_observation_drift_and_huge_text_blocker_are_read_only`
- `factory_v1.tests.test_bugs.BugTests.test_render_retains_contract_evidence_environment_and_priority`
- `factory_v1.tests.test_bugs.BugTests.test_result_contract_scope_references_comparisons_and_cycles`
- `factory_v1.tests.test_bugs.BugTests.test_revised_and_original_affected_scopes_publish_under_their_own_authority`
- `factory_v1.tests.test_bugs.BugTests.test_semantic_duplicates_require_symptoms_and_scope_not_title`
- `factory_v1.tests.test_bugs.BugTests.test_single_current_bug_publication_then_read_only_replay_and_completed_release`
- `factory_v1.tests.test_bugs.BugTests.test_stale_affected_intent_refuses_native_mutation`
- `factory_v1.tests.test_bugs.BugTests.test_synthesis_replay_new_ids_and_added_scope_refusal_are_durable`
- `factory_v1.tests.test_bugs.BugTests.test_unknown_creation_current_or_archived_prevents_duplicate_write`
- `factory_v1.tests.test_ticket_native_scope.NativeScopeTests.test_board_selection_schema_and_access_fail_closed`
- `factory_v1.tests.test_ticket_native_scope.NativeScopeTests.test_completed_blocker_requires_closed_completed_done_current_scope`
- `factory_v1.tests.test_ticket_native_scope.NativeScopeTests.test_connection_pagination_and_project_shape_validation`
- `factory_v1.tests.test_ticket_native_scope.NativeScopeTests.test_exact_native_blocker_identity_checked_outside_board_scope`
- `factory_v1.tests.test_ticket_native_scope.NativeScopeTests.test_native_scope_missing_ambiguous_inaccessible_and_replaced_identity`
- `factory_v1.tests.test_ticket_native_scope.NativeScopeTests.test_native_single_issue_publication_and_lost_membership_receipt`
- `factory_v1.tests.test_ticket_native_scope.NativeScopeTests.test_native_textual_disagreement_and_cycles_refuse_durable_frontier`
- `factory_v1.tests.test_ticket_native_scope.NativeScopeTests.test_textual_holds_and_foreign_scope_leave_independent_frontier`
- `factory_v1.tests.test_ticket_native_scope.NativeScopeTests.test_three_state_frontier_and_one_reservation`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_final_blocked_status_write_cannot_finalize_drifted_publication`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_host_executor_byte_permissions_and_arguments_are_checked_without_execution`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_late_pause_keeps_truthful_stage_receipt_but_never_admits_result`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_late_sqlite_exception_keeps_receipt_without_admitting_execution`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_material_hold_retires_assignments_dependents_bugs_and_reservation`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_minor_trace_requires_validity_without_status_writes_or_material_release`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_missing_execution_evidence_retains_receipt_and_denies_automatic_retry`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_partial_status_restore_replays_same_pending_hold_without_repeat_write`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_post_status_membership_body_native_and_spec_drift_retains_pending_hold`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_record_reopens_delivered_work_but_keeps_it_blocked`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_release_preserves_prior_blocked_deferred_done_and_normalizes_active`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_requirement_release_cannot_overwrite_independent_new_progress`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_scope_authority_and_input_selection_keep_independent_original_inputs`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_stage_records_validated_execution_and_reuses_exact_inputs`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_unconfirmed_old_worker_cleanup_prevents_resume`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_unknown_commit_intent_never_creates_another_version`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_updated_body_preserves_unrelated_bytes_identity_blockers_and_footer`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_updated_issue_effect_lost_response_replays_without_repeated_patch`
- `factory_v1.tests.test_requirements.RequirementChangeTests.test_wrong_operator_stale_revision_and_unresolved_artifacts_refuse`
- `factory_v1.tests.test_requirements.StuckRequirementRevisionTests.test_correlated_resume_releases_only_linked_revise_hold`
- `factory_v1.tests.test_requirement_bugs.BugHistoryHoldTests.test_archived_discovery_holds_without_a_current_record`
- `factory_v1.tests.test_requirement_bugs.BugHistoryHoldTests.test_every_archived_pending_effect_survives_fresh_settlement`
- `factory_v1.tests.test_requirement_bugs.BugHistoryHoldTests.test_fresh_settlement_cannot_drop_uncovered_scope`
- `factory_v1.tests.test_requirement_bugs.BugHistoryHoldTests.test_known_settlement_survives_retirement_but_cannot_settle_newer_discovery`
- `factory_v1.tests.test_requirement_bugs.RequirementBugTests.test_archived_and_current_pending_discoveries_hold_every_affected_issue`
- `factory_v1.tests.test_requirement_bugs.RequirementBugTests.test_archived_unknown_creation_survives_later_settlement_and_closed_blocker`
- `factory_v1.tests.test_requirement_bugs.RequirementBugTests.test_fresh_settlement_releases_discovery_only_not_older_unknown_effect`
- `factory_v1.tests.test_requirement_bugs.RequirementBugTests.test_independent_current_assignment_is_not_invalidated_by_unrelated_authority`
- `factory_v1.tests.test_requirement_bugs.RequirementBugTests.test_obsolete_result_ids_refused_current_and_archived_after_restart`
- `factory_v1.tests.test_requirement_bugs.RequirementBugTests.test_retired_refresh_cannot_drop_affected_scope`
- `factory_v1.tests.test_requirement_bugs.RequirementBugTests.test_retired_refresh_preserves_history_scope_and_uses_each_scoped_authority`
- `factory_v1.tests.test_requirement_merge.RequirementMergeTests.test_board_progress_drift_at_last_protection_read_refuses_admission`
- `factory_v1.tests.test_requirement_merge.RequirementMergeTests.test_board_scope_drift_at_protection_read_refuses_admission`
- `factory_v1.tests.test_requirement_merge.RequirementMergeTests.test_exact_ticket_body_drift_at_protection_read_refuses_admission`
- `factory_v1.tests.test_requirement_merge.RequirementMergeTests.test_independent_candidate_retains_exact_revision_evidence_when_other_issue_held`
- `factory_v1.tests.test_requirement_merge.RequirementMergeTests.test_last_publication_guard_rejects_remote_doc_drift_without_mutation`
- `factory_v1.tests.test_requirement_merge.RequirementMergeTests.test_material_revision_after_approval_refuses_result_and_merge_inspection_retains_history`
- `factory_v1.tests.test_requirement_merge.RequirementMergeTests.test_minor_prose_cannot_revalidate_changed_controller_check_bytes`
- `factory_v1.tests.test_requirement_merge.RequirementMergeTests.test_native_textual_blocker_drift_at_protection_read_refuses_admission`
- `factory_v1.tests.test_requirement_merge.RequirementReviewTests.test_material_revision_before_review_refuses_old_candidate_on_durable_reload`

## Final-candidate fingerprints

HEAD (read without a Git subprocess): `b0196edd7a149b9097a9e3346579f2a6a900e891`. Commit tree: `0a8c8dac54e36d03eaa92e9928276028ce5e3bc2`. These hashes bind the working-tree tests, not merely HEAD. The audit self-test simulated socket/process/signal audit events without performing those operations and verified denial survives caught exceptions; **zero attempted prohibited events** occurred during discovery/tests. Candidate file bytes and modes were identical before/after the final run.

| Test file | SHA-256 before = after | Mode |
| --- | --- | --- |
| `factory_v1/tests/test_planning.py` | `75b9b877674b1603721a37bc87e65e6fd454fedd97348529027a2cc24fdbaa22` | `0o664` |
| `factory_v1/tests/test_tickets.py` | `80b17c9c7a6cf136a33f2e09e2608d782fff2438d26e9f4db1769be0c5780401` | `0o664` |
| `factory_v1/tests/test_bugs.py` | `d8f4c46b5ff40659043b574c76a0cd8cb40be8431baba5ac373551845c51b28b` | `0o664` |
| `factory_v1/tests/test_ticket_native_scope.py` | `d4fc6446ee038c61c6839000f26b02f024e86f5f752a0652aceb7384f1edc5b5` | `0o664` |
| `factory_v1/tests/test_requirements.py` | `dd2b500adebf9c106a686be879e8ebf627b316d410db3062cfee3e3863cecf1b` | `0o664` |
| `factory_v1/tests/test_requirement_bugs.py` | `c1813d091d2c03545ed106f73f4d39c5feda41debf939b82f7ae3e8d879eeb94` | `0o664` |
| `factory_v1/tests/test_requirement_merge.py` | `892211c4737d361da714d8b5fd0838961ccfd0dac421a1bcfe381b5e05e0d176` | `0o664` |

Production and protected focused files still match their tracked index blobs. Other concurrent streams have their own test/CI/runner edits in the shared worktree; this evidence does not attribute those changes to this stream. Whole-suite and unassigned fixture-consumer acceptance remain unrun, and independent review/integration belong to the parent.
