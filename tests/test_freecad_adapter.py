import copy

from cadcopilot.adapters.freecad import FreeCADKernelAdapter
from cadcopilot.contracts import Action, ActionKind, ActionPlan, ExecutionStatus


class FakeVector:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z

    @property
    def Length(self):
        return (self.x**2 + self.y**2 + self.z**2) ** 0.5


class FakeObject:
    def __init__(self, type_id, name):
        self.TypeId = type_id
        self.Name = name
        self.Label = name
        self.PropertiesList = []
        self.State = []
        if type_id.startswith("Sketcher::"):
            self.Geometry = []
            self.Constraints = []
        elif type_id == "Part::Extrusion":
            self.LengthFwd = 0
            self.Solid = False
            self.Symmetric = False

    def addProperty(self, _property_type, name, _group):
        self.PropertiesList.append(name)
        setattr(self, name, "")

    def addGeometry(self, geometry, _construction):
        self.Geometry.append(geometry)
        return len(self.Geometry) - 1

    def addConstraint(self, constraint):
        self.Constraints.append(constraint)
        return len(self.Constraints) - 1


class FakeDocument:
    def __init__(self, name):
        self.Name = name
        self.Label = name
        self.Objects = []
        self._transaction = None
        self._undo = None

    def addObject(self, type_id, name):
        obj = FakeObject(type_id, name)
        self.Objects.append(obj)
        return obj

    def removeObject(self, name):
        self.Objects = [obj for obj in self.Objects if obj.Name != name]

    def openTransaction(self, _name):
        self._transaction = copy.deepcopy(self.Objects)

    def commitTransaction(self):
        self._undo = self._transaction
        self._transaction = None

    def abortTransaction(self):
        self.Objects = self._transaction
        self._transaction = None

    def undo(self):
        if self._undo is None:
            return False
        self.Objects = self._undo
        self._undo = None
        return True

    def recompute(self):
        return True


class FakeApp:
    def __init__(self):
        self.documents = {}

    def Version(self):
        return (1, 0, 2)

    def newDocument(self, name):
        document = FakeDocument(name)
        self.documents[name] = document
        return document

    def getDocument(self, name):
        if name not in self.documents:
            raise NameError(name)
        return self.documents[name]

    def closeDocument(self, name):
        del self.documents[name]

    Vector = FakeVector

    @staticmethod
    def Rotation(*args):
        return ("rotation", args)

    @staticmethod
    def Placement(base, rotation):
        return (base, rotation)


class FakePart:
    @staticmethod
    def LineSegment(start, end):
        return ("line", start, end)

    @staticmethod
    def Circle(center, normal, radius):
        return ("circle", center, normal, radius)


class FakeSketcher:
    @staticmethod
    def Constraint(*args):
        return args


def make_adapter():
    app = FakeApp()
    adapter = FreeCADKernelAdapter(
        app=app,
        gui=object(),
        part=FakePart(),
        sketcher=FakeSketcher(),
    )
    return adapter, app


def plan(plan_id, revision, *actions, document_id="part_1"):
    return ActionPlan(
        plan_id=plan_id,
        document_id=document_id,
        base_revision=revision,
        actions=tuple(actions),
    )


def test_freecad_adapter_creates_native_parametric_primitive_and_rolls_back():
    adapter, app = make_adapter()
    create = plan(
        "create-box",
        0,
        Action("create-doc", ActionKind.CREATE_DOCUMENT),
        Action(
            "box",
            ActionKind.ADD_BOX,
            parameters={"feature_id": "main-body", "width": 10, "depth": 20, "height": 5},
        ),
    )

    receipt = adapter.execute(create)

    assert receipt.status is ExecutionStatus.APPLIED
    assert receipt.after.features[0].feature_id == "main-body"
    assert receipt.after.features[0].parameters["native_type"] == "Part::Box"
    assert receipt.after.features[0].parameters["width"] == 10
    assert app.documents["part_1"].Objects[0].Length == 10
    assert adapter.rollback(receipt.receipt_id).status is ExecutionStatus.ROLLED_BACK
    assert adapter.observe("part_1") is None


def test_freecad_adapter_dry_run_does_not_leave_a_document():
    adapter, _ = make_adapter()
    receipt = adapter.execute(
        plan(
            "preview",
            0,
            Action("create-doc", ActionKind.CREATE_DOCUMENT),
            Action(
                "cylinder",
                ActionKind.ADD_CYLINDER,
                parameters={"feature_id": "boss", "radius": 3, "height": 8},
            ),
        ),
        dry_run=True,
    )
    assert receipt.status is ExecutionStatus.DRY_RUN
    assert adapter.observe("part_1") is None


def test_freecad_adapter_builds_semantic_sketch_and_parametric_extrusion():
    adapter, _ = make_adapter()
    geometry = [
        {"geometry_id": "bottom", "type": "line", "start": [0, 0], "end": [20, 0]},
        {"geometry_id": "right", "type": "line", "start": [20, 0], "end": [20, 10]},
        {"geometry_id": "top", "type": "line", "start": [20, 10], "end": [0, 10]},
        {"geometry_id": "left", "type": "line", "start": [0, 10], "end": [0, 0]},
    ]
    receipt = adapter.execute(
        plan(
            "sketch-and-extrude",
            0,
            Action("create-doc", ActionKind.CREATE_DOCUMENT),
            Action(
                "create-sketch",
                ActionKind.CREATE_SKETCH,
                parameters={"feature_id": "profile", "plane": "XY"},
            ),
            Action(
                "geometry",
                ActionKind.ADD_SKETCH_GEOMETRY,
                target_id="profile",
                parameters={"geometry": geometry},
            ),
            Action(
                "constraints",
                ActionKind.ADD_CONSTRAINT,
                target_id="profile",
                parameters={
                    "constraints": [
                        {"type": "horizontal", "geometry_id": "bottom"},
                        {"type": "vertical", "geometry_id": "right"},
                        {
                            "type": "coincident",
                            "geometry_id": "bottom",
                            "point": "end",
                            "other_geometry_id": "right",
                            "other_point": "start",
                        },
                    ]
                },
            ),
            Action(
                "extrude",
                ActionKind.EXTRUDE,
                target_id="profile",
                parameters={"feature_id": "body", "length": 12, "solid": True},
            ),
        )
    )

    assert receipt.status is ExecutionStatus.APPLIED
    by_id = {feature.feature_id: feature for feature in receipt.after.features}
    assert by_id["profile"].parameters["geometry_ids"] == ["bottom", "left", "right", "top"]
    assert by_id["profile"].parameters["constraint_count"] == 3
    assert by_id["body"].parameters["base_id"] == "profile"
    assert by_id["body"].parameters["length"] == 12


def test_freecad_revision_detects_native_edits_and_rejects_stale_plans():
    adapter, app = make_adapter()
    document = app.newDocument("part_1")
    box = document.addObject("Part::Box", "Box")
    box.Length, box.Width, box.Height = 1, 2, 3
    assert adapter.observe("part_1").revision == 0

    box.Length = 7
    assert adapter.observe("part_1").revision == 1
    stale = plan(
        "stale",
        0,
        Action(
            "edit",
            ActionKind.SET_PARAMETER,
            target_id="Box",
            parameters={"name": "width", "value": 9},
        ),
    )
    assert adapter.execute(stale).diagnostics[0].code == "STALE_DOCUMENT_REVISION"


def test_freecad_rejects_document_ids_it_cannot_preserve():
    adapter, _ = make_adapter()
    invalid = plan(
        "invalid-name",
        0,
        Action("create-doc", ActionKind.CREATE_DOCUMENT),
        document_id="part-with-dashes",
    )
    assert adapter.execute(invalid).diagnostics[0].code == "INVALID_DOCUMENT_ID"
