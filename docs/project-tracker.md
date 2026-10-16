# Conversational tracker preparation

`factory_v1.project_tracker.ProjectTracker(setup, config)` consumes an existing
`ProjectSetup`; it does not rerun onboarding, create a native backlog, publish
issues, dispatch workers or start a service. T7 owns enrollment/tool wiring.

Public host-only interface:

- `prepare(draft_id, expected_revision)` returns a durable ready/blocked journal.
  It checks the sufficient draft, T2 transfer, exact repository ID and current
  native binding; interruption and planning identity are rechecked before effects.
- `read(draft_id)` returns retained intents, identities and blocker, never secrets.
- `tracker_config(draft_id)` revalidates preparation and returns the existing
  `ticket_github.resolve_board`/ticket synthesis configuration, not a new schema.
  `read()['identities']` contains resolved project, Status field/options and native
  milestone IDs. T5 uses this checkpoint before deriving execution configuration;
  T7 passes the tracker configuration to the existing ticket publisher.

Host configuration has exactly `root`, `capability_config`, `tracker`. The first
is a separate private journal directory. The capability is a private controller
file with `operator_id`, exact `repository`, approved `reference`, `bearer`, and
`operations`. Supported operation grants: `create_board`, `private_board`, `link`,
`status`, `view`, `milestone`. No grant is inferred from the draft or T2 read
access. The capability is reread before every write. Do not expose it to models.
`tracker` uses the existing four-key contract; default:

```json
{"project_id": null, "status_field": "Status", "statuses": {"ready": "Todo", "active": "In Progress", "done": "Done"}, "triage_label": "triage"}
```

An explicitly configured project must already be linked. Multiple open linked
boards, inaccessible selections, closed/ambiguous milestones and ambiguous
fields/options hold for exact target resolution; no unrelated substitution.
Existing board configuration, extra options, views and item progress are never
modified. The current milestone is T2's actual iteration name (currently `m1`),
with the agreed draft `product.milestone` as its description. An existing matching
milestone must have that same scope; a different description holds, not overwrites.
This service never invents subsequent milestones or a second authoritative backlog.

Missing boards use `createProjectV2` with exact owner/repository node IDs. Returned
board ID is retained before subsequent reads. New boards are verified private;
only newly created empty boards may have default Status options configured.
A board view is created with Status as vertical grouping, independently read as
`BOARD_LAYOUT`; no existing view is changed. View REST path supports both
organization and user ownership. Status `databaseId` (not its node ID) is used by
REST; GraphQL grouping/readback uses the exact node ID.

Every mutation has a persisted intent, with an active-state/identity/permission
check before writing. Replays read current effects first and skip verified work.
Absent effects with an earlier intent are uncertain, never blindly resent.
A lost board creation response without a retained immutable ID stays held even
if a board later appears linked: trusted host reconciliation must establish the
exact created ID before continuation. Known partial link/schema/view states can
continue by readback; a lost view response with matching grouping does not create
a duplicate. Milestone returned identities are retained and independently checked.
Journal reconciliation is a privileged host operation, not an affirmative-chat
approval or permission to clear pause, incident, revision or budget holds.

## T6 blockers

Blocked results include `code`, `operation=prepare-tracker`, exact
`effect_operation`, `cause` (`permission`, `platform`, `uncertain`), repository/
revision/iteration/project `scope`, consequence, next decision and
`reconciliation_required`. Missing individual mutation grants name the operation.
The T6 bridge supplies the stable operation ID/project/zero spending ceiling and
uses its existing scoped decision/outbox contract; this module creates no alternate
notification path. Restoring authority resumes the same `prepare` call, not a new
setup. Routine success creates no attention or approval request. Scope mismatch
or insufficient T2 inputs can refuse before a journal exists; the host must retain
the original held draft rather than forge a tracker receipt.

## API source verification and qualification

Read-only source inspection used:

- `https://raw.githubusercontent.com/octokit/graphql-schema/master/schema.graphql`,
  SHA-256 `3c62d0526d133cee53221c89de9b455ade24db78b9e7ad56d642c4c15bce2654`:
  CreateProjectV2Input, UpdateProjectV2Input, LinkProjectV2ToRepositoryInput,
  UpdateProjectV2FieldInput/options, ProjectV2View/Layout and Status databaseId.
- `https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json`,
  SHA-256 `65ca6210f72021d07e4608fc26787c8132d77f8f3a475d8c065f0d7f5f02f6dc`:
  POST `/orgs/{org}/projectsV2/{project_number}/views` and
  `/users/{user_id}/projectsV2/{project_number}/views`, `layout=board`,
  numeric `vertical_group_by`.

There is no guessed GraphQL create/update-view mutation. Tests mock the raw HTTP
boundary of these concrete operations; they do not call GitHub, native APIs,
subprocesses, models, workers or service endpoints, and do not execute precursor
stages. Live permissions, board creation, runtime installation, service readiness
and automatic T7 progression are not qualified by this component evidence.
