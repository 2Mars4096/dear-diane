"""Unit tests for TUI display classes (Rich and plain fallback)."""

from __future__ import annotations

import json
import time
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from dan.cli.run import (
    JSONLDisplay,
    PlainDisplay,
    QuietDisplay,
    make_display,
)
from dan.engine.events import EngineEvent, EventType


def _make_event(event_type: EventType, node_id: str = "", data: dict | None = None) -> EngineEvent:
    return EngineEvent(
        event_type=event_type,
        run_id="test-run",
        node_id=node_id,
        data=data or {},
    )


class TestPlainDisplay:
    def test_start_prints_message(self, capsys):
        d = PlainDisplay()
        d.start(5)
        captured = capsys.readouterr()
        assert "5 nodes" in captured.err

    def test_node_completed(self, capsys):
        d = PlainDisplay()
        d.start(3)
        d.handle_event(_make_event(EventType.NODE_COMPLETED, "n1"))
        captured = capsys.readouterr()
        assert "n1" in captured.err
        assert "completed" in captured.err

    def test_node_failed(self, capsys):
        d = PlainDisplay()
        d.start(3)
        d.handle_event(_make_event(EventType.NODE_FAILED, "n2", {"error": "boom"}))
        captured = capsys.readouterr()
        assert "FAILED" in captured.err

    def test_verbose_shows_all_events(self, capsys):
        d = PlainDisplay(verbose=True)
        d.start(1)
        d.handle_event(_make_event(EventType.LLM_THINKING, "n1"))
        captured = capsys.readouterr()
        assert "llm_thinking" in captured.err

    def test_automatic_recovery_event_is_printed(self, capsys):
        d = PlainDisplay()
        d.start(1)
        d.handle_event(
            _make_event(
                EventType.AUTOMATIC_RECOVERY_STARTED,
                data={"selected_action": "rerun_from_checkpoint"},
            )
        )
        captured = capsys.readouterr()
        assert "auto-repair started" in captured.err
        assert "rerun_from_checkpoint" in captured.err

    def test_automatic_recovery_exhausted_prints_escalation_summary(self, capsys):
        d = PlainDisplay()
        d.start(1)
        d.handle_event(
            _make_event(
                EventType.AUTOMATIC_RECOVERY_COMPLETED,
                data={"status": "exhausted", "escalation_summary": "Manual review required."},
            )
        )
        captured = capsys.readouterr()
        assert "auto-repair exhausted" in captured.err
        assert "Manual review required." in captured.err

    def test_recovery_child_node_completion_is_labeled(self, capsys):
        d = PlainDisplay()
        d.start(1)
        d.handle_event(
            _make_event(
                EventType.NODE_COMPLETED,
                "task",
                {"automatic_recovery": True, "recovery_run_id": "rerun-1"},
            )
        )
        captured = capsys.readouterr()
        assert "auto-repair task completed" in captured.err

    def test_summary(self, capsys):
        d = PlainDisplay()
        d.start(1)
        result = MagicMock(success=True, errors={})
        d.print_summary(result)
        captured = capsys.readouterr()
        assert "SUCCESS" in captured.err


class TestQuietDisplay:
    def test_no_output(self, capsys):
        d = QuietDisplay()
        d.start(5)
        d.handle_event(_make_event(EventType.NODE_COMPLETED, "n1"))
        d.stop()
        result = MagicMock(success=True, errors={})
        d.print_summary(result)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


class TestJSONLDisplay:
    def test_outputs_jsonl(self, capsys):
        d = JSONLDisplay()
        d.start(1)
        ev = _make_event(EventType.NODE_COMPLETED, "n1", {"foo": "bar"})
        d.handle_event(ev)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["event_type"] == "node_completed"
        assert parsed["node_id"] == "n1"
        assert parsed["data"]["foo"] == "bar"


class TestMakeDisplay:
    def test_json_format_returns_jsonl(self):
        d = make_display(quiet=False, verbose=False, output_format="json")
        assert isinstance(d, JSONLDisplay)

    def test_quiet_returns_quiet(self):
        d = make_display(quiet=True, verbose=False, output_format="text")
        assert isinstance(d, QuietDisplay)

    def test_no_rich_returns_plain(self):
        with patch.dict("sys.modules", {"rich": None, "rich.console": None}):
            from dan.cli.run import _try_import_rich
            with patch("dan.cli.run._try_import_rich", return_value=(None, None)):
                d = make_display(quiet=False, verbose=False, output_format="text")
                assert isinstance(d, PlainDisplay)


class TestTUIDisplay:
    """Tests for Rich-based TUI (only run if rich is available)."""

    @pytest.fixture(autouse=True)
    def _skip_no_rich(self):
        try:
            import rich
        except ImportError:
            pytest.skip("rich not installed")

    def test_start_stop(self):
        from dan.cli.run import TUIDisplay
        d = TUIDisplay()
        d.start(3)
        assert d._live is not None
        d.stop()
        assert d._live is None

    def test_node_tracking(self):
        from dan.cli.run import TUIDisplay
        d = TUIDisplay()
        d.start(2)
        d.handle_event(_make_event(EventType.NODE_STARTED, "n1", {"name": "Analyze", "node_type": "llm_operator"}))
        assert "n1" in d._nodes
        assert d._nodes["n1"]["status"] == "running"

        d.handle_event(_make_event(EventType.NODE_COMPLETED, "n1"))
        assert d._nodes["n1"]["status"] == "completed"
        assert d._completed_count == 1
        d.stop()

    def test_cost_tracking(self):
        from dan.cli.run import TUIDisplay
        d = TUIDisplay()
        d.start(1)
        d.handle_event(_make_event(EventType.NODE_STARTED, "n1", {"name": "n1"}))
        d.handle_event(_make_event(EventType.COST_RECORDED, "n1", {"total_tokens": 500, "cost": 0.01}))
        assert d._total_tokens == 500
        assert d._total_cost == 0.01
        d.stop()
