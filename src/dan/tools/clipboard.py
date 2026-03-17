"""Built-in tool: copy text to system clipboard."""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil

logger = logging.getLogger(__name__)

_NO_CLIPBOARD_UTILITY_MESSAGE = (
    "No clipboard utility found. Requires pbcopy/pbpaste (macOS) or "
    "xclip/xsel (Linux). Not available in headless/SSH environments "
    "without X forwarding."
)

TOOL_METADATA = {
    "tool_id": "clipboard",
    "description": (
        "Copy text to the system clipboard. Works on macOS (pbcopy) and "
        "Linux (xclip or xsel). Returns an error if no clipboard utility "
        "is available (e.g. headless server, SSH without X forwarding)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The text to copy to the clipboard.",
            },
        },
        "required": ["text"],
    },
    "examples": [
        {
            "input": {"text": "Hello, world!"},
            "output": {"copied": True, "length": 13},
        },
    ],
    "category": "system",
    "returns": "dict with copied (bool) and length (int)",
}


def _find_clipboard_cmd() -> list[str] | None:
    system = platform.system()
    if system == "Darwin":
        if shutil.which("pbcopy"):
            return ["pbcopy"]
    elif system == "Linux":
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard"]
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--input"]
    return None


def _find_clipboard_read_cmd() -> list[str] | None:
    system = platform.system()
    if system == "Darwin":
        if shutil.which("pbpaste"):
            return ["pbpaste"]
    elif system == "Linux":
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard", "-o"]
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--output"]
    return None


def _clipboard_unavailable_message(detail: str = "") -> str:
    detail = detail.strip()
    if detail:
        return f"Clipboard utility exists but is unavailable in this session: {detail}"
    return (
        "Clipboard utility exists but is unavailable in this session. "
        "This often happens in headless/SSH environments or when GUI "
        "clipboard access is disabled."
    )


async def _run_clipboard_command(
    cmd: list[str],
    *,
    input_bytes: bytes | None = None,
) -> tuple[bytes, str, int]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=input_bytes)
    return stdout, stderr.decode("utf-8", errors="replace").strip(), proc.returncode


async def read_clipboard_text() -> str:
    cmd = _find_clipboard_read_cmd()
    if cmd is None:
        raise RuntimeError(_NO_CLIPBOARD_UTILITY_MESSAGE)

    stdout, stderr, returncode = await _run_clipboard_command(cmd)
    if returncode != 0:
        raise RuntimeError(_clipboard_unavailable_message(stderr))
    return stdout.decode("utf-8", errors="replace")


async def ensure_clipboard_available() -> None:
    write_cmd = _find_clipboard_cmd()
    if write_cmd is None:
        raise RuntimeError(_NO_CLIPBOARD_UTILITY_MESSAGE)

    read_cmd = _find_clipboard_read_cmd()
    if read_cmd is None:
        return

    await read_clipboard_text()


async def clipboard(text: str, **_kwargs) -> dict:
    cmd = _find_clipboard_cmd()
    if cmd is None:
        raise RuntimeError(_NO_CLIPBOARD_UTILITY_MESSAGE)

    await ensure_clipboard_available()
    _stdout, stderr, returncode = await _run_clipboard_command(
        cmd,
        input_bytes=text.encode("utf-8"),
    )

    if returncode != 0:
        if stderr:
            raise RuntimeError(f"Clipboard command failed: {stderr}")
        raise RuntimeError(_clipboard_unavailable_message())

    return {"copied": True, "length": len(text)}
