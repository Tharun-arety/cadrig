"""Verifier-governed, contract-driven CADRIG agent orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from cadrig.action_graph import ActionGraphContext, ActionGraphPlanner, ActionPlanningError
from cadrig.contract_compiler import ContractCompiler, ContractContext
from cadrig.contracts import ActionPlan, ExecutionReceipt
from cadrig.design_contracts import DesignContract
from cadrig.executor import ExecutionEngine
from cadrig.tracing import AgentPhase, AgentTrace, TraceBuilder
from cadrig.verification import ContractVerifier, VerificationReport


class NativeAgentError(RuntimeError):
    """Raised when a run cannot reach the planning or execution boundary."""


class AgentRunStatus(str, Enum):
    PREVIEWED = "previewed"
    COMMITTED = "committed"
    REFUSED = "refused"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class AgentRun:
    status: AgentRunStatus
    contract: DesignContract
    plan: ActionPlan | None
    receipt: ExecutionReceipt | None
    verification: VerificationReport | None
    trace: AgentTrace
    attempts: int
    rollback_receipt: ExecutionReceipt | None = None

    @property
    def accepted(self) -> bool:
        return self.status in {AgentRunStatus.PREVIEWED, AgentRunStatus.COMMITTED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "accepted": self.accepted,
            "attempts": self.attempts,
            "contract": self.contract.to_dict(),
            "plan": self.plan.to_dict() if self.plan else None,
            "receipt": self.receipt.to_dict() if self.receipt else None,
            "verification": self.verification.to_dict() if self.verification else None,
            "rollback_receipt": self.rollback_receipt.to_dict() if self.rollback_receipt else None,
            "trace": self.trace.to_dict(),
        }


class NativeCADAgent:
    """Own the run state machine while treating every model proposal as untrusted."""

    def __init__(
        self,
        *,
        executor: ExecutionEngine,
        compiler: ContractCompiler,
        planner: ActionGraphPlanner,
        verifier: ContractVerifier | None = None,
        max_repairs: int = 1,
    ) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs must be non-negative")
        self._executor = executor
        self._compiler = compiler
        self._planner = planner
        self._verifier = verifier or ContractVerifier()
        self._max_repairs = max_repairs

    def run(
        self,
        *,
        intent: str,
        adapter_id: str,
        document_id: str,
        apply: bool = False,
    ) -> AgentRun:
        trace = TraceBuilder(intent=intent, adapter_id=adapter_id, document_id=document_id)
        metadata = next(
            (item for item in self._executor.adapters() if item.adapter_id == adapter_id), None
        )
        if metadata is None:
            raise NativeAgentError(f"adapter is not registered: {adapter_id}")
        snapshot = self._executor.observe(adapter_id, document_id)
        trace.record(
            AgentPhase.OBSERVE,
            "ok",
            {"snapshot": snapshot.to_dict() if snapshot else None, "adapter": metadata.to_dict()},
        )
        try:
            contract = self._compiler.compile(
                ContractContext(
                    intent=intent,
                    document_id=document_id,
                    snapshot=snapshot,
                    adapter=metadata,
                )
            )
        except Exception as exc:
            trace.record(AgentPhase.COMPILE, "failed", {"error": str(exc)})
            raise NativeAgentError(f"contract compilation failed: {exc}") from exc
        trace.record(AgentPhase.COMPILE, "ok", {"contract": contract.to_dict()})

        feedback: tuple[dict[str, Any], ...] = ()
        last_plan: ActionPlan | None = None
        last_receipt: ExecutionReceipt | None = None
        last_report: VerificationReport | None = None
        attempts = 0
        for attempt_index in range(self._max_repairs + 1):
            attempts = attempt_index + 1
            try:
                last_plan = self._planner.propose(
                    ActionGraphContext(
                        contract=contract,
                        snapshot=snapshot,
                        adapter=metadata,
                        repair_feedback=feedback,
                    )
                )
            except ActionPlanningError as exc:
                trace.record(AgentPhase.PLAN, "failed", {"attempt": attempts, "error": str(exc)})
                feedback = ({"code": "ACTION_GRAPH_INVALID", "message": str(exc)},)
                if attempt_index < self._max_repairs:
                    trace.record(AgentPhase.REPAIR, "requested", {"feedback": list(feedback)})
                    continue
                break
            trace.record(
                AgentPhase.PLAN,
                "ok",
                {"attempt": attempts, "action_graph": last_plan.to_dict()},
            )
            diagnostics = self._executor.validate(adapter_id, last_plan)
            if diagnostics:
                feedback = tuple(diagnostic.to_dict() for diagnostic in diagnostics)
                trace.record(
                    AgentPhase.PREFLIGHT,
                    "failed",
                    {"attempt": attempts, "diagnostics": list(feedback)},
                )
                if attempt_index < self._max_repairs:
                    trace.record(AgentPhase.REPAIR, "requested", {"feedback": list(feedback)})
                    continue
                break
            trace.record(AgentPhase.PREFLIGHT, "ok", {"attempt": attempts})
            last_receipt = self._executor.execute(adapter_id, last_plan, dry_run=True)
            trace.record(
                AgentPhase.EXECUTE,
                "preview",
                {"attempt": attempts, "receipt": last_receipt.to_dict()},
            )
            last_report = self._verifier.verify(contract, last_receipt)
            trace.record(
                AgentPhase.VERIFY,
                "accepted" if last_report.accepted else "rejected",
                {"attempt": attempts, "report": last_report.to_dict()},
            )
            if not last_report.accepted:
                feedback = tuple(issue.to_dict() for issue in last_report.issues)
                if attempt_index < self._max_repairs:
                    trace.record(AgentPhase.REPAIR, "requested", {"feedback": list(feedback)})
                    continue
                break
            if not apply:
                trace.record(AgentPhase.COMMIT, "preview_only", {"plan_id": last_plan.plan_id})
                return AgentRun(
                    status=AgentRunStatus.PREVIEWED,
                    contract=contract,
                    plan=last_plan,
                    receipt=last_receipt,
                    verification=last_report,
                    trace=trace.finish(),
                    attempts=attempts,
                )

            applied_receipt = self._executor.execute(adapter_id, last_plan, dry_run=False)
            trace.record(
                AgentPhase.EXECUTE,
                "applied" if applied_receipt.accepted else "refused",
                {"attempt": attempts, "receipt": applied_receipt.to_dict()},
            )
            applied_report = self._verifier.verify(contract, applied_receipt)
            trace.record(
                AgentPhase.VERIFY,
                "accepted" if applied_report.accepted else "rejected",
                {"attempt": attempts, "report": applied_report.to_dict()},
            )
            if applied_report.accepted:
                trace.record(AgentPhase.COMMIT, "committed", {"receipt_id": applied_receipt.receipt_id})
                return AgentRun(
                    status=AgentRunStatus.COMMITTED,
                    contract=contract,
                    plan=last_plan,
                    receipt=applied_receipt,
                    verification=applied_report,
                    trace=trace.finish(),
                    attempts=attempts,
                )
            rollback = None
            if applied_receipt.accepted:
                rollback = self._executor.rollback(adapter_id, applied_receipt.receipt_id)
                trace.record(AgentPhase.ROLLBACK, rollback.status.value, rollback.to_dict())
            trace.record(AgentPhase.REFUSE, "post_commit_verification_failed")
            return AgentRun(
                status=AgentRunStatus.ROLLED_BACK if rollback and rollback.accepted else AgentRunStatus.REFUSED,
                contract=contract,
                plan=last_plan,
                receipt=applied_receipt,
                verification=applied_report,
                rollback_receipt=rollback,
                trace=trace.finish(),
                attempts=attempts,
            )

        trace.record(
            AgentPhase.REFUSE,
            "repair_budget_exhausted",
            {"attempts": attempts, "feedback": list(feedback)},
        )
        return AgentRun(
            status=AgentRunStatus.REFUSED,
            contract=contract,
            plan=last_plan,
            receipt=last_receipt,
            verification=last_report,
            trace=trace.finish(),
            attempts=attempts,
        )
