# CADRIG architecture

CADRIG is a native, contract-driven CAD agent plus a reproducible execution and
evaluation harness. The interactive workbench and headless CLI use the same
state machine.

```text
User / benchmark task
        |
        v
ContractCompiler -----> DesignContract
        |                 requirements + invariants
        v
ActionGraphPlanner ---> closed typed ActionPlan
        |
        v
ExecutionEngine ------> adapter preflight
        |                 transactional preview
        v
ContractVerifier -----> accept / structured rejection
        |                        |
        |                        +--> bounded repair --> re-plan
        v
transactional commit --> post-commit verification --> rollback on failure
        |
        v
AgentTrace: contract + plans + receipts + verification + repair evidence
```

## Authority model

- The model may interpret intent and propose typed actions.
- The model may not execute source code, mutate a CAD host or declare success.
- The adapter is the only component that calls native CAD APIs.
- The verifier is the only component that accepts a result.
- The orchestrator enforces ordering, repair budgets, commit and rollback.

## Public artifacts

- `DesignContract`: deterministic desired-state predicates and preservation invariants.
- `ActionPlan`: capability-scoped, backend-neutral action graph.
- `DocumentSnapshot`: immutable semantic observation of one revision.
- `ExecutionReceipt`: transactional before/after evidence and diagnostics.
- `AgentTrace`: ordered record of the entire state machine.

## Package layout

The implementation lives under `src/cadrig`. CAD backends implement
`cadrig.adapters.base.KernelAdapter` and register under the `cadrig.adapters`
entry-point group. `freecad_workbench/CADRIG` is a thin client; it does not
contain another agent runtime.

The complete protocol, repair behavior and trust boundaries are specified in
[docs/NATIVE_AGENT_ARCHITECTURE.md](docs/NATIVE_AGENT_ARCHITECTURE.md). The
benchmark engine is specified separately in
[docs/CADGENBENCH_ARCHITECTURE.md](docs/CADGENBENCH_ARCHITECTURE.md).
