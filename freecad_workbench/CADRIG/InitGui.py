"""Register the native CADRIG workbench in FreeCAD."""

import os

import FreeCAD
import FreeCADGui

_mod_dir = os.path.join(FreeCAD.getUserAppDataDir(), "Mod", "CADRIG")
if not os.path.isdir(_mod_dir):
    _mod_dir = os.path.normpath(os.path.join(FreeCAD.getHomePath(), "Mod", "CADRIG"))
FreeCAD._cadrig_dir = _mod_dir
FreeCAD._cadrig_icon = os.path.join(
    FreeCAD._cadrig_dir, "resources", "icons", "CADRIGWorkbench.svg"
)


def _show_agent_panel():
    from panel import show_panel

    return show_panel()


FreeCAD._cadrig_show_agent_panel = _show_agent_panel


class CADRIGWorkbench(Workbench):  # noqa: F821 - provided by FreeCAD's loader.
    MenuText = "CADRIG"
    ToolTip = "Contract-driven, verifier-governed CAD agent"
    Icon = FreeCAD._cadrig_icon

    def Initialize(self):
        icon_dir = os.path.join(FreeCAD._cadrig_dir, "resources", "icons")
        if os.path.isdir(icon_dir):
            FreeCADGui.addIconPath(icon_dir)
        self.appendToolbar("CADRIG", ["CADRIG_ShowPanel"])
        self.appendMenu("CADRIG", ["CADRIG_ShowPanel"])

    def Activated(self):
        FreeCAD._cadrig_show_agent_panel()

    def Deactivated(self):
        pass


class _ShowPanelCommand:
    def GetResources(self):
        return {
            "MenuText": "Open CADRIG",
            "ToolTip": "Open the native contract-driven CADRIG agent",
            "Pixmap": FreeCAD._cadrig_icon,
        }

    def Activated(self):
        FreeCAD._cadrig_show_agent_panel()

    def IsActive(self):
        return True


FreeCADGui.addCommand("CADRIG_ShowPanel", _ShowPanelCommand())
FreeCADGui.addWorkbench(CADRIGWorkbench())
FreeCAD.Console.PrintMessage("Native CADRIG agent workbench loaded.\n")
