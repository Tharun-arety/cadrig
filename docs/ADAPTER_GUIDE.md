# CAD-host adapter guide

An adapter may wrap an in-process Python API, an out-of-process command, a
desktop plugin or a remote CAD service. It must implement `KernelAdapter`.

An independently distributed adapter declares a factory entry point:

```toml
[project.entry-points."cadrig.adapters"]
freecad = "cadrig_freecad:make_adapter"
```

The factory takes no arguments and returns one adapter instance. The core loads
these packages with `AdapterRegistry.discover()`; adapter imports therefore
remain outside the core package.

## Required behavior

1. Report the exact action kinds implemented by this adapter version.
2. Return semantic snapshots without kernel objects or raw topology indices.
3. Reject plans created from a stale document revision.
4. Validate the complete plan before native mutation.
5. Apply the plan as one native undo transaction where the host permits it.
6. Rebuild/recompute and inspect host errors before reporting success.
7. Restore the previous state when any action or rebuild fails.
8. Keep dry-run free of persistent native mutations.
9. Return typed diagnostics for unavailable or unsupported behavior.

## Extension policy

Host-specific settings belong in namespaced adapter configuration, not in the
core action model. If several hosts need the same operation, propose a typed
core action and define its geometry-independent semantics before implementing it.
