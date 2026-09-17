import json

import pytest

from cadcopilot.macros import (
    FreeCADMacroExecutor,
    FreeCADMacroGenerator,
    MacroArtifact,
    MacroExecutionError,
    MacroGenerationError,
    MacroPolicy,
)

SAFE_MACRO = """import FreeCAD as App

doc = App.ActiveDocument or App.newDocument("GeneratedPart")
box = doc.addObject("Part::Box", "Box")
box.Length = 10
box.Width = 20
box.Height = 5
if doc.recompute() is False:
    raise RuntimeError("FreeCAD recompute failed")
"""


class FakeModel:
    def __init__(self, code=SAFE_MACRO):
        self.code = code
        self.schema = None

    def complete(self, _messages, *, response_schema=None):
        self.schema = response_schema
        return json.dumps(
            {
                "name": "Create Box",
                "description": "Creates a native parametric box.",
                "code": self.code,
                "assumptions": ["Dimensions are millimetres"],
            }
        )


def test_generator_returns_reviewed_macro_without_executing_it():
    model = FakeModel()
    artifact = FreeCADMacroGenerator(model).generate("Create a 10 by 20 by 5 box")

    assert artifact.name == "Create Box"
    assert artifact.diagnostics == ()
    assert artifact.metadata["auto_executed"] is False
    assert model.schema["properties"]["code"]["type"] == "string"


def test_safe_policy_rejects_shell_and_file_access():
    code = "import os\nos.system('dangerous')\n"
    with pytest.raises(MacroGenerationError, match="UNSAFE_IMPORT"):
        FreeCADMacroGenerator(FakeModel(code)).generate("Run something")


def test_review_policy_preserves_risky_macro_with_diagnostics():
    code = "import os\nos.system('review me')\n"
    artifact = FreeCADMacroGenerator(FakeModel(code)).generate(
        "Generate an advanced macro", policy_mode="review"
    )
    assert {item.code for item in artifact.diagnostics} == {"UNSAFE_IMPORT", "DANGEROUS_CALL"}
    assert artifact.metadata["auto_executed"] is False


def test_syntax_errors_are_always_rejected():
    with pytest.raises(MacroGenerationError, match="SYNTAX_ERROR"):
        FreeCADMacroGenerator(FakeModel("if broken\n    pass\n")).generate(
            "Broken macro", policy_mode="review"
        )


def test_macro_artifact_writes_only_fcmacro_and_protects_existing_file(tmp_path):
    artifact = FreeCADMacroGenerator(FakeModel()).generate("Create a box")
    output = tmp_path / "CreateBox.FCMacro"
    assert artifact.write(output) == output.resolve()
    assert output.read_text(encoding="utf-8").startswith("import FreeCAD")
    with pytest.raises(MacroGenerationError, match="already exists"):
        artifact.write(output)
    with pytest.raises(MacroGenerationError, match=".FCMacro"):
        artifact.write(tmp_path / "bad.py")


def test_policy_reports_exact_risky_line():
    diagnostics = MacroPolicy().review("import subprocess\nsubprocess.run(['x'])\n")
    assert diagnostics[0].line == 1
    assert diagnostics[1].line == 2


class FakeDocument:
    def __init__(self, name="Part"):
        self.Name = name
        self.Objects = []
        self.value = 1
        self._before = None

    def saveCopy(self, path):
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("backup")
        return True

    def openTransaction(self, _name):
        self._before = self.value

    def commitTransaction(self):
        self._before = None

    def abortTransaction(self):
        self.value = self._before
        self._before = None

    def recompute(self):
        return True


class FakeApp:
    def __init__(self):
        self.ActiveDocument = FakeDocument()
        self.documents = {"Part": self.ActiveDocument}

    def listDocuments(self):
        return self.documents

    def closeDocument(self, name):
        del self.documents[name]


def execution_artifact(code):
    return MacroArtifact(name="Test Macro", description="Test", code=code)


def test_macro_executor_requires_explicit_approval(tmp_path):
    with pytest.raises(MacroExecutionError, match="approval"):
        FreeCADMacroExecutor(FakeApp(), backup_root=tmp_path).execute(
            execution_artifact("print('no')")
        )


def test_macro_executor_backs_up_captures_output_and_commits(tmp_path):
    app = FakeApp()
    receipt = FreeCADMacroExecutor(app, backup_root=tmp_path).execute(
        execution_artifact("print('hello')\nApp.ActiveDocument.value = 7\n"), approved=True
    )
    assert receipt.accepted
    assert receipt.stdout == "hello\n"
    assert app.ActiveDocument.value == 7
    assert receipt.backup_path.endswith(".FCStd")


def test_macro_executor_rolls_back_and_reports_failure(tmp_path):
    app = FakeApp()
    receipt = FreeCADMacroExecutor(app, backup_root=tmp_path).execute(
        execution_artifact(
            "App.ActiveDocument.value = 9\nraise RuntimeError('generated failure')\n"
        ),
        approved=True,
    )
    assert receipt.status == "failed"
    assert receipt.error == "generated failure"
    assert "RuntimeError" in receipt.traceback
    assert app.ActiveDocument.value == 1
