"""Web capability handlers: web_search, web_fetch, http_request."""
from __future__ import annotations

import re as _re
from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.capabilities._helpers import (
    _FILE_READ_MAX,
    _classify_network_exception,
    _failure_result,
    _sanitize_web_content,
)


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
        from dan.tools.web_search import web_search
        result = await web_search(query=query, num_results=num)
    except ImportError:
        return _failure_result(
            "Web search is not available. Set DAN_TAVILY_API_KEY or DAN_BRAVE_API_KEY, or install duckduckgo-search.",
            error_type="provider_unavailable",
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

    lines = []
    for item in results[:num]:
        title = item.get("title", "").strip()
        snippet = item.get("snippet", "").strip()
        url = item.get("url", "").strip()
        parts = [p for p in (title, snippet, url) if p]
        if parts:
            lines.append(" — ".join(parts))

    message = "\n\n".join(lines)

    if fetch_content and results:
        top_url = results[0].get("url", "").strip()
        if top_url:
            try:
                from dan.tools.web_fetch import web_fetch
                fetched = await web_fetch(url=top_url)
                content = fetched.get("content", "")
                content = _sanitize_web_content(content)
                if len(content) > 6000:
                    content = content[:6000] + "\n[...truncated]"
                if content.strip():
                    message += f"\n\n--- Content from {top_url} ---\n{content}"
            except Exception:
                pass

    return CapabilityResult(
        success=True,
        message=message,
        data=result,
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
        return CapabilityResult(success=True, message=content, data=result)
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
