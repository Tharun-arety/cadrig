# CADRIG CADGen-Bench architecture

## 1. Objective

Build a model- and kernel-agnostic execution harness for CAD agents. CADGen-Bench
provides reproducible external validation of geometry quality and end-to-end
pipeline operation; it is not evidence that CADRIG itself is a new generative
model.

A benchmark result is not considered reproducible until all 81 public inputs
have been processed, the submission passes the public sanity checker, and the
resulting report is stored with the exact code, model, prompt, configuration and
dataset revision that produced it.

CADGen-Bench is the geometry-quality layer. Harness evaluation separately
measures model and kernel portability, retries, validation, repair,
observability, latency and cost. Native feature history, constraints,
manufacturability and human review remain separate product acceptance gates.

## 2. Architectural decisions

1. **The benchmark engine is headless.** FreeCAD's Qt process is not the job
   runner. It may submit work and display progress, but model calls, generated
   code, STEP validation and rendering run outside the UI process.
2. **Build123d/OCP is the primary benchmark backend.** It is STEP-native,
   process-isolatable and consistent with the strongest validated public
   baseline. FreeCAD and CadQuery are replaceable kernel backends.
3. **Perception is separated from synthesis.** The model first converts the
   prompt, drawing and input STEP into a structured `PartSpec` or `EditSpec`.
   CAD code is generated from that contract, not directly from an unstructured
   image description.
4. **One attempt is insufficient.** The engine generates several candidates,
   rejects invalid geometry, critiques valid candidates against observable
   evidence, performs bounded revisions and selects the strongest artifact.
5. **STEP is the artifact of record.** Source code, native documents and renders
   are diagnostic sidecars. The final contract is one valid `output.step` for
   every sample.
6. **Artifacts and decisions are immutable.** A revision creates a new
   candidate. It never silently mutates or replaces the evidence for an earlier
   candidate.
7. **Benchmark methodology must generalize.** No sample-ID-specific solutions,
   hand-authored fixture geometry or private-ground-truth assumptions are part
   of the agent. Validated-leaderboard eligibility is an architectural concern.

## 3. System view

```text
CADGenBench dataset                    FreeCAD workbench
        |                                     |
        v                                     v
  TaskSourceAdapter                    InteractiveTaskAdapter
        |                                     |
        +---------------+---------------------+
                        v
                 Task Normalizer
                        |
          +-------------+-------------+
          |                           |
          v                           v
 Drawing/Prompt Interpreter      STEP Characterizer
          |                           |
          +-------------+-------------+
                        v
             PartSpec / EditSpec
                        |
                        v
                Strategy Router
                 /             \
          generation           editing
              |                   |
              +---------+---------+
                        v
             Candidate Orchestrator
                        |
            +-----------+-----------+
            |           |           |
            v           v           v
        Kernel       Kernel       Kernel
        worker       worker       worker
            |           |           |
            +-----------+-----------+
                        v
             STEP inspection pipeline
       validity -> measurements -> topology
          -> feature checks -> canonical pose
                        |
                        v
               Render/Compare/Critic
                        |
                 revise or select
                        |
                        v
                   output.step
                        |
          +-------------+-------------+
          v                           v
  Submission packager             FreeCAD importer
```

## 4. Core contracts

These contracts belong in a new benchmark engine package. The existing
transactional `ActionPlan` and `DocumentSnapshot` contracts remain the boundary
for applying reviewed changes to a live CAD host.

### `TaskInput`

Normalized, read-only benchmark or interactive input.

```text
task_id
task_type: generation | editing
instruction
drawing_assets[]
input_step?          # required for editing
dataset_revision?
source_metadata
```

### `PartSpec`

A model-generated proposal that is schema-validated before synthesis.

```text
units
coordinate_frame
overall_envelope
datum_features[]
profiles[]
features[]           # extrude, cut, hole, slot, revolve, fillet, pattern, ...
dimension_graph[]    # value, tolerance, endpoints and provenance
topology_expectation # solids, through-holes and enclosed voids
interfaces[]         # mating/mounting features and relative locations
symmetry[]
assumptions[]
uncertainties[]
evidence_links[]     # drawing view/annotation or input-STEP observation
```

Every numeric requirement records provenance. A dimension read from a drawing,
an engineering inference and an arbitrary default must never look equivalent.

### `EditSpec`

```text
base_characterization
requested_changes[]
preserve_regions[]
allowed_global_changes[]
result_expectations
uncertainties[]
```

An editing strategy must identify the intended local delta and the geometry
that should remain invariant before producing code.

### `BuildProgram`

```text
candidate_id
parent_candidate_id?
backend
source_code
strategy
spec_revision
generation_parameters
```

### `CandidateArtifact`

```text
candidate_id
source_code_path
step_path?
execution_receipt
analysis
render_set
critic_findings[]
proxy_score
status
```

### `CandidateAnalysis`

```text
validity
solid_count
shell_count
face_count
volume
bounding_box
center_of_mass
betti_numbers
detected_features[]
dimension_checks[]
interface_checks[]
canonical_pose
warnings[]
```

### `RunManifest`

Records the complete reproducibility boundary:

```text
run_id
git_revision
dataset_revision
engine_version
model/provider identifiers
prompt hashes
configuration
environment/container digest
sample_results[]
token, time and cost totals
```

Secrets and raw credentials are never written to a manifest.

## 5. Generation pipeline

1. Load `description.yaml` and all drawing assets.
2. Classify drawing views, crop evidence regions and extract annotations.
3. Produce a schema-valid `PartSpec`, including explicit uncertainty.
4. Run deterministic consistency checks on dimensions, units, symmetry and
   impossible feature relationships.
5. Select one or more construction strategies, such as prismatic sketch-first,
   revolve-first, profile-and-cut, or multi-body Boolean composition.
6. Generate `K` independent `BuildProgram` candidates. Diversity should come
   from construction strategy and uncertain dimension hypotheses, not merely
   sampling temperature.
7. Execute each program in a disposable kernel worker with strict time and
   resource limits.
8. Apply the CADGenBench validity gate. Invalid candidates cannot be selected.
9. Inspect valid candidates and render a fixed view set: front, rear, left,
   right, top, bottom and isometric.
10. Compare renders, dimensions, topology and interfaces against `PartSpec`
    evidence. Produce structured critic findings.
11. Revise only the strongest candidates under a bounded token/time budget.
12. Select the best candidate, canonicalize its pose, re-export it and run the
    validity gate again.

## 6. Editing pipeline

Editing is not generation with an extra file in the prompt.

1. Import and validate `input.step`.
2. Characterize solids, faces, holes, pockets, envelopes, symmetries and likely
   construction features without relying on unstable face indices.
3. Parse the request into `EditSpec`: required delta plus preserve regions.
4. Prefer local feature recognition and direct BREP operations for simple edits.
5. Use reconstruction only when a stable local edit cannot be expressed.
6. Generate multiple edit candidates and compare each candidate to the input:
   requested regions must change; preserve regions must remain geometrically
   close.
7. Penalize no-op results explicitly. CADGenBench caps a no-op editing result at
   `0.4`, so a candidate is not complete merely because it remains valid.
8. Revalidate, canonicalize and export one `output.step`.

The editing critic uses a change mask and a preservation mask. This avoids the
common failure where an edit is technically present but unrelated geometry has
drifted or disappeared.

## 7. Metric-aligned local objective

The private ground truth is unavailable, so the engine uses evidence-based
proxies. These proxies guide candidate selection but must never be reported as
the official CAD score.

| Official concern | Local selection signal |
|---|---|
| Validity gate | Official public sanity checker plus OCP BREP and mesh checks |
| Shape similarity | Drawing silhouette/depth agreement, dimension residuals, envelope and volume plausibility |
| Topology | Expected versus measured solids, through-holes and enclosed voids |
| Interface | Exact size and relative pose of mounting holes, slots, seats and datums |
| Editing improvement | Requested-region change satisfaction plus unchanged-region preservation |

Candidate selection is lexicographic at the top level:

1. valid, watertight, meshable single-part result;
2. task requirement coverage;
3. interface and topology agreement;
4. dimensional and silhouette agreement;
5. lower uncertainty, complexity and repair count.

A weighted scalar may rank candidates within a tier, but it cannot compensate
for invalid geometry or a missed required feature.

## 8. Process and concurrency model

```text
run coordinator process
  |-- provider client pool       # network I/O, bounded concurrency
  |-- sample worker process 101
  |     |-- candidate kernel process A
  |     |-- candidate kernel process B
  |     `-- render/inspect worker
  |-- sample worker process 102
  `-- event + artifact writer
```

- A kernel worker gets one candidate directory and no other sample's state.
- Generated code runs in a child process, never in the coordinator or UI.
- Timeouts terminate the process rather than attempting cooperative recovery.
- Provider credentials remain in the coordinator/provider boundary.
- Rendering and OCC tessellation use worker processes because they are CPU and
  failure intensive.
- Parallelism is configured independently for samples, model calls, candidate
  execution and rendering to prevent oversubscription.
- Every event carries `run_id`, `task_id`, `candidate_id` and monotonic sequence.
- Provider-reported usage is persisted atomically before the task-cap decision.
  A response rejected for crossing the cap therefore remains chargeable and
  auditable even when it is absent from the accepted agent-turn trace.

Comparative calibration adds a matrix coordinator above the cohort scheduler.
It executes deterministic model-by-kernel cells sequentially, with the same
fixture set in every cell. Each cell keeps its own resumable state and full-set
admission budget; `matrix.json` stores the immutable experiment fingerprint and
aggregate ledger. A separate matrix lock prevents concurrent coordinators while
the existing cohort locks continue to protect individual cells.

For the FreeCAD product, the panel submits a job and listens to events. Only the
small final native-document transaction is marshalled onto FreeCAD's main
thread. Long-running inference, rendering and candidate search never block Qt.

## 9. Backend boundaries

### `KernelBackend`

```text
capabilities()
execute(build_program, work_dir, limits) -> ExecutionReceipt
import_step(path) -> ShapeHandle
export_step(shape, path)
measure(path) -> GeometryMeasurements
```

Initial implementations:

- `Build123dBackend`: primary generation and reconstruction backend.
- `FreeCADWorkerBackend`: native editing experiments and product integration.
- `CadQueryBackend`: comparison/fallback backend.

### `ModelProvider`

Provider-specific streaming, retries and thought-signature handling remain
behind one interface. Higher-level orchestration consumes typed responses and
usage data, never raw provider payloads.

### `TaskSource`

- `CADGenBenchTaskSource` reads the public dataset contract.
- `InteractiveTaskSource` adapts a user request, attachments and active document.
- Future internal engineering suites use the same normalized `TaskInput`.

## 10. Repository layout

```text
src/cadcopilot/
  engine/
    contracts.py
    orchestrator.py
    strategy.py
    generation.py
    editing.py
    critic.py
    selector.py
    events.py
  perception/
    drawing.py
    dimensions.py
    step_characterizer.py
  inspection/
    validity.py
    measurements.py
    topology.py
    features.py
    render.py
    canonicalize.py
  kernels/
    base.py
    build123d_worker.py
    freecad_worker.py
    cadquery_worker.py
  benchmarks/cadgenbench/
    dataset.py
    runner.py
    scheduler.py
    matrix.py
    harness_eval.py
    sanity.py
    package.py
    reports.py
  providers/
    base.py
    openai_compatible.py
freecad_workbench/CADCopilot/
  ... thin job client and native apply/review UI ...
tests/
  engine/
  perception/
  inspection/
  kernels/
  benchmarks/
```

The current `freecad_workbench/.../agent_runtime` contains useful provider,
geometry-analysis and session logic, but it must not remain a second independent
agent architecture. Reusable logic moves into `src/cadcopilot`; the workbench
imports it as a client library.

## 11. Artifact layout

```text
results/<run_id>/
  manifest.json
  summary.json
  <task_id>/
    task.json
    spec.json
    trace.jsonl
    candidates/
      <candidate_id>/
        program.py
        execution.json
        analysis.json
        renders/
        output.step
    selected.json
    output.step
```

A comparative experiment uses this enclosing structure:

```text
results/<matrix_id>/
  matrix.json
  harness-evaluation.json
  cells/
    <model-kernel-cell>/
      cohort.json
      manifest.json
      <task_id>/
        output.step
        trace.json
```

The submission packager copies only the required `output.step` files and root
metadata into the official ZIP layout. Diagnostic artifacts stay out of the
submission but remain available for regression analysis.

## 12. Failure taxonomy

Every unsuccessful sample is assigned exactly one primary class:

- input/perception failure;
- inconsistent or incomplete specification;
- strategy-selection failure;
- code-generation failure;
- kernel exception or timeout;
- invalid or non-watertight BREP;
- topology mismatch;
- dimension/interface mismatch;
- edit was a no-op;
- edit damaged preserve regions;
- budget exhausted;
- packaging or canonicalization failure.

This taxonomy drives work selection. Prompt tuning is not an acceptable response
to a kernel, validator or orchestration failure.

## 13. Delivery plan and gates

### M0 — Reproducible benchmark foundation

- Add the official dataset loader, one/all-sample runner, sanity checker wrapper
  and submission packager.
- Add immutable run manifests and per-sample traces.
- Run the official baseline on one generation and one editing sample, then all
  81 samples.

Gate: a complete submission can be reproduced from one command and no sample is
silently missing.

### M1 — Valid artifact engine

- Add isolated Build123d workers, canonicalization, retry policy and validity
  triage.
- Refactor geometry inspection out of the FreeCAD UI runtime.

Gate: at least `98%` local validity over all 81 samples.

### M2 — Structured generation

- Implement `PartSpec`, dimension graph, drawing evidence extraction, strategy
  routing and multi-candidate generation.

Gate: every selected feature/dimension is traceable to evidence or an explicit
assumption; first official aggregate target `>= 0.50`.

### M3 — Metric-directed search

- Add orthographic render comparison, topology/interface checks, critic revisions
  and proxy-based candidate selection.

Gate: beat the current second validated tier and reach `>= 0.54` aggregate.

### M4 — Editing engine

- Implement STEP characterization, `EditSpec`, change/preserve masks and local
  BREP edit strategies.

Gate: editing score `>= 0.66`, no-op rate below `5%`, overall validity `>= 98%`.

### M5 — Validated-leader attempt

- Freeze prompts and configuration, rerun from a clean environment, package and
  submit with complete methodology documentation.

Gate: validated aggregate `> 0.64` with no hard-coded fixture solutions.

### M6 — Stretch and product integration

- Use failure clusters and ablations to improve difficult feature families.
- Connect the same engine to the FreeCAD review/apply workflow.

Gate: aggregate `> 0.81` and a responsive UI where background work never blocks
normal FreeCAD interaction.

## 14. Explicit non-goals for the first implementation

- Assemblies and kinematic mates.
- General finite-element or manufacturing-process simulation.
- Training a new foundation model before establishing agent baselines.
- Recreating the private grader or estimating an unofficial CAD score locally.
- Optimizing the current chat panel before the headless benchmark path works.

## 15. First implementation slice

The first code change following this design should implement only the M0 vertical
slice:

```text
cadrig benchmark cadgenbench run <sample|--all>
cadrig benchmark cadgenbench verify <run_dir>
cadrig benchmark cadgenbench package <run_dir>
```

It should initially delegate candidate generation to the official baseline or a
small `CandidateGenerator` interface. This gives the project a measured starting
score and stable experiment harness before new perception or synthesis logic is
added.
