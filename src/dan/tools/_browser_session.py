"""Shared browser session for workflow-engine browser tools.

Provides a process-level singleton ``PlaywrightBrowserController`` so that
multiple browser tool calls within the same workflow run share one browser
instance (and therefore one set of cookies / auth state).

The profile name defaults to ``DAN_BROWSER_PROFILE`` env var (or ``"default"``).
Headless mode is controlled by ``DAN_BROWSER_HEADLESS`` (default ``"1"``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_controller: Any = None


async def get_controller() -> Any:
    """Return (and lazily create) the shared ``PlaywrightBrowserController``."""
    global _controller
    if _controller is not None:
        return _controller

    async with _lock:
        if _controller is not None:
            return _controller

        from dan.tools.browser_control import PlaywrightBrowserController

        profile = os.environ.get("DAN_BROWSER_PROFILE", "default")
        headless = os.environ.get("DAN_BROWSER_HEADLESS", "1") != "0"

        _controller = PlaywrightBrowserController(
            profile=profile,
            headless=headless,
        )
        return _controller


async def close_controller() -> None:
    """Shut down the shared browser (called at process exit)."""
    global _controller
    if _controller is not None:
        try:
            await _controller.close()
        except Exception:
            logger.debug("Error closing shared browser controller", exc_info=True)
        _controller = None
