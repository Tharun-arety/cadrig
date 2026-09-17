"""Exercise approved macro execution against a real FreeCAD runtime."""

import tempfile
from pathlib import Path

import FreeCAD as App

from cadcopilot.macros import FreeCADMacroExecutor, MacroArtifact

document = App.newDocument("MacroRunnerSmoke")
with tempfile.TemporaryDirectory(prefix="cadcopilot-runner-") as backup_directory:
    executor = FreeCADMacroExecutor(App, backup_root=Path(backup_directory))
    success = executor.execute(
        MacroArtifact(
            name="Native Box",
            description="Create a native box",
            code=(
                "import FreeCAD as App\n"
                "doc = App.ActiveDocument\n"
                "box = doc.addObject('Part::Box', 'GeneratedBox')\n"
                "box.Length = 10\nbox.Width = 20\nbox.Height = 5\n"
            ),
        ),
        approved=True,
    )
    if not success.accepted or document.getObject("GeneratedBox") is None:
        raise RuntimeError(f"approved macro failed: {success.to_dict()}")
    if not Path(success.backup_path).exists():
        raise RuntimeError("pre-execution FCStd backup was not created")

    document.undo()
    document.recompute()
    failure = executor.execute(
        MacroArtifact(
            name="Expected Failure",
            description="Prove transaction rollback",
            code=(
                "import FreeCAD as App\n"
                "doc = App.ActiveDocument\n"
                "doc.addObject('Part::Cylinder', 'MustRollBack')\n"
                "raise RuntimeError('expected smoke failure')\n"
            ),
        ),
        approved=True,
    )
    if failure.accepted or "expected smoke failure" not in (failure.error or ""):
        raise RuntimeError("failure was not captured")
    if document.getObject("MustRollBack") is not None:
        raise RuntimeError("failed macro left partial FreeCAD geometry")
    print(success.to_dict())
    print(failure.to_dict())

App.closeDocument(document.Name)
