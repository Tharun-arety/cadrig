"""Resumable paired model-by-kernel calibration matrices."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .common import (
    exclusive_process_lock,
    read_json,
    validate_task_id,
    write_json_atomic,
)
from .harness_eval import evaluate_harness
from .runner import DEFAULT_DATA_REPO
from .scheduler import CadgenbenchCohortConfig, run_cohort

MATRIX_SCHEMA_VERSION = "1.0.0"
_MILLION = Decimal(1_000_000)
_SAFE_SLUG = re.compile(r"[^A-Za-z0-9._-]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ModelPricing:
    """Explicit provider rates and admission limit for one model in each cell."""

    model: str
    input_usd_per_million: float
    output_usd_per_million: float
    cost_budget_usd_per_cell: float

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("pricing model must not be blank")
        for name in (
            "input_usd_per_million",
            "output_usd_per_million",
            "cost_budget_usd_per_cell",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")


@dataclass(frozen=True)
class CadgenbenchMatrixConfig:
    """Immutable policy for paired cohorts across models and CAD kernels."""

    matrix_dir: Path
    fixtures: tuple[str, ...]
    models: tuple[str, ...]
    backends: tuple[str, ...]
    token_budget_per_cell: int
    max_tokens_per_task: int
    max_iterations: int | None = None
    max_tokens_per_call: int | None = None
    max_duration: float | None = None
    reasoning_effort: str | None = None
    pricing: tuple[ModelPricing, ...] = ()
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
        if not self.models or any(not model.strip() for model in self.models):
            raise ValueError("models must not be empty or blank")
        if len(set(self.models)) != len(self.models):
            raise ValueError("models must not contain duplicates")
        if not self.backends or any(not backend.strip() for backend in self.backends):
            raise ValueError("backends must not be empty or blank")
        if len(set(self.backends)) != len(self.backends):
            raise ValueError("backends must not contain duplicates")
        unsupported = sorted(set(self.backends) - {"build123d", "cadquery"})
        if unsupported:
            raise ValueError(f"unsupported backends: {', '.join(unsupported)}")
        if self.max_tokens_per_task <= 0 or self.token_budget_per_cell <= 0:
            raise ValueError("token budgets must be greater than zero")
        minimum_cell_budget = len(self.fixtures) * self.max_tokens_per_task
        if self.token_budget_per_cell < minimum_cell_budget:
            raise ValueError(
                "token_budget_per_cell must admit the full paired fixture set; "
                f"minimum is {minimum_cell_budget}"
            )
        for name in ("max_iterations", "max_tokens_per_call", "max_duration"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        pricing_models = [item.model for item in self.pricing]
        if len(set(pricing_models)) != len(pricing_models):
            raise ValueError("pricing must not contain duplicate models")
        if self.pricing and set(pricing_models) != set(self.models):
            raise ValueError("pricing must cover every matrix model exactly once")
        for item in self.pricing:
            maximum_rate = max(
                Decimal(str(item.input_usd_per_million)),
                Decimal(str(item.output_usd_per_million)),
            )
            minimum_cost = Decimal(minimum_cell_budget) * maximum_rate / _MILLION
            if Decimal(str(item.cost_budget_usd_per_cell)) < minimum_cost:
                raise ValueError(
                    f"cost budget for {item.model} cannot admit the full fixture set; "
                    f"minimum is {minimum_cost}"
                )

    def fingerprint_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("matrix_dir")
        payload["fixtures"] = list(self.fixtures)
        payload["models"] = list(self.models)
        payload["backends"] = list(self.backends)
        payload["pricing"] = [asdict(item) for item in self.pricing]
        for name in ("dataset_dir", "sanity_script"):
            value = payload[name]
            payload[name] = str(Path(value).resolve()) if value is not None else None
        return payload

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.fingerprint_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def maximum_admitted_tokens(self) -> int:
        return len(self.models) * len(self.backends) * self.token_budget_per_cell


@dataclass(frozen=True)
class MatrixResult:
    matrix_dir: Path
    status: str
    cell_count: int
    completed_cells: int
    incomplete_cells: int
    total_tokens: int
    debited_tokens: int
    cost_usd: float | None
    harness_evaluation: Path | None


def load_model_pricing(path: Path) -> tuple[ModelPricing, ...]:
    """Load explicit per-model rates from a versionable JSON policy file."""

    payload = read_json(path)
    if payload is None:
        raise ValueError(f"pricing file is invalid or unreadable: {path.resolve()}")
    pricing: list[ModelPricing] = []
    for model, raw in sorted(payload.items()):
        entry = raw if isinstance(raw, dict) else {}
        try:
            pricing.append(
                ModelPricing(
                    model=model,
                    input_usd_per_million=float(entry["input_usd_per_million"]),
                    output_usd_per_million=float(entry["output_usd_per_million"]),
                    cost_budget_usd_per_cell=float(entry["cost_budget_usd_per_cell"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"pricing entry for {model} is incomplete or invalid") from exc
    return tuple(pricing)


def _cell_id(model_index: int, model: str, backend_index: int, backend: str) -> str:
    readable = _SAFE_SLUG.sub("-", model).strip("-._")[:32] or "model"
    identity = hashlib.sha256(f"{model}\0{backend}".encode()).hexdigest()[:8]
    return f"m{model_index + 1:02d}-{readable}-k{backend_index + 1:02d}-{backend}-{identity}"


def _cells(config: CadgenbenchMatrixConfig) -> dict[str, dict[str, Any]]:
    return {
        _cell_id(model_index, model, backend_index, backend): {
            "model": model,
            "backend": backend,
            "status": "pending",
            "cohort_dir": f"cells/{_cell_id(model_index, model, backend_index, backend)}",
            "error": None,
        }
        for model_index, model in enumerate(config.models)
        for backend_index, backend in enumerate(config.backends)
    }


def _initial_state(config: CadgenbenchMatrixConfig) -> dict[str, Any]:
    created = _now()
    return {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "config_fingerprint": config.fingerprint,
        "config": config.fingerprint_payload(),
        "status": "ready",
        "created_at": created,
        "updated_at": created,
        "cells": _cells(config),
        "admission": {
            "token_budget_per_cell": config.token_budget_per_cell,
            "maximum_admitted_tokens": config.maximum_admitted_tokens,
        },
        "usage": {
            "total_tokens": 0,
            "debited_tokens": 0,
            "cost_usd": 0.0 if config.pricing else None,
            "debited_cost_usd": 0.0 if config.pricing else None,
        },
    }


def _load_state(config: CadgenbenchMatrixConfig) -> tuple[Path, dict[str, Any]]:
    matrix_dir = config.matrix_dir.resolve()
    existed = matrix_dir.exists()
    matrix_dir.mkdir(parents=True, exist_ok=True)
    state_path = matrix_dir / "matrix.json"
    state = read_json(state_path)
    if state is None:
        if state_path.exists():
            raise ValueError(f"matrix state is corrupt or unreadable: {state_path}")
        if existed and any(matrix_dir.iterdir()):
            raise ValueError("refusing to initialize a nonempty matrix directory without matrix.json")
        state = _initial_state(config)
        write_json_atomic(state_path, state)
    elif state.get("config_fingerprint") != config.fingerprint:
        raise ValueError(
            "matrix configuration does not match its saved fingerprint; "
            "use the original configuration or a new matrix directory"
        )
    return state_path, state


def _pricing_by_model(config: CadgenbenchMatrixConfig) -> dict[str, ModelPricing]:
    return {item.model: item for item in config.pricing}


def _cohort_config(
    config: CadgenbenchMatrixConfig, cell: dict[str, Any]
) -> CadgenbenchCohortConfig:
    pricing = _pricing_by_model(config).get(str(cell["model"]))
    return CadgenbenchCohortConfig(
        cohort_dir=config.matrix_dir / str(cell["cohort_dir"]),
        fixtures=config.fixtures,
        model=str(cell["model"]),
        backend=str(cell["backend"]),
        total_token_budget=config.token_budget_per_cell,
        max_tokens_per_task=config.max_tokens_per_task,
        max_iterations=config.max_iterations,
        max_tokens_per_call=config.max_tokens_per_call,
        max_duration=config.max_duration,
        reasoning_effort=config.reasoning_effort,
        input_usd_per_million=pricing.input_usd_per_million if pricing else None,
        output_usd_per_million=pricing.output_usd_per_million if pricing else None,
        total_cost_budget_usd=pricing.cost_budget_usd_per_cell if pricing else None,
        dataset_dir=config.dataset_dir,
        sanity_script=config.sanity_script,
        data_repo=config.data_repo,
    )


def _recalculate(state: dict[str, Any], matrix_dir: Path, has_pricing: bool) -> None:
    total_tokens = debited_tokens = 0
    total_cost = Decimal(0)
    debited_cost = Decimal(0)
    for cell in state["cells"].values():
        cohort = read_json(matrix_dir / str(cell["cohort_dir"]) / "cohort.json") or {}
        if not cohort:
            continue
        cell["status"] = cohort.get("status", cell["status"])
        usage = cohort.get("usage") if isinstance(cohort.get("usage"), dict) else {}
        admission = (
            cohort.get("budget_accounting")
            if isinstance(cohort.get("budget_accounting"), dict)
            else {}
        )
        total_tokens += usage.get("total_tokens", 0)
        debited_tokens += admission.get("debited_tokens", 0)
        if has_pricing:
            total_cost += Decimal(str(usage.get("cost_usd") or 0))
            debited_cost += Decimal(str(admission.get("debited_cost_usd") or 0))
    state["usage"] = {
        "total_tokens": total_tokens,
        "debited_tokens": debited_tokens,
        "cost_usd": float(total_cost) if has_pricing else None,
        "debited_cost_usd": float(debited_cost) if has_pricing else None,
    }


def _save(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _now()
    write_json_atomic(path, state)


def run_matrix(
    config: CadgenbenchMatrixConfig,
    *,
    command_prefix: tuple[str, ...] | None = None,
    cwd: Path | None = None,
) -> MatrixResult:
    """Run or resume every paired model-by-kernel cohort sequentially."""

    config = CadgenbenchMatrixConfig(
        **{**asdict(config), "matrix_dir": config.matrix_dir.resolve(), "pricing": config.pricing}
    )
    with exclusive_process_lock(config.matrix_dir, "matrix"):
        state_path, state = _load_state(config)
        state["status"] = "running"
        _save(state_path, state)
        for cell in state["cells"].values():
            cell["status"] = "running"
            cell["error"] = None
            _save(state_path, state)
            try:
                result = run_cohort(
                    _cohort_config(config, cell),
                    command_prefix=command_prefix,
                    cwd=cwd,
                )
            except (RuntimeError, ValueError) as exc:
                cell["status"] = "orchestration_error"
                cell["error"] = str(exc)
                state["status"] = "failed"
                _recalculate(state, config.matrix_dir, bool(config.pricing))
                _save(state_path, state)
                raise
            cell["status"] = result.status
            _recalculate(state, config.matrix_dir, bool(config.pricing))
            _save(state_path, state)

        statuses = [str(cell["status"]) for cell in state["cells"].values()]
        state["status"] = (
            "completed" if all(status == "completed" for status in statuses) else "partial"
        )
        _recalculate(state, config.matrix_dir, bool(config.pricing))
        _save(state_path, state)
        cohort_dirs = [
            config.matrix_dir / str(cell["cohort_dir"])
            for cell in state["cells"].values()
            if (config.matrix_dir / str(cell["cohort_dir"]) / "cohort.json").is_file()
        ]
        evaluation_path: Path | None = None
        if cohort_dirs:
            evaluation_path = config.matrix_dir / "harness-evaluation.json"
            write_json_atomic(evaluation_path, evaluate_harness(cohort_dirs))
        return MatrixResult(
            matrix_dir=config.matrix_dir,
            status=state["status"],
            cell_count=len(statuses),
            completed_cells=statuses.count("completed"),
            incomplete_cells=len(statuses) - statuses.count("completed"),
            total_tokens=state["usage"]["total_tokens"],
            debited_tokens=state["usage"]["debited_tokens"],
            cost_usd=state["usage"]["cost_usd"],
            harness_evaluation=evaluation_path,
        )
