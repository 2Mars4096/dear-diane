"""Built-in tool: list open tabs in the persistent browser session."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_tabs",
    "description": (
        "List open tabs in the persistent browser session. "
        "Use this to inspect current browser state before navigating, clicking, "
        "or extracting page content."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "examples": [
        {
            "input": {},
            "output": {
                "tabs": [{"index": 0, "url": "https://example.com", "title": "Example Domain"}],
                "count": 1,
            },
        },
    ],
    "category": "browser",
    "returns": "dict with tabs list and count",
}


async def browser_tabs(**_kwargs: object) -> dict:
    from dan.tools._browser_session import get_controller

    ctrl = await get_controller()
    tabs = await ctrl.list_tabs()
    session_info = getattr(ctrl, "session_info", lambda: {})()
    return {"tabs": tabs, "count": len(tabs), "session": session_info}
