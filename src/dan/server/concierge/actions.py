"""Post-LLM response actions: file delivery, message splitting, claim validation."""

from __future__ import annotations

import html
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WHATSAPP_CHAR_LIMIT = 4096

_MESSAGING_SURFACES = frozenset({"whatsapp", "whatsapp-web", "telegram", "email"})

# ---------------------------------------------------------------------------
# HTML stripping for messaging surfaces
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_TAGS = re.compile(
    r"<\s*/?\s*(?:div|p|br|h[1-6]|li|tr|blockquote|section|article|header|footer|hr|pre|table)\b[^>]*/?\s*>",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"<\s*h([1-6])\b[^>]*>(.*?)</\s*h\1\s*>", re.IGNORECASE | re.DOTALL)
_LIST_ITEM_RE = re.compile(r"<\s*li\b[^>]*>(.*?)</\s*li\s*>", re.IGNORECASE | re.DOTALL)
_LINK_RE = re.compile(r"<\s*a\b[^>]*href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</\s*a\s*>", re.IGNORECASE | re.DOTALL)
_CONSECUTIVE_BLANK_RE = re.compile(r"\n{3,}")
_HTML_DETECTION_RE = re.compile(r"<(?:div|span|p|br|h[1-6]|ul|ol|li|table|tr|td|th|a\s|img|section|article|header|footer|blockquote|pre|code|strong|em|b|i)\b", re.IGNORECASE)


def _looks_like_html(text: str) -> bool:
    """Quick heuristic: does the text contain likely HTML tags?"""
    return bool(_HTML_DETECTION_RE.search(text))


def strip_html_for_messaging(text: str) -> str:
    """Convert HTML-laden text to clean plain text for messaging surfaces.

    Preserves semantic structure: headings become *bold* lines, list items get
    bullet markers, links show their URL. Falls through to a no-op when the
    text doesn't look like HTML.
    """
    if not _looks_like_html(text):
        return text

    out = text
    # Convert headings to *bold* lines
    out = _HEADING_RE.sub(lambda m: f"\n\n*{m.group(2).strip()}*\n", out)
    # Convert list items to bullet points
    out = _LIST_ITEM_RE.sub(lambda m: f"\n• {m.group(1).strip()}", out)
    # Convert links: show text + URL
    out = _LINK_RE.sub(lambda m: f"{m.group(2).strip()} ({m.group(1)})" if m.group(2).strip() != m.group(1) else m.group(1), out)
    # Block tags → newlines
    out = _BLOCK_TAGS.sub("\n", out)
    # Strip remaining tags
    out = _HTML_TAG_RE.sub("", out)
    # Decode HTML entities
    out = html.unescape(out)
    # Collapse excessive blank lines
    out = _CONSECUTIVE_BLANK_RE.sub("\n\n", out)
    return out.strip()

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
