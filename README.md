# CADRIG

**A Native, Contract-Driven Harness for Verifier-Governed CAD Agents**

CADRIG is a native CAD agent and execution harness built around three original
public contracts: a testable `DesignContract`, a closed typed action graph, and
a replayable execution receipt. Language models compile intent and propose
actions, but cannot execute code or declare success. Deterministic validators
and the selected CAD backend control acceptance, repair, commit and rollback.

We do not present CADRIG as a new state-of-the-art CAD-generation model.
CADGen-Bench is used to establish a reproducible external baseline and validate
that the alpha system can execute the complete generation and editing pipeline.
The central claim is that the same controlled CAD-agent pipeline can run with
interchangeable models and CAD backends while preserving contracts, execution
traces, validation, recovery and evaluation. True cross-kernel portability is a
target that requires evidence from geometrically independent kernels.

## Evaluation model

| Evaluation layer | What it demonstrates |
| --- | --- |
| **CADGen-Bench score** | Quality and validity of generated or edited CAD geometry |
| **Harness evaluation** | Model portability, kernel portability, retries, validation, repair, observability, latency, and cost |

FreeCAD is the first interactive host and Build123d/OCP is the primary headless
benchmark backend. The adapter contract is designed to admit CadQuery and other
CAD kernels without changing the agent-control or evaluation layers.

The alpha implementation and evaluation framing are described in
[the report](docs/REPORT.md), and the native state machine is specified in
[the architecture guide](docs/NATIVE_AGENT_ARCHITECTURE.md). The Python package
and CLI are both named `cadrig`.

## Design rules

- The CAD kernel, not the model, is the authority on whether geometry succeeded.
- Natural-language intent is compiled into deterministic requirements and invariants.
- The model can propose only typed actions advertised by the active adapter.
- Independent verification is mandatory before commit.
- Every agent execution gets a recoverable document snapshot.
- Generated macros are reviewable artifacts and are never silently executed.
- Typed actions remain available for previewable, automatically executed operations.
- Host adapters own native API calls and capability declarations.
- Every edit is validated against the observed document revision.
- A plan is atomic: all actions apply, or none do.
- Dry-run and rollback are first-class operations.
- Unsupported behavior is an explicit refusal, never a silent approximation.
- Native feature identities are semantic and never raw face/edge indices.

## CADRIG Native Alpha

Version `0.2.0a1` includes:

- strict action-plan and document-snapshot contracts;
- runtime plugin-adapter discovery and capability negotiation;
- optimistic revision checks;
- atomic execution, dry-run and safe rollback;
- a deterministic in-memory reference adapter;
- an optional first-party FreeCAD adapter for native parametric features;
- a native contract compiler and closed action-graph planner;
- a deterministic observe → compile → plan → preflight → execute → verify state machine;
- an independent semantic contract verifier;
- bounded, verifier-feedback-driven plan repair;
- automatic rollback when post-commit verification fails;
- replay-oriented traces containing contracts, plans, receipts and verification reports;
- a thin FreeCAD workbench that exposes contract, action graph, verification and receipt;
- bring-your-own-model planning through OpenAI-compatible endpoints;
- FreeCAD `.FCMacro` generation with safe and review-only policies;
- a CLI for running typed plans;
- tests for failures, rollback and stale-document protection.

The first native-agent vertical slice is included: connect any OpenAI-compatible
model endpoint, observe a document through an installed backend adapter, compile
intent into a design contract, propose a capability-scoped action graph, preview
it, verify it independently and then either commit or refuse it.
The FreeCAD adapter runs in FreeCAD's Python environment and currently covers
primitives, semantic sketches and constraints, parametric extrusion, Boolean
cut, parameter edits, deletion, native observation, transactions and undo.
Broader Part Design operations and hard process isolation remain on the roadmap.

## Try it

```powershell
python -m pytest
$env:PYTHONPATH = "src"
cadrig adapters
cadrig run examples/create_demo_part.json
```

The example creates an in-memory document, adds a box and cylinder, and prints
the resulting immutable snapshot as JSON.

## Bring your own model and CAD kernel

Point the CLI at an OpenAI-compatible local or hosted endpoint. It defaults to a
dry run and prints both the proposed plan and the kernel receipt:

```powershell
$env:CADRIG_MODEL_BASE_URL = "http://localhost:1234/v1"
$env:CADRIG_MODEL = "my-model"
$env:CADRIG_MODEL_API_KEY = "optional-key"
cadrig ask "Create an 80 by 45 by 12 box" --document demo-part
```

Add `--apply` to commit. Use `--response-format json_object` or
`--response-format none` when an endpoint does not support strict JSON Schema.
The command performs separate contract-compilation and action-planning calls.
The model never receives native kernel access: its proposal must pass the closed
action contract, backend preflight and independent design-contract verification.

The bundled `memory` adapter proves the flow. Install a third-party adapter via
the `cadrig.adapters` entry-point group to bring your own CAD backend. See
[the model guide](docs/MODEL_GUIDE.md) and [the adapter guide](docs/ADAPTER_GUIDE.md).

For the native FreeCAD path, see [the FreeCAD guide](docs/FREECAD_GUIDE.md).

## Install the FreeCAD workbench

On this machine, FreeCAD 1.1 is installed at the following path:

```powershell
$freecad = "$env:LOCALAPPDATA\Programs\FreeCAD 1.1"
& "$freecad\bin\python.exe" -m pip install -e .
& "$freecad\bin\python.exe" .\freecad_workbench\install.py
```

Restart FreeCAD and choose **CADRIG** from the workbench selector. Configure an
OpenAI-compatible endpoint, enter an engineering intent, and choose either
**Compile + Preview** or **Verify + Apply**. The dock exposes the resulting
contract, action graph, verification report and complete execution receipt.

## Generate a FreeCAD macro

With the same model environment variables configured:

```powershell
cadrig macro `
  "Create a macro that renames selected objects using a numbered prefix" `
  --output .\generated\RenameSelected.FCMacro
```

The default `safe` policy refuses risky Python. `--policy review` preserves
advanced code with line-level diagnostics, but still does not run it. See the
[macro guide](docs/MACRO_GUIDE.md).

## Run CADGenBench

The benchmark integration uses the official CADGenBench baseline in an isolated
Python 3.12 environment. With `uv` installed:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .\.venv\Scripts\python.exe -r requirements-cadgenbench.txt
```

Put provider credentials in the repository-root `.env` file (which is ignored
by Git), for example `GEMINI_API_KEY=...`. Do not paste a key into the FreeCAD
instruction box.

Run a pilot before spending the model budget for all 81 samples:

```powershell
.\.venv\Scripts\cadrig.exe benchmark cadgenbench run 101 `
  --model gemini/gemini-3.1-pro-preview --reasoning-effort medium `
  --max-iter 4 --max-tokens 80000 --max-tokens-per-call 32768 `
  --max-duration 600
```

For an all-task production attempt, first freeze a diversity-balanced batch
plan. The planner excludes only hash-checked candidates from a run with a
passing official sanity report, ranks generation and editing tasks separately
using public-input complexity proxies, and distributes every stratum across
small batches:

```powershell
.\.venv\Scripts\cadrig.exe benchmark cadgenbench plan-batches `
  --dataset-dir C:/path/to/cadgenbench-data `
  --completed-run results/cadgenbench/readiness-verified `
  --output results/cadgenbench/production-batches/plan.json `
  --model gemini/gemini-3.1-pro-preview --target-batch-size 6 `
  --max-tokens-per-task 80000 --max-tokens-per-call 16000 `
  --max-iter 5 --max-duration 600 --reasoning-effort low `
  --input-usd-per-million <rate> --output-usd-per-million <rate>

.\.venv\Scripts\cadrig.exe benchmark cadgenbench run-batch `
  results/cadgenbench/production-batches/plan.json batch-01
```

`run-batch` verifies the plan and dataset fingerprints before delegating to the
resumable scheduler. Each batch has its own exact token reservation and
conservative all-output cost ceiling. After a batch, retain valid candidates,
classify failures, make only generic harness fixes with regression tests, rerun
failed fixtures in a separate repair cohort, and compose the replacements
before admitting the next batch. Complexity bands are planning heuristics based
only on public input size, drawing-view count, and editing-language signals;
they are not private benchmark labels or predicted CAD scores.

For a single production cohort, use the lower-level resumable scheduler. It
runs fixtures sequentially in isolated attempt directories, strictly validates
each STEP, and reserves the complete per-task cap before making a provider call:

```powershell
.\.venv\Scripts\cadrig.exe benchmark cadgenbench cohort --all `
  --cohort-dir results/cadgenbench/leaderboard-01 `
  --dataset-dir C:/path/to/cadgenbench-data `
  --model gemini/gemini-3.1-pro-preview `
  --token-budget 6480000 --max-tokens-per-task 80000 `
  --max-tokens-per-call 32768 --max-iter 4 --reasoning-effort medium
```

Re-run the identical command to resume; completed candidates are hash-checked
and sanity-checked before they are skipped. Any configuration change is
refused for that cohort directory. Optional dollar budgeting requires prices
you supply explicitly—there are deliberately no live or assumed prices:

```powershell
  --input-usd-per-million <rate> --output-usd-per-million <rate> `
  --cost-budget-usd <limit>
```

`cohort.json` distinguishes provider-reported usage from conservative budget
debits. A missing/corrupt trace or interrupted attempt retains its entire
reservation, so a resume cannot silently spend past the scheduler's admission
bound. Provider-side billing can still differ when a provider charges failed or
retried requests, so production accounts should also have a provider spending
limit.

Agent completion is not authoritative: `[DONE]` is accepted only after a
separate review turn confirms that the latest successful candidate is valid,
watertight and mesh-renderable. Editing tasks with an invalid source STEP may
use a supplied watertight mesh sidecar through a bounded terminal-feature
fallback; the agent still selects the operation axis, side and distance.
Editing prompts also document Build123d's enum-based geometry types and expose
an operation-neutral atomic STEP exporter that falls back to OpenCascade's
direct writer when Build123d cannot serialize an otherwise usable BREP.

For paired harness experiments, run the identical fixtures through an explicit
model × kernel matrix. Every cell is an independently resumable cohort, and the
matrix automatically writes `harness-evaluation.json`:

```powershell
.\.venv\Scripts\cadrig.exe benchmark cadgenbench matrix 101 201 `
  --matrix-dir results/cadgenbench/calibration-matrix-01 `
  --model provider/model-a --model provider/model-b `
  --backend build123d --backend cadquery `
  --token-budget-per-cell 160000 --max-tokens-per-task 80000 `
  --max-tokens-per-call 32768 --max-iter 4 --reasoning-effort medium
```

The per-cell token budget must reserve every selected fixture, preventing a
nominal matrix from silently becoming unpaired. Maximum admitted token exposure
is `models × kernels × token-budget-per-cell`. Optional cost enforcement uses a
JSON `--pricing-file` keyed by exact model identifier; each entry contains
`input_usd_per_million`, `output_usd_per_million`, and
`cost_budget_usd_per_cell`. CADRIG refuses partial pricing coverage rather than
applying one model's rates to another.

The commands print the created run directory and record `manifest.json`. With
the public dataset already cached, strict verification discovers its official
sanity checker automatically. Future runs also record `trace.json` per fixture
with token, timing, recovery and code-execution telemetry. Compare pilots, then
create a leaderboard ZIP:

```powershell
.\.venv\Scripts\cadrig.exe benchmark cadgenbench verify <run-dir> `
  --require-sanity

.\.venv\Scripts\cadrig.exe benchmark cadgenbench report <run-a> <run-b>

.\.venv\Scripts\cadrig.exe benchmark cadgenbench harness-eval <run-or-cohort> `
  -o results/cadgenbench/harness-evaluation.json

.\.venv\Scripts\cadrig.exe benchmark cadgenbench compose <run-a> <cohort-b> `
  -o results/cadgenbench/composed `
  --dataset-dir C:/path/to/cadgenbench-data

.\.venv\Scripts\cadrig.exe benchmark cadgenbench package <run-dir> `
  --require-sanity `
  --submitter "Your Name" --name "CADRIG Alpha" --agree
```

`harness-eval` emits a metric vector rather than a composite score. It reports
observed model/kernel diversity, validation and trace coverage, retries,
execution repair, latency distributions, tokens, and cost when explicit rates
were recorded. Portability is marked demonstrated only when the same task has a
valid candidate and trace under at least two distinct models or kernels;
unpaired configuration diversity is reported as observed evidence only.

`compose` copies only direct task candidates, preserves candidate hashes and
available traces, rejects conflicting duplicates, and performs strict
verification before atomically publishing the combined run directory.

Production packaging requires official sanity validation and publication
consent, hashes each verified candidate, and atomically self-checks the final
ZIP. It refuses partial runs by default. Use `--allow-incomplete` only for a
local pilot ZIP that will not be treated as a full benchmark submission. The
benchmark architecture and score gates are documented in
[the CADGenBench architecture](docs/CADGENBENCH_ARCHITECTURE.md).

## Adding a CAD host

Implement `KernelAdapter` from `cadrig.adapters.base`, declare the exact
action kinds the host supports, and publish its factory in the
`cadrig.adapters` package entry-point group. The adapter guide defines the
required transaction and identity behavior.

## License

CADRIG is licensed under Apache License 2.0. The current source tree contains no
vendored CAD-agent runtime. A transparent note about the removed pre-native
prototype remains in [third-party notices](THIRD_PARTY_NOTICES.md). Contributions
and independent CAD-host adapters are welcome.
