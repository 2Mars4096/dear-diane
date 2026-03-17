"""NotificationManager — subscribes to GlobalEventBus and dispatches to channels."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from dan.notifications.config import NotificationConfig

logger = logging.getLogger(__name__)

NOTIFICATION_EVENTS = frozenset(
    {"run_completed", "run_failed", "human_input_needed", "schedule_result_ready"}
)


class NotificationManager:
    """Subscribes to :class:`GlobalEventBus` and dispatches to configured channels."""

    def __init__(self, config: NotificationConfig) -> None:
        self._config = config
        self._channels: list[Any] = []
        self._subscriber_id = "notification-manager"
        self._task: asyncio.Task[None] | None = None
        self._setup_channels()

    def _setup_channels(self) -> None:
        if self._config.macos.enabled:
            from dan.notifications.macos import MacOSNotifier

            self._channels.append(MacOSNotifier(self._config.macos))
        if self._config.bell.enabled:
            from dan.notifications.terminal import TerminalBellNotifier

            self._channels.append(TerminalBellNotifier(self._config.bell))
        if self._config.webhook.enabled and self._config.webhook.url:
            from dan.notifications.webhook import WebhookNotifier

            self._channels.append(WebhookNotifier(self._config.webhook))

    @property
    def channels(self) -> list[Any]:
        return list(self._channels)

    async def start(self, event_bus: Any) -> None:
        """Subscribe to *event_bus* and start processing events."""
        queue = event_bus.subscribe(self._subscriber_id)
        self._task = asyncio.create_task(self._process_events(queue))

    async def _process_events(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        while True:
            try:
                event = await queue.get()
                event_type = event.get("event_type", "")
                if event_type in NOTIFICATION_EVENTS:
                    await self._dispatch(event)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Notification dispatch error")

    async def _dispatch(self, event: dict[str, Any]) -> None:
        for channel in self._channels:
            try:
                await channel.notify(event)
            except Exception:
                logger.exception("Channel %s failed", type(channel).__name__)

    async def stop(self, event_bus: Any) -> None:
        """Cancel the processing task and unsubscribe."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        event_bus.unsubscribe(self._subscriber_id)
