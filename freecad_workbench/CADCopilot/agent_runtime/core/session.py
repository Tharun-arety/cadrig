"""
Session manager — manages conversation history for multi-turn agent interactions.

Three-layer memory:
  - Short-term: messages[] during a single agent run
  - Mid-term: ChatSession persists across multiple user inputs within one FreeCAD session
  - Long-term: session_store.py saves to disk for cross-session persistence (Phase 2)
"""
from __future__ import annotations

import uuid
from datetime import datetime


class ChatSession:
    """One complete design session containing multiple agent interactions."""

    SESSION_VERSION = 1  # Bump when serialization format changes

    def __init__(self):
        self.session_id = uuid.uuid4().hex[:12]
        self.created_at = datetime.now().isoformat()
        self.messages: list[dict] = []
        self.summary: str = ""
        self.document_state: str = ""
        self.last_mode: str = "auto"
        self.parameters: dict = {}
        self.parametric_code: str = ""
        self.persistent_vars: dict = {}
        self._system_prompt: str = ""
        self._version: int = self.SESSION_VERSION

    def set_system_prompt(self, prompt: str):
        """Set the system prompt for the first run or a new session."""
        self._system_prompt = prompt
        # Replace an existing system message; otherwise insert one.
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0] = {"role": "system", "content": prompt}
        else:
            self.messages.insert(0, {"role": "system", "content": prompt})

    def add_user_message(self, text: str):
        """Add a user message."""
        self.messages.append({"role": "user", "content": text})

    def add_assistant_message(self, msg: dict):
        """Add an assistant message, optionally containing tool calls."""
        # Ensure role is set
        entry = dict(msg)
        entry["role"] = "assistant"
        self.messages.append(entry)

    def add_tool_result(self, tool_call_id: str, result: str, tool_name: str = ""):
        """Add a tool execution result."""
        message = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        }
        if tool_name:
            message["name"] = tool_name
        self.messages.append(message)

    def get_messages(self) -> list[dict]:
        """Return the complete conversation history."""
        return list(self.messages)

    def get_last_assistant_text(self) -> str:
        """Return the latest assistant text for display in the UI."""
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                return msg["content"]
        return ""

    def update_summary(self, text: str):
        """Update the session summary after an agent run."""
        self.summary = text

    def update_document_state(self, state: str):
        """Update the document-state snapshot."""
        self.document_state = state

    def message_count(self) -> int:
        """Return the total message count."""
        return len(self.messages)

    def user_turn_count(self) -> int:
        """Return the number of user messages."""
        return sum(1 for m in self.messages if m.get("role") == "user")

    def clear(self):
        """Clear the session while preserving the system prompt."""
        system = self._system_prompt
        self.messages.clear()
        self.summary = ""
        self.document_state = ""
        self.parameters = {}
        self.parametric_code = ""
        self.persistent_vars = {}
        if system:
            self.messages.append({"role": "system", "content": system})

    def to_dict(self) -> dict:
        """Serialize the session for persistence."""
        return {
            "version": self._version,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "summary": self.summary,
            "document_state": self.document_state,
            "last_mode": self.last_mode,
            "messages": self.messages,
            "parameters": self.parameters,
            "parametric_code": self.parametric_code,
            "persistent_vars": self.persistent_vars,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ChatSession:
        """Deserialize a session from a dictionary."""
        session = cls.__new__(cls)
        session._version = data.get("version", 0)  # 0 = legacy format
        session.session_id = data.get("session_id", uuid.uuid4().hex[:12])
        session.created_at = data.get("created_at", datetime.now().isoformat())
        session.messages = data.get("messages", [])
        session.summary = data.get("summary", "")
        session.document_state = data.get("document_state", "")
        session.last_mode = data.get("last_mode", "auto")
        session.parameters = data.get("parameters", {})
        session.parametric_code = data.get("parametric_code", "")
        session.persistent_vars = data.get("persistent_vars", {})
        session._system_prompt = session.messages[0].get("content", "") if session.messages else ""
        return session
