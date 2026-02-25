"""Built-in tool: fetch URL content via httpx."""

from __future__ import annotations

import httpx

TOOL_METADATA = {
    "tool_id": "web_fetch",
    "description": (
        "Fetch the content of a URL and return it as text. "
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
                "content": "<html>...</html>",
                "status_code": 200,
                "content_type": "text/html",
            },
        },
    ],
    "category": "web",
    "returns": "dict with url, content, status_code, and content_type",
}


async def web_fetch(
    url: str,
    timeout: int = 30,
    max_length: int = 100_000,
    **_kwargs,
) -> dict:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        resp = await client.get(url)

    content = resp.text[:max_length]
    return {
        "url": str(resp.url),
        "content": content,
        "status_code": resp.status_code,
        "content_type": resp.headers.get("content-type", ""),
    }
