"""Built-in tool: wait for a page element or network idle state."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_wait",
    "description": (
        "Wait for a specific element to appear on the page, or for the page to "
        "reach network-idle state. Use after navigation or actions that trigger "
        "dynamic content loading."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector to wait for (optional — omit to wait for network idle).",
            },
            "timeout": {
                "type": "number",
                "description": "Maximum wait time in seconds.",
                "default": 30.0,
            },
        },
        "required": [],
    },
    "examples": [
        {
            "input": {"selector": "div.feed-loaded", "timeout": 10},
            "output": {"status": "ok", "selector": "div.feed-loaded"},
        },
        {
            "input": {},
            "output": {"status": "ok", "selector": None},
        },
    ],
    "category": "browser",
    "returns": "dict with status and the selector that was waited for",
}


async def browser_wait(
    *, selector: str | None = None, timeout: float = 30.0, **_kwargs: object
) -> dict:
    from diane.tools._browser_session import get_controller

    ctrl = await get_controller()
    result = await ctrl.wait_for(selector, timeout)
    session_info = getattr(ctrl, "session_info", lambda: {})()
    if isinstance(result, dict):
        return {**result, "session": session_info}
    return {"status": "ok", "selector": selector, "session": session_info}
