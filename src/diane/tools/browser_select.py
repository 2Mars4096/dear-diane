"""Built-in tool: select an option in a browser dropdown."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_select",
    "description": (
        "Select an option in a browser <select> element by CSS selector and option value. "
        "Use browser_inspect first when the selector or available controls are unknown."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector for the <select> element."},
            "value": {"type": "string", "description": "Option value to select."},
        },
        "required": ["selector", "value"],
    },
    "examples": [
        {
            "input": {"selector": "select#country", "value": "US"},
            "output": {"status": "ok", "selector": "select#country", "value": "US"},
        },
    ],
    "category": "browser",
    "returns": "dict with status, selector, and selected value",
}


async def browser_select(*, selector: str, value: str, **_kwargs: object) -> dict:
    from diane.tools._browser_session import get_controller

    ctrl = await get_controller()
    return await ctrl.select(selector, value)
