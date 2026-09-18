# Bring-your-own-model guide

Models are translation and planning components, not execution authorities. The
native agent first compiles intent into a deterministic `DesignContract`, then
plans a closed `ActionPlan` against the selected adapter's capabilities. The
independent verifier—not the model—decides whether the preview may be committed.

## OpenAI-compatible endpoints

The built-in connector targets `<base-url>/chat/completions` and sends no API
key unless the configured environment variable is present:

```powershell
$env:CADRIG_MODEL_BASE_URL = "https://your-provider.example/v1"
$env:CADRIG_MODEL = "your-model-id"
$env:CADRIG_MODEL_API_KEY = "your-key"
cadrig ask "Add a 10 mm mounting boss" `
  --document active-part --adapter your-adapter
```

Strict `json_schema` output is the default. Select `json_object` for providers
with JSON mode, or `none` for local servers that support neither. The planner
still parses and validates the result in all three modes. Omit `--apply` while
evaluating a model; this asks the adapter for a non-mutating dry run.

## Other model APIs

Implement the small `cadrig.models.ModelClient` protocol and use it for the
contract compiler and action-graph planner:

```python
from cadrig import (
    ActionGraphPlanner,
    ContractCompiler,
    ExecutionEngine,
    NativeCADAgent,
)

model = MyModelClient()
agent = NativeCADAgent(
    executor=ExecutionEngine(registry),
    compiler=ContractCompiler(model),
    planner=ActionGraphPlanner(model),
)
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

The core rejects malformed JSON, unknown contract fields, untestable predicates,
executable action kinds, changed intent or document IDs, stale revisions and
actions the active backend does not declare. The adapter remains responsible for
parameter-level geometry validation, native transactionality, rebuild inspection
and rollback. The `ContractVerifier` separately evaluates result requirements and
preservation invariants.
