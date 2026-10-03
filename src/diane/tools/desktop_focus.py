"""Built-in tool: focus a desktop application/window."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "desktop_focus",
    "description": (
        "Focus or activate a desktop application/window by app name or title. "
        "Requires DAN_COMPUTER_CONTROL=1."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app": {"type": "string", "description": "Application name to activate."},
            "title": {"type": "string", "description": "Window title fallback."},
        },
        "anyOf": [
            {"required": ["app"]},
            {"required": ["title"]},
        ],
    },
    "examples": [
        {"input": {"app": "Safari"}, "output": {"status": "ok", "app": "Safari"}},
    ],
    "category": "desktop",
    "returns": "dict with status and focused app/title",
}


async def desktop_focus(
    *, app: str | None = None, title: str | None = None, **_kwargs: object
) -> dict:
    from diane.tools._desktop_session import get_controller, require_desktop_control_enabled

    require_desktop_control_enabled()
    ctrl = await get_controller()
    return await ctrl.focus_window(title=title, app=app)
