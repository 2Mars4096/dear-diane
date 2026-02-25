"""Built-in tool: web search via DuckDuckGo (zero-config, no API key)."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "web_search",
    "description": (
        "Search the web using DuckDuckGo. Returns titles, URLs, and snippets. "
        "No API key required — works out of the box. "
        "Requires the optional 'duckduckgo-search' package."
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
    "returns": "dict with results (list of {title, url, snippet}) and count",
}


async def web_search(query: str, num_results: int = 5, **_kwargs) -> dict:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        raise ImportError(
            "web_search requires the 'duckduckgo-search' package. "
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

    return {"results": results, "count": len(results)}
