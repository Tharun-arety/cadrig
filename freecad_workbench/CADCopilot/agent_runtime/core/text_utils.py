"""Text processing utilities."""
from __future__ import annotations

import re


def strip_markdown(text: str) -> str:
    """Remove Markdown code-fence markers that may wrap LLM output."""
    text = text.strip()
    text = re.sub(r"^```(?:python)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()
