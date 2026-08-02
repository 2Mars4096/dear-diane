"""Tests for dan.notifications.macos — MacOSNotifier."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.notifications.macos import EVENT_TITLES, MacOSNotifier


def _make_config(event_types: list[str] | None = None) -> MagicMock:
    cfg = MagicMock()
    cfg.event_types = event_types if event_types is not None else [
        "run_completed", "run_failed", "human_input_needed"
    ]
    return cfg


def _make_event(event_type: str, **kwargs: Any) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "run_id": "r1",
        "workflow_name": "my-workflow",
        "data": {},
        **kwargs,
    }


# ── _build_body ───────────────────────────────────────────────────────────


class TestBuildBody:
    def test_run_completed_body(self):
        notifier = MacOSNotifier(_make_config())
        body = notifier._build_body("run_completed", "my-wf", _make_event("run_completed"))
        assert "my-wf" in body
        assert "successfully" in body

    def test_run_failed_body(self):
        event = _make_event("run_failed", data={"error": "timeout"})
        notifier = MacOSNotifier(_make_config())
        body = notifier._build_body("run_failed", "wf1", event)
        assert "wf1" in body
        assert "timeout" in body

    def test_run_failed_body_unknown_error(self):
        event = _make_event("run_failed", data={})
        notifier = MacOSNotifier(_make_config())
        body = notifier._build_body("run_failed", "wf1", event)
        assert "unknown error" in body

    def test_human_input_needed_body(self):
        event = _make_event("human_input_needed", data={"prompt": "Please review"})
        notifier = MacOSNotifier(_make_config())
        body = notifier._build_body("human_input_needed", "wf1", event)
        assert "Please review" in body

    def test_human_input_default_prompt(self):
        event = _make_event("human_input_needed", data={})
        notifier = MacOSNotifier(_make_config())
        body = notifier._build_body("human_input_needed", "wf1", event)
        assert "Input required" in body

    def test_unknown_event_type_body(self):
        notifier = MacOSNotifier(_make_config())
        body = notifier._build_body("custom_event", "wf1", _make_event("custom_event"))
        assert "custom_event" in body


# ── Event titles ──────────────────────────────────────────────────────────


class TestEventTitles:
    def test_all_notification_events_have_titles(self):
        for et in ("run_completed", "run_failed", "human_input_needed"):
            assert et in EVENT_TITLES

    def test_titles_contain_dan(self):
        for title in EVENT_TITLES.values():
            assert "DAN" in title


# ── notify filtering ─────────────────────────────────────────────────────


class TestNotifyFiltering:
    @pytest.mark.asyncio
    async def test_skips_non_darwin(self):
        notifier = MacOSNotifier(_make_config())
        with patch("dan.notifications.macos.sys") as mock_sys:
            mock_sys.platform = "linux"
            notifier._send = AsyncMock()
            await notifier.notify(_make_event("run_completed"))
        notifier._send.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_filtered_event_types(self):
        cfg = _make_config(event_types=["run_failed"])
        notifier = MacOSNotifier(cfg)
        notifier._send = AsyncMock()
        with patch("dan.notifications.macos.sys") as mock_sys:
            mock_sys.platform = "darwin"
            await notifier.notify(_make_event("run_completed"))
        notifier._send.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_matching_event(self):
        notifier = MacOSNotifier(_make_config())
        notifier._send = AsyncMock()
        with patch("dan.notifications.macos.sys") as mock_sys:
            mock_sys.platform = "darwin"
            await notifier.notify(_make_event("run_completed"))
        notifier._send.assert_called_once()


# ── _send ─────────────────────────────────────────────────────────────────


class TestSend:
    @pytest.mark.asyncio
    async def test_uses_terminal_notifier_when_available(self):
        notifier = MacOSNotifier(_make_config())
        notifier._has_terminal_notifier = True
        captured_cmd: list[str] = []

        async def fake_to_thread(fn, cmd, **kwargs):
            captured_cmd.extend(cmd)

        with patch("dan.notifications.macos.asyncio.to_thread", side_effect=fake_to_thread):
            await notifier._send("Title", "Body")
        assert captured_cmd[0] == "terminal-notifier"
        assert "-title" in captured_cmd
        assert "Title" in captured_cmd

    @pytest.mark.asyncio
    async def test_falls_back_to_osascript(self):
        notifier = MacOSNotifier(_make_config())
        notifier._has_terminal_notifier = False
        captured_cmd: list[str] = []

        async def fake_to_thread(fn, cmd, **kwargs):
            captured_cmd.extend(cmd)

        with patch("dan.notifications.macos.asyncio.to_thread", side_effect=fake_to_thread):
            await notifier._send("Title", "Body")
        assert captured_cmd[0] == "osascript"
        assert "-e" in captured_cmd

    @pytest.mark.asyncio
    async def test_send_handles_exception_gracefully(self):
        notifier = MacOSNotifier(_make_config())
        notifier._has_terminal_notifier = False
        with patch("dan.notifications.macos.asyncio.to_thread", side_effect=OSError("fail")):
            await notifier._send("Title", "Body")
