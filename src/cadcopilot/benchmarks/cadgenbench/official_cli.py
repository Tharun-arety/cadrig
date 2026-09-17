"""Run the upstream CLI with narrow, versioned compatibility patches applied."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from cadgenbench.baseline import agent
from cadgenbench.baseline.llm import LLMClient
from cadgenbench.baseline.types import AgentResult
from cadgenbench.cli import main

from .common import read_json, write_json_atomic
from .compat import completion_token_allowance, extract_code_blocks_tolerant

_strict_extract_code_blocks = agent.extract_code_blocks
_strict_agent_result_save = AgentResult.save
_strict_llm_complete = LLMClient.complete
_recovered_code_hashes: set[str] = set()
_TOKEN_CAP_ENV = "CADCOPILOT_ATTEMPT_TOKEN_CAP"
_USAGE_LEDGER_ENV = "CADCOPILOT_PROVIDER_USAGE_PATH"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _extract_code_blocks(text: str, lang: str = "python") -> list[str]:
    strict = _strict_extract_code_blocks(text, lang)
    recovered = extract_code_blocks_tolerant(text, lang, _strict_extract_code_blocks)
    if recovered != strict:
        _recovered_code_hashes.update(_sha256_text(code) for code in recovered)
    return recovered


def _save_with_trace(self: AgentResult, output_dir: str | Path) -> Path:
    destination = _strict_agent_result_save(self, output_dir)
    turns: list[dict[str, Any]] = []
    for record in self.turns:
        executions = []
        for execution in record.code_executions:
            code_hash = _sha256_text(execution.code)
            executions.append(
                {
                    "code_sha256": code_hash,
                    "recovered_unterminated_fence": code_hash in _recovered_code_hashes,
                    "success": execution.success,
                    "duration_seconds": round(execution.duration_s, 3),
                    "files_produced": dict(sorted(execution.files_produced.items())),
                    "stdout_bytes": len(execution.stdout.encode("utf-8")),
                    "stderr_bytes": len(execution.stderr.encode("utf-8")),
                }
            )
        turns.append(
            {
                "turn": record.turn,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "reasoning_tokens": record.reasoning_tokens,
                "total_tokens": record.prompt_tokens + record.completion_tokens,
                "duration_seconds": round(record.duration_s, 3),
                "assistant_message_sha256": _sha256_text(record.assistant_message),
                "executions": executions,
            }
        )
    write_json_atomic(
        destination / "trace.json",
        {
            "schema_version": "1.0.0",
            "total_tokens": self.total_tokens,
            "total_duration_seconds": round(self.total_duration_s, 3),
            "completed": self.completed,
            "stopped_reason": self.stopped_reason,
            "turns": turns,
        },
    )
    return destination


def _usage_value(completion: Any, name: str) -> int:
    value = getattr(completion, name, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _record_provider_usage(completion: Any, *, accepted: bool) -> None:
    raw_path = os.environ.get(_USAGE_LEDGER_ENV)
    if raw_path is None:
        return
    path = Path(raw_path).resolve()
    payload = read_json(path) or {"schema_version": "1.0.0", "calls": []}
    calls = payload.get("calls")
    if not isinstance(calls, list):
        calls = []
    total = _usage_value(completion, "total_tokens")
    prompt = _usage_value(completion, "prompt_tokens")
    completion_tokens = _usage_value(completion, "completion_tokens")
    calls.append(
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion_tokens,
            "unclassified_tokens": max(total - prompt - completion_tokens, 0),
            "total_tokens": total,
            "accepted_by_attempt_cap": accepted,
        }
    )
    payload["calls"] = calls
    payload["prompt_tokens"] = sum(_usage_value_from_call(call, "prompt_tokens") for call in calls)
    payload["completion_tokens"] = sum(
        _usage_value_from_call(call, "completion_tokens") for call in calls
    )
    payload["unclassified_tokens"] = sum(
        _usage_value_from_call(call, "unclassified_tokens") for call in calls
    )
    payload["total_tokens"] = sum(_usage_value_from_call(call, "total_tokens") for call in calls)
    payload["call_count"] = len(calls)
    payload["rejected_call_count"] = sum(
        isinstance(call, dict) and call.get("accepted_by_attempt_cap") is False for call in calls
    )
    write_json_atomic(path, payload)


def _usage_value_from_call(call: object, name: str) -> int:
    if not isinstance(call, dict):
        return 0
    value = call.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _complete_with_cap(
    self: LLMClient, messages: list[dict[str, Any]], **kwargs: Any
) -> Any:
    raw_cap = os.environ.get(_TOKEN_CAP_ENV)
    if raw_cap is None:
        return _strict_llm_complete(self, messages, **kwargs)
    try:
        token_cap = int(raw_cap)
        consumed = int(getattr(self, "_cadcopilot_consumed_tokens", 0))
        prompt_tokens = self.count_tokens(messages)
        requested = int(kwargs.get("max_tokens", 0))
        allowance = completion_token_allowance(
            token_cap=token_cap,
            consumed=consumed,
            prompt_tokens=prompt_tokens,
            requested=requested,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"invalid {_TOKEN_CAP_ENV} budget configuration") from exc
    if allowance <= 0:
        raise RuntimeError(
            f"attempt token cap exhausted before model call ({consumed}+{prompt_tokens} "
            f">= {token_cap})"
        )
    kwargs["max_tokens"] = allowance
    completion = _strict_llm_complete(self, messages, **kwargs)
    new_total = consumed + completion.total_tokens
    self._cadcopilot_consumed_tokens = new_total
    _record_provider_usage(completion, accepted=new_total <= token_cap)
    if new_total > token_cap:
        raise RuntimeError(
            f"provider-reported usage exceeded attempt token cap ({new_total} > {token_cap})"
        )
    return completion


agent.extract_code_blocks = _extract_code_blocks
AgentResult.save = _save_with_trace
LLMClient.complete = _complete_with_cap


if __name__ == "__main__":
    raise SystemExit(main())
