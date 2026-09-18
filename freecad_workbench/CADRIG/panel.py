"""Thin FreeCAD client for the native CADRIG contract-driven agent."""

from __future__ import annotations

import json
import os
import traceback

import FreeCAD as App
import FreeCADGui as Gui

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets  # type: ignore[no-redef]

from cadrig.action_graph import ActionGraphPlanner
from cadrig.adapters.freecad import FreeCADKernelAdapter
from cadrig.contract_compiler import ContractCompiler
from cadrig.episodes import EpisodeContext, EpisodeStore
from cadrig.executor import ExecutionEngine
from cadrig.models import OpenAICompatibleClient
from cadrig.native_agent import NativeCADAgent
from cadrig.registry import AdapterRegistry

DOCK_OBJECT_NAME = "CADRIGNativeAgentDock"
PREFERENCES = App.ParamGet("User parameter:BaseApp/Preferences/Mod/CADRIG")
_PANEL = None


class CADRIGPanel(QtWidgets.QWidget):
    """Expose contracts, plans, verification and traces instead of raw model chat."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._load_preferences()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        title = QtWidgets.QLabel("CADRIG  /  NATIVE AGENT")
        title_font = title.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        title.setFont(title_font)
        layout.addWidget(title)

        subtitle = QtWidgets.QLabel(
            "Intent becomes a testable contract. Only independently verified geometry is accepted."
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        provider_group = QtWidgets.QGroupBox("Reasoning model")
        provider_form = QtWidgets.QFormLayout(provider_group)
        self.base_url = QtWidgets.QLineEdit()
        self.base_url.setPlaceholderText("http://localhost:1234/v1")
        self.model_name = QtWidgets.QLineEdit()
        self.model_name.setPlaceholderText("provider/model")
        self.api_key = QtWidgets.QLineEdit()
        self.api_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("kept in memory only")
        self.api_key_env = QtWidgets.QLineEdit("CADRIG_MODEL_API_KEY")
        self.response_format = QtWidgets.QComboBox()
        self.response_format.addItems(["json_schema", "json_object", "none"])
        self.max_repairs = QtWidgets.QSpinBox()
        self.max_repairs.setRange(0, 3)
        self.max_repairs.setValue(1)
        provider_form.addRow("Endpoint", self.base_url)
        provider_form.addRow("Model", self.model_name)
        provider_form.addRow("API key", self.api_key)
        provider_form.addRow("Or key env", self.api_key_env)
        provider_form.addRow("Structured output", self.response_format)
        provider_form.addRow("Repair attempts", self.max_repairs)
        layout.addWidget(provider_group)

        self.intent = QtWidgets.QPlainTextEdit()
        self.intent.setPlaceholderText(
            "Describe the required geometry and explicitly state what must remain unchanged."
        )
        self.intent.setMinimumHeight(90)
        layout.addWidget(self.intent)

        button_row = QtWidgets.QHBoxLayout()
        self.preview_button = QtWidgets.QPushButton("Compile + Preview")
        self.apply_button = QtWidgets.QPushButton("Verify + Apply")
        self.preview_button.clicked.connect(lambda: self._run_agent(apply=False))
        self.apply_button.clicked.connect(lambda: self._run_agent(apply=True))
        button_row.addWidget(self.preview_button)
        button_row.addWidget(self.apply_button)
        layout.addLayout(button_row)

        self.tabs = QtWidgets.QTabWidget()
        self.contract_view = self._json_view()
        self.plan_view = self._json_view()
        self.verification_view = self._json_view()
        self.trace_view = self._json_view()
        self.tabs.addTab(self.contract_view, "Contract")
        self.tabs.addTab(self.plan_view, "Action graph")
        self.tabs.addTab(self.verification_view, "Verification")
        self.tabs.addTab(self.trace_view, "Receipt")
        layout.addWidget(self.tabs, 1)

        self.status = QtWidgets.QLabel("Ready — the model has no direct kernel access.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    @staticmethod
    def _json_view():
        view = QtWidgets.QPlainTextEdit()
        view.setReadOnly(True)
        return view

    def _load_preferences(self):
        self.base_url.setText(PREFERENCES.GetString("BaseUrl", ""))
        self.model_name.setText(PREFERENCES.GetString("Model", ""))
        self.api_key_env.setText(
            PREFERENCES.GetString("ApiKeyEnvironment", "CADRIG_MODEL_API_KEY")
        )
        value = PREFERENCES.GetString("ResponseFormat", "json_schema")
        index = self.response_format.findText(value)
        if index >= 0:
            self.response_format.setCurrentIndex(index)
        self.max_repairs.setValue(PREFERENCES.GetInt("MaxRepairs", 1))

    def _save_preferences(self):
        PREFERENCES.SetString("BaseUrl", self.base_url.text().strip())
        PREFERENCES.SetString("Model", self.model_name.text().strip())
        PREFERENCES.SetString("ApiKeyEnvironment", self.api_key_env.text().strip())
        PREFERENCES.SetString("ResponseFormat", self.response_format.currentText())
        PREFERENCES.SetInt("MaxRepairs", self.max_repairs.value())

    def _run_agent(self, *, apply):
        intent = self.intent.toPlainText().strip()
        base_url = self.base_url.text().strip()
        model_name = self.model_name.text().strip()
        if not intent or not base_url or not model_name:
            self._message("Endpoint, model and engineering intent are required.")
            return
        document_id = App.ActiveDocument.Name if App.ActiveDocument else "CADRIGPart"
        api_key = self.api_key.text() or os.environ.get(self.api_key_env.text().strip(), "")
        self._save_preferences()
        self._set_busy(True)
        self.status.setText("Compiling design contract…")
        QtWidgets.QApplication.processEvents()
        native_agent = None
        try:
            model = OpenAICompatibleClient(
                base_url=base_url,
                model=model_name,
                api_key=api_key or None,
                timeout_seconds=120,
                response_format=self.response_format.currentText(),
            )
            registry = AdapterRegistry()
            registry.register(FreeCADKernelAdapter(app=App, gui=Gui))
            native_agent = NativeCADAgent(
                executor=ExecutionEngine(registry),
                compiler=ContractCompiler(model),
                planner=ActionGraphPlanner(model),
                max_repairs=self.max_repairs.value(),
                episode_store=EpisodeStore(),
                episode_context=EpisodeContext(
                    task_source="freecad_interactive",
                    model={
                        "provider": "openai_compatible",
                        "model": model_name,
                        "response_format": self.response_format.currentText(),
                    },
                    environment={"freecad": str(App.Version())},
                ),
            )
            result = native_agent.run(
                intent=intent,
                adapter_id="freecad",
                document_id=document_id,
                apply=apply,
            )
        except Exception:  # noqa: BLE001 - surface the native boundary failure.
            self.trace_view.setPlainText(traceback.format_exc())
            self.tabs.setCurrentWidget(self.trace_view)
            episode_status = (
                f" Evidence: {native_agent.last_episode.path}"
                if native_agent is not None and native_agent.last_episode
                else f" Recording warning: {native_agent.last_episode_error}"
                if native_agent is not None and native_agent.last_episode_error
                else ""
            )
            self.status.setText(
                f"CADRIG refused the run before geometry was accepted.{episode_status}"
            )
            self._set_busy(False)
            return

        self.contract_view.setPlainText(json.dumps(result.contract.to_dict(), indent=2))
        self.plan_view.setPlainText(
            json.dumps(result.plan.to_dict() if result.plan else None, indent=2)
        )
        self.verification_view.setPlainText(
            json.dumps(result.verification.to_dict() if result.verification else None, indent=2)
        )
        self.trace_view.setPlainText(json.dumps(result.to_dict(), indent=2))
        self.tabs.setCurrentWidget(
            self.verification_view if result.verification else self.trace_view
        )
        episode_status = (
            f" Episode: {result.episode.path}"
            if result.episode
            else f" Episode recording warning: {result.episode_error}"
            if result.episode_error
            else ""
        )
        self.status.setText(
            f"{result.status.value.upper()} — {result.attempts} planning attempt(s); "
            f"acceptance decided by the contract verifier.{episode_status}"
        )
        if result.accepted and apply:
            try:
                Gui.activeDocument().activeView().fitAll()
            except Exception as exc:  # noqa: BLE001 - view refresh is non-critical.
                App.Console.PrintWarning(f"CADRIG view refresh warning: {exc}\n")
        self._set_busy(False)

    def _set_busy(self, busy):
        self.preview_button.setEnabled(not busy)
        self.apply_button.setEnabled(not busy)

    def _message(self, message):
        QtWidgets.QMessageBox.information(self, "CADRIG", message)


def show_panel():
    global _PANEL
    main_window = Gui.getMainWindow()
    dock = main_window.findChild(QtWidgets.QDockWidget, DOCK_OBJECT_NAME)
    if dock is None:
        dock = QtWidgets.QDockWidget("CADRIG Native Agent", main_window)
        dock.setObjectName(DOCK_OBJECT_NAME)
        dock.setAllowedAreas(
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            | QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        )
        dock.setMinimumWidth(420)
        _PANEL = CADRIGPanel(dock)
        dock.setWidget(_PANEL)
        main_window.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
    elif _PANEL is None:
        _PANEL = dock.widget()
    dock.show()
    dock.raise_()
    return _PANEL
