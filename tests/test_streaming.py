import importlib
import sys
from pathlib import Path

RUNTIME = (
    Path(__file__).resolve().parents[1]
    / "freecad_workbench"
    / "CADCopilot"
    / "agent_runtime"
)
sys.path.insert(0, str(RUNTIME))

accumulate_tool_call_deltas = importlib.import_module(
    "core.streaming"
).accumulate_tool_call_deltas


def test_preserves_gemini_thought_signature_across_streamed_tool_call():
    calls = {}
    accumulate_tool_call_deltas(calls, [{
        "index": 0,
        "id": "call_123",
        "type": "function",
        "function": {"name": "execute_", "arguments": '{"code":"'},
        "extra_content": {
            "google": {"thought_signature": "signature-"},
        },
    }])
    accumulate_tool_call_deltas(calls, [{
        "index": 0,
        "function": {"name": "code", "arguments": "pass\"}"},
        "extra_content": {
            "google": {"thought_signature": "fragment"},
        },
    }])

    assert calls[0] == {
        "id": "call_123",
        "type": "function",
        "function": {
            "name": "execute_code",
            "arguments": '{"code":"pass"}',
        },
        "extra_content": {
            "google": {"thought_signature": "signature-fragment"},
        },
    }


def test_keeps_parallel_tool_calls_separate():
    calls = {}
    accumulate_tool_call_deltas(calls, [
        {"index": 0, "id": "first", "function": {"name": "one"}},
        {"index": 1, "id": "second", "function": {"name": "two"}},
    ])

    assert calls[0]["function"]["name"] == "one"
    assert calls[1]["function"]["name"] == "two"
