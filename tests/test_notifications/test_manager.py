"""Tests for dan.notifications.manager — NotificationManager."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.notifications.config import ChannelConfig, NotificationConfig, WebhookConfig
from dan.notifications.manager import NOTIFICATION_EVENTS, NotificationManager


def _make_event(event_type: str, **kwargs: Any) -> dict[str, Any]:
    return {"event_type": event_type, "run_id": "r1", "workflow_name": "wf1", **kwargs}


class _FakeEventBus:
    """Minimal stand-in for GlobalEventBus."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribed: str | None = None
        self._unsubscribed: str | None = None

    def subscribe(self, subscriber_id: str) -> asyncio.Queue[dict[str, Any]]:
        self._subscribed = subscriber_id
        return self.queue

    def unsubscribe(self, subscriber_id: str) -> None:
        self._unsubscribed = subscriber_id


# ── Setup / channels ──────────────────────────────────────────────────────


class TestSetupChannels:
    def test_no_channels_when_all_disabled(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=False),
            bell=ChannelConfig(enabled=False),
            webhook=WebhookConfig(enabled=False),
        )
        mgr = NotificationManager(cfg)
        assert len(mgr.channels) == 0

    def test_macos_channel_added_when_enabled(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=True),
            bell=ChannelConfig(enabled=False),
            webhook=WebhookConfig(enabled=False),
        )
        with patch("dan.notifications.macos.MacOSNotifier") as mock_cls:
            mock_cls.return_value = MagicMock()
            mgr = NotificationManager(cfg)
        assert len(mgr.channels) == 1

    def test_bell_channel_added_when_enabled(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=False),
            bell=ChannelConfig(enabled=True),
            webhook=WebhookConfig(enabled=False),
        )
        mgr = NotificationManager(cfg)
        assert len(mgr.channels) == 1

    def test_webhook_needs_url(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=False),
            bell=ChannelConfig(enabled=False),
            webhook=WebhookConfig(enabled=True, url=""),
        )
        mgr = NotificationManager(cfg)
        assert len(mgr.channels) == 0

    def test_webhook_added_with_url(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=False),
            bell=ChannelConfig(enabled=False),
            webhook=WebhookConfig(enabled=True, url="https://example.com/hook"),
        )
        mgr = NotificationManager(cfg)
        assert len(mgr.channels) == 1


# ── Event processing ─────────────────────────────────────────────────────


class TestEventProcessing:
    @pytest.mark.asyncio
    async def test_dispatches_notification_events(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=False),
            bell=ChannelConfig(enabled=False),
            webhook=WebhookConfig(enabled=False),
        )
        mgr = NotificationManager(cfg)
        mock_channel = AsyncMock()
        mgr._channels = [mock_channel]

        bus = _FakeEventBus()
        await mgr.start(bus)

        event = _make_event("run_completed")
        await bus.queue.put(event)
        await asyncio.sleep(0.05)

        mock_channel.notify.assert_called_once_with(event)
        await mgr.stop(bus)

    @pytest.mark.asyncio
    async def test_filters_non_notification_events(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=False),
            bell=ChannelConfig(enabled=False),
            webhook=WebhookConfig(enabled=False),
        )
        mgr = NotificationManager(cfg)
        mock_channel = AsyncMock()
        mgr._channels = [mock_channel]

        bus = _FakeEventBus()
        await mgr.start(bus)

        await bus.queue.put(_make_event("node_started"))
        await bus.queue.put(_make_event("run_started"))
        await bus.queue.put(_make_event("log"))
        await asyncio.sleep(0.05)

        mock_channel.notify.assert_not_called()
        await mgr.stop(bus)

    @pytest.mark.asyncio
    async def test_dispatches_all_notification_event_types(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=False),
            bell=ChannelConfig(enabled=False),
            webhook=WebhookConfig(enabled=False),
        )
        mgr = NotificationManager(cfg)
        mock_channel = AsyncMock()
        mgr._channels = [mock_channel]

        bus = _FakeEventBus()
        await mgr.start(bus)

        for et in NOTIFICATION_EVENTS:
            await bus.queue.put(_make_event(et))
        await asyncio.sleep(0.05)

        assert mock_channel.notify.call_count == len(NOTIFICATION_EVENTS)
        await mgr.stop(bus)

    @pytest.mark.asyncio
    async def test_dispatch_to_multiple_channels(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=False),
            bell=ChannelConfig(enabled=False),
            webhook=WebhookConfig(enabled=False),
        )
        mgr = NotificationManager(cfg)
        ch1, ch2 = AsyncMock(), AsyncMock()
        mgr._channels = [ch1, ch2]

        bus = _FakeEventBus()
        await mgr.start(bus)

        event = _make_event("run_failed")
        await bus.queue.put(event)
        await asyncio.sleep(0.05)

        ch1.notify.assert_called_once_with(event)
        ch2.notify.assert_called_once_with(event)
        await mgr.stop(bus)

    @pytest.mark.asyncio
    async def test_channel_error_does_not_crash_loop(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=False),
            bell=ChannelConfig(enabled=False),
            webhook=WebhookConfig(enabled=False),
        )
        mgr = NotificationManager(cfg)
        failing = AsyncMock(side_effect=RuntimeError("boom"))
        healthy = AsyncMock()
        mgr._channels = [failing, healthy]

        bus = _FakeEventBus()
        await mgr.start(bus)

        await bus.queue.put(_make_event("run_completed"))
        await asyncio.sleep(0.05)

        healthy.notify.assert_called_once()
        await mgr.stop(bus)


# ── Start / stop lifecycle ────────────────────────────────────────────────


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=False),
            bell=ChannelConfig(enabled=False),
            webhook=WebhookConfig(enabled=False),
        )
        mgr = NotificationManager(cfg)
        bus = _FakeEventBus()
        await mgr.start(bus)
        assert mgr._task is not None
        assert not mgr._task.done()

        await mgr.stop(bus)
        assert mgr._task.done()
        assert bus._unsubscribed == "notification-manager"

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        cfg = NotificationConfig(
            macos=ChannelConfig(enabled=False),
            bell=ChannelConfig(enabled=False),
            webhook=WebhookConfig(enabled=False),
        )
        mgr = NotificationManager(cfg)
        bus = _FakeEventBus()
        await mgr.stop(bus)
        assert bus._unsubscribed == "notification-manager"
