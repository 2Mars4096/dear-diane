"""Built-in tool: open a URL in a Playwright browser."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_open",
    "description": (
        "Open a URL in a persistent browser session. "
        "The browser uses a dedicated profile so login sessions, cookies, and "
        "localStorage persist across runs — log in once interactively and "
        "subsequent automated runs reuse that auth state. "
        "Returns page title and final URL after navigation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to navigate to.",
            },
        },
        "required": ["url"],
    },
    "examples": [
        {
            "input": {"url": "https://example.com"},
            "output": {"status": "ok", "url": "https://example.com", "title": "Example Domain"},
        },
    ],
    "category": "browser",
    "returns": "dict with status, url, and title",
}


async def browser_open(*, url: str, **_kwargs: object) -> dict:
    from diane.tools._browser_session import get_controller

    ctrl = await get_controller()
    return await ctrl.open(url)
