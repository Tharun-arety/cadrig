"""Immutable, provenance-aware episode storage for native CADRIG runs."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from importlib import metadata as importlib_metadata
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from cadrig.tracing import AgentTrace

if TYPE_CHECKING:
    from cadrig.native_agent import AgentRun


EPISODE_SCHEMA_VERSION = "1.0.0"
_REDACTED = "[REDACTED]"
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
}


class EpisodeStoreError(RuntimeError):
    """Raised when an immutable episode cannot be persisted safely."""


class EpisodeSplit(str, Enum):
    TRAINING = "training"
    REGRESSION = "regression"
    EVALUATION = "evaluation"


@dataclass(frozen=True)
class EpisodeContext:
    """Caller-owned provenance and policy labels for one recorded run."""

    split: EpisodeSplit = EpisodeSplit.TRAINING
    task_id: str | None = None
    task_source: str = "interactive"
    dataset_revision: str | None = None
    task_license: str | None = None
    training_eligible: bool = False
    model: Mapping[str, Any] = field(default_factory=dict)
    environment: Mapping[str, Any] = field(default_factory=dict)
    labels: Mapping[str, Any] = field(default_factory=dict)
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.split is EpisodeSplit.EVALUATION and self.training_eligible:
            raise ValueError("evaluation episodes cannot be marked training eligible")
        for name in ("task_id", "dataset_revision", "task_license"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be non-empty when provided")
        if not self.task_source.strip():
            raise ValueError("task_source must not be empty")


@dataclass(frozen=True)
class EpisodeRecord:
    episode_id: str
    path: Path
    manifest_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "episode_id": self.episode_id,
            "path": str(self.path),
            "manifest_sha256": self.manifest_sha256,
        }


def default_episode_root() -> Path:
    """Resolve the developer repository when editable, with an explicit override."""

    configured = os.environ.get("CADRIG_EPISODE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    module_path = Path(__file__).resolve()
    for parent in module_path.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "cadrig").is_dir():
            return parent / "results" / "episodes"
    return Path.cwd().resolve() / "results" / "episodes"


def redact_secrets(value: Any) -> Any:
    """Remove known credential-bearing fields without erasing token metrics."""

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            redacted[str(key)] = (
                _REDACTED
                if normalized in _SECRET_KEYS or normalized.endswith("_api_key")
                else redact_secrets(item)
            )
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    return value


class EpisodeStore:
    """Write each episode once, with hashes for every replay-relevant sidecar."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root is not None else default_episode_root()

    def record_run(
        self,
        run: AgentRun,
        *,
        context: EpisodeContext | None = None,
        artifacts: Mapping[str, Path | str] | None = None,
    ) -> EpisodeRecord:
        context = context or EpisodeContext()
        receipt = run.rollback_receipt or run.receipt
        payloads: dict[str, Any] = {
            "contract.json": run.contract.to_dict(),
            "action_graph.json": run.plan.to_dict() if run.plan else None,
            "verification.json": run.verification.to_dict() if run.verification else None,
            "receipt.json": run.receipt.to_dict() if run.receipt else None,
            "rollback_receipt.json": (
                run.rollback_receipt.to_dict() if run.rollback_receipt else None
            ),
            "input_snapshot.json": receipt.before.to_dict() if receipt and receipt.before else None,
            "output_snapshot.json": receipt.after.to_dict() if receipt and receipt.after else None,
            "artifact_capture.json": (
                run.artifact_capture.to_dict() if run.artifact_capture else None
            ),
        }
        return self._record(
            trace=run.trace,
            status=run.status.value,
            accepted=run.accepted,
            attempts=run.attempts,
            context=context,
            payloads=payloads,
            artifacts=self._merge_artifacts(
                run.artifact_capture.files if run.artifact_capture else None,
                artifacts,
            ),
            error=None,
        )

    def record_failure(
        self,
        trace: AgentTrace,
        error: str,
        *,
        context: EpisodeContext | None = None,
    ) -> EpisodeRecord:
        return self._record(
            trace=trace,
            status="error",
            accepted=False,
            attempts=0,
            context=context or EpisodeContext(),
            payloads={
                "contract.json": None,
                "action_graph.json": None,
                "verification.json": None,
                "receipt.json": None,
                "rollback_receipt.json": None,
                "input_snapshot.json": None,
                "output_snapshot.json": None,
                "artifact_capture.json": None,
            },
            artifacts=None,
            error=error,
        )

    def _record(
        self,
        *,
        trace: AgentTrace,
        status: str,
        accepted: bool,
        attempts: int,
        context: EpisodeContext,
        payloads: Mapping[str, Any],
        artifacts: Mapping[str, Path | str] | None,
        error: str | None,
    ) -> EpisodeRecord:
        now = datetime.now(timezone.utc)
        episode_id = trace.trace_id
        day_root = self.root / context.split.value / now.date().isoformat()
        destination = day_root / episode_id
        if destination.exists():
            raise EpisodeStoreError(f"episode already exists and is immutable: {destination}")
        day_root.mkdir(parents=True, exist_ok=True)
        staging = day_root / f".{episode_id}.tmp-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            for name, payload in payloads.items():
                self._write_json(staging / name, redact_secrets(payload))
            self._write_trace(staging / "trace.jsonl", trace)
            if artifacts:
                self._copy_artifacts(staging / "artifacts", artifacts)

            files = self._file_inventory(staging)
            adapter = self._adapter_from_trace(trace)
            manifest = redact_secrets(
                {
                    "schema_version": EPISODE_SCHEMA_VERSION,
                    "episode_id": episode_id,
                    "created_at": now.isoformat(),
                    "task": {
                        "task_id": context.task_id or episode_id,
                        "instruction": trace.intent,
                        "source": context.task_source,
                        "dataset_revision": context.dataset_revision,
                        "license": context.task_license,
                    },
                    "data_policy": {
                        "split": context.split.value,
                        "training_eligible": context.training_eligible,
                        "evaluation_only": context.split is EpisodeSplit.EVALUATION,
                    },
                    "system": {
                        "cadrig_version": self._package_version(),
                        "adapter": adapter,
                        "model": dict(context.model),
                        "environment": {
                            "python": platform.python_version(),
                            "platform": platform.platform(),
                            **dict(context.environment),
                        },
                    },
                    "run": {
                        "trace_id": trace.trace_id,
                        "status": status,
                        "accepted": accepted,
                        "attempts": attempts,
                        "error": error,
                    },
                    "labels": dict(context.labels),
                    "metrics": dict(context.metrics),
                    "files": files,
                }
            )
            manifest_path = staging / "episode.json"
            self._write_json(manifest_path, manifest)
            manifest_hash = self._sha256_file(manifest_path)
            os.replace(staging, destination)
            return EpisodeRecord(
                episode_id=episode_id,
                path=destination,
                manifest_sha256=manifest_hash,
            )
        except Exception as exc:
            shutil.rmtree(staging, ignore_errors=True)
            if isinstance(exc, EpisodeStoreError):
                raise
            raise EpisodeStoreError(f"could not persist episode {episode_id}: {exc}") from exc

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_trace(path: Path, trace: AgentTrace) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            for event in trace.events:
                stream.write(
                    json.dumps(
                        redact_secrets(event.to_dict()),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    )
                )
                stream.write("\n")

    @staticmethod
    def _copy_artifacts(root: Path, artifacts: Mapping[str, Path | str]) -> None:
        for logical_name, source_value in artifacts.items():
            logical = PurePosixPath(logical_name.replace("\\", "/"))
            if logical.is_absolute() or not logical.parts or ".." in logical.parts:
                raise EpisodeStoreError(f"unsafe artifact name: {logical_name}")
            source = Path(source_value).expanduser().resolve()
            if not source.is_file():
                raise EpisodeStoreError(f"artifact is not a file: {source}")
            destination = root.joinpath(*logical.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    @staticmethod
    def _merge_artifacts(
        captured: Mapping[str, Path | str] | None,
        supplied: Mapping[str, Path | str] | None,
    ) -> dict[str, Path | str] | None:
        if not captured and not supplied:
            return None
        overlap = set(captured or {}) & set(supplied or {})
        if overlap:
            raise EpisodeStoreError(f"duplicate artifact names: {sorted(overlap)}")
        return {**dict(captured or {}), **dict(supplied or {})}

    @classmethod
    def _file_inventory(cls, root: Path) -> list[dict[str, Any]]:
        result = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            result.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": cls._sha256_file(path),
                }
            )
        return result

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _adapter_from_trace(trace: AgentTrace) -> dict[str, Any]:
        for event in trace.events:
            adapter = event.payload.get("adapter")
            if isinstance(adapter, Mapping):
                return dict(adapter)
        return {"adapter_id": trace.adapter_id}

    @staticmethod
    def _package_version() -> str:
        try:
            return importlib_metadata.version("cadrig")
        except importlib_metadata.PackageNotFoundError:
            package = sys.modules.get("cadrig")
            return str(getattr(package, "__version__", "unknown"))
