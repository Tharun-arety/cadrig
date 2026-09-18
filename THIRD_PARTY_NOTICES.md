# Third-party notices

The current CADRIG source tree contains no vendored third-party CAD-agent runtime.

## Historical development note

Pre-native alpha commits temporarily included MIT-licensed runtime files from
[SSSSSia/CadAgent](https://github.com/SSSSSia/CadAgent) while CADRIG's execution
requirements were being explored. Those files, their ReAct/CQ runtime and their
UI were removed before the native CADRIG agent architecture was introduced.
Repository history retains the original attribution and license record; it has
not been rewritten or concealed.

The current agent is implemented under `src/cadrig` as a contract compiler,
typed action-graph planner, deterministic orchestrator, independent verifier,
bounded repair loop and replay-oriented trace system. The FreeCAD workbench is
a thin client of that native implementation.
