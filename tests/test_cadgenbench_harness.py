from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

from cadcopilot.benchmarks.cadgenbench.compat import (
    completion_token_allowance,
    extract_code_blocks_tolerant,
)
from cadcopilot.benchmarks.cadgenbench.dataset import find_sanity_script
from cadcopilot.benchmarks.cadgenbench.package import package_run
from cadcopilot.benchmarks.cadgenbench.runner import (
    CadgenbenchRunConfig,
    build_baseline_command,
    run_official_baseline,
)
from cadcopilot.benchmarks.cadgenbench.sanity import verify_run


def _strict_fence_extractor(text: str, lang: str) -> list[str]:
    import re

    return re.findall(rf"```{re.escape(lang)}\s*\n(.*?)```", text, re.DOTALL)


def test_tolerant_extractor_preserves_complete_blocks() -> None:
    text = "before\n```python\nprint('ok')\n```\nafter"
    assert extract_code_blocks_tolerant(text, "python", _strict_fence_extractor) == [
        "print('ok')\n"
    ]


def test_tolerant_extractor_recovers_truncated_final_block() -> None:
    text = "analysis\n```python\nfrom build123d import *\n# response limit"
    assert extract_code_blocks_tolerant(text, "python", _strict_fence_extractor) == [
        "from build123d import *\n# response limit"
    ]


def test_tolerant_extractor_prefers_valid_truncated_final_block() -> None:
    text = "```python\nprint('old')\n```\nrevision\n```python\nprint('new')"
    assert extract_code_blocks_tolerant(text, "python", _strict_fence_extractor) == [
        "print('new')"
    ]


def test_tolerant_extractor_rejects_invalid_truncated_python() -> None:
    text = "analysis\n```python\nvalue = ("
    assert extract_code_blocks_tolerant(text, "python", _strict_fence_extractor) == []


def test_completion_token_allowance_reserves_prompt_before_call() -> None:
    assert completion_token_allowance(
        token_cap=10_000,
        consumed=4_000,
        prompt_tokens=1_500,
        requested=8_000,
    ) == 4_500
    assert completion_token_allowance(
        token_cap=5_000,
        consumed=4_000,
        prompt_tokens=1_000,
        requested=2_000,
    ) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("max_iterations", 0),
        ("max_tokens", -1),
        ("max_tokens_per_call", 0),
        ("max_duration", -0.1),
    ),
)
def test_run_config_rejects_nonpositive_limits(
    tmp_path: Path, field: str, value: float
) -> None:
    kwargs = {field: value}
    with pytest.raises(ValueError, match=field):
        CadgenbenchRunConfig(output_root=tmp_path, fixtures=("101",), **kwargs)


def test_find_sanity_script_discovers_huggingface_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "hub"
    script = (
        cache
        / "datasets--HuggingAI4Engineering--cadgenbench-data"
        / "snapshots"
        / "revision"
        / "sanity_check_submission.py"
    )
    script.parent.mkdir(parents=True)
    script.write_text("# checker", encoding="utf-8")
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    assert find_sanity_script(None) == script


def _write_run(run_dir: Path, *, all_tasks: bool = True, missing: bool = False) -> None:
    run_dir.mkdir()
    (run_dir / "params.json").write_text(
        json.dumps({"fixtures": ["101", "201"]}), encoding="utf-8"
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"request": {"all": all_tasks, "fixtures": []}}), encoding="utf-8"
    )
    for task_id in ("101", "201"):
        task_dir = run_dir / task_id
        task_dir.mkdir()
        if not missing or task_id != "201":
            (task_dir / "output.step").write_bytes(f"STEP {task_id}".encode())


def test_build_baseline_command_contains_reproducibility_controls(tmp_path: Path) -> None:
    config = CadgenbenchRunConfig(
        output_root=tmp_path,
        run_all=True,
        model="openai/example",
        parallel=3,
        max_iterations=12,
        max_tokens=5000,
        max_tokens_per_call=32000,
        max_duration=90,
        reasoning_effort="high",
    )
    command = build_baseline_command(config, command_prefix=("cgb",))
    assert command[:4] == ["cgb", "baseline", "run", "--all"]
    assert command[command.index("--model") + 1] == "openai/example"
    assert command[command.index("--parallel") + 1] == "3"
    assert command[command.index("--max-iter") + 1] == "12"
    assert command[command.index("--max-tokens-per-call") + 1] == "32000"


def test_verify_run_checks_every_candidate_with_sanity_command(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir)
    checker = (
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; sys.exit(0 if Path(sys.argv[1]).stat().st_size else 1)",
    )
    report = verify_run(run_dir, sanity_command=checker, require_sanity=True)
    assert report.passed
    assert report.scope == "all"
    assert report.expected_count == 2
    assert report.valid_count == 2
    assert json.loads((run_dir / "verification.json").read_text())["passed"] is True


def test_verify_run_reports_missing_candidate(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir, missing=True)
    report = verify_run(run_dir)
    assert not report.passed
    assert report.missing_count == 1
    assert report.tasks[1].status == "missing"


def test_package_run_writes_only_submission_contract_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir)
    checker = tmp_path / "sanity.py"
    checker.write_text("raise SystemExit(0)", encoding="utf-8")
    result = package_run(
        run_dir,
        submitter="Engineer",
        submission_name="CADRIG Alpha",
        agree_to_publish=True,
        sanity_script=checker,
    )
    with zipfile.ZipFile(result.output) as archive:
        assert set(archive.namelist()) == {
            "meta.json",
            "101/",
            "101/output.step",
            "201/",
            "201/output.step",
        }
        meta = json.loads(archive.read("meta.json"))
    assert meta["submitter_name"] == "Engineer"
    assert meta["agree_to_publish"] is True


def test_package_run_refuses_partial_scope_by_default(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir, all_tasks=False)
    with pytest.raises(ValueError, match="incomplete CADGenBench run"):
        package_run(run_dir)


def test_official_baseline_run_records_manifest(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_cadgenbench.py"
    fake_cli.write_text(
        """
import json
import sys
from pathlib import Path

args = sys.argv[1:]
output_root = Path(args[args.index('--output-dir') + 1])
run_dir = output_root / 'fake-run'
run_dir.mkdir(parents=True)
(run_dir / 'params.json').write_text(json.dumps({'fixtures': ['101', '201']}))
for task_id in ('101', '201'):
    task_dir = run_dir / task_id
    task_dir.mkdir()
    (task_dir / 'output.step').write_text('STEP')
""".strip(),
        encoding="utf-8",
    )
    config = CadgenbenchRunConfig(output_root=tmp_path / "results", run_all=True)
    run_dir = run_official_baseline(
        config,
        command_prefix=(sys.executable, str(fake_cli)),
        environ={},
        cwd=tmp_path,
    )
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["request"]["all"] is True
    assert manifest["official_params"]["fixtures"] == ["101", "201"]
    assert manifest["environment"]["python"]
    assert manifest["completion"]["complete"] is True
    assert manifest["completion"]["trace_count"] == 0
    assert manifest["command"][0] == sys.executable
    assert (run_dir / "harness.log").is_file()
    invocation = json.loads((run_dir / "harness_invocation.json").read_text())
    assert invocation["status"] == "completed"
