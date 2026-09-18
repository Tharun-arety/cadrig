"""Host-independent execution boundary for the native CADRIG agent."""

from __future__ import annotations

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
