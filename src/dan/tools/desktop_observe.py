"""Built-in tool: observe the current desktop with screenshot, OCR, and windows."""

from __future__ import annotations

TOOL_METADATA = {
    "tool_id": "desktop_observe",
    "description": (
        "Observe the current desktop UI state by capturing a local screenshot, "
        "local OCR text when available, and visible window/app names. "
        "Requires DAN_COMPUTER_CONTROL=1."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "region": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 4,
                "maxItems": 4,
                "description": "Optional [x, y, width, height] region to capture.",
            },
        },
        "required": [],
    },
    "examples": [
        {
            "input": {},
            "output": {
                "surface_type": "desktop",
                "screenshot_path": "~/.dan/screenshots/desktop_1710000000000.png",
                "window_title": "Finder",
            },
        },
    ],
    "category": "desktop",
    "returns": "dict with screenshot_path, ocr_text, windows, and window_title",
}


async def desktop_observe(
    *, region: list[int] | tuple[int, int, int, int] | None = None, **_kwargs: object
) -> dict:
    from dan.tools._desktop_session import _region_tuple, get_controller, require_desktop_control_enabled

    require_desktop_control_enabled()
    normalized_region = _region_tuple(region)
    ctrl = await get_controller()
    screenshot_path = await ctrl.screenshot(normalized_region)
    ocr_text = await ctrl.ocr(normalized_region)
    windows = await ctrl.list_windows()
    window_title = windows[0].get("app") if windows else None
    return {
        "surface_type": "desktop",
        "screenshot_path": screenshot_path,
        "ocr_text": ocr_text,
        "windows": windows,
        "window_title": window_title,
    }
