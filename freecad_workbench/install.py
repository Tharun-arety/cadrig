"""Install the CADRIG workbench into the current FreeCAD user profile."""

from pathlib import Path
from shutil import copytree, ignore_patterns, rmtree

import FreeCAD as App

source = Path(__file__).resolve().parent / "CADRIG"
module_root = Path(str(App.getUserAppDataDir())) / "Mod"
destination = module_root / "CADRIG"
destination.mkdir(parents=True, exist_ok=True)

# Remove this project's old pre-native installation only when its registration
# file identifies it as CADRIG.  An unrelated user module with the same legacy
# folder name is left untouched.
legacy_destination = module_root / "CADCopilot"
legacy_init = legacy_destination / "InitGui.py"
if legacy_init.is_file():
    try:
        legacy_source = legacy_init.read_text(encoding="utf-8", errors="ignore")
        is_cadrig_legacy = (
            "CADRIG" in legacy_source
            or (
                "Open CAD Copilot" in legacy_source
                and (legacy_destination / "agent_runtime").is_dir()
            )
        )
    except OSError:
        is_cadrig_legacy = False
    if is_cadrig_legacy:
        rmtree(legacy_destination)
        print(f"Removed legacy CADRIG workbench from {legacy_destination}")

# Remove the pre-native alpha runtime before copying.  It is intentionally not
# part of CADRIG's current source tree and copytree would otherwise leave an old
# installed directory behind.
obsolete_runtime = destination / "agent_runtime"
if obsolete_runtime.is_dir():
    rmtree(obsolete_runtime)
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

general_preferences = App.ParamGet("User parameter:BaseApp/Preferences/General")
general_preferences.SetString("AutoloadModule", "CADRIGWorkbench")
general_preferences.SetString("LastModule", "CADRIGWorkbench")
print(f"Installed CADRIG workbench to {destination}")
print("Configured CADRIG as the startup workbench")
