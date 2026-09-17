from __future__ import annotations

import json
from pathlib import Path

import pytest

from cadcopilot.benchmarks.cadgenbench import batch_plan
from cadcopilot.benchmarks.cadgenbench.batch_plan import (
    CadgenbenchBatchPlanConfig,
    create_batch_plan,
    run_planned_batch,
)
from cadcopilot.benchmarks.cadgenbench.scheduler import CohortResult


def _dataset(tmp_path: Path) -> Path:
    dataset = tmp_path / "dataset"
    for index in range(1, 13):
        task_id = f"1{index:02d}"
        task = dataset / task_id
        task.mkdir(parents=True)
        (task / "description.yaml").write_text("description: reproduce\n", encoding="utf-8")
        (task / "input.png").write_bytes(b"x" * (100 + index * 10))
    descriptions = (
        "Increase the bore diameter.",
        "Remove two holes from the furthest face.",
        "Resize the fillet on each of three bosses.",
        "Increase the height.",
        "Remove the groove.",
        "Increase each flange and make it flush with the nearest face.",
        "Widen the bore.",
        "Change the spacing of two holes.",
        "Remove the chamfer and fillet every outer edge.",
        "Thicken the wall.",
        "Remove three holes.",
        "Resize the blend on each of four ribs.",
    )
    for index, description in enumerate(descriptions, start=1):
        task_id = f"2{index:02d}"
        task = dataset / task_id
        task.mkdir(parents=True)
        (task / "description.yaml").write_text("task_type: editing\n", encoding="utf-8")
        (task / "edit_description.txt").write_text(description, encoding="utf-8")
        (task / "input.step").write_bytes(b"s" * (100 + index * 20))
    return dataset


def _completed(tmp_path: Path, task_ids: tuple[str, ...] = ("101", "201")) -> Path:
    run = tmp_path / "completed"
    tasks = []
    for task_id in task_ids:
        task = run / task_id
        task.mkdir(parents=True)
        candidate = task / "output.step"
        candidate.write_text(f"STEP-{task_id}", encoding="utf-8")
        tasks.append(
            {
                "task_id": task_id,
                "status": "valid",
                "sha256": batch_plan.sha256_file(candidate),
            }
        )
    (run / "verification.json").write_text(
        json.dumps({"passed": True, "sanity_checked": True, "tasks": tasks}),
        encoding="utf-8",
    )
    return run


def _config(tmp_path: Path, **changes: object) -> CadgenbenchBatchPlanConfig:
    values: dict[str, object] = {
        "dataset_dir": _dataset(tmp_path),
        "completed_run": _completed(tmp_path),
        "output": tmp_path / "plan.json",
        "model": "test/model",
        "target_batch_size": 6,
        "input_usd_per_million": 2.0,
        "output_usd_per_million": 12.0,
    }
    values.update(changes)
    return CadgenbenchBatchPlanConfig(**values)  # type: ignore[arg-type]


def test_plan_is_complete_balanced_and_deterministic(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first = create_batch_plan(config)
    second = create_batch_plan(config)

    assert first["plan_id"] == second["plan_id"]
    assert first["summary"]["dataset_tasks"] == 24
    assert first["summary"]["completed_tasks"] == 2
    assert first["summary"]["remaining_tasks"] == 22
    assert first["summary"]["batch_count"] == 4
    fixtures = [task for item in first["batches"] for task in item["fixtures"]]
    assert len(fixtures) == len(set(fixtures)) == 22
    assert {"101", "201"}.isdisjoint(fixtures)
    assert all(set(batch["family_counts"]) == {"editing", "generation"} for batch in first["batches"])
    assert all(set(batch["complexity_counts"]) == set(batch_plan._BANDS) for batch in first["batches"])
    assert {batch["task_count"] for batch in first["batches"]} <= {5, 6}
    assert first["batches"][0]["conservative_cost_ceiling_usd"] == 5.76


def test_plan_refuses_unverified_or_changed_completed_candidates(tmp_path: Path) -> None:
    config = _config(tmp_path)
    verification = config.completed_run / "verification.json"
    payload = json.loads(verification.read_text(encoding="utf-8"))
    payload["sanity_checked"] = False
    verification.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="passing official sanity"):
        create_batch_plan(config)

    payload["sanity_checked"] = True
    verification.write_text(json.dumps(payload), encoding="utf-8")
    (config.completed_run / "101" / "output.step").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash changed"):
        create_batch_plan(config)


def test_run_batch_rejects_plan_tampering(tmp_path: Path) -> None:
    config = _config(tmp_path)
    plan = create_batch_plan(config)
    plan["batches"][0]["fixtures"].append("999")
    config.output.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        run_planned_batch(config.output, "batch-01")


def test_run_batch_uses_exact_saved_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    plan = create_batch_plan(config)
    captured: dict[str, object] = {}

    def fake_run(cohort_config: object, **kwargs: object) -> CohortResult:
        captured["config"] = cohort_config
        captured.update(kwargs)
        return CohortResult(Path("cohort"), "completed", 6, 0, 0, 10, 0.01)

    monkeypatch.setattr(batch_plan, "run_cohort", fake_run)
    result = run_planned_batch(config.output, "batch-01", cwd=tmp_path)
    saved = captured["config"]

    assert result.status == "completed"
    assert saved.fixtures == tuple(plan["batches"][0]["fixtures"])  # type: ignore[union-attr]
    assert saved.total_token_budget == 480_000  # type: ignore[union-attr]
    assert saved.total_cost_budget_usd == 5.76  # type: ignore[union-attr]
    assert saved.reasoning_effort == "low"  # type: ignore[union-attr]
