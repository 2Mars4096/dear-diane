"""Desktop automation — Protocol, macOS backend, cross-platform stubs, and mock.

Provides ``DesktopController`` protocol, ``MacOSDesktopController`` that wraps
subprocess calls to ``screencapture``, ``pbcopy``/``pbpaste``, and AppleScript
for window/keyboard control, ``WindowsDesktopController`` and
``LinuxDesktopController`` stub classes (raise ``NotImplementedError``), a
``detect_platform()`` factory, and ``MockDesktopController`` for tests.

**V1 scope guard:** The first implementation targets macOS only.  Browser
automation (via Playwright) plus explicit native-dialog handoff plus minimal
desktop primitives (observe, focus, click, type, hotkey) on macOS.  Broader
arbitrary cross-app workflows and Windows/Linux backends are follow-on.
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
from typing import Any, Literal, Protocol, runtime_checkable

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

    async def handle_file_dialog(
        self, action: Literal["save", "open"], path: str | None = None
    ) -> dict: ...


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


def get_bundle_id(app_name: str) -> str | None:
    """Get macOS bundle ID for an app name. Returns None on non-macOS or if not found."""
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["osascript", "-e", f'id of application "{app_name}"'],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["mdfind", f"kMDItemCFBundleIdentifier == '*' && kMDItemDisplayName == '{app_name}'"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            app_path = result.stdout.strip().splitlines()[0]
            id_result = subprocess.run(
                ["mdls", "-name", "kMDItemCFBundleIdentifier", "-raw", app_path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if id_result.returncode == 0 and id_result.stdout.strip() != "(null)":
                return id_result.stdout.strip()
    except Exception:
        pass
    return None


def check_window_allowed(
    app_name: str,
    config: Any = None,
    chunk: str = "input",
) -> bool:
    """Check if *app_name* is in the allowed list for the given chunk.

    Returns True if no config is provided or the chunk has no restrictions.
    """
    if config is None:
        return True
    try:
        from diane.tools.control_policy import is_app_allowed
        chunk_policy = getattr(config.chunk_policies, chunk, None)
        if chunk_policy is None:
            return True
        return is_app_allowed(app_name, chunk_policy)
    except Exception:
        return True


class MacOSDesktopController:
    """macOS desktop controller using screencapture, pbcopy/pbpaste, AppleScript."""

    def __init__(self, config: Any = None) -> None:
        if platform.system() != "Darwin":
            raise RuntimeError("MacOSDesktopController only works on macOS")
        self._config = config

    async def _check_app_allowed(self, action_name: str) -> dict | None:
        """Return an error dict if the frontmost app is not allowed, else None."""
        if self._config is None:
            return None
        try:
            windows = await self.list_windows()
            if windows:
                front_app = windows[0].get("app", "")
                if not check_window_allowed(front_app, self._config, "input"):
                    return {
                        "status": "denied",
                        "message": f"App '{front_app}' not in allowlist for action '{action_name}'",
                    }
        except Exception:
            pass
        return None

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
        denied = await self._check_app_allowed("click")
        if denied:
            return denied
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
        denied = await self._check_app_allowed("type_text")
        if denied:
            return denied
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
        denied = await self._check_app_allowed("hotkey")
        if denied:
            return denied
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

    async def handle_file_dialog(
        self, action: Literal["save", "open"], path: str | None = None
    ) -> dict:
        """Interact with a standard macOS save/open file dialog via AppleScript.

        V1: types the path into the file name field and presses Enter.
        """
        if not path:
            return {"status": "error", "message": "No path provided for file dialog"}
        if action == "save":
            script = (
                'tell application "System Events"\n'
                f"  keystroke {_apple_script_quote(path)}\n"
                '  delay 0.3\n'
                '  keystroke return\n'
                "end tell"
            )
        else:
            script = (
                'tell application "System Events"\n'
                '  keystroke "g" using {command down, shift down}\n'
                '  delay 0.5\n'
                f"  keystroke {_apple_script_quote(path)}\n"
                '  delay 0.3\n'
                '  keystroke return\n'
                '  delay 0.3\n'
                '  keystroke return\n'
                "end tell"
            )
        try:
            await _run_applescript(script)
            return {"status": "ok", "path": path}
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

    async def handle_file_dialog(
        self, action: Literal["save", "open"], path: str | None = None
    ) -> dict:
        return self._record("handle_file_dialog", action=action, path=path)

    async def clipboard_read(self) -> str:
        self._record("clipboard_read")
        return self._responses.get("clipboard_read", "mock clipboard content")

    async def clipboard_write(self, text: str) -> dict:
        return self._record("clipboard_write", text=text)


# ---------------------------------------------------------------------------
# Windows stub (not yet implemented)
# ---------------------------------------------------------------------------


class WindowsDesktopController:
    """Stub Windows desktop controller — all methods raise ``NotImplementedError``.

    The ``DesktopController`` protocol is satisfied at the type level; actual
    functionality will be implemented in a follow-on slice targeting Win32/UI
    Automation APIs.
    """

    def __init__(self, config: Any = None) -> None:
        self._config = config

    async def screenshot(self, region: tuple[int, int, int, int] | None = None) -> str:
        raise NotImplementedError("WindowsDesktopController: screenshot not yet implemented — Windows backend is follow-on work")

    async def ocr(self, region: tuple[int, int, int, int] | None = None) -> str:
        raise NotImplementedError("WindowsDesktopController: ocr not yet implemented — Windows backend is follow-on work")

    async def list_windows(self) -> list[dict]:
        raise NotImplementedError("WindowsDesktopController: list_windows not yet implemented — Windows backend is follow-on work")

    async def focus_window(self, title: str | None = None, app: str | None = None) -> dict:
        raise NotImplementedError("WindowsDesktopController: focus_window not yet implemented — Windows backend is follow-on work")

    async def click(self, x: int, y: int, button: str = "left") -> dict:
        raise NotImplementedError("WindowsDesktopController: click not yet implemented — Windows backend is follow-on work")

    async def type_text(self, text: str) -> dict:
        raise NotImplementedError("WindowsDesktopController: type_text not yet implemented — Windows backend is follow-on work")

    async def hotkey(self, keys: list[str]) -> dict:
        raise NotImplementedError("WindowsDesktopController: hotkey not yet implemented — Windows backend is follow-on work")

    async def clipboard_read(self) -> str:
        raise NotImplementedError("WindowsDesktopController: clipboard_read not yet implemented — Windows backend is follow-on work")

    async def clipboard_write(self, text: str) -> dict:
        raise NotImplementedError("WindowsDesktopController: clipboard_write not yet implemented — Windows backend is follow-on work")

    async def handle_file_dialog(self, action: Literal["save", "open"], path: str | None = None) -> dict:
        raise NotImplementedError("WindowsDesktopController: handle_file_dialog not yet implemented — Windows backend is follow-on work")


# ---------------------------------------------------------------------------
# Linux stub (not yet implemented)
# ---------------------------------------------------------------------------


class LinuxDesktopController:
    """Stub Linux desktop controller — all methods raise ``NotImplementedError``.

    The ``DesktopController`` protocol is satisfied at the type level; actual
    functionality will be implemented in a follow-on slice targeting
    xdotool / AT-SPI / D-Bus interfaces.
    """

    def __init__(self, config: Any = None) -> None:
        self._config = config

    async def screenshot(self, region: tuple[int, int, int, int] | None = None) -> str:
        raise NotImplementedError("LinuxDesktopController: screenshot not yet implemented — Linux backend is follow-on work")

    async def ocr(self, region: tuple[int, int, int, int] | None = None) -> str:
        raise NotImplementedError("LinuxDesktopController: ocr not yet implemented — Linux backend is follow-on work")

    async def list_windows(self) -> list[dict]:
        raise NotImplementedError("LinuxDesktopController: list_windows not yet implemented — Linux backend is follow-on work")

    async def focus_window(self, title: str | None = None, app: str | None = None) -> dict:
        raise NotImplementedError("LinuxDesktopController: focus_window not yet implemented — Linux backend is follow-on work")

    async def click(self, x: int, y: int, button: str = "left") -> dict:
        raise NotImplementedError("LinuxDesktopController: click not yet implemented — Linux backend is follow-on work")

    async def type_text(self, text: str) -> dict:
        raise NotImplementedError("LinuxDesktopController: type_text not yet implemented — Linux backend is follow-on work")

    async def hotkey(self, keys: list[str]) -> dict:
        raise NotImplementedError("LinuxDesktopController: hotkey not yet implemented — Linux backend is follow-on work")

    async def clipboard_read(self) -> str:
        raise NotImplementedError("LinuxDesktopController: clipboard_read not yet implemented — Linux backend is follow-on work")

    async def clipboard_write(self, text: str) -> dict:
        raise NotImplementedError("LinuxDesktopController: clipboard_write not yet implemented — Linux backend is follow-on work")

    async def handle_file_dialog(self, action: Literal["save", "open"], path: str | None = None) -> dict:
        raise NotImplementedError("LinuxDesktopController: handle_file_dialog not yet implemented — Linux backend is follow-on work")


# ---------------------------------------------------------------------------
# Platform detection factory
# ---------------------------------------------------------------------------


def detect_platform(config: Any = None) -> DesktopController:
    """Return the appropriate ``DesktopController`` for the current OS.

    macOS → ``MacOSDesktopController``; Windows/Linux → stub that raises
    ``NotImplementedError`` on every call.  Callers should catch those errors
    and degrade gracefully.
    """
    system = platform.system()
    if system == "Darwin":
        return MacOSDesktopController(config=config)
    if system == "Windows":
        return WindowsDesktopController(config=config)
    if system == "Linux":
        return LinuxDesktopController(config=config)
    logger.warning("Unknown platform %r — returning Linux stub", system)
    return LinuxDesktopController(config=config)
