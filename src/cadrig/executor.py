"""Host-independent execution boundary for the native CADRIG agent."""

from __future__ import annotations

from pathlib import Path

from cadrig.adapters.base import ArtifactCaptureAdapter
from cadrig.artifacts import ArtifactCapture, ArtifactCaptureDiagnostic
from cadrig.contracts import (
    ActionPlan,
    AdapterMetadata,
    Diagnostic,
    DocumentSnapshot,
    ExecutionReceipt,
)
from cadrig.registry import AdapterRegistry


class ExecutionEngine:
    def __init__(self, registry: AdapterRegistry) -> None:
        self._registry = registry

    def adapters(self) -> tuple[AdapterMetadata, ...]:
        return self._registry.capabilities()

    def observe(self, adapter_id: str, document_id: str) -> DocumentSnapshot | None:
        return self._registry.get(adapter_id).observe(document_id)

    def validate(self, adapter_id: str, plan: ActionPlan) -> tuple[Diagnostic, ...]:
        return self._registry.get(adapter_id).validate(plan)

    def execute(
        self, adapter_id: str, plan: ActionPlan, *, dry_run: bool = False
    ) -> ExecutionReceipt:
        return self._registry.get(adapter_id).execute(plan, dry_run=dry_run)

    def rollback(self, adapter_id: str, receipt_id: str) -> ExecutionReceipt:
        return self._registry.get(adapter_id).rollback(receipt_id)

    def capture_artifacts(
        self, adapter_id: str, document_id: str, destination: Path
    ) -> ArtifactCapture:
        adapter = self._registry.get(adapter_id)
        if not isinstance(adapter, ArtifactCaptureAdapter):
            return ArtifactCapture(
                diagnostics=(
                    ArtifactCaptureDiagnostic(
                        code="ARTIFACT_CAPTURE_UNSUPPORTED",
                        message=f"adapter {adapter_id} does not expose native artifact capture",
                    ),
                )
            )
        try:
            return adapter.capture_artifacts(document_id, destination)
        except Exception as exc:  # noqa: BLE001 - host exporters use provider-specific errors.
            return ArtifactCapture(
                diagnostics=(
                    ArtifactCaptureDiagnostic(
                        code="ARTIFACT_CAPTURE_FAILED",
                        message=str(exc) or type(exc).__name__,
                        severity="error",
                    ),
                )
            )
