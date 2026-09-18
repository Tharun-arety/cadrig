"""The public boundary implemented by every CAD host."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cadrig.contracts import (
    ActionPlan,
    AdapterMetadata,
    Diagnostic,
    DocumentSnapshot,
    ExecutionReceipt,
)


@runtime_checkable
class KernelAdapter(Protocol):
    def metadata(self) -> AdapterMetadata:
        """Return truthful, machine-readable host capabilities."""

    def observe(self, document_id: str) -> DocumentSnapshot | None:
        """Observe one exact native document revision."""

    def validate(self, plan: ActionPlan) -> tuple[Diagnostic, ...]:
        """Validate a plan without mutating the CAD host."""

    def execute(self, plan: ActionPlan, *, dry_run: bool = False) -> ExecutionReceipt:
        """Apply all actions atomically, or refuse without mutation."""

    def rollback(self, receipt_id: str) -> ExecutionReceipt:
        """Rollback a committed transaction when it is still safe to do so."""
