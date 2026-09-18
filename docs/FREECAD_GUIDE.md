# FreeCAD adapter guide

The first-party `FreeCADKernelAdapter` operates on documents already loaded in
FreeCAD. It imports FreeCAD only when instantiated, so the core package and other
kernel adapters remain usable without a FreeCAD installation.

## Current native operations

- Observe the document feature tree and selected objects.
- Create `Part::Box`, `Part::Cylinder` and `Sketcher::SketchObject` features.
- Add lines and circles with stable semantic geometry IDs.
- Add common geometric and dimensional sketch constraints using those IDs.
- Create parametric `Part::Extrusion` and `Part::Cut` features.
- Edit primitive dimensions and extrusion length.
- Delete features, recompute, inspect errors, dry-run, commit and undo.

The adapter deliberately does not expose arbitrary Python or macros. Fillets,
chamfers and face-attached features are withheld until the protocol has a stable
semantic-reference scheme that does not depend on fragile `Face3`/`Edge7` names.

## Installation

Install this package into the Python environment bundled with FreeCAD. On a
typical Windows installation:

```powershell
& "C:\Program Files\FreeCAD 1.0\bin\python.exe" -m pip install -e .
```

FreeCAD and FreeCADCmd paths vary by release; use the Python executable shipped
with your installation. The `freecad` adapter registers automatically whenever
the `FreeCAD`, `Part` and `Sketcher` modules are importable.

## Using an open document

From a FreeCAD Python console or macro:

```python
from cadrig import (
    ActionGraphPlanner,
    AdapterRegistry,
    ContractCompiler,
    ExecutionEngine,
    NativeCADAgent,
)
from cadrig.adapters.freecad import FreeCADKernelAdapter
from cadrig.models import OpenAICompatibleClient

registry = AdapterRegistry()
registry.register(FreeCADKernelAdapter())
executor = ExecutionEngine(registry)
model = OpenAICompatibleClient(
    base_url="http://localhost:1234/v1",
    model="my-model",
    response_format="json_object",
)
agent = NativeCADAgent(
    executor=executor,
    compiler=ContractCompiler(model),
    planner=ActionGraphPlanner(model),
)
result = agent.run(
    intent="Create a constrained 20 by 10 mm profile and extrude it 12 mm",
    adapter_id="freecad",
    document_id="MyPart",
    apply=False,
)
print(result.to_dict())
```

Use the internal document `Name`, not its display `Label`. Run with `apply=False`
for preview. After inspecting the proposed plan and dry-run receipt, repeat with
`apply=True` to create one native FreeCAD undo transaction.

FreeCADCmd provides the same document and geometry APIs without `FreeCADGui`;
selection observation is simply omitted in that mode. Headless scripts must save
the document explicitly after a successful applied result if persistence is
desired.

## Install the CADRIG workbench

After installing the Python package, copy the bundled workbench into the active
FreeCAD user profile:

```powershell
& "C:\Program Files\FreeCAD 1.0\bin\python.exe" .\freecad_workbench\install.py
```

Restart FreeCAD and select **View → Workbench → CADRIG**. The dock provides
bring-your-own-model settings, contract compilation, typed action-graph preview,
independent verification, bounded repair and a complete execution receipt. It
does not run model-generated Python. The separate macro command remains an
optional review-only automation utility described in [the macro guide](MACRO_GUIDE.md).

## Geometry contract

Sketch geometry uses caller-selected IDs such as `bottom_edge` or `mount_hole`,
which the adapter privately maps to FreeCAD's sketch geometry indices. Constraint
actions reference those semantic IDs. Native topology labels such as `Face1` and
`Edge4` never cross the public model boundary.
