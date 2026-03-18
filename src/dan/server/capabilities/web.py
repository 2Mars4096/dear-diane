"""Web capability handlers: web_search, web_fetch, http_request."""
from __future__ import annotations

import asyncio
import re as _re
from typing import Any

import httpx

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.capabilities._helpers import (
    _FILE_READ_MAX,
    _classify_network_exception,
    _failure_result,
    _sanitize_web_content,
)

_MAX_AUTO_FETCH_RESULTS = 2
_FETCH_EXCERPT_MAX = 1800


def _trim_note(text: str, limit: int = 180) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _summarize_web_exception(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        reason = exc.response.reason_phrase if exc.response is not None else ""
        summary = f"HTTP {status}" if status is not None else "HTTP error"
        if reason:
            summary += f" {reason}"
        return summary
    return _trim_note(str(exc) or type(exc).__name__)


def _format_provider_failures(failures: list[dict[str, Any]] | None) -> str:
    if not failures:
        return ""

    rendered: list[str] = []
    for failure in failures[:2]:
        provider = str(failure.get("provider", "") or "").strip()
        summary = str(
            failure.get("summary", "")
            or failure.get("message", "")
            or failure.get("error", "")
            or ""
        ).strip()
        summary = _trim_note(summary, limit=90)
        if provider and summary:
            rendered.append(f"{provider}: {summary}")
        elif provider:
            rendered.append(provider)
        elif summary:
            rendered.append(summary)

    note = "; ".join(rendered)
    remaining = len(failures) - len(rendered)
    if remaining > 0:
        note += f"; +{remaining} more"
    return note


def _format_fetch_header(result: dict[str, Any], *, requested_url: str) -> str:
    resolved_url = str(result.get("url", "") or "").strip() or requested_url
    parts: list[str] = []
    status_code = result.get("status_code")
    if isinstance(status_code, int) and status_code > 0:
        parts.append(f"HTTP {status_code}")
    content_type = str(result.get("content_type", "") or "").split(";", 1)[0].strip()
    if content_type:
        parts.append(content_type)
    if result.get("cache_hit"):
        parts.append("cache hit")

    header = f"Fetched {resolved_url}" if resolved_url else "Fetched content"
    if parts:
        header += f" ({'; '.join(parts)})"
    return header


def _format_search_result(index: int, item: dict[str, Any]) -> str:
    title = str(item.get("title", "") or "").strip() or "(untitled result)"
    snippet = str(item.get("snippet", "") or "").strip()
    url = str(item.get("url", "") or "").strip()

    lines = [f"[{index}] {title}"]
    if snippet:
        lines.append(f"Snippet: {snippet}")
    if url:
        lines.append(f"URL: {url}")
    return "\n".join(lines)


def _truncate_grounding_excerpt(content: str, limit: int = _FETCH_EXCERPT_MAX) -> str:
    content = content.strip()
    if len(content) > limit:
        return content[:limit] + "\n[...truncated]"
    return content


async def _fetch_grounding_excerpt(index: int, url: str) -> dict[str, Any]:
    try:
        from dan.tools.web_fetch import web_fetch

        fetched = await web_fetch(url=url)
        content = _sanitize_web_content(fetched.get("content", ""))
        content = _truncate_grounding_excerpt(content)
        if not content:
            return {
                "index": index,
                "url": url,
                "success": False,
                "error": "Fetched page returned no usable text.",
            }
        return {
            "index": index,
            "url": url,
            "success": True,
            "content": content,
            "cache_hit": bool(fetched.get("cache_hit")),
        }
    except Exception as exc:
        error = _summarize_web_exception(exc)
        return {
            "index": index,
            "url": url,
            "success": False,
            "error": error[:240],
        }


async def handle_web_search(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    query = args.get("query", "").strip()
    if not query:
        return CapabilityResult(success=False, message="No search query provided.")
    num = min(10, max(1, args.get("num_results") or 3))
    fetch_content = bool(args.get("fetch_content", False))
    try:
        from dan.tools.web_search import WebSearchProvidersExhaustedError, web_search
        result = await web_search(query=query, num_results=num)
    except ImportError:
        return _failure_result(
            "Web search is not available. Set DAN_TAVILY_API_KEY or DAN_BRAVE_API_KEY, or install duckduckgo-search.",
            error_type="provider_unavailable",
        )
    except WebSearchProvidersExhaustedError as exc:
        fallback_note = _format_provider_failures(exc.provider_failures)
        message = "Web search failed."
        if fallback_note:
            message = f"Web search failed after fallback attempts: {fallback_note}"
        return _failure_result(
            message,
            error_type=exc.error_type,
            retryable=exc.retryable,
            data={"query": query, "provider_failures": exc.provider_failures},
        )
    except Exception as exc:
        error_type, retryable = _classify_network_exception(exc)
        return _failure_result(
            f"Web search failed: {exc}",
            error_type=error_type,
            retryable=retryable,
        )

    results = result.get("results", [])
    if not results:
        return CapabilityResult(success=True, message="No web results found for that query.")

    provider = str(result.get("provider", "") or "").strip()
    provider_failures = result.get("provider_failures") or []
    result_payload = dict(result)
    result_payload["query"] = query
    result_payload["fetch_content_requested"] = fetch_content
    result_payload["grounded_result_count"] = 0
    result_payload["fetched_results"] = []

    header_parts: list[str] = []
    if provider:
        header_parts.append(f"provider: {provider}")
    if result.get("cache_hit"):
        header_parts.append("cache hit")

    lines = [f'Web search results for "{query}"']
    if header_parts:
        lines[0] += f" ({'; '.join(header_parts)})"
    fallback_note = _format_provider_failures(provider_failures)
    if fallback_note:
        lines.append(f"Fallbacks: {fallback_note}")
    lines.append("Results are numbered for citation; snippets may be incomplete without fetched page content.")
    lines.append("")

    for index, item in enumerate(results[:num], start=1):
        lines.append(_format_search_result(index, item))
        lines.append("")

    message = "\n".join(lines).strip()

    if fetch_content and results:
        fetch_targets: list[tuple[int, str]] = []
        for index, item in enumerate(results[:num], start=1):
            url = str(item.get("url", "") or "").strip()
            if not url:
                continue
            fetch_targets.append((index, url))
            if len(fetch_targets) >= _MAX_AUTO_FETCH_RESULTS:
                break

        if fetch_targets:
            fetched_results = await asyncio.gather(*[
                _fetch_grounding_excerpt(index, url) for index, url in fetch_targets
            ])
            result_payload["fetched_results"] = fetched_results
            result_payload["grounded_result_count"] = sum(
                1 for item in fetched_results if item.get("success")
            )

            fetched_sections: list[str] = []
            for fetched in fetched_results:
                idx = int(fetched.get("index", 0) or 0)
                url = str(fetched.get("url", "") or "").strip()
                if fetched.get("success"):
                    fetched_sections.append(
                        f"[{idx}] Fetched content from {url}\n{fetched.get('content', '')}"
                    )
                else:
                    fetched_sections.append(
                        f"[{idx}] Fetch failed for {url}: {fetched.get('error', 'unknown error')}"
                    )
            if fetched_sections:
                message += (
                    "\n\nFetched page excerpts (top results fetched in parallel):\n\n"
                    + "\n\n".join(fetched_sections)
                )

    return CapabilityResult(
        success=True,
        message=message,
        data=result_payload,
    )


async def handle_web_fetch(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    url = args.get("url", "").strip()
    if not url:
        return CapabilityResult(success=False, message="No URL provided.")
    extract_only = (args.get("extract_only") or "").strip()
    try:
        from dan.tools.web_fetch import web_fetch
        result = await web_fetch(url=url)
        content = result.get("content", "")
        content = _sanitize_web_content(content)

        if extract_only:
            pat = _re.compile(_re.escape(extract_only), _re.IGNORECASE)
            lines = content.splitlines()
            kept: list[str] = []
            for i, line in enumerate(lines):
                if pat.search(line):
                    lo = max(0, i - 2)
                    hi = min(len(lines) - 1, i + 2)
                    for j in range(lo, hi + 1):
                        if lines[j] not in kept[-5:]:
                            kept.append(lines[j])
                    kept.append("")
            if kept:
                content = f"Extracted lines matching '{extract_only}':\n\n" + "\n".join(kept)
            else:
                content = f"No content matching '{extract_only}' found on this page."

        if len(content) > _FILE_READ_MAX:
            content = content[:_FILE_READ_MAX] + "\n\n[truncated]"
        header = _format_fetch_header(result, requested_url=url)
        message = header if not content else f"{header}\n\n{content}"
        return CapabilityResult(success=True, message=message, data=result)
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 0
        content_type = exc.response.headers.get("content-type", "") if exc.response is not None else ""
        requested = str(exc.request.url) if exc.request is not None else url
        retryable = status_code == 429 or status_code >= 500
        error_type = "provider_error" if retryable or status_code >= 500 else "http_error"
        return _failure_result(
            f"Failed to fetch URL (HTTP {status_code}) from {requested}",
            error_type=error_type,
            retryable=retryable,
            data={
                "url": requested,
                "status_code": status_code,
                "content_type": content_type,
            },
        )
    except Exception as exc:
        error_type, retryable = _classify_network_exception(exc)
        return _failure_result(
            f"Failed to fetch URL: {exc}",
            error_type=error_type,
            retryable=retryable,
        )


async def handle_http_request(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    url = args.get("url", "").strip()
    if not url:
        return CapabilityResult(success=False, message="No URL provided.")
    try:
        from dan.tools.http_request import http_request
        result = await http_request(
            url=url,
            method=args.get("method", "GET"),
            headers=args.get("headers", {}),
            body=args.get("body", ""),
        )
        body = result.get("body", "")
        if len(body) > _FILE_READ_MAX:
            body = body[:_FILE_READ_MAX] + "\n\n[truncated]"
        status = result.get("status_code", 0)
        error_type: str | None = None
        retryable = False
        if status == 429 or status >= 500:
            error_type = "provider_error"
            retryable = True
        elif status >= 400:
            error_type = "http_error"
        return CapabilityResult(
            success=200 <= status < 400,
            message=f"HTTP {status}\n\n{body}",
            data=result,
            retryable=retryable,
            error_type=error_type,
        )
    except Exception as exc:
        error_type, retryable = _classify_network_exception(exc)
        return _failure_result(
            f"HTTP request failed: {exc}",
            error_type=error_type,
            retryable=retryable,
        )
