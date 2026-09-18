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

### 3.1 Native-agent replacement

On 2026-09-18 the interactive alpha runtime was replaced with CADRIG's own
contract-driven agent. The current source tree contains no vendored CAD-agent
runtime. Earlier exploratory provenance remains visible in repository history
and `THIRD_PARTY_NOTICES.md`, but no inherited ReAct loop, CQ translation layer,
prompt set, session store, streaming implementation or agent UI is shipped.

The native runtime introduces three explicit artifacts:

1. `DesignContract` compiles the exact user intent into deterministic result
   requirements and preservation invariants.
2. A closed typed action graph limits the model to capabilities declared by the
   selected backend adapter; arbitrary generated source is not an action.
3. `AgentTrace` records ordered observe, compile, plan, preflight, preview,
   verification, repair, commit and rollback evidence.

The orchestrator always previews before commit. The model cannot declare success:
only the independent `ContractVerifier` may accept a preview or applied result.
Rejected previews provide structured feedback to a caller-bounded repair loop.
Post-commit verification failure invokes the adapter's conflict-aware rollback.
The FreeCAD workbench is now a thin client that displays the contract, action
graph, verifier report and execution receipt produced by this same headless core.

The replacement increased the complete automated suite to 129 passing tests.
New adversarial cases cover changed-intent refusal, malformed predicates, absence
of an arbitrary-code action, non-mutating preview, verifier-driven repair,
repair-budget exhaustion and post-commit rollback.

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
`readiness-24-composed-001`. The automated suite currently contains 98 passing
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

The sixth cohort initially produced five strict-valid candidates. Fixture 242,
a symmetric mounting-boss length edit, generated an invalid multi-solid BRep
and then exposed a generic limitation in the terminal mesh fallback: it could
move only one end per invocation and accepted only an NPZ input. The helper now
supports `side="both"`, moving each terminal outward by the caller-selected
distance while preserving the existing axis, distance and topology checks. A
separate retry exercised that path on the real fixture. Across seven attempts,
all six tasks are strict-valid after 208,968 tokens and $0.633306. The composed
set passed both strict gates; combined evidence records five repair
opportunities, four recoveries, complete trace coverage, and the original
failed task attempt. The campaign now has 48 unique strict-valid fixtures, and
the automated suite contains 98 passing tests.

The seventh cohort completed all six tasks without a task-level retry after
186,689 tokens and $0.515208. All candidates passed independent strict sanity
validation. Across 26 turns and 22 executions, 17 executions succeeded; all
four observed failed-execution repair opportunities recovered. Three traces
ended with accepted `done` and three reached `max_iterations` while retaining
strict-valid candidates. Trace, verification and candidate-hash coverage were
complete, so no repair cohort or harness change was required. The campaign now
has 54 unique strict-valid fixtures.

The eighth cohort completed all six tasks after 185,439 tokens and $0.479048.
All 27 code executions succeeded, all six candidates passed independent strict
sanity validation, and trace, verification and candidate-hash coverage were
complete. One trace ended with accepted `done`; five reached `max_iterations`
while retaining strict-valid candidates. With no execution failure, repair
opportunity, or final failure taxonomy, this cohort provides a clean baseline
for the harness metrics. The campaign now has 60 unique strict-valid fixtures.

The ninth cohort completed all six tasks after 222,996 tokens and $0.794932.
All 26 code executions succeeded, all candidates passed independent strict
sanity validation, and observability coverage was complete. One trace ended
with accepted `done`; five reached `max_iterations`. Fixture 132 consumed a
near-limit completion that contained no executable code, increasing the cohort
maximum to 62,423 tokens and 221.75 traced seconds without exceeding its task
budget. It subsequently produced three successful refinements. No repair
cohort or code change was required. The campaign now has 66 unique
strict-valid fixtures.

The tenth cohort initially completed four of five tasks. Fixture 217, a radial
through-bore resize, exposed a gap distinct from terminal length edits: the
input BRep passed source validation but its unchanged round trip became
unorientable, and the existing terminal fallback could not express a radial
operation. CADRIG now provides a caller-parameterized cylindrical mesh-region
resize using an explicit axis, perpendicular center, current radius, axial span
and radial delta. A separate retry used this generic path and passed both
strict gates. Across six attempts, all five tasks are strict-valid after
228,129 tokens and $0.686808. The original failed attempt remains visible;
combined evidence records three repair opportunities and two recoveries. The
campaign now has 71 unique strict-valid fixtures, and the automated suite has
98 passing tests.

The eleventh cohort completed all five tasks after 118,865 tokens and
$0.360480. All candidates passed independent strict sanity validation. Across
18 turns and 15 executions, 13 executions succeeded; the one observed repair
opportunity recovered. Three traces ended with accepted `done` and two reached
`max_iterations` with candidates retained. Trace, verification and candidate
hash coverage were complete, so no task-level retry or code change was needed.
The campaign now has 76 unique strict-valid fixtures.

The twelfth and final production cohort completed all five tasks after 117,024
tokens and $0.325208. All five candidates passed independent strict sanity
validation. Across 18 turns and 15 executions, 13 executions succeeded; both
observed failed-execution repair opportunities recovered. Three traces ended
with accepted `done` and two reached `max_iterations` with valid candidates
retained. Trace, verification and candidate-hash coverage were complete, so no
task-level retry or code change was required.

The completed production phase covers 69 unique tasks through 73 attempts,
including four explicit task-level retries. All 69 tasks have a strict-valid
candidate. Its combined metric vector records 317 turns, 289 executions, 243
successful executions, 28 within-trace repair opportunities and 24 successful
repairs. It also records 2,223,243 provider-accounted tokens and $6.519986 at
the declared rates. Attempt-level trace coverage is 98.63%; verification and
candidate-hash coverage are each 94.52% because the denominator retains failed
and abandoned attempts that could not publish a candidate. The failure
taxonomy retains one abandoned attempt, three failed attempts and one missing
trace instead of erasing them after recovery.

The 69 production candidates were composed with the 12 previously verified
readiness candidates into `readiness-81-composed-001`. All 81 unique public
fixtures passed composition-time strict verification and a second independent
strict verification. Production packaging performed a third strict pass and
created a 52,317,179-byte archive containing exactly 81 task directories, 81
STEP candidates and `meta.json`. An independent audit found no missing, extra
or unexpected entries and read every archive member successfully. The archive
SHA-256 is `31b2c2992cab5c1a9543de10c6b3d93a78c718c3d35508382c43113f0fb1603d`.
This establishes full-set pipeline completion and public sanity validity. It
does not by itself establish semantic accuracy. The exact audited archive was
subsequently submitted to CADGen-Bench; the service returned an aggregate score
of **0.3002** and placed `CADRIG Alpha` on the **Unvalidated** leaderboard. This
is the first external alpha baseline, not a validated-leaderboard claim.

The official report records validity **1.0000**, generation **0.2151** across
49 tasks, and editing **0.4305** across 32 tasks. This confirms that the alpha's
dominant gap is semantic geometry quality rather than file validity, with the
generation configuration substantially weaker than editing. The submitted
archive remains immutable; score-driven changes are evaluated in separate
replacement cohorts.

The first improvement cohort targeted two low-scoring generation fixtures
(107 and 116) and one low-scoring editing fixture (202). Fixtures 107 and 116
produced strict-valid candidates with 22 and 69 faces respectively, replacing
the alpha's visibly simplified flange approximation and repeated box. These
are local structural improvements, not claimed score gains until an official
resubmission. Fixture 202 exposed two harness gaps: a failed process could
leave a valid-looking placeholder candidate, and exact-circle feature matching
failed on a split/tessellated planar face. CADRIG now rejects artifacts produced
by failed executions, preserves the newest successful producing turn, supports
caller-selected connected planar mesh-patch translation, and bounds repetitive
feature inspection. The autonomous repair produced a strict-valid, watertight
candidate after 88,508 tokens and $0.248446. The composed three-task improvement
batch passed independent strict validation with all three candidates valid.

The second improvement cohort targeted generation fixtures 123 and 126 plus
editing fixture 207. All three passed independent strict validation after
245,659 tokens and $1.109798. The generation replacements contain 65 and 90
faces; fixture 126 replaced an alpha trace that repeated a single box with a
multi-feature model. Fixture 207 produced a 286-face local three-boss edit and
retained it across a later failed correction. This cohort also exposed two
efficiency/control-flow failures: fixture 123 twice consumed the 16k completion
allowance without executable code, and fixture 207 attempted another model
call after 113,516 tokens even though it already had a strict-valid candidate.
Prompts now require code before explanation with bounded prose. At the attempt
boundary, CADRIG emits an observable zero-token stop only when a prior candidate
has passed strict validation; without such a candidate it continues to fail
closed. This prevents budget exhaustion from converting known-good work into a
task exception.

The third improvement cohort targeted generation fixtures 136 and 150 plus
editing fixture 230. All three candidates passed independent strict validation
after 317,182 provider-accounted tokens and $1.388154. Fixture 136 produced
eight consecutive executable refinements and a 47-face candidate; fixture 150
produced a 63-face candidate but still had one near-16k plan-only response and
one rejected final call whose provider-reported usage took the attempt 3,368
tokens over its nominal cap. Fixture 230 completed a three-segment diameter edit
with a 147-face candidate at 117,980 tokens. CADRIG now reserves an additional
4,096-token prompt-estimation margin near the task boundary. A residual
provider-reported overrun is recorded as rejected and converted to the same
candidate-preserving terminal response only when earlier strict validity exists;
otherwise it remains a hard failure.

The fourth improvement cohort targeted generation fixtures 148 and 134 plus
editing fixture 201. The three initial artifacts passed structural verification
after 321,268 tokens and $1.647716. Fixtures 148 and 134 produced 174-face and
38-face candidates; fixture 148 also behaviorally confirmed the prompt-margin
guard by preserving its last strict-valid candidate and stopping with a
zero-token terminal turn at 117,040 tokens. Semantic audit rejected fixture 201
despite its watertight 447-face STEP: the producing script modified three broad
faces even though the instruction names four distinct pockets. A follow-up run
then tried to manufacture the required count by switching from the stated wall
orientation to four Z-normal faces and later overwrote the working candidate
with an empty shape. CADRIG now treats explicit “each of the N” cardinality as
a semantic acceptance contract, requires unique per-instance topology evidence,
forbids inspection-only STEP exports, and restores the last accepted working
candidate immediately after a rejected turn. Fixture 201 remains excluded from
the replacement set until a repair satisfies the instruction; structural
validity alone is no longer reported as success for this case. Two further
agent retries also failed closed rather than publishing unsupported edits. Their
traces motivated a generic mesh-region inspector that groups connected drafted
or tessellated walls by orientation, side, area and bounding-box axis, plus a
seeded translation fallback with per-region signed motion. On fixture 201 the
inspector deterministically exposed four plausible wall regions and an
operator-selected prototype passed the official validity gate, but that
prototype is not admitted as an autonomous replacement because target-region
interpretation remains unconfirmed.

The fifth improvement cohort targeted generation fixtures 129 and 133 plus
editing fixture 203, whose alpha CAD scores were approximately 0.108, 0.107
and 0.308. The generation candidates passed independent strict validation with
34 faces each. Fixture 133 still spent two near-16k completion allowances in
hidden reasoning without executable output, so prompt-only response-efficiency
mitigation remains incomplete. The initial fixture-203 artifact was an exact
kernel-level re-export of its input despite passing STEP validity. CADRIG now
compares editing input/output volume, area, bounds, center, topology counts and
face-area distribution, rejecting operation-neutral rewrites during the live
repair loop. A subsequent five-blade reconstruction changed the geometry but
produced five disconnected solids from a one-solid source. Editing acceptance
therefore also preserves source solid-body count by default unless the task
explicitly requests a body-level topology change.

The connected-body retry then failed closed after eight inspection turns. Its
trace and a public-input probe motivated a generic BRep fallback for radial
features that isolate as separate solids across a caller-observed attachment
plane. The helper orders those features by polar angle, verifies the observed
source count, cuts selected feature volumes directly from the source, and
rejects body-count changes. In the autonomous confirmation run, the agent used
the helper after one inspection turn, reported seven source features and five
remaining, preserved one solid, passed independent validation and completed in
46,491 tokens at $0.148682. The composed Batch 5 contains all three accepted
candidates and passed strict verification, three of three. Across its initial
cohort and three bounded fixture-203 repairs, Batch 5 used 494,188
provider-accounted tokens and $2.168816. The radial fallback removes selected
features but does not redistribute those that remain, so official rescoring—not
local validity—is still the authority on whether task-203 similarity improved.

The sixth improvement cohort targeted generation fixtures 108 and 140 plus
editing fixture 241. All three initial files passed structural verification
after 291,871 tokens and $1.484472, but semantic audit admitted only the two
generation candidates. Fixture 108 is a one-solid, 44-face flat-pattern model;
fixture 140 is a two-solid, 112-face housing model. They were isolated into a
two-candidate composition that passed strict verification. Fixture 140 also
exposed a response-efficiency failure: three model turns exhausted almost the
entire 15,996-token completion allowance without returning executable code.
After any such response, CADRIG now lowers subsequent provider reasoning effort
for that task and records both the empty response and downgrade in the trace.

The initial fixture-241 edit added concentric cylindrical geometry near the
requested blend while retaining both original 18 mm blend faces. Aggregate
shape change and watertightness therefore supplied a false semantic positive.
For explicit “blend/fillet from X mm to Y mm” instructions, acceptance now
requires analytically observed old-radius faces to decrease and new-radius
faces to increase; proxy rings are rejected. A bounded repair consumed 110,121
tokens and $0.373872, but its producing turns either restored the unchanged
input or removed the old blend without successfully creating the 14 mm blend.
It consequently failed closed with no published candidate. Batch 6 used
401,992 provider-accounted tokens and $1.858344 in total. Fixture 241 remains
excluded, and only fixtures 108 and 140 are retained as local replacement
candidates pending official scoring.

The seventh improvement cohort deliberately mixed simple generation fixture
106, complex generation fixture 117 and two-hole-spacing edit fixture 204.
Fixture 117 produced a one-solid, 55-face candidate through eight executable
turns. An initial sandbox-blocked launch left fixture 106 conservatively
reserved but with zero provider receipts; its clean retry produced a two-solid,
54-face candidate. After one 15,996-token response without code, the adaptive
controller lowered all subsequent calls to low reasoning and recovered with a
1,231-token executable response. The accepted 106 and 117 artifacts form a
two-candidate composition that passed strict verification.

Fixture 204 first spent seven inspection-only turns and crossed its token cap
by 406 provider-reported tokens without a candidate. CADRIG now calibrates its
prompt safety margin from the discrepancy between local estimates and each
provider receipt. Editing runs also receive a controller-enforced inspection
budget: after two non-producing executions, later calls use low reasoning and
a 4,096-token response ceiling; a fifth inspection is refused. The controlled
retry completed in 72,061 tokens, but semantic audit found that it made one
arbitrary cylindrical cut instead of relocating the named pair. A new explicit
hole-spacing contract requires the old analytic cylinder axes to disappear and
the requested number of new axes to demonstrate both the old and new stated
center spacing. A final autonomous run made five structurally exportable but
semantically rejected attempts and failed closed. Across the initial cohort and
two repairs, Batch 7 used 478,269 provider-accounted tokens and $1.662088; the
interrupted zero-receipt attempt additionally retains its conservative
120,000-token, $1.44 scheduler reservation. Fixture 204 remains excluded.

The eighth improvement cohort combined simple generation fixture 113, complex
generation fixture 139 and central-boss fillet-removal fixture 218. Fixtures
113 and 139 retained strict-valid one-solid candidates with 10 and 95 faces.
Fixture 139 repeated the 15,996-token no-code response pattern; adaptive
reasoning again recovered on the next call with a 1,159-token executable
response. Fixture 113 demonstrated candidate rollback by preserving its earlier
valid artifact across five later invalid refinements.

The first fixture-218 artifact filled the boss edge but also cut a novel 55 mm
coaxial recess. CADRIG now accepts explicit fillet-removal edits only when
analytic torus evidence decreases without introducing a new cylinder axis and
radius. A first gated repair correctly failed closed. A public-input prototype
then motivated a guarded, kernel-level torus-defeaturing helper: the caller must
supply observed major/minor radii, center and exact matching face count; the
helper rejects ambiguous selection, body-count changes and results that do not
reduce analytic torus faces. In the autonomous confirmation, the agent inspected
the source, selected both 97/2 mm torus faces centered at `(70, 0, -12)`, invoked
the helper and produced a one-solid, 82-face result. The three accepted Batch 8
candidates passed strict composition verification. Across the initial cohort
and two repairs, Batch 8 used 497,290 provider-accounted tokens and $1.788920.
The confirmation also exposed repeated comment-only `# Done` code blocks after
success; CADRIG now recognizes that form as completion only after strict-valid
review, avoiding further no-op calls without weakening the artifact gate.

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

1. Preserve the submitted archive and its 0.3002 score as the immutable alpha
   baseline while running score-driven replacement cohorts separately.
2. Track the benchmark's validation outcome and update the report only when the
   row's status changes.
3. Continue small, diverse improvement batches; after each batch, classify
   failures, add only general repairs and regressions, and compose strict-valid
   replacements before advancing.
4. Resume paired model and kernel portability experiments after the first
   score, using identical fixture sets and independent execution boundaries.

## 8. Limitations

- All 81 public fixtures have strict-valid candidates, but strict public sanity
  checks do not measure instruction fidelity or geometric similarity to the
  private references.
- The simple/moderate/complex batch labels are deterministic public-input
  heuristics, not validated predictors of private benchmark difficulty.
- The first aggregate score is 0.3002 on the Unvalidated leaderboard; its
  generation/editing breakdown is recorded, but validated status remains
  pending.
- The FreeCAD experience remains an alpha adapter rather than a mature product.
- The mesh fallback emits a faceted BREP and now covers terminal, cylindrical
  radial, annular-plane and connected planar-patch edits. Strict validity is
  demonstrated, but semantic accuracy remains unscored.
- The readiness cohort's exact historical cost is a lower bound because one
  rejected 7,593-token response predates the provider-usage sidecar fix.
- Provider-side billing may still include transport failures or provider retries
  that do not return a usage receipt to the harness.
- Full-set strict verification currently checks candidates serially and emits
  no per-candidate progress, making repeated 81-task validation appear stalled
  even though it completes deterministically.
- Model and kernel portability are architectural and test-supported claims;
  comparative empirical results are still pending.

## 9. Release naming

- **Project:** CADRIG
- **Repository:** `cadrig`
- **Release:** CADRIG Alpha
- **Report:** *CADRIG: A Model- and Kernel-Agnostic Harness for CAD Agents*
- **Benchmark section:** External Validation on CADGen-Bench
