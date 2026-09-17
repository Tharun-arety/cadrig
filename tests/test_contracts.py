import pytest

from cadcopilot.contracts import ActionPlan, ContractError


def valid_payload():
    return {
        "schema_version": "1.0.0",
        "plan_id": "plan-1",
        "document_id": "part-1",
        "base_revision": 0,
        "actions": [
            {"action_id": "create", "kind": "create_document", "parameters": {}}
        ],
    }


def test_plan_round_trip_is_deterministic():
    plan = ActionPlan.from_dict(valid_payload())
    assert ActionPlan.from_dict(plan.to_dict()) == plan


def test_unknown_plan_and_action_fields_are_rejected():
    payload = valid_payload()
    payload["executable_code"] = "dangerous()"
    with pytest.raises(ContractError, match="unknown plan fields"):
        ActionPlan.from_dict(payload)
    payload = valid_payload()
    payload["actions"][0]["macro"] = "dangerous()"
    with pytest.raises(ContractError, match="unknown action fields"):
        ActionPlan.from_dict(payload)


def test_unknown_action_kind_is_rejected():
    payload = valid_payload()
    payload["actions"][0]["kind"] = "execute_python"
    with pytest.raises(ContractError, match="unknown action kind"):
        ActionPlan.from_dict(payload)


def test_duplicate_action_ids_are_rejected():
    payload = valid_payload()
    payload["actions"].append(dict(payload["actions"][0]))
    with pytest.raises(ContractError, match="must be unique"):
        ActionPlan.from_dict(payload)
