"""Delegate reproducible generation runs to the official CADGenBench baseline."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

from cadcopilot import __version__

from .common import (
    MANIFEST_SCHEMA_VERSION,
    candidate_for,
    read_json,
    validate_task_id,
    write_json_atomic,
)

DEFAULT_DATA_REPO = "HuggingAI4Engineering/cadgenbench-data"


@dataclass(frozen=True)
class CadgenbenchRunConfig:
    output_root: Path
    fixtures: tuple[str, ...] = ()
    run_all: bool = False
    model: str | None = None
    backend: str = "build123d"
    parallel: int = 1
    max_iterations: int | None = None
    max_tokens: int | None = None
    max_tokens_per_call: int | None = None
    max_duration: float | None = None
    reasoning_effort: str | None = None
    data_repo: str = DEFAULT_DATA_REPO

    def __post_init__(self) -> None:
        if self.run_all == bool(self.fixtures):
            raise ValueError("select either fixtures or run_all")
        if self.backend not in {"build123d", "cadquery"}:
            raise ValueError("backend must be build123d or cadquery")
        if self.parallel < 1:
            raise ValueError("parallel must be at least 1")
        if len(set(self.fixtures)) != len(self.fixtures):
            raise ValueError("fixtures must not contain duplicates")
        numeric_limits = {
            "max_iterations": self.max_iterations,
            "max_tokens": self.max_tokens,
            "max_tokens_per_call": self.max_tokens_per_call,
            "max_duration": self.max_duration,
        }
        for name, value in numeric_limits.items():
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.model is not None and not self.model.strip():
            raise ValueError("model must not be blank")
        for task_id in self.fixtures:
            validate_task_id(task_id)


def official_command_prefix() -> tuple[str, ...]:
    local_python_candidates = (
        Path.cwd() / ".venv" / "Scripts" / "python.exe",
        Path.cwd() / ".venv" / "bin" / "python",
    )
    local = next((path for path in local_python_candidates if path.is_file()), None)
    if local is not None:
        return (
            str(local),
            "-m",
            "cadcopilot.benchmarks.cadgenbench.official_cli",
        )
    executable = shutil.which("cadgenbench") or shutil.which("cgb")
    if executable:
        return (executable,)
    return (sys.executable, "-m", "cadgenbench.cli")


def build_baseline_command(
    config: CadgenbenchRunConfig,
    *,
    command_prefix: Sequence[str] | None = None,
) -> list[str]:
    command = list(command_prefix or official_command_prefix())
    command.extend(("baseline", "run"))
    if config.run_all:
        command.append("--all")
    else:
        command.extend(config.fixtures)
    command.extend(("--output-dir", str(config.output_root)))
    command.extend(("--backend", config.backend, "--parallel", str(config.parallel)))
    if config.model:
        command.extend(("--model", config.model))
    if config.max_iterations is not None:
        command.extend(("--max-iter", str(config.max_iterations)))
    if config.max_tokens is not None:
        command.extend(("--max-tokens", str(config.max_tokens)))
    if config.max_tokens_per_call is not None:
        command.extend(("--max-tokens-per-call", str(config.max_tokens_per_call)))
    if config.max_duration is not None:
        command.extend(("--max-duration", str(config.max_duration)))
    if config.reasoning_effort:
        command.extend(("--reasoning-effort", config.reasoning_effort))
    return command


def _git_revision(cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _package_environment() -> dict[str, object]:
    cadgenbench: dict[str, object] = {"version": None, "commit": None, "url": None}
    try:
        cadgenbench["version"] = version("cadgenbench")
        direct_url = distribution("cadgenbench").read_text("direct_url.json")
        if direct_url:
            payload = json.loads(direct_url)
            cadgenbench["url"] = payload.get("url")
            vcs_info = payload.get("vcs_info")
            if isinstance(vcs_info, dict):
                cadgenbench["commit"] = vcs_info.get("commit_id")
    except (PackageNotFoundError, OSError, ValueError):
        pass
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cadgenbench": cadgenbench,
    }


def _manifest_payload(
    config: CadgenbenchRunConfig,
    run_dir: Path,
    *,
    started: datetime,
    duration_seconds: float,
    status: str,
    exit_code: int,
    cwd: Path,
    command: Sequence[str],
    invocation_id: str,
    completion: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": run_dir.name,
        "engine": "official-cadgenbench-baseline",
        "compatibility": {"recover_unterminated_code_fence": True},
        "cadcopilot_version": __version__,
        "git_revision": _git_revision(cwd),
        "environment": _package_environment(),
        "dataset_repo": config.data_repo,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "status": status,
        "exit_code": exit_code,
        "invocation_id": invocation_id,
        "command": list(command),
        "completion": completion,
        "request": {
            "all": config.run_all,
            "fixtures": list(config.fixtures),
            "model": config.model,
            "backend": config.backend,
            "parallel": config.parallel,
            "max_iterations": config.max_iterations,
            "max_tokens": config.max_tokens,
            "max_tokens_per_call": config.max_tokens_per_call,
            "max_duration": config.max_duration,
            "reasoning_effort": config.reasoning_effort,
        },
        "official_params": read_json(run_dir / "params.json") or {},
    }


def _run_directories(root: Path) -> set[Path]:
    if not root.is_dir():
        return set()
    return {
        child.resolve()
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    }


def _locate_created_run(output_root: Path, before: set[Path]) -> Path | None:
    created = _run_directories(output_root) - before
    if not created:
        return None
    with_params = [path for path in created if (path / "params.json").is_file()]
    candidates = with_params or list(created)
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def _stream_subprocess(
    command: Sequence[str],
    *,
    cwd: Path | None,
    environment: Mapping[str, str],
    log_path: Path,
) -> int:
    """Stream a child process to the terminal and a durable UTF-8 log."""
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    try:
        with log_path.open("w", encoding="utf-8") as log:
            if process.stdout is not None:
                for line in process.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        raise


def _completion_summary(run_dir: Path) -> dict[str, object]:
    params = read_json(run_dir / "params.json") or {}
    raw_fixtures = params.get("fixtures")
    fixtures = [item for item in raw_fixtures or [] if isinstance(item, str)]
    missing: list[str] = []
    invalid: list[str] = []
    traces: list[dict[str, object]] = []
    stop_reasons: dict[str, str] = {}
    for fixture in fixtures:
        candidate, _ = candidate_for(run_dir / fixture)
        if candidate is None:
            missing.append(fixture)
            continue
        result = read_json(run_dir / fixture / "result.json")
        if result is not None and result.get("status") not in {None, "valid"}:
            invalid.append(fixture)
        trace = read_json(run_dir / fixture / "trace.json")
        if trace is not None:
            traces.append(trace)
            reason = trace.get("stopped_reason")
            if isinstance(reason, str):
                stop_reasons[fixture] = reason
    token_total = sum(
        value
        for trace in traces
        if isinstance((value := trace.get("total_tokens")), int) and not isinstance(value, bool)
    )
    turn_total = sum(
        len(turns)
        for trace in traces
        if isinstance((turns := trace.get("turns")), list)
    )
    return {
        "expected_count": len(fixtures),
        "candidate_count": len(fixtures) - len(missing),
        "missing_fixtures": missing,
        "invalid_fixtures": invalid,
        "complete": bool(fixtures) and not missing and not invalid,
        "trace_count": len(traces),
        "total_tokens": token_total if traces else None,
        "total_turns": turn_total if traces else None,
        "stop_reasons": stop_reasons,
    }


def _finish_invocation_files(
    state_path: Path,
    log_path: Path,
    run_dir: Path,
    state: dict[str, object],
) -> None:
    write_json_atomic(state_path, state)
    state_path.replace(run_dir / "harness_invocation.json")
    if log_path.exists():
        log_path.replace(run_dir / "harness.log")


def run_official_baseline(
    config: CadgenbenchRunConfig,
    *,
    command_prefix: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Path:
    """Run the official baseline and return its newly-created run directory."""
    output_root = config.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    before = _run_directories(output_root)
    command = build_baseline_command(config, command_prefix=command_prefix)
    environment = dict(os.environ)
    if environ is not None:
        environment.update(environ)
    environment["CADGENBENCH_DATA_REPO"] = config.data_repo
    environment["PYTHONUNBUFFERED"] = "1"
    started = datetime.now(timezone.utc)
    monotonic_start = time.monotonic()
    run_cwd = cwd or Path.cwd()
    invocation_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
    harness_dir = output_root / ".harness"
    harness_dir.mkdir(exist_ok=True)
    state_path = harness_dir / f"{invocation_id}.json"
    log_path = harness_dir / f"{invocation_id}.log"
    invocation: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "status": "running",
        "started_at": started.isoformat(),
        "command": command,
        "fixtures": list(config.fixtures),
        "all": config.run_all,
        "log": log_path.name,
    }
    write_json_atomic(state_path, invocation)
    try:
        exit_code = _stream_subprocess(
            command,
            cwd=cwd,
            environment=environment,
            log_path=log_path,
        )
    except KeyboardInterrupt as exc:
        run_dir = _locate_created_run(output_root, before)
        invocation.update(
            {
                "status": "interrupted",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": 130,
            }
        )
        if run_dir is not None:
            manifest = _manifest_payload(
                config,
                run_dir,
                started=started,
                duration_seconds=time.monotonic() - monotonic_start,
                status="interrupted",
                exit_code=130,
                cwd=run_cwd,
                command=command,
                invocation_id=invocation_id,
                completion=_completion_summary(run_dir),
            )
            write_json_atomic(run_dir / "manifest.json", manifest)
            _finish_invocation_files(state_path, log_path, run_dir, invocation)
            raise RuntimeError(f"CADGenBench run interrupted; partial run: {run_dir}") from exc
        write_json_atomic(state_path, invocation)
        raise RuntimeError("CADGenBench run interrupted before creating a run directory") from exc
    except OSError as exc:
        invocation.update(
            {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
            }
        )
        write_json_atomic(state_path, invocation)
        raise RuntimeError(
            "could not start the official CADGenBench CLI; install the benchmark "
            "with its baseline dependencies or pass a valid command"
        ) from exc
    run_dir = _locate_created_run(output_root, before)
    if run_dir is None:
        invocation.update(
            {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "exit_code": exit_code,
                "error": "child exited without creating a run directory",
            }
        )
        write_json_atomic(state_path, invocation)
        raise RuntimeError(
            f"CADGenBench exited with code {exit_code} without creating a run directory"
        )

    completion = _completion_summary(run_dir)
    process_succeeded = exit_code == 0
    run_complete = completion["complete"] is True
    status = "completed" if process_succeeded and run_complete else "partial"
    if not process_succeeded:
        status = "failed"
    manifest = _manifest_payload(
        config,
        run_dir,
        started=started,
        duration_seconds=time.monotonic() - monotonic_start,
        status=status,
        exit_code=exit_code,
        cwd=run_cwd,
        command=command,
        invocation_id=invocation_id,
        completion=completion,
    )
    write_json_atomic(run_dir / "manifest.json", manifest)
    invocation.update(
        {
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": exit_code,
            "run_dir": str(run_dir),
        }
    )
    _finish_invocation_files(state_path, log_path, run_dir, invocation)
    if exit_code != 0:
        raise RuntimeError(
            f"CADGenBench baseline failed with exit code {exit_code}; partial run: {run_dir}"
        )
    if not run_complete:
        raise RuntimeError(f"CADGenBench exited successfully but the run is partial: {run_dir}")
    return run_dir
