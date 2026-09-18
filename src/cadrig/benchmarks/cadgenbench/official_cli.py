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
_TOKEN_CAP_ENV = "CADRIG_ATTEMPT_TOKEN_CAP"
_USAGE_LEDGER_ENV = "CADRIG_PROVIDER_USAGE_PATH"
_PROMPT_TOKEN_MARGIN = 4_096
_PROMPT_CALIBRATION_BUFFER = 2_048
_EDIT_INSPECTION_SOFT_LIMIT = 2
_EDIT_INSPECTION_HARD_LIMIT = 4
_validation_state = threading.local()
_CANDIDATE_NAMES = ("output.step", "output.stp")
_COUNT_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_EDIT_COUNT_RE = re.compile(
    r"(?m)^CADRIG_EDIT_COUNTS\s+expected=(\d+)\s+matched=(\d+)\s+modified=(\d+)\s*$"
)
_EDIT_INSTANCE_RE = re.compile(
    r"(?m)^CADRIG_EDIT_INSTANCE\s+index=(\d+)\s+"
    r"center=\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*"
    r"(-?\d+(?:\.\d+)?)\s*\)\s*$"
)

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
- Inspection code must not write or re-export `output.step`. Export only after
  an actual geometric operation has changed the requested feature.
- Re-exporting `input.step` without a measurable kernel-level geometry change
  is rejected as a no-op, even when the resulting STEP is valid and watertight.
- Preserve the input solid-body count unless the instruction explicitly asks
  to split, add, remove, merge, or otherwise change bodies. Repeated features
  such as blades must remain connected to their owning part.
- A local blend, fillet, chamfer, hole, bore, pocket, or slot edit must preserve
  the source outer extents. Adding proxy geometry outside those extents is not
  an acceptable way to simulate the requested feature change.
- For an explicit analytic blend/fillet radius transition, the old-radius face
  count must decrease and the new-radius face count must increase. Adding a
  separate ring with the new radius while retaining the old blend is rejected.
- For an explicit multi-hole spacing transition, relocate every named hole:
  old analytic cylinder axes must disappear and the requested number of new
  axes must form the stated center-to-center spacing. One arbitrary extra cut
  is not an acceptable substitute.
- Removing a fillet must reduce analytic toroidal fillet evidence without
  introducing a novel coaxial cylinder radius. Do not cut an internal recess
  while filling an outer edge.
- Spend at most two distinct execution turns on feature inspection. If exact
  analytic topology is unavailable but plausible split/tessellated faces were
  found, do not repeat the same search with looser predicates. Rank candidates
  using the task's stated axis direction, relative size (such as smaller or
  larger), position, face normal, and local dimensions; then execute the most
  appropriate mesh fallback by turn three. Reserve later turns for validation,
  rendering, and one corrective retry.
"""


def _explicit_each_target_count(task_description: str) -> int | None:
    """Extract an unambiguous cardinality from phrases such as 'each of the four'."""
    match = re.search(
        r"\beach\s+of\s+(?:the\s+)?(?P<count>\d+|"
        + "|".join(_COUNT_WORDS)
        + r")\b",
        task_description,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    raw = match.group("count").lower()
    return int(raw) if raw.isdigit() else _COUNT_WORDS[raw]


def _editing_cardinality_guidance(expected: int) -> str:
    return f"""

Explicit edit-cardinality contract:
- The instruction explicitly targets {expected} distinct feature instances.
  This is a hard acceptance constraint, not an approximate hint. Identify and
  cluster the topology by feature instance; do not substitute {expected - 1}
  global faces or broad body walls for {expected} separate target features.
- "Instance" means one distinct named feature from the instruction, not one
  arbitrarily selected face. Preserve every stated geometric qualifier such as
  side, axis, face normal, long-axis direction, size, and feature type. Do not
  switch axes or normal directions merely to manufacture the required count.
- Before exporting, confirm that exactly {expected} instances were matched and
  exactly {expected} instances were modified by the requested amount.
- For every modified named feature, print one evidence line using its actual
  representative topology center in this exact format:
  `CADRIG_EDIT_INSTANCE index=<1..{expected}> center=(<x>,<y>,<z>)`. Indices
  and centers must be unique. A list of faces from one feature does not prove
  that multiple feature instances changed.
- Producing code must print one machine-readable line with integer values in
  this exact format: `CADRIG_EDIT_COUNTS expected={expected} matched=<count> modified=<count>`.
  The candidate is rejected unless both counts equal {expected}. Never print
  compliant counts unless the executed topology selection and operation
  substantiate them.
"""


def _editing_count_evidence_passed(execution: Any, expected: int | None) -> bool:
    if expected is None:
        return True
    stdout = getattr(execution, "stdout", "")
    if not isinstance(stdout, str):
        return False
    matches = list(_EDIT_COUNT_RE.finditer(stdout))
    if not matches:
        return False
    reported_expected, matched, modified = (int(value) for value in matches[-1].groups())
    instances = list(_EDIT_INSTANCE_RE.finditer(stdout))
    indices = {int(match.group(1)) for match in instances}
    centers = {
        (float(match.group(2)), float(match.group(3)), float(match.group(4)))
        for match in instances
    }
    return (
        reported_expected == expected
        and matched == expected
        and modified == expected
        and indices == set(range(1, expected + 1))
        and len(centers) == expected
    )


def _shape_edit_signature(shape: Any) -> dict[str, Any]:
    bbox = shape.bounding_box().size
    center = shape.center()
    return {
        "volume": float(shape.volume),
        "area": float(shape.area),
        "bbox": (float(bbox.X), float(bbox.Y), float(bbox.Z)),
        "center": (float(center.X), float(center.Y), float(center.Z)),
        "face_count": len(shape.faces()),
        "edge_count": len(shape.edges()),
        "solid_count": len(shape.solids()),
        "face_areas": tuple(sorted(float(face.area) for face in shape.faces())),
        "analytic_blend_radii": _analytic_blend_radii(shape),
        "analytic_cylinder_axes": _analytic_cylinder_axes(shape),
        "analytic_torus_radii": _analytic_torus_radii(shape),
    }


def _analytic_blend_radii(shape: Any) -> tuple[float, ...]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Torus

    radii: list[float] = []
    for face in shape.faces():
        wrapped = getattr(face, "wrapped", None)
        if wrapped is None:
            continue
        surface = BRepAdaptor_Surface(wrapped)
        if surface.GetType() == GeomAbs_Cylinder:
            radii.append(float(surface.Cylinder().Radius()))
        elif surface.GetType() == GeomAbs_Torus:
            radii.append(float(surface.Torus().MinorRadius()))
    return tuple(sorted(radii))


def _analytic_cylinder_axes(
    shape: Any,
) -> tuple[tuple[float, float, float, float, float, float, float], ...]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder

    axes = set()
    for face in shape.faces():
        wrapped = getattr(face, "wrapped", None)
        if wrapped is None:
            continue
        surface = BRepAdaptor_Surface(wrapped)
        if surface.GetType() != GeomAbs_Cylinder:
            continue
        cylinder = surface.Cylinder()
        direction = cylinder.Axis().Direction()
        vector = [float(direction.X()), float(direction.Y()), float(direction.Z())]
        first_nonzero = next((value for value in vector if abs(value) > 1.0e-8), 1.0)
        if first_nonzero < 0:
            vector = [-value for value in vector]
        location = cylinder.Location()
        point = [float(location.X()), float(location.Y()), float(location.Z())]
        projection = sum(value * component for value, component in zip(point, vector))
        anchor = [
            value - projection * component
            for value, component in zip(point, vector, strict=True)
        ]
        axes.add(
            (
                round(float(cylinder.Radius()), 3),
                *(round(value, 4) for value in vector),
                *(round(value, 3) for value in anchor),
            )
        )
    return tuple(sorted(axes))


def _analytic_torus_radii(shape: Any) -> tuple[tuple[float, float], ...]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Torus

    radii = []
    for face in shape.faces():
        wrapped = getattr(face, "wrapped", None)
        if wrapped is None:
            continue
        surface = BRepAdaptor_Surface(wrapped)
        if surface.GetType() == GeomAbs_Torus:
            torus = surface.Torus()
            radii.append(
                (round(float(torus.MajorRadius()), 3), round(float(torus.MinorRadius()), 3))
            )
    return tuple(sorted(radii))


def _explicit_blend_radius_transition(
    task_description: str,
) -> tuple[float, float] | None:
    match = re.search(
        r"\b(?:blends?|fillets?)\b.*?\bfrom\s+([0-9]+(?:\.[0-9]+)?)\s*mm\s+"
        r"\bto\s+([0-9]+(?:\.[0-9]+)?)\s*mm\b",
        task_description,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return float(match.group(1)), float(match.group(2))


def _blend_radius_transition_passed(
    before: dict[str, Any],
    after: dict[str, Any],
    transition: tuple[float, float],
) -> tuple[bool, str]:
    old_radius, new_radius = transition

    def count(signature: dict[str, Any], radius: float) -> int:
        return sum(
            abs(float(value) - radius) <= 0.1
            for value in signature["analytic_blend_radii"]
        )

    old_before = count(before, old_radius)
    if old_before == 0:
        return True, "source blend radius is not analytically observable"
    old_after = count(after, old_radius)
    new_before = count(before, new_radius)
    new_after = count(after, new_radius)
    passed = old_after < old_before and new_after > new_before
    return (
        passed,
        (
            f"analytic radius counts {old_radius:g} mm: {old_before}->{old_after}, "
            f"{new_radius:g} mm: {new_before}->{new_after}"
        ),
    )


def _explicit_hole_spacing_transition(
    task_description: str,
) -> tuple[int, str, str, float, float] | None:
    count_match = re.search(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+holes?\b",
        task_description,
        flags=re.IGNORECASE,
    )
    hole_axis_match = re.search(
        r"\baxes?\s+(?:are\s+)?collinear\s+with\s+(?:the\s+)?([xyz])\b",
        task_description,
        flags=re.IGNORECASE,
    )
    alignment_match = re.search(
        r"\baligned\s+along\s+(?:the\s+)?([xyz])(?:\s+axis)?\b",
        task_description,
        flags=re.IGNORECASE,
    )
    spacing_match = re.search(
        r"\bspacing\s+from\s+([0-9]+(?:\.[0-9]+)?)\s*mm"
        r"(?:\s+apart)?(?:\s+center-to-center)?\s+to\s+"
        r"([0-9]+(?:\.[0-9]+)?)\s*mm\b",
        task_description,
        flags=re.IGNORECASE,
    )
    if not all((count_match, hole_axis_match, alignment_match, spacing_match)):
        return None
    assert count_match is not None
    assert hole_axis_match is not None
    assert alignment_match is not None
    assert spacing_match is not None
    raw_count = count_match.group(1).lower()
    count = _COUNT_WORDS.get(raw_count, int(raw_count) if raw_count.isdigit() else 0)
    return (
        count,
        hole_axis_match.group(1).upper(),
        alignment_match.group(1).upper(),
        float(spacing_match.group(1)),
        float(spacing_match.group(2)),
    )


def _hole_spacing_transition_passed(
    before: dict[str, Any],
    after: dict[str, Any],
    transition: tuple[int, str, str, float, float],
) -> tuple[bool, str]:
    count, hole_axis, alignment_axis, old_spacing, new_spacing = transition
    axis_index = {"X": 1, "Y": 2, "Z": 3}[hole_axis]
    coordinate_index = {"X": 4, "Y": 5, "Z": 6}[alignment_axis]
    lateral_indices = [
        index
        for axis, index in {"X": 4, "Y": 5, "Z": 6}.items()
        if axis not in {hole_axis, alignment_axis}
    ]

    def matching_axes(signature: dict[str, Any]) -> set[tuple[float, ...]]:
        return {
            tuple(axis)
            for axis in signature["analytic_cylinder_axes"]
            if abs(abs(float(axis[axis_index])) - 1.0) <= 1.0e-3
        }

    def has_spacing_pair(axes: set[tuple[float, ...]], spacing: float) -> bool:
        unique_locations = {tuple(axis[4:7]) for axis in axes}
        locations = sorted(unique_locations)
        for first_index, first in enumerate(locations):
            for second in locations[first_index + 1 :]:
                if abs(abs(first[coordinate_index - 4] - second[coordinate_index - 4]) - spacing) > 1.0:
                    continue
                if all(
                    abs(first[index - 4] - second[index - 4]) <= 1.0
                    for index in lateral_indices
                ):
                    return True
        return False

    source_axes = matching_axes(before)
    candidate_axes = matching_axes(after)
    removed = source_axes - candidate_axes
    added = candidate_axes - source_axes
    old_pair = has_spacing_pair(removed, old_spacing)
    new_pair = has_spacing_pair(added, new_spacing)
    passed = len(removed) >= count and len(added) >= count and old_pair and new_pair
    return (
        passed,
        (
            f"analytic {hole_axis}-axis hole transition removed={len(removed)} "
            f"added={len(added)}; {alignment_axis}-spacing "
            f"{old_spacing:g}->{new_spacing:g} mm pairs={old_pair}->{new_pair}"
        ),
    )


def _instruction_requires_fillet_removal(task_description: str) -> bool:
    return re.search(
        r"\b(?:remove|delete|eliminate)\b.{0,30}\bfillets?\b",
        task_description,
        flags=re.IGNORECASE,
    ) is not None


def _fillet_removal_passed(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[bool, str]:
    before_tori = tuple(before["analytic_torus_radii"])
    after_tori = tuple(after["analytic_torus_radii"])
    source_cylinders = {tuple(axis) for axis in before["analytic_cylinder_axes"]}
    candidate_cylinders = {tuple(axis) for axis in after["analytic_cylinder_axes"]}
    added_cylinders = candidate_cylinders - source_cylinders
    passed = len(after_tori) < len(before_tori) and not added_cylinders
    return (
        passed,
        (
            f"analytic fillet removal tori={len(before_tori)}->{len(after_tori)}; "
            f"novel cylinder axes={len(added_cylinders)}"
        ),
    )


def _relative_delta(first: float, second: float) -> float:
    return abs(first - second) / max(abs(first), abs(second), 1.0)


def _editing_candidate_changed(
    work_dir: Path,
    produced_names: set[str] | None = None,
    *,
    preserve_solid_count: bool = True,
    preserve_extents: bool = False,
    required_radius_transition: tuple[float, float] | None = None,
    required_hole_spacing_transition: tuple[int, str, str, float, float] | None = None,
    require_fillet_removal: bool = False,
) -> tuple[bool, str, str]:
    """Reject operation-neutral STEP rewrites using kernel-level shape signatures."""
    from build123d import import_step

    source = work_dir / "input.step"
    eligible_names = produced_names if produced_names is not None else set(_CANDIDATE_NAMES)
    candidate = next(
        (
            work_dir / name
            for name in _CANDIDATE_NAMES
            if name in eligible_names and (work_dir / name).is_file()
        ),
        None,
    )
    if not source.is_file() or candidate is None:
        return (
            False,
            "editing comparison requires input.step and an output candidate",
            "comparison_error",
        )
    try:
        before = _shape_edit_signature(import_step(source))
        after = _shape_edit_signature(import_step(candidate))
    except (OSError, RuntimeError, ValueError) as exc:
        return (
            False,
            f"kernel comparison failed: {type(exc).__name__}: {exc}",
            "comparison_error",
        )

    if preserve_solid_count and before["solid_count"] != after["solid_count"]:
        return (
            False,
            f"solid-body count changed from {before['solid_count']} to {after['solid_count']}",
            "topology_violation",
        )
    if preserve_extents:
        extent_deltas = [
            abs(first - second)
            for first, second in zip(before["bbox"], after["bbox"], strict=True)
        ]
        extent_tolerances = [max(0.5, abs(value) * 0.005) for value in before["bbox"]]
        if any(
            delta > tolerance
            for delta, tolerance in zip(
                extent_deltas, extent_tolerances, strict=True
            )
        ):
            formatted = ", ".join(f"{value:.3f}" for value in extent_deltas)
            return (
                False,
                f"local-feature edit changed outer extents by ({formatted}) mm",
                "topology_violation",
            )
    if required_radius_transition is not None:
        radius_passed, radius_reason = _blend_radius_transition_passed(
            before, after, required_radius_transition
        )
        if not radius_passed:
            return False, radius_reason, "topology_violation"
    if required_hole_spacing_transition is not None:
        spacing_passed, spacing_reason = _hole_spacing_transition_passed(
            before, after, required_hole_spacing_transition
        )
        if not spacing_passed:
            return False, spacing_reason, "topology_violation"
    if require_fillet_removal:
        fillet_passed, fillet_reason = _fillet_removal_passed(before, after)
        if not fillet_passed:
            return False, fillet_reason, "topology_violation"

    if before["face_count"] != after["face_count"]:
        return True, "face count changed", "changed"
    if before["edge_count"] != after["edge_count"]:
        return True, "edge count changed", "changed"
    if _relative_delta(before["volume"], after["volume"]) > 1.0e-4:
        return True, "volume changed", "changed"
    if _relative_delta(before["area"], after["area"]) > 1.0e-4:
        return True, "surface area changed", "changed"
    if any(
        abs(first - second) > 1.0e-3
        for first, second in zip(before["bbox"], after["bbox"], strict=True)
    ):
        return True, "bounding box changed", "changed"
    if any(
        abs(first - second) > 1.0e-2
        for first, second in zip(before["center"], after["center"], strict=True)
    ):
        return True, "center of mass changed", "changed"
    if len(before["face_areas"]) == len(after["face_areas"]) and any(
        _relative_delta(first, second) > 1.0e-4
        for first, second in zip(
            before["face_areas"], after["face_areas"], strict=True
        )
    ):
        return True, "face-area distribution changed", "changed"
    return (
        False,
        "input/output kernel signatures are equivalent within tolerance",
        "no_op",
    )


def _instruction_allows_solid_count_change(task_description: str) -> bool:
    return re.search(
        r"\b(?:split|separate|merge|combine)\b.{0,30}\b(?:body|bodies|solid|solids)\b|"
        r"\b(?:add|create|remove|delete)\b.{0,20}\b(?:a |the |\d+ )?"
        r"(?:body|bodies|solid|solids)\b",
        task_description,
        flags=re.IGNORECASE,
    ) is not None


def _instruction_requires_extent_preservation(task_description: str) -> bool:
    return re.search(
        r"\b(?:blend|fillet|chamfer|hole|bore|pocket|slot)\b",
        task_description,
        flags=re.IGNORECASE,
    ) is not None


_STEP_EDIT_GUIDANCE = """

Imported STEP compatibility notes:
- `geom_type` is a `GeomType` enum, not a string. Compare with values such as
  `GeomType.CYLINDER`, `GeomType.PLANE`, or use `str(face.geom_type)` while
  inspecting unknown geometry.
- If Build123d's `export_step` rejects an otherwise valid imported or modified
  shape, use the operation-neutral atomic fallback:

```python
from cadrig.benchmarks.cadgenbench.step_io import robust_export_step
print("STEP export method:", robust_export_step(shape, "output.step"))
```

This helper only writes the BREP you provide; it does not select features,
repair topology, or perform the requested edit.

For a radial feature-count reduction, if a caller-observed split plane isolates
the repeated features as separate solids on one side, prefer cutting selected
feature volumes from the source rather than reconstructing the hub:

```python
from cadrig.benchmarks.cadgenbench.brep_fallback import (
    remove_isolated_radial_features_to_step,
)
print(remove_isolated_radial_features_to_step(
    "input.step", "output.step",
    axis="z", split_position_mm=<observed attachment coordinate>, side="min",
    expected_source_count=<observed count>, remove_indices=[<indices>],
    axis_center=(<observed center 1>, <observed center 2>),
))
```

The helper orders isolated features by polar angle, verifies the observed count,
cuts only the selected feature volumes, and rejects any solid-body-count change.
It does not redistribute the remaining features or infer the split plane.

For fillet removal on imported STEP geometry, Build123d may label an analytic
torus as a spline. Inspect it with `BRepAdaptor_Surface` and `GeomAbs_Torus`.
After observing the torus major/minor radii, center and exact matching face
count, remove only those faces with:

```python
from cadrig.benchmarks.cadgenbench.brep_fallback import (
    remove_analytic_toroidal_faces_to_step,
)
print(remove_analytic_toroidal_faces_to_step(
    "input.step", "output.step",
    major_radius_mm=<observed>, minor_radius_mm=<observed>,
    center=(<observed x>, <observed y>, <observed z>),
    expected_face_count=<observed exact count>,
))
```

The helper rejects ambiguous selections, solid-body-count changes and results
that do not reduce analytic torus faces. It does not infer which fillet the
instruction names; selection remains the agent's responsibility.
"""

_MESH_FALLBACK_GUIDANCE = """

Kernel fallback available: this editing input includes `input.mesh.npz`. If the
source STEP is invalid, or a direct BRep terminal-length edit repeatedly creates
invalid or unorientable output, select the feature axis and terminal side from
the task and render, then call:

```python
from cadrig.benchmarks.cadgenbench.mesh_fallback import extend_terminal_mesh_to_step
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
from cadrig.benchmarks.cadgenbench.mesh_fallback import (
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
from cadrig.benchmarks.cadgenbench.mesh_fallback import (
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
from cadrig.benchmarks.cadgenbench.mesh_fallback import (
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

For repeated drafted, filleted or tessellated walls that are not exposed as
simple planar BRep faces, cluster connected mesh regions by their approximate
normal direction and stated side of the part:

```python
from cadrig.benchmarks.cadgenbench.mesh_fallback import (
    inspect_oriented_mesh_regions,
    translate_oriented_mesh_regions_to_step,
)
regions = inspect_oriented_mesh_regions(
    "input.mesh.npz", normal_axis="<x|y|z>",
    center_axis="<x|y|z>", center_min_mm=<optional lower side bound>,
    center_max_mm=<optional upper side bound>, min_area_mm2=<minimum patch area>,
    normal_sign="<positive|negative|both>",
    bbox_long_axis="<x|y|z if the task states one>",
)
print(regions)
# After selecting one unique observed center per named feature instance:
print(translate_oriented_mesh_regions_to_step(
    "input.mesh.npz", "output.step", normal_axis="<x|y|z>",
    seeds=[(<x>, <y>, <z>), ...],
    distance_mm=<one signed value or one signed value per seed>,
))
```

The inspector reports connected region centers, averaged normals, bounding
boxes and areas. Choose seeds only after matching every qualifier in the task
and rendered views. The translation fallback moves precisely the caller-seeded
regions along the selected axis; it never chooses the regions or sign itself.
For opposing walls, pass a distance list so each seed moves in its own signed
direction. Start with restrictive side, normal-sign, long-axis and area filters
instead of dumping every mesh region into the model context.
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
    code_blocks = _extract_code_blocks(text)
    comment_only_done = bool(code_blocks) and all(
        all(not line.strip() or line.lstrip().startswith("#") for line in block.splitlines())
        and re.search(r"(?im)^\s*#\s*done\b", block) is not None
        for block in code_blocks
    )
    if not _strict_has_done_signal(text) and not comment_only_done:
        return False
    if code_blocks and not comment_only_done:
        return False
    accepted = (
        getattr(_validation_state, "passed", False) is True
        or getattr(_validation_state, "budget_stop", False) is True
    )
    if accepted and comment_only_done:
        _validation_state.comment_done_recovery_count = (
            getattr(_validation_state, "comment_done_recovery_count", 0) + 1
        )
    return accepted


def _validation_feedback_passed(auto_text: str, auto_iso: bytes | None) -> bool:
    valid = re.search(r"(?m)^Valid:\s+True\s*$", auto_text) is not None
    watertight = re.search(r"(?m)^Watertight:\s+True\s*$", auto_text) is not None
    has_error = "Validation error:" in auto_text or "Render failed:" in auto_text
    return valid and watertight and not has_error and auto_iso is not None


def _auto_validate_and_render_strict(*args: Any, **kwargs: Any) -> tuple[str, bytes | None]:
    """Remember whether the latest executed candidate cleared the mesh gate."""
    auto_text, auto_iso = _strict_auto_validate_and_render(*args, **kwargs)
    work_dir = Path(args[0] if args else kwargs["work_dir"])
    last_execution = args[1] if len(args) > 1 else kwargs.get("last_exe")
    execution_succeeded = getattr(last_execution, "success", False) is True
    produced = _candidate_names_produced(last_execution)
    if not produced:
        if (
            getattr(_validation_state, "is_editing", False) is True
            and execution_succeeded
        ):
            inspection_count = (
                getattr(_validation_state, "inspection_only_edit_turn_count", 0) + 1
            )
            _validation_state.inspection_only_edit_turn_count = inspection_count
            if inspection_count >= _EDIT_INSPECTION_SOFT_LIMIT:
                auto_text += (
                    "\nCADRIG controller: the edit inspection budget is exhausted. "
                    "The next response must attempt the requested modification and "
                    "export output.step; another inspection-only program may terminate "
                    "the attempt.\n"
                )
        _validation_state.passed = (
            getattr(_validation_state, "ever_passed", False) is True
        )
        return auto_text, auto_iso

    expected = getattr(_validation_state, "required_edit_instances", None)
    geometry_passed = execution_succeeded and _validation_feedback_passed(
        auto_text, auto_iso
    )
    change_passed = True
    change_reason = "not an editing task"
    change_status = "not_applicable"
    if geometry_passed and getattr(_validation_state, "is_editing", False) is True:
        change_passed, change_reason, change_status = _editing_candidate_changed(
            work_dir,
            produced,
            preserve_solid_count=getattr(
                _validation_state, "preserve_edit_solid_count", True
            ),
            preserve_extents=getattr(
                _validation_state, "preserve_edit_extents", False
            ),
            required_radius_transition=getattr(
                _validation_state, "required_blend_radius_transition", None
            ),
            required_hole_spacing_transition=getattr(
                _validation_state, "required_hole_spacing_transition", None
            ),
            require_fillet_removal=getattr(
                _validation_state, "require_fillet_removal", False
            ),
        )
    evidence_passed = _editing_count_evidence_passed(last_execution, expected)
    _validation_state.passed = geometry_passed and change_passed and evidence_passed
    if _validation_state.passed:
        _validation_state.ever_passed = True
        accepted = getattr(_validation_state, "accepted_candidate_code_hashes", set())
        code = getattr(last_execution, "code", "")
        if isinstance(code, str):
            accepted.add(_sha256_text(code))
        _validation_state.accepted_candidate_code_hashes = accepted
        for name in produced:
            candidate = work_dir / name
            if candidate.is_file():
                shutil.copy2(candidate, work_dir / f".cadrig_last_accepted_{name}")
        if expected is not None:
            auto_text += f"\nSemantic edit acceptance: PASS ({expected}/{expected} instances).\n"
        elif getattr(_validation_state, "is_editing", False) is True:
            auto_text += f"\nSemantic edit acceptance: PASS ({change_reason}).\n"
    else:
        for name in produced:
            candidate = work_dir / name
            backup = work_dir / f".cadrig_last_accepted_{name}"
            if backup.is_file():
                shutil.copy2(backup, candidate)
            elif candidate.exists():
                candidate.unlink()
        if geometry_passed and expected is not None and not evidence_passed:
            auto_text += (
                "\nSemantic edit acceptance: REJECTED. The explicit target-instance "
                f"contract requires {expected} matched and {expected} modified "
                "instances. The previous accepted candidate was restored.\n"
            )
        if geometry_passed and not change_passed:
            counter_name = (
                "no_op_rejection_count"
                if change_status == "no_op"
                else "semantic_invariance_rejection_count"
            )
            setattr(
                _validation_state,
                counter_name,
                getattr(_validation_state, counter_name, 0) + 1,
            )
            rejection_kind = (
                "a no-op"
                if change_status == "no_op"
                else "an edit-invariance violation"
            )
            auto_text += (
                f"\nSemantic edit acceptance: REJECTED as {rejection_kind}. "
                f"{change_reason}. The previous accepted candidate was restored.\n"
            )
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
    _validation_state.accepted_candidate_code_hashes = set()
    _validation_state.required_edit_instances = None
    _validation_state.no_op_rejection_count = 0
    _validation_state.semantic_invariance_rejection_count = 0
    _validation_state.no_code_provider_response_count = 0
    _validation_state.response_effort_downgrade_count = 0
    _validation_state.inspection_only_edit_turn_count = 0
    _validation_state.inspection_budget_stop_count = 0
    _validation_state.prompt_margin_calibration_count = 0
    _validation_state.adaptive_prompt_margin_tokens = _PROMPT_TOKEN_MARGIN
    _validation_state.comment_done_recovery_count = 0
    sidecars: list[Path] = []
    has_step_input = False
    for source in input_files or []:
        if source.suffix.lower() in {".step", ".stp"}:
            has_step_input = True
            sidecar = source.with_name(f"{source.stem}.mesh.npz")
            if sidecar.is_file():
                sidecars.append(sidecar)
    if has_step_input:
        _validation_state.is_editing = True
        _validation_state.preserve_edit_solid_count = (
            not _instruction_allows_solid_count_change(task_description)
        )
        _validation_state.preserve_edit_extents = (
            _instruction_requires_extent_preservation(task_description)
        )
        _validation_state.required_blend_radius_transition = (
            _explicit_blend_radius_transition(task_description)
        )
        _validation_state.required_hole_spacing_transition = (
            _explicit_hole_spacing_transition(task_description)
        )
        _validation_state.require_fillet_removal = (
            _instruction_requires_fillet_removal(task_description)
        )
        expected_instances = _explicit_each_target_count(task_description)
        _validation_state.required_edit_instances = expected_instances
        task_description += (
            _ARTIFACT_SAFETY_GUIDANCE
            + _EDITING_COMPLETENESS_GUIDANCE
            + _STEP_EDIT_GUIDANCE
        )
        if expected_instances is not None:
            task_description += _editing_cardinality_guidance(expected_instances)
    else:
        _validation_state.is_editing = False
        _validation_state.preserve_edit_solid_count = False
        _validation_state.preserve_edit_extents = False
        _validation_state.required_blend_radius_transition = None
        _validation_state.required_hole_spacing_transition = None
        _validation_state.require_fillet_removal = False
        task_description += _ARTIFACT_SAFETY_GUIDANCE + _GENERATION_COMPLETENESS_GUIDANCE
    if sidecars:
        if work_dir is None:
            work_dir = Path(tempfile.mkdtemp(prefix="cadgenbench_agent_"))
        work_dir.mkdir(parents=True, exist_ok=True)
        for sidecar in sidecars:
            shutil.copy2(sidecar, work_dir / sidecar.name)
        task_description += _MESH_FALLBACK_GUIDANCE
    _validation_state.run_active = True
    try:
        result = _strict_run_agent(
            task_description,
            *args,
            input_files=input_files,
            work_dir=work_dir,
            **kwargs,
        )
    finally:
        _validation_state.run_active = False
    if isinstance(result, AgentResult):
        result._cadrig_enforce_accepted_candidates = True
        result._cadrig_accepted_candidate_code_hashes = frozenset(
            getattr(_validation_state, "accepted_candidate_code_hashes", set())
        )
        result._cadrig_required_edit_instances = getattr(
            _validation_state, "required_edit_instances", None
        )
        result._cadrig_no_op_rejection_count = getattr(
            _validation_state, "no_op_rejection_count", 0
        )
        result._cadrig_semantic_invariance_rejection_count = getattr(
            _validation_state, "semantic_invariance_rejection_count", 0
        )
        result._cadrig_no_code_provider_response_count = getattr(
            _validation_state, "no_code_provider_response_count", 0
        )
        result._cadrig_response_effort_downgrade_count = getattr(
            _validation_state, "response_effort_downgrade_count", 0
        )
        result._cadrig_inspection_only_edit_turn_count = getattr(
            _validation_state, "inspection_only_edit_turn_count", 0
        )
        result._cadrig_inspection_budget_stop_count = getattr(
            _validation_state, "inspection_budget_stop_count", 0
        )
        result._cadrig_prompt_margin_calibration_count = getattr(
            _validation_state, "prompt_margin_calibration_count", 0
        )
        result._cadrig_adaptive_prompt_margin_tokens = getattr(
            _validation_state, "adaptive_prompt_margin_tokens", _PROMPT_TOKEN_MARGIN
        )
        result._cadrig_comment_done_recovery_count = getattr(
            _validation_state, "comment_done_recovery_count", 0
        )
    return result


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
    enforce_accepted = getattr(result, "_cadrig_enforce_accepted_candidates", False) is True
    accepted_hashes = set(
        getattr(result, "_cadrig_accepted_candidate_code_hashes", frozenset())
    )
    for record in result.turns:
        last_producer_succeeded: dict[str, bool] = {}
        last_producer_accepted: dict[str, bool] = {}
        for execution in record.code_executions:
            for name in _candidate_names_produced(execution):
                succeeded = getattr(execution, "success", False) is True
                last_producer_succeeded[name] = succeeded
                code = getattr(execution, "code", "")
                last_producer_accepted[name] = succeeded and (
                    not enforce_accepted
                    or (isinstance(code, str) and _sha256_text(code) in accepted_hashes)
                )

        turn_dir = destination / f"turn_{record.turn}"
        for name in _CANDIDATE_NAMES:
            candidate = turn_dir / name
            if not candidate.is_file():
                continue
            if last_producer_succeeded.get(name) is False:
                candidate.replace(turn_dir / f"rejected_failed_{name}")
            elif last_producer_accepted.get(name) is False:
                candidate.replace(turn_dir / f"rejected_unvalidated_{name}")
            elif last_producer_accepted.get(name) is True:
                continue
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
    if getattr(_validation_state, "run_active", False) is True:
        self._cadrig_enforce_accepted_candidates = True
        self._cadrig_accepted_candidate_code_hashes = frozenset(
            getattr(_validation_state, "accepted_candidate_code_hashes", set())
        )
        self._cadrig_required_edit_instances = getattr(
            _validation_state, "required_edit_instances", None
        )
        self._cadrig_no_code_provider_response_count = getattr(
            _validation_state, "no_code_provider_response_count", 0
        )
        self._cadrig_response_effort_downgrade_count = getattr(
            _validation_state, "response_effort_downgrade_count", 0
        )
        self._cadrig_no_op_rejection_count = getattr(
            _validation_state, "no_op_rejection_count", 0
        )
        self._cadrig_semantic_invariance_rejection_count = getattr(
            _validation_state, "semantic_invariance_rejection_count", 0
        )
        self._cadrig_inspection_only_edit_turn_count = getattr(
            _validation_state, "inspection_only_edit_turn_count", 0
        )
        self._cadrig_inspection_budget_stop_count = getattr(
            _validation_state, "inspection_budget_stop_count", 0
        )
        self._cadrig_prompt_margin_calibration_count = getattr(
            _validation_state, "prompt_margin_calibration_count", 0
        )
        self._cadrig_adaptive_prompt_margin_tokens = getattr(
            _validation_state, "adaptive_prompt_margin_tokens", _PROMPT_TOKEN_MARGIN
        )
        self._cadrig_comment_done_recovery_count = getattr(
            _validation_state, "comment_done_recovery_count", 0
        )
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
            "semantic_no_op_rejections": getattr(
                self, "_cadrig_no_op_rejection_count", 0
            ),
            "semantic_invariance_rejections": getattr(
                self, "_cadrig_semantic_invariance_rejection_count", 0
            ),
            "no_code_provider_responses": getattr(
                self, "_cadrig_no_code_provider_response_count", 0
            ),
            "response_effort_downgrades": getattr(
                self, "_cadrig_response_effort_downgrade_count", 0
            ),
            "inspection_only_edit_turns": getattr(
                self, "_cadrig_inspection_only_edit_turn_count", 0
            ),
            "inspection_budget_stops": getattr(
                self, "_cadrig_inspection_budget_stop_count", 0
            ),
            "prompt_margin_calibrations": getattr(
                self, "_cadrig_prompt_margin_calibration_count", 0
            ),
            "adaptive_prompt_margin_tokens": getattr(
                self,
                "_cadrig_adaptive_prompt_margin_tokens",
                _PROMPT_TOKEN_MARGIN,
            ),
            "comment_done_recoveries": getattr(
                self, "_cadrig_comment_done_recovery_count", 0
            ),
            "semantic_edit_contract": (
                {
                    "required_instances": getattr(
                        self, "_cadrig_required_edit_instances", None
                    ),
                    "accepted_candidate_count": len(
                        getattr(
                            self,
                            "_cadrig_accepted_candidate_code_hashes",
                            frozenset(),
                        )
                    ),
                }
                if getattr(self, "_cadrig_required_edit_instances", None) is not None
                else None
            ),
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
    inspection_count = getattr(
        _validation_state, "inspection_only_edit_turn_count", 0
    )
    if (
        inspection_count >= _EDIT_INSPECTION_HARD_LIMIT
        and getattr(_validation_state, "ever_passed", False) is not True
    ):
        _validation_state.inspection_budget_stop_count = (
            getattr(_validation_state, "inspection_budget_stop_count", 0) + 1
        )
        raise RuntimeError(
            "editing inspection budget exhausted without a candidate "
            f"({inspection_count} inspection-only turns)"
        )
    force_low_reasoning = (
        getattr(self, "_cadrig_force_low_reasoning", False) is True
        or inspection_count >= _EDIT_INSPECTION_SOFT_LIMIT
    )
    if (
        force_low_reasoning
        and kwargs.get("reasoning_effort") in {"medium", "high"}
    ):
        kwargs["reasoning_effort"] = "low"
        _validation_state.response_effort_downgrade_count = (
            getattr(_validation_state, "response_effort_downgrade_count", 0) + 1
        )
    try:
        token_cap = int(raw_cap)
        consumed = int(getattr(self, "_cadrig_consumed_tokens", 0))
        prompt_tokens = self.count_tokens(messages)
        requested = int(kwargs.get("max_tokens", 0))
        if inspection_count >= _EDIT_INSPECTION_SOFT_LIMIT:
            requested = min(requested, 4_096)
        prompt_margin = max(
            _PROMPT_TOKEN_MARGIN,
            int(
                getattr(
                    self, "_cadrig_adaptive_prompt_margin_tokens", _PROMPT_TOKEN_MARGIN
                )
            ),
        )
        allowance = completion_token_allowance(
            token_cap=token_cap,
            consumed=consumed,
            prompt_tokens=prompt_tokens + prompt_margin,
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
    observed_prompt_tokens = _usage_value(completion, "prompt_tokens")
    calibrated_margin = max(
        prompt_margin,
        observed_prompt_tokens - prompt_tokens + _PROMPT_CALIBRATION_BUFFER,
    )
    if calibrated_margin > prompt_margin:
        self._cadrig_adaptive_prompt_margin_tokens = calibrated_margin
        _validation_state.adaptive_prompt_margin_tokens = calibrated_margin
        _validation_state.prompt_margin_calibration_count = (
            getattr(_validation_state, "prompt_margin_calibration_count", 0) + 1
        )
    new_total = consumed + completion.total_tokens
    self._cadrig_consumed_tokens = new_total
    _record_provider_usage(completion, accepted=new_total <= token_cap)
    if new_total > token_cap:
        if getattr(_validation_state, "ever_passed", False) is True:
            _validation_state.budget_stop = True
            return CompletionResult(
                content=(
                    "[DONE]\nProvider usage crossed the token boundary; reject "
                    "this response and preserve the last strict-valid candidate."
                ),
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                model=str(getattr(self, "model", "unknown")),
                raw=None,
            )
        raise RuntimeError(
            f"provider-reported usage exceeded attempt token cap ({new_total} > {token_cap})"
        )
    if (
        not _extract_code_blocks(completion.content)
        and not _strict_has_done_signal(completion.content)
    ):
        self._cadrig_force_low_reasoning = True
        _validation_state.no_code_provider_response_count = (
            getattr(_validation_state, "no_code_provider_response_count", 0) + 1
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
