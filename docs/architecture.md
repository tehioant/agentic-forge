# Architecture and operator trust boundary

Hermes owns native project and card lifecycle. Forge is an out-of-tree deterministic CLI controller, not a core Hermes patch or perpetual LLM loop. It owns approvals, immutable specs, campaign deadlines/attempts, scope checks, serialized writes and host Git commits. No autonomous agent can update the control board because its database is not mounted.

Native cards use local-only completion contracts and remain in review after the pipeline. This explicitly avoids claiming PR delivery without exact-SHA CI. Publishing and integration remain trusted operator work in this version. Multiple independent registered repos have their own native boards and clones; pending dependent work is not promoted based on a reviewed-but-unintegrated artifact.

Worker runtime is the official Hermes image, resolved from a verified registry manifest and pinned by digest. The default s6 entrypoint is bypassed in favor of its shipped CLI shim, so no gateway/service is started inside workers. Every invocation uses a fresh home, fresh conversation, project-local log, role template and selected skills.

## Isolation

Writable mounts: one standalone ticket clone, one fresh worker home. Git metadata is overridden with a nested read-only mount. A reviewer receives an entirely read-only clone. Docker drops all capabilities, disallows privilege escalation, runs as the invoking nonroot uid, caps memory/CPU/processes and makes the image filesystem read-only. No Docker socket, host network, SSH/GitHub configuration, personal Hermes home or sibling checkout is mounted. Offline verification has no inference credential and no networking.

Inference uses a host-only access-token snapshot from the authorized Codex login. Workers never receive actual provider grants: an attempt-scoped Unix socket and random capability authorize only fixed-upstream Codex responses/compact for one configured model. All worker containers have networking disabled. A container-local HTTP relay connects only to the mounted socket, avoiding host TCP listeners and firewall changes. The broker refuses redirects, expired capabilities, other endpoints/models, more than 120 requests and more than two simultaneous requests; response/log bytes are capped during streaming. The snapshot contains no refresh grant and must be refreshed securely before expiry. Project source/prompts reach the configured model provider, so this is not data-local execution. Docker/kernel vulnerabilities and shared-provider quota/cost remain limitations.

## Recovery

Campaign state persists absolute wall-clock deadline and consumed attempt counts before starting work. Repeat the same campaign name to resume; restarting must never grant fresh attempt/time limits. A killed process is not proof that Docker stopped: runtime explicitly removes the named container on timeout/interruption. Abrupt machine/controller death is a distinct operational case; inspect `docker ps --filter name=forge-` and evidence before cleanup/resume. Never delete or overwrite a partially implemented clone automatically.

Project pause/failure state is independent. A single global nonblocking implementation lock deliberately serializes initial writers. Review and simplification are separate contexts. Tests and scope checks follow every modifying stage and after review; evidence records the reviewed commit. Invalid/ambiguous output fails closed. No-op simplification is allowed.

## Unattended operation

Only explicitly approved tickets may run, within an explicitly named bounded campaign. No service scans arbitrary boards continuously. No merge/deployment/production migration or credential operation is automated. Repeatable CLI commands are preferred to recurrent agent reasoning. A script-only Hermes cron may deliver pending notifications; no LLM should process a deterministic event log.

## Sources

- https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban
- https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-worker-lanes
- https://hermes-agent.nousresearch.com/docs/user-guide/docker
- https://www.aihero.dev/getting-started-with-ralph

Registry availability and shipped CLI behavior take precedence over examples in documentation; stable image tag availability must be checked before pinning.
