"""Open CADRIG with the Google Gemini connector selected."""

import traceback

import FreeCADGui as Gui
from PySide6 import QtCore, QtWidgets


def open_gemini_connector():
    try:
        Gui.activateWorkbench("CADCopilotWorkbench")
        from ui.settings_dialog import PROVIDER_PRESETS, SettingsDialog

        dialog = SettingsDialog(parent=Gui.getMainWindow())
        provider_index = next(
            index
            for index, provider in enumerate(PROVIDER_PRESETS)
            if provider["name"] == "Google Gemini"
        )
        dialog.combo_provider.setCurrentIndex(provider_index)
        dialog.setModal(False)
        Gui._cadcopilot_connector_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
    except Exception:  # noqa: BLE001 - show startup diagnostics inside FreeCAD.
        QtWidgets.QMessageBox.critical(
            Gui.getMainWindow(),
            "Gemini connector failed",
            traceback.format_exc(),
        )


QtCore.QTimer.singleShot(750, open_gemini_connector)
