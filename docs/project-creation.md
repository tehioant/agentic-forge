# Private new-product creation

`factory_v1.project_creation.ProjectCreation` composes an existing, configured
`ProjectSetup`. It is an explicit new-product operation, not an existing-repository
attachment fallback. Host installation (and T7) may use:

```python
creator = ProjectCreation(enrollment.setup, {
    'root': '/private/creation',
    'state': '/private/creation/history.sqlite',
    'account': 'selected-github-login',
    'reference': 'reviewed-exact-target-create-and-baseline-approval',
}).register()
result = creator.create(draft_id, expected_revision, 'selected-github-login/product',
                        session_id=actual_native_session)
```

The host provisions a private, separate creation directory and reviewed exact
repository capability through the existing `ProjectSetup` configuration. The
reference must authorize private creation **and** deterministic README/AGENTS
initialization, and must equal the capability reference. No secret, grant file,
initial commit, internal project ID or receipt is requested from the product user.
No broader grant, visibility change, paid fallback or application code is created.
Only personal-account creation supported by existing `repositories.onboard` is
selected; organizations/enterprise require another reviewed capability, not a
fallback endpoint.

`register()` installs `factory_project_create` with only `repository` and
`expected_revision` model arguments. It derives draft/operator/origin/session from
the admitted conversation. `create` is the host API; `read(draft_id)` is its private
creation journal. Success returns the actual unchanged T2 attachment result, so
T7 consumes `result['binding']`, `result['transfer']` and
`enrollment.setup.route(draft_id)` normally. Registration is explicit: constructing
a creator does not dispatch a worker or install a second workflow controller.

## Persistence and reconciliation

Existing `repositories.onboard` performs `/user` identity selection, durable
new-product intent, private `/user/repos` creation, immutable returned-ID pinning
and independent exact-name/private/marker readback. A preexisting name is a
`repository_collision`, never adopted. A current correlated renamed-product answer
can supersede only a proven no-effect collision, with fresh exact-target host
approval and no attachment, baseline or creation intent. The old journal is archived
and its intake retained as superseded-no-effect; the new attempt has a versioned
iteration and marker. Unknown or created effects cannot be superseded. Lost responses
reuse the retained creation attempt and exact marker; absent or contradictory
effects stay held and are not blindly retried.

Creation intake lives in the separate configured private journal SQLite database;
T2's existing-repository intake remains in its configured controller database.
This deliberately avoids re-registering a new-intent request as an incompatible
existing-intent request. The real original new intent/onboarding identity stays
in creation history, not rewritten or replaced with a fabricated receipt. Both
paths retain their own ordinary pause/restart guards; an already-registered T2
attachment hold is checked before creation and initialization. Private creation
state is not a worker dispatch binding.

Minimal baseline: `README.md` with the agreed name and original retained
request. On a verified empty repository only, GitHub Contents API `PUT` creates
it with base64 content, no update SHA, explicit deterministic commit message and
no author impersonation. There is no forced push, existing-content update or
manual initial commit. The durable baseline intent precedes PUT; the returned
commit is pinned before readback. The initial commit must have no parents, the
exact marker message and exactly the expected README blob, whose bytes are read
back at the immutable commit. A lost PUT response is reconciled from those same
checks without another PUT; missing/changed content or HEAD remains held.
A second independently intent-bound Contents API PUT creates deterministic
product-local `AGENTS.md`: agreed-scope work, focused mocked tests, no private
controller configuration in source, and human-controlled merge/production. It
copies no shared skills/configuration and grants no execution authority. Its exact
parent must be the verified README commit; message, sole added blob and immutable
bytes are read back before attachment to the final commit. Each uncertain write
is reconciled read-only, never resent; this is not an atomic multifile commit.

T2's attachment journal is seeded with the verified immutable repository ID and
baseline commit before calling `ProjectSetup.attach`, preventing attachment to a
replacement repository or another initial revision. T2 still does its ordinary
onboarding, checkout, optional native Project and planning readbacks; the creator
does not manufacture a planning binding or transfer. Interview observations are
unchanged. Routine success is quiet; blocked results distinguish collision,
approval, uncertainty, baseline conflict and independent interruption causes.
T6/T7 host composition can consume these results; this module does not edit the
plugin entrypoint or automatically select downstream execution configuration.

API references: [Contents](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents),
[branches](https://docs.github.com/en/rest/branches/branches#list-branches).
Focused tests mock external boundaries and directly seed each component's inputs;
they establish source behavior, not remote creation or live qualification.
