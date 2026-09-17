# Product roadmap

The parity target is a useful native CAD copilot, not merely text-to-mesh or
macro generation. Each release must pass a representative task benchmark and
must report unsupported tasks truthfully.

## R0 — Kernel-neutral execution foundation

- [x] Closed typed action plans and immutable snapshots.
- [x] Adapter discovery and capability negotiation.
- [x] Atomic execution, dry-run, optimistic concurrency and rollback.
- [x] Deterministic reference adapter and contract tests.
- [ ] Publish protocol JSON Schema and compatibility policy.

## R0.5 — Bring-your-own-model copilot loop

- [x] Provider-neutral `ModelClient` boundary.
- [x] Built-in OpenAI-compatible connector for local and hosted models.
- [x] Capability-scoped structured planning from native document snapshots.
- [x] Deterministic post-model policy checks and dry-run-by-default CLI.
- [ ] Multi-turn observe-plan-execute-inspect loop with bounded recovery.
- [ ] Provider plugin discovery, credential profiles and streaming events.

## R0.7 — FreeCAD macro copilot

- [x] Structured `.FCMacro` generation through a user-supplied model.
- [x] Syntax validation and line-level static risk diagnostics.
- [x] Safe rejection and review-only generation policies.
- [x] Explicit artifact persistence without automatic execution.
- [x] Embedded FreeCAD macro editor with diff, approve and run controls.
- [x] Confirmed execution with document backup, rollback and audit receipt.
- [x] Macro repair loop using captured FreeCAD errors and post-run inspection.
- [ ] Process-isolated execution worker with a hard timeout.

## R0.8 — CADGenBench measurement foundation

- [x] Isolated Python 3.12 environment with a pinned official CADGenBench build.
- [x] One-sample and all-sample baseline runner with reproducible manifests.
- [x] Strict output completeness checks and official STEP sanity-check integration.
- [x] Leaderboard-compatible ZIP packaging with explicit publication consent.
- [x] Zero-model-call sample pilot and real OCP/STEP validity smoke test.
- [x] Model-backed generation pilot with strict STEP validation.
- [x] Model-backed editing pilot with strict STEP validation.
- [x] Deterministic experiment reporting and per-turn execution telemetry.
- [x] Durable invocation logs, interruption manifests and post-run reconciliation.
- [x] Hashed candidate verification and atomic self-validating submission ZIPs.
- [x] Resumable fixture scheduling with conservative cross-run token/cost admission.
- [x] Provider-usage sidecars that retain responses rejected by the task cap.
- [x] Strict completion gate requiring valid, watertight, mesh-renderable output.
- [x] Agent-selected mesh-domain fallback for an invalid editing source STEP.
- [x] Atomic multi-cohort candidate composition with duplicate conflict checks.
- [x] First-class harness evaluation for portability, recovery, observability,
  latency, tokens and explicit-rate cost without a composite score.
- [x] Resumable paired model-by-kernel calibration matrices with isolated cell
  budgets, fingerprints, locking and automatic harness evaluation.
- [ ] Complete 81-sample baseline run and first official score.

## R1 — Useful FreeCAD copilot

- [x] Attach to an open FreeCAD document and observe selection and feature tree.
- [x] Create primitives, semantic sketches and constraints, parametric extrusions
  and Boolean cuts through native FreeCAD objects.
- [ ] Create and edit sketches, constraints, pads, pockets, revolves, holes,
  chamfers, fillets and patterns through typed actions.
- [ ] Preview plan impact, apply it as one transaction, rebuild and undo.
- [x] Provide an embedded macro copilot panel with action confirmation.
- [ ] Pass a 30-task benchmark covering creation and modification of existing
  models with no silent document corruption.

## R2 — Professional workflow breadth

- [ ] Assemblies, mates and interference checks.
- [ ] Drawings, views, dimensions, annotations and exports.
- [ ] Sheet metal and common manufacturing features.
- [ ] Safe batch operations across document sets.
- [ ] Parameter tables, standard parts and reusable feature templates.

## R3 — Context-aware engineering agent

- [ ] Observe-plan-execute-inspect-recover loop over native state.
- [ ] Selection-aware commands such as “move these holes” and “match this face”.
- [ ] Constraint diagnosis and bounded repair alternatives.
- [ ] Engineering calculations with explicit inputs and provenance.
- [ ] Persistent project memory that never overrides document truth.

## R4 — Multimodal design assistance

- [ ] Extract silhouettes, circles, slots and repeated features from images.
- [ ] Ask for one scale or known dimension, then propose editable assumptions.
- [ ] Compare rendered candidates to references and show confidence overlays.
- [ ] Treat hidden geometry as unknown and request additional views when needed.
- [ ] Produce native parametric features rather than a visually similar mesh.

## R5 — Measurable parity

- [ ] Public benchmark of at least 100 creation, edit, drawing, assembly and
  automation tasks.
- [ ] Target at least 85% successful autonomous completion on supported tasks.
- [ ] Guarantee explicit refusal or rollback for failed supported tasks.
- [ ] Installer, crash isolation, telemetry controls and version compatibility.
- [ ] Compare current capabilities against MecAgent using reproducible evidence.
