"""Register the CADRIG workbench in FreeCAD."""

import os
import sys

import FreeCAD
import FreeCADGui

_mod_dir = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "CADCopilot")
if not os.path.isdir(_mod_dir):
    _mod_dir = os.path.normpath(os.path.join(FreeCAD.getHomePath(), "Mod", "CADCopilot"))
_runtime_dir = os.path.join(_mod_dir, "agent_runtime")
if _runtime_dir not in sys.path:
    sys.path.insert(0, _runtime_dir)
FreeCAD._cadcopilot_dir = _mod_dir
FreeCAD._cadcopilot_runtime_dir = _runtime_dir


def _show_agent_panel():
    import FreeCADGui
    from PySide6 import QtCore
    from ui.panel import AgentPanel

    panel = getattr(FreeCADGui, "_cadcopilot_agent_panel", None)
    if panel is None:
        panel = AgentPanel()
        FreeCADGui._cadcopilot_agent_panel = panel
        FreeCADGui.getMainWindow().addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
    panel.show()
    panel.raise_()


FreeCAD._cadcopilot_show_agent_panel = _show_agent_panel


class CADCopilotWorkbench(Workbench):  # noqa: F821 - injected by FreeCAD's InitGui loader.
    MenuText = "CADRIG"
    ToolTip = "Chat-first, bring-your-own-model CAD agent for FreeCAD"
    Icon = ""

    def __init__(self):
        import os

        icon_path = os.path.join(
            FreeCAD._cadcopilot_runtime_dir,
            "resources",
            "icons",
            "OpenCADCopilotWorkbench.svg",
        )
        if os.path.isfile(icon_path):
            self.__class__.Icon = icon_path

    def Initialize(self):
        import os

        import FreeCADGui

        icon_dir = os.path.join(FreeCAD._cadcopilot_runtime_dir, "resources", "icons")
        if os.path.isdir(icon_dir):
            FreeCADGui.addIconPath(icon_dir)
        self.appendToolbar(
            "CADRIG", ["CADCopilot_ShowPanel", "CADCopilot_Settings"]
        )
        self.appendMenu(
            "CADRIG", ["CADCopilot_ShowPanel", "CADCopilot_Settings"]
        )

    def Activated(self):
        FreeCAD._cadcopilot_show_agent_panel()

    def Deactivated(self):
        pass


class _ShowPanelCommand:
    def GetResources(self):
        return {
            "MenuText": "CADRIG",
            "ToolTip": "Open the chat-first CAD agent panel",
        }

    def Activated(self):
        FreeCAD._cadcopilot_show_agent_panel()

    def IsActive(self):
        return True


class _SettingsCommand:
    def GetResources(self):
        return {
            "MenuText": "CADRIG Settings",
            "ToolTip": "Configure model providers and agent parameters",
        }

    def Activated(self):
        import FreeCADGui
        from ui.settings_dialog import SettingsDialog

        SettingsDialog(parent=FreeCADGui.getMainWindow()).exec()

    def IsActive(self):
        return True


FreeCADGui.addCommand("CADCopilot_ShowPanel", _ShowPanelCommand())
FreeCADGui.addCommand("CADCopilot_Settings", _SettingsCommand())
FreeCADGui.addWorkbench(CADCopilotWorkbench())
FreeCAD.Console.PrintMessage("CADRIG agent workbench loaded.\n")


def _open_copilot_on_startup():
    """Make the Copilot dock discoverable whenever FreeCAD starts."""
    from PySide6 import QtCore, QtWidgets

    def show_copilot():
        try:
            FreeCADGui.activateWorkbench("CADCopilotWorkbench")
            FreeCAD._cadcopilot_show_agent_panel()
            if os.environ.get("CADCOPILOT_OPEN_GEMINI") != "1":
                return

            from ui.settings_dialog import PROVIDER_PRESETS, SettingsDialog

            dialog = SettingsDialog(parent=FreeCADGui.getMainWindow())
            provider_index = next(
                index
                for index, provider in enumerate(PROVIDER_PRESETS)
                if provider["name"] == "Google Gemini"
            )
            dialog.combo_provider.setCurrentIndex(provider_index)
            dialog.setModal(False)
            FreeCADGui._cadcopilot_connector_dialog = dialog
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
        except Exception as error:  # noqa: BLE001 - surface startup errors in the UI.
            QtWidgets.QMessageBox.critical(
                FreeCADGui.getMainWindow(),
                "CADRIG",
                f"Could not open the Gemini connector:\n{error}",
            )

    QtCore.QTimer.singleShot(1800, show_copilot)


_open_copilot_on_startup()
