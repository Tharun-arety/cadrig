"""Deterministic mesh-domain fallbacks for topology-fragile STEP edits."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _validate_terminal_edit(
    axis: str, side: str, distance_mm: float, target_triangles: int
) -> tuple[int, float]:
    normalized_axis = axis.lower()
    normalized_side = side.lower()
    if normalized_axis not in {"x", "y", "z"}:
        raise ValueError("axis must be x, y, or z")
    if normalized_side not in {"min", "max", "both"}:
        raise ValueError("side must be min, max, or both")
    if distance_mm <= 0:
        raise ValueError("distance_mm must be positive")
    if target_triangles < 100:
        raise ValueError("target_triangles must be at least 100")
    direction = {"min": -1.0, "max": 1.0, "both": 0.0}[normalized_side]
    return {"x": 0, "y": 1, "z": 2}[normalized_axis], direction


def extend_terminal_mesh_to_step(
    mesh_npz: str | Path,
    output_step: str | Path,
    *,
    axis: str,
    side: str,
    distance_mm: float,
    target_triangles: int = 12_000,
) -> dict[str, Any]:
    """Extend one or both terminal feature caps into a strict-valid faceted STEP.

    This fallback is intended for an editing input whose STEP BREP cannot be
    oriented or meshed, or whose direct terminal edit remains invalid, but whose
    supplied CADGenBench ``*.mesh.npz`` sidecar is watertight. The caller must
    select the feature's axis and terminal side from task evidence; ``both``
    moves each terminal by ``distance_mm`` in opposite directions for a
    symmetric extension. No fixture identifiers or shape-specific dimensions
    are used.
    """
    axis_index, direction = _validate_terminal_edit(
        axis, side, distance_mm, target_triangles
    )

    import numpy as np
    source = Path(mesh_npz).resolve()
    destination = Path(output_step).resolve()
    with np.load(source, allow_pickle=False) as data:
        if not {"vertices", "triangles"}.issubset(data.files):
            raise ValueError("mesh sidecar must contain vertices and triangles")
        vertices = data["vertices"].astype(float, copy=True)
        triangles = data["triangles"].astype(int, copy=True)

    if direction == 0:
        min_mask = np.isclose(
            vertices[:, axis_index], float(vertices[:, axis_index].min()), atol=1.0e-6
        )
        max_mask = np.isclose(
            vertices[:, axis_index], float(vertices[:, axis_index].max()), atol=1.0e-6
        )
        if int(min_mask.sum()) < 3 or int(max_mask.sum()) < 3:
            raise RuntimeError("each terminal feature cap must have at least three vertices")
        vertices[min_mask, axis_index] -= distance_mm
        vertices[max_mask, axis_index] += distance_mm
        moved_vertex_count = int(min_mask.sum() + max_mask.sum())
    else:
        terminal = (
            float(vertices[:, axis_index].min())
            if direction < 0
            else float(vertices[:, axis_index].max())
        )
        mask = np.isclose(vertices[:, axis_index], terminal, atol=1.0e-6)
        if int(mask.sum()) < 3:
            raise RuntimeError("terminal feature cap has fewer than three vertices")
        vertices[mask, axis_index] += direction * distance_mm
        moved_vertex_count = int(mask.sum())

    triangle_count, size_bytes = _write_faceted_step(
        vertices, triangles, destination, target_triangles
    )
    return {
        "output_step": str(destination),
        "axis": axis.lower(),
        "side": side.lower(),
        "distance_mm": distance_mm,
        "moved_vertex_count": moved_vertex_count,
        "triangle_count": triangle_count,
        "size_bytes": size_bytes,
    }


def resize_cylindrical_mesh_region_to_step(
    mesh_npz: str | Path,
    output_step: str | Path,
    *,
    axis: str,
    center: tuple[float, float],
    current_radius_mm: float,
    radial_delta_mm: float,
    axis_min_mm: float | None = None,
    axis_max_mm: float | None = None,
    radial_tolerance_mm: float = 0.05,
    target_triangles: int = 12_000,
) -> dict[str, Any]:
    """Resize a caller-selected cylindrical mesh region and emit faceted STEP.

    ``center`` is expressed in the two coordinates perpendicular to ``axis``:
    ``(y, z)`` for X, ``(x, z)`` for Y and ``(x, y)`` for Z. Positive radial
    delta widens a bore or outer cylinder by moving only vertices within the
    selected radius tolerance and optional axial span. Feature identification
    remains the caller's responsibility; no fixture-specific values are used.
    """
    import numpy as np

    normalized_axis = axis.lower()
    if normalized_axis not in {"x", "y", "z"}:
        raise ValueError("axis must be x, y, or z")
    if len(center) != 2:
        raise ValueError("center must contain two perpendicular coordinates")
    if current_radius_mm <= 0:
        raise ValueError("current_radius_mm must be positive")
    if current_radius_mm + radial_delta_mm <= 0:
        raise ValueError("radial_delta_mm would produce a non-positive radius")
    if radial_tolerance_mm <= 0:
        raise ValueError("radial_tolerance_mm must be positive")
    if target_triangles < 100:
        raise ValueError("target_triangles must be at least 100")
    if (axis_min_mm is None) != (axis_max_mm is None):
        raise ValueError("axis_min_mm and axis_max_mm must be provided together")
    if axis_min_mm is not None and axis_max_mm is not None and axis_min_mm >= axis_max_mm:
        raise ValueError("axis_min_mm must be less than axis_max_mm")

    source = Path(mesh_npz).resolve()
    destination = Path(output_step).resolve()
    with np.load(source, allow_pickle=False) as data:
        if not {"vertices", "triangles"}.issubset(data.files):
            raise ValueError("mesh sidecar must contain vertices and triangles")
        vertices = data["vertices"].astype(float, copy=True)
        triangles = data["triangles"].astype(int, copy=True)

    axis_index = {"x": 0, "y": 1, "z": 2}[normalized_axis]
    radial_indices = {
        "x": (1, 2),
        "y": (0, 2),
        "z": (0, 1),
    }[normalized_axis]
    offsets = vertices[:, radial_indices] - np.asarray(center, dtype=float)
    radii = np.linalg.norm(offsets, axis=1)
    mask = np.isclose(radii, current_radius_mm, atol=radial_tolerance_mm)
    if axis_min_mm is not None and axis_max_mm is not None:
        mask &= vertices[:, axis_index] >= axis_min_mm - radial_tolerance_mm
        mask &= vertices[:, axis_index] <= axis_max_mm + radial_tolerance_mm
    if int(mask.sum()) < 6:
        raise RuntimeError("selected cylindrical region has fewer than six vertices")
    if bool(np.any(radii[mask] <= 0)):
        raise RuntimeError("selected cylindrical region includes an axis vertex")

    selected_indices = np.flatnonzero(mask)
    scale = (radii[mask] + radial_delta_mm) / radii[mask]
    vertices[np.ix_(selected_indices, radial_indices)] = (
        np.asarray(center, dtype=float) + offsets[mask] * scale[:, None]
    )
    triangle_count, size_bytes = _write_faceted_step(
        vertices, triangles, destination, target_triangles
    )
    return {
        "output_step": str(destination),
        "axis": normalized_axis,
        "center": [float(value) for value in center],
        "current_radius_mm": current_radius_mm,
        "radial_delta_mm": radial_delta_mm,
        "axis_min_mm": axis_min_mm,
        "axis_max_mm": axis_max_mm,
        "moved_vertex_count": int(mask.sum()),
        "triangle_count": triangle_count,
        "size_bytes": size_bytes,
    }


def translate_planar_annulus_mesh_region_to_step(
    mesh_npz: str | Path,
    output_step: str | Path,
    *,
    axis: str,
    center: tuple[float, float],
    plane_position_mm: float,
    inner_radius_mm: float,
    outer_radius_mm: float,
    distance_mm: float,
    plane_tolerance_mm: float = 0.05,
    radial_tolerance_mm: float = 0.05,
    target_triangles: int = 12_000,
) -> dict[str, Any]:
    """Translate one caller-selected planar annulus and emit faceted STEP.

    ``center`` is expressed in the two coordinates perpendicular to ``axis``.
    A positive distance moves toward the positive axis direction. The annulus
    is selected by its source plane and inner/outer radii; connected mesh
    vertices outside that local region remain unchanged. Feature discovery and
    direction selection remain the caller's responsibility.
    """
    import numpy as np

    normalized_axis = axis.lower()
    if normalized_axis not in {"x", "y", "z"}:
        raise ValueError("axis must be x, y, or z")
    if len(center) != 2:
        raise ValueError("center must contain two perpendicular coordinates")
    if inner_radius_mm < 0:
        raise ValueError("inner_radius_mm must be non-negative")
    if outer_radius_mm <= inner_radius_mm:
        raise ValueError("outer_radius_mm must be greater than inner_radius_mm")
    if distance_mm == 0:
        raise ValueError("distance_mm must be non-zero")
    if plane_tolerance_mm <= 0:
        raise ValueError("plane_tolerance_mm must be positive")
    if radial_tolerance_mm <= 0:
        raise ValueError("radial_tolerance_mm must be positive")
    if target_triangles < 100:
        raise ValueError("target_triangles must be at least 100")

    source = Path(mesh_npz).resolve()
    destination = Path(output_step).resolve()
    with np.load(source, allow_pickle=False) as data:
        if not {"vertices", "triangles"}.issubset(data.files):
            raise ValueError("mesh sidecar must contain vertices and triangles")
        vertices = data["vertices"].astype(float, copy=True)
        triangles = data["triangles"].astype(int, copy=True)

    axis_index = {"x": 0, "y": 1, "z": 2}[normalized_axis]
    radial_indices = {
        "x": (1, 2),
        "y": (0, 2),
        "z": (0, 1),
    }[normalized_axis]
    offsets = vertices[:, radial_indices] - np.asarray(center, dtype=float)
    radii = np.linalg.norm(offsets, axis=1)
    mask = np.isclose(
        vertices[:, axis_index], plane_position_mm, atol=plane_tolerance_mm
    )
    mask &= radii >= inner_radius_mm - radial_tolerance_mm
    mask &= radii <= outer_radius_mm + radial_tolerance_mm
    if int(mask.sum()) < 6:
        raise RuntimeError("selected planar annulus has fewer than six vertices")

    vertices[mask, axis_index] += distance_mm
    triangle_count, size_bytes = _write_faceted_step(
        vertices, triangles, destination, target_triangles
    )
    return {
        "output_step": str(destination),
        "axis": normalized_axis,
        "center": [float(value) for value in center],
        "plane_position_mm": plane_position_mm,
        "inner_radius_mm": inner_radius_mm,
        "outer_radius_mm": outer_radius_mm,
        "distance_mm": distance_mm,
        "moved_vertex_count": int(mask.sum()),
        "triangle_count": triangle_count,
        "size_bytes": size_bytes,
    }


def translate_planar_mesh_patch_to_step(
    mesh_npz: str | Path,
    output_step: str | Path,
    *,
    axis: str,
    plane_position_mm: float,
    seed: tuple[float, float],
    distance_mm: float,
    plane_tolerance_mm: float = 0.05,
    target_triangles: int = 12_000,
) -> dict[str, Any]:
    """Translate the coplanar mesh patch nearest a caller-selected seed.

    This is the topology-safe fallback for a planar BRep face whose intended
    circular or annular boundary was split, clipped, or converted into line and
    spline edges during exchange. ``seed`` is expressed in the coordinates
    perpendicular to ``axis`` and should come from the observed target face's
    center. Only the edge-connected coplanar triangle component nearest that
    seed is moved.
    """
    from collections import defaultdict, deque

    import numpy as np

    normalized_axis = axis.lower()
    if normalized_axis not in {"x", "y", "z"}:
        raise ValueError("axis must be x, y, or z")
    if len(seed) != 2:
        raise ValueError("seed must contain two perpendicular coordinates")
    if distance_mm == 0:
        raise ValueError("distance_mm must be non-zero")
    if plane_tolerance_mm <= 0:
        raise ValueError("plane_tolerance_mm must be positive")
    if target_triangles < 100:
        raise ValueError("target_triangles must be at least 100")

    source = Path(mesh_npz).resolve()
    destination = Path(output_step).resolve()
    with np.load(source, allow_pickle=False) as data:
        if not {"vertices", "triangles"}.issubset(data.files):
            raise ValueError("mesh sidecar must contain vertices and triangles")
        vertices = data["vertices"].astype(float, copy=True)
        triangles = data["triangles"].astype(int, copy=True)

    axis_index = {"x": 0, "y": 1, "z": 2}[normalized_axis]
    radial_indices = {
        "x": (1, 2),
        "y": (0, 2),
        "z": (0, 1),
    }[normalized_axis]
    triangle_axis_values = vertices[triangles, axis_index]
    coplanar = (
        np.ptp(triangle_axis_values, axis=1) <= plane_tolerance_mm
    ) & np.isclose(
        triangle_axis_values.mean(axis=1),
        plane_position_mm,
        atol=plane_tolerance_mm,
    )
    candidate_indices = np.flatnonzero(coplanar)
    if len(candidate_indices) == 0:
        raise RuntimeError("no triangles match the selected plane")

    triangle_centers = vertices[triangles[candidate_indices]][
        :, :, radial_indices
    ].mean(axis=1)
    seed_array = np.asarray(seed, dtype=float)
    seed_triangle = int(
        candidate_indices[
            np.argmin(np.linalg.norm(triangle_centers - seed_array, axis=1))
        ]
    )

    edge_owners: dict[tuple[int, int], list[int]] = defaultdict(list)
    for triangle_index in candidate_indices:
        triangle = triangles[int(triangle_index)]
        for first, second in ((0, 1), (1, 2), (2, 0)):
            edge = tuple(sorted((int(triangle[first]), int(triangle[second]))))
            edge_owners[edge].append(int(triangle_index))
    neighbors: dict[int, set[int]] = defaultdict(set)
    for owners in edge_owners.values():
        if len(owners) == 2:
            neighbors[owners[0]].add(owners[1])
            neighbors[owners[1]].add(owners[0])

    component = {seed_triangle}
    queue = deque([seed_triangle])
    while queue:
        current = queue.popleft()
        for neighbor in neighbors[current]:
            if neighbor not in component:
                component.add(neighbor)
                queue.append(neighbor)
    moved_indices = np.unique(triangles[list(component)])
    if len(moved_indices) < 3:
        raise RuntimeError("selected planar patch has fewer than three vertices")

    vertices[moved_indices, axis_index] += distance_mm
    triangle_count, size_bytes = _write_faceted_step(
        vertices, triangles, destination, target_triangles
    )
    return {
        "output_step": str(destination),
        "axis": normalized_axis,
        "plane_position_mm": plane_position_mm,
        "seed": [float(value) for value in seed],
        "distance_mm": distance_mm,
        "component_triangle_count": len(component),
        "moved_vertex_count": len(moved_indices),
        "triangle_count": triangle_count,
        "size_bytes": size_bytes,
    }


def _oriented_mesh_components(
    vertices: Any,
    triangles: Any,
    *,
    normal_axis_index: int,
    min_abs_normal: float,
) -> list[dict[str, Any]]:
    from collections import defaultdict, deque

    import numpy as np

    triangle_points = vertices[triangles]
    cross = np.cross(
        triangle_points[:, 1] - triangle_points[:, 0],
        triangle_points[:, 2] - triangle_points[:, 0],
    )
    double_area = np.linalg.norm(cross, axis=1)
    normals = cross / np.maximum(double_area[:, None], 1.0e-12)
    candidate_indices = np.flatnonzero(
        (double_area > 1.0e-12)
        & (np.abs(normals[:, normal_axis_index]) >= min_abs_normal)
    )

    edge_owners: dict[tuple[int, int], list[int]] = defaultdict(list)
    for triangle_index in candidate_indices:
        triangle = triangles[int(triangle_index)]
        for first, second in ((0, 1), (1, 2), (2, 0)):
            edge = tuple(sorted((int(triangle[first]), int(triangle[second]))))
            edge_owners[edge].append(int(triangle_index))
    neighbors: dict[int, set[int]] = defaultdict(set)
    for owners in edge_owners.values():
        if len(owners) == 2:
            neighbors[owners[0]].add(owners[1])
            neighbors[owners[1]].add(owners[0])

    remaining = {int(index) for index in candidate_indices}
    components: list[dict[str, Any]] = []
    triangle_centers = triangle_points.mean(axis=1)
    while remaining:
        seed = remaining.pop()
        component = {seed}
        queue = deque([seed])
        while queue:
            current = queue.popleft()
            for neighbor in neighbors[current]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)

        indices = np.asarray(sorted(component), dtype=int)
        vertex_indices = np.unique(triangles[indices])
        component_points = vertices[vertex_indices]
        weights = double_area[indices]
        components.append(
            {
                "triangle_indices": indices,
                "vertex_indices": vertex_indices,
                "area": float(weights.sum() / 2.0),
                "center": np.average(triangle_centers[indices], axis=0, weights=weights),
                "normal": np.average(normals[indices], axis=0, weights=weights),
                "bbox": np.ptp(component_points, axis=0),
            }
        )
    return components


def inspect_oriented_mesh_regions(
    mesh_npz: str | Path,
    *,
    normal_axis: str,
    min_abs_normal: float = 0.55,
    center_axis: str | None = None,
    center_min_mm: float | None = None,
    center_max_mm: float | None = None,
    min_area_mm2: float = 5.0,
    normal_sign: str = "both",
    bbox_long_axis: str | None = None,
) -> list[dict[str, Any]]:
    """Group connected mesh patches with a caller-selected normal orientation.

    The result is a deterministic feature-discovery aid for repeated drafted,
    filleted or tessellated walls that do not survive STEP exchange as simple
    planar faces. Optional center bounds localize the search to a stated side of
    the part. No region is inferred or edited automatically.
    """
    import numpy as np

    normalized_normal_axis = normal_axis.lower()
    if normalized_normal_axis not in {"x", "y", "z"}:
        raise ValueError("normal_axis must be x, y, or z")
    if not 0 < min_abs_normal <= 1:
        raise ValueError("min_abs_normal must be in (0, 1]")
    if min_area_mm2 <= 0:
        raise ValueError("min_area_mm2 must be positive")
    normalized_sign = normal_sign.lower()
    if normalized_sign not in {"positive", "negative", "both"}:
        raise ValueError("normal_sign must be positive, negative, or both")
    if (center_min_mm is not None or center_max_mm is not None) and center_axis is None:
        raise ValueError("center_axis is required when center bounds are used")
    normalized_center_axis = center_axis.lower() if center_axis is not None else None
    if normalized_center_axis not in {None, "x", "y", "z"}:
        raise ValueError("center_axis must be x, y, or z")
    normalized_bbox_axis = bbox_long_axis.lower() if bbox_long_axis is not None else None
    if normalized_bbox_axis not in {None, "x", "y", "z"}:
        raise ValueError("bbox_long_axis must be x, y, or z")
    if (
        center_min_mm is not None
        and center_max_mm is not None
        and center_min_mm > center_max_mm
    ):
        raise ValueError("center_min_mm must not exceed center_max_mm")

    with np.load(Path(mesh_npz).resolve(), allow_pickle=False) as data:
        if not {"vertices", "triangles"}.issubset(data.files):
            raise ValueError("mesh sidecar must contain vertices and triangles")
        vertices = data["vertices"].astype(float, copy=False)
        triangles = data["triangles"].astype(int, copy=False)
    axis_indices = {"x": 0, "y": 1, "z": 2}
    components = _oriented_mesh_components(
        vertices,
        triangles,
        normal_axis_index=axis_indices[normalized_normal_axis],
        min_abs_normal=min_abs_normal,
    )
    center_axis_index = (
        axis_indices[normalized_center_axis]
        if normalized_center_axis is not None
        else None
    )
    normal_axis_index = axis_indices[normalized_normal_axis]
    bbox_axis_index = (
        axis_indices[normalized_bbox_axis]
        if normalized_bbox_axis is not None
        else None
    )
    filtered = []
    for component in components:
        center = component["center"]
        if component["area"] < min_area_mm2:
            continue
        normal_value = float(component["normal"][normal_axis_index])
        if normalized_sign == "positive" and normal_value <= 0:
            continue
        if normalized_sign == "negative" and normal_value >= 0:
            continue
        if bbox_axis_index is not None:
            bbox = component["bbox"]
            other_sizes = [
                float(value)
                for index, value in enumerate(bbox)
                if index != bbox_axis_index
            ]
            if float(bbox[bbox_axis_index]) < max(other_sizes):
                continue
        if center_axis_index is not None:
            coordinate = float(center[center_axis_index])
            if center_min_mm is not None and coordinate < center_min_mm:
                continue
            if center_max_mm is not None and coordinate > center_max_mm:
                continue
        filtered.append(component)
    filtered.sort(
        key=lambda component: (
            -component["area"],
            *(float(value) for value in component["center"]),
        )
    )
    return [
        {
            "region_id": index,
            "area_mm2": round(component["area"], 6),
            "center": [round(float(value), 6) for value in component["center"]],
            "normal": [round(float(value), 6) for value in component["normal"]],
            "bbox": [round(float(value), 6) for value in component["bbox"]],
            "triangle_count": len(component["triangle_indices"]),
            "vertex_count": len(component["vertex_indices"]),
        }
        for index, component in enumerate(filtered)
    ]


def translate_oriented_mesh_regions_to_step(
    mesh_npz: str | Path,
    output_step: str | Path,
    *,
    normal_axis: str,
    seeds: list[tuple[float, float, float]],
    distance_mm: float | list[float],
    min_abs_normal: float = 0.55,
    seed_tolerance_mm: float = 5.0,
    target_triangles: int = 12_000,
) -> dict[str, Any]:
    """Translate caller-selected connected oriented mesh regions along an axis.

    ``distance_mm`` may be one signed translation shared by every seed or a
    list containing one signed translation per seed. Selected regions must not
    overlap, and the resulting mesh must remain watertight and orientable.
    """
    import numpy as np

    normalized_axis = normal_axis.lower()
    if normalized_axis not in {"x", "y", "z"}:
        raise ValueError("normal_axis must be x, y, or z")
    if not seeds or any(len(seed) != 3 for seed in seeds):
        raise ValueError("seeds must contain one or more 3D centers")
    if isinstance(distance_mm, list):
        if len(distance_mm) != len(seeds):
            raise ValueError("distance_mm list must contain one value per seed")
        distances = [float(value) for value in distance_mm]
    else:
        distances = [float(distance_mm)] * len(seeds)
    if any(value == 0 for value in distances):
        raise ValueError("distance_mm values must be non-zero")
    if not 0 < min_abs_normal <= 1:
        raise ValueError("min_abs_normal must be in (0, 1]")
    if seed_tolerance_mm <= 0:
        raise ValueError("seed_tolerance_mm must be positive")
    if target_triangles < 100:
        raise ValueError("target_triangles must be at least 100")

    source = Path(mesh_npz).resolve()
    destination = Path(output_step).resolve()
    with np.load(source, allow_pickle=False) as data:
        if not {"vertices", "triangles"}.issubset(data.files):
            raise ValueError("mesh sidecar must contain vertices and triangles")
        vertices = data["vertices"].astype(float, copy=True)
        triangles = data["triangles"].astype(int, copy=True)
    axis_index = {"x": 0, "y": 1, "z": 2}[normalized_axis]
    components = _oriented_mesh_components(
        vertices,
        triangles,
        normal_axis_index=axis_index,
        min_abs_normal=min_abs_normal,
    )
    if not components:
        raise RuntimeError("no oriented mesh regions matched the selected normal")

    centers = np.asarray([component["center"] for component in components])
    selected_indices: list[int] = []
    for seed in seeds:
        seed_distances = np.linalg.norm(
            centers - np.asarray(seed, dtype=float), axis=1
        )
        component_index = int(np.argmin(seed_distances))
        if float(seed_distances[component_index]) > seed_tolerance_mm:
            raise RuntimeError("no oriented mesh region is close enough to a seed")
        if component_index in selected_indices:
            raise RuntimeError("multiple seeds selected the same oriented mesh region")
        selected_indices.append(component_index)

    moved_vertex_sets: list[set[int]] = []
    for component_index, distance in zip(selected_indices, distances, strict=True):
        vertex_indices = components[component_index]["vertex_indices"]
        current_set = {int(index) for index in vertex_indices}
        if any(current_set & previous for previous in moved_vertex_sets):
            raise RuntimeError("selected oriented mesh regions share boundary vertices")
        moved_vertex_sets.append(current_set)
        vertices[vertex_indices, axis_index] += distance
    moved_indices = np.asarray(sorted(set().union(*moved_vertex_sets)), dtype=int)
    triangle_count, size_bytes = _write_faceted_step(
        vertices, triangles, destination, target_triangles
    )
    selected_regions = [
        {
            "center": [
                round(float(value), 6) for value in components[index]["center"]
            ],
            "normal": [
                round(float(value), 6) for value in components[index]["normal"]
            ],
            "area_mm2": round(float(components[index]["area"]), 6),
        }
        for index in selected_indices
    ]
    return {
        "output_step": str(destination),
        "normal_axis": normalized_axis,
        "distance_mm": distances if isinstance(distance_mm, list) else distances[0],
        "selected_regions": selected_regions,
        "moved_vertex_count": len(moved_indices),
        "triangle_count": triangle_count,
        "size_bytes": size_bytes,
    }


def _write_faceted_step(
    vertices: Any,
    triangles: Any,
    destination: Path,
    target_triangles: int,
) -> tuple[int, int]:
    """Validate, optionally decimate and sew an indexed mesh into one solid."""
    import numpy as np
    import open3d as o3d
    import trimesh
    from build123d import Shape, export_step
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakePolygon,
        BRepBuilderAPI_Sewing,
    )
    from OCP.BRepLib import BRepLib
    from OCP.gp import gp_Pnt
    from OCP.ShapeFix import ShapeFix_Shell
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Solid

    edited = trimesh.Trimesh(vertices=vertices, faces=triangles, process=False)
    if not edited.is_watertight or not edited.is_winding_consistent:
        raise RuntimeError("mesh edit damaged topology")

    mesh = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(vertices),
        o3d.utility.Vector3iVector(triangles),
    )
    if len(triangles) > target_triangles:
        mesh = mesh.simplify_quadric_decimation(
            target_triangles, boundary_weight=10.0
        )
        mesh.remove_degenerate_triangles()
        mesh.remove_duplicated_triangles()
        mesh.remove_duplicated_vertices()
        mesh.remove_unreferenced_vertices()
    if not mesh.is_watertight() or not mesh.is_orientable():
        raise RuntimeError("output mesh is not watertight and orientable")
    vertices = np.asarray(mesh.vertices)
    triangles = np.asarray(mesh.triangles)

    sewing = BRepBuilderAPI_Sewing(1.0e-6)
    for triangle in triangles:
        polygon = BRepBuilderAPI_MakePolygon()
        for vertex_index in triangle:
            x, y, z = vertices[int(vertex_index)]
            polygon.Add(gp_Pnt(float(x), float(y), float(z)))
        polygon.Close()
        sewing.Add(BRepBuilderAPI_MakeFace(polygon.Wire()).Face())
    sewing.Perform()
    if sewing.NbFreeEdges() != 0:
        raise RuntimeError(f"sewn mesh has {sewing.NbFreeEdges()} free edges")

    shell_explorer = TopExp_Explorer(sewing.SewedShape(), TopAbs_SHELL)
    if not shell_explorer.More():
        raise RuntimeError("mesh sewing produced no shell")
    shell_fixer = ShapeFix_Shell(TopoDS.Shell_s(shell_explorer.Current()))
    shell_fixer.Perform()

    builder = BRep_Builder()
    solid = TopoDS_Solid()
    builder.MakeSolid(solid)
    builder.Add(solid, shell_fixer.Shell())
    if not BRepLib.OrientClosedSolid_s(solid):
        raise RuntimeError("rebuilt mesh shell could not be oriented as a closed solid")

    destination.parent.mkdir(parents=True, exist_ok=True)
    export_step(Shape(solid), destination)
    return len(triangles), destination.stat().st_size
