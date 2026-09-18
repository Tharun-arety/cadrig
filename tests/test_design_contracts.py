import pytest

from cadrig.contracts import ContractError
from cadrig.design_contracts import DesignContract, DesignPredicate, PredicateKind


def predicate(predicate_id="exists", kind="feature_exists", parameters=None):
    return {
        "predicate_id": predicate_id,
        "kind": kind,
        "parameters": parameters or {"feature_id": "body"},
    }


def contract_payload(**overrides):
    payload = {
        "schema_version": "1.0.0",
        "contract_id": "contract-1",
        "intent": "Create a box",
        "document_id": "part-1",
        "requirements": [predicate()],
        "invariants": [],
    }
    payload.update(overrides)
    return payload


def test_design_contract_round_trip_is_closed_and_typed():
    contract = DesignContract.from_dict(contract_payload())
    assert DesignContract.from_dict(contract.to_dict()) == contract
    assert contract.requirements[0].kind is PredicateKind.FEATURE_EXISTS


def test_contract_rejects_unknown_fields():
    with pytest.raises(ContractError, match="unknown design-contract fields"):
        DesignContract.from_dict(contract_payload(hidden_instruction="run code"))


def test_contract_rejects_requirement_used_as_invariant():
    with pytest.raises(ContractError, match="invalid invariant predicates"):
        DesignContract.from_dict(
            contract_payload(requirements=[predicate()], invariants=[predicate("bad")])
        )


@pytest.mark.parametrize(
    ("kind", "parameters", "message"),
    [
        (PredicateKind.FEATURE_COUNT, {"count": -1}, "non-negative integer"),
        (PredicateKind.PARAMETER_EQUALS, {"feature_id": "body", "name": "width"}, "requires value"),
        (PredicateKind.MAX_FEATURE_DELTA, {"maximum": -1}, "non-negative integer"),
    ],
)
def test_predicate_parameter_contracts_fail_closed(kind, parameters, message):
    with pytest.raises(ContractError, match=message):
        DesignPredicate("predicate", kind, parameters)
