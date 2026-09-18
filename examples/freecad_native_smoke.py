"""Real FreeCAD smoke test; run with FreeCADCmd, not regular Python."""

import json
import tempfile
from pathlib import Path

from cadrig.adapters.freecad import FreeCADKernelAdapter
from cadrig.contracts import Action, ActionKind, ActionPlan, ExecutionStatus

adapter = FreeCADKernelAdapter()
plan = ActionPlan(
    plan_id="freecad-native-smoke",
    document_id="CADRIGSmoke",
    base_revision=0,
    actions=(
        Action("create-document", ActionKind.CREATE_DOCUMENT),
        Action(
            "create-sketch",
            ActionKind.CREATE_SKETCH,
            parameters={"feature_id": "profile", "plane": "XY"},
        ),
        Action(
            "add-circle",
            ActionKind.ADD_SKETCH_GEOMETRY,
            target_id="profile",
            parameters={
                "geometry": [
                    {
                        "geometry_id": "outer-circle",
                        "type": "circle",
                        "center": [0, 0],
                        "radius": 5,
                    }
                ]
            },
        ),
        Action(
            "constrain-circle",
            ActionKind.ADD_CONSTRAINT,
            target_id="profile",
            parameters={
                "constraints": [
                    {"type": "radius", "geometry_id": "outer-circle", "value": 5}
                ]
            },
        ),
        Action(
            "extrude-profile",
            ActionKind.EXTRUDE,
            target_id="profile",
            parameters={"feature_id": "body", "length": 10, "solid": True},
        ),
    ),
)

receipt = adapter.execute(plan)
print(json.dumps(receipt.to_dict(), indent=2))
if receipt.status is not ExecutionStatus.APPLIED:
    raise RuntimeError("native FreeCAD smoke plan was refused")

features = {feature.feature_id: feature for feature in receipt.after.features}
if features["profile"].parameters["native_type"] != "Sketcher::SketchObject":
    raise RuntimeError("profile was not created as a native FreeCAD sketch")
if features["body"].parameters["native_type"] != "Part::Extrusion":
    raise RuntimeError("body was not created as a native parametric extrusion")

with tempfile.TemporaryDirectory(prefix="cadrig-freecad-capture-") as temporary:
    capture = adapter.capture_artifacts("CADRIGSmoke", Path(temporary))
    captured_names = {artifact.logical_name for artifact in capture.artifacts}
    required_names = {"output.FCStd", "output.step"}
    if not required_names.issubset(captured_names):
        raise RuntimeError(
            f"native artifact capture was incomplete: {capture.to_dict()}"
        )
    if any(item.severity == "error" for item in capture.diagnostics):
        raise RuntimeError(f"native artifact capture failed: {capture.to_dict()}")
    print(json.dumps({"artifact_capture": capture.to_dict()}, indent=2))

rollback = adapter.rollback(receipt.receipt_id)
print(json.dumps({"rollback": rollback.status.value}, indent=2))
if rollback.status is not ExecutionStatus.ROLLED_BACK:
    raise RuntimeError("native FreeCAD smoke rollback failed")
