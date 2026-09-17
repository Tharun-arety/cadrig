"""Create a CADGenBench leaderboard submission from a verified run."""

from __future__ import annotations

import getpass
import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .common import candidate_for, sha256_file
from .sanity import VerificationReport, verify_run


@dataclass(frozen=True)
class PackageResult:
    output: Path
    fixture_count: int
    candidate_count: int
    meta: dict[str, object]
    verification: VerificationReport


def package_run(
    run_dir: Path,
    *,
    output: Path | None = None,
    submitter: str | None = None,
    submission_name: str | None = None,
    agent_url: str | None = None,
    notes: str | None = None,
    agree_to_publish: bool = False,
    allow_incomplete: bool = False,
    dataset_dir: Path | None = None,
    sanity_script: Path | None = None,
    require_sanity: bool = True,
) -> PackageResult:
    run_dir = run_dir.resolve()
    report = verify_run(
        run_dir,
        dataset_dir=dataset_dir,
        sanity_script=sanity_script,
        require_sanity=require_sanity or not allow_incomplete,
    )
    if not allow_incomplete and (not report.passed or report.scope != "all"):
        detail = (
            f"scope={report.scope}, missing={report.missing_count}, "
            f"invalid={report.invalid_count}, sanity_checked={report.sanity_checked}"
        )
        raise ValueError(f"refusing to package an incomplete CADGenBench run ({detail})")
    if notes is not None and len(notes) > 500:
        raise ValueError("notes must not exceed 500 characters")
    if not allow_incomplete and not agree_to_publish:
        raise ValueError("production packaging requires explicit publication consent")

    meta: dict[str, object] = {
        "submitter_name": submitter or getpass.getuser(),
        "submission_name": submission_name or f"CADRIG Alpha ({run_dir.name})",
        "agent_url": agent_url,
        "notes": notes,
        "agree_to_publish": agree_to_publish,
    }
    output_path = (output or run_dir.with_suffix(".zip")).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.{os.getpid()}.tmp")
    candidates = 0
    expected_names = {"meta.json"}
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("meta.json", json.dumps(meta, indent=2) + "\n")
            for task in report.tasks:
                directory_name = f"{task.task_id}/"
                archive.writestr(directory_name, "")
                expected_names.add(directory_name)
                candidate, _ = candidate_for(run_dir / task.task_id)
                if candidate is not None:
                    if task.sha256 is not None and sha256_file(candidate) != task.sha256:
                        raise ValueError(f"candidate changed after verification: {task.task_id}")
                    candidate_name = f"{task.task_id}/{candidate.name}"
                    archive.write(candidate, arcname=candidate_name)
                    expected_names.add(candidate_name)
                    candidates += 1
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("submission ZIP failed its CRC integrity check")
            if set(archive.namelist()) != expected_names:
                raise ValueError("submission ZIP layout differs from the verified task set")
            json.loads(archive.read("meta.json"))
        temporary.replace(output_path)
    finally:
        if temporary.exists():
            temporary.unlink()

    return PackageResult(output_path, len(report.tasks), candidates, meta, report)
