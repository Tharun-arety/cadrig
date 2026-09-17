# Bring-your-own-model guide

Models are planning components, not execution authorities. A model receives a
serialized document snapshot and the action vocabulary supported by the chosen
CAD adapter. It returns one closed `ActionPlan`; the core checks and the CAD
adapter validate that plan before any native API is called.

## OpenAI-compatible endpoints

The built-in connector targets `<base-url>/chat/completions` and sends no API
key unless the configured environment variable is present:

```powershell
$env:CADCOPILOT_MODEL_BASE_URL = "https://your-provider.example/v1"
$env:CADCOPILOT_MODEL = "your-model-id"
$env:CADCOPILOT_MODEL_API_KEY = "your-key"
cadrig ask "Add a 10 mm mounting boss" `
  --document active-part --adapter your-adapter
```

Strict `json_schema` output is the default. Select `json_object` for providers
with JSON mode, or `none` for local servers that support neither. The planner
still parses and validates the result in all three modes. Omit `--apply` while
evaluating a model; this asks the adapter for a non-mutating dry run.

## Other model APIs

Implement the small `cadcopilot.models.ModelClient` protocol and pass it to
`CopilotPlanner`:

```python
from cadcopilot import CopilotAgent, CopilotPlanner

planner = CopilotPlanner(MyModelClient())
agent = CopilotAgent(executor, planner)
result = agent.run(
    intent="Move these holes 5 mm inward",
    adapter_id="my-kernel",
    document_id="part-42",
    apply=False,
)
```

`complete(messages, response_schema=...)` must return the assistant text. Keep
provider credentials and retry/rate-limit policy inside the connector; never put
secrets in prompts or action parameters.

## Trust boundary

The core rejects malformed JSON, unknown contract fields, executable action
kinds, changed document IDs, stale base revisions and actions the active kernel
does not declare. The adapter remains responsible for parameter-level geometry
validation, native transactionality, rebuild inspection and rollback.
