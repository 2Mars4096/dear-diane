"""Built-in tool: send a notification via available system channels."""

from __future__ import annotations

import logging
import json
import os
import sys

logger = logging.getLogger(__name__)

TOOL_METADATA = {
    "tool_id": "notify",
    "description": (
        "Send a desktop or webhook notification. Uses the Diane notification "
        "infrastructure (macOS Notification Center, webhook, or terminal bell). "
        "Useful for alerting the user when a long-running task completes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "The notification message body.",
            },
            "title": {
                "type": "string",
                "description": "Optional notification title.",
                "default": "Diane",
            },
            "channel": {
                "type": "string",
                "description": "Notification channel: 'macos', 'webhook', 'bell', or 'auto' (try all available).",
                "default": "auto",
                "enum": ["auto", "macos", "webhook", "bell"],
            },
        },
        "required": ["message"],
    },
    "examples": [
        {
            "input": {"message": "Your workflow has completed!", "title": "Diane"},
            "output": {"delivered": True, "channel": "macos"},
        },
    ],
    "category": "system",
    "returns": "dict with delivered (bool) and channel used",
}


def _applescript_string(s: str) -> str:
    """Safely quote a string for AppleScript — prevents injection."""
    escaped = s.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


async def notify(
    message: str,
    title: str = "Diane",
    channel: str = "auto",
    **_kwargs,
) -> dict:
    delivered = False
    used_channel = None

    if channel in ("auto", "macos"):
        try:
            import asyncio
            import shutil
            if sys.platform == "darwin":
                if shutil.which("terminal-notifier"):
                    proc = await asyncio.create_subprocess_exec(
                        "terminal-notifier", "-title", title, "-message", message,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
                    delivered = proc.returncode == 0
                else:
                    script = (
                        f'display notification {_applescript_string(message)}'
                        f' with title {_applescript_string(title)}'
                    )
                    proc = await asyncio.create_subprocess_exec(
                        "osascript", "-",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.communicate(input=script.encode("utf-8"))
                    delivered = proc.returncode == 0
                if delivered:
                    used_channel = "macos"
        except Exception:
            if channel == "macos":
                raise
            logger.debug("macOS notification unavailable, trying next channel")

    if not delivered and channel in ("auto", "bell"):
        try:
            sys.stderr.write("\a")
            sys.stderr.flush()
            delivered = True
            used_channel = "bell"
        except Exception:
            if channel == "bell":
                raise

    if not delivered and channel in ("auto", "webhook"):
        try:
            webhook_url = os.environ.get("DAN_NOTIFY_WEBHOOK_URL", "").strip()
            if webhook_url:
                import httpx

                raw_headers = os.environ.get("DAN_NOTIFY_WEBHOOK_HEADERS", "").strip()
                parsed_headers = json.loads(raw_headers) if raw_headers else {}
                headers = (
                    {str(key): str(value) for key, value in parsed_headers.items()}
                    if isinstance(parsed_headers, dict)
                    else {}
                )
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        webhook_url,
                        headers=headers,
                        json={
                            "event_type": "tool_notification",
                            "status": "tool_notification",
                            "message": message,
                            "title": title,
                        },
                    )
                    response.raise_for_status()
                delivered = True
                used_channel = "webhook"
        except Exception:
            if channel == "webhook":
                raise
            logger.debug("Webhook notification unavailable")

    if not delivered:
        sys.stderr.write(f"\a[{title}] {message}\n")
        sys.stderr.flush()
        delivered = True
        used_channel = "stderr"

    return {"delivered": delivered, "channel": used_channel}
