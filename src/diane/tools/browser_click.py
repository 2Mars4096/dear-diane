"""Built-in tool: click an element in the browser by CSS selector."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_click",
    "description": (
        "Click an element on the current browser page identified by CSS selector. "
        "Waits for the element to be visible and actionable before clicking."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector of the element to click.",
            },
        },
        "required": ["selector"],
    },
    "examples": [
        {
            "input": {"selector": "button.submit"},
            "output": {"status": "ok", "selector": "button.submit"},
        },
    ],
    "category": "browser",
    "returns": "dict with status and selector",
}


async def browser_click(*, selector: str, **_kwargs: object) -> dict:
    from diane.tools._browser_session import get_controller

    ctrl = await get_controller()
    return await ctrl.click(selector)
