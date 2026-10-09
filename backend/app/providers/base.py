"""Provider abstraction + JSON extraction helpers shared by inference providers."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from ..schemas import ModelRun


class LLMProvider:
    """Common interface. Subclasses implement `_complete`."""

    name: str = "base"
    label: str = "base"
    model: str = ""
    enabled: bool = False

    async def run(
        self,
        *,
        system: str,
        user: str,
        image_data_uri: Optional[str] = None,
        reasoning_effort: str = "default",
        agent: str = "vision",
    ) -> ModelRun:
        raise NotImplementedError


def extract_json(text: str) -> Optional[dict[str, Any]]:
    """Best-effort JSON extraction from a model response.

    Models often wrap JSON in ```json fences or prose. We try, in order:
    fenced block, first balanced {...}, then the raw string.
    """
    if not text:
        return None

    # 1. fenced ```json ... ```
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = []
    if fenced:
        candidates.append(fenced.group(1))

    # 2. first balanced object
    start = text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : i + 1])
                    break

    # 3. raw
    candidates.append(text.strip())

    for c in candidates:
        try:
            return json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
    return None
