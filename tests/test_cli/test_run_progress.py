"""Tests for dan.cli.run_progress — streaming run progress tracker."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from dan.cli.run_progress import RunProgressTracker, _format_duration


# ---------------------------------------------------------------------------
# _format_duration tests
# ---------------------------------------------------------------------------

class TestFormatDuration:
    def test_milliseconds(self):
        assert _format_duration(0.123) == "123ms"

    def test_seconds(self):
        assert _format_duration(5.3) == "5.3s"

    def test_minutes(self):
        assert _format_duration(125.0) == "2m5s"

    def test_zero(self):
        assert _format_duration(0.0) == "0ms"

    def test_sub_millisecond(self):
        result = _format_duration(0.0004)
        assert "ms" in result


# ---------------------------------------------------------------------------
# RunProgressTracker tests
# ---------------------------------------------------------------------------

class TestRunProgressTracker:
    def test_node_started_event(self):
        tracker = RunProgressTracker()
        line = tracker.update({
            "event_type": "node_started",
            "node_id": "n1",
            "node_name": "Research",
        })
        assert line is not None
        assert "Research" in line

    def test_node_completed_event(self):
        tracker = RunProgressTracker()
        tracker.update({
            "event_type": "node_started",
            "node_id": "n1",
            "node_name": "Research",
        })
        time.sleep(0.01)
        line = tracker.update({
            "event_type": "node_completed",
            "node_id": "n1",
            "node_name": "Research",
        })
        assert line is not None
        assert "Research" in line
        # Should contain a duration
        assert "ms" in line or "s" in line

    def test_node_failed_event(self):
        tracker = RunProgressTracker()
        tracker.update({
            "event_type": "node_started",
            "node_id": "n1",
            "node_name": "Broken",
        })
        line = tracker.update({
            "event_type": "node_failed",
            "node_id": "n1",
            "node_name": "Broken",
            "error": "timeout",
        })
        assert line is not None
        assert "Broken" in line
        assert "timeout" in line

    def test_node_skipped_event(self):
        tracker = RunProgressTracker()
        line = tracker.update({
            "event_type": "node_skipped",
            "node_id": "n1",
            "node_name": "Optional",
        })
        assert line is not None
        assert "Optional" in line
        assert "skipped" in line

    def test_run_completed_returns_none(self):
        tracker = RunProgressTracker()
        line = tracker.update({"event_type": "run_completed"})
        assert line is None

    def test_run_failed_returns_none(self):
        tracker = RunProgressTracker()
        line = tracker.update({"event_type": "run_failed", "error": "oom"})
        assert line is None

    def test_unknown_event_returns_none(self):
        tracker = RunProgressTracker()
        line = tracker.update({"event_type": "something_else"})
        assert line is None

    def test_automatic_recovery_started_event(self):
        tracker = RunProgressTracker()
        line = tracker.update(
            {
                "event_type": "automatic_recovery_started",
                "data": {"selected_action": "rerun_from_checkpoint"},
            }
        )
        assert line is not None
        assert "Auto-repair started" in line
        assert "rerun_from_checkpoint" in line

    def test_automatic_recovery_completed_event(self):
        tracker = RunProgressTracker()
        line = tracker.update(
            {
                "event_type": "automatic_recovery_completed",
                "data": {
                    "status": "exhausted",
                    "escalation_summary": "Manual review required.",
                },
            }
        )
        assert line is not None
        assert "Auto-repair exhausted" in line
        assert "Manual review required." in line

    def test_recovery_child_node_event_is_labeled(self):
        tracker = RunProgressTracker()
        line = tracker.update(
            {
                "event_type": "node_started",
                "node_id": "n1",
                "node_name": "RepairTask",
                "data": {"automatic_recovery": True, "recovery_run_id": "rerun-1"},
            }
        )
        assert line is not None
        assert "Auto-repair rerun: RepairTask" in line

    def test_finish_summary_completed(self):
        tracker = RunProgressTracker()
        tracker.update({"event_type": "node_started", "node_id": "n1", "node_name": "A"})
        tracker.update({"event_type": "node_completed", "node_id": "n1", "node_name": "A"})
        tracker.update({"event_type": "node_started", "node_id": "n2", "node_name": "B"})
        tracker.update({"event_type": "node_completed", "node_id": "n2", "node_name": "B"})
        tracker.update({"event_type": "run_completed"})

        summary = tracker.finish()
        assert "completed" in summary.lower()
        assert "2/2" in summary
        assert "Total time:" in summary

    def test_finish_summary_with_failures(self):
        tracker = RunProgressTracker()
        tracker.update({"event_type": "node_started", "node_id": "n1", "node_name": "A"})
        tracker.update({"event_type": "node_completed", "node_id": "n1", "node_name": "A"})
        tracker.update({"event_type": "node_started", "node_id": "n2", "node_name": "B"})
        tracker.update({"event_type": "node_failed", "node_id": "n2", "node_name": "B"})
        tracker.update({"event_type": "node_skipped", "node_id": "n3", "node_name": "C"})
        tracker.update({"event_type": "run_failed", "error": "node B failed"})

        summary = tracker.finish()
        assert "failed" in summary.lower()
        assert "1/3" in summary
        assert "1 failed" in summary
        assert "1 skipped" in summary

    def test_finish_includes_avg_time(self):
        tracker = RunProgressTracker()
        tracker.update({"event_type": "node_started", "node_id": "n1", "node_name": "A"})
        tracker.update({"event_type": "node_completed", "node_id": "n1", "node_name": "A"})
        tracker.update({"event_type": "run_completed"})
        summary = tracker.finish()
        assert "Avg node time:" in summary

    def test_full_sequence(self):
        """End-to-end: 3-node pipeline with one failure."""
        tracker = RunProgressTracker()

        lines = []
        events = [
            {"event_type": "node_started", "node_id": "n1", "node_name": "Gather"},
            {"event_type": "node_completed", "node_id": "n1", "node_name": "Gather"},
            {"event_type": "node_started", "node_id": "n2", "node_name": "Analyze"},
            {"event_type": "node_failed", "node_id": "n2", "node_name": "Analyze", "error": "API error"},
            {"event_type": "node_skipped", "node_id": "n3", "node_name": "Report"},
            {"event_type": "run_failed", "error": "pipeline failed"},
        ]
        for ev in events:
            line = tracker.update(ev)
            if line is not None:
                lines.append(line)

        assert len(lines) == 5  # started(Gather), completed(Gather), started(Analyze), failed(Analyze), skipped(Report)
        summary = tracker.finish()
        assert "1/3 completed" in summary
        assert "1 failed" in summary
        assert "1 skipped" in summary


# ---------------------------------------------------------------------------
# Unicode icon tests
# ---------------------------------------------------------------------------

class TestProgressIcons:
    def test_unicode_icons_when_supported(self):
        with patch("dan.cli.run_progress._supports_unicode", return_value=True):
            tracker = RunProgressTracker()
            line = tracker.update({
                "event_type": "node_started",
                "node_id": "n1",
                "node_name": "Test",
            })
            assert "⏳" in line

    def test_ascii_icons_fallback(self):
        with patch("dan.cli.run_progress._supports_unicode", return_value=False):
            tracker = RunProgressTracker()
            line = tracker.update({
                "event_type": "node_started",
                "node_id": "n1",
                "node_name": "Test",
            })
            assert "[..]" in line
