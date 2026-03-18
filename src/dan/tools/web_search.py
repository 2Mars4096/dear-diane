"""Built-in tool: web search with provider cascade (Tavily -> Brave -> DuckDuckGo)."""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_TTL_SECONDS = 900.0
_SEARCH_CACHE: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_SEARCH_INFLIGHT: dict[tuple[str, int], asyncio.Task[dict[str, Any]]] = {}
_SEARCH_STATE_LOCK = threading.Lock()

TOOL_METADATA = {
    "tool_id": "web_search",
    "description": (
        "Search the web for current information. Provider cascade: "
        "Tavily (DAN_TAVILY_API_KEY), Brave (DAN_BRAVE_API_KEY), "
        "DuckDuckGo (no key, least reliable). Uses the first available provider."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query string.",
            },
            "num_results": {
                "type": "integer",
                "description": "Maximum number of results to return.",
                "default": 5,
            },
        },
        "required": ["query"],
    },
    "examples": [
        {
            "input": {"query": "python asyncio tutorial", "num_results": 3},
            "output": {
                "results": [
                    {"title": "AsyncIO in Python", "url": "https://example.com", "snippet": "A tutorial..."}
                ],
                "count": 1,
            },
        },
    ],
    "category": "web",
    "returns": (
        "dict with results (list of {title, url, snippet}), count, provider, "
        "cache_hit, and provider_failures"
    ),
}


class WebSearchProvidersExhaustedError(RuntimeError):
    """Raised when every configured web-search provider fails."""

    def __init__(self, provider_failures: list[dict[str, Any]], last_error: Exception):
        self.provider_failures = copy.deepcopy(provider_failures)
        self.last_error = last_error
        self.retryable = any(bool(item.get("retryable")) for item in provider_failures)
        self.error_type = (
            "provider_unavailable"
            if provider_failures
            and all(str(item.get("error_type", "")) == "provider_unavailable" for item in provider_failures)
            else "provider_error"
        )

        last_provider = "provider"
        summary = str(last_error).strip() or type(last_error).__name__
        if provider_failures:
            last_provider = str(provider_failures[-1].get("provider", last_provider))
            summary = (
                str(provider_failures[-1].get("summary", "") or "").strip()
                or str(provider_failures[-1].get("message", "") or "").strip()
                or summary
            )
        super().__init__(f"All web search providers failed. Last error from {last_provider}: {summary}")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw.strip()))
    except (TypeError, ValueError):
        return default


def _search_cache_ttl_seconds() -> float:
    shared_default = _env_float("DAN_WEB_CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL_SECONDS)
    return _env_float("DAN_WEB_SEARCH_CACHE_TTL_SECONDS", shared_default)


def _trim_message(text: str, limit: int = 240) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _summarize_exception(exc: Exception) -> tuple[str, str, bool, int | None]:
    if isinstance(exc, ImportError):
        message = _trim_message(str(exc) or "Provider is unavailable.")
        return "provider_unavailable", message, False, None

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        reason = exc.response.reason_phrase if exc.response is not None else ""
        summary = f"HTTP {status}" if status is not None else "HTTP error"
        if reason:
            summary += f" {reason}"
        retryable = status == 429 or (status is not None and status >= 500)
        return "provider_error", summary, retryable, status

    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            TimeoutError,
        ),
    ):
        return "network_error", "timeout", True, None

    if isinstance(exc, (httpx.RequestError, ConnectionError, OSError)):
        return "network_error", _trim_message(str(exc) or type(exc).__name__), True, None

    message = _trim_message(str(exc) or type(exc).__name__)
    lowered = message.lower()
    retryable = any(
        token in lowered
        for token in ("rate limit", "temporar", "service unavailable", "timed out", "network")
    )
    return ("provider_error" if retryable else "internal_exception"), message, retryable, None


def _provider_failure(provider: str, exc: Exception) -> dict[str, Any]:
    error_type, summary, retryable, status_code = _summarize_exception(exc)
    return {
        "provider": provider,
        "error_type": error_type,
        "retryable": retryable,
        "status_code": status_code,
        "summary": summary,
        "message": _trim_message(str(exc) or type(exc).__name__),
    }


def _search_cache_key(query: str, num_results: int) -> tuple[str, int]:
    return query.strip(), int(num_results)


def _get_cached_result(key: tuple[str, int]) -> dict[str, Any] | None:
    now = time.monotonic()
    with _SEARCH_STATE_LOCK:
        cached = _SEARCH_CACHE.get(key)
        if cached is None:
            return None
        expires_at, payload = cached
        if expires_at <= now:
            _SEARCH_CACHE.pop(key, None)
            return None

    result = copy.deepcopy(payload)
    result["cache_hit"] = True
    result["shared_inflight"] = False
    return result


def _store_cached_result(key: tuple[str, int], payload: dict[str, Any]) -> None:
    ttl_seconds = _search_cache_ttl_seconds()
    if ttl_seconds <= 0:
        return

    cached_payload = copy.deepcopy(payload)
    cached_payload["cache_hit"] = False
    cached_payload["shared_inflight"] = False
    expires_at = time.monotonic() + ttl_seconds
    with _SEARCH_STATE_LOCK:
        _SEARCH_CACHE[key] = (expires_at, cached_payload)


def _finalize_search_result(payload: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(payload)
    payload.setdefault("provider_failures", [])
    payload["cache_hit"] = False
    payload["shared_inflight"] = False
    payload["cache_ttl_seconds"] = _search_cache_ttl_seconds()
    return payload


async def _try_search_provider(
    provider: str,
    fn,
    *args,
    provider_failures: list[dict[str, Any]],
) -> dict[str, Any] | None:
    try:
        return await fn(*args)
    except Exception as exc:
        provider_failures.append(_provider_failure(provider, exc))
        return None


async def _tavily_search(query: str, num_results: int, api_key: str) -> dict:
    """Search via Tavily API (https://api.tavily.com)."""
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "max_results": min(num_results, 20),
                "search_depth": "basic",
                "topic": "general",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for r in (data.get("results") or [])[:num_results]:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
        })

    return {"results": results, "count": len(results), "provider": "tavily"}


async def _brave_search(query: str, num_results: int, api_key: str) -> dict:
    """Search via Brave Search API (https://api.search.brave.com)."""
    import httpx

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {"q": query, "count": min(num_results, 20)}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

    results = []
    for r in (data.get("web", {}).get("results") or [])[:num_results]:
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", ""),
        })

    return {"results": results, "count": len(results), "provider": "brave"}


async def _ddg_search(query: str, num_results: int) -> dict:
    """Fallback search via DuckDuckGo scraping."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        raise ImportError(
            "web_search requires one of: DAN_TAVILY_API_KEY (recommended), "
            "DAN_BRAVE_API_KEY, or the 'duckduckgo-search' package. "
            "Install with: pip install 'duckduckgo-search>=6.0'"
        )

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=num_results):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            })

    return {"results": results, "count": len(results), "provider": "duckduckgo"}


async def _execute_search(query: str, num_results: int) -> dict[str, Any]:
    tavily_key = os.environ.get("DAN_TAVILY_API_KEY", "").strip()
    brave_key = os.environ.get("DAN_BRAVE_API_KEY", "").strip()
    provider_failures: list[dict[str, Any]] = []

    if tavily_key:
        tavily_result = await _try_search_provider(
            "tavily",
            _tavily_search,
            query,
            num_results,
            tavily_key,
            provider_failures=provider_failures,
        )
        if tavily_result is not None:
            tavily_result["provider_failures"] = provider_failures
            return _finalize_search_result(tavily_result)
        logger.warning(
            "Tavily search failed (%s), trying next provider",
            provider_failures[-1]["summary"],
        )

    if brave_key:
        brave_result = await _try_search_provider(
            "brave",
            _brave_search,
            query,
            num_results,
            brave_key,
            provider_failures=provider_failures,
        )
        if brave_result is not None:
            brave_result["provider_failures"] = provider_failures
            return _finalize_search_result(brave_result)
        logger.warning(
            "Brave search failed (%s), falling back to DuckDuckGo",
            provider_failures[-1]["summary"],
        )

    try:
        ddg_result = await _ddg_search(query, num_results)
    except Exception as exc:
        provider_failures.append(_provider_failure("duckduckgo", exc))
        if isinstance(exc, ImportError) and len(provider_failures) == 1:
            raise
        raise WebSearchProvidersExhaustedError(provider_failures, exc) from exc

    ddg_result["provider_failures"] = provider_failures
    return _finalize_search_result(ddg_result)


async def web_search(query: str, num_results: int = 5, **_kwargs) -> dict:
    key = _search_cache_key(query, num_results)
    cached = _get_cached_result(key)
    if cached is not None:
        return cached

    with _SEARCH_STATE_LOCK:
        inflight = _SEARCH_INFLIGHT.get(key)
        is_owner = inflight is None
        if inflight is None:
            inflight = asyncio.create_task(_execute_search(query.strip(), int(num_results)))
            _SEARCH_INFLIGHT[key] = inflight

    try:
        result = await inflight
    finally:
        if is_owner:
            with _SEARCH_STATE_LOCK:
                if _SEARCH_INFLIGHT.get(key) is inflight:
                    _SEARCH_INFLIGHT.pop(key, None)

    if is_owner:
        _store_cached_result(key, result)

    result_copy = copy.deepcopy(result)
    result_copy["cache_hit"] = False
    result_copy["shared_inflight"] = not is_owner
    return result_copy
