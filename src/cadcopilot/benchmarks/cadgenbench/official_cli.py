"""Run the upstream CLI with narrow, versioned compatibility patches applied."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from cadgenbench.baseline import _cli as baseline_cli
from cadgenbench.baseline import agent
from cadgenbench.baseline.llm import CompletionResult, LLMClient
from cadgenbench.baseline.types import AgentResult
from cadgenbench.cli import main

from .common import read_json, write_json_atomic
from .compat import completion_token_allowance, extract_code_blocks_tolerant

_strict_extract_code_blocks = agent.extract_code_blocks
_strict_has_done_signal = agent._has_done_signal
_strict_auto_validate_and_render = agent._auto_validate_and_render
_strict_run_agent = agent.run_agent
_strict_agent_result_save = AgentResult.save
_strict_llm_complete = LLMClient.complete
_recovered_code_hashes: set[str] = set()
_TOKEN_CAP_ENV = "CADCOPILOT_ATTEMPT_TOKEN_CAP"
_USAGE_LEDGER_ENV = "CADCOPILOT_PROVIDER_USAGE_PATH"
_validation_state = threading.local()
_CANDIDATE_NAMES = ("output.step", "output.stp")

_ARTIFACT_SAFETY_GUIDANCE = """

Artifact safety contract:
- Never write a dummy, placeholder, sentinel, or fallback primitive to
  `output.step`. If an operation fails, raise the error and leave the candidate
  absent or preserve the last successful candidate unchanged.
- A candidate written by a failed process is rejected even when it happens to
  be a valid watertight solid.
- Do not repeat unchanged code merely to reach the iteration limit or satisfy
  the completion protocol. Use each turn to inspect, diagnose, or improve the
  geometry.
- Put the executable Python block before any explanation and keep non-code text
  below 200 words. Never consume a full turn narrating a plan without code;
  implement the first measurable geometry version immediately.
"""

_GENERATION_COMPLETENESS_GUIDANCE = """

Generation semantic-completeness contract:
- Before writing code, extract a checklist of the visible overall dimensions,
  primary profile, holes/cutouts, bosses, bends/flanges, patterns, and major
  fillets or chamfers. Implement every clearly discernible major feature.
- A bounding box, single primitive, or intentionally simplified stand-in is
  not an acceptable result when the drawing shows additional features. Never
  stop because the part is complex; decompose it into additive and subtractive
  operations and make measurable progress.
- Before `[DONE]`, compare the render with the drawing and name any visible
  feature still missing. Continue iterating while a major feature is missing.
"""

_EDITING_COMPLETENESS_GUIDANCE = """

Editing semantic-completeness contract:
- Treat the requested edit as local unless the instruction explicitly says
  otherwise. Preserve unrelated bodies, interfaces, holes, topology, overall
  scale, and placement.
- Compare input and output bounding boxes, volume, and major feature counts.
  A catastrophic scale or volume change is a failed edit, not a fallback.
- Before `[DONE]`, verify that the named feature changed by the requested
  amount and that unrelated geometry stayed invariant.
- Spend at most two distinct execution turns on feature inspection. If exact
  analytic topology is unavailable but plausible split/tessellated faces were
  found, do not repeat the same search with looser predicates. Rank candidates
  using the task's stated axis direction, relative size (such as smaller or
  larger), position, face normal, and local dimensions; then execute the most
  appropriate mesh fallback by turn three. Reserve later turns for validation,
  rendering, and one corrective retry.
"""

_STEP_EDIT_GUIDANCE = """

Imported STEP compatibility notes:
- `geom_type` is a `GeomType` enum, not a string. Compare with values such as
  `GeomType.CYLINDER`, `GeomType.PLANE`, or use `str(face.geom_type)` while
  inspecting unknown geometry.
- If Build123d's `export_step` rejects an otherwise valid imported or modified
  shape, use the operation-neutral atomic fallback:

```python
from cadcopilot.benchmarks.cadgenbench.step_io import robust_export_step
print("STEP export method:", robust_export_step(shape, "output.step"))
```

This helper only writes the BREP you provide; it does not select features,
repair topology, or perform the requested edit.
"""

_MESH_FALLBACK_GUIDANCE = """

Kernel fallback available: this editing input includes `input.mesh.npz`. If the
source STEP is invalid, or a direct BRep terminal-length edit repeatedly creates
invalid or unorientable output, select the feature axis and terminal side from
the task and render, then call:

```python
from cadcopilot.benchmarks.cadgenbench.mesh_fallback import extend_terminal_mesh_to_step
print(extend_terminal_mesh_to_step(
    "input.mesh.npz", "output.step",
    axis="<x|y|z>", side="<min|max|both>", distance_mm=<positive distance per side>,
))
```

Use `both` only for a symmetric extension; it moves each terminal outward by
`distance_mm`. Replace every placeholder from the current task and render. No
operation values are supplied by the harness.

For a radial resize such as widening an axial through bore, first inspect the
target cylindrical face's true axis, center, radius and axial span. Do not use
`Face.center()` as the cylinder-axis location. One generic inspection path is
`BRepAdaptor_Surface(face.wrapped).Cylinder().Axis()`. If direct BRep edits
remain invalid, call:

```python
from cadcopilot.benchmarks.cadgenbench.mesh_fallback import (
    resize_cylindrical_mesh_region_to_step,
)
print(resize_cylindrical_mesh_region_to_step(
    "input.mesh.npz", "output.step",
    axis="<x|y|z>", center=(<perpendicular coordinate 1>, <coordinate 2>),
    current_radius_mm=<observed radius>, radial_delta_mm=<radius change>,
    axis_min_mm=<observed minimum>, axis_max_mm=<observed maximum>,
))
```

Center coordinates are `(y, z)` for an X axis, `(x, z)` for Y and `(x, y)`
for Z. Convert a requested diameter change to a radius change. This fallback
only moves mesh vertices matching the caller-selected cylindrical region.

For a local planar annular edit (for example, raising one ring-shaped opening
without moving every terminal face), inspect the mesh/STEP to identify the
plane axis, circle center, plane position, and inner/outer radii. If the direct
BRep edit remains invalid, call:

```python
from cadcopilot.benchmarks.cadgenbench.mesh_fallback import (
    translate_planar_annulus_mesh_region_to_step,
)
print(translate_planar_annulus_mesh_region_to_step(
    "input.mesh.npz", "output.step",
    axis="<x|y|z>", center=(<perpendicular coordinate 1>, <coordinate 2>),
    plane_position_mm=<observed plane position>,
    inner_radius_mm=<observed inner radius>,
    outer_radius_mm=<observed outer radius>,
    distance_mm=<signed translation along axis>,
))
```

Cluster mesh vertices by the candidate axis coordinate and radial distance to
measure an annulus when the source BRep cannot be queried reliably. Positive
distance moves toward the positive axis direction. This fallback preserves the
mesh connectivity and moves only vertices on the caller-selected annular plane.

If the intended planar face is visible in the BRep but its circular boundary
has been split, clipped, or exchanged as line/B-spline edges, do not require
exactly two circle edges. Use its observed plane coordinate and face center as
a seed for the connected planar mesh patch:

```python
from cadcopilot.benchmarks.cadgenbench.mesh_fallback import (
    translate_planar_mesh_patch_to_step,
)
print(translate_planar_mesh_patch_to_step(
    "input.mesh.npz", "output.step",
    axis="<x|y|z>", plane_position_mm=<observed plane position>,
    seed=(<observed perpendicular face-center coordinates>),
    distance_mm=<signed translation along axis>,
))
```

This moves only the edge-connected coplanar triangle component nearest the
seed. Inspect and render the selected face before invoking it; no target face
or operation value is inferred by the harness.
"""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _extract_code_blocks(text: str, lang: str = "python") -> list[str]:
    strict = _strict_extract_code_blocks(text, lang)
    recovered = extract_code_blocks_tolerant(text, lang, _strict_extract_code_blocks)
    if recovered != strict:
        _recovered_code_hashes.update(_sha256_text(code) for code in recovered)
    return recovered


def _has_done_signal_after_review(text: str) -> bool:
    """Accept completion only after separate review of a strict-valid candidate."""
    if not _strict_has_done_signal(text):
        return False
    if _extract_code_blocks(text):
        return False
    return (
        getattr(_validation_state, "passed", False) is True
        or getattr(_validation_state, "budget_stop", False) is True
    )


def _validation_feedback_passed(auto_text: str, auto_iso: bytes | None) -> bool:
    valid = re.search(r"(?m)^Valid:\s+True\s*$", auto_text) is not None
    watertight = re.search(r"(?m)^Watertight:\s+True\s*$", auto_text) is not None
    has_error = "Validation error:" in auto_text or "Render failed:" in auto_text
    return valid and watertight and not has_error and auto_iso is not None


def _auto_validate_and_render_strict(*args: Any, **kwargs: Any) -> tuple[str, bytes | None]:
    """Remember whether the latest executed candidate cleared the mesh gate."""
    auto_text, auto_iso = _strict_auto_validate_and_render(*args, **kwargs)
    last_execution = args[1] if len(args) > 1 else kwargs.get("last_exe")
    execution_succeeded = getattr(last_execution, "success", False) is True
    _validation_state.passed = execution_succeeded and _validation_feedback_passed(
        auto_text, auto_iso
    )
    if _validation_state.passed:
        _validation_state.ever_passed = True
    return auto_text, auto_iso


def _run_agent_with_mesh_sidecars(
    task_description: str,
    *args: Any,
    input_files: list[Path] | None = None,
    work_dir: Path | None = None,
    **kwargs: Any,
) -> AgentResult:
    _validation_state.passed = False
    _validation_state.ever_passed = False
    _validation_state.budget_stop = False
    sidecars: list[Path] = []
    has_step_input = False
    for source in input_files or []:
        if source.suffix.lower() in {".step", ".stp"}:
            has_step_input = True
            sidecar = source.with_name(f"{source.stem}.mesh.npz")
            if sidecar.is_file():
                sidecars.append(sidecar)
    if has_step_input:
        task_description += (
            _ARTIFACT_SAFETY_GUIDANCE
            + _EDITING_COMPLETENESS_GUIDANCE
            + _STEP_EDIT_GUIDANCE
        )
    else:
        task_description += _ARTIFACT_SAFETY_GUIDANCE + _GENERATION_COMPLETENESS_GUIDANCE
    if sidecars:
        if work_dir is None:
            work_dir = Path(tempfile.mkdtemp(prefix="cadgenbench_agent_"))
        work_dir.mkdir(parents=True, exist_ok=True)
        for sidecar in sidecars:
            shutil.copy2(sidecar, work_dir / sidecar.name)
        task_description += _MESH_FALLBACK_GUIDANCE
    return _strict_run_agent(
        task_description,
        *args,
        input_files=input_files,
        work_dir=work_dir,
        **kwargs,
    )


def _candidate_names_produced(execution: Any) -> set[str]:
    files = getattr(execution, "files_produced", {})
    if not isinstance(files, dict):
        return set()
    return {
        Path(str(name)).name.lower()
        for name in files
        if Path(str(name)).name.lower() in _CANDIDATE_NAMES
    }


def _sanitize_saved_candidates(result: AgentResult, destination: Path) -> None:
    """Make the canonical artifact come only from a successful producing turn.

    The upstream incremental saver snapshots the live work-directory candidate
    into every latest turn, even when that turn failed or did not modify the
    artifact. A failed program can therefore write a valid dummy solid and have
    it selected solely because its turn number is highest. Quarantine artifacts
    actually produced by failed executions, remove stale duplicates, and
    rematerialize the root candidate from the newest successful producing turn.
    """
    for record in result.turns:
        last_producer_succeeded: dict[str, bool] = {}
        for execution in record.code_executions:
            for name in _candidate_names_produced(execution):
                last_producer_succeeded[name] = (
                    getattr(execution, "success", False) is True
                )

        turn_dir = destination / f"turn_{record.turn}"
        for name in _CANDIDATE_NAMES:
            candidate = turn_dir / name
            if not candidate.is_file() or last_producer_succeeded.get(name) is True:
                continue
            if last_producer_succeeded.get(name) is False:
                candidate.replace(turn_dir / f"rejected_failed_{name}")
            else:
                candidate.unlink()

    for name in _CANDIDATE_NAMES:
        canonical = destination / name
        if canonical.exists():
            canonical.unlink()

    for record in reversed(result.turns):
        turn_dir = destination / f"turn_{record.turn}"
        for name in _CANDIDATE_NAMES:
            candidate = turn_dir / name
            if candidate.is_file():
                shutil.copy2(candidate, destination / name)
                return


def _save_with_trace(self: AgentResult, output_dir: str | Path) -> Path:
    destination = _strict_agent_result_save(self, output_dir)
    _sanitize_saved_candidates(self, destination)
    turns: list[dict[str, Any]] = []
    for record in self.turns:
        executions = []
        for execution in record.code_executions:
            code_hash = _sha256_text(execution.code)
            executions.append(
                {
                    "code_sha256": code_hash,
                    "recovered_unterminated_fence": code_hash in _recovered_code_hashes,
                    "success": execution.success,
                    "duration_seconds": round(execution.duration_s, 3),
                    "files_produced": dict(sorted(execution.files_produced.items())),
                    "stdout_bytes": len(execution.stdout.encode("utf-8")),
                    "stderr_bytes": len(execution.stderr.encode("utf-8")),
                }
            )
        turns.append(
            {
                "turn": record.turn,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "reasoning_tokens": record.reasoning_tokens,
                "total_tokens": record.prompt_tokens + record.completion_tokens,
                "duration_seconds": round(record.duration_s, 3),
                "assistant_message_sha256": _sha256_text(record.assistant_message),
                "executions": executions,
            }
        )
    write_json_atomic(
        destination / "trace.json",
        {
            "schema_version": "1.0.0",
            "total_tokens": self.total_tokens,
            "total_duration_seconds": round(self.total_duration_s, 3),
            "completed": self.completed,
            "stopped_reason": self.stopped_reason,
            "budget_stop": getattr(_validation_state, "budget_stop", False) is True,
            "turns": turns,
        },
    )
    return destination


def _usage_value(completion: Any, name: str) -> int:
    value = getattr(completion, name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _record_provider_usage(completion: Any, *, accepted: bool) -> None:
    raw_path = os.environ.get(_USAGE_LEDGER_ENV)
    if raw_path is None:
        return
    path = Path(raw_path).resolve()
    payload = read_json(path) or {"schema_version": "1.0.0", "calls": []}
    calls = payload.get("calls")
    if not isinstance(calls, list):
        calls = []
    total = _usage_value(completion, "total_tokens")
    prompt = _usage_value(completion, "prompt_tokens")
    completion_tokens = _usage_value(completion, "completion_tokens")
    calls.append(
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion_tokens,
            "unclassified_tokens": max(total - prompt - completion_tokens, 0),
            "total_tokens": total,
            "accepted_by_attempt_cap": accepted,
        }
    )
    payload["calls"] = calls
    payload["prompt_tokens"] = sum(_usage_value_from_call(call, "prompt_tokens") for call in calls)
    payload["completion_tokens"] = sum(
        _usage_value_from_call(call, "completion_tokens") for call in calls
    )
    payload["unclassified_tokens"] = sum(
        _usage_value_from_call(call, "unclassified_tokens") for call in calls
    )
    payload["total_tokens"] = sum(_usage_value_from_call(call, "total_tokens") for call in calls)
    payload["call_count"] = len(calls)
    payload["rejected_call_count"] = sum(
        isinstance(call, dict) and call.get("accepted_by_attempt_cap") is False for call in calls
    )
    write_json_atomic(path, payload)


def _usage_value_from_call(call: object, name: str) -> int:
    if not isinstance(call, dict):
        return 0
    value = call.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _complete_with_cap(
    self: LLMClient, messages: list[dict[str, Any]], **kwargs: Any
) -> Any:
    raw_cap = os.environ.get(_TOKEN_CAP_ENV)
    if raw_cap is None:
        return _strict_llm_complete(self, messages, **kwargs)
    try:
        token_cap = int(raw_cap)
        consumed = int(getattr(self, "_cadcopilot_consumed_tokens", 0))
        prompt_tokens = self.count_tokens(messages)
        requested = int(kwargs.get("max_tokens", 0))
        allowance = completion_token_allowance(
            token_cap=token_cap,
            consumed=consumed,
            prompt_tokens=prompt_tokens,
            requested=requested,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid {_TOKEN_CAP_ENV} budget configuration") from exc
    if allowance <= 0:
        if getattr(_validation_state, "ever_passed", False) is True:
            _validation_state.budget_stop = True
            return CompletionResult(
                content=(
                    "[DONE]\nToken budget reached; preserve the last "
                    "strict-valid candidate."
                ),
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                model=str(getattr(self, "model", "unknown")),
                raw=None,
            )
        raise RuntimeError(
            f"attempt token cap exhausted before model call ({consumed}+{prompt_tokens} "
            f">= {token_cap})"
        )
    kwargs["max_tokens"] = allowance
    completion = _strict_llm_complete(self, messages, **kwargs)
    new_total = consumed + completion.total_tokens
    self._cadcopilot_consumed_tokens = new_total
    _record_provider_usage(completion, accepted=new_total <= token_cap)
    if new_total > token_cap:
        raise RuntimeError(
            f"provider-reported usage exceeded attempt token cap ({new_total} > {token_cap})"
        )
    return completion


agent.extract_code_blocks = _extract_code_blocks
agent._has_done_signal = _has_done_signal_after_review
agent._auto_validate_and_render = _auto_validate_and_render_strict
agent.run_agent = _run_agent_with_mesh_sidecars
baseline_cli.run_agent = _run_agent_with_mesh_sidecars
AgentResult.save = _save_with_trace
LLMClient.complete = _complete_with_cap


if __name__ == "__main__":
    raise SystemExit(main())
