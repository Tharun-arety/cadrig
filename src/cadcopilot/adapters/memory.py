"""Deterministic adapter used to prove the host contract without a CAD install."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from cadcopilot.contracts import (
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
)


@dataclass
class _Feature:
    feature_id: str
    feature_type: str
    parameters: dict[str, Any]


@dataclass
class _Document:
    document_id: str
    revision: int = 0
    features: dict[str, _Feature] = field(default_factory=dict)


@dataclass
class _UndoRecord:
    document_id: str
    expected_revision: int
    before: _Document | None
    original_receipt: ExecutionReceipt


class _ActionFailure(ValueError):
    def __init__(self, code: str, message: str, action_id: str):
        super().__init__(message)
        self.diagnostic = Diagnostic(code=code, message=message, action_id=action_id)


class MemoryKernelAdapter:
    """Small but rigorous reference implementation of the adapter protocol."""

    _SUPPORTED = (
        ActionKind.CREATE_DOCUMENT,
        ActionKind.ADD_BOX,
        ActionKind.ADD_CYLINDER,
        ActionKind.SET_PARAMETER,
        ActionKind.DELETE_FEATURE,
    )

    def __init__(self) -> None:
        self._documents: dict[str, _Document] = {}
        self._undo: dict[str, _UndoRecord] = {}
        self._lock = RLock()

    def metadata(self) -> AdapterMetadata:
        return AdapterMetadata(
            adapter_id="memory",
            display_name="In-memory contract adapter",
            adapter_version="0.1.0",
            protocol_version=ACTION_PLAN_SCHEMA_VERSION,
            supported_actions=self._SUPPORTED,
            native_host="none",
        )

    def observe(self, document_id: str) -> DocumentSnapshot | None:
        with self._lock:
            document = self._documents.get(document_id)
            return self._snapshot(document) if document else None

    def validate(self, plan: ActionPlan) -> tuple[Diagnostic, ...]:
        with self._lock:
            diagnostics = []
            for action in plan.actions:
                if action.kind not in self._SUPPORTED:
                    diagnostics.append(
                        Diagnostic(
                            code="UNSUPPORTED_ACTION",
                            message=f"memory adapter does not support {action.kind.value}",
                            action_id=action.action_id,
                        )
                    )

            current = self._documents.get(plan.document_id)
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
            return tuple(diagnostics)

    def execute(self, plan: ActionPlan, *, dry_run: bool = False) -> ExecutionReceipt:
        with self._lock:
            before_document = copy.deepcopy(self._documents.get(plan.document_id))
            before_snapshot = self._snapshot(before_document) if before_document else None
            diagnostics = self.validate(plan)
            receipt_id = str(uuid.uuid4())
            if diagnostics:
                return ExecutionReceipt(
                    receipt_id=receipt_id,
                    plan_id=plan.plan_id,
                    adapter_id="memory",
                    status=ExecutionStatus.REFUSED,
                    diagnostics=diagnostics,
                    before=before_snapshot,
                    after=before_snapshot,
                )

            candidate = copy.deepcopy(before_document)
            try:
                for action in plan.actions:
                    candidate = self._apply(plan.document_id, candidate, action)
            except _ActionFailure as exc:
                return ExecutionReceipt(
                    receipt_id=receipt_id,
                    plan_id=plan.plan_id,
                    adapter_id="memory",
                    status=ExecutionStatus.REFUSED,
                    diagnostics=(exc.diagnostic,),
                    before=before_snapshot,
                    after=before_snapshot,
                )

            assert candidate is not None
            candidate.revision = plan.base_revision + 1
            after_snapshot = self._snapshot(candidate)
            status = ExecutionStatus.DRY_RUN if dry_run else ExecutionStatus.APPLIED
            receipt = ExecutionReceipt(
                receipt_id=receipt_id,
                plan_id=plan.plan_id,
                adapter_id="memory",
                status=status,
                diagnostics=(),
                before=before_snapshot,
                after=after_snapshot,
            )
            if not dry_run:
                self._documents[plan.document_id] = candidate
                self._undo[receipt_id] = _UndoRecord(
                    document_id=plan.document_id,
                    expected_revision=candidate.revision,
                    before=before_document,
                    original_receipt=receipt,
                )
            return receipt

    def rollback(self, receipt_id: str) -> ExecutionReceipt:
        with self._lock:
            record = self._undo.get(receipt_id)
            if record is None:
                return self._rollback_refusal(receipt_id, "ROLLBACK_NOT_FOUND", "unknown receipt")
            current = self._documents.get(record.document_id)
            if current is None or current.revision != record.expected_revision:
                return self._rollback_refusal(
                    receipt_id,
                    "ROLLBACK_CONFLICT",
                    "document changed after this transaction; rollback would discard newer work",
                )

            before = self._snapshot(current)
            if record.before is None:
                del self._documents[record.document_id]
                after = None
            else:
                restored = copy.deepcopy(record.before)
                self._documents[record.document_id] = restored
                after = self._snapshot(restored)
            del self._undo[receipt_id]
            return ExecutionReceipt(
                receipt_id=str(uuid.uuid4()),
                plan_id=record.original_receipt.plan_id,
                adapter_id="memory",
                status=ExecutionStatus.ROLLED_BACK,
                diagnostics=(),
                before=before,
                after=after,
            )

    @staticmethod
    def _snapshot(document: _Document) -> DocumentSnapshot:
        features = tuple(
            FeatureSnapshot(
                feature_id=feature.feature_id,
                feature_type=feature.feature_type,
                parameters=feature.parameters,
            )
            for feature in document.features.values()
        )
        return DocumentSnapshot(
            document_id=document.document_id,
            revision=document.revision,
            features=features,
        )

    def _apply(
        self, document_id: str, document: _Document | None, action: Action
    ) -> _Document:
        if action.kind is ActionKind.CREATE_DOCUMENT:
            return _Document(document_id=document_id)
        if document is None:
            raise _ActionFailure("DOCUMENT_NOT_FOUND", "document does not exist", action.action_id)
        if action.kind is ActionKind.ADD_BOX:
            feature_id = self._new_feature_id(action)
            parameters = self._positive_parameters(action, ("width", "depth", "height"))
            self._add_feature(document, feature_id, "box", parameters, action.action_id)
        elif action.kind is ActionKind.ADD_CYLINDER:
            feature_id = self._new_feature_id(action)
            parameters = self._positive_parameters(action, ("radius", "height"))
            self._add_feature(document, feature_id, "cylinder", parameters, action.action_id)
        elif action.kind is ActionKind.SET_PARAMETER:
            self._set_parameter(document, action)
        elif action.kind is ActionKind.DELETE_FEATURE:
            target = action.target_id
            if target is None or target not in document.features:
                raise _ActionFailure(
                    "FEATURE_NOT_FOUND", "delete target does not exist", action.action_id
                )
            del document.features[target]
        else:
            raise _ActionFailure(
                "UNSUPPORTED_ACTION",
                f"memory adapter does not support {action.kind.value}",
                action.action_id,
            )
        return document

    @staticmethod
    def _new_feature_id(action: Action) -> str:
        value = action.parameters.get("feature_id")
        if not isinstance(value, str) or not value.strip():
            raise _ActionFailure(
                "INVALID_PARAMETER", "feature_id must be a non-empty string", action.action_id
            )
        return value

    @staticmethod
    def _positive_parameters(action: Action, names: tuple[str, ...]) -> dict[str, float]:
        result: dict[str, float] = {}
        for name in names:
            value = action.parameters.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise _ActionFailure(
                    "INVALID_PARAMETER",
                    f"{name} must be a positive number",
                    action.action_id,
                )
            result[name] = float(value)
        return result

    @staticmethod
    def _add_feature(
        document: _Document,
        feature_id: str,
        feature_type: str,
        parameters: dict[str, Any],
        action_id: str,
    ) -> None:
        if feature_id in document.features:
            raise _ActionFailure(
                "DUPLICATE_FEATURE", f"feature {feature_id} already exists", action_id
            )
        document.features[feature_id] = _Feature(feature_id, feature_type, parameters)

    @staticmethod
    def _set_parameter(document: _Document, action: Action) -> None:
        target = action.target_id
        if target is None or target not in document.features:
            raise _ActionFailure("FEATURE_NOT_FOUND", "edit target does not exist", action.action_id)
        name = action.parameters.get("name")
        value = action.parameters.get("value")
        feature = document.features[target]
        if not isinstance(name, str) or name not in feature.parameters:
            raise _ActionFailure(
                "PARAMETER_NOT_FOUND", "parameter does not exist on target", action.action_id
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise _ActionFailure(
                "INVALID_PARAMETER", "parameter value must be a positive number", action.action_id
            )
        feature.parameters[name] = float(value)

    def _rollback_refusal(self, plan_id: str, code: str, message: str) -> ExecutionReceipt:
        return ExecutionReceipt(
            receipt_id=str(uuid.uuid4()),
            plan_id=plan_id,
            adapter_id="memory",
            status=ExecutionStatus.REFUSED,
            diagnostics=(Diagnostic(code=code, message=message),),
            before=None,
            after=None,
        )
