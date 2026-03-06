"""macOS Notification Center integration (osascript / terminal-notifier)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

EVENT_TITLES = {
    "run_completed": "DAN: Run Completed",
    "run_failed": "DAN: Run Failed",
    "human_input_needed": "DAN: Input Needed",
}


class MacOSNotifier:
    """Send notifications via macOS Notification Center."""

    def __init__(self, config: Any) -> None:
        self._config = config
        self._has_terminal_notifier = shutil.which("terminal-notifier") is not None

    async def notify(self, event: dict[str, Any]) -> None:
        if sys.platform != "darwin":
            return
        event_type = event.get("event_type", "")
        if self._config.event_types and event_type not in self._config.event_types:
            return
        title = EVENT_TITLES.get(event_type, "DAN")
        workflow = event.get("workflow_name") or event.get("workflow_id") or ""
        body = self._build_body(event_type, workflow, event)
        await self._send(title, body)

    def _build_body(self, event_type: str, workflow: str, event: dict[str, Any]) -> str:
        if event_type == "run_completed":
            return f"Workflow '{workflow}' finished successfully"
        elif event_type == "run_failed":
            error = event.get("data", {}).get("error", "unknown error")
            return f"Workflow '{workflow}' failed: {error}"
        elif event_type == "human_input_needed":
            prompt = event.get("data", {}).get("prompt", "Input required")
            return f"Workflow '{workflow}': {prompt}"
        return f"Event: {event_type}"

    async def _send(self, title: str, body: str) -> None:
        try:
            if self._has_terminal_notifier:
                cmd = [
                    "terminal-notifier",
                    "-title", title,
                    "-message", body,
                    "-group", "dan",
                ]
            else:
                script = f'display notification "{body}" with title "{title}"'
                cmd = ["osascript", "-e", script]
            await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, timeout=5
            )
        except Exception:
            logger.debug("macOS notification failed", exc_info=True)
