# CADRIG native agent architecture

## Purpose

CADRIG is a verifier-governed CAD agent, not a chat wrapper around generated
Python. It converts an engineering instruction into a testable contract and a
closed action graph, previews that graph transactionally, and accepts geometry
only when deterministic checks satisfy the contract.

The language model is replaceable reasoning infrastructure. The agent itself is
the CADRIG state machine, contracts, policies, verifier, repair controller and
trace format.

## State machine

```text
OBSERVE
  -> COMPILE_CONTRACT
  -> PLAN_ACTION_GRAPH
  -> PREFLIGHT
  -> EXECUTE_PREVIEW
  -> VERIFY
       | rejected and repair budget remains
       v
     REPAIR -> PLAN_ACTION_GRAPH
       | accepted
       v
  -> COMMIT
  -> VERIFY_COMMIT
       | rejected
       v
     ROLLBACK
```

No model response can skip a state, directly mutate the CAD host or mark a run
successful. A preview is mandatory before an applied transaction.

## Native contracts

### DesignContract

`DesignContract` is a versioned declaration of desired facts and preservation
invariants. The alpha verifier supports document existence, feature presence or
absence, typed feature counts, parameter equality with tolerance, revision
advancement, complete feature preservation, parameter preservation and maximum
feature-count deltas.

Unknown fields, unknown predicates, invalid parameter shapes, duplicate IDs and
using a result requirement as a preservation invariant are refused.

### Action graph

`ActionPlan` is the executable intermediate representation. Every action has a
stable ID, a declared `ActionKind`, an optional semantic target ID and JSON-only
parameters. The active adapter supplies the exact action vocabulary. Arbitrary
source code, shell commands and native face/edge indices are not action kinds.

### Execution receipt

An `ExecutionReceipt` records the adapter, plan, status, diagnostics and immutable
before/after snapshots. `AgentTrace` adds the compiled contract, every proposed
action graph, preview and commit receipts, verifier reports, repair feedback and
rollback result in ordered state-machine events.

## Trust boundaries

| Component | May interpret intent | May mutate CAD | May accept result |
| --- | ---: | ---: | ---: |
| Contract compiler model | Yes | No | No |
| Action planner model | Yes | No | No |
| CAD adapter | No | Transactionally | No |
| Contract verifier | No | No | Yes |
| Native orchestrator | Controls sequence | Through adapter | Enforces verifier decision |

The compiler must preserve the caller's exact intent and document ID. The
planner must preserve the document ID and observed revision. Snapshot strings,
adapter metadata and repair messages are treated as untrusted data in prompts.

## Repair policy

Repair is bounded by caller configuration, not by model preference. A rejected
preview is converted into structured verifier issues or adapter diagnostics.
Only that feedback is passed to the next planning attempt. Exhausting the repair
budget produces a refusal without mutating the document.

If the preview passes but applied geometry fails the independent post-commit
check, the orchestrator invokes the adapter's conflict-aware rollback receipt.

## Package boundaries

```text
src/cadrig/
  contract_compiler.py   natural language -> DesignContract
  design_contracts.py    predicates and strict schemas
  action_graph.py        DesignContract -> closed ActionPlan
  native_agent.py        deterministic state machine
  verification.py        independent acceptance authority
  tracing.py             replay-oriented event schema
  executor.py            adapter execution boundary
  adapters/              host capability and transaction implementations
  models/                replaceable provider clients
  benchmarks/            external evaluation and packaging
```

`freecad_workbench/CADRIG` contains only a thin UI client and original visual
assets. Interactive execution and CLI execution use the same `NativeCADAgent`.

## Independence boundary

The current source tree contains no vendored CAD-agent runtime, ReAct parser,
CadQuery translation shim, inherited prompts, inherited session store or
third-party agent UI. Historical alpha provenance remains visible in Git and in
`THIRD_PARTY_NOTICES.md`; it is not part of the native runtime.

## Verification evidence

The automated suite exercises strict contract parsing, scope mutation refusal,
closed action schemas, non-mutating preview, independently verified commit,
verifier-driven repair, repair-budget exhaustion and post-commit rollback. The
full suite must pass before publishing or installing a native-agent revision.
