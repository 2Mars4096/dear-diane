"""Terminal bell helpers for CLI-side notifications."""

from __future__ import annotations

import os
import sys
from typing import Any

BELL_EVENTS = frozenset(
    {"run_completed", "run_failed", "human_input_needed", "schedule_result_ready"}
)


def should_ring_bell() -> bool:
    """Check if terminal bell notifications are enabled via ``DAN_NOTIFY_BELL``."""
    return os.environ.get("DAN_NOTIFY_BELL", "").strip().lower() in ("1", "true", "yes")


def ring_bell() -> None:
    """Ring terminal bell if enabled."""
    if should_ring_bell():
        try:
            sys.stderr.write("\a")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass


def maybe_ring_on_event(event_type: str) -> None:
    """Ring bell for notification-worthy events."""
    if event_type in BELL_EVENTS:
        ring_bell()


class TerminalBellNotifier:
    """Channel adapter so the bell can be used by :class:`NotificationManager`."""

    def __init__(self, config: Any) -> None:
        self._config = config

    async def notify(self, event: dict[str, Any]) -> None:
        event_type = event.get("event_type", "")
        if self._config.event_types and event_type not in self._config.event_types:
            return
        try:
            sys.stderr.write("\a")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
