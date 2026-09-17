"""Connector for OpenAI-compatible chat-completions endpoints."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from cadcopilot.models.base import ModelClient, ModelError, ModelMessage


class OpenAICompatibleClient(ModelClient):
    """Use a local or hosted model exposing ``/chat/completions``."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 60,
        response_format: str = "json_schema",
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be empty")
        if not model.strip():
            raise ValueError("model must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if response_format not in {"json_schema", "json_object", "none"}:
            raise ValueError("response_format must be json_schema, json_object or none")
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._response_format = response_format

    def complete(
        self,
        messages: Sequence[ModelMessage],
        *,
        response_schema: Mapping[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [message.to_dict() for message in messages],
            "temperature": 0,
        }
        if response_schema is not None and self._response_format == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "cad_action_plan",
                    "strict": True,
                    "schema": dict(response_schema),
                },
            }
        elif response_schema is not None and self._response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise ModelError(f"model endpoint returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ModelError(f"model endpoint request failed: {exc}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelError("model endpoint returned invalid JSON") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelError("model response has no choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise ModelError("model response content is empty")
        return content
