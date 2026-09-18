"""Independent, deterministic acceptance checks for CADRIG agent proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cadrig.contracts import DocumentSnapshot, ExecutionReceipt, content_hash
from cadrig.design_contracts import DesignContract, DesignPredicate, PredicateKind


@dataclass(frozen=True)
class VerificationIssue:
    predicate_id: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "predicate_id": self.predicate_id,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class VerificationReport:
    accepted: bool
    issues: tuple[VerificationIssue, ...]
    before_hash: str | None
    after_hash: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "issues": [issue.to_dict() for issue in self.issues],
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
        }


class ContractVerifier:
    """The sole authority that may accept a native-agent execution."""

    def verify(self, contract: DesignContract, receipt: ExecutionReceipt) -> VerificationReport:
        issues: list[VerificationIssue] = []
        if not receipt.accepted:
            if receipt.diagnostics:
                issues.extend(
                    VerificationIssue(
                        predicate_id="execution",
                        code=diagnostic.code,
                        message=diagnostic.message,
                    )
                    for diagnostic in receipt.diagnostics
                )
            else:
                issues.append(
                    VerificationIssue("execution", "EXECUTION_REFUSED", "adapter refused execution")
                )
        after = receipt.after
        before = receipt.before
        for predicate in contract.requirements:
            issue = self._evaluate_requirement(predicate, before, after)
            if issue:
                issues.append(issue)
        for predicate in contract.invariants:
            issue = self._evaluate_invariant(predicate, before, after)
            if issue:
                issues.append(issue)
        return VerificationReport(
            accepted=not issues,
            issues=tuple(issues),
            before_hash=self._snapshot_hash(before),
            after_hash=self._snapshot_hash(after),
        )

    def _evaluate_requirement(
        self,
        predicate: DesignPredicate,
        before: DocumentSnapshot | None,
        after: DocumentSnapshot | None,
    ) -> VerificationIssue | None:
        kind = predicate.kind
        parameters = predicate.parameters
        if kind is PredicateKind.DOCUMENT_EXISTS:
            return None if after is not None else self._failure(predicate, "DOCUMENT_MISSING")
        if kind is PredicateKind.REVISION_ADVANCED:
            base_revision = before.revision if before else 0
            if after is not None and after.revision > base_revision:
                return None
            return self._failure(predicate, "REVISION_NOT_ADVANCED")
        if after is None:
            return self._failure(predicate, "DOCUMENT_MISSING")
        features = {feature.feature_id: feature for feature in after.features}
        feature_id = parameters.get("feature_id")
        if kind is PredicateKind.FEATURE_EXISTS:
            return None if feature_id in features else self._failure(predicate, "FEATURE_MISSING")
        if kind is PredicateKind.FEATURE_ABSENT:
            return None if feature_id not in features else self._failure(predicate, "FEATURE_PRESENT")
        if kind is PredicateKind.FEATURE_COUNT:
            feature_type = parameters.get("feature_type")
            count = sum(
                1 for feature in after.features if feature_type is None or feature.feature_type == feature_type
            )
            if count == parameters["count"]:
                return None
            return self._failure(predicate, "FEATURE_COUNT_MISMATCH", actual=count)
        if kind is PredicateKind.PARAMETER_EQUALS:
            feature = features.get(str(feature_id))
            if feature is None:
                return self._failure(predicate, "FEATURE_MISSING")
            name = str(parameters["name"])
            if name not in feature.parameters:
                return self._failure(predicate, "PARAMETER_MISSING")
            actual = feature.parameters[name]
            expected = parameters["value"]
            tolerance = parameters.get("tolerance", 0.0)
            if self._equivalent(actual, expected, tolerance):
                return None
            return self._failure(predicate, "PARAMETER_MISMATCH", actual=actual)
        return self._failure(predicate, "UNSUPPORTED_REQUIREMENT")

    def _evaluate_invariant(
        self,
        predicate: DesignPredicate,
        before: DocumentSnapshot | None,
        after: DocumentSnapshot | None,
    ) -> VerificationIssue | None:
        if before is None or after is None:
            return self._failure(predicate, "INVARIANT_STATE_UNAVAILABLE")
        before_features = {feature.feature_id: feature for feature in before.features}
        after_features = {feature.feature_id: feature for feature in after.features}
        feature_id = str(predicate.parameters.get("feature_id", ""))
        if predicate.kind is PredicateKind.PRESERVE_FEATURE:
            if before_features.get(feature_id) == after_features.get(feature_id) and feature_id in before_features:
                return None
            return self._failure(predicate, "FEATURE_CHANGED")
        if predicate.kind is PredicateKind.PRESERVE_PARAMETER:
            name = str(predicate.parameters["name"])
            before_feature = before_features.get(feature_id)
            after_feature = after_features.get(feature_id)
            if (
                before_feature is not None
                and after_feature is not None
                and name in before_feature.parameters
                and before_feature.parameters.get(name) == after_feature.parameters.get(name)
            ):
                return None
            return self._failure(predicate, "PARAMETER_CHANGED")
        if predicate.kind is PredicateKind.MAX_FEATURE_DELTA:
            delta = abs(len(after.features) - len(before.features))
            if delta <= predicate.parameters["maximum"]:
                return None
            return self._failure(predicate, "FEATURE_DELTA_EXCEEDED", actual=delta)
        return self._failure(predicate, "UNSUPPORTED_INVARIANT")

    @staticmethod
    def _equivalent(actual: Any, expected: Any, tolerance: float) -> bool:
        if isinstance(actual, bool) or isinstance(expected, bool):
            return actual == expected
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            return abs(float(actual) - float(expected)) <= float(tolerance)
        return actual == expected

    @staticmethod
    def _failure(
        predicate: DesignPredicate, code: str, *, actual: Any | None = None
    ) -> VerificationIssue:
        suffix = f"; actual={actual!r}" if actual is not None else ""
        return VerificationIssue(
            predicate_id=predicate.predicate_id,
            code=code,
            message=f"{predicate.kind.value} was not satisfied{suffix}",
        )

    @staticmethod
    def _snapshot_hash(snapshot: DocumentSnapshot | None) -> str | None:
        return content_hash(snapshot.to_dict(include_hash=False)) if snapshot else None
