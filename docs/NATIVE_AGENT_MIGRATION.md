# Native-agent migration record

Date: 2026-09-18

Release boundary: `0.2.0a1` (`CADRIG Native Alpha`)

## Objective

Replace the exploratory third-party agent runtime completely with a native
CADRIG architecture. Keep historical attribution truthful, but ship no inherited
agent loop, prompt set, CQ translation layer, session store, streaming layer or
agent UI.

## Delivered

- Renamed the Python implementation boundary from `cadcopilot` to `cadrig`.
- Renamed the FreeCAD module from `CADCopilot` to `CADRIG`.
- Added versioned `DesignContract` predicates for desired state and invariants.
- Added a separate strict contract-compilation model boundary.
- Added a closed typed action-graph planner with no arbitrary-code action.
- Added the deterministic native-agent state machine.
- Added independent preview and post-commit contract verification.
- Added bounded verifier-feedback repair and conflict-aware rollback.
- Added ordered, replay-oriented agent traces.
- Replaced the inherited FreeCAD interface with an original thin evidence UI.
- Removed the vendored agent runtime and obsolete compatibility package.

## Problems found and fixed

### Workspace ACL blocked the package move

The repository is owned by a different Windows SID than the execution account.
A direct move partially copied the FreeCAD module but could not move the Python
package. The source was left intact, the destination was verified, and a scoped
copy was performed before the old package was removed. No unrelated path was
modified.

### The first GUI smoke command did not quote the workspace path

FreeCAD did not execute the smoke script because the repository path contains a
space. The hidden test process was terminated by its exact PID and the script
argument was quoted on the next run.

### FreeCAD executes `InitGui.py` with split global/local scopes

Class methods could not resolve module-local `_mod_dir`, and a command resource
method could not resolve `CADRIGWorkbench`. Both values were persisted on the
FreeCAD application module before class creation. The subsequent GUI smoke test
registered and activated the workbench successfully.

### Installed legacy data required recoverable handling

Permanent recursive deletion of the old user-profile workbench was rejected
because it contained sessions and uploads. The directory was moved outside
FreeCAD's active `Mod` scan tree into a dated disabled backup. The current
`Mod/CADRIG` workbench is the only active implementation.

## Verification evidence

- `pytest`: 129 passed.
- `ruff check src/cadrig tests freecad_workbench/CADRIG`: passed.
- CLI adapter discovery: passed.
- Typed-plan dry-run against the memory adapter: passed.
- Native FreeCAD sketch/extrusion transaction: passed.
- Native FreeCAD rollback: passed.
- Hidden FreeCAD GUI registration and evidence-view smoke test: passed.

The GUI smoke result verified `CADRIGWorkbench`, the `CADRIG Native Agent` dock,
and the Compile + Preview, Verify + Apply, Contract, Action Graph, Verification
and Receipt surfaces.
