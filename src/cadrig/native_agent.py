"""Verifier-governed, contract-driven CADRIG agent orchestration."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

from cadrig.action_graph import ActionGraphContext, ActionGraphPlanner, ActionPlanningError
from cadrig.artifacts import ArtifactCapture, CapturedArtifact
from cadrig.contract_compiler import ContractCompiler, ContractContext
from cadrig.contracts import ActionPlan, ExecutionReceipt
from cadrig.design_contracts import DesignContract
from cadrig.episodes import EpisodeContext, EpisodeRecord, EpisodeStore, EpisodeStoreError
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
    artifact_capture: ArtifactCapture | None = None
    episode: EpisodeRecord | None = None
    episode_error: str | None = None

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
            "artifact_capture": (
                self.artifact_capture.to_dict() if self.artifact_capture else None
            ),
            "episode": self.episode.to_dict() if self.episode else None,
            "episode_error": self.episode_error,
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
        episode_store: EpisodeStore | None = None,
        episode_context: EpisodeContext | None = None,
    ) -> None:
        if max_repairs < 0:
            raise ValueError("max_repairs must be non-negative")
        self._executor = executor
        self._compiler = compiler
        self._planner = planner
        self._verifier = verifier or ContractVerifier()
        self._max_repairs = max_repairs
        self._episode_store = episode_store
        self._episode_context = episode_context or EpisodeContext()
        self.last_episode: EpisodeRecord | None = None
        self.last_episode_error: str | None = None

    def _finish(self, run: AgentRun) -> AgentRun:
        """Persist evidence without changing the already-decided CAD outcome."""

        self.last_episode = None
        self.last_episode_error = None
        if self._episode_store is None:
            return run
        with TemporaryDirectory(prefix="cadrig-artifacts-") as temporary:
            if run.status is AgentRunStatus.COMMITTED:
                capture = self._executor.capture_artifacts(
                    run.trace.adapter_id,
                    run.trace.document_id,
                    Path(temporary),
                )
                run = replace(run, artifact_capture=capture)
            try:
                self.last_episode = self._episode_store.record_run(
                    run,
                    context=self._episode_context,
                )
            except EpisodeStoreError as exc:
                self.last_episode_error = str(exc)
                return replace(run, episode_error=self.last_episode_error)
        stored_capture = None
        if run.artifact_capture is not None:
            assert self.last_episode is not None
            stored_capture = ArtifactCapture(
                artifacts=tuple(
                    CapturedArtifact(
                        artifact.logical_name,
                        self.last_episode.path
                        / "artifacts"
                        / Path(*PurePosixPath(artifact.logical_name).parts),
                        artifact.media_type,
                        artifact.role,
                    )
                    for artifact in run.artifact_capture.artifacts
                ),
                diagnostics=run.artifact_capture.diagnostics,
            )
        return replace(run, episode=self.last_episode, artifact_capture=stored_capture)

    def _record_failure(self, trace: AgentTrace, error: str) -> None:
        if self._episode_store is None:
            return
        try:
            self.last_episode = self._episode_store.record_failure(
                trace,
                error,
                context=self._episode_context,
            )
        except EpisodeStoreError as exc:
            self.last_episode_error = str(exc)

    def run(
        self,
        *,
        intent: str,
        adapter_id: str,
        document_id: str,
        apply: bool = False,
    ) -> AgentRun:
        self.last_episode = None
        self.last_episode_error = None
        trace = TraceBuilder(intent=intent, adapter_id=adapter_id, document_id=document_id)
        metadata = next(
            (item for item in self._executor.adapters() if item.adapter_id == adapter_id), None
        )
        if metadata is None:
            error = f"adapter is not registered: {adapter_id}"
            trace.record(AgentPhase.OBSERVE, "failed", {"error": error})
            self._record_failure(trace.finish(), error)
            raise NativeAgentError(error)
        try:
            snapshot = self._executor.observe(adapter_id, document_id)
        except Exception as exc:
            trace.record(AgentPhase.OBSERVE, "failed", {"error": str(exc)})
            self._record_failure(trace.finish(), str(exc))
            raise NativeAgentError(f"document observation failed: {exc}") from exc
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
            self._record_failure(trace.finish(), str(exc))
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
                return self._finish(AgentRun(
                    status=AgentRunStatus.PREVIEWED,
                    contract=contract,
                    plan=last_plan,
                    receipt=last_receipt,
                    verification=last_report,
                    trace=trace.finish(),
                    attempts=attempts,
                ))

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
                return self._finish(AgentRun(
                    status=AgentRunStatus.COMMITTED,
                    contract=contract,
                    plan=last_plan,
                    receipt=applied_receipt,
                    verification=applied_report,
                    trace=trace.finish(),
                    attempts=attempts,
                ))
            rollback = None
            if applied_receipt.accepted:
                rollback = self._executor.rollback(adapter_id, applied_receipt.receipt_id)
                trace.record(AgentPhase.ROLLBACK, rollback.status.value, rollback.to_dict())
            trace.record(AgentPhase.REFUSE, "post_commit_verification_failed")
            return self._finish(AgentRun(
                status=AgentRunStatus.ROLLED_BACK if rollback and rollback.accepted else AgentRunStatus.REFUSED,
                contract=contract,
                plan=last_plan,
                receipt=applied_receipt,
                verification=applied_report,
                rollback_receipt=rollback,
                trace=trace.finish(),
                attempts=attempts,
            ))

        trace.record(
            AgentPhase.REFUSE,
            "repair_budget_exhausted",
            {"attempts": attempts, "feedback": list(feedback)},
        )
        return self._finish(AgentRun(
            status=AgentRunStatus.REFUSED,
            contract=contract,
            plan=last_plan,
            receipt=last_receipt,
            verification=last_report,
            trace=trace.finish(),
            attempts=attempts,
        ))
