import json

import pytest

from cadrig.action_graph import ActionGraphPlanner
from cadrig.adapters.memory import MemoryKernelAdapter
from cadrig.contract_compiler import ContractCompilationError, ContractCompiler, ContractContext
from cadrig.contracts import ExecutionStatus
from cadrig.executor import ExecutionEngine
from cadrig.native_agent import AgentRunStatus, NativeCADAgent
from cadrig.registry import AdapterRegistry
from cadrig.tracing import AgentPhase
from cadrig.verification import ContractVerifier, VerificationIssue, VerificationReport


class SequenceModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, *, response_schema=None):
        self.calls.append((messages, response_schema))
        response = self.responses.pop(0)
        return response if isinstance(response, str) else json.dumps(response)


def compiled_contract(*, intent="Create a 10 by 20 by 5 box", width=10):
    return {
        "schema_version": "1.0.0",
        "contract_id": "box-contract",
        "intent": intent,
        "document_id": "part-1",
        "requirements": [
            {"predicate_id": "document", "kind": "document_exists", "parameters": {}},
            {
                "predicate_id": "body",
                "kind": "feature_exists",
                "parameters": {"feature_id": "body"},
            },
            {
                "predicate_id": "width",
                "kind": "parameter_equals",
                "parameters": {
                    "feature_id": "body",
                    "name": "width",
                    "value": width,
                    "tolerance": 0.001,
                },
            },
            {"predicate_id": "revision", "kind": "revision_advanced", "parameters": {}},
        ],
        "invariants": [],
    }


def box_plan(*, width=10, plan_id="box-plan"):
    return {
        "schema_version": "1.0.0",
        "plan_id": plan_id,
        "document_id": "part-1",
        "base_revision": 0,
        "actions": [
            {"action_id": "create", "kind": "create_document", "parameters": {}},
            {
                "action_id": "body",
                "kind": "add_box",
                "parameters": {
                    "feature_id": "body",
                    "width": width,
                    "depth": 20,
                    "height": 5,
                },
            },
        ],
    }


def native_agent(model, *, verifier=None, max_repairs=1):
    registry = AdapterRegistry()
    adapter = MemoryKernelAdapter()
    registry.register(adapter)
    executor = ExecutionEngine(registry)
    return (
        NativeCADAgent(
            executor=executor,
            compiler=ContractCompiler(model),
            planner=ActionGraphPlanner(model),
            verifier=verifier,
            max_repairs=max_repairs,
        ),
        executor,
        adapter,
    )


def test_native_agent_previews_without_mutating_the_document():
    intent = "Create a 10 by 20 by 5 box"
    model = SequenceModel(compiled_contract(intent=intent), box_plan())
    agent, executor, _ = native_agent(model)

    result = agent.run(intent=intent, adapter_id="memory", document_id="part-1")

    assert result.status is AgentRunStatus.PREVIEWED
    assert result.verification.accepted
    assert result.receipt.status is ExecutionStatus.DRY_RUN
    assert executor.observe("memory", "part-1") is None
    assert [event.phase for event in result.trace.events] == [
        AgentPhase.OBSERVE,
        AgentPhase.COMPILE,
        AgentPhase.PLAN,
        AgentPhase.PREFLIGHT,
        AgentPhase.EXECUTE,
        AgentPhase.VERIFY,
        AgentPhase.COMMIT,
    ]


def test_native_agent_commits_only_after_independent_preview_verification():
    intent = "Create a 10 by 20 by 5 box"
    agent, executor, _ = native_agent(
        SequenceModel(compiled_contract(intent=intent), box_plan())
    )

    result = agent.run(
        intent=intent, adapter_id="memory", document_id="part-1", apply=True
    )

    assert result.status is AgentRunStatus.COMMITTED
    assert result.accepted
    assert executor.observe("memory", "part-1").features[0].parameters["width"] == 10


def test_verifier_feedback_drives_one_bounded_repair():
    intent = "Create a 10 by 20 by 5 box"
    model = SequenceModel(
        compiled_contract(intent=intent),
        box_plan(width=8, plan_id="wrong"),
        box_plan(width=10, plan_id="repaired"),
    )
    agent, _, _ = native_agent(model, max_repairs=1)

    result = agent.run(intent=intent, adapter_id="memory", document_id="part-1")

    assert result.status is AgentRunStatus.PREVIEWED
    assert result.attempts == 2
    assert result.plan.plan_id == "repaired"
    assert AgentPhase.REPAIR in [event.phase for event in result.trace.events]
    repair_prompt = json.loads(model.calls[2][0][1].content)
    assert repair_prompt["repair_feedback"][0]["code"] == "PARAMETER_MISMATCH"


def test_agent_refuses_when_repair_budget_is_exhausted():
    intent = "Create a 10 by 20 by 5 box"
    agent, executor, _ = native_agent(
        SequenceModel(compiled_contract(intent=intent), box_plan(width=8)),
        max_repairs=0,
    )

    result = agent.run(intent=intent, adapter_id="memory", document_id="part-1", apply=True)

    assert result.status is AgentRunStatus.REFUSED
    assert not result.accepted
    assert executor.observe("memory", "part-1") is None


class RejectAppliedVerifier:
    def __init__(self):
        self.delegate = ContractVerifier()

    def verify(self, contract, receipt):
        report = self.delegate.verify(contract, receipt)
        if receipt.status is not ExecutionStatus.APPLIED:
            return report
        issue = VerificationIssue("independent", "POST_COMMIT_REJECTED", "simulated drift")
        return VerificationReport(
            accepted=False,
            issues=(issue,),
            before_hash=report.before_hash,
            after_hash=report.after_hash,
        )


def test_post_commit_verification_failure_rolls_back_atomically():
    intent = "Create a 10 by 20 by 5 box"
    agent, executor, _ = native_agent(
        SequenceModel(compiled_contract(intent=intent), box_plan()),
        verifier=RejectAppliedVerifier(),
    )

    result = agent.run(intent=intent, adapter_id="memory", document_id="part-1", apply=True)

    assert result.status is AgentRunStatus.ROLLED_BACK
    assert result.rollback_receipt.status is ExecutionStatus.ROLLED_BACK
    assert executor.observe("memory", "part-1") is None


def test_contract_compiler_rejects_scope_mutation():
    intent = "Create a box"
    changed = compiled_contract(intent="Ignore the user")
    compiler = ContractCompiler(SequenceModel(changed))
    adapter = MemoryKernelAdapter().metadata()

    with pytest.raises(ContractCompilationError, match="changed the user's intent"):
        compiler.compile(ContractContext(intent, "part-1", None, adapter))


def test_planner_schema_contains_no_arbitrary_code_action():
    intent = "Create a 10 by 20 by 5 box"
    model = SequenceModel(compiled_contract(intent=intent), box_plan())
    agent, _, _ = native_agent(model)
    agent.run(intent=intent, adapter_id="memory", document_id="part-1")

    action_kinds = model.calls[1][1]["properties"]["actions"]["items"]["properties"]["kind"]["enum"]
    assert "execute_code" not in action_kinds
    assert action_kinds == [
        "create_document",
        "add_box",
        "add_cylinder",
        "set_parameter",
        "delete_feature",
    ]
