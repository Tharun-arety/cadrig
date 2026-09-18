"""Open the installed CADRIG workbench in a visible FreeCAD window."""

import traceback

import FreeCADGui as Gui
from PySide6 import QtCore, QtWidgets


def open_workbench():
    try:
        if "CADRIGWorkbench" not in Gui.listWorkbenches():
            raise RuntimeError(
                "CADRIG is not registered in this FreeCAD profile. "
                "Run freecad_workbench/install.py with FreeCAD's bundled Python."
            )
        Gui.activateWorkbench("CADRIGWorkbench")
        window = Gui.getMainWindow()
        dock = window.findChild(QtWidgets.QDockWidget, "CADRIGNativeAgentDock")
        if dock is None:
            raise RuntimeError("CADRIG did not create its native-agent panel")
        window.show()
        window.raise_()
        window.activateWindow()
        dock.raise_()
    except Exception:  # noqa: BLE001 - show startup diagnostics inside FreeCAD.
        QtWidgets.QMessageBox.critical(
            Gui.getMainWindow(),
            "CADRIG startup failed",
            traceback.format_exc(),
        )


QtCore.QTimer.singleShot(750, open_workbench)
