"""Run-scoped lazy browsers, with legacy standalone-tool session support.

GUI runs use fresh contexts and desktop-aware visibility. Standalone tools retain
their persistent DAN_BROWSER_PROFILE and legacy headless default.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_controller: Any = None


def browser_headless() -> bool:
    """Use a visible window on desktop hosts, honouring an explicit override."""
    value = os.environ.get("DAN_BROWSER_HEADLESS", "auto").strip().lower()
    if value != "auto":
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        raise ValueError("DAN_BROWSER_HEADLESS must be auto, 0, or 1")
    return sys.platform not in {"darwin", "win32"} and not bool(
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    )


@dataclass
class BrowserScope:
    workspace: Path
    artifact_dir: Path
    controller: Any = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_scope: ContextVar[BrowserScope | None] = ContextVar("dan_browser_scope", default=None)


@asynccontextmanager
async def browser_scope(workspace: str | Path, run_id: str):
    """Lazy, isolated browser for one run; never share a user's Chrome profile."""
    import hashlib
    root = Path(workspace).expanduser().resolve()
    run_key = hashlib.sha256(run_id.encode()).hexdigest()[:16]
    scope = BrowserScope(root, root / "output" / "browser" / run_key)
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        try:
            if scope.controller is not None:
                try:
                    await scope.controller.close()
                except Exception:
                    logger.exception("Could not close this run's browser")
        finally:
            _scope.reset(token)


def download_destination(destination: str | None) -> str | None:
    scope = _scope.get()
    if scope is None:
        return destination
    if not destination:
        raise ValueError("Specify destination_path inside the project workspace")
    path = Path(destination).expanduser()
    path = (path if path.is_absolute() else scope.workspace / path).resolve()
    if not path.is_relative_to(scope.workspace):
        raise ValueError("Browser downloads must stay inside the project workspace")
    if path.exists():
        raise ValueError("Download destination already exists; choose a new filename")
    return str(path)


async def get_controller() -> Any:
    """Return (and lazily create) the shared ``PlaywrightBrowserController``."""
    global _controller
    scope = _scope.get()
    if scope is not None:
        async with scope.lock:
            if scope.controller is None:
                from dan.tools.browser_control import PlaywrightBrowserController
                artifacts = scope.artifact_dir.resolve()
                if not artifacts.is_relative_to(scope.workspace):
                    raise ValueError("Browser artifacts must stay inside the project workspace")
                scope.controller = PlaywrightBrowserController(
                    headless=browser_headless(), artifact_dir=str(artifacts),
                    web_only=True,
                )
            return scope.controller
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
