"""Atomically compose disjoint verified candidates from multiple runs."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .common import candidate_for, sha256_file, validate_task_id, write_json_atomic
from .dataset import discover_dataset_tasks
from .sanity import VerificationReport, verify_run


@dataclass(frozen=True)
class CompositionResult:
    output_dir: Path
    task_count: int
    verification: VerificationReport


def compose_runs(
    sources: tuple[Path, ...],
    output_dir: Path,
    *,
    dataset_dir: Path | None = None,
    sanity_script: Path | None = None,
) -> CompositionResult:
    """Compose candidate-only run directories and fail on ambiguous provenance."""
    if not sources:
        raise ValueError("at least one source run is required")
    destination = output_dir.resolve()
    if destination.exists():
        raise ValueError(f"composition output already exists: {destination}")

    selected: dict[str, tuple[Path, str, list[str]]] = {}
    for raw_source in sources:
        source = raw_source.resolve()
        if not source.is_dir():
            raise ValueError(f"composition source not found: {source}")
        for task_dir in sorted(source.iterdir(), key=lambda path: path.name):
            if not task_dir.is_dir() or task_dir.name.startswith("."):
                continue
            try:
                task_id = validate_task_id(task_dir.name)
            except ValueError:
                continue
            candidate, _ = candidate_for(task_dir)
            if candidate is None:
                continue
            digest = sha256_file(candidate)
            prior = selected.get(task_id)
            if prior is not None:
                if prior[1] != digest:
                    raise ValueError(
                        f"conflicting candidates for task {task_id}: "
                        f"{prior[0]} and {candidate}"
                    )
                prior[2].append(str(candidate))
                continue
            selected[task_id] = (candidate, digest, [str(candidate)])
    if not selected:
        raise ValueError("composition sources contain no candidate files")

    staging = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        staging.mkdir(parents=True)
        provenance: dict[str, object] = {
            "schema_version": "1.0.0",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sources": [str(path.resolve()) for path in sources],
            "tasks": {},
        }
        task_provenance = provenance["tasks"]
        assert isinstance(task_provenance, dict)
        for task_id, (candidate, digest, candidate_sources) in sorted(selected.items()):
            task_dir = staging / task_id
            task_dir.mkdir()
            shutil.copy2(candidate, task_dir / "output.step")
            trace = candidate.parent / "trace.json"
            if trace.is_file():
                shutil.copy2(trace, task_dir / "trace.json")
            task_provenance[task_id] = {
                "sha256": digest,
                "selected_source": str(candidate),
                "equivalent_sources": candidate_sources,
            }

        fixtures = tuple(sorted(selected))
        dataset_tasks = discover_dataset_tasks(dataset_dir) if dataset_dir is not None else ()
        is_all = bool(dataset_tasks) and fixtures == tuple(sorted(dataset_tasks))
        write_json_atomic(staging / "composition.json", provenance)
        write_json_atomic(staging / "params.json", {"fixtures": list(fixtures)})
        write_json_atomic(
            staging / "manifest.json",
            {
                "schema_version": "1.0.0",
                "engine": "cadrig-composition",
                "status": "completed",
                "request": {"all": is_all, "fixtures": list(fixtures)},
            },
        )
        verification = verify_run(
            staging,
            explicit_tasks=fixtures,
            sanity_script=sanity_script,
            dataset_dir=dataset_dir,
            require_sanity=True,
        )
        if not verification.passed:
            raise RuntimeError(
                "composed candidates failed strict verification: "
                f"missing={verification.missing_count}, invalid={verification.invalid_count}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    verification = verify_run(
        destination,
        explicit_tasks=tuple(sorted(selected)),
        sanity_script=sanity_script,
        dataset_dir=dataset_dir,
        require_sanity=True,
    )
    return CompositionResult(destination, len(selected), verification)
