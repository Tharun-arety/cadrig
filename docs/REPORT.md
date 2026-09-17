# CADRIG: A Model- and Kernel-Agnostic Harness for CAD Agents

## Alpha Implementation and Reproducible Evaluation on CADGen-Bench

### Abstract

CAD agents combine multimodal or textual reasoning with executable geometry
programs and CAD kernels. Comparing only final geometry scores hides operational
properties that determine whether an agent can be reproduced, inspected, and
safely used. CADRIG is a model- and kernel-agnostic execution harness that
separates model inference, geometry execution, validation, bounded repair,
observability, and evaluation.

CADRIG is not presented as a new state-of-the-art CAD-generation model. The
alpha implementation uses CADGen-Bench as an external geometry-quality baseline
and as an end-to-end validation of the execution pipeline. A separate harness
evaluation measures portability, recovery behavior, trace completeness, latency,
and cost. This separation allows the infrastructure contribution to be assessed
independently of any one model's benchmark score.

## 1. Positioning and research claim

The primary claim is:

> The same controlled CAD-agent pipeline can run with interchangeable models
> and CAD kernels while preserving execution traces, validation, recovery, and
> evaluation.

The alpha currently integrates Gemini through a provider-neutral boundary,
Build123d/OCP as the primary headless kernel path, and FreeCAD as the first
interactive host. These implementations are initial adapters, not architectural
requirements.

## 2. Evaluation layers

| Evaluation layer | Research question | Measures |
| --- | --- | --- |
| **External validation on CADGen-Bench** | Does the configured agent produce geometrically valid and accurate CAD? | Official CAD score, validity, generation/editing breakdown |
| **Harness evaluation** | Does the controlled pipeline remain observable, recoverable, and portable? | Model/kernel substitution, retry and repair outcomes, trace coverage, validity gates, latency, tokens, explicit-rate cost |

A modest CADGen-Bench score does not invalidate the harness contribution. It
instead establishes the quality of the current model/kernel configuration and
provides a reproducible baseline for later configurations.

## 3. Alpha architecture

CADRIG separates six responsibilities:

1. A provider-neutral model boundary receives a bounded task context.
2. A backend adapter exposes the selected CAD kernel and export contract.
3. Generated code executes in an isolated attempt with time and token limits.
4. Kernel-derived checks validate topology, watertightness, solids and bounds.
5. Structured feedback supports bounded repair without hiding failed attempts.
6. The evaluation layer stores immutable candidates, traces, hashes and reports.

The FreeCAD workbench is a client of this engine rather than the benchmark job
runner. This prevents GUI lifecycle and event-loop behavior from determining
benchmark reproducibility.

## 4. Reproducibility controls

The alpha harness records the model identifier, kernel backend, reasoning
setting, CADGen-Bench revision, limits, environment, attempt state, code hashes,
token use, timing, validation output and candidate SHA-256. Cohort execution
uses isolated fixture attempts, durable reservations, exclusive locking,
configuration fingerprints, strict sanity checks and immutable publication.

Unknown or interrupted usage is charged conservatively rather than treated as
zero. Submission archives are created atomically and checked for CRC, exact
layout, verified hashes and explicit publication consent.

## 5. Current evidence

The alpha has completed one CADGen-Bench generation fixture and one editing
fixture with valid, watertight STEP outputs. These public candidate-only runs do
not have access to private ground-truth metrics and therefore are not reported
as scored results. An offline cohort smoke test exercises subprocess execution,
Build123d export, validation, accounting, immutable publication and idempotent
resume. The automated suite currently contains 80 passing tests.

These results demonstrate pipeline viability, not statistical benchmark quality
or state-of-the-art performance.

## 6. Harness evaluation protocol

CADRIG provides a deterministic `harness-eval` command for ordinary
CADGen-Bench runs and resumable cohort directories. It reports a metric vector;
there is deliberately no composite harness score whose weighting could conceal
weak validity, incomplete traces, or missing cost evidence.

| Metric family | Operational definition |
| --- | --- |
| Portability | Distinct observed model and kernel identifiers; demonstrated only when the same task is valid and traced under two or more configurations |
| Workload | Tasks, attempts, valid-task rate, retried tasks and additional attempts |
| Observability | Attempt-level trace, verification and candidate-hash coverage |
| Execution and repair | Turns, code executions, execution success, failed-execution repair opportunities and successful repairs |
| Latency | Trace and wall-clock attempt distributions with count, min, mean, median, p95 and max |
| Usage and cost | Provider-reported token categories and explicit-rate cost; missing prices remain unavailable |
| Failures | Stable counts for attempt status, validation failure and missing traces |

Agent-loop completion and kernel validation are separate measures. A valid
candidate may survive even when the loop stops at a turn limit; conversely, an
agent completion claim does not prove valid geometry. Repair success requires
an observed failed code execution followed by a later successful execution in
the same trace.

Applied to the offline cohort smoke, the evaluator reports one valid task,
complete trace/verification/hash coverage, two successful code executions, 60
tokens and 49.781 traced seconds. It also records `max_iterations` and a false
agent-completion flag. This is execution-path evidence only: the fake offline
model and single Build123d configuration do not demonstrate model or kernel
portability.

Paired experiments are orchestrated as a model-by-kernel matrix. Each cell uses
the same fixture set but retains an independent cohort state, token/cost
admission boundary, configuration fingerprint and lock. The matrix refuses a
per-cell token or cost budget that cannot reserve the full fixture set. Model
pricing is explicit per identifier, because applying one flat rate across
different providers would make cost comparisons unreliable. Matrix completion
automatically regenerates the combined harness evaluation.

## 7. Planned evaluation

The next evaluation sequence is:

1. Run a small, representative calibration cohort spanning generation and
   editing tasks under a declared admission budget.
2. Report validity, failure taxonomy, repair success, trace completeness,
   latency and token/cost distributions.
3. Use the paired matrix runner across at least two model configurations.
4. Repeat the same representative tasks across at least two kernel backends.
5. Freeze the configuration and execute all 81 public CADGen-Bench inputs.
6. Strictly validate, package and submit the first official external score.

## 8. Limitations

- Only two real public fixtures have been executed so far.
- No official leaderboard score has been obtained.
- The FreeCAD experience remains an alpha adapter rather than a mature product.
- Provider-side billing may include failed or retried requests that are not
  represented by successful response usage.
- Model and kernel portability are architectural and test-supported claims;
  comparative empirical results are still pending.

## 9. Release naming

- **Project:** CADRIG
- **Repository:** `cadrig`
- **Release:** CADRIG Alpha
- **Report:** *CADRIG: A Model- and Kernel-Agnostic Harness for CAD Agents*
- **Benchmark section:** External Validation on CADGen-Bench
