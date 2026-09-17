"""Install the CADRIG workbench into the current FreeCAD user profile."""

from pathlib import Path
from shutil import copytree, ignore_patterns, rmtree

import FreeCAD as App

source = Path(__file__).resolve().parent / "CADCopilot"
destination = Path(str(App.getUserAppDataDir())) / "Mod" / "CADCopilot"
destination.mkdir(parents=True, exist_ok=True)
copytree(
    source,
    destination,
    dirs_exist_ok=True,
    ignore=ignore_patterns("__pycache__", "*.pyc", ".env", "sessions", "snapshots", "log"),
)

# ``copytree(..., dirs_exist_ok=True)`` updates source files but leaves bytecode
# from older installations behind.  FreeCAD is a long-running embedded Python
# process, and those stale caches can keep old agent-routing behavior alive even
# after the corresponding ``.py`` file has been replaced.  Always remove cached
# bytecode from the installed workbench so the next FreeCAD start compiles the
# files that were just installed.
for cache_dir in destination.rglob("__pycache__"):
    if cache_dir.is_dir():
        rmtree(cache_dir)
for bytecode in destination.rglob("*.pyc"):
    if bytecode.is_file():
        bytecode.unlink()

for obsolete in (
    destination / "agent_runtime" / "resources" / "icons" / "CadAgentWorkbench.svg",
    destination / "agent_runtime" / "README_EN.md",
):
    if obsolete.is_file():
        obsolete.unlink()
general_preferences = App.ParamGet("User parameter:BaseApp/Preferences/General")
general_preferences.SetString("AutoloadModule", "CADCopilotWorkbench")
general_preferences.SetString("LastModule", "CADCopilotWorkbench")
print(f"Installed CADRIG workbench to {destination}")
print("Configured CADRIG as the startup workbench")
