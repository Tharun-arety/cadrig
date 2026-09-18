"""Deterministic, diversity-balanced planning for CADGenBench production batches."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from .common import read_json, sha256_file, task_directories, write_json_atomic
from .dataset import discover_dataset_tasks
from .scheduler import CadgenbenchCohortConfig, CohortResult, run_cohort

BATCH_PLAN_SCHEMA_VERSION = "1.0.0"
_BANDS = ("simple", "moderate", "complex")
_MILLION = Decimal(1_000_000)
_TOPOLOGY_TERMS = (
    "remove",
    "reduce the number",
    "spacing",
    "fillet",
    "filleted",
    "chamfer",
    "groove",
    "blend",
    "flush",
)
_MULTIPLICITY_TERMS = (
    " each",
    " every",
    " two ",
    " three ",
    " four ",
    "ring of",
    "bosses",
    "flanges",
    "ribs",
    "holes",
)
_AMBIGUITY_TERMS = ("furthest", "nearest", "roughly", "principal", "only boss")


@dataclass(frozen=True)
class CadgenbenchBatchPlanConfig:
    """Frozen execution policy used by every batch in a production plan."""

    dataset_dir: Path
    completed_run: Path
    output: Path
    model: str
    target_batch_size: int = 8
    backend: str = "build123d"
    max_tokens_per_task: int = 80_000
    max_tokens_per_call: int | None = 16_000
    max_iterations: int | None = 5
    max_duration: float | None = 600
    reasoning_effort: str | None = "low"
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None

    def __post_init__(self) -> None:
        if self.target_batch_size < 3:
            raise ValueError("target_batch_size must be at least 3 for diversity")
        if self.max_tokens_per_task <= 0:
            raise ValueError("max_tokens_per_task must be greater than zero")
        if not self.model.strip():
            raise ValueError("model must not be blank")
        rates = (self.input_usd_per_million, self.output_usd_per_million)
        if (rates[0] is None) != (rates[1] is None):
            raise ValueError("input and output rates must be supplied together")
        if any(rate is not None and rate < 0 for rate in rates):
            raise ValueError("pricing rates must not be negative")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_root(dataset_dir: Path, task_id: str) -> Path:
    for root in (dataset_dir, dataset_dir / "inputs", dataset_dir / "data" / "inputs"):
        candidate = root / task_id
        if (candidate / "description.yaml").is_file():
            return candidate
    raise ValueError(f"dataset task is missing: {task_id}")


def _description(task_dir: Path) -> str:
    edit = task_dir / "edit_description.txt"
    source = edit if edit.is_file() else task_dir / "description.yaml"
    return source.read_text(encoding="utf-8", errors="replace").lower()


def _profile_score(task_dir: Path, family: str) -> tuple[float, list[str]]:
    inputs = [
        item
        for item in task_dir.iterdir()
        if item.is_file() and item.name not in {"description.yaml", "edit_description.txt"}
    ]
    input_bytes = sum(item.stat().st_size for item in inputs)
    score = math.log2(max(input_bytes, 1))
    signals = [f"input_bytes={input_bytes}"]
    if family == "generation":
        image_count = sum(item.suffix.lower() in {".png", ".jpg", ".jpeg"} for item in inputs)
        score += max(image_count - 1, 0) * 1.5
        signals.append(f"image_views={image_count}")
        return score, signals

    description = f" {_description(task_dir)} "
    topology_hits = sum(term in description for term in _TOPOLOGY_TERMS)
    multiplicity_hits = sum(term in description for term in _MULTIPLICITY_TERMS)
    ambiguity_hits = sum(term in description for term in _AMBIGUITY_TERMS)
    score += topology_hits * 1.25 + multiplicity_hits * 0.6 + ambiguity_hits * 0.4
    signals.extend(
        (
            f"topology_terms={topology_hits}",
            f"multiplicity_terms={multiplicity_hits}",
            f"ambiguity_terms={ambiguity_hits}",
        )
    )
    return score, signals


def _profiles(dataset_dir: Path) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for task_id in discover_dataset_tasks(dataset_dir):
        task_dir = _task_root(dataset_dir, task_id)
        family = "editing" if (task_dir / "input.step").is_file() else "generation"
        score, signals = _profile_score(task_dir, family)
        profiles.append(
            {
                "task_id": task_id,
                "family": family,
                "complexity_score": round(score, 6),
                "complexity_signals": signals,
            }
        )

    for family in ("generation", "editing"):
        members = sorted(
            (profile for profile in profiles if profile["family"] == family),
            key=lambda profile: (profile["complexity_score"], profile["task_id"]),
        )
        for index, profile in enumerate(members):
            band_index = min(len(_BANDS) - 1, index * len(_BANDS) // len(members))
            profile["complexity_band"] = _BANDS[band_index]
    return profiles


def _verified_completed(completed_run: Path) -> dict[str, str]:
    verification = read_json(completed_run / "verification.json")
    if not verification or not verification.get("passed") or not verification.get("sanity_checked"):
        raise ValueError("completed_run must have a passing official sanity verification")
    valid = {
        item.get("task_id"): item.get("sha256")
        for item in verification.get("tasks", [])
        if isinstance(item, dict) and item.get("status") == "valid"
    }
    completed: dict[str, str] = {}
    for task_dir in task_directories(completed_run):
        expected_hash = valid.get(task_dir.name)
        candidate = task_dir / "output.step"
        if not isinstance(expected_hash, str) or not candidate.is_file():
            raise ValueError(f"completed task lacks verified candidate evidence: {task_dir.name}")
        actual_hash = sha256_file(candidate)
        if actual_hash != expected_hash:
            raise ValueError(f"completed candidate hash changed: {task_dir.name}")
        completed[task_dir.name] = actual_hash
    if set(valid) != set(completed):
        raise ValueError("completed verification and candidate directories do not match")
    return completed


def _dataset_fingerprint(dataset_dir: Path, profiles: list[dict[str, Any]]) -> str:
    inventory: list[dict[str, Any]] = []
    for profile in sorted(profiles, key=lambda item: item["task_id"]):
        task_dir = _task_root(dataset_dir, profile["task_id"])
        inventory.append(
            {
                "task_id": profile["task_id"],
                "files": [
                    {"name": item.name, "size_bytes": item.stat().st_size}
                    for item in sorted(task_dir.iterdir(), key=lambda path: path.name)
                    if item.is_file()
                ],
            }
        )
    encoded = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _batch_capacities(task_count: int, target_size: int) -> list[int]:
    batch_count = math.ceil(task_count / target_size)
    smaller = task_count // batch_count
    larger_count = task_count % batch_count
    return [smaller + (index < larger_count) for index in range(batch_count)]


def _assign_batches(profiles: list[dict[str, Any]], capacities: list[int]) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = [[] for _ in capacities]
    strata: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for profile in profiles:
        strata.setdefault((profile["family"], profile["complexity_band"]), []).append(profile)

    ordered_strata = sorted(strata.items(), key=lambda item: (len(item[1]), item[0]))
    for stratum_index, ((family, band), members) in enumerate(ordered_strata):
        for profile in sorted(
            members, key=lambda item: (-item["complexity_score"], item["task_id"])
        ):
            eligible = [index for index, batch in enumerate(batches) if len(batch) < capacities[index]]
            if not eligible:
                raise RuntimeError("batch planner exhausted capacity unexpectedly")
            offset = stratum_index % len(batches)

            def rank(
                index: int,
                family: str = family,
                band: str = band,
                offset: int = offset,
            ) -> tuple[float, ...]:
                batch = batches[index]
                same_stratum = sum(
                    item["family"] == family and item["complexity_band"] == band
                    for item in batch
                )
                same_family = sum(item["family"] == family for item in batch)
                same_band = sum(item["complexity_band"] == band for item in batch)
                score = sum(item["complexity_score"] for item in batch)
                rotated_index = (index - offset) % len(batches)
                return same_stratum, same_family, same_band, len(batch), score, rotated_index

            selected = min(eligible, key=rank)
            batches[selected].append(profile)
    return batches


def _conservative_cost(task_count: int, config: CadgenbenchBatchPlanConfig) -> float | None:
    rates = (config.input_usd_per_million, config.output_usd_per_million)
    if rates[0] is None or rates[1] is None:
        return None
    maximum_rate = max(Decimal(str(rates[0])), Decimal(str(rates[1])))
    return float(Decimal(task_count * config.max_tokens_per_task) * maximum_rate / _MILLION)


def create_batch_plan(config: CadgenbenchBatchPlanConfig) -> dict[str, Any]:
    """Create and atomically persist a deterministic balanced production plan."""
    dataset_dir = config.dataset_dir.resolve()
    completed_run = config.completed_run.resolve()
    profiles = _profiles(dataset_dir)
    completed = _verified_completed(completed_run)
    known_tasks = {profile["task_id"] for profile in profiles}
    if not set(completed).issubset(known_tasks):
        raise ValueError("completed_run contains tasks outside the dataset")
    remaining = [profile for profile in profiles if profile["task_id"] not in completed]
    if not remaining:
        raise ValueError("no uncompleted dataset tasks remain")
    capacities = _batch_capacities(len(remaining), config.target_batch_size)
    assignments = _assign_batches(remaining, capacities)

    policy = asdict(config)
    for name in ("dataset_dir", "completed_run", "output"):
        policy.pop(name)
    batches: list[dict[str, Any]] = []
    for index, members in enumerate(assignments, start=1):
        members = sorted(members, key=lambda item: item["task_id"])
        family_counts = Counter(item["family"] for item in members)
        band_counts = Counter(item["complexity_band"] for item in members)
        batches.append(
            {
                "batch_id": f"batch-{index:02d}",
                "fixtures": [item["task_id"] for item in members],
                "task_count": len(members),
                "family_counts": dict(sorted(family_counts.items())),
                "complexity_counts": dict(sorted(band_counts.items())),
                "token_budget": len(members) * config.max_tokens_per_task,
                "conservative_cost_ceiling_usd": _conservative_cost(len(members), config),
            }
        )

    fingerprint_payload = {
        "schema_version": BATCH_PLAN_SCHEMA_VERSION,
        "dataset_fingerprint": _dataset_fingerprint(dataset_dir, profiles),
        "completed_candidates": completed,
        "policy": policy,
        "profiles": sorted(remaining, key=lambda item: item["task_id"]),
        "batches": batches,
    }
    encoded = json.dumps(
        fingerprint_payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    plan_id = hashlib.sha256(encoded).hexdigest()
    plan = {
        **fingerprint_payload,
        "plan_id": plan_id,
        "created_at": _now(),
        "dataset_dir": str(dataset_dir),
        "completed_run": str(completed_run),
        "summary": {
            "dataset_tasks": len(profiles),
            "completed_tasks": len(completed),
            "remaining_tasks": len(remaining),
            "batch_count": len(batches),
            "maximum_tokens": len(remaining) * config.max_tokens_per_task,
            "conservative_cost_ceiling_usd": _conservative_cost(len(remaining), config),
        },
        "complexity_method": {
            "status": "public-input heuristic, not benchmark ground truth",
            "bands": list(_BANDS),
            "ranking": "within-family tertiles over all authoritative tasks",
            "generation_signals": ["input byte size", "drawing view count"],
            "editing_signals": [
                "input byte size",
                "topology-changing language",
                "feature multiplicity",
                "positional ambiguity",
            ],
        },
    }
    write_json_atomic(config.output.resolve(), plan)
    return plan


def _load_plan(plan_path: Path) -> dict[str, Any]:
    plan = read_json(plan_path)
    if not plan or plan.get("schema_version") != BATCH_PLAN_SCHEMA_VERSION:
        raise ValueError("batch plan is missing, corrupt, or has an unsupported schema")
    fingerprint_payload = {
        key: plan[key]
        for key in (
            "schema_version",
            "dataset_fingerprint",
            "completed_candidates",
            "policy",
            "profiles",
            "batches",
        )
    }
    encoded = json.dumps(
        fingerprint_payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != plan.get("plan_id"):
        raise ValueError("batch plan fingerprint does not match its contents")
    return plan


def run_planned_batch(
    plan_path: Path,
    batch_id: str,
    *,
    cohort_dir: Path | None = None,
    cwd: Path | None = None,
) -> CohortResult:
    """Run one immutable plan batch through the existing resumable scheduler."""
    plan_path = plan_path.resolve()
    plan = _load_plan(plan_path)
    selected = next(
        (batch for batch in plan["batches"] if batch.get("batch_id") == batch_id), None
    )
    if selected is None:
        raise ValueError(f"unknown batch id: {batch_id}")
    dataset_dir = Path(plan["dataset_dir"])
    profiles = _profiles(dataset_dir)
    if _dataset_fingerprint(dataset_dir, profiles) != plan["dataset_fingerprint"]:
        raise ValueError("dataset fingerprint changed since the batch plan was created")

    policy = plan["policy"]
    destination = cohort_dir or plan_path.parent / "runs" / batch_id
    return run_cohort(
        CadgenbenchCohortConfig(
            cohort_dir=destination,
            fixtures=tuple(selected["fixtures"]),
            model=policy["model"],
            total_token_budget=selected["token_budget"],
            max_tokens_per_task=policy["max_tokens_per_task"],
            backend=policy["backend"],
            max_iterations=policy["max_iterations"],
            max_tokens_per_call=policy["max_tokens_per_call"],
            max_duration=policy["max_duration"],
            reasoning_effort=policy["reasoning_effort"],
            input_usd_per_million=policy["input_usd_per_million"],
            output_usd_per_million=policy["output_usd_per_million"],
            total_cost_budget_usd=selected["conservative_cost_ceiling_usd"],
            dataset_dir=dataset_dir,
        ),
        cwd=cwd,
    )
