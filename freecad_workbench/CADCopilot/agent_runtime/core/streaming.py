"""Provider-neutral helpers for assembling streamed chat-completion deltas."""
from __future__ import annotations


def _merge_delta(target: dict, delta: dict) -> None:
    """Merge one streamed object delta without discarding extension fields.

    OpenAI-compatible providers may add nested metadata to tool calls.  Gemini
    3, for example, returns ``extra_content.google.thought_signature`` and
    requires that exact value in the next request.  Strings in streamed delta
    fields are fragments, so concatenate them while recursively retaining
    dictionaries that this client does not otherwise interpret.
    """
    for key, value in delta.items():
        if key == "index" or value is None:
            continue
        current = target.get(key)
        if isinstance(value, dict):
            if not isinstance(current, dict):
                current = {}
                target[key] = current
            _merge_delta(current, value)
        elif isinstance(value, str) and isinstance(current, str):
            target[key] = current + value
        elif isinstance(value, list) and isinstance(current, list):
            current.extend(value)
        else:
            target[key] = value


def accumulate_tool_call_deltas(
    tool_call_map: dict[int, dict],
    deltas: list[dict] | None,
) -> None:
    """Accumulate streamed tool-call deltas by their OpenAI ``index``."""
    for delta in deltas or []:
        index = delta.get("index", 0)
        entry = tool_call_map.setdefault(index, {})
        _merge_delta(entry, delta)

        # Keep the standard shape predictable for downstream dispatch while
        # retaining any provider extension fields merged above.
        entry.setdefault("id", "")
        entry.setdefault("type", "function")
        function = entry.setdefault("function", {})
        function.setdefault("name", "")
        function.setdefault("arguments", "")
