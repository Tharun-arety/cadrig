"""Deterministic summaries for CADGenBench experiment runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import read_json

REPORT_SCHEMA_VERSION = "1.0.0"
_FILES = ("manifest.json", "run_summary.json", "verification.json")


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _integer(value: object) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_inputs(run_dir: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    inputs: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for name in _FILES:
        path = run_dir / name
        payload = read_json(path)
        if payload is not None:
            inputs[name] = payload
        elif path.exists():
            warnings.append(f"invalid {name}")
        else:
            warnings.append(f"missing {name}")
    return inputs, warnings


def _fixtures(manifest: dict[str, Any]) -> list[str]:
    request = _mapping(manifest.get("request"))
    raw = request.get("fixtures")
    if not isinstance(raw, list):
        raw = _mapping(manifest.get("official_params")).get("fixtures")
    if not isinstance(raw, list):
        return []
    return sorted({item for item in raw if isinstance(item, str)})


def _task_type_scores(summary: dict[str, Any]) -> dict[str, int | float]:
    raw = _mapping(summary.get("score_by_task_type"))
    return {
        key: value
        for key, candidate in sorted(raw.items())
        if (value := _number(candidate)) is not None
    }


def _has_ground_truth_scores(run_dir: Path, fixtures: list[str]) -> bool:
    for fixture in fixtures:
        result = read_json(run_dir / fixture / "result.json") or {}
        if _mapping(result.get("gt_metrics")) and _number(result.get("cad_score")) is not None:
            return True
    return False


def _usage(run_dir: Path, fixtures: list[str]) -> dict[str, int | float | None]:
    traces = [read_json(run_dir / fixture / "trace.json") for fixture in fixtures]
    available = [trace for trace in traces if trace is not None]
    if not available:
        return {
            "trace_count": 0,
            "total_tokens": None,
            "total_duration_seconds": None,
            "turns": None,
        }
    return {
        "trace_count": len(available),
        "total_tokens": sum(_integer(trace.get("total_tokens")) or 0 for trace in available),
        "total_duration_seconds": round(
            sum(_number(trace.get("total_duration_seconds")) or 0 for trace in available), 3
        ),
        "turns": sum(len(trace.get("turns", [])) for trace in available),
    }


def summarize_run(run_dir: Path) -> dict[str, Any]:
    """Summarize a run directory, retaining useful data from partial runs."""

    resolved = run_dir.resolve()
    if not resolved.is_dir():
        raise ValueError(f"run directory not found: {resolved}")

    inputs, warnings = _load_inputs(resolved)
    manifest = inputs.get("manifest.json", {})
    summary = inputs.get("run_summary.json", {})
    verification = inputs.get("verification.json", {})
    cohort = read_json(resolved / "cohort.json") or {}
    cohort_config = _mapping(cohort.get("config"))
    if cohort:
        warnings = [warning for warning in warnings if warning != "missing run_summary.json"]
    request = _mapping(manifest.get("request"))
    fixtures = _fixtures(manifest)
    score_available = _has_ground_truth_scores(resolved, fixtures)
    reported_score = _number(summary.get("aggregate_score"))

    expected_count = _integer(verification.get("expected_count"))
    valid_count = _integer(verification.get("valid_count"))
    validity_rate = _number(summary.get("validity_rate"))
    if validity_rate is None and expected_count and valid_count is not None:
        validity_rate = valid_count / expected_count

    status = _text(cohort.get("status")) or _text(manifest.get("status"))
    if status is None:
        if verification.get("passed") is True:
            status = "verified"
        elif inputs:
            status = "partial"
        else:
            status = "empty"

    scope = _text(verification.get("scope"))
    if scope is None:
        scope = "all" if request.get("all") is True else "partial"

    return {
        "run_dir": str(resolved),
        "run_id": _text(manifest.get("run_id")) or resolved.name,
        "status": status,
        "model": _text(request.get("model")) or _text(cohort_config.get("model")),
        "backend": _text(request.get("backend")) or _text(cohort_config.get("backend")),
        "reasoning_effort": _text(request.get("reasoning_effort"))
        or _text(cohort_config.get("reasoning_effort")),
        "started_at": _text(manifest.get("started_at")) or _text(cohort.get("created_at")),
        "finished_at": _text(manifest.get("finished_at"))
        or (_text(cohort.get("updated_at")) if status == "completed" else None),
        "duration_seconds": _number(manifest.get("duration_seconds")),
        "scope": scope,
        "fixtures": fixtures,
        "metrics": {
            "score_available": score_available,
            "aggregate_score": reported_score if score_available else None,
            "reported_aggregate_score": reported_score,
            "validity_rate": validity_rate,
            "n_samples": _integer(summary.get("n_samples")) or expected_count,
            "n_valid": _integer(summary.get("n_valid"))
            if _integer(summary.get("n_valid")) is not None
            else valid_count,
            "n_invalid": _integer(summary.get("n_invalid"))
            if _integer(summary.get("n_invalid")) is not None
            else _integer(verification.get("invalid_count")),
            "n_missing": _integer(summary.get("n_missing"))
            if _integer(summary.get("n_missing")) is not None
            else _integer(verification.get("missing_count")),
            "score_by_task_type": _task_type_scores(summary),
        },
        "usage": _usage(resolved, fixtures),
        "verification": {
            "passed": verification.get("passed")
            if isinstance(verification.get("passed"), bool)
            else None,
            "complete": verification.get("complete")
            if isinstance(verification.get("complete"), bool)
            else None,
            "sanity_checked": verification.get("sanity_checked")
            if isinstance(verification.get("sanity_checked"), bool)
            else None,
            "expected_count": expected_count,
            "candidate_count": _integer(verification.get("candidate_count")),
            "valid_count": valid_count,
            "invalid_count": _integer(verification.get("invalid_count")),
            "missing_count": _integer(verification.get("missing_count")),
        },
        "artifacts": {
            **{name.removesuffix(".json"): name in inputs for name in _FILES},
            "cohort": bool(cohort),
        },
        "warnings": warnings,
    }


def report_runs(run_dirs: list[Path] | tuple[Path, ...]) -> dict[str, Any]:
    """Build a stable experiment report for one or more CADGenBench runs."""

    if not run_dirs:
        raise ValueError("at least one CADGenBench run directory is required")
    runs = [summarize_run(path) for path in run_dirs]
    runs.sort(key=lambda item: (item["run_id"], item["run_dir"]))

    ranked = [run for run in runs if run["metrics"]["score_available"]]
    ranked.sort(
        key=lambda item: (
            -item["metrics"]["aggregate_score"],
            -(item["metrics"]["validity_rate"] or 0),
            item["run_id"],
            item["run_dir"],
        )
    )
    ranking = [
        {
            "rank": index,
            "run_id": run["run_id"],
            "aggregate_score": run["metrics"]["aggregate_score"],
            "validity_rate": run["metrics"]["validity_rate"],
        }
        for index, run in enumerate(ranked, start=1)
    ]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_count": len(runs),
        "completed_run_count": sum(run["status"] == "completed" for run in runs),
        "scored_run_count": len(ranking),
        "best_run_id": ranking[0]["run_id"] if ranking else None,
        "ranking": ranking,
        "runs": runs,
    }
