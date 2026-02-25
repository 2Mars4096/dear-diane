"""Built-in tool: general-purpose HTTP client."""

from __future__ import annotations

import httpx

TOOL_METADATA = {
    "tool_id": "http_request",
    "description": (
        "Send an HTTP request with configurable method, headers, and body. "
        "Covers REST API integration needs (GET, POST, PUT, DELETE, PATCH). "
        "Returns status code, response headers, and body."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to send the request to.",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
                "description": "HTTP method.",
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "Request headers as key-value pairs.",
            },
            "body": {
                "type": "string",
                "description": "Request body (for POST/PUT/PATCH).",
            },
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds.",
                "default": 30,
            },
        },
        "required": ["url"],
    },
    "examples": [
        {
            "input": {
                "url": "https://api.example.com/data",
                "method": "POST",
                "headers": {"Content-Type": "application/json"},
                "body": '{"key": "value"}',
            },
            "output": {
                "status_code": 201,
                "headers": {"content-type": "application/json"},
                "body": '{"id": 1}',
            },
        },
    ],
    "category": "web",
    "returns": "dict with status_code, headers, and body",
}


async def http_request(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    body: str | None = None,
    timeout: int = 30,
    **_kwargs,
) -> dict:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        resp = await client.request(
            method=method.upper(),
            url=url,
            headers=headers,
            content=body,
        )

    return {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "body": resp.text,
    }
