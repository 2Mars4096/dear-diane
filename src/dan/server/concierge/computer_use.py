"""Computer use controller — observe/act/verify loop, lease, and /computer commands.

Provides ``UIObservation`` / ``ObservedElement`` perception models,
``ComputerUseController`` high-level runtime, ``ComputerUseLeaseManager``
single-session guard, and ``handle_computer_command`` for the ``/computer``
slash-command family.
"""

from __future__ import annotations

import asyncio
import logging
import platform
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from dan.server.concierge.computer_policy import (
    ActionType,
    AuditEntry,
    AuditLog,
    ComputerControlConfig,
    classify_action,
    is_domain_allowed,
    requires_approval,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Perception models
# ---------------------------------------------------------------------------


class ObservedElement(BaseModel):
    """A single element observed in browser or desktop UI."""

    label: str
    role: str
    bounds: tuple[int, int, int, int] | None = None
    confidence: float = 1.0
    selector: str | None = None


class UIObservation(BaseModel):
    """Snapshot of observable UI state from browser or desktop."""

    surface_type: Literal["browser", "desktop"]
    screenshot_path: str | None = None
    ocr_text: str | None = None
    window_title: str | None = None
    page_url: str | None = None
    elements: list[ObservedElement] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Lease manager — one active input-control session per process
# ---------------------------------------------------------------------------


class ComputerUseLeaseManager:
    """Ensures at most one active computer-use session per process.

    Read-only observation is always allowed. Input actions require acquiring
    the lease via ``acquire(task_id)``.
    """

    def __init__(self) -> None:
        self._active_task_id: str | None = None
        self._lock = asyncio.Lock()

    async def acquire(self, task_id: str) -> bool:
        """Try to acquire the lease for *task_id*. Returns True on success."""
        async with self._lock:
            if self._active_task_id is None or self._active_task_id == task_id:
                self._active_task_id = task_id
                return True
            return False

    async def release(self, task_id: str) -> None:
        """Release the lease held by *task_id*."""
        async with self._lock:
            if self._active_task_id == task_id:
                self._active_task_id = None

    def is_active(self) -> bool:
        return self._active_task_id is not None

    @property
    def active_task_id(self) -> str | None:
        return self._active_task_id


# ---------------------------------------------------------------------------
# Computer use controller
# ---------------------------------------------------------------------------


class ComputerUseController:
    """High-level runtime selecting browser or desktop actions.

    Implements the observe -> act -> verify pattern and browser-to-desktop
    handoff for native dialogs.
    """

    def __init__(
        self,
        config: ComputerControlConfig,
        lease: ComputerUseLeaseManager,
        audit: AuditLog,
        *,
        browser: Any | None = None,
        desktop: Any | None = None,
    ) -> None:
        self.config = config
        self.lease = lease
        self.audit = audit
        self._browser = browser
        self._desktop = desktop

    async def observe(self, surface: Literal["browser", "desktop"] = "browser") -> UIObservation:
        """Capture current UI state from the selected surface."""
        if surface == "browser" and self._browser:
            try:
                screenshot_path = await self._browser.screenshot()
                text = await self._browser.extract_text()
                tabs = await self._browser.list_tabs()
                url = tabs[0]["url"] if tabs else None
                title = tabs[0].get("title") if tabs else None
                return UIObservation(
                    surface_type="browser",
                    screenshot_path=screenshot_path,
                    ocr_text=text[:2000] if text else None,
                    page_url=url,
                    window_title=title,
                )
            except Exception as exc:
                logger.warning("Browser observation failed: %s", exc)

        if self._desktop:
            try:
                screenshot_path = await self._desktop.screenshot()
                ocr_text = await self._desktop.ocr()
                windows = await self._desktop.list_windows()
                title = windows[0]["app"] if windows else None
                return UIObservation(
                    surface_type="desktop",
                    screenshot_path=screenshot_path,
                    ocr_text=ocr_text,
                    window_title=title,
                )
            except Exception as exc:
                logger.warning("Desktop observation failed: %s", exc)

        return UIObservation(surface_type=surface)

    async def act(
        self,
        action: str,
        target: str | None = None,
        task_id: str = "default",
        **kwargs: Any,
    ) -> dict:
        """Execute a computer-use action with policy checks and audit."""
        if not self.config.enabled:
            return {"status": "error", "message": "Computer control is disabled"}

        action_type = classify_action(action, target)

        if requires_approval(action_type, self.config):
            self.audit.add(AuditEntry(
                action=action,
                target=target,
                action_type=action_type,
                approved=False,
                result="denied",
            ))
            return {
                "status": "denied",
                "message": f"Action '{action}' requires approval (type: {action_type})",
                "action_type": action_type,
            }

        if action_type != "read_only":
            acquired = await self.lease.acquire(task_id)
            if not acquired:
                return {
                    "status": "error",
                    "message": f"Lease held by another task ({self.lease.active_task_id})",
                }

        try:
            raw = await self._dispatch(action, target, **kwargs)
            result = raw if isinstance(raw, dict) else {"status": "ok", "value": raw}
            self.audit.add(AuditEntry(
                action=action,
                target=target,
                action_type=action_type,
                approved=True,
                result="success",
            ))
            return result
        except Exception as exc:
            self.audit.add(AuditEntry(
                action=action,
                target=target,
                action_type=action_type,
                approved=True,
                result="failed",
            ))
            return {"status": "error", "message": str(exc)}

    async def _dispatch(self, action: str, target: str | None, **kwargs: Any) -> dict:
        """Route action to the appropriate backend."""
        browser_actions = {
            "open", "click", "type_text", "fill", "select",
            "wait_for", "extract_text", "screenshot", "download",
            "list_tabs", "switch_tab",
        }
        desktop_actions = {
            "focus_window", "hotkey", "clipboard_read", "clipboard_write",
            "list_windows", "ocr",
        }

        if action in browser_actions and self._browser:
            method = getattr(self._browser, action, None)
            if method:
                if action == "open" and target:
                    return await method(target)
                if action in ("click", "extract_text") and target:
                    return await method(target)
                if action in ("type_text", "fill") and target:
                    text = kwargs.get("text", "")
                    return await method(target, text)
                if action == "select" and target:
                    value = kwargs.get("value", "")
                    return await method(target, value)
                if action == "wait_for":
                    return await method(target, **kwargs)
                if action == "switch_tab":
                    index = kwargs.get("index", 0)
                    return await method(index)
                return await method()

        if action in desktop_actions and self._desktop:
            method = getattr(self._desktop, action, None)
            if method:
                if action == "focus_window":
                    return await method(title=kwargs.get("title"), app=kwargs.get("app") or target)
                if action == "click":
                    return await method(
                        x=kwargs.get("x", 0),
                        y=kwargs.get("y", 0),
                        button=kwargs.get("button", "left"),
                    )
                if action == "type_text":
                    return await method(target or kwargs.get("text", ""))
                if action == "hotkey":
                    return await method(kwargs.get("keys", []))
                if action == "clipboard_write":
                    return await method(target or kwargs.get("text", ""))
                return await method()

        if action == "click" and self._desktop and target is None:
            return await self._desktop.click(
                x=kwargs.get("x", 0),
                y=kwargs.get("y", 0),
                button=kwargs.get("button", "left"),
            )

        return {"status": "error", "message": f"No backend available for action '{action}'"}


# ---------------------------------------------------------------------------
# /computer command handler
# ---------------------------------------------------------------------------


def _format_doctor() -> str:
    """Run permission checks and format results."""
    lines = ["**Computer Doctor**\n"]

    lines.append(f"Platform: {platform.system()} {platform.release()}")

    try:
        from dan.tools.desktop_control import check_macos_permissions
        perms = check_macos_permissions()
        if perms.platform_supported:
            lines.append(f"Screen Recording: {'OK' if perms.screen_recording else 'MISSING'}")
            lines.append(f"Accessibility: {'OK' if perms.accessibility else 'MISSING'}")
            lines.append(f"Apple Events: {'OK' if perms.apple_events else 'MISSING'}")
        else:
            lines.append("macOS permissions: N/A (not macOS)")
    except Exception as exc:
        lines.append(f"Permission check error: {exc}")

    try:
        from dan.tools.browser_control import is_playwright_available
        pw = is_playwright_available()
        lines.append(f"Playwright: {'installed' if pw else 'NOT installed'}")
    except Exception:
        lines.append("Playwright: check failed")

    return "\n".join(lines)


def handle_computer_command(
    text: str,
    config: ComputerControlConfig,
    lease: ComputerUseLeaseManager,
    audit: AuditLog,
) -> str:
    """Dispatch ``/computer status|doctor|approve`` commands."""
    parts = text.strip().split(maxsplit=2)
    subcommand = parts[1].lower() if len(parts) > 1 else "status"

    if subcommand == "status":
        chunks = config.chunk_policies
        enabled_chunks = []
        for name in ("observe", "browser", "input", "window", "files", "system"):
            chunk = getattr(chunks, name)
            if chunk.enabled:
                enabled_chunks.append(name)

        lines = [
            "**Computer Control Status**\n",
            f"Enabled: {config.enabled}",
            f"Foreground only: {config.foreground_only}",
            f"Active chunks: {', '.join(enabled_chunks) or 'none'}",
            f"Active session: {lease.active_task_id or 'none'}",
            "",
            audit.format_summary(),
        ]
        return "\n".join(lines)

    if subcommand == "doctor":
        return _format_doctor()

    if subcommand == "approve":
        request_id = parts[2].strip() if len(parts) > 2 else ""
        if not request_id:
            return "Usage: `/computer approve <request-id>`"
        return f"Approval recorded for request `{request_id}`. (V1: approval plumbing is minimal — this is a placeholder acknowledgement.)"

    return f"Unknown subcommand: `{subcommand}`. Use `status`, `doctor`, or `approve`."
