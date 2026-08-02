"""Tests for dan.notifications.terminal — bell helpers + TerminalBellNotifier."""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dan.notifications.terminal import (
    BELL_EVENTS,
    TerminalBellNotifier,
    maybe_ring_on_event,
    ring_bell,
    should_ring_bell,
)


# ── should_ring_bell ──────────────────────────────────────────────────────


class TestShouldRingBell:
    def test_enabled_with_1(self):
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": "1"}):
            assert should_ring_bell() is True

    def test_enabled_with_true(self):
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": "true"}):
            assert should_ring_bell() is True

    def test_enabled_with_yes(self):
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": "yes"}):
            assert should_ring_bell() is True

    def test_enabled_case_insensitive(self):
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": "TRUE"}):
            assert should_ring_bell() is True

    def test_disabled_with_0(self):
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": "0"}):
            assert should_ring_bell() is False

    def test_disabled_when_unset(self):
        env = {k: v for k, v in __import__("os").environ.items() if k != "DAN_NOTIFY_BELL"}
        with patch.dict("os.environ", env, clear=True):
            assert should_ring_bell() is False

    def test_disabled_with_empty(self):
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": ""}):
            assert should_ring_bell() is False


# ── ring_bell ─────────────────────────────────────────────────────────────


class TestRingBell:
    def test_writes_bel_when_enabled(self):
        fake_stderr = io.StringIO()
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": "1"}):
            with patch("dan.notifications.terminal.sys.stderr", fake_stderr):
                ring_bell()
        assert fake_stderr.getvalue() == "\a"

    def test_no_write_when_disabled(self):
        fake_stderr = io.StringIO()
        env = {k: v for k, v in __import__("os").environ.items() if k != "DAN_NOTIFY_BELL"}
        with patch.dict("os.environ", env, clear=True):
            with patch("dan.notifications.terminal.sys.stderr", fake_stderr):
                ring_bell()
        assert fake_stderr.getvalue() == ""

    def test_handles_write_error(self):
        mock_stderr = MagicMock()
        mock_stderr.write.side_effect = OSError("broken pipe")
        with patch.dict("os.environ", {"DAN_NOTIFY_BELL": "1"}):
            with patch("dan.notifications.terminal.sys.stderr", mock_stderr):
                ring_bell()


# ── maybe_ring_on_event ───────────────────────────────────────────────────


class TestMaybeRingOnEvent:
    def test_rings_on_run_completed(self):
        with patch("dan.notifications.terminal.ring_bell") as mock_bell:
            maybe_ring_on_event("run_completed")
        mock_bell.assert_called_once()

    def test_rings_on_run_failed(self):
        with patch("dan.notifications.terminal.ring_bell") as mock_bell:
            maybe_ring_on_event("run_failed")
        mock_bell.assert_called_once()

    def test_rings_on_human_input_needed(self):
        with patch("dan.notifications.terminal.ring_bell") as mock_bell:
            maybe_ring_on_event("human_input_needed")
        mock_bell.assert_called_once()

    def test_no_ring_on_other_events(self):
        with patch("dan.notifications.terminal.ring_bell") as mock_bell:
            maybe_ring_on_event("node_started")
        mock_bell.assert_not_called()

    def test_bell_events_constant(self):
        assert "run_completed" in BELL_EVENTS
        assert "run_failed" in BELL_EVENTS
        assert "human_input_needed" in BELL_EVENTS
        assert "log" not in BELL_EVENTS


# ── TerminalBellNotifier (channel adapter) ────────────────────────────────


class TestTerminalBellNotifier:
    @pytest.mark.asyncio
    async def test_notify_writes_bell(self):
        cfg = MagicMock()
        cfg.event_types = ["run_completed", "run_failed", "human_input_needed"]
        notifier = TerminalBellNotifier(cfg)
        fake_stderr = io.StringIO()
        with patch("dan.notifications.terminal.sys.stderr", fake_stderr):
            await notifier.notify({"event_type": "run_completed"})
        assert "\a" in fake_stderr.getvalue()

    @pytest.mark.asyncio
    async def test_notify_skips_filtered_events(self):
        cfg = MagicMock()
        cfg.event_types = ["run_failed"]
        notifier = TerminalBellNotifier(cfg)
        fake_stderr = io.StringIO()
        with patch("dan.notifications.terminal.sys.stderr", fake_stderr):
            await notifier.notify({"event_type": "run_completed"})
        assert fake_stderr.getvalue() == ""

    @pytest.mark.asyncio
    async def test_notify_handles_write_error(self):
        cfg = MagicMock()
        cfg.event_types = ["run_completed"]
        notifier = TerminalBellNotifier(cfg)
        mock_stderr = MagicMock()
        mock_stderr.write.side_effect = OSError("broken")
        with patch("dan.notifications.terminal.sys.stderr", mock_stderr):
            await notifier.notify({"event_type": "run_completed"})
