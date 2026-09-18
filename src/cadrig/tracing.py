"""Replay-oriented execution traces for the native CADRIG agent."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentPhase(str, Enum):
    OBSERVE = "observe"
    COMPILE = "compile_contract"
    PLAN = "plan_action_graph"
    PREFLIGHT = "preflight"
    EXECUTE = "execute"
    VERIFY = "verify"
    REPAIR = "repair"
    COMMIT = "commit"
    REFUSE = "refuse"
    ROLLBACK = "rollback"


@dataclass(frozen=True)
class TraceEvent:
    sequence: int
    phase: AgentPhase
    status: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "phase": self.phase.value,
            "status": self.status,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class AgentTrace:
    trace_id: str
    intent: str
    adapter_id: str
    document_id: str
    events: tuple[TraceEvent, ...]
    schema_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trace_id": self.trace_id,
            "intent": self.intent,
            "adapter_id": self.adapter_id,
            "document_id": self.document_id,
            "events": [event.to_dict() for event in self.events],
        }


@dataclass
class TraceBuilder:
    intent: str
    adapter_id: str
    document_id: str
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _events: list[TraceEvent] = field(default_factory=list)

    def record(self, phase: AgentPhase, status: str, payload: dict[str, Any] | None = None) -> None:
        self._events.append(
            TraceEvent(
                sequence=len(self._events),
                phase=phase,
                status=status,
                payload=dict(payload or {}),
            )
        )

    def finish(self) -> AgentTrace:
        return AgentTrace(
            trace_id=self.trace_id,
            intent=self.intent,
            adapter_id=self.adapter_id,
            document_id=self.document_id,
            events=tuple(self._events),
        )
