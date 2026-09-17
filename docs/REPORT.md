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
zero. Provider usage is also written to an atomic sidecar immediately after
each response and before the attempt-cap decision, so an over-cap response is
not lost when the agent rejects that turn. Submission archives are created
atomically and checked for CRC, exact layout, verified hashes and explicit
publication consent.

## 5. Current evidence

The alpha has executed a real ten-task submission-readiness cohort spanning five
generation and five editing fixtures. Seven candidates passed the strict public
sanity gate: three of five generation tasks and four of five editing tasks. All
ten attempts retained traces. The run recorded 31 agent turns, 26 code
executions, an execution-success rate of 65.4%, and five successful repairs
across six observed repair opportunities. The persisted traces contain 404,152
tokens; a rejected over-cap call reported 7,593 additional tokens that the
original trace format did not retain. The accounting path has since been fixed
to persist such calls before acceptance or rejection.

The cohort's initial 70% validity did not clear the declared 80% readiness gate,
so the remaining production batch was not launched. A bounded repair campaign
then recovered the two generation failures with low reasoning and a 16k
per-call limit. The editing failure exposed an invalid source STEP plus a
watertight public mesh sidecar. CADRIG now gives the agent a deterministic
mesh-domain terminal-feature operation and reconstructs a sewn faceted BREP;
the agent selected the axis, side and 10 mm extent from the task and render.

The repair and methodology-audit campaign retained six attempts, including two
failed fixture-240 diagnostics and one valid but superseded prompted-example
run. It recorded 165,729 tokens and $0.471998 at the declared rates. An unbiased
rerun first inspected the model, then independently selected X/min/10 mm and
passed the strict gate. All three eligible replacements are therefore valid.
Across all real runs, CADRIG has attempted 12 unique public fixtures and obtained
a strict-valid candidate for each. These public candidate-only runs do not have
access to private ground-truth metrics and are not reported as scored results.
The 12 eligible candidates were merged by the atomic composer and all 12 passed
a second strict verification in `readiness-12-composed-002`. A fingerprinted
production plan now partitions the remaining 69 fixtures into twelve cohorts of
five or six tasks. Every cohort mixes generation and editing tasks and contains
simple, moderate and complex public-input heuristic bands. This planning
metadata is not a claim about private benchmark difficulty or accuracy.

The first six-task production cohort completed with six strict-valid candidates,
191,392 provider-reported tokens and $0.516754 at the declared rates. All six
traces and candidate hashes are present. Twenty-two of 27 code executions
succeeded; each of the three observed repair opportunities recovered, while two
traces ended through the strict agent-completion path and four retained valid
candidates at the turn limit. The six candidates were atomically combined with
the previous 12 and passed double strict verification in
`readiness-18-composed-001`.

The second six-task cohort initially produced five valid candidates. Fixture
205 exhausted its first attempt without output because the agent compared
Build123d geometry enums with string literals and Build123d could not re-export
the imported BREP, although direct OpenCascade transfer succeeded. Generic
enum guidance and an operation-neutral atomic STEP fallback enabled a bounded
repair attempt to identify and fill the two target holes. Across the original
and repair cohorts, all six tasks are valid after seven attempts, 202,270
tokens, and $0.635510. Cross-cohort evaluation now groups matching task IDs and
execution configurations as retries rather than inflating the task count.
The resulting candidates passed double strict verification in
`readiness-24-composed-001`. The automated suite currently contains 96 passing
tests.

The third six-task cohort completed without a task-level retry: all six outputs
were strict-valid after 171,299 tokens and $0.477248. It retained 26 turns and
24 executions with 19 successful executions, complete trace/verification/hash
coverage, and no final failure taxonomy. Two of three within-trace repair
opportunities recovered; the remaining failed refinement occurred after a
valid candidate had already been preserved. No geometry repair or harness
change was required. The cumulative `readiness-30-composed-001` set passed both
strict verification stages with 30 candidates.

The fourth six-task cohort also completed without a task-level rerun. All six
outputs passed strict sanity validation after 178,279 tokens and $0.518398.
Across 27 turns and 24 executions, 21 executions succeeded. Fixture 243
exercised best-candidate preservation: a failed first program was repaired on
the next turn, later refinements regressed, and the harness retained the valid
repaired candidate. This produced one observed repair opportunity and one
successful repair. Trace, verification and candidate-hash coverage remained
complete. The campaign therefore has 36 unique strict-valid public fixtures;
semantic benchmark quality remains unknown until official scoring.

The fifth six-task cohort encountered an infrastructure-only interruption on
fixture 131 when its first process lacked outbound provider access. The
scheduler fail-closed: it marked the zero-usage attempt abandoned and retained
the full 80,000-token/$0.96 reservation rather than assuming it was free. A
separate bounded retry completed fixture 131. Across the original and repair
cohorts, all six unique tasks are strict-valid after seven attempts, 211,893
provider-accounted tokens and $0.577086. Combined evaluation records one
retried task, two within-trace repair opportunities, two successful repairs,
and the abandoned attempt as `abandoned` plus `missing_trace`. The composed
six-candidate set passed both strict verification stages. The campaign now has
42 unique strict-valid public fixtures.

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

1. Continue admitting the remaining 39 unattempted public fixtures through immutable plan
   `production-batches-002`, one five- or six-task cohort at a time, using the
   low-reasoning, 16k-per-call policy and strict completion gate.
2. After every cohort, preserve strict-valid outputs, classify failures, add
   only generic fixes and regression tests, and compose separately rerun
   replacements before admitting the next cohort.
3. Apply the invalid-STEP mesh fallback only when the supplied source BREP fails
   validation and a watertight sidecar exists; retain this path in provenance.
4. Compose the new cohorts with the 12-candidate verified staging run.
5. Strictly validate all 81 outputs, package and submit the first official
   external score.
6. Resume paired model and kernel portability experiments after the first score.

## 8. Limitations

- Only 42 unique real public fixtures have been executed, although all 42 now
  have strict-valid candidates.
- The simple/moderate/complex batch labels are deterministic public-input
  heuristics, not validated predictors of private benchmark difficulty.
- No official leaderboard score has been obtained.
- The FreeCAD experience remains an alpha adapter rather than a mature product.
- The mesh fallback emits a faceted BREP and has been exercised on only one
  invalid-source editing fixture; its semantic accuracy remains unscored.
- The readiness cohort's exact historical cost is a lower bound because one
  rejected 7,593-token response predates the provider-usage sidecar fix.
- Provider-side billing may still include transport failures or provider retries
  that do not return a usage receipt to the harness.
- Model and kernel portability are architectural and test-supported claims;
  comparative empirical results are still pending.

## 9. Release naming

- **Project:** CADRIG
- **Repository:** `cadrig`
- **Release:** CADRIG Alpha
- **Report:** *CADRIG: A Model- and Kernel-Agnostic Harness for CAD Agents*
- **Benchmark section:** External Validation on CADGen-Bench
