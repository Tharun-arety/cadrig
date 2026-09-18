"""Launch FreeCAD GUI, verify the installed CADRIG dock, then exit."""

import json
import os
import traceback
from pathlib import Path

import FreeCADGui as Gui
from PySide6 import QtCore, QtWidgets

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
        }
    except Exception:  # noqa: BLE001 - persist GUI startup diagnostics for the smoke test.
        result = {"status": "failed", "traceback": traceback.format_exc()}
    RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    QtCore.QTimer.singleShot(0, QtWidgets.QApplication.instance().quit)


QtCore.QTimer.singleShot(750, verify_panel)
