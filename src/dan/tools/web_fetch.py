"""Built-in tool: fetch URL content via httpx."""

from __future__ import annotations

import html as html_mod
import re

import httpx

TOOL_METADATA = {
    "tool_id": "web_fetch",
    "description": (
        "Fetch the content of a URL and return it as readable text. "
        "HTML pages are automatically converted to plain text. "
        "Useful for retrieving web pages, API responses, or raw data files. "
        "Content is truncated to max_length characters to prevent memory issues."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch.",
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds.",
                "default": 30,
            },
            "max_length": {
                "type": "integer",
                "description": "Maximum characters to return from the response body.",
                "default": 100000,
            },
        },
        "required": ["url"],
    },
    "examples": [
        {
            "input": {"url": "https://example.com"},
            "output": {
                "url": "https://example.com",
                "content": "Example Domain\n\nThis domain is for use in illustrative examples...",
                "status_code": 200,
                "content_type": "text/html",
            },
        },
    ],
    "category": "web",
    "returns": "dict with url, content (plain text), status_code, and content_type",
}

_SCRIPT_STYLE_RE = re.compile(
    r"<\s*(?:script|style|noscript)\b[^>]*>.*?</\s*(?:script|style|noscript)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HEADING_RE = re.compile(r"<\s*h([1-6])\b[^>]*>(.*?)</\s*h\1\s*>", re.IGNORECASE | re.DOTALL)
_LIST_ITEM_RE = re.compile(r"<\s*li\b[^>]*>(.*?)</\s*li\s*>", re.IGNORECASE | re.DOTALL)
_LINK_RE = re.compile(
    r"<\s*a\b[^>]*href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</\s*a\s*>",
    re.IGNORECASE | re.DOTALL,
)
_BLOCK_TAG_RE = re.compile(
    r"<\s*/?\s*(?:div|p|br|h[1-6]|li|tr|blockquote|section|article|header|footer|hr|pre|table|thead|tbody|dd|dt)\b[^>]*/?\s*>",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def _html_to_text(raw_html: str) -> str:
    """Best-effort HTML to plain text without external dependencies."""
    text = _SCRIPT_STYLE_RE.sub("", raw_html)
    text = _HEADING_RE.sub(lambda m: f"\n\n{m.group(2).strip()}\n", text)
    text = _LIST_ITEM_RE.sub(lambda m: f"\n• {m.group(1).strip()}", text)
    text = _LINK_RE.sub(
        lambda m: (
            f"{m.group(2).strip()} ({m.group(1)})"
            if m.group(2).strip() and m.group(2).strip() != m.group(1)
            else m.group(1)
        ),
        text,
    )
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html_mod.unescape(text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


async def web_fetch(
    url: str,
    timeout: int = 30,
    max_length: int = 100_000,
    **_kwargs,
) -> dict:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        resp = await client.get(url)

    content_type = resp.headers.get("content-type", "")
    raw = resp.text
    if "html" in content_type.lower():
        content = _html_to_text(raw)[:max_length]
    else:
        content = raw[:max_length]
    return {
        "url": str(resp.url),
        "content": content,
        "status_code": resp.status_code,
        "content_type": content_type,
    }
