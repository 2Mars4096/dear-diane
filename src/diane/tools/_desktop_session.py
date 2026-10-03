"""Shared desktop controller helpers for standalone desktop tools."""

from __future__ import annotations

import asyncio
import os
from typing import Any

_lock = asyncio.Lock()
_controller: Any = None


def desktop_control_enabled() -> bool:
    return os.environ.get("DAN_COMPUTER_CONTROL", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def require_desktop_control_enabled() -> None:
    if not desktop_control_enabled():
        raise PermissionError(
            "Desktop control is disabled. Set DAN_COMPUTER_CONTROL=1 for this session "
            "before using desktop_observe, desktop_focus, desktop_click, desktop_type, or desktop_hotkey."
        )


async def get_controller() -> Any:
    global _controller
    if _controller is not None:
        return _controller

    async with _lock:
        if _controller is not None:
            return _controller
        from diane.tools.desktop_control import detect_platform

        _controller = detect_platform()
        return _controller


def _region_tuple(region: list[int] | tuple[int, int, int, int] | None) -> tuple[int, int, int, int] | None:
    if region is None:
        return None
    if len(region) != 4:
        raise ValueError("region must be [x, y, width, height]")
    x, y, width, height = (int(value) for value in region)
    return (x, y, width, height)
