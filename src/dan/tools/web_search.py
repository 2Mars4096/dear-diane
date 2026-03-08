"""Built-in tool: web search with provider cascade (Tavily → Brave → DuckDuckGo)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

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
    "returns": "dict with results (list of {title, url, snippet}), count, and provider",
}


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


async def web_search(query: str, num_results: int = 5, **_kwargs) -> dict:
    tavily_key = os.environ.get("DAN_TAVILY_API_KEY", "").strip()
    brave_key = os.environ.get("DAN_BRAVE_API_KEY", "").strip()

    if tavily_key:
        try:
            return await _tavily_search(query, num_results, tavily_key)
        except Exception as exc:
            logger.warning("Tavily search failed (%s), trying next provider", exc)

    if brave_key:
        try:
            return await _brave_search(query, num_results, brave_key)
        except Exception as exc:
            logger.warning("Brave search failed (%s), falling back to DuckDuckGo", exc)

    return await _ddg_search(query, num_results)
