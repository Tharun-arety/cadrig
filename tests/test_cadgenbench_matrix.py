from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cadrig.benchmarks.cadgenbench import matrix
from cadrig.benchmarks.cadgenbench.matrix import (
    CadgenbenchMatrixConfig,
    ModelPricing,
    load_model_pricing,
    run_matrix,
)
from cadrig.benchmarks.cadgenbench.scheduler import CohortResult
from cadrig.cli import build_parser


def _config(tmp_path: Path, **changes: object) -> CadgenbenchMatrixConfig:
    values: dict[str, object] = {
        "matrix_dir": tmp_path / "matrix",
        "fixtures": ("101",),
        "models": ("model/a", "model/b"),
        "backends": ("build123d", "cadquery"),
        "token_budget_per_cell": 100,
        "max_tokens_per_task": 100,
        "sanity_script": tmp_path / "sanity.py",
    }
    values.update(changes)
    return CadgenbenchMatrixConfig(**values)  # type: ignore[arg-type]


def _fake_cohort(calls: list[tuple[str, str]]):
    def run(config: object, **_kwargs: object) -> CohortResult:
        cohort = config.cohort_dir  # type: ignore[attr-defined]
        model = config.model  # type: ignore[attr-defined]
        backend = config.backend  # type: ignore[attr-defined]
        fixtures = config.fixtures  # type: ignore[attr-defined]
        calls.append((model, backend))
        tasks: dict[str, object] = {}
        for task_id in fixtures:
            task_dir = cohort / task_id
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / "trace.json").write_text(
                json.dumps(
                    {
                        "completed": True,
                        "total_tokens": 10,
                        "total_duration_seconds": 1.0,
                        "turns": [
                            {
                                "prompt_tokens": 4,
                                "completion_tokens": 6,
                                "executions": [{"success": True}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            tasks[task_id] = {
                "status": "completed",
                "attempts": [
                    {
                        "attempt_id": "001",
                        "status": "completed",
                        "usage": {
                            "trace_found": True,
                            "prompt_tokens": 4,
                            "completion_tokens": 6,
                            "unclassified_tokens": 0,
                            "total_tokens": 10,
                        },
                        "verification": {"passed": True},
                        "candidate_sha256": f"{model}-{backend}-{task_id}",
                    }
                ],
            }
        (cohort / "cohort.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "config": {"model": model, "backend": backend},
                    "tasks": tasks,
                    "usage": {"total_tokens": 10 * len(fixtures), "cost_usd": None},
                    "budget_accounting": {
                        "debited_tokens": 10 * len(fixtures),
                        "debited_cost_usd": None,
                    },
                }
            ),
            encoding="utf-8",
        )
        return CohortResult(
            cohort_dir=cohort,
            status="completed",
            completed=len(fixtures),
            failed=0,
            pending=0,
            total_tokens=10 * len(fixtures),
            cost_usd=None,
        )

    return run


def test_matrix_runs_every_pair_and_emits_paired_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(matrix, "run_cohort", _fake_cohort(calls))

    result = run_matrix(_config(tmp_path))

    assert result.status == "completed"
    assert result.cell_count == 4
    assert result.completed_cells == 4
    assert result.total_tokens == 40
    assert result.debited_tokens == 40
    assert calls == [
        ("model/a", "build123d"),
        ("model/a", "cadquery"),
        ("model/b", "build123d"),
        ("model/b", "cadquery"),
    ]
    assert result.harness_evaluation is not None
    evaluation = json.loads(result.harness_evaluation.read_text(encoding="utf-8"))
    assert evaluation["portability"]["model_paired_valid_task_ids"] == ["101"]
    assert evaluation["portability"]["kernel_paired_valid_task_ids"] == ["101"]
    assert evaluation["portability"]["model_portability_demonstrated"] is True
    assert evaluation["portability"]["kernel_portability_demonstrated"] is True
    state = json.loads((tmp_path / "matrix" / "matrix.json").read_text(encoding="utf-8"))
    assert state["admission"]["maximum_admitted_tokens"] == 400


def test_matrix_requires_budget_for_every_fixture_in_each_cell(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="full paired fixture set"):
        _config(
            tmp_path,
            fixtures=("101", "102"),
            token_budget_per_cell=199,
            max_tokens_per_task=100,
        )


def test_matrix_pricing_is_explicit_complete_and_budgeted(tmp_path: Path) -> None:
    pricing_file = tmp_path / "pricing.json"
    pricing_file.write_text(
        json.dumps(
            {
                "model/a": {
                    "input_usd_per_million": 2,
                    "output_usd_per_million": 10,
                    "cost_budget_usd_per_cell": 0.001,
                },
                "model/b": {
                    "input_usd_per_million": 3,
                    "output_usd_per_million": 12,
                    "cost_budget_usd_per_cell": 0.0012,
                },
            }
        ),
        encoding="utf-8",
    )
    pricing = load_model_pricing(pricing_file)
    assert [item.model for item in pricing] == ["model/a", "model/b"]
    assert _config(tmp_path, pricing=pricing).pricing == pricing

    with pytest.raises(ValueError, match="cover every matrix model"):
        _config(tmp_path, pricing=(pricing[0],))
    with pytest.raises(ValueError, match="cannot admit"):
        _config(
            tmp_path,
            pricing=(
                ModelPricing("model/a", 2, 10, 0.0009),
                ModelPricing("model/b", 3, 12, 0.0012),
            ),
        )


def test_matrix_resume_refuses_configuration_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(matrix, "run_cohort", _fake_cohort([]))
    run_matrix(_config(tmp_path))
    with pytest.raises(ValueError, match="saved fingerprint"):
        run_matrix(_config(tmp_path, reasoning_effort="high"))


def test_matrix_lock_refuses_concurrent_scheduler(tmp_path: Path) -> None:
    config = _config(tmp_path)
    lock = config.matrix_dir.parent / f".{config.matrix_dir.name}.matrix.lock"
    lock.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="already locked"):
        run_matrix(config)


def test_matrix_cli_accepts_repeated_models_and_backends(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "benchmark",
            "cadgenbench",
            "matrix",
            "101",
            "--matrix-dir",
            str(tmp_path / "matrix"),
            "--model",
            "model/a",
            "--model",
            "model/b",
            "--backend",
            "build123d",
            "--backend",
            "cadquery",
            "--token-budget-per-cell",
            "200",
            "--max-tokens-per-task",
            "100",
        ]
    )
    assert args.models == ["model/a", "model/b"]
    assert args.backends == ["build123d", "cadquery"]
