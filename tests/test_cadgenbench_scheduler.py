from __future__ import annotations

import json
from pathlib import Path

import pytest

from cadcopilot.benchmarks.cadgenbench import scheduler
from cadcopilot.benchmarks.cadgenbench.scheduler import CadgenbenchCohortConfig, run_cohort


def _checker(tmp_path: Path) -> Path:
    checker = tmp_path / "sanity.py"
    checker.write_text("raise SystemExit(0)\n", encoding="utf-8")
    return checker


def _config(tmp_path: Path, **changes: object) -> CadgenbenchCohortConfig:
    values: dict[str, object] = {
        "cohort_dir": tmp_path / "cohort",
        "fixtures": ("101", "102"),
        "model": "test/model",
        "total_token_budget": 1_000,
        "max_tokens_per_task": 500,
        "input_usd_per_million": 2.0,
        "output_usd_per_million": 10.0,
        "total_cost_budget_usd": 1.0,
        "sanity_script": _checker(tmp_path),
    }
    values.update(changes)
    return CadgenbenchCohortConfig(**values)  # type: ignore[arg-type]


def _fake_baseline(calls: list[dict[str, object]], usages: list[tuple[int, int]]):
    def run(config: object, **kwargs: object) -> Path:
        run_config = config
        task_id = run_config.fixtures[0]  # type: ignore[attr-defined]
        attempt_root = run_config.output_root  # type: ignore[attr-defined]
        run_dir = attempt_root / "official-run"
        task_dir = run_dir / task_id
        task_dir.mkdir(parents=True)
        (run_dir / "params.json").write_text(json.dumps({"fixtures": [task_id]}), encoding="utf-8")
        (task_dir / "output.step").write_text("STEP", encoding="utf-8")
        (task_dir / "debug.txt").write_text("debug", encoding="utf-8")
        prompt, completion = usages[len(calls)]
        (task_dir / "trace.json").write_text(
            json.dumps(
                {
                    "total_tokens": prompt + completion,
                    "turns": [{"prompt_tokens": prompt, "completion_tokens": completion}],
                }
            ),
            encoding="utf-8",
        )
        calls.append({"config": config, **kwargs})
        return run_dir

    return run


def test_cohort_runs_isolated_tasks_and_accounts_actual_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        scheduler, "run_official_baseline", _fake_baseline(calls, [(100, 200), (50, 50)])
    )

    result = run_cohort(_config(tmp_path))

    assert result.status == "completed"
    assert result.total_tokens == 400
    assert result.cost_usd == pytest.approx(0.0028)
    assert len(calls) == 2
    assert all(call["environ"] == {"CADCOPILOT_ATTEMPT_TOKEN_CAP": "500"} for call in calls)
    assert (tmp_path / "cohort" / "101" / "debug.txt").read_text() == "debug"
    state = json.loads((tmp_path / "cohort" / "cohort.json").read_text())
    assert state["usage"]["prompt_tokens"] == 150
    assert state["usage"]["completion_tokens"] == 250
    assert state["budget_accounting"]["debited_tokens"] == 400
    assert len(list((tmp_path / "cohort" / ".attempts" / "101").iterdir())) == 1


def test_resume_revalidates_and_skips_completed_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    fake = _fake_baseline(calls, [(10, 10), (10, 10)])
    monkeypatch.setattr(scheduler, "run_official_baseline", fake)
    config = _config(tmp_path)
    assert run_cohort(config).completed == 2
    assert run_cohort(config).completed == 2
    assert len(calls) == 2


def test_resume_refuses_configuration_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        scheduler, "run_official_baseline", _fake_baseline(calls, [(10, 10), (10, 10)])
    )
    run_cohort(_config(tmp_path))
    with pytest.raises(ValueError, match="saved fingerprint"):
        run_cohort(_config(tmp_path, model="other/model"))


def test_token_reservation_stops_before_unaffordable_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(scheduler, "run_official_baseline", _fake_baseline(calls, [(100, 400)]))
    config = _config(
        tmp_path,
        total_token_budget=600,
        max_tokens_per_task=400,
        input_usd_per_million=None,
        output_usd_per_million=None,
        total_cost_budget_usd=None,
    )
    result = run_cohort(config)
    assert result.status == "stopped_token_budget"
    assert result.completed == 1
    assert len(calls) == 1


def test_cost_reservation_stops_without_calling_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        scheduler,
        "run_official_baseline",
        lambda *_args, **_kwargs: pytest.fail("provider should not be called"),
    )
    result = run_cohort(_config(tmp_path, total_cost_budget_usd=0.004, max_tokens_per_task=500))
    assert result.status == "stopped_cost_budget"
    assert result.completed == 0


def test_missing_trace_keeps_full_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_trace(config: object, **_kwargs: object) -> Path:
        task_id = config.fixtures[0]  # type: ignore[attr-defined]
        run_dir = config.output_root / "run"  # type: ignore[attr-defined]
        task_dir = run_dir / task_id
        task_dir.mkdir(parents=True)
        (task_dir / "output.step").write_text("STEP", encoding="utf-8")
        return run_dir

    monkeypatch.setattr(scheduler, "run_official_baseline", no_trace)
    result = run_cohort(
        _config(
            tmp_path,
            total_token_budget=600,
            max_tokens_per_task=400,
            input_usd_per_million=None,
            output_usd_per_million=None,
            total_cost_budget_usd=None,
        )
    )
    state = json.loads((tmp_path / "cohort" / "cohort.json").read_text())
    assert result.status == "stopped_token_budget"
    assert state["usage"]["total_tokens"] == 0
    assert state["budget_accounting"]["debited_tokens"] == 400
    assert state["budget_accounting"]["unverified_reserved_tokens"] == 400


def test_invalid_cost_configuration_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="supplied together"):
        _config(tmp_path, output_usd_per_million=None)
    with pytest.raises(ValueError, match="requires explicit"):
        _config(
            tmp_path,
            input_usd_per_million=None,
            output_usd_per_million=None,
        )


def test_corrupt_or_missing_state_in_nonempty_directory_fails_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.cohort_dir.mkdir()
    (config.cohort_dir / "cohort.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="state is corrupt"):
        run_cohort(config)

    (config.cohort_dir / "cohort.json").unlink()
    (config.cohort_dir / "unexpected.txt").write_text("data", encoding="utf-8")
    with pytest.raises(ValueError, match="nonempty cohort directory"):
        run_cohort(config)


def test_existing_lock_refuses_second_scheduler(tmp_path: Path) -> None:
    config = _config(tmp_path)
    lock = config.cohort_dir.parent / f".{config.cohort_dir.name}.cohort.lock"
    lock.write_text("locked", encoding="utf-8")
    with pytest.raises(RuntimeError, match="already locked"):
        run_cohort(config)


def test_stale_lock_is_reclaimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    lock = config.cohort_dir.parent / f".{config.cohort_dir.name}.cohort.lock"
    lock.write_text(json.dumps({"pid": 2_147_483_647}), encoding="utf-8")
    monkeypatch.setattr(
        scheduler,
        "run_official_baseline",
        _fake_baseline([], [(10, 10), (10, 10)]),
    )
    assert run_cohort(config).status == "completed"
    assert not lock.exists()


def test_crash_abandoned_attempt_is_not_relaunched_and_keeps_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(
        tmp_path,
        input_usd_per_million=None,
        output_usd_per_million=None,
        total_cost_budget_usd=None,
    )
    config.cohort_dir.mkdir()
    state = scheduler._initial_state(config)
    state["tasks"]["101"]["status"] = "running"
    state["tasks"]["101"]["attempts"].append(
        {
            "attempt_id": "001-crashed",
            "status": "running",
            "usage": {
                "trace_found": False,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "unclassified_tokens": 0,
                "total_tokens": 0,
            },
            "budget_debit_tokens": 500,
            "budget_debit_cost_usd": None,
        }
    )
    scheduler._save_state(config.cohort_dir / "cohort.json", state)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        scheduler, "run_official_baseline", _fake_baseline(calls, [(10, 10)])
    )

    result = run_cohort(config)

    assert len(calls) == 1
    assert calls[0]["config"].fixtures == ("102",)  # type: ignore[union-attr]
    saved = json.loads((config.cohort_dir / "cohort.json").read_text())
    abandoned = saved["tasks"]["101"]["attempts"][0]
    assert abandoned["status"] == "abandoned"
    assert saved["budget_accounting"]["debited_tokens"] == 520
    assert result.status == "partial"


def test_dataset_complete_cohort_marks_manifest_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset"
    for task_id in ("101", "102"):
        task = dataset / "inputs" / task_id
        task.mkdir(parents=True)
        (task / "description.yaml").write_text("description: test", encoding="utf-8")
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        scheduler, "run_official_baseline", _fake_baseline(calls, [(10, 10), (10, 10)])
    )
    run_cohort(_config(tmp_path, dataset_dir=dataset))
    manifest = json.loads((tmp_path / "cohort" / "manifest.json").read_text())
    assert manifest["request"]["all"] is True
