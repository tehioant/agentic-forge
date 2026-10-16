# Project execution preparation

## Delta criteria and result

The original T5 receipts/history remain valid for the legacy scoped-installation route. This delta adds the missing fresh-project route; it does not claim native qualification or completion of #71.

| Criterion | Source route / focused evidence |
| --- | --- |
| Fresh project needs no running service, issue or AGENTS before native planning | `prepare_planning`; directly seeded `preplanning` case with no AGENTS or service configuration, no supervisor/installer calls, and no supervised-readiness binding |
| No administrator-authored project configuration | Reusable platform inventory plus installation authority; verified attachment supplies operator/repository/IDs/project/iteration/state and the current product acceptance |
| Concrete scoped installation/start | `managed_service`, native `service.load`, `service.install`, `service.supervisor`; `managed` and `start-denied` cases mock process/supervisor boundaries |
| No singleton hijack | Deterministic scope/state unit identity, exact fragment name/content and no drop-ins; existing focused service method checks another scope and the singleton fragment are rejected |
| Final continuation consumes actual publication | `published_seed` requires complete planning and published ticket batch, rereads pinned specification inputs and first actual ticket identity/body; `unpublished` refuses before installation |
| Engineering retains all gates | Native launcher, dispatch, readiness, delivery and candidate authority validators; original pause, spending, health, consumed-home, source/retained-byte drift matrix retained |

## Exact T7 composition API

`ProjectExecution(setup, tracker, config)` uses the existing T2/T4 objects. Legacy `config` has exactly `root` and `capability_config`. Managed `config` additionally has `installation_config`. All three are private host-selected paths; ordinary users do not author JSON, profiles, units or grants.

1. `prepare_planning(draft_id, expected_revision)` freshly validates sufficient unchanged attachment and completed tracker scope, reviewed platform policy and host planning/synthesis files. For a fresh managed project it derives private native `service.json` using the original attached SQLite state. It validates without registering supervised-readiness, installing, starting, pausing, resuming or executing a skill. It needs **no** delivery issue, AGENTS, running unit or healthy deployment. Success is `planning_prepared`, `phase: preplanning`; this means setup inputs are available, not native execution is qualified. Use the returned `planning_config` and `synthesis_config` with the existing native planning/publication route; do not call engineering `register` first.
2. After actual native specification and ticket publication, `prepare(draft_id, expected_revision)` performs engineering preparation. In the managed route, complete planning and `ticket_work.status == published`, `publication_complete`, first published ticket identity/body and current pinned specification inputs are required. The real first ticket seeds issue identity, and the actual published specification commit seeds `spec_commit`. AGENTS and the explicit deployed-health contract are now required. Missing publication is `execution_publication_required`, not a missing executor or an invented anchor.
3. The managed engineering route derives the scoped delivery configuration from the reviewed native service template's delivery file, replacing only operator/repository/immutable repository ID/actual first-ticket issue. Contract health URL/revision/acceptance, checks, workflows and environment remain reviewed host authority. Native delivery validation still applies. It installs the deterministic instance through `service.install` only with explicit `install` platform authority, and starts an observed inactive PID-zero instance only with explicit `start` authority. It then rereads supervisor status; only active/running/positive PID counts as running. No restart/resume, new allowance or candidate authorization is inferred.
4. `register(draft_id, expected_revision)` freshly prepares, requires the original iteration already paused, selects the exact prospective service delivery/dispatch/readiness paths under the persistent interruption lane, seals native readiness policy, and rereads binding/configuration. Returns `registered` or `blocked`. Starting a fresh native service itself retains the original startup pause; explicit authenticated controls resume remains separate.
5. `resume` retries registration only; it never calls controls resume. `read` is retained evidence only. `status` refreshes the recorded phase; it is not dispatch authority.

`blocked` records include exact code, operation, cause, draft/revision/scope, readiness failures, consequence, next decision and reconciliation flag. A blocked postpublication preparation may already have performed explicitly authorized scoped install/start; it never means a model/engineering worker ran. `planning_prepared`, `prepared`, `registered`, supervisor running, capability readiness and runnable iteration are distinct facts.

## Two separate host schemas

Legacy `INVENTORY_FIELDS` remains unchanged: exact operator/repository/repository ID/draft revision, review reference, existing scoped `service_config`, role root/templates, planning/ticket skills and configs, candidate authority path lists, protection and policy. Legacy engineering preparation still requires an existing running scoped singleton installation.

Managed `capability_config` has exactly `PLATFORM_FIELDS`: the legacy inventory minus `operator_id`, `repository`, `repository_id`, `draft_revision`, `service_config`. Its policy has exactly the legacy policy fields minus `acceptance`; current draft acceptance is derived, not administrator-entered. Merge/production remain `human-approved`; verification commands remain a finite reviewed nonempty list; `deployment: false` is precisely `nondeployment_unsupported`. No-deployment is not treated as healthy by this implementation.

Managed `installation_config` has exactly:

- `operator_id`, `reference`: exact authenticated host operator and nonempty reviewed lifecycle authority reference.
- `state_root`, `workspace_root`: existing private physical disjoint platform roots. The verified attached controller state and workspace must be inside their respective roots. This authority covers only verified attached projects under those roots, not ambient repositories.
- `service_template`: private reusable native service JSON containing exactly `service.FIELDS` minus `state`, `operator_id`, `project`, `iteration`. Dispatch/readiness selections must be null. The delivery path selects the reviewed native health/check/workflow template; GitHub/notification references remain explicit host capabilities, never ambient grants.
- `unit_directory`: existing operator-owned supervisor directory, validated by the native installer.
- `actions`: unique subset of `install`, `start`. An empty list permits derivation only. Neither install nor start is inferred from project intent or configuration presence.

The journal pins the resolved inventory, installation authority and service-template content. Fresh checks at lifecycle writes reject drift. Derived service/config paths are deterministic and private; native validators validate actual derived files. Role templates remain finite (1–100 slots per existing stage, at most 1000 total); names/homes/claims are stable dedicated identities. Preparation never creates/reuses consumed homes or resets grants, attempts, cleanup or pause state. Candidate-specific review, diagnosis, publication and merge authority is never generated from reports.

## Service instance contract

`service.instance(config)` derives `agentic-forge-<full scope/state digest>.service` from exact state/operator/project/iteration. `install(config, directory, unit=...)` and `supervisor(config, action, unit=...)` accept only the legacy `agentic-forge.service` or that configuration's exact derived instance. Omitted `unit` preserves legacy callers/configuration fields. Exact fragment name, ownership, bytes, selected source/config path and absent drop-ins are still required; another scope's instance cannot be selected. Service configuration schema/default consumers are unchanged.

`service.load(path, register_binding=False)` performs original physical/private/config/state checks without creating a supervised-readiness binding. Its default remains registration enabled. Only preplanning/managed derivation uses the nonregistration form, so native planning/publication is not fenced by a premature missing engineering readiness policy. Engineering registration still uses the original registering load/seal path.

## Evidence and limitations

The existing discovered project method is extended with directly seeded preplanning, managed postpublication and missing-publication/start-authority cases; the existing service focused method is extended with scoped-instance checks. No new discovered methods, worker chain, E2E, network, model, profile, container or real service install/start is used. The managed publication fixture supplies a pinned-input reader response; it proves the caller's correlation/phase contract, not live immutable publication provenance. All production pinned-input/readback validators remain in place.

T7 owns plugin/enrollment/decision wiring and automatic phase scheduling. This delta alone does not qualify that integration, actual GitHub routes, native planning execution, independent verifiers, deployed health, or production readiness. A fresh product may plan before those engineering prerequisites exist; engineering remains blocked until they are real. Existing sealed-input drift requires explicit reconciliation, never automatic reselection. Repeated install detects existing unit bytes through the native supervisor validator; start is never used as controls resume. No external installation, live acceptance or deployment was performed.
