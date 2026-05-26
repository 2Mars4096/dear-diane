"""Built-in tool: take a screenshot of the current browser page."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_screenshot",
    "description": (
        "Take a screenshot of the current browser page. "
        "Returns the file path to the saved PNG image. "
        "Screenshots are stored in ~/.dan/screenshots/ with automatic rotation."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "examples": [
        {
            "input": {},
            "output": {"path": "~/.dan/screenshots/browser_1710000000000.png"},
        },
    ],
    "category": "browser",
    "returns": "dict with path to the saved screenshot PNG",
}


async def browser_screenshot(**_kwargs: object) -> dict:
    from dan.tools._browser_session import get_controller

    ctrl = await get_controller()
    path = await ctrl.screenshot()
    session_info = getattr(ctrl, "session_info", lambda: {})()
    return {"path": path, "session": session_info}
