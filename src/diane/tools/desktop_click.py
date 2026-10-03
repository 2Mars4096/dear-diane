"""Built-in tool: click absolute desktop coordinates."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "desktop_click",
    "description": (
        "Click at absolute screen coordinates in the focused desktop UI. "
        "Use desktop_observe first to ground coordinates. Requires DAN_COMPUTER_CONTROL=1."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "Absolute screen x coordinate."},
            "y": {"type": "integer", "description": "Absolute screen y coordinate."},
            "button": {
                "type": "string",
                "enum": ["left", "right"],
                "default": "left",
                "description": "Mouse button to click.",
            },
        },
        "required": ["x", "y"],
    },
    "examples": [
        {"input": {"x": 100, "y": 200}, "output": {"status": "ok", "x": 100, "y": 200, "button": "left"}},
    ],
    "category": "desktop",
    "returns": "dict with click status and coordinates",
}


async def desktop_click(*, x: int, y: int, button: str = "left", **_kwargs: object) -> dict:
    from diane.tools._desktop_session import get_controller, require_desktop_control_enabled

    require_desktop_control_enabled()
    ctrl = await get_controller()
    return await ctrl.click(int(x), int(y), button=str(button or "left"))
