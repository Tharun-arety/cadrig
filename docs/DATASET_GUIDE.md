# CADRIG episode data

Every native CLI and FreeCAD-workbench agent run is recorded as a versioned,
replay-oriented **CADRIG Episode**. The recorder is part of the harness rather
than a provider or CAD-host adapter, so the same evidence format can be used
with future language models and CAD systems.

## Local storage

An editable checkout writes to:

```text
results/episodes/
  training/YYYY-MM-DD/<episode-id>/
  regression/YYYY-MM-DD/<episode-id>/
  evaluation/YYYY-MM-DD/<episode-id>/
```

`results/` is excluded by `.gitignore`. Set `CADRIG_EPISODE_ROOT` or pass
`cadrig ask --episode-root <path>` to use a private artifact volume. Episode
data, native CAD files, renders, provider outputs and benchmark traces must not
be committed to the source repository.

Each completed run contains `episode.json`, `trace.jsonl`, `contract.json`,
`action_graph.json`, `verification.json`, execution receipts and normalized
input/output snapshots. Callers that own exported CAD artifacts can pass them
to `EpisodeStore.record_run(..., artifacts={...})`; those files are copied
under `artifacts/`. Every sidecar is listed with its byte length and SHA-256
digest in the manifest.

## Data policy

Recording and permission to train are separate decisions. New interactive
episodes default to `training_eligible: false`. A run becomes training material
only after its source, rights, geometry quality and labels have been curated.
Credential-bearing metadata fields are redacted before any file is written.

CADGenBench tasks and traces belong in the `evaluation` split. The episode
contract rejects an evaluation record marked as training eligible. When an
evaluation failure reveals a useful weakness, create an independently authored
analogue for `training` or a non-benchmark regression fixture rather than moving
the held-out task into the training corpus.

## Command-line controls

```powershell
cadrig ask "Create an 80 by 45 by 12 box" `
  --document demo-part `
  --task-id synthetic-box-001 `
  --task-source procedural_generator `
  --task-license Apache-2.0
```

Use `--episode-split evaluation` for held-out evaluation. Use
`--training-eligible` only after provenance review. `--no-record-episode` is an
explicit privacy escape hatch. Endpoint credentials are never included in model
metadata.

## Immutability and failure behavior

An episode is staged and atomically moved into its final directory. Reusing the
same trace ID is refused instead of overwriting evidence. Contract-compilation
failures are recorded too, because rejected and repaired trajectories are useful
for later verifier, preference and recovery training.

Episode persistence cannot change the verifier's CAD decision. If evidence
storage fails after execution, the returned run retains its true CAD status and
reports `episode_error` separately.

## Stage record: episode recorder v1

Implemented on 2026-09-18:

- Added the public episode manifest JSON Schema.
- Added atomic, immutable per-run storage and artifact hashing.
- Added recursive credential-field redaction without erasing token metrics.
- Added a hard evaluation/training eligibility boundary.
- Connected recording to both the native CLI and FreeCAD workbench.
- Preserved compilation failures and recorder failures without falsifying CAD
  outcomes.

During installed-workbench verification, the first hidden FreeCAD GUI smoke
launch timed out because its script path contained a space and was not quoted as
one argument. The bounded process was terminated, the argument was quoted, and
the rerun passed workbench registration and construction of all evidence views.

The initial recorder stores normalized snapshots. Native `.FCStd`, STEP and
multi-view render capture will be connected by the native benchmark runner once
it owns export paths and can finalize all artifacts in the same episode.
