"""Deterministic mesh-domain fallback for edits on invalid source STEP files."""

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
    oriented or meshed, but whose supplied CADGenBench ``*.mesh.npz`` sidecar is
    watertight. The caller must select the feature's axis and terminal side from
    task evidence; ``both`` moves each terminal by ``distance_mm`` in opposite
    directions for a symmetric extension. No fixture identifiers or
    shape-specific dimensions are used.
    """
    axis_index, direction = _validate_terminal_edit(
        axis, side, distance_mm, target_triangles
    )

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

    edited = trimesh.Trimesh(vertices=vertices, faces=triangles, process=False)
    if not edited.is_watertight or not edited.is_winding_consistent:
        raise RuntimeError("terminal extension damaged mesh topology")

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
        raise RuntimeError("decimated mesh is not watertight and orientable")
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
    return {
        "output_step": str(destination),
        "axis": axis.lower(),
        "side": side.lower(),
        "distance_mm": distance_mm,
        "moved_vertex_count": moved_vertex_count,
        "triangle_count": len(triangles),
        "size_bytes": destination.stat().st_size,
    }
