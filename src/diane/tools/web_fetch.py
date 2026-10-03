"""Built-in tool: fetch URL content via httpx."""

from __future__ import annotations

import asyncio
import copy
import html as html_mod
import logging
import os
import re
import threading
import time
from typing import Any

import httpx

from diane.server.search_models import canonicalize_search_url

_DEFAULT_CACHE_TTL_SECONDS = 900.0
_DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; deep-agent-network/0.1; +https://github.com/deep-agent-network)"
_FETCH_CACHE: dict[tuple[str, int, int, int], tuple[float, dict[str, Any]]] = {}
_FETCH_INFLIGHT: dict[tuple[str, int, int, int], asyncio.Task[dict[str, Any]]] = {}
_FETCH_STATE_LOCK = threading.Lock()
logger = logging.getLogger(__name__)

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
            "browser_fallback": {
                "type": "boolean",
                "description": (
                    "If true, retry through the persistent Playwright browser when a plain HTTP "
                    "fetch fails or returns a JavaScript shell / browser-gated page."
                ),
                "default": False,
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
    "returns": (
        "dict with url, content (plain text), status_code, content_type, cache_hit, "
        "fetch_via, and browser_fallback_used"
    ),
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
_BROWSER_SHELL_MARKERS = (
    "enable javascript",
    "javascript is required",
    "javascript required",
    "requires javascript",
    "please enable cookies",
    "checking if the site connection is secure",
    "just a moment",
    "browser is not supported",
)


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


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _browser_fallback_default() -> bool:
    return _env_bool("DAN_WEB_BROWSER_FALLBACK", False)


def _fetch_total_timeout_seconds(request_timeout: int) -> float:
    configured = _env_float("DAN_WEB_FETCH_TOTAL_TIMEOUT_SECONDS", 0.0)
    if configured > 0:
        return configured
    request_timeout = max(1, int(request_timeout))
    return max(float(request_timeout) + 5.0, float(request_timeout) * 1.5)


def _fetch_cache_key(
    url: str,
    timeout: int,
    max_length: int,
    browser_fallback: bool,
) -> tuple[str, int, int, int]:
    normalized_url = canonicalize_search_url(url) or url.strip()
    return normalized_url, int(timeout), int(max_length), int(bool(browser_fallback))


def _get_cached_fetch(key: tuple[str, int, int, int]) -> dict[str, Any] | None:
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


def _store_cached_fetch(key: tuple[str, int, int, int], payload: dict[str, Any]) -> None:
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


def _looks_like_browser_shell(content: str, content_type: str) -> bool:
    normalized = " ".join(str(content or "").lower().split())
    if not normalized:
        return True
    if "html" not in str(content_type or "").lower():
        return False
    if len(normalized) > 500:
        return False
    return any(marker in normalized for marker in _BROWSER_SHELL_MARKERS)


async def _try_browser_fetch(
    url: str,
    *,
    timeout: int,
    max_length: int,
    fallback_reason: str,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        from diane.tools._browser_session import get_controller

        ctrl = await get_controller()
        opened = await ctrl.open(url)
        try:
            await ctrl.wait_for(timeout=min(float(timeout), 10.0))
        except Exception:
            logger.debug("Browser wait_for() failed during browser-backed fetch", exc_info=True)
        text = str(await ctrl.extract_text() or "").strip()
        if not text:
            return None, "Browser-rendered page returned no usable text."
        final_url = str(opened.get("url", "") or url).strip() or url
        title = str(opened.get("title", "") or "").strip()
        content = text[:max_length]
        payload = {
            "url": final_url,
            "content": content,
            "text": content,
            "status_code": 0,
            "content_type": "text/html; browser-rendered",
            "cache_hit": False,
            "shared_inflight": False,
            "cache_ttl_seconds": _fetch_cache_ttl_seconds(),
            "fetch_via": "browser",
            "browser_fallback_used": True,
            "browser_fallback_attempted": True,
            "browser_fallback_reason": fallback_reason,
            "browser_title": title,
            "content_requires_browser": False,
        }
        return payload, None
    except Exception as exc:
        return None, str(exc).strip() or type(exc).__name__


async def _execute_fetch(
    url: str,
    timeout: int,
    max_length: int,
    browser_fallback: bool,
) -> dict[str, Any]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": _fetch_user_agent()},
    ) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            if browser_fallback:
                browser_payload, browser_error = await _try_browser_fetch(
                    url,
                    timeout=timeout,
                    max_length=max_length,
                    fallback_reason="http_fetch_failed",
                )
                if browser_payload is not None:
                    browser_payload["http_error"] = str(exc).strip() or type(exc).__name__
                    return browser_payload
                if browser_error:
                    logger.debug("Browser-backed fetch fallback failed: %s", browser_error)
            raise

    content_type = resp.headers.get("content-type", "")
    raw = resp.text
    if "html" in content_type.lower():
        content = _html_to_text(raw)[:max_length]
    else:
        content = raw[:max_length]

    payload = {
        "url": str(resp.url),
        "content": content,
        "text": content,
        "status_code": resp.status_code,
        "content_type": content_type,
        "cache_hit": False,
        "shared_inflight": False,
        "cache_ttl_seconds": _fetch_cache_ttl_seconds(),
        "fetch_via": "http",
        "browser_fallback_used": False,
        "browser_fallback_attempted": False,
        "content_requires_browser": _looks_like_browser_shell(content, content_type),
    }
    if browser_fallback and payload["content_requires_browser"]:
        browser_payload, browser_error = await _try_browser_fetch(
            str(resp.url),
            timeout=timeout,
            max_length=max_length,
            fallback_reason="http_fetch_unusable",
        )
        if browser_payload is not None:
            browser_payload["http_status_code"] = resp.status_code
            return browser_payload
        payload["browser_fallback_attempted"] = True
        if browser_error:
            payload["browser_fallback_error"] = browser_error
    return payload


async def web_fetch(
    url: str,
    timeout: int = 30,
    max_length: int = 100_000,
    browser_fallback: bool | None = None,
    **_kwargs,
) -> dict:
    fallback_enabled = _browser_fallback_default() if browser_fallback is None else bool(browser_fallback)
    key = _fetch_cache_key(url, timeout, max_length, fallback_enabled)
    cached = _get_cached_fetch(key)
    if cached is not None:
        return cached

    with _FETCH_STATE_LOCK:
        inflight = _FETCH_INFLIGHT.get(key)
        is_owner = inflight is None
        if inflight is None:
            inflight = asyncio.create_task(
                _execute_fetch(
                    url.strip(),
                    int(timeout),
                    int(max_length),
                    fallback_enabled,
                )
            )
            _FETCH_INFLIGHT[key] = inflight

    try:
        total_timeout = _fetch_total_timeout_seconds(int(timeout))
        result = await asyncio.wait_for(
            inflight if is_owner else asyncio.shield(inflight),
            timeout=total_timeout,
        )
    except asyncio.TimeoutError as exc:
        if is_owner and not inflight.done():
            inflight.cancel()
            with _FETCH_STATE_LOCK:
                if _FETCH_INFLIGHT.get(key) is inflight:
                    _FETCH_INFLIGHT.pop(key, None)
        raise TimeoutError(
            f"web_fetch timed out after {total_timeout:.1f}s for {url.strip()}"
        ) from exc
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
