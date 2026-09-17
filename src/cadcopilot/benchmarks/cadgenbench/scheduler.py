"""Resumable, budget-enforced scheduling for CADGenBench cohorts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .common import candidate_for, read_json, sha256_file, validate_task_id, write_json_atomic
from .dataset import discover_dataset_tasks, find_sanity_script
from .runner import DEFAULT_DATA_REPO, CadgenbenchRunConfig, run_official_baseline
from .sanity import verify_run

COHORT_SCHEMA_VERSION = "1.0.0"
_MILLION = Decimal(1_000_000)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CadgenbenchCohortConfig:
    """Immutable generation and budget policy for a resumable cohort."""

    cohort_dir: Path
    fixtures: tuple[str, ...]
    model: str
    total_token_budget: int
    max_tokens_per_task: int
    backend: str = "build123d"
    max_iterations: int | None = None
    max_tokens_per_call: int | None = None
    max_duration: float | None = None
    reasoning_effort: str | None = None
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None
    total_cost_budget_usd: float | None = None
    dataset_dir: Path | None = None
    sanity_script: Path | None = None
    data_repo: str = DEFAULT_DATA_REPO

    def __post_init__(self) -> None:
        if not self.fixtures:
            raise ValueError("fixtures must not be empty")
        if len(set(self.fixtures)) != len(self.fixtures):
            raise ValueError("fixtures must not contain duplicates")
        for task_id in self.fixtures:
            validate_task_id(task_id)
        if not self.model.strip():
            raise ValueError("model must not be blank")
        if self.total_token_budget <= 0 or self.max_tokens_per_task <= 0:
            raise ValueError("token budgets must be greater than zero")
        if self.max_tokens_per_task > self.total_token_budget:
            raise ValueError("max_tokens_per_task exceeds total_token_budget")
        for name in ("max_iterations", "max_tokens_per_call", "max_duration"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        rates = (self.input_usd_per_million, self.output_usd_per_million)
        if (rates[0] is None) != (rates[1] is None):
            raise ValueError("input and output rates must be supplied together")
        for name, value in (
            ("input_usd_per_million", rates[0]),
            ("output_usd_per_million", rates[1]),
            ("total_cost_budget_usd", self.total_cost_budget_usd),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.total_cost_budget_usd is not None and rates[0] is None:
            raise ValueError("a cost budget requires explicit input and output rates")

    def fingerprint_payload(self) -> dict[str, Any]:
        """Return the canonical policy payload; filesystem destination is excluded."""
        payload = asdict(self)
        payload.pop("cohort_dir")
        for name in ("dataset_dir", "sanity_script"):
            value = payload[name]
            payload[name] = str(Path(value).resolve()) if value is not None else None
        payload["fixtures"] = list(self.fixtures)
        return payload

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.fingerprint_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CohortResult:
    cohort_dir: Path
    status: str
    completed: int
    failed: int
    pending: int
    total_tokens: int
    cost_usd: float | None


def _trace_usage(run_dir: Path, task_id: str) -> dict[str, Any]:
    trace = read_json(run_dir / task_id / "trace.json") or {}
    turns = trace.get("turns") if isinstance(trace.get("turns"), list) else []
    prompt = sum(
        item.get("prompt_tokens", 0)
        for item in turns
        if isinstance(item, dict) and isinstance(item.get("prompt_tokens", 0), int)
    )
    completion = sum(
        item.get("completion_tokens", 0)
        for item in turns
        if isinstance(item, dict) and isinstance(item.get("completion_tokens", 0), int)
    )
    reported = trace.get("total_tokens", 0)
    total = max(prompt + completion, reported if isinstance(reported, int) else 0)
    return {
        "trace_found": bool(trace),
        "prompt_tokens": max(prompt, 0),
        "completion_tokens": max(completion, 0),
        "unclassified_tokens": max(total - prompt - completion, 0),
        "total_tokens": max(total, 0),
    }


def _usage_cost(usage: dict[str, Any], config: CadgenbenchCohortConfig) -> Decimal | None:
    if config.input_usd_per_million is None or config.output_usd_per_million is None:
        return None
    input_rate = Decimal(str(config.input_usd_per_million))
    output_rate = Decimal(str(config.output_usd_per_million))
    # Unknown provider token categories are charged at the higher rate.
    return (
        Decimal(usage["prompt_tokens"]) * input_rate
        + Decimal(usage["completion_tokens"]) * output_rate
        + Decimal(usage["unclassified_tokens"]) * max(input_rate, output_rate)
    ) / _MILLION


def _recalculate_usage(state: dict[str, Any], config: CadgenbenchCohortConfig) -> None:
    prompt = completion = unclassified = total = 0
    cost = Decimal(0)
    has_rates = config.input_usd_per_million is not None
    debited_tokens = 0
    debited_cost = Decimal(0)
    for task in state["tasks"].values():
        for attempt in task["attempts"]:
            usage = attempt.get("usage", {})
            prompt += usage.get("prompt_tokens", 0)
            completion += usage.get("completion_tokens", 0)
            unclassified += usage.get("unclassified_tokens", 0)
            total += usage.get("total_tokens", 0)
            attempt_cost = _usage_cost(usage, config)
            if attempt_cost is not None:
                attempt["cost_usd"] = float(attempt_cost)
                cost += attempt_cost
            debited_tokens += attempt.get("budget_debit_tokens", usage.get("total_tokens", 0))
            if has_rates:
                debited_cost += Decimal(
                    str(attempt.get("budget_debit_cost_usd", attempt.get("cost_usd", 0)))
                )
    state["usage"] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "unclassified_tokens": unclassified,
        "total_tokens": total,
        "cost_usd": float(cost) if has_rates else None,
    }
    state["budget_accounting"] = {
        "debited_tokens": debited_tokens,
        "unverified_reserved_tokens": max(debited_tokens - total, 0),
        "debited_cost_usd": float(debited_cost) if has_rates else None,
        "unverified_reserved_cost_usd": float(max(debited_cost - cost, Decimal(0)))
        if has_rates
        else None,
    }


def _initial_state(config: CadgenbenchCohortConfig) -> dict[str, Any]:
    created = _now()
    return {
        "schema_version": COHORT_SCHEMA_VERSION,
        "config_fingerprint": config.fingerprint,
        "config": config.fingerprint_payload(),
        "status": "ready",
        "created_at": created,
        "updated_at": created,
        "tasks": {
            task_id: {"status": "pending", "attempts": [], "candidate_sha256": None}
            for task_id in config.fixtures
        },
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "unclassified_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0 if config.input_usd_per_million is not None else None,
        },
        "budget_accounting": {
            "debited_tokens": 0,
            "unverified_reserved_tokens": 0,
            "debited_cost_usd": 0.0
            if config.input_usd_per_million is not None
            else None,
            "unverified_reserved_cost_usd": 0.0
            if config.input_usd_per_million is not None
            else None,
        },
    }


def _save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now()
    write_json_atomic(path, state)


def _load_state(config: CadgenbenchCohortConfig) -> tuple[Path, dict[str, Any]]:
    cohort_dir = config.cohort_dir.resolve()
    existed = cohort_dir.exists()
    cohort_dir.mkdir(parents=True, exist_ok=True)
    state_path = cohort_dir / "cohort.json"
    state = read_json(state_path)
    if state is None:
        if state_path.exists():
            raise ValueError(f"cohort state is corrupt or unreadable: {state_path}")
        if existed and any(cohort_dir.iterdir()):
            raise ValueError(
                "refusing to initialize a nonempty cohort directory without cohort.json"
            )
        state = _initial_state(config)
        _save_state(state_path, state)
    elif state.get("config_fingerprint") != config.fingerprint:
        raise ValueError(
            "cohort configuration does not match its saved fingerprint; "
            "use the original configuration or a new cohort directory"
        )
    return state_path, state


def _strict_checker(config: CadgenbenchCohortConfig) -> Path:
    script = config.sanity_script or find_sanity_script(config.dataset_dir)
    if script is None or not script.is_file():
        raise ValueError("official CADGenBench sanity checker is required but was not found")
    return script.resolve()


def _verify_completed_task(
    cohort_dir: Path,
    task_id: str,
    task: dict[str, Any],
    config: CadgenbenchCohortConfig,
    checker: Path,
) -> None:
    report = verify_run(
        cohort_dir,
        explicit_tasks=(task_id,),
        sanity_script=checker,
        require_sanity=True,
    )
    candidate, error = candidate_for(cohort_dir / task_id)
    if not report.passed or candidate is None:
        raise RuntimeError(f"completed task {task_id} failed resume verification: {error or 'invalid'}")
    expected_hash = task.get("candidate_sha256")
    if not expected_hash or sha256_file(candidate) != expected_hash:
        raise RuntimeError(f"completed task {task_id} candidate hash changed")


def _reserve_allowed(state: dict[str, Any], config: CadgenbenchCohortConfig) -> str | None:
    used_tokens = state["budget_accounting"]["debited_tokens"]
    if used_tokens + config.max_tokens_per_task > config.total_token_budget:
        return "token_budget"
    if config.total_cost_budget_usd is not None:
        max_rate = max(
            Decimal(str(config.input_usd_per_million)),
            Decimal(str(config.output_usd_per_million)),
        )
        reserve = Decimal(config.max_tokens_per_task) * max_rate / _MILLION
        used_cost = Decimal(str(state["budget_accounting"]["debited_cost_usd"] or 0))
        if used_cost + reserve > Decimal(str(config.total_cost_budget_usd)):
            return "cost_budget"
    return None


def _cost_reservation(config: CadgenbenchCohortConfig) -> float | None:
    if config.input_usd_per_million is None or config.output_usd_per_million is None:
        return None
    maximum_rate = max(
        Decimal(str(config.input_usd_per_million)),
        Decimal(str(config.output_usd_per_million)),
    )
    return float(Decimal(config.max_tokens_per_task) * maximum_rate / _MILLION)


def _attempt_run_dir(attempt_root: Path) -> Path | None:
    if not attempt_root.is_dir():
        return None
    children = [path for path in attempt_root.iterdir() if path.is_dir() and path.name != ".harness"]
    return max(children, key=lambda path: path.stat().st_mtime_ns) if children else None


def _reconcile_abandoned_attempts(
    state: dict[str, Any], config: CadgenbenchCohortConfig
) -> set[str]:
    """Close attempts left running by a crash while retaining their full reservation."""
    abandoned: set[str] = set()
    for task_id, task in state["tasks"].items():
        for attempt in task["attempts"]:
            if attempt.get("status") != "running":
                continue
            attempt_root = (
                config.cohort_dir / ".attempts" / task_id / str(attempt["attempt_id"])
            )
            run_dir = _attempt_run_dir(attempt_root)
            if run_dir is not None:
                attempt["run_dir"] = str(run_dir.relative_to(config.cohort_dir))
                attempt["usage"] = _trace_usage(run_dir, task_id)
            attempt["status"] = "abandoned"
            attempt["error"] = "previous scheduler process ended before attempt reconciliation"
            attempt["finished_at"] = _now()
            # The exact final provider usage is unknowable after a crash. Keeping the
            # original reservation makes the cross-run upper bound real and auditable.
            attempt["budget_debit_tokens"] = config.max_tokens_per_task
            attempt["budget_debit_cost_usd"] = _cost_reservation(config)
            task["status"] = "failed"
            abandoned.add(task_id)
    _recalculate_usage(state, config)
    return abandoned


def _publish_task(source: Path, destination: Path, provenance: dict[str, Any]) -> None:
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite published cohort task: {destination.name}")
    staging = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging)
    write_json_atomic(staging / "cohort_attempt.json", provenance)
    staging.replace(destination)


def _write_cohort_metadata(config: CadgenbenchCohortConfig, state: dict[str, Any]) -> None:
    dataset_tasks = (
        discover_dataset_tasks(config.dataset_dir) if config.dataset_dir is not None else ()
    )
    is_all = bool(dataset_tasks) and tuple(sorted(config.fixtures)) == tuple(
        sorted(dataset_tasks)
    )
    write_json_atomic(config.cohort_dir / "params.json", {"fixtures": list(config.fixtures)})
    write_json_atomic(
        config.cohort_dir / "manifest.json",
        {
            "schema_version": COHORT_SCHEMA_VERSION,
            "engine": "cadrig-resumable-cohort",
            "status": state["status"],
            "request": {"all": is_all, "fixtures": list(config.fixtures)},
            "config_fingerprint": config.fingerprint,
            "usage": state["usage"],
        },
    )


@contextmanager
def _cohort_lock(cohort_dir: Path):
    cohort_dir = cohort_dir.resolve()
    cohort_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_path = cohort_dir.parent / f".{cohort_dir.name}.cohort.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        lock = read_json(lock_path)
        pid = lock.get("pid") if lock is not None else None
        if not isinstance(pid, int) or _pid_is_alive(pid):
            raise RuntimeError(
                f"cohort is already locked by another scheduler: {lock_path}"
            ) from exc
        lock_path.unlink()
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as retry_exc:
            raise RuntimeError(
                f"cohort lock was acquired concurrently: {lock_path}"
            ) from retry_exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "created_at": _now()}))
        yield
    finally:
        lock_path.unlink(missing_ok=True)


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


def _run_cohort_locked(
    config: CadgenbenchCohortConfig,
    *,
    command_prefix: tuple[str, ...] | None = None,
    cwd: Path | None = None,
) -> CohortResult:
    """Run or resume a cohort without ever admitting work beyond its budgets."""
    config = CadgenbenchCohortConfig(**{**asdict(config), "cohort_dir": config.cohort_dir.resolve()})
    checker = _strict_checker(config)  # Refuse before any model call.
    state_path, state = _load_state(config)
    attempted_now = _reconcile_abandoned_attempts(state, config)
    state["status"] = "running"
    _save_state(state_path, state)

    for task_id in config.fixtures:
        task = state["tasks"][task_id]
        if task["status"] == "completed":
            _verify_completed_task(config.cohort_dir, task_id, task, config, checker)
            continue
        if task_id in attempted_now:
            continue
        budget_stop = _reserve_allowed(state, config)
        if budget_stop is not None:
            state["status"] = f"stopped_{budget_stop}"
            break

        attempted_now.add(task_id)
        attempt_number = len(task["attempts"]) + 1
        attempt_id = f"{attempt_number:03d}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        attempt_root = config.cohort_dir / ".attempts" / task_id / attempt_id
        attempt: dict[str, Any] = {
            "attempt_id": attempt_id,
            "status": "running",
            "started_at": _now(),
            "run_dir": None,
            "reserved_tokens": config.max_tokens_per_task,
            "reserved_cost_usd": _cost_reservation(config),
            "budget_debit_tokens": config.max_tokens_per_task,
            "budget_debit_cost_usd": _cost_reservation(config),
            "usage": {
                "trace_found": False,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "unclassified_tokens": 0,
                "total_tokens": 0,
            },
        }
        task["status"] = "running"
        task["attempts"].append(attempt)
        _recalculate_usage(state, config)
        _save_state(state_path, state)  # Durable reservation before provider access.

        run_dir: Path | None = None
        error: str | None = None
        try:
            run_dir = run_official_baseline(
                CadgenbenchRunConfig(
                    output_root=attempt_root,
                    fixtures=(task_id,),
                    model=config.model,
                    backend=config.backend,
                    parallel=1,
                    max_iterations=config.max_iterations,
                    max_tokens=config.max_tokens_per_task,
                    max_tokens_per_call=config.max_tokens_per_call,
                    max_duration=config.max_duration,
                    reasoning_effort=config.reasoning_effort,
                    data_repo=config.data_repo,
                ),
                command_prefix=command_prefix,
                environ={
                    "CADCOPILOT_ATTEMPT_TOKEN_CAP": str(config.max_tokens_per_task),
                },
                cwd=cwd,
            )
        except RuntimeError as exc:
            error = str(exc)
            run_dir = _attempt_run_dir(attempt_root)

        if run_dir is not None:
            attempt["run_dir"] = str(run_dir.relative_to(config.cohort_dir))
            attempt["usage"] = _trace_usage(run_dir, task_id)
        if attempt["usage"].get("trace_found"):
            attempt["budget_debit_tokens"] = attempt["usage"]["total_tokens"]
            actual_cost = _usage_cost(attempt["usage"], config)
            attempt["budget_debit_cost_usd"] = (
                float(actual_cost) if actual_cost is not None else None
            )
        _recalculate_usage(state, config)

        if error is None and run_dir is not None:
            verification = verify_run(
                run_dir,
                explicit_tasks=(task_id,),
                sanity_script=checker,
                require_sanity=True,
            )
            attempt["verification"] = verification.to_dict()
            if verification.passed:
                source = run_dir / task_id
                source_candidate, source_error = candidate_for(source)
                if source_candidate is None:
                    error = source_error or "verified source candidate is missing"
                    attempt["status"] = "failed"
                    attempt["error"] = error
                    task["status"] = "failed"
                    attempt["finished_at"] = _now()
                    _save_state(state_path, state)
                    continue
                attempt["candidate_sha256"] = sha256_file(source_candidate)
                attempt["status"] = "completed"
                attempt["finished_at"] = _now()
                _publish_task(source, config.cohort_dir / task_id, attempt)
                candidate, candidate_error = candidate_for(config.cohort_dir / task_id)
                if candidate is None:
                    error = candidate_error or "published candidate is missing"
                else:
                    task["candidate_sha256"] = sha256_file(candidate)
                    task["status"] = "completed"
            else:
                error = "strict candidate verification failed"
        if error is not None:
            attempt["status"] = "failed"
            attempt["error"] = error
            task["status"] = "failed"
        attempt.setdefault("finished_at", _now())
        _save_state(state_path, state)

    _recalculate_usage(state, config)
    statuses = [task["status"] for task in state["tasks"].values()]
    if all(status == "completed" for status in statuses):
        state["status"] = "completed"
    elif state["status"] == "running":
        state["status"] = "partial"
    _save_state(state_path, state)
    _write_cohort_metadata(config, state)
    verify_run(
        config.cohort_dir,
        explicit_tasks=config.fixtures,
        sanity_script=checker,
        require_sanity=True,
    )
    return CohortResult(
        cohort_dir=config.cohort_dir,
        status=state["status"],
        completed=statuses.count("completed"),
        failed=statuses.count("failed"),
        pending=len(statuses) - statuses.count("completed") - statuses.count("failed"),
        total_tokens=state["usage"]["total_tokens"],
        cost_usd=state["usage"]["cost_usd"],
    )


def run_cohort(
    config: CadgenbenchCohortConfig,
    *,
    command_prefix: tuple[str, ...] | None = None,
    cwd: Path | None = None,
) -> CohortResult:
    """Run or resume a cohort under an exclusive cross-process lock."""
    with _cohort_lock(config.cohort_dir):
        return _run_cohort_locked(config, command_prefix=command_prefix, cwd=cwd)
