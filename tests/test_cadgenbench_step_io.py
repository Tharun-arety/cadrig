from __future__ import annotations

from pathlib import Path

import build123d
import pytest
from build123d import Box

from cadcopilot.benchmarks.cadgenbench.step_io import robust_export_step


def test_robust_export_uses_build123d_when_available(tmp_path: Path) -> None:
    destination = tmp_path / "output.step"

    method = robust_export_step(Box(10, 20, 30), destination)

    assert method == "build123d"
    assert destination.stat().st_size > 0
    assert not list(tmp_path.glob(".*.tmp.step"))


def test_robust_export_falls_back_to_ocp_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "output.step"
    destination.write_text("previous", encoding="utf-8")
    monkeypatch.setattr(
        build123d,
        "export_step",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("unsupported")),
    )

    method = robust_export_step(Box(10, 20, 30), destination)

    assert method == "ocp-stepcontrol"
    assert destination.stat().st_size > len("previous")
    assert not list(tmp_path.glob(".*.tmp.step"))
