from __future__ import annotations

import json
from pathlib import Path

import pytest

from cadrig.benchmarks.cadgenbench import runner


def _config(tmp_path: Path) -> runner.CadgenbenchRunConfig:
    return runner.CadgenbenchRunConfig(
        output_root=tmp_path / "results",
        fixtures=("101",),
        model="test/model",
    )


def _stub_manifest_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_git_revision", lambda _cwd: "test-revision")
    monkeypatch.setattr(
        runner,
        "_package_environment",
        lambda: {
            "python": "test-python",
            "platform": "test-platform",
            "cadgenbench": {"version": "test", "commit": "test", "url": None},
        },
    )


def test_failed_process_records_partial_run_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _stub_manifest_environment(monkeypatch)

    def fake_run(*_args: object, **_kwargs: object) -> int:
        run_dir = config.output_root / "failed-run"
        run_dir.mkdir(parents=True)
        (run_dir / "params.json").write_text(
            json.dumps({"fixtures": ["101"]}), encoding="utf-8"
        )
        return 17

    monkeypatch.setattr(runner, "_stream_subprocess", fake_run)

    with pytest.raises(RuntimeError, match=r"failed with exit code 17; partial run:"):
        runner.run_official_baseline(config, command_prefix=("fake-cgb",), cwd=tmp_path)

    manifest = json.loads(
        (config.output_root / "failed-run" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["exit_code"] == 17
    assert manifest["official_params"] == {"fixtures": ["101"]}
    assert manifest["git_revision"] == "test-revision"


def test_interrupted_process_records_recoverable_partial_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _stub_manifest_environment(monkeypatch)

    def fake_run(*_args: object, **_kwargs: object) -> None:
        run_dir = config.output_root / "interrupted-run"
        run_dir.mkdir(parents=True)
        (run_dir / "params.json").write_text(
            json.dumps({"fixtures": ["101"]}), encoding="utf-8"
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "_stream_subprocess", fake_run)

    with pytest.raises(RuntimeError, match=r"interrupted; partial run:") as error:
        runner.run_official_baseline(config, command_prefix=("fake-cgb",), cwd=tmp_path)

    assert isinstance(error.value.__cause__, KeyboardInterrupt)
    manifest = json.loads(
        (config.output_root / "interrupted-run" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == "interrupted"
    assert manifest["exit_code"] == 130
    assert manifest["request"]["fixtures"] == ["101"]


def test_interrupt_before_run_directory_has_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        runner,
        "_stream_subprocess",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    with pytest.raises(
        RuntimeError, match="interrupted before creating a run directory"
    ) as error:
        runner.run_official_baseline(config, command_prefix=("fake-cgb",), cwd=tmp_path)

    assert isinstance(error.value.__cause__, KeyboardInterrupt)
