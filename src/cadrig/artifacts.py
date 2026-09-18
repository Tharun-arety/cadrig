"""Backend-neutral descriptions of native CAD episode artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArtifactCaptureDiagnostic:
    code: str
    message: str
    severity: str = "warning"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": self.severity}


@dataclass(frozen=True)
class CapturedArtifact:
    logical_name: str
    path: Path
    media_type: str
    role: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_name": self.logical_name,
            "media_type": self.media_type,
            "role": self.role,
        }


@dataclass(frozen=True)
class ArtifactCapture:
    artifacts: tuple[CapturedArtifact, ...] = ()
    diagnostics: tuple[ArtifactCaptureDiagnostic, ...] = ()

    @property
    def files(self) -> dict[str, Path]:
        return {artifact.logical_name: artifact.path for artifact in self.artifacts}

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "diagnostics": [diagnostic.to_dict() for diagnostic in self.diagnostics],
            "complete": not any(item.severity == "error" for item in self.diagnostics),
        }
