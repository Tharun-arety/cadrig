import json

import pytest

from cadcopilot.adapters.memory import MemoryKernelAdapter
from cadcopilot.agent import CopilotAgent
from cadcopilot.contracts import ExecutionStatus
from cadcopilot.executor import CopilotExecutor
from cadcopilot.models.base import ModelMessage
from cadcopilot.planner import CopilotPlanner, PlanningError
from cadcopilot.registry import AdapterRegistry


class FakeModel:
    def __init__(self, response):
        self.response = response
        self.messages = None
        self.schema = None

    def complete(self, messages, *, response_schema=None):
        self.messages = messages
        self.schema = response_schema
        return json.dumps(self.response)


def model_plan(**overrides):
    result = {
        "schema_version": "1.0.0",
        "plan_id": "model-plan",
        "document_id": "part-1",
        "base_revision": 0,
        "actions": [
            {"action_id": "create", "kind": "create_document", "parameters": {}},
            {
                "action_id": "box",
                "kind": "add_box",
                "parameters": {
                    "feature_id": "body",
                    "width": 10,
                    "depth": 20,
                    "height": 5,
                },
            },
        ],
    }
    result.update(overrides)
    return result


def agent_for(model):
    registry = AdapterRegistry()
    registry.register(MemoryKernelAdapter())
    executor = CopilotExecutor(registry)
    return CopilotAgent(executor, CopilotPlanner(model)), executor


def test_agent_converts_intent_to_a_capability_scoped_dry_run():
    model = FakeModel(model_plan())
    agent, executor = agent_for(model)

    result = agent.run(
        intent="Create a 10 by 20 by 5 box",
        adapter_id="memory",
        document_id="part-1",
    )

    assert result.receipt.status is ExecutionStatus.DRY_RUN
    assert executor.observe("memory", "part-1") is None
    assert model.schema["properties"]["actions"]["items"]["properties"]["kind"]["enum"] == [
        "create_document",
        "add_box",
        "add_cylinder",
        "set_parameter",
        "delete_feature",
    ]
    assert all(isinstance(message, ModelMessage) for message in model.messages)


def test_apply_commits_the_validated_plan():
    agent, executor = agent_for(FakeModel(model_plan()))
    result = agent.run(
        intent="Create a box",
        adapter_id="memory",
        document_id="part-1",
        apply=True,
    )
    assert result.receipt.status is ExecutionStatus.APPLIED
    assert executor.observe("memory", "part-1").features[0].feature_id == "body"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"document_id": "other"}, "document_id"),
        ({"base_revision": 7}, "base_revision"),
    ],
)
def test_planner_rejects_model_changes_to_execution_scope(override, message):
    agent, _ = agent_for(FakeModel(model_plan(**override)))
    with pytest.raises(PlanningError, match=message):
        agent.run(intent="Create a box", adapter_id="memory", document_id="part-1")


def test_planner_rejects_non_json_output():
    model = FakeModel(model_plan())
    model.response = object()
    model.complete = lambda messages, response_schema=None: "not JSON"
    agent, _ = agent_for(model)
    with pytest.raises(PlanningError, match="non-JSON"):
        agent.run(intent="Create a box", adapter_id="memory", document_id="part-1")
