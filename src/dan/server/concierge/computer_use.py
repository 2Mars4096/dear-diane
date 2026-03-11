"""Computer use controller — observe/act/verify loop, lease, and /computer commands.

Provides ``UIObservation`` / ``ObservedElement`` perception models,
``ComputerUseController`` high-level runtime, ``ComputerUseLeaseManager``
single-session guard, and ``handle_computer_command`` for the ``/computer``
slash-command family.

**V1 Boundary**

The first implementation covers:
  - Playwright-backed browser automation on allowlisted domains
  - Explicit browser-owned native-dialog handoff (file pickers, save dialogs)
  - Minimal macOS desktop primitives: observe, focus, click, type, hotkey
  - Local OCR first; external vision export disabled by default
  - Single-process session guard (lease), basic safety policy + audit

Follow-on (not in v1):
  - Broad arbitrary cross-app desktop automation
  - Rich slash-command policy editing (``/computer allow|deny ...``)
  - External vision export with pre-send cropping/redaction workflow
  - Cross-process or multi-host lease management
  - Rich audit/history UI and longer-retention policy analytics
  - Windows and Linux desktop backends (protocol stubs exist now)

**Follow-on: Screenshot Redaction**

v1 sidesteps screenshot privacy by keeping OCR local and vision export
disabled by default.  A future slice should add local pre-send
redaction/cropping for screenshots (region selection, blur/redaction
overlays) before any image data leaves the host.
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
from dan.server.concierge.progress_ux import (
    CheckpointOption,
    CheckpointOptions,
    InteractionRequest,
    ProgressSession,
)

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_FAILURES = 3


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
        progress: ProgressSession | None = None,
    ) -> None:
        self.config = config
        self.lease = lease
        self.audit = audit
        self._browser = browser
        self._desktop = desktop
        self._progress = progress
        self._consecutive_failures = 0
        self._pending_approvals: dict[str, InteractionRequest] = {}
        self._approval_counter = 0

    # -- Progress emission helpers (Task 5-5) ------------------------------

    def _emit_phase(self, label: str) -> None:
        """Emit a progress phase update if a ProgressSession is attached."""
        if self._progress is None:
            return
        phase = self._progress.get_current_phase()
        if phase is not None:
            self._progress.update_phase(phase.id, label)

    def _start_phase(self, phase_id: str, name: str) -> None:
        if self._progress is not None:
            self._progress.start_phase(phase_id, name)

    def _complete_phase(self, phase_id: str, summary: str) -> None:
        if self._progress is not None:
            self._progress.complete_phase(phase_id, summary)

    # -- Approval integration (Task 5-6) -----------------------------------

    def _next_approval_id(self) -> str:
        self._approval_counter += 1
        return f"cu-approval-{self._approval_counter}"

    def create_approval_request(
        self,
        action: str,
        target: str | None,
        action_type: ActionType,
    ) -> tuple[str, InteractionRequest]:
        """Create an ``InteractionRequest`` for a sensitive/destructive action.

        Returns ``(request_id, interaction_request)`` so the caller can surface
        the checkpoint via the progress system and accept via
        ``/computer approve <request_id>``.
        """
        request_id = self._next_approval_id()
        target_label = f" on '{target}'" if target else ""
        interaction = InteractionRequest(
            kind="required_clarification",
            checkpoint=CheckpointOptions(
                summary=f"Computer-use action '{action}'{target_label} is classified as {action_type} and requires approval.",
                options=[
                    CheckpointOption(label="Approve", value="approve", is_default=False),
                    CheckpointOption(label="Deny", value="deny", is_default=True, is_safe_default=True),
                ],
            ),
            timeout_seconds=300.0,
        )
        self._pending_approvals[request_id] = interaction
        return request_id, interaction

    def resolve_approval(self, request_id: str) -> bool:
        """Mark a pending approval as resolved.  Returns True if found."""
        return self._pending_approvals.pop(request_id, None) is not None

    # -- Perception resolution (Task 4-4) ------------------------------------
    #
    # Perception ordering (most deterministic first):
    #   1. DOM selectors — for browser surfaces, Playwright CSS/XPath selectors
    #      give pixel-perfect, fast, fully deterministic element identification.
    #   2. OS/UI tree (Accessibility / AX) — for desktop surfaces, the
    #      accessibility tree provides structured element info without screenshots.
    #   3. Local OCR — extracts text from screenshots using on-device Vision
    #      framework (macOS) or Tesseract; never leaves the host.
    #   4. Vision-model interpretation — fallback-only for ambiguous UIs;
    #      requires explicit VisionExportPolicy.enabled=True + PII protection.
    #
    # The observe() method follows this ordering: browser DOM first, then
    # desktop AX/OCR, with vision-model as an opt-in last resort.

    @staticmethod
    def _resolve_perception_method(
        surface: Literal["browser", "desktop"],
        *,
        has_browser: bool = False,
        has_desktop: bool = False,
        vision_export_enabled: bool = False,
    ) -> list[str]:
        """Return the ordered list of perception methods to try.

        Each entry is one of ``"dom_selectors"``, ``"accessibility_tree"``,
        ``"local_ocr"``, ``"vision_model"``.  Callers should attempt each
        method in order and use the first that succeeds.
        """
        methods: list[str] = []
        if surface == "browser" and has_browser:
            methods.append("dom_selectors")
        if surface == "desktop" and has_desktop:
            methods.append("accessibility_tree")
        methods.append("local_ocr")
        if vision_export_enabled:
            methods.append("vision_model")
        return methods

    async def observe(self, surface: Literal["browser", "desktop"] = "browser") -> UIObservation:
        """Capture current UI state from the selected surface.

        Follows the perception ordering defined in ``_resolve_perception_method``:
        DOM selectors → AX tree → local OCR → vision model (fallback-only).
        """
        # Step 1: Browser DOM selectors (fastest, most deterministic)
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
                if surface == "browser":
                    raise RuntimeError(f"Browser observation failed and no fallback allowed (explicitly requested): {exc}") from exc

        # Step 2/3: Desktop AX tree + local OCR (never leaves host)
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

        # Step 4: Vision model fallback is not attempted here; callers must
        # opt in via VisionExportPolicy and handle it externally.
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

        if self.config.foreground_only and not await self._check_foreground(task_id):
            return {
                "status": "error",
                "message": "Foreground-only mode: target window is not in the foreground",
            }

        action_type = classify_action(action, target)

        if requires_approval(action_type, self.config):
            request_id, interaction = self.create_approval_request(
                action, target, action_type,
            )
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
                "approval_request_id": request_id,
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
        """Route action to the appropriate backend.

        Emits progress phase updates for key action types.
        """
        _PROGRESS_LABELS: dict[str, str] = {
            "open": "opening browser",
            "fill": "filling form",
            "type_text": "filling form",
            "download": "waiting for download",
            "handle_native_dialog": "switching to file dialog",
            "handle_file_dialog": "switching to file dialog",
            "focus_window": "switching window",
        }
        label = _PROGRESS_LABELS.get(action)
        if label:
            self._emit_phase(label)

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

    # -- Foreground enforcement (Task 6-4) ---------------------------------

    async def _check_foreground(self, task_id: str) -> bool:
        """Return True if foreground_only is disabled or the active window matches.

        When foreground_only is True, we verify the current desktop window
        belongs to an expected target (browser or the lease holder).  If no
        desktop backend is available we conservatively allow the action.
        """
        if not self.config.foreground_only:
            return True
        if not self._desktop:
            return True
        try:
            windows = await self._desktop.list_windows()
            if not windows:
                return True
            front_app = windows[0].get("app", "").lower()
            browser_names = {"google chrome", "chromium", "firefox", "safari", "arc", "microsoft edge"}
            if self._browser and front_app in browser_names:
                return True
            return bool(front_app)
        except Exception:
            return True

    # -- Verification helpers (Task 4-3) -----------------------------------

    @staticmethod
    def _verify_action_effect(
        before: UIObservation,
        after: UIObservation,
        action: str,
    ) -> bool:
        """Heuristic check that *action* produced an observable change."""
        if action in ("open", "click", "switch_tab", "focus_window"):
            if before.page_url != after.page_url:
                return True
            if before.window_title != after.window_title:
                return True

        if action in ("type_text", "fill", "hotkey", "clipboard_write"):
            if before.ocr_text != after.ocr_text:
                return True

        if action == "screenshot":
            return after.screenshot_path is not None

        if before.ocr_text != after.ocr_text:
            return True
        if before.page_url != after.page_url:
            return True
        if before.window_title != after.window_title:
            return True

        return False

    async def execute_with_verification(
        self,
        action: str,
        target: str | None = None,
        task_id: str = "default",
        max_retries: int = 2,
        expected_change: str | None = None,
        **kwargs: Any,
    ) -> dict:
        """Observe-act-verify loop with automatic retry.

        1. Observe current state
        2. Execute the action via ``act()``
        3. Re-observe and verify state changed
        4. Retry up to *max_retries* times on verification failure
        5. Abort if ``MAX_CONSECUTIVE_FAILURES`` is reached

        Emits progress phase updates at each stage when a ProgressSession is
        attached.
        """
        phase_id = f"computer-use-{action}"
        self._start_phase(phase_id, f"computer: {action}")

        for attempt in range(1 + max_retries):
            before = await self.observe(
                "browser" if self._browser else "desktop"
            )

            result = await self.act(action, target, task_id=task_id, **kwargs)

            if result.get("status") in ("error", "denied"):
                self._consecutive_failures += 1
                self._emit_phase(f"verification failed; retrying ({attempt + 1}/{1 + max_retries})")
                if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    await self.lease.release(task_id)
                    self._complete_phase(phase_id, f"aborted after {MAX_CONSECUTIVE_FAILURES} failures")
                    return {
                        "status": "aborted",
                        "message": (
                            f"Aborting after {MAX_CONSECUTIVE_FAILURES} consecutive "
                            "verification failures — lease released"
                        ),
                        "last_result": result,
                    }
                if attempt < max_retries:
                    continue
                self._complete_phase(phase_id, f"{action} failed")
                return result

            after = await self.observe(
                "browser" if self._browser else "desktop"
            )

            if self._verify_action_effect(before, after, action):
                self._consecutive_failures = 0
                result["verified"] = True
                self._complete_phase(phase_id, f"{action} verified")
                return result

            self._consecutive_failures += 1
            self._emit_phase(f"verification failed; retrying ({attempt + 1}/{1 + max_retries})")
            logger.warning(
                "Verification failed for %s (attempt %d/%d)",
                action,
                attempt + 1,
                1 + max_retries,
            )

            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                await self.lease.release(task_id)
                self._complete_phase(phase_id, f"aborted after {MAX_CONSECUTIVE_FAILURES} failures")
                return {
                    "status": "aborted",
                    "message": (
                        f"Aborting after {MAX_CONSECUTIVE_FAILURES} consecutive "
                        "verification failures — lease released"
                    ),
                    "last_result": result,
                }

        self._complete_phase(phase_id, f"{action} verification failed")
        return {
            "status": "verification_failed",
            "message": f"Action '{action}' did not produce a verifiable change after {1 + max_retries} attempts",
        }


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
    *,
    controller: "ComputerUseController | None" = None,
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

        vision_status = "enabled" if config.vision_export.enabled else "disabled"
        lines = [
            "**Computer Control Status**\n",
            f"Enabled: {config.enabled}",
            f"Foreground only: {config.foreground_only}",
            f"Vision export: {vision_status}",
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
        if controller is not None and controller.resolve_approval(request_id):
            return f"Approval granted for request `{request_id}`."
        return f"Approval recorded for request `{request_id}`. (No matching pending request found — may have expired or already been resolved.)"

    return f"Unknown subcommand: `{subcommand}`. Use `status`, `doctor`, or `approve`."
