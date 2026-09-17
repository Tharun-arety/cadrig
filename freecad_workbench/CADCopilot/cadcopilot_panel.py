"""Docked FreeCAD UI for macro generation, review, execution and repair."""

from __future__ import annotations

import difflib
import json
import os
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any

import FreeCAD as App
import FreeCADGui as Gui

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:  # FreeCAD 0.21 and older bundles.
    from PySide2 import QtCore, QtGui, QtWidgets  # type: ignore[no-redef]

from cadcopilot.adapters.freecad import FreeCADKernelAdapter
from cadcopilot.macros import (
    FreeCADMacroExecutor,
    FreeCADMacroGenerator,
    MacroArtifact,
    MacroExecutionError,
    MacroPolicy,
)
from cadcopilot.models import OpenAICompatibleClient

DOCK_OBJECT_NAME = "CADCopilotDockWidget"
PREFERENCES = App.ParamGet("User parameter:BaseApp/Preferences/Mod/CADCopilot")
_PANEL: CADCopilotPanel | None = None


class _GenerationWorker(QtCore.QObject):
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, request: dict[str, Any]) -> None:
        super().__init__()
        self._request = request

    @QtCore.Slot()
    def run(self) -> None:
        try:
            model = OpenAICompatibleClient(
                base_url=self._request["base_url"],
                model=self._request["model"],
                api_key=self._request["api_key"],
                timeout_seconds=self._request["timeout"],
                response_format=self._request["response_format"],
            )
            generator = FreeCADMacroGenerator(model)
            if self._request["repair"]:
                artifact = generator.repair(
                    intent=self._request["intent"],
                    previous=self._request["previous"],
                    failure=self._request["failure"],
                    freecad_version=self._request["freecad_version"],
                    policy_mode=self._request["policy_mode"],
                    document_context=self._request["document_context"],
                )
            else:
                artifact = generator.generate(
                    self._request["intent"],
                    freecad_version=self._request["freecad_version"],
                    policy_mode=self._request["policy_mode"],
                    document_context=self._request["document_context"],
                )
            self.finished.emit(artifact)
        except Exception:  # noqa: BLE001 - marshal worker failures to the GUI.
            self.failed.emit(traceback.format_exc())


class CADCopilotPanel(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._artifact: MacroArtifact | None = None
        self._diff_base = ""
        self._last_failure = ""
        self._thread: QtCore.QThread | None = None
        self._worker: _GenerationWorker | None = None
        self._build_ui()
        self._load_preferences()
        self._set_busy(False)

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QtWidgets.QLabel("CADRIG - FreeCAD Agent")
        font = title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        title.setFont(font)
        layout.addWidget(title)

        settings_group = QtWidgets.QGroupBox("Bring your own model")
        settings = QtWidgets.QFormLayout(settings_group)
        self.base_url = QtWidgets.QLineEdit()
        self.base_url.setPlaceholderText("http://localhost:1234/v1")
        self.model_name = QtWidgets.QLineEdit()
        self.model_name.setPlaceholderText("model identifier")
        self.api_key = QtWidgets.QLineEdit()
        self.api_key.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("session only; optional for local models")
        self.key_environment = QtWidgets.QLineEdit("CADCOPILOT_MODEL_API_KEY")
        self.response_format = QtWidgets.QComboBox()
        self.response_format.addItems(["json_schema", "json_object", "none"])
        self.policy_mode = QtWidgets.QComboBox()
        self.policy_mode.addItems(["safe", "review"])
        settings.addRow("Base URL", self.base_url)
        settings.addRow("Model", self.model_name)
        settings.addRow("API key", self.api_key)
        settings.addRow("Or key env", self.key_environment)
        settings.addRow("Response", self.response_format)
        settings.addRow("Policy", self.policy_mode)
        layout.addWidget(settings_group)

        self.intent = QtWidgets.QPlainTextEdit()
        self.intent.setPlaceholderText(
            "Describe the FreeCAD automation you want, including inputs and expected result…"
        )
        self.intent.setMinimumHeight(95)
        layout.addWidget(self.intent)

        generation_row = QtWidgets.QHBoxLayout()
        self.generate_button = QtWidgets.QPushButton("Generate macro")
        self.generate_button.clicked.connect(lambda: self._start_generation(repair=False))
        self.repair_button = QtWidgets.QPushButton("Repair from error")
        self.repair_button.clicked.connect(lambda: self._start_generation(repair=True))
        self.repair_button.setEnabled(False)
        generation_row.addWidget(self.generate_button)
        generation_row.addWidget(self.repair_button)
        layout.addLayout(generation_row)

        self.tabs = QtWidgets.QTabWidget()
        self.code_editor = QtWidgets.QPlainTextEdit()
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        self.code_editor.setFont(fixed_font)
        self.code_editor.textChanged.connect(self._refresh_review)
        self.diff_view = QtWidgets.QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setFont(fixed_font)
        self.diagnostics_view = QtWidgets.QPlainTextEdit()
        self.diagnostics_view.setReadOnly(True)
        self.output_view = QtWidgets.QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setFont(fixed_font)
        self.tabs.addTab(self.code_editor, "Macro")
        self.tabs.addTab(self.diff_view, "Diff")
        self.tabs.addTab(self.diagnostics_view, "Review")
        self.tabs.addTab(self.output_view, "Run output")
        layout.addWidget(self.tabs, 1)

        action_row = QtWidgets.QHBoxLayout()
        self.save_button = QtWidgets.QPushButton("Save .FCMacro")
        self.save_button.clicked.connect(self._save_macro)
        self.run_button = QtWidgets.QPushButton("Approve && Run")
        self.run_button.clicked.connect(self._approve_and_run)
        action_row.addWidget(self.save_button)
        action_row.addWidget(self.run_button)
        layout.addLayout(action_row)

        self.status = QtWidgets.QLabel("Ready")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _load_preferences(self) -> None:
        self.base_url.setText(PREFERENCES.GetString("BaseUrl", ""))
        self.model_name.setText(PREFERENCES.GetString("Model", ""))
        self.key_environment.setText(
            PREFERENCES.GetString("ApiKeyEnvironment", "CADCOPILOT_MODEL_API_KEY")
        )
        self._select(self.response_format, PREFERENCES.GetString("ResponseFormat", "json_schema"))
        self._select(self.policy_mode, PREFERENCES.GetString("PolicyMode", "safe"))

    def _save_preferences(self) -> None:
        PREFERENCES.SetString("BaseUrl", self.base_url.text().strip())
        PREFERENCES.SetString("Model", self.model_name.text().strip())
        PREFERENCES.SetString("ApiKeyEnvironment", self.key_environment.text().strip())
        PREFERENCES.SetString("ResponseFormat", self.response_format.currentText())
        PREFERENCES.SetString("PolicyMode", self.policy_mode.currentText())

    @staticmethod
    def _select(combo: QtWidgets.QComboBox, value: str) -> None:
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _document_context(self) -> dict[str, Any] | None:
        document = App.ActiveDocument
        if document is None:
            return None
        try:
            snapshot = FreeCADKernelAdapter(app=App, gui=Gui).observe(document.Name)
            return snapshot.to_dict() if snapshot else None
        except Exception as exc:  # noqa: BLE001 - context is helpful but optional.
            return {"document_id": document.Name, "context_error": str(exc)}

    def _start_generation(self, *, repair: bool) -> None:
        if self._thread is not None:
            return
        base_url = self.base_url.text().strip()
        model_name = self.model_name.text().strip()
        intent = self.intent.toPlainText().strip()
        if not base_url or not model_name or not intent:
            self._message("Base URL, model and automation request are required.")
            return
        if repair and (self._artifact is None or not self._last_failure):
            self._message("Run a macro and capture a failure before requesting repair.")
            return
        self._save_preferences()
        key = self.api_key.text()
        if not key:
            key = os.environ.get(self.key_environment.text().strip() or "", "")
        previous = self._artifact_from_editor() if self._artifact else None
        request = {
            "base_url": base_url,
            "model": model_name,
            "api_key": key or None,
            "timeout": 120,
            "response_format": self.response_format.currentText(),
            "policy_mode": self.policy_mode.currentText(),
            "freecad_version": ".".join(str(item) for item in App.Version()[:2]),
            "intent": intent,
            "document_context": self._document_context(),
            "repair": repair,
            "previous": previous,
            "failure": self._last_failure,
        }
        self._set_busy(True)
        self.status.setText("Repairing macro…" if repair else "Generating macro…")
        self._thread = QtCore.QThread(self)
        self._worker = _GenerationWorker(request)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._generation_finished)
        self._worker.failed.connect(self._generation_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._generation_thread_finished)
        self._thread.start()

    @QtCore.Slot(object)
    def _generation_finished(self, artifact: MacroArtifact) -> None:
        old_code = self.code_editor.toPlainText()
        self._diff_base = old_code or artifact.code
        self._artifact = artifact
        self.code_editor.setPlainText(artifact.code)
        self._last_failure = ""
        self.repair_button.setEnabled(False)
        self.tabs.setCurrentWidget(self.code_editor)
        self.status.setText(f"Generated: {artifact.name}. Review the source before running.")
        self._refresh_review()

    @QtCore.Slot(str)
    def _generation_failed(self, failure: str) -> None:
        self.output_view.setPlainText(failure)
        self.tabs.setCurrentWidget(self.output_view)
        self.status.setText("Macro generation failed. See Run output for details.")

    @QtCore.Slot()
    def _generation_thread_finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None
        self._set_busy(False)

    def _refresh_review(self) -> None:
        code = self.code_editor.toPlainText()
        diff = difflib.unified_diff(
            self._diff_base.splitlines(),
            code.splitlines(),
            fromfile="previous.FCMacro",
            tofile="current.FCMacro",
            lineterm="",
        )
        self.diff_view.setPlainText("\n".join(diff))
        diagnostics = MacroPolicy().review(code) if code.strip() else ()
        if diagnostics:
            text = "\n".join(
                f"{item.code} - line {item.line or '?'} - {item.message}" for item in diagnostics
            )
        else:
            text = "No syntax or static policy issues detected."
        self.diagnostics_view.setPlainText(text)
        available = self._artifact is not None and bool(code.strip()) and self._thread is None
        self.save_button.setEnabled(available)
        self.run_button.setEnabled(available)

    def _artifact_from_editor(self) -> MacroArtifact:
        if self._artifact is None:
            raise MacroExecutionError("generate a macro first")
        code = self.code_editor.toPlainText()
        diagnostics = MacroPolicy().review(code)
        return replace(self._artifact, code=code, diagnostics=diagnostics)

    def _approve_and_run(self) -> None:
        try:
            artifact = self._artifact_from_editor()
        except MacroExecutionError as exc:
            self._message(str(exc))
            return
        if artifact.diagnostics and self.policy_mode.currentText() == "safe":
            self.tabs.setCurrentWidget(self.diagnostics_view)
            self._message("Safe policy blocks this macro. Resolve the review findings first.")
            return
        warning = (
            "This macro contains review findings. It may access files or other system resources.\n\n"
            if artifact.diagnostics
            else ""
        )
        answer = QtWidgets.QMessageBox.question(
            self,
            "Approve FreeCAD macro",
            warning
            + "Run the currently displayed source against the active FreeCAD session?\n\n"
            "A recoverable FCStd backup will be created first.",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.status.setText("Running approved macro…")
        QtWidgets.QApplication.processEvents()
        try:
            receipt = FreeCADMacroExecutor(App).execute(artifact, approved=True)
        except Exception:  # noqa: BLE001 - display execution-boundary failures.
            self._last_failure = traceback.format_exc()
            self.output_view.setPlainText(self._last_failure)
            self.repair_button.setEnabled(True)
            self.status.setText("Macro runner failed before execution.")
            self.tabs.setCurrentWidget(self.output_view)
            return
        output = json.dumps(receipt.to_dict(), indent=2)
        self.output_view.setPlainText(output)
        self.tabs.setCurrentWidget(self.output_view)
        if receipt.accepted:
            self._last_failure = ""
            self.repair_button.setEnabled(False)
            self.status.setText(f"Macro applied. Backup: {receipt.backup_path or 'not needed'}")
            try:
                Gui.activeDocument().activeView().fitAll()
            except Exception as exc:  # noqa: BLE001 - view refresh is optional.
                self.status.setText(f"Macro applied; view refresh warning: {exc}")
        else:
            self._last_failure = "\n".join(
                item for item in (receipt.error, receipt.traceback, receipt.stderr) if item
            )
            self.repair_button.setEnabled(True)
            self.status.setText("Macro failed and was rolled back. Repair is available.")

    def _save_macro(self) -> None:
        try:
            artifact = self._artifact_from_editor()
        except MacroExecutionError as exc:
            self._message(str(exc))
            return
        default_dir = Path(str(App.getUserMacroDir(True)))
        default_path = default_dir / f"{self._safe_file_name(artifact.name)}.FCMacro"
        selected, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save FreeCAD macro", str(default_path), "FreeCAD Macro (*.FCMacro)"
        )
        if not selected:
            return
        destination = Path(selected)
        overwrite = False
        if destination.exists():
            answer = QtWidgets.QMessageBox.question(
                self,
                "Overwrite macro?",
                f"Replace {destination}?",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            overwrite = answer == QtWidgets.QMessageBox.StandardButton.Yes
            if not overwrite:
                return
        try:
            written = artifact.write(destination, overwrite=overwrite)
        except Exception as exc:  # noqa: BLE001 - show filesystem errors in the panel.
            self._message(str(exc))
            return
        self.status.setText(f"Saved {written}")

    @staticmethod
    def _safe_file_name(value: str) -> str:
        return "".join(char if char.isalnum() or char in "-_" else "_" for char in value)

    def _set_busy(self, busy: bool) -> None:
        self.generate_button.setEnabled(not busy)
        self.base_url.setEnabled(not busy)
        self.model_name.setEnabled(not busy)
        self.intent.setEnabled(not busy)
        self._refresh_review()

    def _message(self, message: str) -> None:
        QtWidgets.QMessageBox.information(self, "CADRIG", message)


def show_panel() -> CADCopilotPanel:
    global _PANEL
    main_window = Gui.getMainWindow()
    dock = main_window.findChild(QtWidgets.QDockWidget, DOCK_OBJECT_NAME)
    if dock is None:
        dock = QtWidgets.QDockWidget("CADRIG", main_window)
        dock.setObjectName(DOCK_OBJECT_NAME)
        dock.setAllowedAreas(
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            | QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        )
        dock.setMinimumWidth(380)
        _PANEL = CADCopilotPanel(dock)
        dock.setWidget(_PANEL)
        main_window.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
    elif _PANEL is None:
        _PANEL = dock.widget()
    dock.show()
    dock.raise_()
    return _PANEL
