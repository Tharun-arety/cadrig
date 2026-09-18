# FreeCAD macro generation

Macro generation is an optional, separate open-ended automation utility. It supports
tasks that are too broad for the closed typed-action vocabulary, while keeping
code generation separate from code execution.

## Generate

Configure any OpenAI-compatible local or hosted model:

```powershell
$env:CADRIG_MODEL_BASE_URL = "http://localhost:1234/v1"
$env:CADRIG_MODEL = "my-coding-model"
cadrig macro `
  "Create a dialog that asks for box dimensions and creates a parametric box" `
  --output .\generated\CreateBoxDialog.FCMacro
```

The response is constrained to a macro name, description, assumptions and
Python source. The source is parsed as Python before any file is written.

## Policies

`--policy safe` is the default. It accepts documented CAD/UI modules and rejects
syntax errors, shell/process/network modules, dynamic execution, direct file
access and dangerous introspection.

`--policy review` still rejects invalid Python, but saves code containing risky
operations with line-level diagnostics. Use this for legitimate workflows such
as custom file processing that need broader Python access. Review mode does not
execute the macro.

Existing macro files are protected unless `--overwrite` is supplied explicitly.
The output path must end in `.FCMacro`.

## Relationship to the native FreeCAD agent

Install the package and workbench with FreeCAD's bundled Python:

```powershell
$freecad = "C:\Users\tharu\AppData\Local\Programs\FreeCAD 1.1"
& "$freecad\bin\python.exe" -m pip install -e .
& "$freecad\bin\python.exe" .\freecad_workbench\install.py
```

The FreeCAD workbench runs the native contract-driven agent and never invokes
this macro generator. Use `cadrig macro` explicitly when a reviewed source-code
artifact is the desired output. Macro generation cannot be used as a fallback
that bypasses the native agent's typed action or verification boundaries.

## Execution boundary

The runner—not generated code—owns the FreeCAD transaction. Before approved code
runs, it saves a timestamped `.FCStd` backup beside the current document (or in
FreeCAD's temporary directory for an unsaved document). It then captures stdout,
stderr and traceback information, recomputes and inspects the document, commits
only on success, and aborts the transaction on failure. The Run output tab shows
the audit receipt and backup path.

Execution currently happens in FreeCAD's GUI process. A process-isolated runner
with a hard timeout remains roadmap work, so review loops and unbounded operations
carefully before approval.
