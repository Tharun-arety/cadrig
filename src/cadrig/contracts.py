"""Serializable, host-independent CADRIG action and snapshot contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

ACTION_PLAN_SCHEMA_VERSION = "1.0.0"
SNAPSHOT_SCHEMA_VERSION = "1.0.0"


class ContractError(ValueError):
    """Raised when untrusted contract data is invalid."""


class ActionKind(str, Enum):
    CREATE_DOCUMENT = "create_document"
    CREATE_SKETCH = "create_sketch"
    ADD_SKETCH_GEOMETRY = "add_sketch_geometry"
    ADD_CONSTRAINT = "add_constraint"
    ADD_BOX = "add_box"
    ADD_CYLINDER = "add_cylinder"
    EXTRUDE = "extrude"
    REVOLVE = "revolve"
    BOOLEAN_CUT = "boolean_cut"
    FILLET = "fillet"
    CHAMFER = "chamfer"
    PATTERN = "pattern"
    SET_PARAMETER = "set_parameter"
    DELETE_FEATURE = "delete_feature"
    SAVE_DOCUMENT = "save_document"
    EXPORT_DOCUMENT = "export_document"


class ExecutionStatus(str, Enum):
    APPLIED = "applied"
    DRY_RUN = "dry_run"
    REFUSED = "refused"
    ROLLED_BACK = "rolled_back"


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string")
    if len(value) > 128:
        raise ContractError(f"{field_name} must not exceed 128 characters")
    return value


@dataclass(frozen=True)
class Action:
    action_id: str
    kind: ActionKind
    target_id: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.action_id, "action_id")
        if self.target_id is not None:
            _require_identifier(self.target_id, "target_id")
        if not isinstance(self.parameters, Mapping):
            raise ContractError("parameters must be an object")
        try:
            canonical_json(dict(self.parameters))
        except (TypeError, ValueError) as exc:
            raise ContractError("parameters must contain only JSON values") from exc
        object.__setattr__(self, "parameters", _freeze_mapping(self.parameters))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Action:
        allowed = {"action_id", "kind", "target_id", "parameters"}
        extras = set(payload) - allowed
        if extras:
            raise ContractError(f"unknown action fields: {sorted(extras)}")
        try:
            kind = ActionKind(payload["kind"])
            action_id = payload["action_id"]
        except KeyError as exc:
            raise ContractError(f"missing action field: {exc.args[0]}") from exc
        except ValueError as exc:
            raise ContractError(f"unknown action kind: {payload.get('kind')}") from exc
        return cls(
            action_id=action_id,
            kind=kind,
            target_id=payload.get("target_id"),
            parameters=payload.get("parameters", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action_id": self.action_id,
            "kind": self.kind.value,
            "parameters": dict(self.parameters),
        }
        if self.target_id is not None:
            result["target_id"] = self.target_id
        return result


@dataclass(frozen=True)
class ActionPlan:
    plan_id: str
    document_id: str
    base_revision: int
    actions: tuple[Action, ...]
    schema_version: str = ACTION_PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_identifier(self.plan_id, "plan_id")
        _require_identifier(self.document_id, "document_id")
        if self.schema_version != ACTION_PLAN_SCHEMA_VERSION:
            raise ContractError(f"unsupported action-plan schema: {self.schema_version}")
        if not isinstance(self.base_revision, int) or self.base_revision < 0:
            raise ContractError("base_revision must be a non-negative integer")
        if not self.actions:
            raise ContractError("an action plan must contain at least one action")
        ids = [action.action_id for action in self.actions]
        if len(ids) != len(set(ids)):
            raise ContractError("action_id values must be unique within a plan")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ActionPlan:
        allowed = {"schema_version", "plan_id", "document_id", "base_revision", "actions"}
        extras = set(payload) - allowed
        if extras:
            raise ContractError(f"unknown plan fields: {sorted(extras)}")
        required = {"schema_version", "plan_id", "document_id", "base_revision", "actions"}
        missing = required - set(payload)
        if missing:
            raise ContractError(f"missing plan fields: {sorted(missing)}")
        actions_payload = payload["actions"]
        if not isinstance(actions_payload, Sequence) or isinstance(actions_payload, (str, bytes)):
            raise ContractError("actions must be an array")
        return cls(
            schema_version=payload["schema_version"],
            plan_id=payload["plan_id"],
            document_id=payload["document_id"],
            base_revision=payload["base_revision"],
            actions=tuple(Action.from_dict(item) for item in actions_payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "document_id": self.document_id,
            "base_revision": self.base_revision,
            "actions": [action.to_dict() for action in self.actions],
        }


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    action_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.code, "diagnostic code")
        if not self.message:
            raise ContractError("diagnostic message must not be empty")

    def to_dict(self) -> dict[str, Any]:
        result = {"code": self.code, "message": self.message}
        if self.action_id is not None:
            result["action_id"] = self.action_id
        return result


@dataclass(frozen=True)
class FeatureSnapshot:
    feature_id: str
    feature_type: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_identifier(self.feature_id, "feature_id")
        _require_identifier(self.feature_type, "feature_type")
        object.__setattr__(self, "parameters", _freeze_mapping(self.parameters))

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "feature_type": self.feature_type,
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class DocumentSnapshot:
    document_id: str
    revision: int
    features: tuple[FeatureSnapshot, ...]
    selected_ids: tuple[str, ...] = ()
    schema_version: str = SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_identifier(self.document_id, "document_id")
        if self.revision < 0:
            raise ContractError("snapshot revision must be non-negative")

    def to_dict(self, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "document_id": self.document_id,
            "revision": self.revision,
            "features": [feature.to_dict() for feature in self.features],
            "selected_ids": list(self.selected_ids),
        }
        if include_hash:
            result["content_hash"] = content_hash(result)
        return result


@dataclass(frozen=True)
class AdapterMetadata:
    adapter_id: str
    display_name: str
    adapter_version: str
    protocol_version: str
    supported_actions: tuple[ActionKind, ...]
    native_host: str

    def __post_init__(self) -> None:
        _require_identifier(self.adapter_id, "adapter_id")
        _require_identifier(self.display_name, "display_name")
        _require_identifier(self.adapter_version, "adapter_version")
        _require_identifier(self.protocol_version, "protocol_version")
        _require_identifier(self.native_host, "native_host")
        if len(self.supported_actions) != len(set(self.supported_actions)):
            raise ContractError("supported_actions must not contain duplicates")

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "display_name": self.display_name,
            "adapter_version": self.adapter_version,
            "protocol_version": self.protocol_version,
            "supported_actions": [item.value for item in self.supported_actions],
            "native_host": self.native_host,
        }


@dataclass(frozen=True)
class ExecutionReceipt:
    receipt_id: str
    plan_id: str
    adapter_id: str
    status: ExecutionStatus
    diagnostics: tuple[Diagnostic, ...]
    before: DocumentSnapshot | None
    after: DocumentSnapshot | None

    @property
    def accepted(self) -> bool:
        return self.status in {
            ExecutionStatus.APPLIED,
            ExecutionStatus.DRY_RUN,
            ExecutionStatus.ROLLED_BACK,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "plan_id": self.plan_id,
            "adapter_id": self.adapter_id,
            "status": self.status.value,
            "accepted": self.accepted,
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "before": self.before.to_dict() if self.before else None,
            "after": self.after.to_dict() if self.after else None,
        }
