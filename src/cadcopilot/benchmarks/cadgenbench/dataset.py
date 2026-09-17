"""CADGenBench public-input discovery without importing the benchmark package."""

from __future__ import annotations

import os
from pathlib import Path

from .common import read_json, task_directories, validate_task_id


def discover_dataset_tasks(dataset_dir: Path) -> tuple[str, ...]:
    """Return task ids under a dataset checkout containing description.yaml."""
    roots = [dataset_dir, dataset_dir / "inputs", dataset_dir / "data" / "inputs"]
    for root in roots:
        if not root.is_dir():
            continue
        found = tuple(
            sorted(
                child.name
                for child in root.iterdir()
                if child.is_dir() and (child / "description.yaml").is_file()
            )
        )
        if found:
            return found
    return ()


def expected_tasks(
    run_dir: Path,
    *,
    dataset_dir: Path | None = None,
    explicit: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Resolve the authoritative task set for a run in decreasing priority."""
    if explicit:
        return tuple(sorted(validate_task_id(task_id) for task_id in explicit))

    params = read_json(run_dir / "params.json") or {}
    fixtures = params.get("fixtures")
    if isinstance(fixtures, list) and fixtures and all(isinstance(item, str) for item in fixtures):
        return tuple(sorted(validate_task_id(item) for item in fixtures))

    manifest = read_json(run_dir / "manifest.json") or {}
    request = manifest.get("request")
    requested = request.get("fixtures") if isinstance(request, dict) else None
    if isinstance(requested, list) and requested and all(isinstance(item, str) for item in requested):
        return tuple(sorted(validate_task_id(item) for item in requested))

    if dataset_dir is not None:
        discovered = discover_dataset_tasks(dataset_dir)
        if discovered:
            return discovered

    return tuple(path.name for path in task_directories(run_dir))


def find_sanity_script(dataset_dir: Path | None) -> Path | None:
    candidates: list[Path] = []
    if dataset_dir is not None:
        candidates.extend(
            (
                dataset_dir / "sanity_check_submission.py",
                dataset_dir.parent / "sanity_check_submission.py",
                dataset_dir / "inputs" / "sanity_check_submission.py",
            )
        )

    cache_roots: list[Path] = []
    hub_cache = os.environ.get("HF_HUB_CACHE")
    hf_home = os.environ.get("HF_HOME")
    if hub_cache:
        cache_roots.append(Path(hub_cache))
    if hf_home:
        cache_roots.append(Path(hf_home) / "hub")
    cache_roots.append(Path.home() / ".cache" / "huggingface" / "hub")

    dataset_cache_name = "datasets--HuggingAI4Engineering--cadgenbench-data"
    for root in dict.fromkeys(cache_roots):
        snapshots = root / dataset_cache_name / "snapshots"
        if snapshots.is_dir():
            candidates.extend(
                sorted(
                    snapshots.glob("*/sanity_check_submission.py"),
                    key=lambda path: path.stat().st_mtime_ns,
                    reverse=True,
                )
            )
    return next((path for path in candidates if path.is_file()), None)
