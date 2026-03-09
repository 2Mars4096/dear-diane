"""Built-in tool: copy text to system clipboard."""

from __future__ import annotations

import asyncio
import logging
import platform
import shutil

logger = logging.getLogger(__name__)

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


async def clipboard(text: str, **_kwargs) -> dict:
    cmd = _find_clipboard_cmd()
    if cmd is None:
        raise RuntimeError(
            "No clipboard utility found. Requires pbcopy (macOS) or xclip/xsel (Linux). "
            "Not available in headless/SSH environments without X forwarding."
        )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate(input=text.encode("utf-8"))

    if proc.returncode != 0:
        raise RuntimeError(f"Clipboard command failed: {stderr.decode().strip()}")

    return {"copied": True, "length": len(text)}
