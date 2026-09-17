# CADRIG

**A Model- and Kernel-Agnostic Harness for CAD Agents**

CADRIG is an execution harness for building, evaluating, and operating CAD
agents with interchangeable language models and geometry kernels. It provides
controlled execution, validation, repair, observability, reproducible traces,
latency and token accounting, and benchmark packaging around an untrusted model.

We do not present CADRIG as a new state-of-the-art CAD-generation model.
CADGen-Bench is used to establish a reproducible external baseline and validate
that the alpha system can execute the complete generation and editing pipeline.
The central claim is that the same controlled CAD-agent pipeline can run with
interchangeable models and CAD kernels while preserving execution traces,
validation, recovery, and evaluation.

## Evaluation model

| Evaluation layer | What it demonstrates |
| --- | --- |
| **CADGen-Bench score** | Quality and validity of generated or edited CAD geometry |
| **Harness evaluation** | Model portability, kernel portability, retries, validation, repair, observability, latency, and cost |

FreeCAD is the first interactive host and Build123d/OCP is the primary headless
benchmark backend. The adapter contract is designed to admit CadQuery and other
CAD kernels without changing the agent-control or evaluation layers.

The alpha implementation and evaluation framing are described in
[the report](docs/REPORT.md). The internal Python package and compatibility CLI
remain named `cadcopilot` during the alpha transition; new users should invoke
the `cadrig` command.

## Design rules

- The CAD kernel, not the model, is the authority on whether geometry succeeded.
- Every agent execution gets a recoverable document snapshot.
- Generated macros are reviewable artifacts and are never silently executed.
- Typed actions remain available for previewable, automatically executed operations.
- Host adapters own native API calls and capability declarations.
- Every edit is validated against the observed document revision.
- A plan is atomic: all actions apply, or none do.
- Dry-run and rollback are first-class operations.
- Unsupported behavior is an explicit refusal, never a silent approximation.
- Native feature identities are semantic and never raw face/edge indices.

## CADRIG Alpha

Version `0.1.0a1` includes:

- strict action-plan and document-snapshot contracts;
- runtime plugin-adapter discovery and capability negotiation;
- optimistic revision checks;
- atomic execution, dry-run and safe rollback;
- a deterministic in-memory reference adapter;
- an optional first-party FreeCAD adapter for native parametric features;
- bring-your-own-model planning through OpenAI-compatible endpoints;
- a chat-first FreeCAD workbench with streaming responses and persistent sessions;
- a multi-iteration ReAct/tool-calling loop with stop and undo controls;
- live document analysis, viewport-aware context and parametric CQ-style modeling;
- geometry quality gates, structured failure feedback and automatic self-correction;
- STEP, IGES, STL and OBJ export tools plus optional vision analysis;
- FreeCAD `.FCMacro` generation with safe and review-only policies;
- a CLI for running typed plans;
- tests for failures, rollback and stale-document protection.

The first copilot vertical slice is included: connect any OpenAI-compatible
model endpoint, observe a document through any installed kernel adapter, turn a
natural-language request into a capability-scoped plan, and dry-run or apply it.
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
$env:CADCOPILOT_MODEL_BASE_URL = "http://localhost:1234/v1"
$env:CADCOPILOT_MODEL = "my-model"
$env:CADCOPILOT_MODEL_API_KEY = "optional-key"
cadrig ask "Create an 80 by 45 by 12 box" --document demo-part
```

Add `--apply` to commit. Use `--response-format json_object` or
`--response-format none` when an endpoint does not support strict JSON Schema.
The model never receives native kernel access: its output must pass the closed
action contract and the selected adapter's validation first.

The bundled `memory` adapter proves the flow. Install a third-party adapter via
the `cadcopilot.adapters` entry-point group to bring your own CAD kernel. See
[the model guide](docs/MODEL_GUIDE.md) and [the adapter guide](docs/ADAPTER_GUIDE.md).

For the native FreeCAD path, see [the FreeCAD guide](docs/FREECAD_GUIDE.md).

## Install the FreeCAD workbench

On this machine, FreeCAD 1.1 is installed at the following path:

```powershell
$freecad = "$env:LOCALAPPDATA\Programs\FreeCAD 1.1"
& "$freecad\bin\python.exe" -m pip install -e .
& "$freecad\bin\python.exe" .\freecad_workbench\install.py
```

Restart FreeCAD and choose **CADRIG** from the workbench selector. Use
the gear button to select a provider, model and agent limits, test the connection,
then describe a part or an edit in the chat box. The agent plans, executes,
inspects and iterates while FreeCAD updates. Use **Stop** to interrupt and **Undo**
to restore the previous document snapshot.

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

For a production cohort, use the resumable scheduler. It runs fixtures
sequentially in isolated attempt directories, strictly validates each STEP,
and reserves the complete per-task cap before making a provider call:

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

.\.venv\Scripts\cadrig.exe benchmark cadgenbench package <run-dir> `
  --require-sanity `
  --submitter "Your Name" --name "CADRIG Alpha" --agree
```

`harness-eval` emits a metric vector rather than a composite score. It reports
observed model/kernel diversity, validation and trace coverage, retries,
execution repair, latency distributions, tokens, and cost when explicit rates
were recorded. Portability is marked demonstrated only when the supplied runs
contain at least two distinct models or kernels.

Production packaging requires official sanity validation and publication
consent, hashes each verified candidate, and atomically self-checks the final
ZIP. It refuses partial runs by default. Use `--allow-incomplete` only for a
local pilot ZIP that will not be treated as a full benchmark submission. The
benchmark architecture and score gates are documented in
[the CADGenBench architecture](docs/CADGENBENCH_ARCHITECTURE.md).

## Adding a CAD host

Implement `KernelAdapter` from `cadcopilot.adapters.base`, declare the exact
action kinds the host supports, and publish its factory in the
`cadcopilot.adapters` package entry-point group. The adapter guide defines the
required transaction and identity behavior.

## License

Original CADRIG code is Apache License 2.0. The vendored agent runtime
is derived from MIT-licensed CadAgent; see [third-party notices](THIRD_PARTY_NOTICES.md).
Contributions and independent CAD-host adapters are welcome.
