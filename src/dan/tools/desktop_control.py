"""Desktop automation — Protocol, macOS backend, and mock for testing.

Provides ``DesktopController`` protocol, ``MacOSDesktopController`` that wraps
subprocess calls to ``screencapture``, ``pbcopy``/``pbpaste``, and AppleScript
for window/keyboard control, and ``MockDesktopController`` for tests.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = os.path.expanduser("~/.dan/screenshots")


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DesktopController(Protocol):
    """Common interface for desktop automation backends."""

    async def screenshot(
        self, region: tuple[int, int, int, int] | None = None
    ) -> str: ...

    async def ocr(
        self, region: tuple[int, int, int, int] | None = None
    ) -> str: ...

    async def list_windows(self) -> list[dict]: ...

    async def focus_window(
        self, title: str | None = None, app: str | None = None
    ) -> dict: ...

    async def click(self, x: int, y: int, button: str = "left") -> dict: ...

    async def type_text(self, text: str) -> dict: ...

    async def hotkey(self, keys: list[str]) -> dict: ...

    async def clipboard_read(self) -> str: ...

    async def clipboard_write(self, text: str) -> dict: ...


# ---------------------------------------------------------------------------
# Permission detection
# ---------------------------------------------------------------------------


@dataclass
class PermissionStatus:
    """macOS permission check results."""

    screen_recording: bool = False
    accessibility: bool = False
    apple_events: bool = True
    platform_supported: bool = False


def check_macos_permissions() -> PermissionStatus:
    """Best-effort check for required macOS permissions."""
    status = PermissionStatus(platform_supported=platform.system() == "Darwin")
    if not status.platform_supported:
        return status

    try:
        result = subprocess.run(
            ["screencapture", "-x", "-t", "png", "/dev/null"],
            capture_output=True,
            timeout=5,
        )
        status.screen_recording = result.returncode == 0
    except Exception:
        pass

    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first process whose visible is true',
            ],
            capture_output=True,
            timeout=5,
        )
        status.accessibility = result.returncode == 0
    except Exception:
        pass

    return status


# ---------------------------------------------------------------------------
# macOS implementation
# ---------------------------------------------------------------------------


async def _run_applescript(script: str) -> str:
    """Run an AppleScript and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"AppleScript failed (rc={proc.returncode}): {stderr.decode().strip()}"
        )
    return stdout.decode().strip()


def _apple_script_quote(value: str) -> str:
    """Quote arbitrary text for use inside AppleScript string literals."""
    escaped = (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def _build_ocr_script(screenshot_path: str) -> str:
    """Build the placeholder Vision AppleScript with a safely quoted image path."""
    return (
        'use framework "Vision"\n'
        f"set imgPath to POSIX file {_apple_script_quote(screenshot_path)}\n"
        "set img to current application's NSImage's alloc()'s "
        "initWithContentsOfFile:(POSIX path of imgPath)\n"
    )


class MacOSDesktopController:
    """macOS desktop controller using screencapture, pbcopy/pbpaste, AppleScript."""

    def __init__(self) -> None:
        if platform.system() != "Darwin":
            raise RuntimeError("MacOSDesktopController only works on macOS")

    async def screenshot(
        self, region: tuple[int, int, int, int] | None = None
    ) -> str:
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(SCREENSHOT_DIR, f"desktop_{int(time.time() * 1000)}.png")
        cmd = ["screencapture", "-x"]
        if region:
            x, y, w, h = region
            cmd.extend(["-R", f"{x},{y},{w},{h}"])
        cmd.append(path)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        if proc.returncode != 0 or not os.path.isfile(path):
            raise RuntimeError("screencapture failed — check Screen Recording permission")
        return path

    async def ocr(
        self, region: tuple[int, int, int, int] | None = None
    ) -> str:
        screenshot_path = await self.screenshot(region)
        try:
            script = _build_ocr_script(screenshot_path)
            result = await _run_applescript(
                'do shell script "echo OCR not available via AppleScript in v1"'
            )
            return result or f"[OCR placeholder — screenshot at {screenshot_path}]"
        except Exception:
            return f"[OCR unavailable — screenshot at {screenshot_path}]"

    async def list_windows(self) -> list[dict]:
        script = (
            'tell application "System Events"\n'
            "  set procList to name of every process whose visible is true\n"
            "end tell\n"
            "return procList"
        )
        try:
            raw = await _run_applescript(script)
            names = [n.strip() for n in raw.split(",") if n.strip()]
            return [{"app": name, "index": i} for i, name in enumerate(names)]
        except Exception as exc:
            logger.warning("list_windows failed: %s", exc)
            return []

    async def focus_window(
        self, title: str | None = None, app: str | None = None
    ) -> dict:
        target = app or title
        if not target:
            return {"status": "error", "message": "No target specified"}
        script = (
            f"tell application {_apple_script_quote(target)}\n"
            f"  activate\n"
            f"end tell"
        )
        try:
            await _run_applescript(script)
            return {"status": "ok", "app": target}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    async def click(self, x: int, y: int, button: str = "left") -> dict:
        btn_flag = "1" if button == "left" else "2"
        script = (
            'do shell script "cliclick c:' + f"{x},{y}" + '"'
        )
        try:
            await _run_applescript(script)
            return {"status": "ok", "x": x, "y": y, "button": button}
        except Exception:
            script_fallback = (
                f'tell application "System Events"\n'
                f'  click at {{{x}, {y}}}\n'
                f"end tell"
            )
            try:
                await _run_applescript(script_fallback)
                return {"status": "ok", "x": x, "y": y, "button": button}
            except Exception as exc:
                return {"status": "error", "message": str(exc)}

    async def type_text(self, text: str) -> dict:
        script = (
            f'tell application "System Events"\n'
            f"  keystroke {_apple_script_quote(text)}\n"
            f"end tell"
        )
        try:
            await _run_applescript(script)
            return {"status": "ok", "length": len(text)}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    async def hotkey(self, keys: list[str]) -> dict:
        modifier_map = {
            "command": "command down",
            "cmd": "command down",
            "shift": "shift down",
            "option": "option down",
            "alt": "option down",
            "control": "control down",
            "ctrl": "control down",
        }
        modifiers = []
        key_char = None
        for k in keys:
            lower = k.lower()
            if lower in modifier_map:
                modifiers.append(modifier_map[lower])
            else:
                key_char = k

        if key_char is None:
            return {"status": "error", "message": "No key character specified"}

        mod_str = ", ".join(modifiers)
        using = f" using {{{mod_str}}}" if modifiers else ""
        script = (
            f'tell application "System Events"\n'
            f"  keystroke {_apple_script_quote(key_char)}{using}\n"
            f"end tell"
        )
        try:
            await _run_applescript(script)
            return {"status": "ok", "keys": keys}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    async def clipboard_read(self) -> str:
        proc = await asyncio.create_subprocess_exec(
            "pbpaste",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode()

    async def clipboard_write(self, text: str) -> dict:
        proc = await asyncio.create_subprocess_exec(
            "pbcopy",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate(input=text.encode())
        return {"status": "ok", "length": len(text)}


# ---------------------------------------------------------------------------
# Mock implementation (for testing)
# ---------------------------------------------------------------------------


class MockDesktopController:
    """Records all actions for test assertions."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.actions: list[dict[str, Any]] = []
        self._responses = responses or {}

    def _record(self, action: str, **kwargs: Any) -> dict:
        entry = {"action": action, **kwargs}
        self.actions.append(entry)
        return self._responses.get(action, {"status": "ok", **kwargs})

    async def screenshot(
        self, region: tuple[int, int, int, int] | None = None
    ) -> str:
        self._record("screenshot", region=region)
        return self._responses.get("screenshot", "/tmp/mock_desktop_screenshot.png")

    async def ocr(
        self, region: tuple[int, int, int, int] | None = None
    ) -> str:
        self._record("ocr", region=region)
        return self._responses.get("ocr", "mock ocr text")

    async def list_windows(self) -> list[dict]:
        self._record("list_windows")
        return self._responses.get(
            "list_windows",
            [{"app": "Finder", "index": 0}],
        )

    async def focus_window(
        self, title: str | None = None, app: str | None = None
    ) -> dict:
        return self._record("focus_window", title=title, app=app)

    async def click(self, x: int, y: int, button: str = "left") -> dict:
        return self._record("click", x=x, y=y, button=button)

    async def type_text(self, text: str) -> dict:
        return self._record("type_text", text=text)

    async def hotkey(self, keys: list[str]) -> dict:
        return self._record("hotkey", keys=keys)

    async def clipboard_read(self) -> str:
        self._record("clipboard_read")
        return self._responses.get("clipboard_read", "mock clipboard content")

    async def clipboard_write(self, text: str) -> dict:
        return self._record("clipboard_write", text=text)
