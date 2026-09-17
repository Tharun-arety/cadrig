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

AgentController = importlib.import_module("agent.controller").AgentController
loop_module = importlib.import_module("agent.loop")
AgentLoop = loop_module.AgentLoop
LoopActionKind = loop_module.LoopActionKind
ChatSession = importlib.import_module("core.session").ChatSession


def test_gemini_stop_with_tool_calls_executes_tools():
    session = ChatSession()
    loop = AgentLoop(AgentController(session), context="", last_mode="auto")
    loop.start("Create a cylinder")
    tool_calls = [
        {
            "id": "call_gemini_1",
            "type": "function",
            "function": {
                "name": "execute_code",
                "arguments": '{"code":"cq_show(cq.Workplane(\\"XY\\").cylinder(20, 5))"}',
            },
        }
    ]

    action = loop.handle_stream_done(
        {
            "choices": [
                {
                    "message": {"content": "", "tool_calls": tool_calls},
                    "finish_reason": "stop",
                }
            ]
        },
        has_streaming_text=False,
    )

    assert action.kind == LoopActionKind.EXECUTE_TOOLS
    assert action.tool_calls == tool_calls
    assert loop.mode == "tool_calling"
