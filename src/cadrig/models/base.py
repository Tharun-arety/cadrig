"""Provider-neutral boundary for user-supplied language models."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class ModelError(RuntimeError):
    """Raised when a model provider cannot return a usable response."""


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@runtime_checkable
class ModelClient(Protocol):
    """Minimal interface implemented by any bring-your-own-model connector."""

    def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        response_schema: Mapping[str, Any] | None = None,
    ) -> str:
        """Return the assistant's text response."""
