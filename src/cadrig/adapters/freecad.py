"""First-party in-process adapter for FreeCAD's native Python API."""

from __future__ import annotations

import importlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any, ClassVar

from cadrig.contracts import (
    ACTION_PLAN_SCHEMA_VERSION,
    Action,
    ActionKind,
    ActionPlan,
    AdapterMetadata,
    Diagnostic,
    DocumentSnapshot,
    ExecutionReceipt,
    ExecutionStatus,
    FeatureSnapshot,
    content_hash,
)


class FreeCADUnavailableError(ImportError):
    """Raised when the adapter is loaded outside a FreeCAD Python runtime."""


class _ActionFailure(ValueError):
    def __init__(self, code: str, message: str, action_id: str | None = None):
        super().__init__(message)
        self.diagnostic = Diagnostic(code=code, message=message, action_id=action_id)


@dataclass
class _RevisionState:
    fingerprint: str
    revision: int


@dataclass
class _UndoRecord:
    document_id: str
    expected_revision: int
    created_document: bool
    original_receipt: ExecutionReceipt


class FreeCADKernelAdapter:
    """Operate on native FreeCAD documents without exposing its API to the model."""

    _SUPPORTED = (
        ActionKind.CREATE_DOCUMENT,
        ActionKind.CREATE_SKETCH,
        ActionKind.ADD_SKETCH_GEOMETRY,
        ActionKind.ADD_CONSTRAINT,
        ActionKind.ADD_BOX,
        ActionKind.ADD_CYLINDER,
        ActionKind.EXTRUDE,
        ActionKind.BOOLEAN_CUT,
        ActionKind.SET_PARAMETER,
        ActionKind.DELETE_FEATURE,
    )
    _FEATURE_ACTIONS: ClassVar[set[ActionKind]] = {
        ActionKind.CREATE_SKETCH,
        ActionKind.ADD_BOX,
        ActionKind.ADD_CYLINDER,
        ActionKind.EXTRUDE,
        ActionKind.BOOLEAN_CUT,
    }

    def __init__(
        self,
        *,
        app: Any | None = None,
        gui: Any | None = None,
        part: Any | None = None,
        sketcher: Any | None = None,
    ) -> None:
        try:
            self._app = app or importlib.import_module("FreeCAD")
            self._part = part or importlib.import_module("Part")
            self._sketcher = sketcher or importlib.import_module("Sketcher")
        except ImportError as exc:
            raise FreeCADUnavailableError(
                "FreeCAD adapter must run inside FreeCAD or FreeCADCmd's Python runtime"
            ) from exc
        if gui is not None:
            self._gui = gui
        else:
            try:
                self._gui = importlib.import_module("FreeCADGui")
            except ImportError:
                self._gui = None
        self._revisions: dict[str, _RevisionState] = {}
        self._undo: dict[str, _UndoRecord] = {}

    def metadata(self) -> AdapterMetadata:
        version = "unknown"
        try:
            version = ".".join(str(item) for item in self._app.Version()[:3])
        except (AttributeError, TypeError):
            pass
        return AdapterMetadata(
            adapter_id="freecad",
            display_name="FreeCAD native adapter",
            adapter_version="0.1.0",
            protocol_version=ACTION_PLAN_SCHEMA_VERSION,
            supported_actions=self._SUPPORTED,
            native_host=f"FreeCAD {version}",
        )

    def observe(self, document_id: str) -> DocumentSnapshot | None:
        document = self._get_document(document_id)
        if document is None:
            self._revisions.pop(document_id, None)
            return None
        provisional = self._snapshot(document, revision=0)
        fingerprint = self._fingerprint(provisional)
        state = self._revisions.get(document_id)
        if state is None:
            state = _RevisionState(fingerprint=fingerprint, revision=0)
            self._revisions[document_id] = state
        elif state.fingerprint != fingerprint:
            state = _RevisionState(fingerprint=fingerprint, revision=state.revision + 1)
            self._revisions[document_id] = state
        return self._snapshot(document, revision=state.revision)

    def validate(self, plan: ActionPlan) -> tuple[Diagnostic, ...]:
        diagnostics: list[Diagnostic] = []
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", plan.document_id):
            diagnostics.append(
                Diagnostic(
                    code="INVALID_DOCUMENT_ID",
                    message=(
                        "FreeCAD document_id must start with a letter or underscore and contain "
                        "only letters, digits and underscores"
                    ),
                )
            )
        current = self.observe(plan.document_id)
        current_revision = current.revision if current else 0
        if current_revision != plan.base_revision:
            diagnostics.append(
                Diagnostic(
                    code="STALE_DOCUMENT_REVISION",
                    message=(
                        f"plan expects revision {plan.base_revision}, "
                        f"but document is revision {current_revision}"
                    ),
                )
            )

        create_actions = [
            action for action in plan.actions if action.kind is ActionKind.CREATE_DOCUMENT
        ]
        if current is None:
            if len(create_actions) != 1 or plan.actions[0].kind is not ActionKind.CREATE_DOCUMENT:
                diagnostics.append(
                    Diagnostic(
                        code="DOCUMENT_NOT_FOUND",
                        message="a missing document must begin with exactly one create_document action",
                    )
                )
        elif create_actions:
            diagnostics.append(
                Diagnostic(
                    code="DOCUMENT_ALREADY_EXISTS",
                    message="create_document cannot target an existing document",
                    action_id=create_actions[0].action_id,
                )
            )

        known_ids = {feature.feature_id for feature in current.features} if current else set()
        for action in plan.actions:
            if action.kind not in self._SUPPORTED:
                diagnostics.append(
                    Diagnostic(
                        code="UNSUPPORTED_ACTION",
                        message=f"FreeCAD adapter does not support {action.kind.value}",
                        action_id=action.action_id,
                    )
                )
                continue
            try:
                self._preflight(action, known_ids)
            except _ActionFailure as exc:
                diagnostics.append(exc.diagnostic)
            else:
                if action.kind in self._FEATURE_ACTIONS:
                    known_ids.add(str(action.parameters["feature_id"]))
                elif action.kind is ActionKind.DELETE_FEATURE and action.target_id:
                    known_ids.discard(action.target_id)
        return tuple(diagnostics)

    def execute(self, plan: ActionPlan, *, dry_run: bool = False) -> ExecutionReceipt:
        before = self.observe(plan.document_id)
        diagnostics = self.validate(plan)
        receipt_id = str(uuid.uuid4())
        if diagnostics:
            return self._receipt(
                receipt_id, plan.plan_id, ExecutionStatus.REFUSED, diagnostics, before, before
            )

        document = self._get_document(plan.document_id)
        created_document = document is None
        if (
            not dry_run
            and not created_document
            and getattr(document, "UndoMode", 1) == 0
        ):
            return self._receipt(
                receipt_id,
                plan.plan_id,
                ExecutionStatus.REFUSED,
                (
                    Diagnostic(
                        code="UNDO_UNAVAILABLE",
                        message=(
                            "FreeCAD undo is disabled for this document; refusing a change that "
                            "could not satisfy the rollback contract"
                        ),
                    ),
                ),
                before,
                before,
            )
        transaction_open = False
        try:
            actions = list(plan.actions)
            if created_document:
                document = self._app.newDocument(plan.document_id)
                actions.pop(0)
            assert document is not None
            document.openTransaction(f"CADRIG: {plan.plan_id}")
            transaction_open = True
            for action in actions:
                self._apply(document, action)
            self._recompute_and_inspect(document)
            after = self._snapshot(document, revision=plan.base_revision + 1)

            if dry_run:
                if created_document:
                    document.abortTransaction()
                    transaction_open = False
                    self._app.closeDocument(document.Name)
                else:
                    document.abortTransaction()
                    transaction_open = False
                    document.recompute()
                return self._receipt(
                    receipt_id, plan.plan_id, ExecutionStatus.DRY_RUN, (), before, after
                )

            document.commitTransaction()
            transaction_open = False
            fingerprint = self._fingerprint(after)
            self._revisions[plan.document_id] = _RevisionState(
                fingerprint=fingerprint, revision=after.revision
            )
            receipt = self._receipt(
                receipt_id, plan.plan_id, ExecutionStatus.APPLIED, (), before, after
            )
            self._undo[receipt_id] = _UndoRecord(
                document_id=plan.document_id,
                expected_revision=after.revision,
                created_document=created_document,
                original_receipt=receipt,
            )
            return receipt
        except _ActionFailure as exc:
            diagnostic = exc.diagnostic
        except Exception as exc:  # noqa: BLE001 - host exceptions vary by FreeCAD build.
            diagnostic = Diagnostic(code="FREECAD_ERROR", message=str(exc) or type(exc).__name__)

        cleanup_diagnostic = None
        if document is not None:
            try:
                if transaction_open:
                    document.abortTransaction()
                if created_document:
                    self._app.closeDocument(document.Name)
                else:
                    document.recompute()
            except Exception as exc:  # noqa: BLE001 - report cleanup failures from host APIs.
                cleanup_diagnostic = Diagnostic(
                    code="FREECAD_CLEANUP_ERROR", message=str(exc) or type(exc).__name__
                )
        failure_diagnostics = (diagnostic,)
        if cleanup_diagnostic is not None:
            failure_diagnostics += (cleanup_diagnostic,)
        return self._receipt(
            receipt_id, plan.plan_id, ExecutionStatus.REFUSED, failure_diagnostics, before, before
        )

    def rollback(self, receipt_id: str) -> ExecutionReceipt:
        record = self._undo.get(receipt_id)
        if record is None:
            return self._rollback_refusal(receipt_id, "ROLLBACK_NOT_FOUND", "unknown receipt")
        current = self.observe(record.document_id)
        if current is None or current.revision != record.expected_revision:
            return self._rollback_refusal(
                receipt_id,
                "ROLLBACK_CONFLICT",
                "document changed after this transaction; rollback would discard newer work",
            )

        document = self._get_document(record.document_id)
        assert document is not None
        before = current
        try:
            if record.created_document:
                self._app.closeDocument(document.Name)
                after = None
                self._revisions.pop(record.document_id, None)
            else:
                undone = document.undo()
                if undone is False:
                    raise RuntimeError("FreeCAD has no undo record for this transaction")
                document.recompute()
                restored_revision = (
                    record.original_receipt.before.revision
                    if record.original_receipt.before is not None
                    else 0
                )
                after = self._snapshot(document, restored_revision)
                self._revisions[record.document_id] = _RevisionState(
                    fingerprint=self._fingerprint(after), revision=restored_revision
                )
        except Exception as exc:  # noqa: BLE001 - host exceptions vary by FreeCAD build.
            return self._rollback_refusal(receipt_id, "FREECAD_ROLLBACK_ERROR", str(exc))
        del self._undo[receipt_id]
        return self._receipt(
            str(uuid.uuid4()),
            record.original_receipt.plan_id,
            ExecutionStatus.ROLLED_BACK,
            (),
            before,
            after,
        )

    def _preflight(self, action: Action, known_ids: set[str]) -> None:
        if action.kind is ActionKind.CREATE_DOCUMENT:
            return
        if action.kind in self._FEATURE_ACTIONS:
            feature_id = self._required_string(action, "feature_id")
            if feature_id in known_ids:
                raise _ActionFailure(
                    "DUPLICATE_FEATURE", f"feature {feature_id} already exists", action.action_id
                )
        if action.kind in {
            ActionKind.ADD_SKETCH_GEOMETRY,
            ActionKind.ADD_CONSTRAINT,
            ActionKind.SET_PARAMETER,
            ActionKind.DELETE_FEATURE,
            ActionKind.EXTRUDE,
            ActionKind.BOOLEAN_CUT,
        } and (not action.target_id or action.target_id not in known_ids):
            raise _ActionFailure(
                "FEATURE_NOT_FOUND", "target feature does not exist", action.action_id
            )
        if action.kind is ActionKind.ADD_BOX:
            self._positive_parameters(action, ("width", "depth", "height"))
        elif action.kind is ActionKind.ADD_CYLINDER:
            self._positive_parameters(action, ("radius", "height"))
        elif action.kind is ActionKind.CREATE_SKETCH:
            plane = action.parameters.get("plane", "XY")
            if plane not in {"XY", "XZ", "YZ"}:
                raise _ActionFailure(
                    "INVALID_PARAMETER", "plane must be XY, XZ or YZ", action.action_id
                )
        elif action.kind is ActionKind.ADD_SKETCH_GEOMETRY:
            geometry = action.parameters.get("geometry")
            if not isinstance(geometry, list) or not geometry:
                raise _ActionFailure(
                    "INVALID_PARAMETER", "geometry must be a non-empty array", action.action_id
                )
            seen: set[str] = set()
            for item in geometry:
                if not isinstance(item, dict):
                    raise _ActionFailure(
                        "INVALID_PARAMETER", "each geometry item must be an object", action.action_id
                    )
                geometry_id = item.get("geometry_id")
                if not isinstance(geometry_id, str) or not geometry_id.strip() or geometry_id in seen:
                    raise _ActionFailure(
                        "INVALID_PARAMETER", "geometry_id values must be non-empty and unique", action.action_id
                    )
                seen.add(geometry_id)
                if item.get("type") not in {"line", "circle"}:
                    raise _ActionFailure(
                        "INVALID_PARAMETER", "geometry type must be line or circle", action.action_id
                    )
        elif action.kind is ActionKind.ADD_CONSTRAINT:
            constraints = action.parameters.get("constraints")
            if not isinstance(constraints, list) or not constraints:
                raise _ActionFailure(
                    "INVALID_PARAMETER", "constraints must be a non-empty array", action.action_id
                )
        elif action.kind is ActionKind.EXTRUDE:
            self._positive_parameters(action, ("length",))
        elif action.kind is ActionKind.BOOLEAN_CUT:
            tool_id = self._required_string(action, "tool_id")
            if tool_id not in known_ids:
                raise _ActionFailure(
                    "FEATURE_NOT_FOUND", "boolean tool feature does not exist", action.action_id
                )
            if tool_id == action.target_id:
                raise _ActionFailure(
                    "INVALID_PARAMETER", "boolean base and tool must differ", action.action_id
                )
        elif action.kind is ActionKind.SET_PARAMETER:
            self._required_string(action, "name")
            value = action.parameters.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise _ActionFailure(
                    "INVALID_PARAMETER", "value must be a finite number", action.action_id
                )

    def _apply(self, document: Any, action: Action) -> None:
        if action.kind is ActionKind.ADD_BOX:
            feature_id = self._required_string(action, "feature_id")
            values = self._positive_parameters(action, ("width", "depth", "height"))
            obj = document.addObject("Part::Box", self._internal_name(feature_id))
            self._tag(obj, feature_id, "box")
            obj.Length, obj.Width, obj.Height = values["width"], values["depth"], values["height"]
        elif action.kind is ActionKind.ADD_CYLINDER:
            feature_id = self._required_string(action, "feature_id")
            values = self._positive_parameters(action, ("radius", "height"))
            obj = document.addObject("Part::Cylinder", self._internal_name(feature_id))
            self._tag(obj, feature_id, "cylinder")
            obj.Radius, obj.Height = values["radius"], values["height"]
        elif action.kind is ActionKind.CREATE_SKETCH:
            self._create_sketch(document, action)
        elif action.kind is ActionKind.ADD_SKETCH_GEOMETRY:
            self._add_sketch_geometry(document, action)
        elif action.kind is ActionKind.ADD_CONSTRAINT:
            self._add_constraints(document, action)
        elif action.kind is ActionKind.EXTRUDE:
            self._extrude(document, action)
        elif action.kind is ActionKind.BOOLEAN_CUT:
            self._boolean_cut(document, action)
        elif action.kind is ActionKind.SET_PARAMETER:
            self._set_parameter(document, action)
        elif action.kind is ActionKind.DELETE_FEATURE:
            obj = self._object_by_id(document, action.target_id)
            if obj is None:
                raise _ActionFailure("FEATURE_NOT_FOUND", "delete target does not exist", action.action_id)
            document.removeObject(obj.Name)
        else:
            raise _ActionFailure(
                "UNSUPPORTED_ACTION",
                f"FreeCAD adapter does not support {action.kind.value}",
                action.action_id,
            )

    def _create_sketch(self, document: Any, action: Action) -> None:
        feature_id = self._required_string(action, "feature_id")
        plane = action.parameters.get("plane", "XY")
        offset = action.parameters.get("offset", 0)
        if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset):
            raise _ActionFailure("INVALID_PARAMETER", "offset must be finite", action.action_id)
        obj = document.addObject("Sketcher::SketchObject", self._internal_name(feature_id))
        self._tag(obj, feature_id, "sketch")
        self._ensure_property(obj, "App::PropertyString", "CadrigGeometryMap")
        obj.CadrigGeometryMap = "{}"
        if plane == "XY":
            rotation = self._app.Rotation()
            base = self._app.Vector(0, 0, float(offset))
        elif plane == "XZ":
            rotation = self._app.Rotation(self._app.Vector(1, 0, 0), 90)
            base = self._app.Vector(0, float(offset), 0)
        else:
            rotation = self._app.Rotation(self._app.Vector(0, 1, 0), 90)
            base = self._app.Vector(float(offset), 0, 0)
        obj.Placement = self._app.Placement(base, rotation)

    def _add_sketch_geometry(self, document: Any, action: Action) -> None:
        sketch = self._required_object(document, action.target_id, action.action_id)
        if not str(getattr(sketch, "TypeId", "")).startswith("Sketcher::SketchObject"):
            raise _ActionFailure("INVALID_TARGET", "target must be a sketch", action.action_id)
        mapping = self._geometry_map(sketch)
        for item in action.parameters["geometry"]:
            geometry_id = item["geometry_id"]
            if geometry_id in mapping:
                raise _ActionFailure(
                    "DUPLICATE_GEOMETRY", f"geometry {geometry_id} already exists", action.action_id
                )
            if item["type"] == "line":
                start = self._point(item.get("start"), "start", action.action_id)
                end = self._point(item.get("end"), "end", action.action_id)
                geometry = self._part.LineSegment(
                    self._app.Vector(start[0], start[1], 0),
                    self._app.Vector(end[0], end[1], 0),
                )
            else:
                center = self._point(item.get("center"), "center", action.action_id)
                radius = item.get("radius")
                if isinstance(radius, bool) or not isinstance(radius, (int, float)) or radius <= 0:
                    raise _ActionFailure(
                        "INVALID_PARAMETER", "circle radius must be positive", action.action_id
                    )
                geometry = self._part.Circle(
                    self._app.Vector(center[0], center[1], 0),
                    self._app.Vector(0, 0, 1),
                    float(radius),
                )
            mapping[geometry_id] = int(sketch.addGeometry(geometry, False))
        sketch.CadrigGeometryMap = json.dumps(mapping, sort_keys=True)

    def _add_constraints(self, document: Any, action: Action) -> None:
        sketch = self._required_object(document, action.target_id, action.action_id)
        mapping = self._geometry_map(sketch)
        for item in action.parameters["constraints"]:
            if not isinstance(item, dict):
                raise _ActionFailure("INVALID_PARAMETER", "constraint must be an object", action.action_id)
            kind = item.get("type")
            first = self._geometry_index(mapping, item.get("geometry_id"), action.action_id)
            if kind in {"horizontal", "vertical", "block"}:
                native_kind = {"horizontal": "Horizontal", "vertical": "Vertical", "block": "Block"}[kind]
                constraint = self._sketcher.Constraint(native_kind, first)
            elif kind in {"radius", "diameter", "distance"}:
                value = item.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                    raise _ActionFailure("INVALID_PARAMETER", "constraint value must be positive", action.action_id)
                native_kind = {"radius": "Radius", "diameter": "Diameter", "distance": "Distance"}[kind]
                constraint = self._sketcher.Constraint(native_kind, first, float(value))
            elif kind in {"parallel", "perpendicular", "equal"}:
                second = self._geometry_index(mapping, item.get("other_geometry_id"), action.action_id)
                native_kind = {
                    "parallel": "Parallel",
                    "perpendicular": "Perpendicular",
                    "equal": "Equal",
                }[kind]
                constraint = self._sketcher.Constraint(native_kind, first, second)
            elif kind == "coincident":
                second = self._geometry_index(mapping, item.get("other_geometry_id"), action.action_id)
                first_point = self._point_position(item.get("point", "end"), action.action_id)
                second_point = self._point_position(item.get("other_point", "start"), action.action_id)
                constraint = self._sketcher.Constraint(
                    "Coincident", first, first_point, second, second_point
                )
            else:
                raise _ActionFailure(
                    "INVALID_PARAMETER",
                    "constraint type must be horizontal, vertical, block, radius, diameter, "
                    "distance, parallel, perpendicular, equal or coincident",
                    action.action_id,
                )
            sketch.addConstraint(constraint)

    def _extrude(self, document: Any, action: Action) -> None:
        base = self._required_object(document, action.target_id, action.action_id)
        feature_id = self._required_string(action, "feature_id")
        length = self._positive_parameters(action, ("length",))["length"]
        obj = document.addObject("Part::Extrusion", self._internal_name(feature_id))
        self._tag(obj, feature_id, "extrusion")
        obj.Base = base
        if hasattr(obj, "DirMode"):
            obj.DirMode = "Custom"
        obj.Dir = self._app.Vector(0, 0, length)
        if hasattr(obj, "LengthFwd"):
            obj.LengthFwd = length
        obj.Solid = bool(action.parameters.get("solid", True))
        if hasattr(obj, "Symmetric"):
            obj.Symmetric = bool(action.parameters.get("symmetric", False))

    def _boolean_cut(self, document: Any, action: Action) -> None:
        base = self._required_object(document, action.target_id, action.action_id)
        tool = self._required_object(document, action.parameters.get("tool_id"), action.action_id)
        feature_id = self._required_string(action, "feature_id")
        obj = document.addObject("Part::Cut", self._internal_name(feature_id))
        self._tag(obj, feature_id, "boolean_cut")
        obj.Base, obj.Tool = base, tool

    def _set_parameter(self, document: Any, action: Action) -> None:
        obj = self._required_object(document, action.target_id, action.action_id)
        name = self._required_string(action, "name")
        value = float(action.parameters["value"])
        property_map = {
            "Part::Box": {"width": "Length", "depth": "Width", "height": "Height"},
            "Part::Cylinder": {"radius": "Radius", "height": "Height"},
            "Part::Extrusion": {"length": "LengthFwd" if hasattr(obj, "LengthFwd") else "Dir"},
        }
        native = property_map.get(getattr(obj, "TypeId", ""), {}).get(name)
        if native is None:
            raise _ActionFailure(
                "PARAMETER_NOT_FOUND", f"parameter {name} is not editable on target", action.action_id
            )
        if name in {"width", "depth", "height", "radius", "length"} and value <= 0:
            raise _ActionFailure("INVALID_PARAMETER", "parameter must be positive", action.action_id)
        if native == "Dir":
            obj.Dir = self._app.Vector(0, 0, value)
        else:
            setattr(obj, native, value)

    def _recompute_and_inspect(self, document: Any) -> None:
        result = document.recompute()
        if result is False:
            raise _ActionFailure("RECOMPUTE_FAILED", "FreeCAD recompute failed")
        for obj in document.Objects:
            states = [str(item).lower() for item in getattr(obj, "State", ())]
            if any("error" in item or "invalid" in item for item in states):
                raise _ActionFailure(
                    "RECOMPUTE_FAILED", f"feature {self._feature_id(obj)} is invalid"
                )
            shape = getattr(obj, "Shape", None)
            if (
                shape is not None
                and hasattr(shape, "isNull")
                and not shape.isNull()
                and hasattr(shape, "isValid")
                and not shape.isValid()
            ):
                raise _ActionFailure(
                    "INVALID_SHAPE", f"feature {self._feature_id(obj)} produced invalid geometry"
                )

    def _snapshot(self, document: Any, revision: int) -> DocumentSnapshot:
        features = tuple(self._feature_snapshot(obj) for obj in document.Objects)
        selected_ids: tuple[str, ...] = ()
        if self._gui is not None:
            try:
                selected = self._gui.Selection.getSelection(document.Name)
                selected_ids = tuple(
                    dict.fromkeys(
                        self._feature_id(obj)
                        for obj in selected
                        if getattr(obj, "Document", document) is document
                    )
                )
            except Exception:  # noqa: BLE001 - selection is optional across GUI/headless builds.
                selected_ids = ()
        return DocumentSnapshot(
            document_id=document.Name,
            revision=revision,
            features=features,
            selected_ids=selected_ids,
        )

    def _feature_snapshot(self, obj: Any) -> FeatureSnapshot:
        type_id = str(getattr(obj, "TypeId", "Unknown"))
        parameters: dict[str, Any] = {
            "label": str(getattr(obj, "Label", getattr(obj, "Name", ""))),
            "native_type": type_id,
        }
        if type_id == "Part::Box":
            parameters.update(
                width=self._number(obj.Length),
                depth=self._number(obj.Width),
                height=self._number(obj.Height),
            )
        elif type_id == "Part::Cylinder":
            parameters.update(radius=self._number(obj.Radius), height=self._number(obj.Height))
        elif type_id.startswith("Sketcher::SketchObject"):
            parameters.update(
                geometry_ids=sorted(self._geometry_map(obj)),
                geometry_count=int(getattr(obj, "GeometryCount", len(getattr(obj, "Geometry", ())))),
                constraint_count=len(getattr(obj, "Constraints", ())),
            )
        elif type_id == "Part::Extrusion":
            parameters.update(
                base_id=self._feature_id(obj.Base) if getattr(obj, "Base", None) else None,
                length=self._extrusion_length(obj),
                solid=bool(getattr(obj, "Solid", False)),
            )
        elif type_id == "Part::Cut":
            parameters.update(
                base_id=self._feature_id(obj.Base) if getattr(obj, "Base", None) else None,
                tool_id=self._feature_id(obj.Tool) if getattr(obj, "Tool", None) else None,
            )
        shape = getattr(obj, "Shape", None)
        if shape is not None and hasattr(shape, "isNull") and not shape.isNull():
            volume = self._optional_number(getattr(shape, "Volume", None))
            area = self._optional_number(getattr(shape, "Area", None))
            if volume is not None:
                parameters["volume"] = volume
            if area is not None:
                parameters["area"] = area
        return FeatureSnapshot(
            feature_id=self._feature_id(obj),
            feature_type=self._feature_kind(obj),
            parameters=parameters,
        )

    def _get_document(self, document_id: str) -> Any | None:
        try:
            return self._app.getDocument(document_id)
        except (NameError, RuntimeError):
            return None

    def _required_object(self, document: Any, feature_id: Any, action_id: str) -> Any:
        obj = self._object_by_id(document, feature_id)
        if obj is None:
            raise _ActionFailure("FEATURE_NOT_FOUND", f"feature {feature_id} does not exist", action_id)
        return obj

    def _object_by_id(self, document: Any, feature_id: Any) -> Any | None:
        if not isinstance(feature_id, str):
            return None
        return next((obj for obj in document.Objects if self._feature_id(obj) == feature_id), None)

    @staticmethod
    def _feature_id(obj: Any) -> str:
        value = getattr(obj, "CadrigFeatureId", None)
        return value if isinstance(value, str) and value else str(obj.Name)

    @staticmethod
    def _feature_kind(obj: Any) -> str:
        value = getattr(obj, "CadrigFeatureKind", None)
        if isinstance(value, str) and value:
            return value
        return str(getattr(obj, "TypeId", "unknown"))

    def _tag(self, obj: Any, feature_id: str, feature_kind: str) -> None:
        self._ensure_property(obj, "App::PropertyString", "CadrigFeatureId")
        self._ensure_property(obj, "App::PropertyString", "CadrigFeatureKind")
        obj.CadrigFeatureId = feature_id
        obj.CadrigFeatureKind = feature_kind
        obj.Label = feature_id

    @staticmethod
    def _ensure_property(obj: Any, property_type: str, name: str) -> None:
        if name not in getattr(obj, "PropertiesList", ()):
            obj.addProperty(property_type, name, "CADRIG")

    @staticmethod
    def _internal_name(feature_id: str) -> str:
        result = re.sub(r"[^A-Za-z0-9_]", "_", feature_id)
        if not result or result[0].isdigit():
            result = f"Feature_{result}"
        return result[:96]

    @staticmethod
    def _required_string(action: Action, name: str) -> str:
        value = action.parameters.get(name)
        if not isinstance(value, str) or not value.strip():
            raise _ActionFailure(
                "INVALID_PARAMETER", f"{name} must be a non-empty string", action.action_id
            )
        return value

    @staticmethod
    def _positive_parameters(action: Action, names: tuple[str, ...]) -> dict[str, float]:
        result: dict[str, float] = {}
        for name in names:
            value = action.parameters.get(name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise _ActionFailure(
                    "INVALID_PARAMETER", f"{name} must be a positive finite number", action.action_id
                )
            result[name] = float(value)
        return result

    @staticmethod
    def _point(value: Any, name: str, action_id: str) -> tuple[float, float]:
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                for item in value
            )
        ):
            raise _ActionFailure(
                "INVALID_PARAMETER", f"{name} must be [x, y] finite numbers", action_id
            )
        return float(value[0]), float(value[1])

    @staticmethod
    def _geometry_map(sketch: Any) -> dict[str, int]:
        raw = getattr(sketch, "CadrigGeometryMap", "{}") or "{}"
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return {str(key): int(value) for key, value in payload.items()}

    @staticmethod
    def _geometry_index(mapping: dict[str, int], geometry_id: Any, action_id: str) -> int:
        if not isinstance(geometry_id, str) or geometry_id not in mapping:
            raise _ActionFailure(
                "GEOMETRY_NOT_FOUND", f"geometry {geometry_id} does not exist", action_id
            )
        return mapping[geometry_id]

    @staticmethod
    def _point_position(value: Any, action_id: str) -> int:
        try:
            return {"start": 1, "end": 2, "center": 3}[value]
        except (KeyError, TypeError) as exc:
            raise _ActionFailure(
                "INVALID_PARAMETER", "point must be start, end or center", action_id
            ) from exc

    @staticmethod
    def _number(value: Any) -> float:
        raw = getattr(value, "Value", value)
        return float(raw)

    @classmethod
    def _optional_number(cls, value: Any) -> float | None:
        try:
            result = cls._number(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    @classmethod
    def _extrusion_length(cls, obj: Any) -> float:
        if hasattr(obj, "LengthFwd"):
            value = cls._optional_number(obj.LengthFwd)
            if value is not None and value > 0:
                return value
        direction = getattr(obj, "Dir", None)
        if direction is None:
            return 0.0
        length = getattr(direction, "Length", None)
        if length is not None:
            return float(length)
        return math.sqrt(sum(float(getattr(direction, axis, 0)) ** 2 for axis in ("x", "y", "z")))

    @staticmethod
    def _fingerprint(snapshot: DocumentSnapshot) -> str:
        return content_hash([feature.to_dict() for feature in snapshot.features])

    @staticmethod
    def _receipt(
        receipt_id: str,
        plan_id: str,
        status: ExecutionStatus,
        diagnostics: tuple[Diagnostic, ...],
        before: DocumentSnapshot | None,
        after: DocumentSnapshot | None,
    ) -> ExecutionReceipt:
        return ExecutionReceipt(
            receipt_id=receipt_id,
            plan_id=plan_id,
            adapter_id="freecad",
            status=status,
            diagnostics=tuple(diagnostics),
            before=before,
            after=after,
        )

    def _rollback_refusal(self, plan_id: str, code: str, message: str) -> ExecutionReceipt:
        return self._receipt(
            str(uuid.uuid4()),
            plan_id,
            ExecutionStatus.REFUSED,
            (Diagnostic(code=code, message=message),),
            None,
            None,
        )


def make_adapter() -> FreeCADKernelAdapter:
    """Entry-point-compatible adapter factory."""

    return FreeCADKernelAdapter()
