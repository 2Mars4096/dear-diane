"""Built-in tool: clear and fill text into a browser element."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_fill",
    "description": (
        "Clear an input element and fill it with new text. "
        "Unlike browser_type which appends, this replaces the entire value. "
        "Also works on contenteditable elements."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS selector of the input element.",
            },
            "text": {
                "type": "string",
                "description": "Text to fill into the element (replaces existing content).",
            },
        },
        "required": ["selector", "text"],
    },
    "examples": [
        {
            "input": {"selector": "textarea#reply", "text": "Great post!"},
            "output": {"status": "ok", "selector": "textarea#reply", "length": 11},
        },
    ],
    "category": "browser",
    "returns": "dict with status, selector, and length of filled text",
}


async def browser_fill(*, selector: str, text: str, **_kwargs: object) -> dict:
    from dan.tools._browser_session import get_controller

    ctrl = await get_controller()
    return await ctrl.fill(selector, text)
