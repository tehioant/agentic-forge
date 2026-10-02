# Fresh factory v1 — approved intake tracer bullet

This independent, standard-library Python controller implements the local intake/inspection slice of issue #5. It uses only the fresh specification and interview; no previous factory implementation, profiles or state are loaded. Run from the repository root with Python 3.11 or later. No package installation, Hermes core changes or credentials are required.

## Trust boundary

The CLI is a **trusted operator/controller interface**, not an unauthenticated API or a worker tool. The caller must already have authenticated the operator and verified the approval reference and gateway origin against the original conversation. `--operator-id` is administrator-supplied configuration, never a value taken from an untrusted intake. Matching a self-reported operator ID is not authentication. The CLI validates and durably records the trusted handoff; it does not independently verify a Discord message or GitHub permissions. Network-backed approval verification and controlled GitHub operations are not established by this slice.

Place state in an operator-owned, private directory outside the checkout. The entire directory, including SQLite journals and backups, must be inaccessible to workers and other users. Do not use symlinked, attacker-writable, or shared state paths. New state files are created with owner-only permissions on POSIX; existing file and directory permissions remain the administrator's responsibility. Request files and stdout contain approval and routing metadata; protect them too. No secrets belong in the request.

## Public commands

Use a sanitized, operator-verified request file. These example identifiers are placeholders, not real approval or live verification evidence:

```json
{
  "project_id": "approved-product",
  "iteration_id": "milestone-1",
  "repository": "example/approved-product",
  "idea": "The product explicitly requested by its operator",
  "approval": {"operator_id": "42", "reference": "operator-message-101"},
  "origin": {
    "platform": "discord",
    "chat_id": "123",
    "thread_id": "123",
    "parent_chat_id": "456",
    "scope_id": "789"
  },
  "work_item_number": 5
}
```

`work_item_number` is optional. When provided, its repository is the exact selected repository; it is a correlation reference, not proof that an issue exists or authority to claim it. Intake requires an exact `owner/repository` string, not a URL or guessed default. Identifiers are preserved, never repaired or normalized. This initial slice supports the verified Discord-thread origin shape; other origins are refused rather than guessed.

```bash
python -m factory_v1 --state /private/operator/state.sqlite --operator-id 42 \
  register --request /private/operator/approved-intake.json

python -m factory_v1 --state /private/operator/state.sqlite --operator-id 42 \
  inspect --project approved-product --iteration milestone-1
```

Successful commands print one JSON record and exit 0. Refusals and operational errors print a JSON error on stderr and exit 2; argument errors use argparse's stderr/help and exit 2.

- Missing approval, wrong operator, ambiguous repository/origin, unknown fields, malformed/duplicate JSON fields and invalid issue references are refused before creating state.
- The first approved iteration is `active`. Further approved iterations are `paused`. A serialized SQLite transaction makes concurrent registration obey the same rule.
- Identical registration returns the original record and identities. Changed scope, provenance or work-item reference for the same project/iteration is an `intake_conflict`, not a silent update.
- Each record exposes project, iteration and stage, an optional work-item reference, an intake control-run ID and evidence ID. The control-run ID identifies local registration, **not** a worker or model invocation. `worker_run_id` is null; no worker is started.
- State and approval/origin survive a fresh process. `inspect` is read-only and never creates missing state. No daemon is needed for this slice.
- `execution_allowed` is always false, even for an active approved iteration: worker isolation, spending admission, repository access, scheduling and merge gates are not implemented here. Active means admitted intake, not runnable work.
- No GitHub writes, notifications, charges, worker launches, merges or deployment occur. No pause/resume scheduler or crash reconciliation of external actions is claimed.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s factory_v1/tests -v
python -m compileall -q factory_v1
```

Tests invoke the actual public CLI in fresh subprocesses against temporary SQLite files. They cover restart inspection, refused intake, idempotent/conflicting replay, concurrent one-active admission, persistent correlations, read-only missing-state inspection, malformed/missing request handling and private new-state permissions. They do not mock the controller or pretend to prove external/container/notification/spending boundaries.

The `Fresh factory v1` GitHub Actions workflow exercises these same tests on Python 3.11 and 3.13 using read-only repository permission and pinned action revisions. Independent requirement/security review and integrated-main verification are still required before issue #5 can close. This tracer bullet is not a functioning autonomous factory.
