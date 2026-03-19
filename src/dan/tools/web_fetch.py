"""Built-in tool: fetch URL content via httpx."""

from __future__ import annotations

import asyncio
import copy
import html as html_mod
import os
import re
import threading
import time
from typing import Any

import httpx

from dan.server.search_models import canonicalize_search_url

_DEFAULT_CACHE_TTL_SECONDS = 900.0
_DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; deep-agent-network/0.1; +https://github.com/deep-agent-network)"
_FETCH_CACHE: dict[tuple[str, int, int], tuple[float, dict[str, Any]]] = {}
_FETCH_INFLIGHT: dict[tuple[str, int, int], asyncio.Task[dict[str, Any]]] = {}
_FETCH_STATE_LOCK = threading.Lock()

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
    "returns": "dict with url, content (plain text), status_code, content_type, and cache_hit",
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


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw.strip()))
    except (TypeError, ValueError):
        return default


def _fetch_cache_ttl_seconds() -> float:
    shared_default = _env_float("DAN_WEB_CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL_SECONDS)
    return _env_float("DAN_WEB_FETCH_CACHE_TTL_SECONDS", shared_default)


def _fetch_user_agent() -> str:
    return os.environ.get("DAN_WEB_FETCH_USER_AGENT", _DEFAULT_USER_AGENT).strip() or _DEFAULT_USER_AGENT


def _fetch_cache_key(url: str, timeout: int, max_length: int) -> tuple[str, int, int]:
    normalized_url = canonicalize_search_url(url) or url.strip()
    return normalized_url, int(timeout), int(max_length)


def _get_cached_fetch(key: tuple[str, int, int]) -> dict[str, Any] | None:
    now = time.monotonic()
    with _FETCH_STATE_LOCK:
        cached = _FETCH_CACHE.get(key)
        if cached is None:
            return None
        expires_at, payload = cached
        if expires_at <= now:
            _FETCH_CACHE.pop(key, None)
            return None

    result = copy.deepcopy(payload)
    result["cache_hit"] = True
    result["shared_inflight"] = False
    return result


def _store_cached_fetch(key: tuple[str, int, int], payload: dict[str, Any]) -> None:
    ttl_seconds = _fetch_cache_ttl_seconds()
    if ttl_seconds <= 0:
        return

    cached_payload = copy.deepcopy(payload)
    cached_payload["cache_hit"] = False
    cached_payload["shared_inflight"] = False
    expires_at = time.monotonic() + ttl_seconds
    with _FETCH_STATE_LOCK:
        _FETCH_CACHE[key] = (expires_at, cached_payload)


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


async def _execute_fetch(url: str, timeout: int, max_length: int) -> dict[str, Any]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": _fetch_user_agent()},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    content_type = resp.headers.get("content-type", "")
    raw = resp.text
    if "html" in content_type.lower():
        content = _html_to_text(raw)[:max_length]
    else:
        content = raw[:max_length]
    return {
        "url": str(resp.url),
        "content": content,
        "text": content,
        "status_code": resp.status_code,
        "content_type": content_type,
        "cache_hit": False,
        "shared_inflight": False,
        "cache_ttl_seconds": _fetch_cache_ttl_seconds(),
    }


async def web_fetch(
    url: str,
    timeout: int = 30,
    max_length: int = 100_000,
    **_kwargs,
) -> dict:
    key = _fetch_cache_key(url, timeout, max_length)
    cached = _get_cached_fetch(key)
    if cached is not None:
        return cached

    with _FETCH_STATE_LOCK:
        inflight = _FETCH_INFLIGHT.get(key)
        is_owner = inflight is None
        if inflight is None:
            inflight = asyncio.create_task(_execute_fetch(url.strip(), int(timeout), int(max_length)))
            _FETCH_INFLIGHT[key] = inflight

    try:
        result = await inflight
    finally:
        if is_owner:
            with _FETCH_STATE_LOCK:
                if _FETCH_INFLIGHT.get(key) is inflight:
                    _FETCH_INFLIGHT.pop(key, None)

    if is_owner:
        _store_cached_fetch(key, result)

    result_copy = copy.deepcopy(result)
    result_copy["cache_hit"] = False
    result_copy["shared_inflight"] = not is_owner
    return result_copy
