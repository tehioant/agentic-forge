# Agentic Forge

A small, deterministic controller for bounded Hermes coding work across independent repositories. Planning stays conversational. Execution is one approved ticket at a time in fresh, whole-agent Docker containers:

```text
approved ticket → builder → offline checks → simplifier → offline checks
                → independent read-only reviewer → offline checks → review
```

Tests are mandatory, not TDD. Simplification may make no changes. The controller commits builder and cleanup separately, pins review to an exact commit, and never merges `main` or deploys.

## Install

Requirements: Linux, Python 3.11+, Git, Docker and a current Hermes installation. No public listener, daemon, gateway restart or personal-profile change is needed.

```bash
# Pull the documented image; if stable is unavailable, use a verified latest digest.
docker pull nousresearch/hermes-agent:latest
python3 scripts/install.py --image nousresearch/hermes-agent:latest
python3 -m unittest discover -s tests -v
```

Installer pins the retrieved image digest and creates **only** `~/.local/share/agentic-forge/`. Set `FORGE_HOME` or the CLI home option for another location. State, board databases, worker homes, campaigns and logs are private local files, not Git contents.

The installed worker templates contain no personal memories, integrations or messaging credentials. Reviewed planning sources (`to-spec`, `to-tickets`, `code-review`) are linked from the uploaded skills; Forge-specific role adapters remove mandatory TDD and fan-out. Unrelated shared skills remain unchanged.

## Register and approve work

```bash
python3 -m forge register my-project /absolute/clean/repo --verify 'python3 -m unittest discover -s tests -v'
python3 -m forge ticket my-project 'Small approved change' --spec-file specs/change.md --allow-path 'src/*' --allow-path 'tests/*'
python3 -m forge approve my-project TASK_ID
python3 -m forge run my-project --campaign batch-001
python3 -m forge status my-project
python3 -m forge pause my-project
python3 -m forge resume my-project
```

Each registered repository gets a native Hermes Project and bound Kanban board in the separate factory home. Tickets begin blocked awaiting approval. The external-lane assignee deliberately cannot be resolved by the normal dispatcher: only the trusted Forge runner may launch its confined agents. Use the native CLI to inspect the same lifecycle:

```bash
HERMES_HOME=$HOME/.local/share/agentic-forge/hermes hermes project list
HERMES_HOME=$HOME/.local/share/agentic-forge/hermes hermes kanban --board my-project list --json
```

One global implementation writer initially, at most five distinct approved tickets per named campaign, a one-hour absolute campaign deadline, at most two attempts per stage and two review/fix cycles. Each agent attempt is capped at 15 minutes and 60 tool iterations. Reusing the campaign name resumes its remaining budget; it does not reset a failed stage's count. A new batch requires an explicit new campaign. Conflicts, credentials, scope changes, bad evidence and exhausted limits fail closed.

Project pauses and failure state are separate. The shared writer lock can temporarily make another project busy; it is not a shared failure/paused flag. A paused project must be resumed explicitly. A killed controller may require cleanup of its named `forge-*` container before resuming; consult status/evidence rather than deleting workspaces.

## Boundaries

- Whole-agent process is unprivileged, capability-free, resource-limited and read-only outside its isolated clone/home/tmpfs. The clone's `.git` is read-only; reviewer clone is entirely read-only.
- No personal Hermes directory, Docker socket, SSH keys, GitHub grant or other repository is mounted.
- Verification executes approved explicit argv **inside offline containers**, never project scripts on the host.
- Actual Codex access/refresh grants never enter workers. A host-side broker receives an access-only snapshot; each worker gets a random short-lived capability via its private Unix socket. The broker permits only Codex responses/compact for the configured model, caps requests/concurrency/response bytes and expires with the attempt. Provider quota is shared, not a separate billing identity or monetary-spend cap.
- **All containers have networking disabled**, including inference workers. A container-local relay reaches only that attempt's host Unix socket; the host broker forwards to one fixed HTTPS upstream without redirects. No public listener or firewall change is needed. No production secrets belong in registered repositories; prompts/source still go to the configured inference provider.
- Refresh an expired access snapshot explicitly: `python3 scripts/install.py --refresh-access`. Failure to authenticate blocks the batch rather than borrowing another integration.
- Review-ready is **not** done, published, merged or integrated. The trusted operator reviews the diff, pushes the feature branch, opens a PR and verifies exact-head CI before treating delivery as complete. No repository-required-check policy is weakened or silently created.
- Dependent tickets must wait for manual integration into the registered baseline; a reviewed ticket's clone is not automatically visible to another ticket. This initial version does not claim multi-repository dependency orchestration.

Notifications are metadata-only change outputs from `python3 -m forge.notify`. A script-only cron watcher can forward them to this Discord thread without another agent; no bot token is placed in workers. Notification delivery is best-effort, not a transactional outbox.

See [architecture](docs/architecture.md), [pilot spec](specs/pilot-doctor.md) and the checked-in tests. No auto-merge or production deployment is enabled.
