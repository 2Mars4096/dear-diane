"""Built-in tool: type text into the focused desktop UI."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "desktop_type",
    "description": (
        "Type text into the focused desktop application. "
        "Use only after focusing/observing the target UI. Requires DAN_COMPUTER_CONTROL=1."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to type into the focused UI."},
        },
        "required": ["text"],
    },
    "examples": [
        {"input": {"text": "hello"}, "output": {"status": "ok", "length": 5}},
    ],
    "category": "desktop",
    "returns": "dict with type status and typed length",
}


async def desktop_type(*, text: str, **_kwargs: object) -> dict:
    from diane.tools._desktop_session import get_controller, require_desktop_control_enabled

    require_desktop_control_enabled()
    ctrl = await get_controller()
    return await ctrl.type_text(str(text))
