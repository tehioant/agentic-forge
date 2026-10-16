# Per-project native planning authority

`NativeProject(enrollment, setup, execution, config)` is a host-only adapter. Its exact configuration is `{"root": "/private/native-routes", "capability_config": "/private/host/native.json"}`. Keep the private route root separate from workspaces, controller source/state, drafts, profiles and host inventory. No model tool accepts this configuration.

The reviewed inventory has exactly `operator_id`, `reference`, `routes`. Each of the finite (1–100) routes has exactly:

* `repository`, immutable positive integer `repository_id`, boolean `private`, immutable full SHA `parent`;
* `credential_config`: an existing private host credential configuration with exactly `operator_id`, `repository`, `reference`, `api_base`, `bearer` (only `https://api.github.com` is supported);
* `profile_name`, `source_digest` (the digest of actual installed planning `skills` and approved `adaptation`);
* `allowance`: original `reference`, `provider_route`, `model`, integer `expires_at`, `max_wakeups: 1`;
* `input_envelope`: the existing NativeWakeup plain or bounded sender-attribution contract.

The operator must explicitly delegate that exact repository and finite native allowance before onboarding. Conversational intent, enrollment and setup do not grant permission. Repository names must be unique. One private immutable claim binds each selected repository to its generated draft/binding/assignment; another draft cannot consume it. Changing the inventory, deadline, identity, source pins or credential reference never silently repins an existing claim. An invalid or interrupted selection may retain private files; reconcile rather than delete/reissue grants.

`resolve(draft_id)` validates the actual transfer, setup, assignment, current installed sources, session, origin, repository ID/privacy and current HEAD against the approved parent, then returns exactly `publication_config` and `wakeup_config` absolute private paths. Derived JSON contains credential references, not bearer values. The publisher loader resolves the existing host-only credential on each existing authority guard and rechecks inventory pins plus repository metadata/current HEAD. Existing direct-bearer publication configurations remain supported unchanged. Publication still uses the original exact-intent/payload allowlist and immutable readbacks.

`bind(native, draft_id)` returns the same binding with its concrete host-only reader and publisher. Pending work starts the actual `NativeWakeup.start()` once per owned immutable route; repeated binds reuse its subscriber. Native `spawn_task` and `on_unload` must exist and return the supported host-owned future; no fallback loop, thread, service or model invocation exists. Pin drift at rebinding stops the owned subscriber. Unload uses the existing bounded cancellation/join; `close()` stops all owned tasks idempotently. `shutdown_unconfirmed` remains visible on the subscriber; it is not proof of termination.

Already-completed original work is bound read-only without starting another wakeup or renewing its expired allowance. The existing `planning_wakeup.supervise` must still validate actual native output, completed publication and fresh immutable handoff before consumption. Pending mailbox enqueue, skill loading and a successful bind are never completion. The coordinator owns supervision/resumption, not this adapter. Missing native lifecycle remains blocked even for binding completed work.

Focused tests mock external GitHub/native scheduling seams. They do not qualify a real installed native host, invoke credentials, start models/services, or prove live publication. Parent integration and independent review are separate gates.
