# CADRIG architecture

The benchmark-first generation and editing architecture is specified separately
in [docs/CADGENBENCH_ARCHITECTURE.md](docs/CADGENBENCH_ARCHITECTURE.md). It adds a
headless candidate-search engine behind this product-facing adapter architecture;
the FreeCAD UI becomes a client of that engine rather than its execution host.

```text
Copilot UI / CLI / API
       |
Context + user intent
       v
ModelClient (user-supplied model API)
       |
       +-----------------------------+
       |                             |
       v                             v
Macro generator                 Typed planner
       |                             |
AST policy review               Plan validation
       |                             |
Reviewable .FCMacro             KernelAdapter
       v
Explicit user execution         Safe execution
       |
  +----+---------+-------------+
  |              |             |
FreeCAD       CadQuery      Other CAD host
adapter       adapter       adapter/plugin
  |              |             |
Native host APIs and geometry kernels
```

## Ownership

The harness owns conversation, intent interpretation, planning, safety policy
and audit history. A host adapter owns document observation, capability truth,
native calls, rebuild checks and rollback. The CAD host remains the authority
for its document and geometry.

The model is replaceable and untrusted. `ModelClient` translates one provider's
API into a small completion boundary. `CopilotPlanner` supplies the current
immutable document snapshot, scopes the response schema to the active adapter's
capabilities, then independently checks document identity, revision and action
kinds. `CopilotAgent` is the observe-plan-execute coordinator. It dry-runs by
default and requires an explicit apply decision for mutation.

`FreeCADMacroGenerator` is the primary open-ended automation path. It asks the
model for a structured macro artifact, parses the Python AST, reports unsafe
imports and calls, and writes only `.FCMacro` files. Generation and execution
are separate authority boundaries: saving a macro never executes it.

## Core contracts

- `ActionPlan`: ordered closed actions against one exact document revision.
- `DocumentSnapshot`: immutable semantic observation returned by a host.
- `AdapterMetadata`: host identity and exact supported action kinds.
- `ExecutionReceipt`: accepted/refused result with before/after snapshots and
  diagnostics.

The first contract version intentionally models only a small action vocabulary.
New operations must be added as typed schema versions, not arbitrary scripts.

## Process boundary

The protocol does not require adapters to run in-process. A FreeCAD worker,
desktop plugin or remote CAD service can expose the same messages over JSON-RPC,
named pipes or another authenticated transport. Transport is replaceable and
does not change execution authority.
