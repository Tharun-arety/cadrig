"""Launch FreeCAD GUI, verify the installed CADRIG dock, then exit."""

import json
import os
import tempfile
import traceback
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide6 import QtCore, QtWidgets

from cadrig.adapters.freecad import FreeCADKernelAdapter

RESULT = Path(
    os.environ.get(
        "CADRIG_UI_SMOKE_RESULT",
        Path(__file__).resolve().parent.parent / "freecad-ui-smoke-result.json",
    )
)


def verify_panel():
    try:
        workbenches = Gui.listWorkbenches()
        if "CADRIGWorkbench" not in workbenches:
            raise RuntimeError(f"CADRIG workbench was not registered: {workbenches}")
        Gui.activateWorkbench("CADRIGWorkbench")
        dock = Gui.getMainWindow().findChild(QtWidgets.QDockWidget, "CADRIGNativeAgentDock")
        panel = dock.widget() if dock is not None else None
        if dock is None or not dock.isVisible():
            raise RuntimeError("CADRIG dock was not visible")
        required = ("contract_view", "plan_view", "verification_view", "trace_view")
        if panel is None or not all(hasattr(panel, name) for name in required):
            raise RuntimeError("CADRIG native-agent evidence views were not constructed")
        document = App.newDocument("CADRIGArtifactSmoke")
        body = document.addObject("Part::Feature", "Body")
        body.Shape = Part.makeBox(10, 20, 5)
        document.recompute()
        with tempfile.TemporaryDirectory(prefix="cadrig-gui-capture-") as temporary:
            capture = FreeCADKernelAdapter(app=App, gui=Gui).capture_artifacts(
                document.Name, Path(temporary)
            )
            captured_names = {artifact.logical_name for artifact in capture.artifacts}
            expected_artifacts = {
                "output.FCStd",
                "output.step",
                "renders/isometric.png",
                "renders/front.png",
                "renders/top.png",
                "renders/right.png",
            }
            if not expected_artifacts.issubset(captured_names):
                raise RuntimeError(f"artifact capture was incomplete: {capture.to_dict()}")
            if capture.diagnostics:
                raise RuntimeError(f"artifact capture reported diagnostics: {capture.to_dict()}")
        App.closeDocument(document.Name)
        result = {
            "status": "passed",
            "workbench": "CADRIGWorkbench",
            "dock_title": dock.windowTitle(),
            "preview_button": panel.preview_button.text(),
            "apply_button": panel.apply_button.text(),
            "has_contract": True,
            "has_action_graph": True,
            "has_verification": True,
            "has_receipt": True,
            "artifact_capture": sorted(captured_names),
        }
    except Exception:  # noqa: BLE001 - persist GUI startup diagnostics for the smoke test.
        result = {"status": "failed", "traceback": traceback.format_exc()}
    RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    QtCore.QTimer.singleShot(0, QtWidgets.QApplication.instance().quit)


QtCore.QTimer.singleShot(750, verify_panel)
