# Conversational handoff (source checkpoint)

The normal interface is the originating project conversation: name, intent,
users, objectives, finite milestone, constraints and acceptance. Existing repository
choices remain literal; a new private name derives under the reviewed account.
Product answers and approval replies never create host permission or spending.

The opt-in `project_handoff` setting composes enrollment, setup, optional creation,
tracker, execution and decisions. Its legacy fields remain `publication_config`
and `wakeup_config` (private absolute paths or null). For fresh projects configure
`project_native` with exactly `root` and `capability_config`: the plugin constructs
`NativeProject(enrollment, setup, execution, config)` and passes that same helper
to the coordinator. `resolve(draft_id)` selects private publisher/wakeup paths;
`bind(native, draft_id)` returns the concrete binding with host publisher and
subscriber started/reused; `close()` owns their cleanup. Do not configure a second
static `binding`. These are administrator capabilities, not user JSON prompts.

`ConversationEnrollment.update` saves the correlated judgment, releases its lock,
and calls `advance`. Actual attach/create is followed by tracker preparation and
`execution.prepare_planning`. No engineering service policy is registered before
native planning. The original native tool schemas and hook identity are preserved.

The pre-LLM reconnect and native publish/complete tools schedule one concrete host
task per draft. There is no new polling controller or model loop. The task uses
`planning_wakeup.supervise` to consume exact original-before-request or requested
completion authority. Enqueue acceptance, loaded instructions and returned strings
are not completion. Durable records select at most the three existing ticket
operations: `tickets.synthesize`, `service_dispatch.synthesize` (the actual bounded
host executor), and `tickets.publish`. Each operation retains its existing admission
and unknown-effect guards. Actual published ticket readback precedes execution
registration. Initial ticket publication does not call `service_dispatch.tick`,
whose engineering readiness policy would circularly fence that publication.

Resolved decisions re-enter their exact producer, require its durable completion
journal to agree, persist the outcome, then advance setup/planning or schedule the
postpublication task. Producer-specific reconciliation re-enters the producer's
retained-effect resolver; it never clears unknown intents or treats approval as a
receipt. Rejection remains a hold. Completed requests no longer hide the native
planning context on later turns. Holds, deadlines, budgets and attempts remain owned
by their existing producers.

## Remaining source boundaries

**First seal is still missing a lawful automatic admission adapter.** Existing
`ProjectExecution.register` requires `controls.blocked` before updating the service;
`readiness_gate.seal` independently requires pause. A fresh active project therefore
cannot register automatically. The minimal required correction is an explicitly
scoped first-registration lane in both existing guards, limited to an unsealed,
unregistered initial policy and the exact reviewed prepared selection, with fresh
owner/authority/readback and interruption-lane checks. It must preserve intentional
pause/stop and all independent holds. This coordinator does not manufacture a pause
or call controls resume. That adapter is outside this lane's source ownership.

The concurrent native-helper implementation must be integrated and verified by the
parent. This checkpoint consumes its fixed interface, not an absent callback. The
helper's actual bound GitHub client must support the scoped ticket read/write route;
the legacy setup binding alone is credential-blind and is not a ticket publisher.
Static legacy configuration therefore does not prove fresh-project reachability.

First/next-ticket retirement, correction, reporting and wait reuse the existing
service/continuation predicates. Their direct component checks establish those
contracts, not connected reachability through the unresolved first-seal boundary.
No whole simulated journey, live model/network/service/container, native activation,
publication, tracker update or deployment verification is performed here. Independent
Standards/Spec review and stable-helper integration remain parent-owned.
