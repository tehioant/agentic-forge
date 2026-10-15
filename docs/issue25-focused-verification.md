# #25 focused verification scope

Publication is held independently of CI compliance. No canonical suite, services,
workers, containers, model/helper execution, campaigns or external mutations are
authorized in this lane. The CI workflow remains unchanged: PR/main publication
would run the canonical inventory, including the previously identified prohibited
journeys. This feature does not migrate, exclude or weaken that inventory.

## Execution graph audited before running

Only the explicitly named tests below are selected; no discovery of the full
repository suite is executed. Their constructors/setUp/active helpers were read,
not inferred from names or an external-execution audit hook.

| Selected class/test | Active setup/helpers | Real behavior exercised | Mocked/stopped boundaries |
| --- | --- | --- | --- |
| `test_service.ServiceTests` | Fresh TemporaryDirectory, `service.initialize`, explicit minimal approved iteration row | Config ownership/isolation; durable pause/startup ordering; stronger emergency hold preservation; one observer/notification cycle; fixed systemd unit rendering and loaded-unit identity; current status not authority | `restart.reconcile`; delivery config/sample; attention read/send; `subprocess.run` (no systemctl); dispatch |
| `test_service_dispatch.DispatchSelectionTests` | Explicit dict projections, no preparation helpers | One pure stage selection at a time; sufficient existing planning handoff; separate reviewer axes; no retry of any retained attempt; ambiguous ownership refusal | No external seam reached |
| `test_service_dispatch.DispatchBoundaryTests` | Explicit descriptors/review result; TemporaryDirectory for cancellation marker; mocked subprocess handle | Installed-source equality refusal; host review authorization cannot be replaced by worker claim; actual cancellation marker and both stop events; existing host-executor prelaunch/interruption cleanup; fixed built-in configuration rejects shell plugin fields | Review precursor evaluator; process creation/kill/wait are mocked; no executable/helper runs |
| `test_service_components.DispatchComponentTests` | Fresh isolated schema and direct scope/assignment projections; no stage preparation is executed in setup | Public scheduler tick's real durable pause checks, final read-return pause injection, stale-stage refusal, existing launcher/guard routing, shutdown propagation, and nonnested publication-lock ownership | Restart precursor observation; authoritative assignment inspection; installed instruction read; simplification preparation; publication and launcher boundaries. Each test stops at its one seam; no result admission or worker cleanup chain runs |
| `test_simplification.SimplificationValidatorTests.test_private_receipt_implementation_status_and_drift_matrix` | Existing `LocalStageEvidence` writes labeled explicit files/records, no worker/preparation lifecycle | Real completed-implementation/status/private controller-check receipt/source-drift guards reused by the service's next-stage seam | No launch, network, agent, Docker or test-command subprocess executes |

The component-row projections contain only the fields read by the selected
scheduling/path/control seams; full prior-stage admission is an explicitly mocked
precursor, not claimed to have run. The retained existing validator fixture labels
its synthetic receipts and never presents them as actual execution evidence.

Tests never call `service.run`, real systemctl, real `sandbox.launch`, actual
`requirement_execution` subprocesses, or a full controller/worker journey. The
executor cancellation test exercises only `invoke` with a mocked Popen handle,
not a launch → result admission → cleanup attempt. Startup tests call only the
startup pause/reconciliation component, never a supervised process lifecycle.

## Commands

```sh
mise exec -- python -m unittest \
  factory_v1.tests.test_service \
  factory_v1.tests.test_service_dispatch \
  factory_v1.tests.test_service_components \
  factory_v1.tests.test_simplification.SimplificationValidatorTests.test_private_receipt_implementation_status_and_drift_matrix -v
```

The final exact-HEAD receipt outside the checkout records discovered selected test
IDs, command, completed exit, runtime and pre/post SHA-256/full-file-mode manifest.
The syntax check compiles source bytes in memory (no generated artifacts). Normal
mise-managed betterleaks/shellcheck/conventional-commit hooks remain enabled.
`.mise.toml` and the CI workflow configure no typechecker: that is an explicit
verification gap, not an invented passing typecheck.

The local installed-source audit is a separate read-only capability check, not a
feature test or native execution receipt. Its observed `implement`, `code-review`
and `to-tickets` mismatches must remain blocked until reviewed; no shared skill
file or reviewed pin is changed by #25.

See `factory_v1/SERVICE.md` for remaining implementation and live criteria. The
parent owns fresh-main integration, dedicated simplification and independent
Standards/Spec review. Do not transfer these checks to a changed integration tree.
