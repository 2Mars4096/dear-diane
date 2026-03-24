"""Pure usage normalization and mutation-plan JSON parsing helpers."""

from __future__ import annotations

import json
import re
from typing import Any

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def _normalize_usage(raw: dict[str, int] | None) -> dict[str, int]:
    """Normalize provider usage dicts to both UI and telemetry keys."""
    if not raw:
        return {}
    prompt = int(raw.get("prompt", 0) or raw.get("prompt_tokens", 0) or 0)
    completion = int(
        raw.get("completion", 0) or raw.get("completion_tokens", 0) or 0
    )
    total = int(raw.get("total_tokens", 0) or (prompt + completion))
    cached_input_tokens = int(raw.get("cached_input_tokens", 0) or 0)
    cache_write_tokens = int(raw.get("cache_write_tokens", 0) or 0)
    return {
        "prompt": prompt,
        "completion": completion,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_tokens": cache_write_tokens,
    }


def _try_parse_mutation_json(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a mutation plan from freeform LLM text."""
    for match in _JSON_BLOCK_RE.finditer(text):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict) and isinstance(data.get("operations"), list):
                return data
        except (json.JSONDecodeError, KeyError):
            continue
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict) and isinstance(data.get("operations"), list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None
