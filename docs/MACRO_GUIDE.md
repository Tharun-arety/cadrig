# FreeCAD macro generation

Macro generation is the primary open-ended automation workflow. It supports
tasks that are too broad for the closed typed-action vocabulary, while keeping
code generation separate from code execution.

## Generate

Configure any OpenAI-compatible local or hosted model:

```powershell
$env:CADCOPILOT_MODEL_BASE_URL = "http://localhost:1234/v1"
$env:CADCOPILOT_MODEL = "my-coding-model"
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

## Use the embedded FreeCAD copilot

Install the package and workbench with FreeCAD's bundled Python:

```powershell
$freecad = "C:\Users\tharu\AppData\Local\Programs\FreeCAD 1.1"
& "$freecad\bin\python.exe" -m pip install -e .
& "$freecad\bin\python.exe" .\freecad_workbench\install.py
```

Restart FreeCAD and select **View → Workbench → CADRIG**. In the dock:

1. Enter an OpenAI-compatible base URL, model identifier and optional API key.
2. Describe the automation and select **Generate macro**.
3. Inspect or edit the **Macro**, **Diff** and **Review** tabs.
4. Select **Approve & Run** and confirm the execution dialog.
5. If execution fails, inspect **Run output** and select **Repair from error**.

The base URL, model, response format and policy are saved in FreeCAD preferences.
The API key is session-only, or can be read from the environment variable named
in **Or key env** (default `CADCOPILOT_MODEL_API_KEY`).

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
