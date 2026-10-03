"""Built-in tool: press a desktop keyboard shortcut."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "desktop_hotkey",
    "description": (
        "Press a keyboard shortcut in the focused desktop application, for example "
        "['command', 'l'] or ['ctrl', 'c']. Requires DAN_COMPUTER_CONTROL=1."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Modifier keys plus the final key.",
            },
        },
        "required": ["keys"],
    },
    "examples": [
        {"input": {"keys": ["command", "l"]}, "output": {"status": "ok", "keys": ["command", "l"]}},
    ],
    "category": "desktop",
    "returns": "dict with hotkey status and keys",
}


async def desktop_hotkey(*, keys: list[str], **_kwargs: object) -> dict:
    from diane.tools._desktop_session import get_controller, require_desktop_control_enabled

    require_desktop_control_enabled()
    ctrl = await get_controller()
    return await ctrl.hotkey([str(key) for key in keys])
