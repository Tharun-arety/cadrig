"""Deterministic BRep fallbacks for topology-fragile STEP edits."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .step_io import robust_export_step


def remove_isolated_radial_features_to_step(
    input_step: str | Path,
    output_step: str | Path,
    *,
    axis: str,
    split_position_mm: float,
    side: str,
    expected_source_count: int,
    remove_indices: list[int],
    axis_center: tuple[float, float] = (0.0, 0.0),
) -> dict[str, Any]:
    """Remove caller-selected radial features that isolate across a split plane.

    This operation is suitable when repeated attached features become separate
    solids after retaining one side of a plane, such as blades below a shroud.
    Features are ordered deterministically by polar angle around ``axis``. The
    caller remains responsible for choosing the plane, side, center and indices
    from task evidence. The source body is cut directly, so unrelated geometry
    is not reconstructed or approximated.
    """
    from build123d import BuildPart, Keep, Plane, add, import_step, split

    normalized_axis = axis.lower()
    normalized_side = side.lower()
    if normalized_axis not in {"x", "y", "z"}:
        raise ValueError("axis must be x, y, or z")
    if normalized_side not in {"min", "max"}:
        raise ValueError("side must be min or max")
    if expected_source_count < 2:
        raise ValueError("expected_source_count must be at least two")
    if not remove_indices or len(set(remove_indices)) != len(remove_indices):
        raise ValueError("remove_indices must contain unique indices")
    if len(remove_indices) >= expected_source_count:
        raise ValueError("at least one radial feature must remain")
    if len(axis_center) != 2:
        raise ValueError("axis_center must contain two perpendicular coordinates")

    source = Path(input_step).resolve()
    destination = Path(output_step).resolve()
    shape = import_step(source)
    source_solid_count = len(shape.solids())
    if source_solid_count < 1:
        raise RuntimeError("input STEP contains no solid")

    axis_index = {"x": 0, "y": 1, "z": 2}[normalized_axis]
    radial_indices = {
        "x": (1, 2),
        "y": (0, 2),
        "z": (0, 1),
    }[normalized_axis]
    origin = [0.0, 0.0, 0.0]
    origin[axis_index] = float(split_position_mm)
    normal = [0.0, 0.0, 0.0]
    normal[axis_index] = 1.0
    keep = Keep.BOTTOM if normalized_side == "min" else Keep.TOP
    with BuildPart() as isolated_builder:
        add(shape)
        split(
            bisect_by=Plane(origin=tuple(origin), z_dir=tuple(normal)),
            keep=keep,
        )
    features = list(isolated_builder.part.solids())
    if len(features) != expected_source_count:
        raise RuntimeError(
            "split plane isolated "
            f"{len(features)} features; expected {expected_source_count}"
        )

    def angle(feature: Any) -> float:
        center = feature.center()
        coordinates = (float(center.X), float(center.Y), float(center.Z))
        first = coordinates[radial_indices[0]] - float(axis_center[0])
        second = coordinates[radial_indices[1]] - float(axis_center[1])
        return math.atan2(second, first) % (2.0 * math.pi)

    ordered = sorted(features, key=angle)
    if min(remove_indices) < 0 or max(remove_indices) >= len(ordered):
        raise ValueError("remove_indices contains an out-of-range feature index")
    selected = [ordered[index] for index in remove_indices]
    result = shape.cut(*selected).clean()
    if len(result.solids()) != source_solid_count:
        raise RuntimeError("feature removal changed the source solid-body count")
    if result.volume >= shape.volume:
        raise RuntimeError("feature removal did not reduce source volume")

    method = robust_export_step(result, destination)
    return {
        "output_step": str(destination),
        "axis": normalized_axis,
        "split_position_mm": float(split_position_mm),
        "side": normalized_side,
        "source_feature_count": len(ordered),
        "remaining_feature_count": len(ordered) - len(selected),
        "removed_indices": list(remove_indices),
        "removed_centers": [
            [
                round(float(feature.center().X), 6),
                round(float(feature.center().Y), 6),
                round(float(feature.center().Z), 6),
            ]
            for feature in selected
        ],
        "source_solid_count": source_solid_count,
        "output_solid_count": len(result.solids()),
        "volume_removed_mm3": round(float(shape.volume - result.volume), 6),
        "export_method": method,
        "size_bytes": destination.stat().st_size,
    }


def remove_analytic_toroidal_faces_to_step(
    input_step: str | Path,
    output_step: str | Path,
    *,
    major_radius_mm: float,
    minor_radius_mm: float,
    center: tuple[float, float, float],
    expected_face_count: int,
    tolerance_mm: float = 0.05,
) -> dict[str, Any]:
    """Defeature caller-identified analytic torus faces without reconstruction."""
    from build123d import Solid, import_step
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
    from OCP.GeomAbs import GeomAbs_Torus
    from OCP.TopTools import TopTools_ListOfShape

    if major_radius_mm <= 0 or minor_radius_mm <= 0:
        raise ValueError("torus radii must be positive")
    if expected_face_count < 1:
        raise ValueError("expected_face_count must be positive")
    if tolerance_mm <= 0:
        raise ValueError("tolerance_mm must be positive")
    if len(center) != 3:
        raise ValueError("center must contain x, y, and z")

    source = Path(input_step).resolve()
    destination = Path(output_step).resolve()
    shape = import_step(source)
    source_solid_count = len(shape.solids())
    matches = []
    source_torus_count = 0
    for face in shape.faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() != GeomAbs_Torus:
            continue
        source_torus_count += 1
        torus = surface.Torus()
        location = torus.Location()
        if (
            abs(float(torus.MajorRadius()) - major_radius_mm) <= tolerance_mm
            and abs(float(torus.MinorRadius()) - minor_radius_mm) <= tolerance_mm
            and all(
                abs(actual - expected) <= tolerance_mm
                for actual, expected in zip(
                    (float(location.X()), float(location.Y()), float(location.Z())),
                    center,
                    strict=True,
                )
            )
        ):
            matches.append(face)
    if len(matches) != expected_face_count:
        raise RuntimeError(
            f"matched {len(matches)} torus faces; expected {expected_face_count}"
        )

    faces = TopTools_ListOfShape()
    for face in matches:
        faces.Append(face.wrapped)
    operation = BRepAlgoAPI_Defeaturing()
    operation.SetShape(shape.wrapped)
    operation.AddFacesToRemove(faces)
    operation.SetRunParallel(True)
    operation.Build()
    if not operation.IsDone() or operation.Shape().IsNull():
        raise RuntimeError("OpenCascade torus defeaturing failed")
    result = Solid(operation.Shape())
    if len(result.solids()) != source_solid_count:
        raise RuntimeError("torus removal changed the source solid-body count")

    result_torus_count = 0
    for face in result.faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if surface.GetType() == GeomAbs_Torus:
            result_torus_count += 1
    if result_torus_count >= source_torus_count:
        raise RuntimeError("torus removal did not reduce analytic torus faces")

    method = robust_export_step(result, destination)
    return {
        "output_step": str(destination),
        "major_radius_mm": float(major_radius_mm),
        "minor_radius_mm": float(minor_radius_mm),
        "center": [float(value) for value in center],
        "matched_face_count": len(matches),
        "source_torus_count": source_torus_count,
        "output_torus_count": result_torus_count,
        "source_solid_count": source_solid_count,
        "output_solid_count": len(result.solids()),
        "export_method": method,
        "size_bytes": destination.stat().st_size,
    }
