"""Shell/system capability handlers: shell_command, screenshot, clipboard, notify."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.capabilities._helpers import (
    _FILE_READ_MAX,
    _failure_result,
    _truncate,
)


async def handle_shell_command(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    command = args.get("command", "").strip()
    if not command:
        return CapabilityResult(success=False, message="No command provided.")
    try:
        from dan.tools.shell_command import shell_command
        result = await shell_command(
            command=command,
            working_directory=args.get("working_directory", ""),
            timeout=args.get("timeout", 30),
        )
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        code = result.get("exit_code", result.get("return_code", -1))
        parts = []
        if stdout:
            parts.append(stdout[:_FILE_READ_MAX])
        if stderr:
            parts.append(f"stderr: {stderr[:2000]}")
        parts.append(f"exit code: {code}")
        message = "\n".join(parts)
        error_type: str | None = None
        retryable = False
        if code != 0:
            if code == -1 and "timed out" in stderr.lower():
                error_type = "execution_timeout"
                retryable = True
            else:
                error_type = "nonzero_exit"
        return CapabilityResult(
            success=code == 0,
            message=message,
            data=result,
            output_preview=_truncate(message),
            retryable=retryable,
            error_type=error_type,
        )
    except Exception as exc:
        return _failure_result(
            f"Command failed: {exc}",
            error_type="internal_exception",
        )


async def handle_screenshot(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    import asyncio
    import sys
    from datetime import datetime

    if sys.platform != "darwin":
        return CapabilityResult(success=False, message="Screenshot is only supported on macOS.")

    filename = args.get("filename", "").strip()
    if not filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{ts}.png"

    dan_dir = Path.home() / ".dan" / "screenshots"
    dan_dir.mkdir(parents=True, exist_ok=True)
    filepath = dan_dir / filename

    proc = await asyncio.create_subprocess_exec(
        "screencapture", "-x", str(filepath),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        return CapabilityResult(success=False, message=f"screencapture failed: {stderr.decode()}")
    if not filepath.exists():
        return CapabilityResult(success=False, message="Screenshot file was not created.")
    return CapabilityResult(
        success=True,
        message=f"Screenshot saved to {filepath}",
        data={"path": str(filepath)},
    )


async def handle_clipboard(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    import asyncio
    import sys

    if sys.platform != "darwin":
        return CapabilityResult(success=False, message="Clipboard is only supported on macOS.")

    action = args.get("action", "read").strip()

    if action == "read":
        proc = await asyncio.create_subprocess_exec(
            "pbpaste",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        content = stdout.decode("utf-8", errors="replace")
        if not content:
            return CapabilityResult(success=True, message="(clipboard is empty)")
        if len(content) > 10_000:
            content = content[:10_000] + "\n\n[truncated]"
        return CapabilityResult(success=True, message=content)

    elif action == "write":
        text = args.get("content", "")
        if not text:
            return CapabilityResult(success=False, message="No content provided to copy.")
        proc = await asyncio.create_subprocess_exec(
            "pbcopy",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate(input=text.encode("utf-8"))
        return CapabilityResult(success=True, message=f"Copied {len(text)} chars to clipboard.")

    return CapabilityResult(success=False, message=f"Unknown action: {action}. Use 'read' or 'write'.")


async def handle_notify(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    try:
        from dan.tools.notify import notify as _tool_notify
        result = await _tool_notify(**args)
        return CapabilityResult(success=True, message="Success", data=result, output_preview=str(result)[:1000])
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Error in notify: {exc}")
