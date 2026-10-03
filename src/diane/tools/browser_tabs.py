"""Built-in tool: list open tabs in the persistent browser session."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_tabs",
    "description": (
        "List open tabs, or switch to a tab by its index. "
        "Use this to inspect current browser state before navigating, clicking, "
        "or extracting page content."
    ),
    "parameters": {
        "type": "object",
        "properties": {"index": {"type": "integer", "description": "Optional tab index to activate."}},
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


async def browser_tabs(*, index: int | None = None, **_kwargs: object) -> dict:
    from diane.tools._browser_session import get_controller

    ctrl = await get_controller()
    if index is not None:
        result = await ctrl.switch_tab(index)
        if result.get("status") != "ok":
            return result
    tabs = await ctrl.list_tabs()
    session_info = getattr(ctrl, "session_info", lambda: {})()
    return {"tabs": tabs, "count": len(tabs), "session": session_info}
