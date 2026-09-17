"""End-to-end copilot orchestration across a user model and CAD adapter."""

from __future__ import annotations

from dataclasses import dataclass

from cadcopilot.contracts import ActionPlan, ExecutionReceipt
from cadcopilot.executor import CopilotExecutor
from cadcopilot.planner import CopilotPlanner, PlanningContext


@dataclass(frozen=True)
class CopilotResult:
    plan: ActionPlan
    receipt: ExecutionReceipt

    def to_dict(self) -> dict[str, object]:
        return {"plan": self.plan.to_dict(), "receipt": self.receipt.to_dict()}


class CopilotAgent:
    """Observe, plan and execute without giving the model direct kernel access."""

    def __init__(self, executor: CopilotExecutor, planner: CopilotPlanner) -> None:
        self._executor = executor
        self._planner = planner

    def run(
        self,
        *,
        intent: str,
        adapter_id: str,
        document_id: str,
        apply: bool = False,
    ) -> CopilotResult:
        adapter = next(
            (item for item in self._executor.adapters() if item.adapter_id == adapter_id),
            None,
        )
        if adapter is None:
            raise KeyError(f"adapter is not registered: {adapter_id}")
        snapshot = self._executor.observe(adapter_id, document_id)
        plan = self._planner.propose(
            PlanningContext(
                intent=intent,
                document_id=document_id,
                snapshot=snapshot,
                adapter=adapter,
            )
        )
        receipt = self._executor.execute(adapter_id, plan, dry_run=not apply)
        return CopilotResult(plan=plan, receipt=receipt)
