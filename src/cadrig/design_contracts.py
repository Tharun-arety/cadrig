"""Backend-neutral engineering intent contracts for the native CADRIG agent."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any

from cadrig.contracts import ContractError, canonical_json

DESIGN_CONTRACT_SCHEMA_VERSION = "1.0.0"


class PredicateKind(str, Enum):
    """Deterministic facts the verifier can evaluate without an LLM."""

    DOCUMENT_EXISTS = "document_exists"
    FEATURE_EXISTS = "feature_exists"
    FEATURE_ABSENT = "feature_absent"
    FEATURE_COUNT = "feature_count"
    PARAMETER_EQUALS = "parameter_equals"
    REVISION_ADVANCED = "revision_advanced"
    PRESERVE_FEATURE = "preserve_feature"
    PRESERVE_PARAMETER = "preserve_parameter"
    MAX_FEATURE_DELTA = "max_feature_delta"


REQUIREMENT_KINDS = frozenset(
    {
        PredicateKind.DOCUMENT_EXISTS,
        PredicateKind.FEATURE_EXISTS,
        PredicateKind.FEATURE_ABSENT,
        PredicateKind.FEATURE_COUNT,
        PredicateKind.PARAMETER_EQUALS,
        PredicateKind.REVISION_ADVANCED,
    }
)
INVARIANT_KINDS = frozenset(
    {
        PredicateKind.PRESERVE_FEATURE,
        PredicateKind.PRESERVE_PARAMETER,
        PredicateKind.MAX_FEATURE_DELTA,
    }
)


def _freeze(value: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        canonical_json(dict(value))
    except (TypeError, ValueError) as exc:
        raise ContractError("predicate parameters must contain only JSON values") from exc
    return MappingProxyType(dict(value))


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field_name} must be a non-empty string")
    if len(value) > 128:
        raise ContractError(f"{field_name} must not exceed 128 characters")
    return value


@dataclass(frozen=True)
class DesignPredicate:
    predicate_id: str
    kind: PredicateKind
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _identifier(self.predicate_id, "predicate_id")
        if not isinstance(self.parameters, Mapping):
            raise ContractError("predicate parameters must be an object")
        object.__setattr__(self, "parameters", _freeze(self.parameters))
        self._validate_parameters()

    def _validate_parameters(self) -> None:
        parameters = self.parameters
        if self.kind in {
            PredicateKind.FEATURE_EXISTS,
            PredicateKind.FEATURE_ABSENT,
            PredicateKind.PARAMETER_EQUALS,
            PredicateKind.PRESERVE_FEATURE,
            PredicateKind.PRESERVE_PARAMETER,
        }:
            _identifier(parameters.get("feature_id"), "feature_id")
        if self.kind in {PredicateKind.PARAMETER_EQUALS, PredicateKind.PRESERVE_PARAMETER}:
            _identifier(parameters.get("name"), "parameter name")
        if self.kind is PredicateKind.PARAMETER_EQUALS:
            if "value" not in parameters:
                raise ContractError("parameter_equals requires value")
            tolerance = parameters.get("tolerance", 0.0)
            if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
                raise ContractError("tolerance must be a non-negative number")
            if tolerance < 0:
                raise ContractError("tolerance must be a non-negative number")
        if self.kind is PredicateKind.FEATURE_COUNT:
            count = parameters.get("count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ContractError("feature_count requires a non-negative integer count")
            feature_type = parameters.get("feature_type")
            if feature_type is not None:
                _identifier(feature_type, "feature_type")
        if self.kind is PredicateKind.MAX_FEATURE_DELTA:
            maximum = parameters.get("maximum")
            if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
                raise ContractError("max_feature_delta requires a non-negative integer maximum")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DesignPredicate:
        allowed = {"predicate_id", "kind", "parameters"}
        extras = set(payload) - allowed
        if extras:
            raise ContractError(f"unknown predicate fields: {sorted(extras)}")
        try:
            return cls(
                predicate_id=payload["predicate_id"],
                kind=PredicateKind(payload["kind"]),
                parameters=payload.get("parameters", {}),
            )
        except KeyError as exc:
            raise ContractError(f"missing predicate field: {exc.args[0]}") from exc
        except ValueError as exc:
            raise ContractError(f"unknown predicate kind: {payload.get('kind')}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicate_id": self.predicate_id,
            "kind": self.kind.value,
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class DesignContract:
    """Testable intent plus explicit preservation constraints."""

    contract_id: str
    intent: str
    document_id: str
    requirements: tuple[DesignPredicate, ...]
    invariants: tuple[DesignPredicate, ...] = ()
    schema_version: str = DESIGN_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _identifier(self.contract_id, "contract_id")
        _identifier(self.document_id, "document_id")
        if not isinstance(self.intent, str) or not self.intent.strip():
            raise ContractError("intent must be a non-empty string")
        if self.schema_version != DESIGN_CONTRACT_SCHEMA_VERSION:
            raise ContractError(f"unsupported design-contract schema: {self.schema_version}")
        if not self.requirements:
            raise ContractError("a design contract requires at least one requirement")
        predicates = (*self.requirements, *self.invariants)
        ids = [predicate.predicate_id for predicate in predicates]
        if len(ids) != len(set(ids)):
            raise ContractError("predicate_id values must be unique within a contract")
        invalid_requirements = [p.kind.value for p in self.requirements if p.kind not in REQUIREMENT_KINDS]
        invalid_invariants = [p.kind.value for p in self.invariants if p.kind not in INVARIANT_KINDS]
        if invalid_requirements:
            raise ContractError(f"invalid requirement predicates: {invalid_requirements}")
        if invalid_invariants:
            raise ContractError(f"invalid invariant predicates: {invalid_invariants}")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DesignContract:
        allowed = {
            "schema_version",
            "contract_id",
            "intent",
            "document_id",
            "requirements",
            "invariants",
        }
        extras = set(payload) - allowed
        if extras:
            raise ContractError(f"unknown design-contract fields: {sorted(extras)}")
        required = {"schema_version", "contract_id", "intent", "document_id", "requirements", "invariants"}
        missing = required - set(payload)
        if missing:
            raise ContractError(f"missing design-contract fields: {sorted(missing)}")
        requirements = payload["requirements"]
        invariants = payload["invariants"]
        if not isinstance(requirements, Sequence) or isinstance(requirements, (str, bytes)):
            raise ContractError("requirements must be an array")
        if not isinstance(invariants, Sequence) or isinstance(invariants, (str, bytes)):
            raise ContractError("invariants must be an array")
        return cls(
            schema_version=payload["schema_version"],
            contract_id=payload["contract_id"],
            intent=payload["intent"],
            document_id=payload["document_id"],
            requirements=tuple(DesignPredicate.from_dict(item) for item in requirements),
            invariants=tuple(DesignPredicate.from_dict(item) for item in invariants),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_id": self.contract_id,
            "intent": self.intent,
            "document_id": self.document_id,
            "requirements": [predicate.to_dict() for predicate in self.requirements],
            "invariants": [predicate.to_dict() for predicate in self.invariants],
        }


def design_contract_json_schema() -> dict[str, Any]:
    predicate_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["predicate_id", "kind", "parameters"],
        "properties": {
            "predicate_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "kind": {"enum": [kind.value for kind in PredicateKind]},
            "parameters": {"type": "object"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "contract_id",
            "intent",
            "document_id",
            "requirements",
            "invariants",
        ],
        "properties": {
            "schema_version": {"const": DESIGN_CONTRACT_SCHEMA_VERSION},
            "contract_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "intent": {"type": "string", "minLength": 1},
            "document_id": {"type": "string", "minLength": 1, "maxLength": 128},
            "requirements": {"type": "array", "minItems": 1, "items": predicate_schema},
            "invariants": {"type": "array", "items": predicate_schema},
        },
    }


def contract_prompt_reference() -> str:
    """Compact, stable predicate reference included in compiler prompts."""

    examples = {
        "document_exists": {},
        "feature_exists": {"feature_id": "semantic-id"},
        "feature_absent": {"feature_id": "semantic-id"},
        "feature_count": {"count": 1, "feature_type": "optional-type"},
        "parameter_equals": {
            "feature_id": "semantic-id",
            "name": "radius",
            "value": 4.0,
            "tolerance": 0.01,
        },
        "revision_advanced": {},
        "preserve_feature": {"feature_id": "semantic-id"},
        "preserve_parameter": {"feature_id": "semantic-id", "name": "height"},
        "max_feature_delta": {"maximum": 1},
    }
    return json.dumps(examples, sort_keys=True)
