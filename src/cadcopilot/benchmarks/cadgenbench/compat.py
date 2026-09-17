"""Small compatibility fixes around the upstream CADGenBench baseline."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable

StrictExtractor = Callable[[str, str], list[str]]


def completion_token_allowance(
    *, token_cap: int, consumed: int, prompt_tokens: int, requested: int
) -> int:
    """Return the largest completion allowance that fits a reported-token cap."""
    if token_cap <= 0 or consumed < 0 or prompt_tokens < 0 or requested <= 0:
        raise ValueError("token budget inputs must be positive and usage non-negative")
    return max(0, min(requested, token_cap - consumed - prompt_tokens))


def extract_code_blocks_tolerant(
    text: str,
    lang: str,
    strict_extractor: StrictExtractor,
) -> list[str]:
    """Recover a final code block when a model response ends before its fence closes."""
    blocks = strict_extractor(text, lang)
    opening = re.compile(rf"```{re.escape(lang)}[ \t]*\r?\n", re.IGNORECASE)
    matches = list(opening.finditer(text))
    if not matches:
        return blocks

    tail = text[matches[-1].end() :]
    if "```" in tail:
        return blocks

    code = tail.strip()
    if not code:
        return blocks
    if lang.casefold() == "python":
        try:
            ast.parse(code)
        except SyntaxError:
            return blocks
    return [code]
