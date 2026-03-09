"""Post-LLM response actions: file delivery, message splitting, claim validation."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WHATSAPP_CHAR_LIMIT = 4096

_MESSAGING_SURFACES = frozenset({"whatsapp", "whatsapp-web", "telegram", "email"})

_NUMERIC_CLAIM_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d+(?:\.\d+)?%"
    r"|\b(?:price|close|open|high|low|volume|cap)\b[^.]*?\$?\d",
    re.IGNORECASE,
)

_DISCLAIMER = (
    "\n\n_Note: This response may contain data from my training "
    "rather than a live source. For current prices or stats, "
    "ask me to search the web._"
)

_unsourced_claim_warnings: int = 0


def extract_file_paths_from_tool_results(tool_results: list[dict[str, Any]]) -> list[str]:
    """Scan tool-call result ``data`` dicts for file paths that exist on disk."""
    paths: list[str] = []
    seen: set[str] = set()
    for result in tool_results:
        data = result.get("data")
        if not isinstance(data, dict):
            continue
        path = data.get("path", "")
        if path and path not in seen and Path(path).is_file():
            paths.append(path)
            seen.add(path)
    return paths


def split_message_for_surface(
    text: str,
    surface: str,
    limit: int = _WHATSAPP_CHAR_LIMIT,
) -> list[str]:
    """Split a long message into parts for messaging surfaces.

    Non-messaging surfaces (editor, CLI, server) are returned as-is.
    Splitting prefers paragraph boundaries, falling back to line boundaries.
    """
    if surface not in _MESSAGING_SURFACES:
        return [text]
    if len(text) <= limit:
        return [text]

    paragraphs = text.split("\n\n")
    parts: list[str] = []
    current = ""
    for para in paragraphs:
        if current and len(current) + len(para) + 2 > limit:
            parts.append(current.strip())
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        parts.append(current.strip())

    final: list[str] = []
    for part in parts:
        if len(part) <= limit:
            final.append(part)
        else:
            lines = part.split("\n")
            chunk = ""
            for line in lines:
                if chunk and len(chunk) + len(line) + 1 > limit:
                    final.append(chunk.strip())
                    chunk = line
                else:
                    chunk = f"{chunk}\n{line}" if chunk else line
            if chunk.strip():
                final.append(chunk.strip())

    # Hard character-level split for parts that still exceed the limit
    # (e.g. a single long line with no whitespace boundaries).
    result: list[str] = []
    for part in (final or [text]):
        while len(part) > limit:
            result.append(part[:limit])
            part = part[limit:]
        if part:
            result.append(part)

    return result if result else [text]


def check_unsourced_claims(text: str, tool_calls_made: list[str]) -> str | None:
    """Return a disclaimer suffix if numeric claims found without web_search backing.

    Returns ``None`` when no disclaimer is needed (web_search was called, or no
    numeric claims detected).
    """
    global _unsourced_claim_warnings
    if not text:
        return None
    if "web_search" in tool_calls_made:
        return None
    if not _NUMERIC_CLAIM_RE.search(text):
        return None
    _unsourced_claim_warnings += 1
    logger.debug(
        "Unsourced numeric claim detected (total warnings: %d)",
        _unsourced_claim_warnings,
    )
    return _DISCLAIMER
