from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from cadrig.benchmarks.cadgenbench.compose import compose_runs


def _source(root: Path, task_id: str, content: str) -> Path:
    source = root / f"source-{task_id}"
    task = source / task_id
    task.mkdir(parents=True)
    (task / "output.step").write_text(content, encoding="utf-8")
    (task / "trace.json").write_text(json.dumps({"task": task_id}), encoding="utf-8")
    return source


def _checker(root: Path) -> Path:
    checker = root / "sanity.py"
    checker.write_text("import sys\nraise SystemExit(0)\n", encoding="utf-8")
    return checker


def test_compose_runs_copies_disjoint_candidates_with_provenance(tmp_path: Path) -> None:
    first = _source(tmp_path, "101", "one")
    second = _source(tmp_path, "201", "two")
    output = tmp_path / "composed"

    result = compose_runs(
        (first, second), output, sanity_script=_checker(tmp_path)
    )

    assert result.task_count == 2
    assert result.verification.passed is True
    assert (output / "101" / "output.step").read_text(encoding="utf-8") == "one"
    assert json.loads((output / "201" / "trace.json").read_text())["task"] == "201"
    provenance = json.loads((output / "composition.json").read_text())
    assert sorted(provenance["tasks"]) == ["101", "201"]


def test_compose_runs_rejects_conflicting_duplicate_candidates(tmp_path: Path) -> None:
    first = _source(tmp_path / "a", "101", "one")
    second = _source(tmp_path / "b", "101", "different")

    with pytest.raises(ValueError, match="conflicting candidates for task 101"):
        compose_runs((first, second), tmp_path / "composed", sanity_script=Path(sys.executable))
