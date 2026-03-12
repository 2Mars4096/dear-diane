"""Built-in tool: type text into a browser element (appends to existing value)."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "browser_type",
    "description": (
        "Type text into an element on the current browser page. "
        "Appends to any existing value (use browser_fill to replace). "
        "Simulates real keystrokes for compatibility with reactive frameworks."
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
                "description": "Text to type into the element.",
            },
        },
        "required": ["selector", "text"],
    },
    "examples": [
        {
            "input": {"selector": "input#search", "text": "hello world"},
            "output": {"status": "ok", "selector": "input#search", "length": 11},
        },
    ],
    "category": "browser",
    "returns": "dict with status, selector, and length of typed text",
}


async def browser_type(*, selector: str, text: str, **_kwargs: object) -> dict:
    from dan.tools._browser_session import get_controller

    ctrl = await get_controller()
    return await ctrl.type_text(selector, text)
