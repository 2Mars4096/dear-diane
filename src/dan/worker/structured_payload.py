"""Helpers for tolerant parsing of structured worker/model payloads."""

from __future__ import annotations

import json
from typing import Any


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines:
        return stripped
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_jsonish_payload(raw: Any) -> Any:
    """Parse JSON-like model output, tolerating code fences and leading prose."""

    if isinstance(raw, (dict, list, int, float, bool)) or raw is None:
        return raw

    text = str(raw or "").strip()
    if not text:
        return ""

    candidates = [text]
    unfenced = _strip_markdown_fence(text)
    if unfenced and unfenced != text:
        candidates.append(unfenced)

    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            pass
        for opener in ("{", "["):
            start = candidate.find(opener)
            if start < 0:
                continue
            try:
                parsed, _end = decoder.raw_decode(candidate[start:])
            except Exception:
                continue
            return parsed

    return text


__all__ = ["parse_jsonish_payload"]
