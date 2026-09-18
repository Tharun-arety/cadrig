"""FreeCAD macro generation, static review and artifact persistence."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import re
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from cadrig.models.base import ModelClient, ModelError, ModelMessage


class MacroGenerationError(ValueError):
    """Raised when model output is malformed or violates the selected macro policy."""


class MacroExecutionError(RuntimeError):
    """Raised when macro execution is requested without explicit approval."""


@dataclass(frozen=True)
class MacroDiagnostic:
    code: str
    message: str
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.line is not None:
            result["line"] = self.line
        return result


@dataclass(frozen=True)
class MacroArtifact:
    name: str
    description: str
    code: str
    assumptions: tuple[str, ...] = ()
    diagnostics: tuple[MacroDiagnostic, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 _-]{0,79}", self.name):
            raise MacroGenerationError(
                "macro name must start with a letter and contain only letters, digits, spaces, "
                "underscores or hyphens"
            )
        if not self.description.strip():
            raise MacroGenerationError("macro description must not be empty")
        if not self.code.strip():
            raise MacroGenerationError("macro code must not be empty")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self, *, include_code: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "assumptions": list(self.assumptions),
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "metadata": dict(self.metadata),
        }
        if include_code:
            result["code"] = self.code
        return result

    def write(self, output: Path, *, overwrite: bool = False) -> Path:
        destination = output.resolve()
        if destination.suffix.lower() != ".fcmacro":
            raise MacroGenerationError("macro output must use the .FCMacro extension")
        if destination.exists() and not overwrite:
            raise MacroGenerationError(f"macro already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(self.code.rstrip() + "\n", encoding="utf-8")
        return destination


class MacroPolicy:
    """Static safety review for model-generated FreeCAD Python."""

    _ALLOWED_IMPORTS = frozenset(
        {
            "Draft",
            "FreeCAD",
            "FreeCADGui",
            "Import",
            "Mesh",
            "Part",
            "PartDesign",
            "Path",
            "PySide",
            "PySide2",
            "PySide6",
            "Sketcher",
            "TechDraw",
            "math",
        }
    )
    _DANGEROUS_CALLS = frozenset(
        {
            "__import__",
            "compile",
            "eval",
            "exec",
            "open",
            "popen",
            "remove",
            "rmtree",
            "run",
            "spawn",
            "system",
            "unlink",
            "urlopen",
        }
    )
    _DANGEROUS_ATTRIBUTES = frozenset(
        {"__bases__", "__builtins__", "__class__", "__globals__", "__mro__", "__subclasses__"}
    )

    def review(self, code: str) -> tuple[MacroDiagnostic, ...]:
        try:
            tree = ast.parse(code, filename="generated.FCMacro", mode="exec")
        except SyntaxError as exc:
            return (
                MacroDiagnostic(
                    code="SYNTAX_ERROR",
                    message=exc.msg,
                    line=exc.lineno,
                ),
            )

        diagnostics: list[MacroDiagnostic] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._check_import(alias.name, node.lineno, diagnostics)
            elif isinstance(node, ast.ImportFrom):
                self._check_import(node.module or "", node.lineno, diagnostics)
            elif isinstance(node, ast.Call):
                name = self._call_name(node.func)
                if name in self._DANGEROUS_CALLS:
                    diagnostics.append(
                        MacroDiagnostic(
                            code="DANGEROUS_CALL",
                            message=f"generated macro calls {name}()",
                            line=node.lineno,
                        )
                    )
            elif isinstance(node, ast.Attribute) and node.attr in self._DANGEROUS_ATTRIBUTES:
                diagnostics.append(
                    MacroDiagnostic(
                        code="DANGEROUS_INTROSPECTION",
                        message=f"generated macro accesses {node.attr}",
                        line=node.lineno,
                    )
                )
        return tuple(diagnostics)

    def _check_import(
        self, module: str, line: int, diagnostics: list[MacroDiagnostic]
    ) -> None:
        root = module.split(".", 1)[0]
        if root not in self._ALLOWED_IMPORTS:
            diagnostics.append(
                MacroDiagnostic(
                    code="UNSAFE_IMPORT",
                    message=f"generated macro imports non-CAD module {module}",
                    line=line,
                )
            )

    @staticmethod
    def _call_name(function: ast.expr) -> str | None:
        if isinstance(function, ast.Name):
            return function.id
        if isinstance(function, ast.Attribute):
            return function.attr
        return None


_MACRO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "description", "code", "assumptions"],
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "description": {"type": "string", "minLength": 1},
        "code": {"type": "string", "minLength": 1},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}


class FreeCADMacroGenerator:
    """Generate reviewable FreeCAD macro artifacts with a user-supplied model."""

    def __init__(self, model: ModelClient, *, policy: MacroPolicy | None = None) -> None:
        self._model = model
        self._policy = policy or MacroPolicy()

    def generate(
        self,
        intent: str,
        *,
        freecad_version: str = "1.1",
        policy_mode: str = "safe",
        document_context: dict[str, Any] | None = None,
    ) -> MacroArtifact:
        if not intent.strip():
            raise MacroGenerationError("macro intent must not be empty")
        if policy_mode not in {"safe", "review"}:
            raise MacroGenerationError("policy_mode must be safe or review")
        messages = (
            ModelMessage(
                role="system",
                content=(
                    "You generate production-quality FreeCAD .FCMacro files in Python. Return "
                    "exactly one JSON object matching the supplied schema, without markdown. "
                    "Target the requested FreeCAD version and use documented native APIs. Prefer "
                    "FreeCAD, Part, PartDesign, Sketcher, Draft and TechDraw objects that remain "
                    "parametric and editable. Detect the active document when the request refers "
                    "to current context. Do not open, commit or abort document transactions: the "
                    "CADRIG execution host owns the transaction, backup and rollback. Raise "
                    "clear exceptions when prerequisites are missing or recompute fails. Do not "
                    "use eval, exec, subprocesses, "
                    "shell commands, network access, dynamic imports, or hidden downloads. Do not "
                    "invent API calls. Put executable Python only in the code field."
                ),
            ),
            ModelMessage(
                role="user",
                content=json.dumps(
                    {
                        "intent": intent,
                        "freecad_version": freecad_version,
                        "document_context": document_context,
                    },
                    sort_keys=True,
                ),
            ),
        )
        try:
            raw = self._model.complete(messages, response_schema=_MACRO_SCHEMA)
            payload = json.loads(raw)
        except ModelError as exc:
            raise MacroGenerationError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise MacroGenerationError("model returned non-JSON macro output") from exc
        if not isinstance(payload, dict):
            raise MacroGenerationError("model response must be one JSON object")
        allowed = {"name", "description", "code", "assumptions"}
        unknown = set(payload) - allowed
        missing = allowed - set(payload)
        if unknown:
            raise MacroGenerationError(f"unknown macro fields: {sorted(unknown)}")
        if missing:
            raise MacroGenerationError(f"missing macro fields: {sorted(missing)}")
        if not isinstance(payload["assumptions"], list) or not all(
            isinstance(item, str) for item in payload["assumptions"]
        ):
            raise MacroGenerationError("assumptions must be an array of strings")
        if not all(isinstance(payload[field], str) for field in ("name", "description", "code")):
            raise MacroGenerationError("name, description and code must be strings")

        diagnostics = self._policy.review(payload["code"])
        syntax_errors = [item for item in diagnostics if item.code == "SYNTAX_ERROR"]
        if syntax_errors:
            raise MacroGenerationError(self._diagnostic_message(syntax_errors))
        if policy_mode == "safe" and diagnostics:
            raise MacroGenerationError(self._diagnostic_message(list(diagnostics)))
        return MacroArtifact(
            name=payload["name"],
            description=payload["description"],
            code=payload["code"],
            assumptions=tuple(payload["assumptions"]),
            diagnostics=diagnostics,
            metadata={
                "freecad_version": freecad_version,
                "policy_mode": policy_mode,
                "auto_executed": False,
            },
        )

    def repair(
        self,
        *,
        intent: str,
        previous: MacroArtifact,
        failure: str,
        freecad_version: str = "1.1",
        policy_mode: str = "safe",
        document_context: dict[str, Any] | None = None,
    ) -> MacroArtifact:
        """Generate a replacement using the prior source and captured failure."""

        repair_request = json.dumps(
            {
                "task": "Repair the failed FreeCAD macro while preserving the original intent.",
                "original_intent": intent,
                "previous_macro": previous.code,
                "execution_failure": failure[-12000:],
            },
            sort_keys=True,
        )
        repaired = self.generate(
            repair_request,
            freecad_version=freecad_version,
            policy_mode=policy_mode,
            document_context=document_context,
        )
        metadata = dict(repaired.metadata)
        metadata["repair_of"] = previous.name
        return MacroArtifact(
            name=repaired.name,
            description=repaired.description,
            code=repaired.code,
            assumptions=repaired.assumptions,
            diagnostics=repaired.diagnostics,
            metadata=metadata,
        )

    @staticmethod
    def _diagnostic_message(diagnostics: list[MacroDiagnostic]) -> str:
        return "; ".join(
            f"{item.code} at line {item.line}: {item.message}" for item in diagnostics
        )


@dataclass(frozen=True)
class MacroExecutionReceipt:
    receipt_id: str
    status: str
    macro_name: str
    code_sha256: str
    elapsed_seconds: float
    stdout: str
    stderr: str
    error: str | None
    traceback: str | None
    backup_path: str | None
    document_name: str | None

    @property
    def accepted(self) -> bool:
        return self.status == "applied"

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "status": self.status,
            "accepted": self.accepted,
            "macro_name": self.macro_name,
            "code_sha256": self.code_sha256,
            "elapsed_seconds": self.elapsed_seconds,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "traceback": self.traceback,
            "backup_path": self.backup_path,
            "document_name": self.document_name,
        }


class FreeCADMacroExecutor:
    """Run an approved macro inside FreeCAD with backup, transaction and audit output."""

    def __init__(self, app: Any, *, backup_root: Path | None = None) -> None:
        self._app = app
        if backup_root is None:
            user_data = Path(str(app.getUserAppDataDir()))
            backup_root = user_data / "CADRIG" / "backups"
        self._backup_root = backup_root

    def execute(self, artifact: MacroArtifact, *, approved: bool = False) -> MacroExecutionReceipt:
        if not approved:
            raise MacroExecutionError("explicit approval is required before macro execution")
        syntax = MacroPolicy().review(artifact.code)
        syntax_errors = [item for item in syntax if item.code == "SYNTAX_ERROR"]
        if syntax_errors:
            raise MacroExecutionError(
                FreeCADMacroGenerator._diagnostic_message(syntax_errors)
            )

        receipt_id = str(uuid.uuid4())
        code_hash = hashlib.sha256(artifact.code.encode("utf-8")).hexdigest()
        active = getattr(self._app, "ActiveDocument", None)
        document_name = str(active.Name) if active is not None else None
        backup_path = self._backup(active, receipt_id) if active is not None else None
        documents_before = set(self._document_names())
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        started = time.perf_counter()
        transaction_open = False
        error: str | None = None
        traceback_text: str | None = None
        status = "applied"
        try:
            if active is not None:
                if getattr(active, "UndoMode", 1) == 0:
                    active.UndoMode = 1
                active.openTransaction(f"CADRIG macro: {artifact.name}")
                transaction_open = True
            namespace = {
                "__name__": "__main__",
                "__file__": f"{artifact.name}.FCMacro",
                "FreeCAD": self._app,
                "App": self._app,
            }
            compiled = compile(artifact.code, namespace["__file__"], "exec")
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(
                stderr_buffer
            ):
                exec(compiled, namespace, namespace)  # noqa: S102 - explicit approved macro boundary.
            self._recompute_and_inspect()
            if active is not None and transaction_open:
                active.commitTransaction()
                transaction_open = False
        except Exception as exc:  # noqa: BLE001 - capture FreeCAD and generated-code failures.
            status = "failed"
            error = str(exc) or type(exc).__name__
            traceback_text = traceback.format_exc()
            if active is not None and transaction_open:
                try:
                    active.abortTransaction()
                    transaction_open = False
                    active.recompute()
                except Exception as rollback_exc:  # noqa: BLE001
                    stderr_buffer.write(f"\nRollback error: {rollback_exc}\n")
            self._close_new_documents(documents_before)
        return MacroExecutionReceipt(
            receipt_id=receipt_id,
            status=status,
            macro_name=artifact.name,
            code_sha256=code_hash,
            elapsed_seconds=round(time.perf_counter() - started, 6),
            stdout=stdout_buffer.getvalue(),
            stderr=stderr_buffer.getvalue(),
            error=error,
            traceback=traceback_text,
            backup_path=str(backup_path) if backup_path else None,
            document_name=document_name,
        )

    def _backup(self, document: Any, receipt_id: str) -> Path:
        self._backup_root.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(document.Name))
        destination = self._backup_root / f"{safe_name}-{receipt_id}.FCStd"
        document.saveCopy(str(destination))
        if not destination.is_file() or destination.stat().st_size == 0:
            raise MacroExecutionError("FreeCAD could not create the pre-execution document backup")
        return destination

    def _document_names(self) -> tuple[str, ...]:
        documents = self._app.listDocuments()
        return tuple(documents)

    def _close_new_documents(self, documents_before: set[str]) -> None:
        for name in set(self._document_names()) - documents_before:
            try:
                self._app.closeDocument(name)
            except Exception as exc:  # noqa: BLE001 - best-effort generated-code cleanup.
                console = getattr(self._app, "Console", None)
                if console is not None:
                    console.PrintWarning(f"CADRIG could not close {name}: {exc}\n")

    def _recompute_and_inspect(self) -> None:
        documents = self._app.listDocuments()
        values = documents.values() if isinstance(documents, dict) else documents
        for document in values:
            if document.recompute() is False:
                raise RuntimeError(f"FreeCAD recompute failed for {document.Name}")
            for obj in document.Objects:
                states = [str(item).lower() for item in getattr(obj, "State", ())]
                if any("error" in item or "invalid" in item for item in states):
                    raise RuntimeError(f"FreeCAD feature {obj.Name} is invalid")
                shape = getattr(obj, "Shape", None)
                if (
                    shape is not None
                    and hasattr(shape, "isNull")
                    and not shape.isNull()
                    and hasattr(shape, "isValid")
                    and not shape.isValid()
                ):
                    raise RuntimeError(f"FreeCAD feature {obj.Name} produced invalid geometry")
