from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cadcopilot.benchmarks.cadgenbench import brep_fallback, mesh_fallback, official_cli
from cadcopilot.benchmarks.cadgenbench.compat import (
    completion_token_allowance,
    extract_code_blocks_tolerant,
)
from cadcopilot.benchmarks.cadgenbench.dataset import find_sanity_script
from cadcopilot.benchmarks.cadgenbench.mesh_fallback import _validate_terminal_edit
from cadcopilot.benchmarks.cadgenbench.package import package_run
from cadcopilot.benchmarks.cadgenbench.runner import (
    CadgenbenchRunConfig,
    build_baseline_command,
    run_official_baseline,
)
from cadcopilot.benchmarks.cadgenbench.sanity import verify_run


def _strict_fence_extractor(text: str, lang: str) -> list[str]:
    import re

    return re.findall(rf"```{re.escape(lang)}\s*\n(.*?)```", text, re.DOTALL)


def test_tolerant_extractor_preserves_complete_blocks() -> None:
    text = "before\n```python\nprint('ok')\n```\nafter"
    assert extract_code_blocks_tolerant(text, "python", _strict_fence_extractor) == [
        "print('ok')\n"
    ]


def test_tolerant_extractor_recovers_truncated_final_block() -> None:
    text = "analysis\n```python\nfrom build123d import *\n# response limit"
    assert extract_code_blocks_tolerant(text, "python", _strict_fence_extractor) == [
        "from build123d import *\n# response limit"
    ]


def test_tolerant_extractor_prefers_valid_truncated_final_block() -> None:
    text = "```python\nprint('old')\n```\nrevision\n```python\nprint('new')"
    assert extract_code_blocks_tolerant(text, "python", _strict_fence_extractor) == [
        "print('new')"
    ]


def test_tolerant_extractor_rejects_invalid_truncated_python() -> None:
    text = "analysis\n```python\nvalue = ("
    assert extract_code_blocks_tolerant(text, "python", _strict_fence_extractor) == []


def test_done_signal_requires_review_after_candidate_changing_code() -> None:
    official_cli._validation_state.passed = True
    assert official_cli._has_done_signal_after_review("The candidate is valid. [DONE]") is True
    assert (
        official_cli._has_done_signal_after_review(
            "```python\nprint('write output.step')\n```\n[DONE]"
        )
        is False
    )
    assert official_cli._has_done_signal_after_review("```python\nprint('[DONE]')\n```") is False

    official_cli._validation_state.passed = False
    assert official_cli._has_done_signal_after_review("[DONE]") is False


def test_validation_feedback_requires_valid_watertight_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = """### Auto-validation of output.step
Valid:      True
Watertight: True
Solids:     1
"""
    assert official_cli._validation_feedback_passed(valid, b"png") is True
    assert official_cli._validation_feedback_passed(valid, None) is False
    assert (
        official_cli._validation_feedback_passed(
            valid + "Validation error: face missing triangulation", b"png"
        )
        is False
    )
    assert (
        official_cli._validation_feedback_passed(
            valid.replace("Watertight: True", "Watertight: False"), b"png"
        )
        is False
    )

    monkeypatch.setattr(
        official_cli,
        "_strict_auto_validate_and_render",
        lambda *_args, **_kwargs: (valid, b"png"),
    )
    official_cli._auto_validate_and_render_strict(
        tmp_path,
        SimpleNamespace(
            success=False,
            files_produced={"output.step": 1},
            code="failed",
            stdout="",
        ),
    )
    assert official_cli._validation_state.passed is False
    (tmp_path / "output.step").write_bytes(b"step")
    official_cli._auto_validate_and_render_strict(
        tmp_path,
        SimpleNamespace(
            success=True,
            files_produced={"output.step": 4},
            code="success",
            stdout="",
        ),
    )
    assert official_cli._validation_state.passed is True


def test_edit_inspection_budget_warns_then_stops_before_another_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        official_cli,
        "_strict_auto_validate_and_render",
        lambda *_args, **_kwargs: ("no candidate", None),
    )
    monkeypatch.setattr(official_cli._validation_state, "is_editing", True, raising=False)
    monkeypatch.setattr(
        official_cli._validation_state,
        "inspection_only_edit_turn_count",
        0,
        raising=False,
    )
    execution = SimpleNamespace(success=True, files_produced={}, code="inspect()")

    official_cli._auto_validate_and_render_strict(tmp_path, execution)
    feedback, _ = official_cli._auto_validate_and_render_strict(tmp_path, execution)

    assert "inspection budget is exhausted" in feedback
    assert official_cli._validation_state.inspection_only_edit_turn_count == 2

    monkeypatch.setenv("CADCOPILOT_ATTEMPT_TOKEN_CAP", "100000")
    monkeypatch.setattr(official_cli._validation_state, "ever_passed", False, raising=False)
    monkeypatch.setattr(
        official_cli._validation_state,
        "inspection_only_edit_turn_count",
        official_cli._EDIT_INSPECTION_HARD_LIMIT,
        raising=False,
    )
    client = SimpleNamespace(count_tokens=lambda _messages: 100)
    with pytest.raises(RuntimeError, match="inspection budget exhausted"):
        official_cli._complete_with_cap(
            client, [], max_tokens=16_000, reasoning_effort="medium"
        )


def test_explicit_each_target_count_and_execution_evidence() -> None:
    task = (
        "For each of the four non-circular pockets, bring their walls inward "
        "by 6mm."
    )
    assert official_cli._explicit_each_target_count(task) == 4
    assert official_cli._explicit_each_target_count("Shorten the smaller bore.") is None
    instance_evidence = """CADRIG_EDIT_INSTANCE index=1 center=(1, 2, 3)
CADRIG_EDIT_INSTANCE index=2 center=(4,5,6)
CADRIG_EDIT_INSTANCE index=3 center=(7,8,9)
CADRIG_EDIT_INSTANCE index=4 center=(10,11,12)
"""
    assert (
        official_cli._editing_count_evidence_passed(
            SimpleNamespace(
                stdout=(
                    instance_evidence
                    + "CADRIG_EDIT_COUNTS expected=4 matched=4 modified=4\n"
                )
            ),
            4,
        )
        is True
    )
    assert (
        official_cli._editing_count_evidence_passed(
            SimpleNamespace(
                stdout="CADRIG_EDIT_COUNTS expected=4 matched=3 modified=3\n"
            ),
            4,
        )
        is False
    )


def test_kernel_edit_signature_rejects_reexport_and_accepts_real_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Point:
        X = 10.0
        Y = 20.0
        Z = 30.0

    class Shape:
        volume = 1_000.0
        area = 600.0

        def __init__(self, face_areas: tuple[float, ...]) -> None:
            self.face_areas = face_areas

        def bounding_box(self) -> SimpleNamespace:
            return SimpleNamespace(size=Point())

        def center(self) -> Point:
            return Point()

        def faces(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(area=value) for value in self.face_areas]

        def edges(self) -> list[int]:
            return [1, 2, 3, 4]

        def solids(self) -> list[int]:
            return [1]

    (tmp_path / "input.step").write_bytes(b"source")
    (tmp_path / "output.step").write_bytes(b"candidate")
    imported = iter((Shape((100.0, 200.0)), Shape((100.0, 200.0))))
    monkeypatch.setattr("build123d.import_step", lambda _path: next(imported))

    changed, reason, status = official_cli._editing_candidate_changed(
        tmp_path, {"output.step"}
    )
    assert changed is False
    assert "equivalent" in reason
    assert status == "no_op"

    imported = iter((Shape((100.0, 200.0)), Shape((100.0, 201.0))))
    monkeypatch.setattr("build123d.import_step", lambda _path: next(imported))
    changed, reason, status = official_cli._editing_candidate_changed(
        tmp_path, {"output.step"}
    )
    assert changed is True
    assert reason == "face-area distribution changed"
    assert status == "changed"

    class ExpandedShape(Shape):
        def bounding_box(self) -> SimpleNamespace:
            return SimpleNamespace(size=SimpleNamespace(X=12.0, Y=20.0, Z=30.0))

    imported = iter((Shape((100.0, 200.0)), ExpandedShape((100.0, 201.0))))
    monkeypatch.setattr("build123d.import_step", lambda _path: next(imported))
    changed, reason, status = official_cli._editing_candidate_changed(
        tmp_path, {"output.step"}, preserve_extents=True
    )
    assert changed is False
    assert "changed outer extents" in reason
    assert status == "topology_violation"


def test_kernel_edit_signature_rejects_disconnected_body_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Shape:
        volume = 100.0
        area = 60.0

        def __init__(self, solid_count: int) -> None:
            self.solid_count = solid_count

        def bounding_box(self) -> SimpleNamespace:
            return SimpleNamespace(size=SimpleNamespace(X=1.0, Y=2.0, Z=3.0))

        def center(self) -> SimpleNamespace:
            return SimpleNamespace(X=0.0, Y=0.0, Z=0.0)

        def faces(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(area=60.0)]

        def edges(self) -> list[int]:
            return [1]

        def solids(self) -> list[int]:
            return list(range(self.solid_count))

    (tmp_path / "input.step").write_bytes(b"source")
    (tmp_path / "output.step").write_bytes(b"candidate")
    imported = iter((Shape(1), Shape(5)))
    monkeypatch.setattr("build123d.import_step", lambda _path: next(imported))

    changed, reason, status = official_cli._editing_candidate_changed(
        tmp_path, {"output.step"}
    )

    assert changed is False
    assert reason == "solid-body count changed from 1 to 5"
    assert status == "topology_violation"
    assert official_cli._instruction_allows_solid_count_change(
        "Split the part into two separate bodies."
    )
    assert not official_cli._instruction_allows_solid_count_change(
        "Reduce the number of impeller blades from 7 to 5."
    )


def test_explicit_blend_radius_transition_requires_old_to_new_face_change() -> None:
    transition = official_cli._explicit_blend_radius_transition(
        "Resize the blend on the peninsula from 18mm to 14mm."
    )
    assert transition == (18.0, 14.0)
    before = {"analytic_blend_radii": (10.0, 14.0, 18.0, 18.0)}
    proxy_ring = {"analytic_blend_radii": (10.0, 14.0, 14.0, 18.0, 18.0, 18.0)}
    correct = {"analytic_blend_radii": (10.0, 14.0, 14.0, 14.0)}

    passed, reason = official_cli._blend_radius_transition_passed(
        before, proxy_ring, transition
    )
    assert passed is False
    assert "18 mm: 2->3" in reason
    assert official_cli._blend_radius_transition_passed(
        before, correct, transition
    )[0] is True


def test_explicit_hole_spacing_requires_relocation_of_the_named_pair() -> None:
    transition = official_cli._explicit_hole_spacing_transition(
        "There are two holes (axes collinear with Z) aligned along the X axis. "
        "Increase their spacing from 20mm apart center-to-center to 30mm apart."
    )
    assert transition == (2, "Z", "X", 20.0, 30.0)
    old_left = (3.0, 0.0, 0.0, 1.0, 0.0, 5.0, 0.0)
    old_right = (3.0, 0.0, 0.0, 1.0, 20.0, 5.0, 0.0)
    new_left = (3.0, 0.0, 0.0, 1.0, -5.0, 5.0, 0.0)
    new_right = (3.0, 0.0, 0.0, 1.0, 25.0, 5.0, 0.0)
    unrelated = (7.0, 0.0, 1.0, 0.0, 4.0, 0.0, 8.0)
    before = {"analytic_cylinder_axes": (old_left, old_right, unrelated)}
    proxy_cut = {
        "analytic_cylinder_axes": (old_left, old_right, unrelated, new_right)
    }
    correct = {"analytic_cylinder_axes": (new_left, new_right, unrelated)}

    passed, reason = official_cli._hole_spacing_transition_passed(
        before, proxy_cut, transition
    )
    assert passed is False
    assert "removed=0 added=1" in reason
    assert official_cli._hole_spacing_transition_passed(
        before, correct, transition
    )[0] is True


def test_editing_no_op_is_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    feedback = "Valid:      True\nWatertight: True\n"
    monkeypatch.setattr(
        official_cli,
        "_strict_auto_validate_and_render",
        lambda *_args, **_kwargs: (feedback, b"png"),
    )
    monkeypatch.setattr(
        official_cli,
        "_editing_candidate_changed",
        lambda *_args, **_kwargs: (
            False,
            "input/output kernel signatures are equivalent",
            "no_op",
        ),
    )
    monkeypatch.setattr(official_cli._validation_state, "is_editing", True, raising=False)
    monkeypatch.setattr(
        official_cli._validation_state, "required_edit_instances", None, raising=False
    )
    monkeypatch.setattr(
        official_cli._validation_state, "no_op_rejection_count", 0, raising=False
    )
    (tmp_path / ".cadrig_last_accepted_output.step").write_bytes(b"accepted")
    (tmp_path / "output.step").write_bytes(b"unchanged")

    feedback_text, _ = official_cli._auto_validate_and_render_strict(
        tmp_path,
        SimpleNamespace(
            success=True,
            code="reexport_input()",
            stdout="",
            files_produced={"output.step": 9},
        ),
    )

    assert official_cli._validation_state.passed is False
    assert official_cli._validation_state.no_op_rejection_count == 1
    assert (tmp_path / "output.step").read_bytes() == b"accepted"
    assert "REJECTED as a no-op" in feedback_text


def test_explicit_edit_count_is_part_of_strict_candidate_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    feedback = "Valid:      True\nWatertight: True\n"
    monkeypatch.setattr(
        official_cli,
        "_strict_auto_validate_and_render",
        lambda *_args, **_kwargs: (feedback, b"png"),
    )
    monkeypatch.setattr(
        official_cli._validation_state, "required_edit_instances", 4, raising=False
    )
    monkeypatch.setattr(
        official_cli._validation_state,
        "accepted_candidate_code_hashes",
        set(),
        raising=False,
    )
    incomplete = SimpleNamespace(
        success=True,
        code="modify_three()",
        stdout="CADRIG_EDIT_COUNTS expected=4 matched=3 modified=3\n",
        files_produced={"output.step": 3},
    )
    (tmp_path / ".cadrig_last_accepted_output.step").write_bytes(b"accepted")
    (tmp_path / "output.step").write_bytes(b"bad")
    feedback_text, _ = official_cli._auto_validate_and_render_strict(
        tmp_path, incomplete
    )
    assert official_cli._validation_state.passed is False
    assert (tmp_path / "output.step").read_bytes() == b"accepted"
    assert "Semantic edit acceptance: REJECTED" in feedback_text

    complete = SimpleNamespace(
        success=True,
        code="modify_four()",
        stdout="""CADRIG_EDIT_INSTANCE index=1 center=(1,2,3)
CADRIG_EDIT_INSTANCE index=2 center=(4,5,6)
CADRIG_EDIT_INSTANCE index=3 center=(7,8,9)
CADRIG_EDIT_INSTANCE index=4 center=(10,11,12)
CADRIG_EDIT_COUNTS expected=4 matched=4 modified=4
""",
        files_produced={"output.step": 4},
    )
    (tmp_path / "output.step").write_bytes(b"good")
    feedback_text, _ = official_cli._auto_validate_and_render_strict(
        tmp_path, complete
    )
    assert official_cli._validation_state.passed is True
    assert (tmp_path / ".cadrig_last_accepted_output.step").read_bytes() == b"good"
    assert "Semantic edit acceptance: PASS" in feedback_text
    assert official_cli._sha256_text("modify_four()") in (
        official_cli._validation_state.accepted_candidate_code_hashes
    )


def test_terminal_mesh_edit_contract_rejects_unsafe_parameters() -> None:
    assert _validate_terminal_edit("x", "min", 10, 12_000) == (0, -1.0)
    assert _validate_terminal_edit("Z", "MAX", 2.5, 100) == (2, 1.0)
    assert _validate_terminal_edit("y", "both", 5, 500) == (1, 0.0)
    with pytest.raises(ValueError, match="axis"):
        _validate_terminal_edit("q", "min", 10, 12_000)
    with pytest.raises(ValueError, match="side"):
        _validate_terminal_edit("x", "near", 10, 12_000)
    with pytest.raises(ValueError, match="distance_mm"):
        _validate_terminal_edit("x", "min", 0, 12_000)
    with pytest.raises(ValueError, match="target_triangles"):
        _validate_terminal_edit("x", "min", 10, 99)


def test_radial_feature_removal_cuts_source_and_preserves_body_count(
    tmp_path: Path,
) -> None:
    from build123d import Align, Axis, Box, Cylinder, Location, export_step

    hub = Cylinder(10, 2, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fins = [
        Box(8, 2, 2.1, align=(Align.MIN, Align.MIN, Align.MIN))
        .moved(Location((6, -1, -2)))
        .rotate(Axis.Z, angle)
        for angle in (0, 120, 240)
    ]
    source = hub.fuse(*fins).clean()
    assert len(source.solids()) == 1
    input_step = tmp_path / "input.step"
    output_step = tmp_path / "output.step"
    export_step(source, input_step)

    result = brep_fallback.remove_isolated_radial_features_to_step(
        input_step,
        output_step,
        axis="z",
        split_position_mm=-0.01,
        side="min",
        expected_source_count=3,
        remove_indices=[1],
    )

    assert result["source_feature_count"] == 3
    assert result["remaining_feature_count"] == 2
    assert result["source_solid_count"] == result["output_solid_count"] == 1
    assert result["volume_removed_mm3"] > 0
    assert output_step.is_file()


def test_cylindrical_mesh_resize_moves_only_selected_ring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import numpy as np

    vertices = np.asarray(
        [
            (2, 0, -1),
            (0, 2, -1),
            (-2, 0, -1),
            (0, -2, -1),
            (2, 0, 1),
            (0, 2, 1),
            (-2, 0, 1),
            (0, -2, 1),
            (4, 0, 0),
        ],
        dtype=float,
    )
    source = tmp_path / "input.mesh.npz"
    np.savez(source, vertices=vertices, triangles=np.empty((0, 3), dtype=int))
    captured: dict[str, object] = {}

    def write_step(
        edited_vertices: object,
        triangles: object,
        destination: Path,
        target_triangles: int,
    ) -> tuple[int, int]:
        captured["vertices"] = np.asarray(edited_vertices).copy()
        return 0, 123

    monkeypatch.setattr(mesh_fallback, "_write_faceted_step", write_step)
    result = mesh_fallback.resize_cylindrical_mesh_region_to_step(
        source,
        tmp_path / "output.step",
        axis="z",
        center=(0, 0),
        current_radius_mm=2,
        radial_delta_mm=1,
        axis_min_mm=-1,
        axis_max_mm=1,
    )

    edited = np.asarray(captured["vertices"])
    assert np.allclose(np.linalg.norm(edited[:8, :2], axis=1), 3)
    assert np.allclose(edited[:8, 2], vertices[:8, 2])
    assert np.allclose(edited[8], vertices[8])
    assert result["moved_vertex_count"] == 8


def test_planar_annulus_translation_moves_only_selected_plane_region(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import numpy as np

    vertices = np.asarray(
        [
            (2, 0, 5),
            (0, 2, 5),
            (-2, 0, 5),
            (0, -2, 5),
            (4, 0, 5),
            (0, 4, 5),
            (-4, 0, 5),
            (0, -4, 5),
            (1, 0, 5),
            (3, 0, 1),
            (6, 0, 5),
        ],
        dtype=float,
    )
    source = tmp_path / "input.mesh.npz"
    np.savez(source, vertices=vertices, triangles=np.empty((0, 3), dtype=int))
    captured: dict[str, object] = {}

    def write_step(
        edited_vertices: object,
        triangles: object,
        destination: Path,
        target_triangles: int,
    ) -> tuple[int, int]:
        captured["vertices"] = np.asarray(edited_vertices).copy()
        return 0, 456

    monkeypatch.setattr(mesh_fallback, "_write_faceted_step", write_step)
    result = mesh_fallback.translate_planar_annulus_mesh_region_to_step(
        source,
        tmp_path / "output.step",
        axis="z",
        center=(0, 0),
        plane_position_mm=5,
        inner_radius_mm=2,
        outer_radius_mm=4,
        distance_mm=10,
    )

    edited = np.asarray(captured["vertices"])
    assert np.allclose(edited[:8, 2], 15)
    assert np.allclose(edited[8:], vertices[8:])
    assert result["moved_vertex_count"] == 8


def test_planar_patch_translation_moves_only_seeded_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import numpy as np

    vertices = np.asarray(
        [
            (0, 0, 5),
            (1, 0, 5),
            (1, 1, 5),
            (0, 1, 5),
            (10, 10, 5),
            (11, 10, 5),
            (10, 11, 5),
            (0, 0, 0),
        ],
        dtype=float,
    )
    triangles = np.asarray([(0, 1, 2), (0, 2, 3), (4, 5, 6), (0, 1, 7)])
    source = tmp_path / "input.mesh.npz"
    np.savez(source, vertices=vertices, triangles=triangles)
    captured: dict[str, object] = {}

    def write_step(
        edited_vertices: object,
        edited_triangles: object,
        destination: Path,
        target_triangles: int,
    ) -> tuple[int, int]:
        captured["vertices"] = np.asarray(edited_vertices).copy()
        return 4, 789

    monkeypatch.setattr(mesh_fallback, "_write_faceted_step", write_step)
    result = mesh_fallback.translate_planar_mesh_patch_to_step(
        source,
        tmp_path / "output.step",
        axis="z",
        plane_position_mm=5,
        seed=(0.5, 0.5),
        distance_mm=10,
    )

    edited = np.asarray(captured["vertices"])
    assert np.allclose(edited[:4, 2], 15)
    assert np.allclose(edited[4:], vertices[4:])
    assert result["component_triangle_count"] == 2
    assert result["moved_vertex_count"] == 4


def test_oriented_mesh_regions_are_inspectable_and_seed_translatable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import numpy as np

    vertices = np.asarray(
        [
            (1, 0, 0),
            (1, 1, 0),
            (1, 1, 1),
            (1, 0, 1),
            (10, 5, 0),
            (10, 6, 0),
            (10, 6, 1),
            (10, 5, 1),
        ],
        dtype=float,
    )
    triangles = np.asarray([(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)])
    source = tmp_path / "input.mesh.npz"
    np.savez(source, vertices=vertices, triangles=triangles)

    regions = mesh_fallback.inspect_oriented_mesh_regions(
        source,
        normal_axis="x",
        center_axis="x",
        center_min_mm=0,
        min_area_mm2=0.5,
        normal_sign="positive",
        bbox_long_axis="y",
    )
    assert [region["center"] for region in regions] == [
        [1.0, 0.5, 0.5],
        [10.0, 5.5, 0.5],
    ]
    assert all(region["triangle_count"] == 2 for region in regions)

    captured: dict[str, object] = {}

    def write_step(
        edited_vertices: object,
        edited_triangles: object,
        destination: Path,
        target_triangles: int,
    ) -> tuple[int, int]:
        captured["vertices"] = np.asarray(edited_vertices).copy()
        return 4, 321

    monkeypatch.setattr(mesh_fallback, "_write_faceted_step", write_step)
    result = mesh_fallback.translate_oriented_mesh_regions_to_step(
        source,
        tmp_path / "output.step",
        normal_axis="x",
        seeds=[(1, 0.5, 0.5), (10, 5.5, 0.5)],
        distance_mm=[2, -2],
        seed_tolerance_mm=0.1,
    )

    edited = np.asarray(captured["vertices"])
    assert np.allclose(edited[:4, 0], 3)
    assert np.allclose(edited[4:, 0], 8)
    assert result["moved_vertex_count"] == 8
    assert len(result["selected_regions"]) == 2


def test_agent_wrapper_copies_mesh_sidecar_and_adds_generic_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "inputs" / "input.step"
    source.parent.mkdir()
    source.write_text("step", encoding="utf-8")
    sidecar = source.with_name("input.mesh.npz")
    sidecar.write_bytes(b"mesh")
    captured: dict[str, object] = {}

    def run_agent(description: str, *args: object, **kwargs: object) -> object:
        captured.update(
            description=description,
            args=args,
            kwargs=kwargs,
            validation_passed=official_cli._validation_state.passed,
        )
        return object()

    monkeypatch.setattr(official_cli, "_strict_run_agent", run_agent)
    work_dir = tmp_path / "work"

    official_cli._run_agent_with_mesh_sidecars(
        "Lengthen the terminal boss by 10 mm.",
        input_files=[source],
        work_dir=work_dir,
    )

    assert (work_dir / "input.mesh.npz").read_bytes() == b"mesh"
    assert "Kernel fallback available" in str(captured["description"])
    assert "Imported STEP compatibility notes" in str(captured["description"])
    assert "GeomType.CYLINDER" in str(captured["description"])
    assert "Never write a dummy" in str(captured["description"])
    assert "executable Python block before any explanation" in str(
        captured["description"]
    )
    assert "Treat the requested edit as local" in str(captured["description"])
    assert "rejected as a no-op" in str(captured["description"])
    assert "source outer extents" in str(captured["description"])
    assert "old-radius face" in str(captured["description"])
    assert "old analytic cylinder axes" in str(captured["description"])
    assert "at most two distinct execution turns" in str(captured["description"])
    assert "translate_planar_annulus_mesh_region_to_step" in str(
        captured["description"]
    )
    assert "translate_planar_mesh_patch_to_step" in str(captured["description"])
    assert "inspect_oriented_mesh_regions" in str(captured["description"])
    assert "translate_oriented_mesh_regions_to_step" in str(
        captured["description"]
    )
    assert "remove_isolated_radial_features_to_step" in str(captured["description"])
    assert "fixture" not in str(captured["description"]).lower()
    assert captured["validation_passed"] is False


def test_generation_wrapper_requires_semantic_completeness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawing = tmp_path / "input.png"
    drawing.write_bytes(b"png")
    captured: dict[str, object] = {}

    def run_agent(description: str, *args: object, **kwargs: object) -> object:
        captured["description"] = description
        return object()

    monkeypatch.setattr(official_cli, "_strict_run_agent", run_agent)
    official_cli._run_agent_with_mesh_sidecars(
        "Reproduce the geometry from the drawing.",
        input_files=[drawing],
        work_dir=tmp_path / "work",
    )

    description = str(captured["description"])
    assert "A bounding box, single primitive" in description
    assert "Implement every clearly discernible major feature" in description
    assert "Treat the requested edit as local" not in description


def test_editing_wrapper_enforces_explicit_target_cardinality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input.step"
    source.write_bytes(b"step")
    captured: dict[str, str] = {}

    def run_agent(description: str, *args: object, **kwargs: object) -> object:
        captured["description"] = description
        return object()

    monkeypatch.setattr(official_cli, "_strict_run_agent", run_agent)
    official_cli._run_agent_with_mesh_sidecars(
        "For each of the four pockets, bring their walls inward by 6mm.",
        input_files=[source],
        work_dir=tmp_path / "work",
    )

    description = captured["description"]
    assert "explicitly targets 4 distinct feature instances" in description
    assert "CADRIG_EDIT_INSTANCE index=<1..4>" in description
    assert "CADRIG_EDIT_COUNTS expected=4" in description
    assert "Inspection code must not write or re-export" in description


def test_failed_candidate_is_quarantined_and_cannot_replace_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = SimpleNamespace(
        turn=0,
        code_executions=[
            SimpleNamespace(
                success=True,
                files_produced={"output.step": 4},
                code="good",
                duration_s=1.0,
                stdout="",
                stderr="",
            )
        ],
        prompt_tokens=1,
        completion_tokens=1,
        reasoning_tokens=None,
        duration_s=1.0,
        assistant_message="good",
    )
    failed = SimpleNamespace(
        turn=1,
        code_executions=[
            SimpleNamespace(
                success=False,
                files_produced={"output.step": 5},
                code="dummy",
                duration_s=1.0,
                stdout="",
                stderr="failed",
            )
        ],
        prompt_tokens=1,
        completion_tokens=1,
        reasoning_tokens=None,
        duration_s=1.0,
        assistant_message="dummy",
    )
    result = SimpleNamespace(
        turns=[good, failed],
        total_tokens=4,
        total_duration_s=2.0,
        completed=False,
        stopped_reason="max_iterations",
    )

    def save(_result: object, output_dir: str | Path) -> Path:
        destination = Path(output_dir)
        (destination / "turn_0").mkdir(parents=True)
        (destination / "turn_1").mkdir()
        (destination / "turn_0" / "output.step").write_bytes(b"good")
        (destination / "turn_1" / "output.step").write_bytes(b"dummy")
        (destination / "output.step").write_bytes(b"dummy")
        return destination

    monkeypatch.setattr(official_cli, "_strict_agent_result_save", save)
    destination = official_cli._save_with_trace(result, tmp_path / "saved")

    assert (destination / "output.step").read_bytes() == b"good"
    assert not (destination / "turn_1" / "output.step").exists()
    assert (destination / "turn_1" / "rejected_failed_output.step").read_bytes() == b"dummy"


def test_failed_only_candidate_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = SimpleNamespace(
        turn=0,
        code_executions=[
            SimpleNamespace(
                success=False,
                files_produced={"output.step": 5},
                code="dummy",
                duration_s=1.0,
                stdout="",
                stderr="failed",
            )
        ],
        prompt_tokens=1,
        completion_tokens=1,
        reasoning_tokens=None,
        duration_s=1.0,
        assistant_message="dummy",
    )
    result = SimpleNamespace(
        turns=[failed],
        total_tokens=2,
        total_duration_s=1.0,
        completed=False,
        stopped_reason="max_iterations",
    )

    def save(_result: object, output_dir: str | Path) -> Path:
        destination = Path(output_dir)
        (destination / "turn_0").mkdir(parents=True)
        (destination / "turn_0" / "output.step").write_bytes(b"dummy")
        (destination / "output.step").write_bytes(b"dummy")
        return destination

    monkeypatch.setattr(official_cli, "_strict_agent_result_save", save)
    destination = official_cli._save_with_trace(result, tmp_path / "saved")

    assert not (destination / "output.step").exists()
    assert (destination / "turn_0" / "rejected_failed_output.step").is_file()


def test_incremental_trace_refreshes_live_semantic_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = SimpleNamespace(
        turns=[],
        total_tokens=0,
        total_duration_s=0.0,
        completed=False,
        stopped_reason="max_iterations",
        _cadrig_semantic_invariance_rejection_count=0,
    )

    def save(_result: object, output_dir: str | Path) -> Path:
        destination = Path(output_dir)
        destination.mkdir(parents=True)
        return destination

    monkeypatch.setattr(official_cli, "_strict_agent_result_save", save)
    monkeypatch.setattr(official_cli._validation_state, "run_active", True, raising=False)
    monkeypatch.setattr(
        official_cli._validation_state,
        "semantic_invariance_rejection_count",
        3,
        raising=False,
    )
    monkeypatch.setattr(
        official_cli._validation_state,
        "accepted_candidate_code_hashes",
        {"accepted"},
        raising=False,
    )

    destination = official_cli._save_with_trace(result, tmp_path / "saved")

    trace = json.loads((destination / "trace.json").read_text(encoding="utf-8"))
    assert trace["semantic_invariance_rejections"] == 3
    assert result._cadrig_accepted_candidate_code_hashes == frozenset({"accepted"})


def test_semantically_unaccepted_edit_candidate_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution = SimpleNamespace(
        success=True,
        files_produced={"output.step": 4},
        code="modify_three()",
        duration_s=1.0,
        stdout="CADRIG_EDIT_COUNTS expected=4 matched=3 modified=3\n",
        stderr="",
    )
    turn = SimpleNamespace(
        turn=0,
        code_executions=[execution],
        prompt_tokens=1,
        completion_tokens=1,
        reasoning_tokens=None,
        duration_s=1.0,
        assistant_message="incomplete edit",
    )
    result = SimpleNamespace(
        turns=[turn],
        total_tokens=2,
        total_duration_s=1.0,
        completed=False,
        stopped_reason="max_iterations",
        _cadrig_enforce_accepted_candidates=True,
        _cadrig_accepted_candidate_code_hashes=frozenset(),
        _cadrig_required_edit_instances=4,
    )

    def save(_result: object, output_dir: str | Path) -> Path:
        destination = Path(output_dir)
        (destination / "turn_0").mkdir(parents=True)
        (destination / "turn_0" / "output.step").write_bytes(b"incomplete")
        (destination / "output.step").write_bytes(b"incomplete")
        return destination

    monkeypatch.setattr(official_cli, "_strict_agent_result_save", save)
    destination = official_cli._save_with_trace(result, tmp_path / "saved")

    assert not (destination / "output.step").exists()
    assert (
        destination / "turn_0" / "rejected_unvalidated_output.step"
    ).read_bytes() == b"incomplete"
    trace = json.loads((destination / "trace.json").read_text(encoding="utf-8"))
    assert trace["semantic_edit_contract"] == {
        "required_instances": 4,
        "accepted_candidate_count": 0,
    }


def test_last_candidate_producer_in_a_turn_controls_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executions = [
        SimpleNamespace(
            success=True,
            files_produced={"output.step": 4},
            code="good",
            duration_s=1.0,
            stdout="",
            stderr="",
        ),
        SimpleNamespace(
            success=False,
            files_produced={"output.step": 5},
            code="bad",
            duration_s=1.0,
            stdout="",
            stderr="failed",
        ),
    ]
    turn = SimpleNamespace(
        turn=0,
        code_executions=executions,
        prompt_tokens=1,
        completion_tokens=1,
        reasoning_tokens=None,
        duration_s=2.0,
        assistant_message="two producers",
    )
    result = SimpleNamespace(
        turns=[turn],
        total_tokens=2,
        total_duration_s=2.0,
        completed=False,
        stopped_reason="max_iterations",
    )

    def save(_result: object, output_dir: str | Path) -> Path:
        destination = Path(output_dir)
        (destination / "turn_0").mkdir(parents=True)
        (destination / "turn_0" / "output.step").write_bytes(b"bad")
        (destination / "output.step").write_bytes(b"bad")
        return destination

    monkeypatch.setattr(official_cli, "_strict_agent_result_save", save)
    destination = official_cli._save_with_trace(result, tmp_path / "saved")

    assert not (destination / "output.step").exists()
    assert (destination / "turn_0" / "rejected_failed_output.step").read_bytes() == b"bad"


def test_completion_token_allowance_reserves_prompt_before_call() -> None:
    assert completion_token_allowance(
        token_cap=10_000,
        consumed=4_000,
        prompt_tokens=1_500,
        requested=8_000,
    ) == 4_500
    assert completion_token_allowance(
        token_cap=5_000,
        consumed=4_000,
        prompt_tokens=1_000,
        requested=2_000,
    ) == 0


def test_provider_usage_ledger_persists_rejected_over_cap_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "provider_usage.json"
    monkeypatch.setenv("CADCOPILOT_PROVIDER_USAGE_PATH", str(ledger))
    monkeypatch.setenv("CADCOPILOT_ATTEMPT_TOKEN_CAP", "10000")
    monkeypatch.setattr(official_cli._validation_state, "ever_passed", False, raising=False)
    client = SimpleNamespace(
        _cadcopilot_consumed_tokens=4_000,
        count_tokens=lambda _messages: 500,
    )

    def complete(_client: object, _messages: object, **kwargs: object) -> object:
        assert kwargs["max_tokens"] == 1_404
        return SimpleNamespace(prompt_tokens=500, completion_tokens=5_601, total_tokens=6_101)

    monkeypatch.setattr(official_cli, "_strict_llm_complete", complete)

    with pytest.raises(RuntimeError, match="10101 > 10000"):
        official_cli._complete_with_cap(client, [], max_tokens=2_000)

    payload = json.loads(ledger.read_text(encoding="utf-8"))
    assert payload["total_tokens"] == 6_101
    assert payload["prompt_tokens"] == 500
    assert payload["completion_tokens"] == 5_601
    assert payload["call_count"] == 1
    assert payload["rejected_call_count"] == 1
    assert payload["calls"] == [
        {
            "accepted_by_attempt_cap": False,
            "completion_tokens": 5_601,
            "prompt_tokens": 500,
            "total_tokens": 6_101,
            "unclassified_tokens": 0,
        }
    ]


def test_no_code_provider_response_downgrades_later_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CADCOPILOT_ATTEMPT_TOKEN_CAP", "100000")
    monkeypatch.setattr(
        official_cli._validation_state,
        "no_code_provider_response_count",
        0,
        raising=False,
    )
    monkeypatch.setattr(
        official_cli._validation_state,
        "response_effort_downgrade_count",
        0,
        raising=False,
    )
    client = SimpleNamespace(
        _cadcopilot_consumed_tokens=0,
        count_tokens=lambda _messages: 100,
    )
    efforts: list[str] = []
    responses = iter(
        (
            SimpleNamespace(
                content="analysis without executable code",
                prompt_tokens=100,
                completion_tokens=900,
                total_tokens=1_000,
            ),
            SimpleNamespace(
                content="```python\nprint('execute')\n```",
                prompt_tokens=100,
                completion_tokens=200,
                total_tokens=300,
            ),
        )
    )

    def complete(_client: object, _messages: object, **kwargs: object) -> object:
        efforts.append(str(kwargs["reasoning_effort"]))
        return next(responses)

    monkeypatch.setattr(official_cli, "_strict_llm_complete", complete)
    official_cli._complete_with_cap(
        client, [], max_tokens=16_000, reasoning_effort="medium"
    )
    official_cli._complete_with_cap(
        client, [], max_tokens=16_000, reasoning_effort="medium"
    )

    assert efforts == ["medium", "low"]
    assert official_cli._validation_state.no_code_provider_response_count == 1
    assert official_cli._validation_state.response_effort_downgrade_count == 1


def test_token_cap_stops_gracefully_after_a_strict_valid_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CADCOPILOT_ATTEMPT_TOKEN_CAP", "100")
    monkeypatch.setattr(official_cli._validation_state, "ever_passed", True, raising=False)
    monkeypatch.setattr(official_cli._validation_state, "budget_stop", False, raising=False)
    client = SimpleNamespace(
        model="test/model",
        _cadcopilot_consumed_tokens=95,
        count_tokens=lambda _messages: 10,
    )

    completion = official_cli._complete_with_cap(client, [], max_tokens=20)

    assert completion.content.startswith("[DONE]")
    assert completion.total_tokens == 0
    assert official_cli._validation_state.budget_stop is True
    assert official_cli._has_done_signal_after_review(completion.content) is True


def test_prompt_margin_stops_before_an_estimation_boundary_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CADCOPILOT_ATTEMPT_TOKEN_CAP", "10_000")
    monkeypatch.setattr(official_cli._validation_state, "ever_passed", True, raising=False)
    monkeypatch.setattr(official_cli._validation_state, "budget_stop", False, raising=False)
    client = SimpleNamespace(
        model="test/model",
        _cadcopilot_consumed_tokens=4_000,
        count_tokens=lambda _messages: 3_000,
    )

    def unexpected_call(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("provider must not be called inside the safety margin")

    monkeypatch.setattr(official_cli, "_strict_llm_complete", unexpected_call)
    completion = official_cli._complete_with_cap(client, [], max_tokens=3_000)

    assert completion.total_tokens == 0
    assert official_cli._validation_state.budget_stop is True


def test_prompt_margin_calibrates_from_provider_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CADCOPILOT_ATTEMPT_TOKEN_CAP", "25_000")
    monkeypatch.setattr(
        official_cli._validation_state,
        "inspection_only_edit_turn_count",
        0,
        raising=False,
    )
    monkeypatch.setattr(
        official_cli._validation_state,
        "prompt_margin_calibration_count",
        0,
        raising=False,
    )
    client = SimpleNamespace(
        _cadcopilot_consumed_tokens=0,
        count_tokens=lambda _messages: 1_000,
    )
    max_tokens_seen: list[int] = []
    responses = iter(
        (
            SimpleNamespace(
                content="```python\nprint('first')\n```",
                prompt_tokens=10_000,
                completion_tokens=100,
                total_tokens=10_100,
            ),
            SimpleNamespace(
                content="```python\nprint('second')\n```",
                prompt_tokens=1_500,
                completion_tokens=500,
                total_tokens=2_000,
            ),
        )
    )

    def complete(_client: object, _messages: object, **kwargs: object) -> object:
        max_tokens_seen.append(int(kwargs["max_tokens"]))
        return next(responses)

    monkeypatch.setattr(official_cli, "_strict_llm_complete", complete)
    official_cli._complete_with_cap(client, [], max_tokens=16_000)
    official_cli._complete_with_cap(client, [], max_tokens=16_000)

    assert max_tokens_seen == [16_000, 2_852]
    assert client._cadrig_adaptive_prompt_margin_tokens == 11_048
    assert official_cli._validation_state.prompt_margin_calibration_count == 1


def test_rejected_provider_overrun_preserves_prior_valid_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "provider_usage.json"
    monkeypatch.setenv("CADCOPILOT_PROVIDER_USAGE_PATH", str(ledger))
    monkeypatch.setenv("CADCOPILOT_ATTEMPT_TOKEN_CAP", "10_000")
    monkeypatch.setattr(official_cli._validation_state, "ever_passed", True, raising=False)
    monkeypatch.setattr(official_cli._validation_state, "budget_stop", False, raising=False)
    client = SimpleNamespace(
        model="test/model",
        _cadcopilot_consumed_tokens=1_000,
        count_tokens=lambda _messages: 1_000,
    )

    def complete(_client: object, _messages: object, **kwargs: object) -> object:
        assert kwargs["max_tokens"] == 3_904
        return SimpleNamespace(
            prompt_tokens=5_000,
            completion_tokens=4_001,
            total_tokens=9_001,
        )

    monkeypatch.setattr(official_cli, "_strict_llm_complete", complete)
    completion = official_cli._complete_with_cap(client, [], max_tokens=8_000)

    assert completion.total_tokens == 0
    assert official_cli._validation_state.budget_stop is True
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    assert payload["total_tokens"] == 9_001
    assert payload["rejected_call_count"] == 1


def test_token_cap_still_fails_closed_without_a_valid_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CADCOPILOT_ATTEMPT_TOKEN_CAP", "100")
    monkeypatch.setattr(official_cli._validation_state, "ever_passed", False, raising=False)
    monkeypatch.setattr(official_cli._validation_state, "budget_stop", False, raising=False)
    client = SimpleNamespace(
        model="test/model",
        _cadcopilot_consumed_tokens=95,
        count_tokens=lambda _messages: 10,
    )

    with pytest.raises(RuntimeError, match="token cap exhausted"):
        official_cli._complete_with_cap(client, [], max_tokens=20)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("max_iterations", 0),
        ("max_tokens", -1),
        ("max_tokens_per_call", 0),
        ("max_duration", -0.1),
    ),
)
def test_run_config_rejects_nonpositive_limits(
    tmp_path: Path, field: str, value: float
) -> None:
    kwargs = {field: value}
    with pytest.raises(ValueError, match=field):
        CadgenbenchRunConfig(output_root=tmp_path, fixtures=("101",), **kwargs)


def test_find_sanity_script_discovers_huggingface_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "hub"
    script = (
        cache
        / "datasets--HuggingAI4Engineering--cadgenbench-data"
        / "snapshots"
        / "revision"
        / "sanity_check_submission.py"
    )
    script.parent.mkdir(parents=True)
    script.write_text("# checker", encoding="utf-8")
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    assert find_sanity_script(None) == script


def _write_run(run_dir: Path, *, all_tasks: bool = True, missing: bool = False) -> None:
    run_dir.mkdir()
    (run_dir / "params.json").write_text(
        json.dumps({"fixtures": ["101", "201"]}), encoding="utf-8"
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"request": {"all": all_tasks, "fixtures": []}}), encoding="utf-8"
    )
    for task_id in ("101", "201"):
        task_dir = run_dir / task_id
        task_dir.mkdir()
        if not missing or task_id != "201":
            (task_dir / "output.step").write_bytes(f"STEP {task_id}".encode())


def test_build_baseline_command_contains_reproducibility_controls(tmp_path: Path) -> None:
    config = CadgenbenchRunConfig(
        output_root=tmp_path,
        run_all=True,
        model="openai/example",
        parallel=3,
        max_iterations=12,
        max_tokens=5000,
        max_tokens_per_call=32000,
        max_duration=90,
        reasoning_effort="high",
    )
    command = build_baseline_command(config, command_prefix=("cgb",))
    assert command[:4] == ["cgb", "baseline", "run", "--all"]
    assert command[command.index("--model") + 1] == "openai/example"
    assert command[command.index("--parallel") + 1] == "3"
    assert command[command.index("--max-iter") + 1] == "12"
    assert command[command.index("--max-tokens-per-call") + 1] == "32000"


def test_verify_run_checks_every_candidate_with_sanity_command(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir)
    checker = (
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; sys.exit(0 if Path(sys.argv[1]).stat().st_size else 1)",
    )
    report = verify_run(run_dir, sanity_command=checker, require_sanity=True)
    assert report.passed
    assert report.scope == "all"
    assert report.expected_count == 2
    assert report.valid_count == 2
    assert json.loads((run_dir / "verification.json").read_text())["passed"] is True


def test_verify_run_reports_missing_candidate(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir, missing=True)
    report = verify_run(run_dir)
    assert not report.passed
    assert report.missing_count == 1
    assert report.tasks[1].status == "missing"


def test_package_run_writes_only_submission_contract_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir)
    checker = tmp_path / "sanity.py"
    checker.write_text("raise SystemExit(0)", encoding="utf-8")
    result = package_run(
        run_dir,
        submitter="Engineer",
        submission_name="CADRIG Alpha",
        agree_to_publish=True,
        sanity_script=checker,
    )
    with zipfile.ZipFile(result.output) as archive:
        assert set(archive.namelist()) == {
            "meta.json",
            "101/",
            "101/output.step",
            "201/",
            "201/output.step",
        }
        meta = json.loads(archive.read("meta.json"))
    assert meta["submitter_name"] == "Engineer"
    assert meta["agree_to_publish"] is True


def test_package_run_refuses_partial_scope_by_default(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_run(run_dir, all_tasks=False)
    with pytest.raises(ValueError, match="incomplete CADGenBench run"):
        package_run(run_dir)


def test_official_baseline_run_records_manifest(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_cadgenbench.py"
    fake_cli.write_text(
        """
import json
import sys
from pathlib import Path

args = sys.argv[1:]
output_root = Path(args[args.index('--output-dir') + 1])
run_dir = output_root / 'fake-run'
run_dir.mkdir(parents=True)
(run_dir / 'params.json').write_text(json.dumps({'fixtures': ['101', '201']}))
for task_id in ('101', '201'):
    task_dir = run_dir / task_id
    task_dir.mkdir()
    (task_dir / 'output.step').write_text('STEP')
""".strip(),
        encoding="utf-8",
    )
    config = CadgenbenchRunConfig(output_root=tmp_path / "results", run_all=True)
    run_dir = run_official_baseline(
        config,
        command_prefix=(sys.executable, str(fake_cli)),
        environ={},
        cwd=tmp_path,
    )
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["request"]["all"] is True
    assert manifest["official_params"]["fixtures"] == ["101", "201"]
    assert manifest["environment"]["python"]
    assert manifest["completion"]["complete"] is True
    assert manifest["completion"]["trace_count"] == 0
    assert manifest["command"][0] == sys.executable
    assert (run_dir / "harness.log").is_file()
    invocation = json.loads((run_dir / "harness_invocation.json").read_text())
    assert invocation["status"] == "completed"
