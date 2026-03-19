"""Built-in tool: web search with provider cascade and health tracking."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import httpx

from dan.server.search_models import canonicalize_search_url, domain_from_url

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_TTL_SECONDS = 900.0
_DEFAULT_PROVIDER_ORDER = ("tavily", "serper", "brave", "duckduckgo")
_DEFAULT_SEARCH_MULTI_PROVIDER = False
_PROVIDER_COOLDOWN_SECONDS = 60.0
_PROVIDER_FAILURE_SKIP_THRESHOLD = 5

_SEARCH_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_SEARCH_INFLIGHT: dict[str, asyncio.Task[dict[str, Any]]] = {}
_SEARCH_STATE_LOCK = threading.Lock()


@dataclass
class ProviderHealthRecord:
    provider: str
    last_success_at: float | None = None
    last_failure_at: float | None = None
    consecutive_failures: int = 0
    avg_latency_ms: float = 0.0
    _recent_latencies: deque[float] = field(
        default_factory=lambda: deque(maxlen=10),
    )

    def record_success(self, latency_ms: float) -> None:
        self.last_success_at = time.time()
        self.consecutive_failures = 0
        self._recent_latencies.append(float(latency_ms))
        if self._recent_latencies:
            self.avg_latency_ms = sum(self._recent_latencies) / len(self._recent_latencies)

    def record_failure(self) -> None:
        self.last_failure_at = time.time()
        self.consecutive_failures += 1


_PROVIDER_HEALTH: dict[str, ProviderHealthRecord] = {}

TOOL_METADATA = {
    "tool_id": "web_search",
    "description": (
        "Search the web for current information. Provider cascade: "
        "Tavily (DAN_TAVILY_API_KEY), Serper/Google (DAN_SERPER_API_KEY), "
        "Brave (DAN_BRAVE_API_KEY), DuckDuckGo (no key, least reliable)."
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
            "search_depth": {
                "type": "string",
                "enum": ["quick", "thorough"],
                "description": "Quick = snippet-first; thorough may query multiple providers.",
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional allowlist of domains to search.",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional blocklist of domains to exclude.",
            },
        },
        "required": ["query"],
    },
    "examples": [
        {
            "input": {"query": "python asyncio tutorial", "num_results": 3},
            "output": {
                "results": [
                    {
                        "title": "AsyncIO in Python",
                        "url": "https://example.com",
                        "snippet": "A tutorial...",
                    }
                ],
                "count": 1,
            },
        },
    ],
    "category": "web",
    "returns": (
        "dict with results (list of {title, url, snippet, provider, result_kind}), "
        "count, provider/providers, cache_hit, and provider_failures"
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
            and all(
                str(item.get("error_type", "")) in {"provider_unavailable", "provider_cooldown"}
                for item in provider_failures
            )
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
        super().__init__(
            f"All web search providers failed. Last error from {last_provider}: {summary}"
        )


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _search_cache_ttl_seconds() -> float:
    shared_default = _env_float("DAN_WEB_CACHE_TTL_SECONDS", _DEFAULT_CACHE_TTL_SECONDS)
    return _env_float("DAN_WEB_SEARCH_CACHE_TTL_SECONDS", shared_default)


def _trim_message(text: str, limit: int = 240) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _normalize_provider_name(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized == "ddg":
        return "duckduckgo"
    return normalized


def _normalize_domain_list(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",")]
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        domain = str(value or "").strip().lower()
        if not domain:
            continue
        if domain.startswith("http://") or domain.startswith("https://"):
            domain = domain_from_url(domain)
        domain = domain.lstrip(".")
        if domain.startswith("www."):
            domain = domain[4:]
        if domain and domain not in seen:
            seen.add(domain)
            normalized.append(domain)
    return normalized


def _default_blocked_domains() -> list[str]:
    return _normalize_domain_list(os.environ.get("DAN_SEARCH_BLOCKED_DOMAINS", ""))


def _normalize_location(location: Any) -> dict[str, str]:
    if not isinstance(location, dict):
        location = {}
    country = str(
        location.get("country")
        or os.environ.get("DAN_SEARCH_COUNTRY")
        or ""
    ).strip()
    language = str(
        location.get("language")
        or os.environ.get("DAN_SEARCH_LANGUAGE")
        or ""
    ).strip()
    payload: dict[str, str] = {}
    if country:
        payload["country"] = country
    if language:
        payload["language"] = language
    return payload


def _effective_provider_order() -> list[str]:
    raw = os.environ.get("DAN_SEARCH_PROVIDER_ORDER", ",".join(_DEFAULT_PROVIDER_ORDER))
    normalized = [_normalize_provider_name(item) for item in raw.split(",")]
    order: list[str] = []
    seen: set[str] = set()
    for name in normalized:
        if not name:
            continue
        if name not in {"tavily", "serper", "brave", "duckduckgo"}:
            continue
        if name not in seen:
            seen.add(name)
            order.append(name)
    if "duckduckgo" not in seen:
        order.append("duckduckgo")
    return order


def _search_cache_key(
    query: str,
    num_results: int,
    *,
    search_depth: str,
    allowed_domains: list[str],
    blocked_domains: list[str],
    location: dict[str, str],
    provider_order: list[str],
    multi_provider: bool,
    max_provider_searches: int,
) -> str:
    payload = {
        "query": query.strip(),
        "num_results": int(num_results),
        "search_depth": str(search_depth or "quick"),
        "allowed_domains": sorted(allowed_domains),
        "blocked_domains": sorted(blocked_domains),
        "location": {
            "country": str(location.get("country") or "").lower(),
            "language": str(location.get("language") or "").lower(),
        },
        "provider_order": ",".join(provider_order),
        "multi_provider": bool(multi_provider),
        "max_provider_searches": int(max_provider_searches),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _get_cached_result(key: str) -> dict[str, Any] | None:
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


def _store_cached_result(key: str, payload: dict[str, Any]) -> None:
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
    payload.setdefault("providers", [payload.get("provider", "")] if payload.get("provider") else [])
    payload["cache_hit"] = False
    payload["shared_inflight"] = False
    payload["cache_ttl_seconds"] = _search_cache_ttl_seconds()
    return payload


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

    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
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


def _provider_skip_failure(provider: str, record: ProviderHealthRecord) -> dict[str, Any]:
    return {
        "provider": provider,
        "error_type": "provider_cooldown",
        "retryable": True,
        "status_code": None,
        "summary": (
            f"temporarily skipped after {record.consecutive_failures} consecutive failures"
        ),
        "message": "provider temporarily skipped due to recent repeated failures",
    }


def _health_record(provider: str) -> ProviderHealthRecord:
    record = _PROVIDER_HEALTH.get(provider)
    if record is None:
        record = ProviderHealthRecord(provider=provider)
        _PROVIDER_HEALTH[provider] = record
    return record


def _provider_on_cooldown(provider: str) -> bool:
    record = _PROVIDER_HEALTH.get(provider)
    if record is None or record.last_failure_at is None:
        return False
    if record.consecutive_failures < _PROVIDER_FAILURE_SKIP_THRESHOLD:
        return False
    return (time.time() - record.last_failure_at) < _PROVIDER_COOLDOWN_SECONDS


def _record_provider_success(provider: str, latency_ms: float) -> None:
    _health_record(provider).record_success(latency_ms)


def _record_provider_failure(provider: str) -> None:
    _health_record(provider).record_failure()


def _with_location_terms(query: str, location: dict[str, str]) -> str:
    query_text = str(query or "").strip()
    extras: list[str] = []
    if location.get("country"):
        extras.append(location["country"])
    if location.get("language"):
        extras.append(location["language"])
    if extras:
        query_text = f"{query_text} {' '.join(extras)}".strip()
    return query_text


def _with_allowed_domains(query: str, allowed_domains: list[str]) -> str:
    if not allowed_domains:
        return query
    site_filters = " OR ".join(f"site:{domain}" for domain in allowed_domains)
    return f"{query} ({site_filters})"


def _domain_allowed(url: str, allowed_domains: list[str], blocked_domains: list[str]) -> bool:
    domain = domain_from_url(url)
    if not domain:
        return not allowed_domains
    normalized = domain
    if normalized.startswith("www."):
        normalized = normalized[4:]
    if blocked_domains and any(
        normalized == blocked or normalized.endswith(f".{blocked}")
        for blocked in blocked_domains
    ):
        return False
    if not allowed_domains:
        return True
    return any(
        normalized == allowed or normalized.endswith(f".{allowed}")
        for allowed in allowed_domains
    )


def _post_filter_results(
    results: list[dict[str, Any]],
    *,
    allowed_domains: list[str],
    blocked_domains: list[str],
) -> list[dict[str, Any]]:
    if not allowed_domains and not blocked_domains:
        return results
    return [
        result for result in results
        if _domain_allowed(str(result.get("url", "") or ""), allowed_domains, blocked_domains)
    ]


def _dedupe_by_canonical_url(results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        url = str(result.get("url", "") or "").strip()
        key = canonicalize_search_url(url) or url
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(result)
        if len(deduped) >= limit:
            break
    return deduped


def _merge_provider_results(results_by_provider: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    result_lists = [list(item.get("results") or []) for item in results_by_provider]
    merged: list[dict[str, Any]] = []
    offset = 0
    while len(merged) < limit:
        emitted = False
        for provider_results in result_lists:
            if offset >= len(provider_results):
                continue
            merged.append(provider_results[offset])
            emitted = True
            if len(merged) >= limit:
                break
        if not emitted:
            break
        offset += 1
    return _dedupe_by_canonical_url(merged, limit)


async def _tavily_search(
    query: str,
    num_results: int,
    api_key: str,
    *,
    search_depth: str,
    allowed_domains: list[str],
    blocked_domains: list[str],
    location: dict[str, str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "api_key": api_key,
        "query": _with_location_terms(query, location),
        "max_results": min(num_results, 20),
        "search_depth": "advanced" if search_depth == "thorough" else "basic",
        "topic": "general",
    }
    if allowed_domains:
        payload["include_domains"] = allowed_domains
    if blocked_domains:
        payload["exclude_domains"] = blocked_domains

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post("https://api.tavily.com/search", json=payload)
        resp.raise_for_status()
        data = resp.json()

    results = []
    for row in (data.get("results") or [])[:num_results]:
        results.append({
            "title": row.get("title", ""),
            "url": row.get("url", ""),
            "snippet": row.get("content", ""),
            "provider": "tavily",
            "result_kind": "organic",
            "page_age": row.get("published_date"),
        })

    results = _post_filter_results(
        results,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )
    return {"results": results, "count": len(results), "provider": "tavily"}


async def _serper_search(
    query: str,
    num_results: int,
    api_key: str,
    *,
    allowed_domains: list[str],
    blocked_domains: list[str],
    location: dict[str, str],
) -> dict[str, Any]:
    serper_query = _with_allowed_domains(query, allowed_domains)
    payload: dict[str, Any] = {
        "q": serper_query,
        "num": min(num_results, 10),
    }
    if location.get("country"):
        payload["gl"] = location["country"].lower()
    if location.get("language"):
        payload["hl"] = location["language"].lower()

    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://google.serper.dev/search",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    results: list[dict[str, Any]] = []
    knowledge_graph = data.get("knowledgeGraph") or {}
    if isinstance(knowledge_graph, dict) and (
        knowledge_graph.get("title")
        or knowledge_graph.get("description")
        or knowledge_graph.get("website")
    ):
        results.append({
            "title": knowledge_graph.get("title", "") or knowledge_graph.get("website", ""),
            "url": knowledge_graph.get("website", ""),
            "snippet": knowledge_graph.get("description", ""),
            "provider": "serper",
            "result_kind": "knowledge_graph",
        })

    answer_box = data.get("answerBox") or {}
    answer_text = (
        answer_box.get("answer")
        or answer_box.get("snippet")
        or answer_box.get("title")
        or ""
    )
    if answer_text:
        results.append({
            "title": answer_box.get("title", "") or "Direct answer",
            "url": answer_box.get("link", ""),
            "snippet": answer_text,
            "provider": "serper",
            "result_kind": "direct_answer",
        })

    for row in (data.get("organic") or [])[:num_results]:
        results.append({
            "title": row.get("title", ""),
            "url": row.get("link", ""),
            "snippet": row.get("snippet", ""),
            "provider": "serper",
            "result_kind": "organic",
        })

    results = _post_filter_results(
        results,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )
    return {"results": results[:num_results], "count": len(results[:num_results]), "provider": "serper"}


async def _brave_search(
    query: str,
    num_results: int,
    api_key: str,
    *,
    location: dict[str, str],
    allowed_domains: list[str],
    blocked_domains: list[str],
) -> dict[str, Any]:
    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {
        "q": _with_allowed_domains(_with_location_terms(query, location), allowed_domains),
        "count": min(num_results, 20),
    }
    if location.get("country"):
        params["country"] = location["country"].lower()
    if location.get("language"):
        params["search_lang"] = location["language"].lower()

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()

    results = []
    for row in (data.get("web", {}).get("results") or [])[:num_results]:
        results.append({
            "title": row.get("title", ""),
            "url": row.get("url", ""),
            "snippet": row.get("description", ""),
            "provider": "brave",
            "result_kind": "organic",
            "page_age": row.get("age"),
        })

    results = _post_filter_results(
        results,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )
    return {"results": results, "count": len(results), "provider": "brave"}


async def _ddg_search(
    query: str,
    num_results: int,
    *,
    allowed_domains: list[str],
    blocked_domains: list[str],
    location: dict[str, str],
) -> dict[str, Any]:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        raise ImportError(
            "web_search requires one of: DAN_TAVILY_API_KEY (recommended), "
            "DAN_SERPER_API_KEY, DAN_BRAVE_API_KEY, or the 'duckduckgo-search' "
            "package. Install with: pip install 'duckduckgo-search>=6.0'"
        )

    ddg_query = _with_allowed_domains(_with_location_terms(query, location), allowed_domains)
    results = []
    with DDGS() as ddgs:
        for row in ddgs.text(ddg_query, max_results=num_results):
            results.append({
                "title": row.get("title", ""),
                "url": row.get("href", ""),
                "snippet": row.get("body", ""),
                "provider": "duckduckgo",
                "result_kind": "organic",
            })

    results = _post_filter_results(
        results,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
    )
    return {"results": results, "count": len(results), "provider": "duckduckgo"}


def _provider_specs() -> dict[str, tuple[str, str]]:
    return {
        "tavily": ("DAN_TAVILY_API_KEY", "_tavily_search"),
        "serper": ("DAN_SERPER_API_KEY", "_serper_search"),
        "brave": ("DAN_BRAVE_API_KEY", "_brave_search"),
        "duckduckgo": ("", "_ddg_search"),
    }


def _configured_providers() -> list[str]:
    providers: list[str] = []
    for provider in _effective_provider_order():
        if provider == "duckduckgo":
            providers.append(provider)
            continue
        env_name, _ = _provider_specs()[provider]
        if os.environ.get(env_name, "").strip():
            providers.append(provider)
    if "duckduckgo" not in providers:
        providers.append("duckduckgo")
    return providers


async def _run_provider_search(
    provider: str,
    *,
    query: str,
    num_results: int,
    search_depth: str,
    allowed_domains: list[str],
    blocked_domains: list[str],
    location: dict[str, str],
) -> dict[str, Any]:
    if _provider_on_cooldown(provider):
        record = _health_record(provider)
        logger.warning(
            "Skipping web-search provider %s during temporary cooldown", provider
        )
        return {"failure": _provider_skip_failure(provider, record)}

    env_name, fn_name = _provider_specs()[provider]
    api_key = os.environ.get(env_name, "").strip() if env_name else ""
    provider_fn = globals()[fn_name]
    kwargs = {
        "allowed_domains": allowed_domains,
        "blocked_domains": blocked_domains,
        "location": location,
    }
    if provider == "tavily":
        kwargs["search_depth"] = search_depth

    start = time.monotonic()
    try:
        try:
            if api_key:
                result = await provider_fn(query, num_results, api_key, **kwargs)
            else:
                result = await provider_fn(query, num_results, **kwargs)
        except TypeError as exc:
            message = str(exc)
            if "unexpected keyword argument" not in message:
                raise
            if api_key:
                result = await provider_fn(query, num_results, api_key)
            else:
                result = await provider_fn(query, num_results)
        latency_ms = (time.monotonic() - start) * 1000.0
        _record_provider_success(provider, latency_ms)
        return {"result": result}
    except Exception as exc:
        _record_provider_failure(provider)
        return {"failure": _provider_failure(provider, exc), "error": exc}


async def _execute_search(
    query: str,
    num_results: int,
    *,
    search_depth: str,
    allowed_domains: list[str],
    blocked_domains: list[str],
    location: dict[str, str],
    multi_provider: bool,
    max_provider_searches: int,
) -> dict[str, Any]:
    providers = _configured_providers()
    provider_failures: list[dict[str, Any]] = []
    safe_provider_limit = max(1, int(max_provider_searches))
    selected = providers[:safe_provider_limit]

    if multi_provider and len(selected) >= 2:
        top_two = selected[:2]
        outcomes = await asyncio.gather(*[
            _run_provider_search(
                provider,
                query=query,
                num_results=num_results,
                search_depth=search_depth,
                allowed_domains=allowed_domains,
                blocked_domains=blocked_domains,
                location=location,
            )
            for provider in top_two
        ])
        successes: list[dict[str, Any]] = []
        for outcome in outcomes:
            failure = outcome.get("failure")
            if isinstance(failure, dict):
                provider_failures.append(failure)
            result = outcome.get("result")
            if isinstance(result, dict):
                successes.append(result)
        if successes:
            merged = _merge_provider_results(successes, num_results)
            return _finalize_search_result({
                "results": merged,
                "count": len(merged),
                "provider": successes[0].get("provider", ""),
                "providers": [item.get("provider", "") for item in successes if item.get("provider")],
                "provider_failures": provider_failures,
            })
        last_error = next(
            (
                outcome.get("error")
                for outcome in reversed(outcomes)
                if isinstance(outcome.get("error"), Exception)
            ),
            RuntimeError("all providers failed"),
        )
        raise WebSearchProvidersExhaustedError(provider_failures, last_error)

    last_exc: Exception | None = None
    for provider in selected:
        outcome = await _run_provider_search(
            provider,
            query=query,
            num_results=num_results,
            search_depth=search_depth,
            allowed_domains=allowed_domains,
            blocked_domains=blocked_domains,
            location=location,
        )
        failure = outcome.get("failure")
        if isinstance(failure, dict):
            provider_failures.append(failure)
            last_exc = outcome.get("error") or RuntimeError(failure.get("summary") or provider)
            continue
        result = outcome.get("result")
        if isinstance(result, dict):
            result["provider_failures"] = provider_failures
            result["providers"] = [str(result.get("provider", "") or "")]
            return _finalize_search_result(result)

    if last_exc is None:
        last_exc = RuntimeError("no web search providers were available")
    raise WebSearchProvidersExhaustedError(provider_failures, last_exc)


async def web_search(
    query: str,
    num_results: int = 5,
    *,
    search_depth: str = "quick",
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
    location: dict[str, str] | None = None,
    max_provider_searches: int = 4,
    multi_provider: bool | None = None,
    **_kwargs,
) -> dict[str, Any]:
    allowed = _normalize_domain_list(allowed_domains)
    blocked = _normalize_domain_list(blocked_domains) + [
        domain for domain in _default_blocked_domains()
        if domain not in _normalize_domain_list(blocked_domains)
    ]
    location_payload = _normalize_location(location)
    provider_order = _configured_providers()
    use_multi_provider = (
        _env_bool("DAN_SEARCH_MULTI_PROVIDER", _DEFAULT_SEARCH_MULTI_PROVIDER)
        if multi_provider is None
        else bool(multi_provider)
    )
    key = _search_cache_key(
        query,
        num_results,
        search_depth=search_depth,
        allowed_domains=allowed,
        blocked_domains=blocked,
        location=location_payload,
        provider_order=provider_order,
        multi_provider=use_multi_provider,
        max_provider_searches=max_provider_searches,
    )
    cached = _get_cached_result(key)
    if cached is not None:
        return cached

    with _SEARCH_STATE_LOCK:
        inflight = _SEARCH_INFLIGHT.get(key)
        is_owner = inflight is None
        if inflight is None:
            inflight = asyncio.create_task(
                _execute_search(
                    query.strip(),
                    int(num_results),
                    search_depth=str(search_depth or "quick"),
                    allowed_domains=allowed,
                    blocked_domains=blocked,
                    location=location_payload,
                    multi_provider=use_multi_provider and str(search_depth or "quick") == "thorough",
                    max_provider_searches=max_provider_searches,
                )
            )
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
