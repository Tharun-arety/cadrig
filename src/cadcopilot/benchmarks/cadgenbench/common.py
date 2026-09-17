"""Shared filesystem helpers for the CADGenBench integration."""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANDIDATE_NAMES = ("output.step", "output.stp")
MANIFEST_SCHEMA_VERSION = "1.0.0"
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.windll.kernel32
        open_process = kernel32.OpenProcess
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            return False
        close_handle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextmanager
def exclusive_process_lock(target_dir: Path, label: str):
    """Acquire a crash-recoverable same-host lock beside a target directory."""

    target_dir = target_dir.resolve()
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target_dir.parent / f".{target_dir.name}.{label}.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        lock = read_json(lock_path)
        pid = lock.get("pid") if lock is not None else None
        if not isinstance(pid, int) or _pid_is_alive(pid):
            raise RuntimeError(f"{label} is already locked: {lock_path}") from exc
        lock_path.unlink()
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as retry_exc:
            raise RuntimeError(f"{label} lock was acquired concurrently: {lock_path}") from retry_exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            )
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def validate_task_id(task_id: str) -> str:
    if not _SAFE_TASK_ID.fullmatch(task_id):
        raise ValueError(f"unsafe CADGenBench task id: {task_id!r}")
    return task_id


def candidate_for(task_dir: Path) -> tuple[Path | None, str | None]:
    matches = [
        task_dir / name
        for name in CANDIDATE_NAMES
        if (task_dir / name).exists() or (task_dir / name).is_symlink()
    ]
    if len(matches) > 1:
        return None, "multiple candidates found (output.step and output.stp)"
    if not matches:
        return None, "candidate file is missing"
    candidate = matches[0]
    if candidate.is_symlink():
        return None, "candidate must not be a symbolic link"
    if not candidate.is_file():
        return None, "candidate is not a regular file"
    if candidate.resolve().parent != task_dir.resolve():
        return None, "candidate escapes its task directory"
    if candidate.stat().st_size <= 0:
        return None, "candidate file is empty"
    return candidate, None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def task_directories(run_dir: Path) -> list[Path]:
    return sorted(
        (child for child in run_dir.iterdir() if child.is_dir() and _SAFE_TASK_ID.fullmatch(child.name)),
        key=lambda path: path.name,
    )
