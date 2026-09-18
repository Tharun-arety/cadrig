"""Verify CADGenBench run completeness and candidate validity."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .common import candidate_for, read_json, sha256_file, write_json_atomic
from .dataset import expected_tasks, find_sanity_script


@dataclass(frozen=True)
class TaskVerification:
    task_id: str
    candidate: str | None
    status: str
    size_bytes: int
    message: str | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class VerificationReport:
    run_dir: str
    scope: str
    expected_count: int
    candidate_count: int
    valid_count: int
    missing_count: int
    invalid_count: int
    sanity_checked: bool
    complete: bool
    passed: bool
    tasks: tuple[TaskVerification, ...]
    generated_at: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["tasks"] = [asdict(item) for item in self.tasks]
        return payload


def _scope(run_dir: Path) -> str:
    manifest = read_json(run_dir / "manifest.json") or {}
    request = manifest.get("request")
    if isinstance(request, dict) and request.get("all") is True:
        return "all"
    return "partial"


def verify_run(
    run_dir: Path,
    *,
    dataset_dir: Path | None = None,
    explicit_tasks: tuple[str, ...] = (),
    sanity_script: Path | None = None,
    sanity_command: Sequence[str] | None = None,
    require_sanity: bool = False,
) -> VerificationReport:
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise ValueError(f"run directory not found: {run_dir}")
    tasks = expected_tasks(run_dir, dataset_dir=dataset_dir, explicit=explicit_tasks)
    if not tasks:
        raise ValueError(f"no CADGenBench task directories found under {run_dir}")

    resolved_script = sanity_script
    if resolved_script is None and (require_sanity or dataset_dir is not None):
        resolved_script = find_sanity_script(dataset_dir)
    command_prefix: list[str] | None
    if sanity_command is not None:
        command_prefix = list(sanity_command)
    elif resolved_script is not None:
        command_prefix = [sys.executable, str(resolved_script)]
    else:
        command_prefix = None
    if require_sanity and command_prefix is None:
        raise ValueError("the official sanity checker is required but was not found")

    results: list[TaskVerification] = []
    for task_id in tasks:
        candidate, error = candidate_for(run_dir / task_id)
        if candidate is None:
            results.append(TaskVerification(task_id, None, "missing", 0, error))
            continue
        size = candidate.stat().st_size
        if command_prefix is None:
            results.append(TaskVerification(task_id, candidate.name, "present", size))
            continue
        try:
            completed = subprocess.run(
                [*command_prefix, str(candidate)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            output = "\n".join(
                part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
            )
            valid = completed.returncode == 0
        except subprocess.TimeoutExpired:
            output = "official sanity checker timed out after 120 seconds"
            valid = False
        except OSError as exc:
            output = f"could not run official sanity checker: {exc}"
            valid = False
        if len(output) > 2000:
            output = output[:2000] + "..."
        results.append(
            TaskVerification(
                task_id,
                candidate.name,
                "valid" if valid else "invalid",
                size,
                message=output or None,
                sha256=sha256_file(candidate),
            )
        )

    missing = sum(item.status == "missing" for item in results)
    invalid = sum(item.status == "invalid" for item in results)
    candidate_count = len(results) - missing
    sanity_checked = command_prefix is not None
    accepted_statuses = {"valid"} if sanity_checked else {"present"}
    valid_count = sum(item.status in accepted_statuses for item in results)
    complete = missing == 0
    passed = complete and invalid == 0 and (sanity_checked or not require_sanity)
    report = VerificationReport(
        run_dir=str(run_dir),
        scope=_scope(run_dir),
        expected_count=len(tasks),
        candidate_count=candidate_count,
        valid_count=valid_count,
        missing_count=missing,
        invalid_count=invalid,
        sanity_checked=sanity_checked,
        complete=complete,
        passed=passed,
        tasks=tuple(results),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    write_json_atomic(run_dir / "verification.json", report.to_dict())
    return report
