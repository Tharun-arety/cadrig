"""Launch FreeCAD GUI, verify the installed CADRIG dock, then exit."""

import json
import os
import traceback
from pathlib import Path

import FreeCADGui as Gui
from PySide6 import QtCore, QtWidgets

RESULT = Path(
    os.environ.get(
        "CADCOPILOT_UI_SMOKE_RESULT",
        Path(__file__).resolve().parent.parent / "freecad-ui-smoke-result.json",
    )
)


def verify_panel():
    try:
        workbenches = Gui.listWorkbenches()
        if "CADCopilotWorkbench" not in workbenches:
            raise RuntimeError(f"CADRIG workbench was not registered: {workbenches}")
        Gui.activateWorkbench("CADCopilotWorkbench")
        panel = getattr(Gui, "_cadcopilot_agent_panel", None)
        dock = panel
        if dock is None or not dock.isVisible():
            raise RuntimeError("CADRIG dock was not visible")
        if not all(hasattr(panel, name) for name in ("chat_display", "text_input", "btn_send")):
            raise RuntimeError("CADRIG chat controls were not constructed")
        result = {
            "status": "passed",
            "workbench": "CADCopilotWorkbench",
            "dock_title": dock.windowTitle(),
            "send_button": panel.btn_send.text(),
            "has_chat": True,
            "has_undo": hasattr(panel, "btn_undo"),
            "has_settings": hasattr(panel, "btn_settings"),
        }
    except Exception:  # noqa: BLE001 - persist GUI startup diagnostics for the smoke test.
        result = {"status": "failed", "traceback": traceback.format_exc()}
    RESULT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    QtCore.QTimer.singleShot(0, QtWidgets.QApplication.instance().quit)


QtCore.QTimer.singleShot(750, verify_panel)
