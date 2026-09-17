"""Operational evaluation for CADRIG CAD-agent runs.

This module deliberately reports a metric vector instead of collapsing unlike
properties (validity, observability, recovery, latency, and cost) into a single
score. Missing evidence remains visible rather than being interpreted as zero.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import read_json, task_directories

HARNESS_EVALUATION_SCHEMA_VERSION = "1.0.0"


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _nonnegative_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _distribution(values: list[int | float]) -> dict[str, int | float | None]:
    if not values:
        return {"count": 0, "min": None, "mean": None, "median": None, "p95": None, "max": None}
    ordered = sorted(float(value) for value in values)
    rank = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "count": len(ordered),
        "min": round(ordered[0], 6),
        "mean": round(statistics.fmean(ordered), 6),
        "median": round(statistics.median(ordered), 6),
        "p95": round(ordered[rank], 6),
        "max": round(ordered[-1], 6),
    }


def _elapsed_seconds(started: object, finished: object) -> float | None:
    if not isinstance(started, str) or not isinstance(finished, str):
        return None
    try:
        value = (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
    except ValueError:
        return None
    return round(value, 6) if value >= 0 else None


def _trace_metrics(trace: dict[str, Any]) -> dict[str, Any]:
    turns = [_mapping(item) for item in _list(trace.get("turns"))]
    executions = [
        _mapping(execution)
        for turn in turns
        for execution in _list(turn.get("executions"))
        if isinstance(execution, dict)
    ]
    successes = [execution.get("success") is True for execution in executions]
    first_failure = next((index for index, success in enumerate(successes) if not success), None)
    repair_opportunity = first_failure is not None
    repaired = bool(
        first_failure is not None and any(successes[index] for index in range(first_failure + 1, len(successes)))
    )
    prompt = sum(_nonnegative_int(turn.get("prompt_tokens")) for turn in turns)
    completion = sum(_nonnegative_int(turn.get("completion_tokens")) for turn in turns)
    reasoning_values = [
        _nonnegative_int(turn.get("reasoning_tokens"))
        for turn in turns
        if _number(turn.get("reasoning_tokens")) is not None
    ]
    reported_total = _nonnegative_int(trace.get("total_tokens"))
    return {
        "agent_completed": trace.get("completed") if isinstance(trace.get("completed"), bool) else None,
        "stopped_reason": trace.get("stopped_reason") if isinstance(trace.get("stopped_reason"), str) else None,
        "duration_seconds": _number(trace.get("total_duration_seconds")),
        "turn_count": len(turns),
        "execution_count": len(executions),
        "successful_execution_count": sum(successes),
        "repair_opportunity": repair_opportunity,
        "repaired_after_execution_failure": repaired,
        "recovered_unterminated_fence_count": sum(
            execution.get("recovered_unterminated_fence") is True for execution in executions
        ),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens": sum(reasoning_values) if reasoning_values else None,
        "total_tokens": max(reported_total, prompt + completion),
    }


def _cohort_records(source: Path, state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    config = _mapping(state.get("config"))
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    tasks = _mapping(state.get("tasks"))
    for task_id, raw_task in sorted(tasks.items()):
        task = _mapping(raw_task)
        attempts = [_mapping(item) for item in _list(task.get("attempts"))]
        if not attempts:
            records.append(
                {
                    "source": str(source),
                    "task_id": task_id,
                    "attempt_id": None,
                    "status": task.get("status"),
                    "model": config.get("model"),
                    "backend": config.get("backend"),
                    "reasoning_effort": config.get("reasoning_effort"),
                    "trace_found": False,
                    "verification_found": False,
                    "valid_candidate": False,
                    "candidate_hash_found": bool(task.get("candidate_sha256")),
                    "usage": {},
                    "trace": {},
                    "cost_usd": None,
                    "wall_duration_seconds": None,
                    "error": None,
                }
            )
            continue
        for attempt in attempts:
            run_dir_value = attempt.get("run_dir")
            trace: dict[str, Any] = {}
            if isinstance(run_dir_value, str):
                portable_run_dir = Path(run_dir_value.replace("\\", "/"))
                trace = read_json(source / portable_run_dir / task_id / "trace.json") or {}
            if not trace and attempt.get("status") == "completed":
                trace = read_json(source / task_id / "trace.json") or {}
            verification = _mapping(attempt.get("verification"))
            usage = _mapping(attempt.get("usage"))
            records.append(
                {
                    "source": str(source),
                    "task_id": task_id,
                    "attempt_id": attempt.get("attempt_id"),
                    "status": attempt.get("status"),
                    "model": config.get("model"),
                    "backend": config.get("backend"),
                    "reasoning_effort": config.get("reasoning_effort"),
                    "trace_found": bool(trace) or usage.get("trace_found") is True,
                    "verification_found": bool(verification),
                    "valid_candidate": verification.get("passed") is True,
                    "candidate_hash_found": bool(attempt.get("candidate_sha256")),
                    "usage": usage,
                    "trace": _trace_metrics(trace) if trace else {},
                    "cost_usd": _number(attempt.get("cost_usd")),
                    "wall_duration_seconds": _elapsed_seconds(
                        attempt.get("started_at"), attempt.get("finished_at")
                    ),
                    "error": attempt.get("error") if isinstance(attempt.get("error"), str) else None,
                }
            )
    if not tasks:
        warnings.append("cohort contains no tasks")
    return records, config, warnings


def _run_records(source: Path) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    manifest = read_json(source / "manifest.json") or {}
    request = _mapping(manifest.get("request"))
    verification = read_json(source / "verification.json") or {}
    verified_tasks = {
        item.get("task_id"): item
        for item in _list(verification.get("tasks"))
        if isinstance(item, dict) and isinstance(item.get("task_id"), str)
    }
    fixtures = request.get("fixtures")
    if not isinstance(fixtures, list):
        fixtures = _mapping(manifest.get("official_params")).get("fixtures")
    task_ids = sorted(item for item in fixtures or [] if isinstance(item, str))
    if not task_ids:
        task_ids = [path.name for path in task_directories(source)]
    records: list[dict[str, Any]] = []
    for task_id in task_ids:
        trace_payload = read_json(source / task_id / "trace.json") or {}
        result = read_json(source / task_id / "result.json") or {}
        verified = _mapping(verified_tasks.get(task_id))
        status = verified.get("status")
        valid = status == "valid" or result.get("valid") is True
        trace = _trace_metrics(trace_payload) if trace_payload else {}
        records.append(
            {
                "source": str(source),
                "task_id": task_id,
                "attempt_id": "official-run",
                "status": "completed" if valid else (status or manifest.get("status")),
                "model": request.get("model"),
                "backend": request.get("backend"),
                "reasoning_effort": request.get("reasoning_effort"),
                "trace_found": bool(trace_payload),
                "verification_found": bool(verified) or bool(verification),
                "valid_candidate": valid,
                "candidate_hash_found": bool(verified.get("sha256") or result.get("candidate_sha256")),
                "usage": {
                    "prompt_tokens": trace.get("prompt_tokens", 0),
                    "completion_tokens": trace.get("completion_tokens", 0),
                    "unclassified_tokens": max(
                        _nonnegative_int(trace.get("total_tokens"))
                        - _nonnegative_int(trace.get("prompt_tokens"))
                        - _nonnegative_int(trace.get("completion_tokens")),
                        0,
                    ),
                    "total_tokens": trace.get("total_tokens", 0),
                },
                "trace": trace,
                "cost_usd": None,
                "wall_duration_seconds": None,
                "error": verified.get("message") if status not in (None, "valid") else None,
            }
        )
    warnings = [] if task_ids else ["run contains no discoverable tasks"]
    return records, request, warnings


def _failure_taxonomy(records: list[dict[str, Any]]) -> dict[str, int]:
    failures: Counter[str] = Counter()
    for record in records:
        status = record.get("status")
        if status not in (None, "completed", "valid", "pending", "ready"):
            failures[str(status)] += 1
        if not record.get("trace_found"):
            failures["missing_trace"] += 1
        if record.get("verification_found") and not record.get("valid_candidate"):
            failures["validation_failed"] += 1
    return dict(sorted(failures.items()))


def evaluate_harness(run_dirs: list[Path] | tuple[Path, ...]) -> dict[str, Any]:
    """Evaluate operational harness evidence from ordinary runs or cohorts."""

    if not run_dirs:
        raise ValueError("at least one CADRIG run or cohort directory is required")
    records: list[dict[str, Any]] = []
    configurations: list[dict[str, Any]] = []
    warnings: list[str] = []
    sources: list[dict[str, str]] = []
    for raw_source in run_dirs:
        source = raw_source.resolve()
        if not source.is_dir():
            raise ValueError(f"run or cohort directory not found: {source}")
        cohort = read_json(source / "cohort.json")
        if cohort is not None:
            source_records, config, source_warnings = _cohort_records(source, cohort)
            source_type = "cohort"
        else:
            source_records, config, source_warnings = _run_records(source)
            source_type = "run"
        records.extend(source_records)
        configurations.append(
            {
                "source": str(source),
                "source_type": source_type,
                "model": config.get("model"),
                "backend": config.get("backend"),
                "reasoning_effort": config.get("reasoning_effort"),
            }
        )
        warnings.extend(f"{source.name}: {warning}" for warning in source_warnings)
        sources.append({"path": str(source), "type": source_type})

    attempts = [record for record in records if record.get("attempt_id") is not None]
    task_keys = {(record["source"], record["task_id"]) for record in records}
    attempts_by_task = Counter((record["source"], record["task_id"]) for record in attempts)
    traces = [record["trace"] for record in attempts if record.get("trace")]
    usages = [_mapping(record.get("usage")) for record in attempts]
    valid_task_keys = {
        (record["source"], record["task_id"])
        for record in records
        if record.get("valid_candidate")
    }
    models = sorted({str(record["model"]) for record in records if record.get("model")})
    backends = sorted({str(record["backend"]) for record in records if record.get("backend")})
    trace_count = sum(record.get("trace_found") is True for record in attempts)
    verification_count = sum(record.get("verification_found") is True for record in attempts)
    hash_count = sum(record.get("candidate_hash_found") is True for record in attempts)
    execution_count = sum(_nonnegative_int(trace.get("execution_count")) for trace in traces)
    successful_execution_count = sum(
        _nonnegative_int(trace.get("successful_execution_count")) for trace in traces
    )
    repair_opportunities = sum(trace.get("repair_opportunity") is True for trace in traces)
    successful_repairs = sum(
        trace.get("repaired_after_execution_failure") is True for trace in traces
    )
    costs = [float(record["cost_usd"]) for record in attempts if _number(record.get("cost_usd")) is not None]
    trace_durations = [
        float(trace["duration_seconds"])
        for trace in traces
        if _number(trace.get("duration_seconds")) is not None
    ]
    wall_durations = [
        float(record["wall_duration_seconds"])
        for record in attempts
        if _number(record.get("wall_duration_seconds")) is not None
    ]
    task_tokens: Counter[tuple[str, str]] = Counter()
    for record, usage in zip(attempts, usages, strict=True):
        task_tokens[(record["source"], record["task_id"])] += _nonnegative_int(
            usage.get("total_tokens")
        )
    stopped_reasons = Counter(
        str(trace["stopped_reason"])
        for trace in traces
        if isinstance(trace.get("stopped_reason"), str)
    )

    return {
        "schema_version": HARNESS_EVALUATION_SCHEMA_VERSION,
        "score_policy": "metric_vector_no_composite",
        "sources": sorted(sources, key=lambda item: item["path"]),
        "configurations": sorted(configurations, key=lambda item: item["source"]),
        "portability": {
            "models": models,
            "model_count": len(models),
            "model_portability_demonstrated": len(models) >= 2,
            "kernels": backends,
            "kernel_count": len(backends),
            "kernel_portability_demonstrated": len(backends) >= 2,
        },
        "workload": {
            "task_count": len(task_keys),
            "attempt_count": len(attempts),
            "completed_valid_task_count": len(valid_task_keys),
            "valid_task_rate": _ratio(len(valid_task_keys), len(task_keys)),
            "retried_task_count": sum(count > 1 for count in attempts_by_task.values()),
            "retry_attempt_count": sum(max(count - 1, 0) for count in attempts_by_task.values()),
        },
        "observability": {
            "trace_count": trace_count,
            "trace_coverage": _ratio(trace_count, len(attempts)),
            "verification_count": verification_count,
            "verification_coverage": _ratio(verification_count, len(attempts)),
            "candidate_hash_count": hash_count,
            "candidate_hash_coverage": _ratio(hash_count, len(attempts)),
        },
        "execution_and_repair": {
            "agent_completed_trace_count": sum(trace.get("agent_completed") is True for trace in traces),
            "agent_completed_rate": _ratio(
                sum(trace.get("agent_completed") is True for trace in traces), len(traces)
            ),
            "turn_count": sum(_nonnegative_int(trace.get("turn_count")) for trace in traces),
            "execution_count": execution_count,
            "successful_execution_count": successful_execution_count,
            "execution_success_rate": _ratio(successful_execution_count, execution_count),
            "repair_opportunity_count": repair_opportunities,
            "successful_repair_count": successful_repairs,
            "repair_success_rate": _ratio(successful_repairs, repair_opportunities),
            "recovered_unterminated_fence_count": sum(
                _nonnegative_int(trace.get("recovered_unterminated_fence_count"))
                for trace in traces
            ),
            "stopped_reasons": dict(sorted(stopped_reasons.items())),
        },
        "usage": {
            "prompt_tokens": sum(_nonnegative_int(usage.get("prompt_tokens")) for usage in usages),
            "completion_tokens": sum(
                _nonnegative_int(usage.get("completion_tokens")) for usage in usages
            ),
            "unclassified_tokens": sum(
                _nonnegative_int(usage.get("unclassified_tokens")) for usage in usages
            ),
            "total_tokens": sum(_nonnegative_int(usage.get("total_tokens")) for usage in usages),
            "tokens_per_task": _distribution(list(task_tokens.values())),
        },
        "latency": {
            "trace_duration_seconds": _distribution(trace_durations),
            "attempt_wall_duration_seconds": _distribution(wall_durations),
        },
        "cost": {
            "available": bool(attempts) and len(costs) == len(attempts),
            "attempts_with_cost": len(costs),
            "total_usd": round(sum(costs), 9) if costs else None,
            "cost_per_attempt_usd": _distribution(costs),
        },
        "failure_taxonomy": _failure_taxonomy(records),
        "warnings": sorted(warnings),
    }
