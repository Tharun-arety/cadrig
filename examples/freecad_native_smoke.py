"""Real FreeCAD smoke test; run with FreeCADCmd, not regular Python."""

import json

from cadcopilot.adapters.freecad import FreeCADKernelAdapter
from cadcopilot.contracts import Action, ActionKind, ActionPlan, ExecutionStatus

adapter = FreeCADKernelAdapter()
plan = ActionPlan(
    plan_id="freecad-native-smoke",
    document_id="CadCopilotSmoke",
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

rollback = adapter.rollback(receipt.receipt_id)
print(json.dumps({"rollback": rollback.status.value}, indent=2))
if rollback.status is not ExecutionStatus.ROLLED_BACK:
    raise RuntimeError("native FreeCAD smoke rollback failed")
