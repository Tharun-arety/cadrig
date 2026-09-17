from __future__ import annotations

import json
from pathlib import Path

from cadcopilot.benchmarks.cadgenbench.report import report_runs, summarize_run
from cadcopilot.cli import main


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _complete_run(root: Path, run_id: str, score: float, validity: float) -> Path:
    run_dir = root / run_id
    run_dir.mkdir()
    _write_json(
        run_dir / "manifest.json",
        {
            "run_id": run_id,
            "status": "completed",
            "duration_seconds": 12.5,
            "request": {
                "model": "provider/model",
                "backend": "build123d",
                "reasoning_effort": "medium",
                "all": False,
                "fixtures": ["201", "101", "101"],
            },
        },
    )
    _write_json(
        run_dir / "run_summary.json",
        {
            "aggregate_score": score,
            "validity_rate": validity,
            "n_samples": 2,
            "n_valid": 2,
            "n_invalid": 0,
            "n_missing": 0,
            "score_by_task_type": {"generation": score},
        },
    )
    _write_json(
        run_dir / "verification.json",
        {
            "passed": True,
            "complete": True,
            "sanity_checked": True,
            "scope": "partial",
            "expected_count": 2,
            "candidate_count": 2,
            "valid_count": 2,
            "invalid_count": 0,
            "missing_count": 0,
        },
    )
    for fixture in ("101", "201"):
        fixture_dir = run_dir / fixture
        fixture_dir.mkdir()
        _write_json(
            fixture_dir / "result.json",
            {"cad_score": score, "gt_metrics": {"shape_volume_iou": score}},
        )
    return run_dir


def test_report_runs_ranks_scores_and_normalizes_run_order(tmp_path: Path) -> None:
    lower = _complete_run(tmp_path, "run-b", 0.4, 1.0)
    higher = _complete_run(tmp_path, "run-a", 0.7, 0.9)

    report = report_runs([lower, higher])

    assert report["run_count"] == 2
    assert report["completed_run_count"] == 2
    assert report["best_run_id"] == "run-a"
    assert [item["run_id"] for item in report["runs"]] == ["run-a", "run-b"]
    assert [item["run_id"] for item in report["ranking"]] == ["run-a", "run-b"]
    assert report["runs"][0]["fixtures"] == ["101", "201"]


def test_summarize_run_tolerates_missing_and_invalid_partial_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "interrupted"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text("not-json", encoding="utf-8")
    _write_json(
        run_dir / "verification.json",
        {
            "complete": False,
            "expected_count": 4,
            "valid_count": 1,
            "missing_count": 3,
        },
    )

    summary = summarize_run(run_dir)

    assert summary["status"] == "partial"
    assert summary["metrics"]["aggregate_score"] is None
    assert summary["metrics"]["validity_rate"] == 0.25
    assert summary["artifacts"] == {
        "manifest": False,
        "run_summary": False,
        "verification": True,
    }
    assert summary["warnings"] == ["invalid manifest.json", "missing run_summary.json"]


def test_report_command_prints_json_for_partial_run(
    tmp_path: Path, capsys: object
) -> None:
    run_dir = tmp_path / "partial"
    run_dir.mkdir()
    _write_json(run_dir / "manifest.json", {"status": "running", "request": {}})

    assert main(["benchmark", "cadgenbench", "report", str(run_dir)]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)
    assert payload["run_count"] == 1
    assert payload["runs"][0]["status"] == "running"


def test_public_candidate_only_zero_is_unscored(tmp_path: Path) -> None:
    run_dir = tmp_path / "public-run"
    run_dir.mkdir()
    _write_json(
        run_dir / "manifest.json",
        {"status": "completed", "request": {"fixtures": ["101"]}},
    )
    _write_json(
        run_dir / "run_summary.json",
        {"aggregate_score": 0.0, "validity_rate": 1.0},
    )
    fixture_dir = run_dir / "101"
    fixture_dir.mkdir()
    _write_json(fixture_dir / "result.json", {"status": "valid"})

    report = report_runs([run_dir])

    assert report["scored_run_count"] == 0
    assert report["best_run_id"] is None
    assert report["runs"][0]["metrics"]["aggregate_score"] is None
    assert report["runs"][0]["metrics"]["reported_aggregate_score"] == 0.0
