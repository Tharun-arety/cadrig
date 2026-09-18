import hashlib
import json

import pytest

from cadrig.action_graph import ActionGraphPlanner
from cadrig.adapters.memory import MemoryKernelAdapter
from cadrig.artifacts import ArtifactCapture, CapturedArtifact
from cadrig.contract_compiler import ContractCompiler
from cadrig.episodes import (
    EpisodeContext,
    EpisodeSplit,
    EpisodeStore,
    EpisodeStoreError,
)
from cadrig.executor import ExecutionEngine
from cadrig.native_agent import NativeAgentError, NativeCADAgent
from cadrig.registry import AdapterRegistry


class SequenceModel:
    def __init__(self, *responses):
        self.responses = list(responses)

    def complete(self, messages, *, response_schema=None):
        response = self.responses.pop(0)
        return response if isinstance(response, str) else json.dumps(response)


def contract(intent="Create a 10 by 20 by 5 box"):
    return {
        "schema_version": "1.0.0",
        "contract_id": "episode-box-contract",
        "intent": intent,
        "document_id": "episode-part",
        "requirements": [
            {"predicate_id": "document", "kind": "document_exists", "parameters": {}},
            {
                "predicate_id": "body",
                "kind": "feature_exists",
                "parameters": {"feature_id": "body"},
            },
            {"predicate_id": "revision", "kind": "revision_advanced", "parameters": {}},
        ],
        "invariants": [],
    }


def plan():
    return {
        "schema_version": "1.0.0",
        "plan_id": "episode-box-plan",
        "document_id": "episode-part",
        "base_revision": 0,
        "actions": [
            {"action_id": "create", "kind": "create_document", "parameters": {}},
            {
                "action_id": "body",
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


def agent(model, *, store=None, context=None, adapter=None):
    registry = AdapterRegistry()
    registry.register(adapter or MemoryKernelAdapter())
    return NativeCADAgent(
        executor=ExecutionEngine(registry),
        compiler=ContractCompiler(model),
        planner=ActionGraphPlanner(model),
        episode_store=store,
        episode_context=context,
    )


class CapturingMemoryAdapter(MemoryKernelAdapter):
    def capture_artifacts(self, document_id, destination):
        native = destination / "output.FCStd"
        step = destination / "output.step"
        native.write_bytes(f"native:{document_id}".encode())
        step.write_bytes(b"ISO-10303-21;\nEND-ISO-10303-21;\n")
        return ArtifactCapture(
            artifacts=(
                CapturedArtifact(
                    "output.FCStd",
                    native,
                    "application/x-freecad-document",
                    "native_document",
                ),
                CapturedArtifact("output.step", step, "model/step", "exchange_document"),
            )
        )


def test_native_agent_records_hashed_secret_safe_episode(tmp_path):
    store = EpisodeStore(tmp_path / "episodes")
    context = EpisodeContext(
        task_id="synthetic-box-001",
        task_source="procedural_generator",
        task_license="Apache-2.0",
        model={
            "provider": "test",
            "model": "sequence",
            "api_key": "must-not-be-written",
            "input_tokens": 12,
        },
    )

    result = agent(SequenceModel(contract(), plan()), store=store, context=context).run(
        intent="Create a 10 by 20 by 5 box",
        adapter_id="memory",
        document_id="episode-part",
    )

    assert result.episode is not None
    episode_dir = result.episode.path
    assert episode_dir.is_relative_to(tmp_path / "episodes" / "training")
    expected = {
        "episode.json",
        "trace.jsonl",
        "contract.json",
        "action_graph.json",
        "verification.json",
        "receipt.json",
        "rollback_receipt.json",
        "input_snapshot.json",
        "output_snapshot.json",
    }
    assert expected.issubset({path.name for path in episode_dir.iterdir()})

    manifest = json.loads((episode_dir / "episode.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["task"]["task_id"] == "synthetic-box-001"
    assert manifest["data_policy"] == {
        "evaluation_only": False,
        "split": "training",
        "training_eligible": False,
    }
    assert manifest["system"]["model"]["api_key"] == "[REDACTED]"
    assert manifest["system"]["model"]["input_tokens"] == 12
    for item in manifest["files"]:
        payload = (episode_dir / item["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
        assert len(payload) == item["bytes"]
    trace_lines = (episode_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["sequence"] for line in trace_lines] == list(
        range(len(trace_lines))
    )


def test_episode_is_immutable_and_can_copy_named_artifacts(tmp_path):
    plain_result = agent(SequenceModel(contract(), plan())).run(
        intent="Create a 10 by 20 by 5 box",
        adapter_id="memory",
        document_id="episode-part",
    )
    source = tmp_path / "output.step"
    source.write_bytes(b"ISO-10303-21;\nEND-ISO-10303-21;\n")
    store = EpisodeStore(tmp_path / "episodes")

    record = store.record_run(plain_result, artifacts={"output.step": source})

    assert (record.path / "artifacts" / "output.step").read_bytes() == source.read_bytes()
    with pytest.raises(EpisodeStoreError, match="immutable"):
        store.record_run(plain_result)


def test_committed_run_automatically_captures_native_artifacts(tmp_path):
    store = EpisodeStore(tmp_path / "episodes")
    result = agent(
        SequenceModel(contract(), plan()),
        store=store,
        adapter=CapturingMemoryAdapter(),
    ).run(
        intent="Create a 10 by 20 by 5 box",
        adapter_id="memory",
        document_id="episode-part",
        apply=True,
    )

    assert result.episode is not None
    assert result.artifact_capture is not None
    assert result.artifact_capture.to_dict()["complete"] is True
    assert {item.logical_name for item in result.artifact_capture.artifacts} == {
        "output.FCStd",
        "output.step",
    }
    assert all(item.path.is_file() for item in result.artifact_capture.artifacts)
    capture = json.loads(
        (result.episode.path / "artifact_capture.json").read_text(encoding="utf-8")
    )
    assert capture["complete"] is True


def test_unsafe_artifact_name_is_rejected_without_partial_episode(tmp_path):
    result = agent(SequenceModel(contract(), plan())).run(
        intent="Create a 10 by 20 by 5 box",
        adapter_id="memory",
        document_id="episode-part",
    )
    source = tmp_path / "output.step"
    source.write_text("step", encoding="utf-8")
    store = EpisodeStore(tmp_path / "episodes")

    with pytest.raises(EpisodeStoreError, match="unsafe artifact"):
        store.record_run(result, artifacts={"../output.step": source})

    assert not list((tmp_path / "episodes").rglob("episode.json"))


def test_evaluation_episode_cannot_be_training_eligible():
    with pytest.raises(ValueError, match="evaluation episodes"):
        EpisodeContext(split=EpisodeSplit.EVALUATION, training_eligible=True)


def test_contract_compilation_failure_is_also_recorded(tmp_path):
    store = EpisodeStore(tmp_path / "episodes")
    invalid = contract(intent="Changed intent")
    native = agent(SequenceModel(invalid), store=store)

    with pytest.raises(NativeAgentError, match="contract compilation failed"):
        native.run(
            intent="Create a 10 by 20 by 5 box",
            adapter_id="memory",
            document_id="episode-part",
        )

    assert native.last_episode is not None
    manifest = json.loads(
        (native.last_episode.path / "episode.json").read_text(encoding="utf-8")
    )
    assert manifest["run"]["status"] == "error"
    assert manifest["run"]["accepted"] is False
    assert "changed the user's intent" in manifest["run"]["error"]


def test_unknown_adapter_failure_is_recorded(tmp_path):
    store = EpisodeStore(tmp_path / "episodes")
    native = agent(SequenceModel(), store=store)

    with pytest.raises(NativeAgentError, match="adapter is not registered"):
        native.run(
            intent="Create a box",
            adapter_id="missing",
            document_id="episode-part",
        )

    assert native.last_episode is not None
    trace = (native.last_episode.path / "trace.jsonl").read_text(encoding="utf-8")
    assert json.loads(trace)["status"] == "failed"
