from __future__ import annotations

import json
from pathlib import Path

import pytest

from cadcopilot.benchmarks.cadgenbench.harness_eval import evaluate_harness
from cadcopilot.cli import main


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _trace(*, failed_then_repaired: bool = False) -> dict[str, object]:
    executions = [
        {
            "success": not failed_then_repaired,
            "duration_seconds": 1.0,
            "recovered_unterminated_fence": failed_then_repaired,
        }
    ]
    if failed_then_repaired:
        executions.append(
            {
                "success": True,
                "duration_seconds": 2.0,
                "recovered_unterminated_fence": False,
            }
        )
    return {
        "completed": False,
        "stopped_reason": "max_iterations",
        "total_duration_seconds": 12.5,
        "total_tokens": 35,
        "turns": [
            {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "reasoning_tokens": 5,
                "executions": executions,
            }
        ],
    }


def _ordinary_run(tmp_path: Path) -> Path:
    run = tmp_path / "ordinary"
    _write(
        run / "manifest.json",
        {
            "status": "completed",
            "request": {
                "fixtures": ["101"],
                "model": "model/a",
                "backend": "build123d",
                "reasoning_effort": "medium",
            },
        },
    )
    _write(
        run / "verification.json",
        {
            "passed": True,
            "tasks": [{"task_id": "101", "status": "valid", "sha256": "abc"}],
        },
    )
    _write(run / "101" / "trace.json", _trace(failed_then_repaired=True))
    return run


def _cohort(tmp_path: Path, *, task_id: str = "201", name: str = "cohort") -> Path:
    cohort = tmp_path / name
    attempt_run = cohort / ".attempts" / task_id / "002" / "run"
    _write(attempt_run / task_id / "trace.json", _trace())
    _write(
        cohort / "cohort.json",
        {
            "config": {
                "model": "model/b",
                "backend": "cadquery",
                "reasoning_effort": "low",
            },
            "tasks": {
                task_id: {
                    "status": "completed",
                    "attempts": [
                        {
                            "attempt_id": "001",
                            "status": "failed",
                            "usage": {"trace_found": False, "total_tokens": 40},
                            "verification": {"passed": False},
                            "cost_usd": 0.002,
                        },
                        {
                            "attempt_id": "002",
                            "status": "completed",
                            "run_dir": f".attempts\\{task_id}\\002\\run",
                            "usage": {
                                "trace_found": True,
                                "prompt_tokens": 10,
                                "completion_tokens": 20,
                                "unclassified_tokens": 5,
                                "total_tokens": 35,
                            },
                            "verification": {"passed": True},
                            "candidate_sha256": "def",
                            "cost_usd": 0.003,
                            "started_at": "2026-09-17T10:00:00+00:00",
                            "finished_at": "2026-09-17T10:00:20+00:00",
                        },
                    ],
                }
            },
        },
    )
    return cohort


def test_evaluation_separates_validity_agent_completion_and_repair(tmp_path: Path) -> None:
    evaluation = evaluate_harness([_ordinary_run(tmp_path)])

    assert evaluation["score_policy"] == "metric_vector_no_composite"
    assert evaluation["workload"]["completed_valid_task_count"] == 1
    assert evaluation["workload"]["valid_task_rate"] == 1.0
    assert evaluation["execution_and_repair"]["agent_completed_rate"] == 0.0
    assert evaluation["execution_and_repair"]["execution_success_rate"] == 0.5
    assert evaluation["execution_and_repair"]["repair_opportunity_count"] == 1
    assert evaluation["execution_and_repair"]["successful_repair_count"] == 1
    assert evaluation["execution_and_repair"]["repair_success_rate"] == 1.0
    assert evaluation["execution_and_repair"]["recovered_unterminated_fence_count"] == 1
    assert evaluation["usage"]["total_tokens"] == 35
    assert evaluation["latency"]["trace_duration_seconds"]["p95"] == 12.5
    assert evaluation["cost"]["available"] is False


def test_cohort_evaluation_reports_retries_cost_and_missing_evidence(tmp_path: Path) -> None:
    evaluation = evaluate_harness([_cohort(tmp_path)])

    assert evaluation["workload"]["task_count"] == 1
    assert evaluation["workload"]["attempt_count"] == 2
    assert evaluation["workload"]["retried_task_count"] == 1
    assert evaluation["workload"]["retry_attempt_count"] == 1
    assert evaluation["observability"]["trace_coverage"] == 0.5
    assert evaluation["failure_taxonomy"] == {
        "failed": 1,
        "missing_trace": 1,
        "validation_failed": 1,
    }
    assert evaluation["cost"]["available"] is True
    assert evaluation["cost"]["total_usd"] == pytest.approx(0.005)
    assert evaluation["latency"]["attempt_wall_duration_seconds"]["mean"] == 20.0


def test_combined_evaluation_only_claims_observed_portability(tmp_path: Path) -> None:
    single = evaluate_harness([_ordinary_run(tmp_path)])
    unpaired = evaluate_harness([tmp_path / "ordinary", _cohort(tmp_path)])
    paired = evaluate_harness(
        [tmp_path / "ordinary", _cohort(tmp_path, task_id="101", name="paired-cohort")]
    )

    assert single["portability"]["model_portability_demonstrated"] is False
    assert single["portability"]["kernel_portability_demonstrated"] is False
    assert unpaired["portability"]["models"] == ["model/a", "model/b"]
    assert unpaired["portability"]["kernels"] == ["build123d", "cadquery"]
    assert unpaired["portability"]["model_portability_demonstrated"] is False
    assert unpaired["portability"]["kernel_portability_demonstrated"] is False
    assert paired["portability"]["model_paired_valid_task_ids"] == ["101"]
    assert paired["portability"]["kernel_paired_valid_task_ids"] == ["101"]
    assert paired["portability"]["model_portability_demonstrated"] is True
    assert paired["portability"]["kernel_portability_demonstrated"] is True


def test_same_task_repair_across_cohorts_is_one_retried_workload(tmp_path: Path) -> None:
    original = _cohort(tmp_path, task_id="205", name="original")
    repair = _cohort(tmp_path, task_id="205", name="repair")

    evaluation = evaluate_harness([original, repair])

    assert evaluation["workload"]["task_count"] == 1
    assert evaluation["workload"]["attempt_count"] == 4
    assert evaluation["workload"]["completed_valid_task_count"] == 1
    assert evaluation["workload"]["valid_task_rate"] == 1.0
    assert evaluation["workload"]["retried_task_count"] == 1
    assert evaluation["workload"]["retry_attempt_count"] == 3
    assert evaluation["usage"]["tokens_per_task"]["count"] == 1


def test_harness_eval_cli_writes_atomic_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _ordinary_run(tmp_path)
    output = tmp_path / "artifacts" / "harness-evaluation.json"

    assert main(["benchmark", "cadgenbench", "harness-eval", str(run), "-o", str(output)]) == 0
    written = json.loads(output.read_text(encoding="utf-8"))
    printed = json.loads(capsys.readouterr().out)
    assert written == printed
    assert written["workload"]["task_count"] == 1


def test_harness_eval_rejects_missing_sources(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        evaluate_harness([tmp_path / "missing"])
