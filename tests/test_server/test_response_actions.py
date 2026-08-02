"""Tests for Plan 28-3: response actions (file delivery, message splitting, claim validation)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from dan.server.concierge.actions import (
    check_unsourced_claims,
    extract_file_paths_from_tool_results,
    split_message_for_surface,
)


# ---------------------------------------------------------------------------
# extract_file_paths_from_tool_results
# ---------------------------------------------------------------------------


class TestExtractFilePaths:
    def test_extracts_existing_file(self, tmp_path: Path) -> None:
        f = tmp_path / "report.csv"
        f.write_text("a,b,c")
        results = [{"data": {"path": str(f), "size": 5}}]
        assert extract_file_paths_from_tool_results(results) == [str(f)]

    def test_skips_nonexistent_path(self) -> None:
        results = [{"data": {"path": "/no/such/file.txt"}}]
        assert extract_file_paths_from_tool_results(results) == []

    def test_skips_missing_data_key(self) -> None:
        results = [{"message": "ok"}]
        assert extract_file_paths_from_tool_results(results) == []

    def test_skips_non_dict_data(self) -> None:
        results = [{"data": "just a string"}]
        assert extract_file_paths_from_tool_results(results) == []

    def test_deduplicates(self, tmp_path: Path) -> None:
        f = tmp_path / "dup.txt"
        f.write_text("x")
        results = [
            {"data": {"path": str(f)}},
            {"data": {"path": str(f)}},
        ]
        assert extract_file_paths_from_tool_results(results) == [str(f)]

    def test_multiple_files(self, tmp_path: Path) -> None:
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("1")
        b.write_text("2")
        results = [
            {"data": {"path": str(a)}},
            {"data": {"path": str(b)}},
        ]
        paths = extract_file_paths_from_tool_results(results)
        assert paths == [str(a), str(b)]

    def test_empty_list(self) -> None:
        assert extract_file_paths_from_tool_results([]) == []


# ---------------------------------------------------------------------------
# split_message_for_surface
# ---------------------------------------------------------------------------


class TestSplitMessageForSurface:
    def test_short_message_no_split(self) -> None:
        text = "Hello world"
        assert split_message_for_surface(text, "whatsapp") == [text]

    def test_non_messaging_surface_no_split(self) -> None:
        text = "x" * 10000
        assert split_message_for_surface(text, "server") == [text]
        assert split_message_for_surface(text, "editor") == [text]
        assert split_message_for_surface(text, "cli") == [text]

    def test_split_at_paragraph_boundaries(self) -> None:
        para_a = "A" * 3000
        para_b = "B" * 3000
        text = f"{para_a}\n\n{para_b}"
        parts = split_message_for_surface(text, "whatsapp")
        assert len(parts) == 2
        assert parts[0] == para_a
        assert parts[1] == para_b

    def test_split_at_line_boundaries_when_paragraph_too_long(self) -> None:
        lines = [f"Line {i}: {'x' * 200}" for i in range(30)]
        text = "\n".join(lines)
        parts = split_message_for_surface(text, "whatsapp")
        assert len(parts) > 1
        for part in parts:
            assert len(part) <= 4096

    def test_custom_limit(self) -> None:
        text = "Hello\n\nWorld"
        parts = split_message_for_surface(text, "whatsapp", limit=6)
        assert len(parts) == 2

    def test_whatsapp_web_surface(self) -> None:
        text = "A" * 5000
        parts = split_message_for_surface(text, "whatsapp-web")
        assert len(parts) > 1

    def test_telegram_surface(self) -> None:
        text = "A" * 5000
        parts = split_message_for_surface(text, "telegram")
        assert len(parts) > 1

    def test_email_surface(self) -> None:
        text = "A" * 5000
        parts = split_message_for_surface(text, "email")
        assert len(parts) > 1

    def test_wechat_surface(self) -> None:
        text = "A" * 5000
        parts = split_message_for_surface(text, "wechat")
        assert len(parts) > 1

    def test_exact_limit_no_split(self) -> None:
        text = "A" * 4096
        parts = split_message_for_surface(text, "whatsapp")
        assert parts == [text]

    def test_empty_text(self) -> None:
        assert split_message_for_surface("", "whatsapp") == [""]


# ---------------------------------------------------------------------------
# check_unsourced_claims
# ---------------------------------------------------------------------------


class TestCheckUnsourcedClaims:
    def test_no_disclaimer_with_web_search(self) -> None:
        text = "The stock price is $150.25 per share."
        assert check_unsourced_claims(text, ["web_search"]) is None

    def test_disclaimer_without_web_search(self) -> None:
        text = "The stock price is $150.25 per share."
        result = check_unsourced_claims(text, [])
        assert result is not None
        assert "training data" in result.lower() or "training" in result.lower()

    def test_no_disclaimer_when_no_numeric_claims(self) -> None:
        text = "Python is a great language for data analysis."
        assert check_unsourced_claims(text, []) is None

    def test_disclaimer_with_percentage(self) -> None:
        text = "Revenue grew by 15.3% last quarter."
        result = check_unsourced_claims(text, ["file_read"])
        assert result is not None

    def test_no_disclaimer_with_empty_text(self) -> None:
        assert check_unsourced_claims("", []) is None

    def test_disclaimer_with_dollar_amount(self) -> None:
        text = "Apple's market cap reached $3 trillion."
        result = check_unsourced_claims(text, [])
        assert result is not None

    def test_web_search_among_other_tools(self) -> None:
        text = "The price is $99.99."
        assert check_unsourced_claims(text, ["file_read", "web_search", "shell_command"]) is None

    def test_price_context_pattern(self) -> None:
        text = "The closing price was high at $45."
        result = check_unsourced_claims(text, [])
        assert result is not None


# ---------------------------------------------------------------------------
# _consume_chat_stream_events (adapter integration)
# ---------------------------------------------------------------------------


class TestConsumeStreamEventsFileAttachment:
    def test_file_attachment_events_collected(self) -> None:
        from dan.cli.adapter import _consume_chat_stream_events

        events = [
            {"type": "chat_file_attachment", "path": "/tmp/report.csv", "filename": "report.csv", "size": 1024},
            {"type": "chat_complete", "content": "Here is your file."},
        ]
        full_reply, mutation, file_paths, _polls = _consume_chat_stream_events(events)
        assert full_reply == "Here is your file."
        assert file_paths == ["/tmp/report.csv"]
        assert mutation is None

    def test_no_file_attachment(self) -> None:
        from dan.cli.adapter import _consume_chat_stream_events

        events = [
            {"type": "chat_complete", "content": "Just text."},
        ]
        _, _, file_paths, _ = _consume_chat_stream_events(events)
        assert file_paths == []

    def test_multiple_file_attachments(self) -> None:
        from dan.cli.adapter import _consume_chat_stream_events

        events = [
            {"type": "chat_file_attachment", "path": "/a.txt", "filename": "a.txt", "size": 10},
            {"type": "chat_file_attachment", "path": "/b.txt", "filename": "b.txt", "size": 20},
            {"type": "chat_complete", "content": "Done."},
        ]
        _, _, file_paths, _ = _consume_chat_stream_events(events)
        assert file_paths == ["/a.txt", "/b.txt"]

    def test_progress_ack_is_not_treated_as_terminal_reply(self) -> None:
        from dan.cli.adapter import _consume_chat_stream_events

        events = [
            {
                "type": "chat_complete",
                "content": "Got it. what's the stock price of AAPL right now?",
                "detected_mode": "progress_ack",
            },
            {
                "type": "chat_complete",
                "content": "AAPL is trading at $123.45 right now.",
            },
        ]

        full_reply, mutation, file_paths, polls = _consume_chat_stream_events(events)

        assert full_reply == "AAPL is trading at $123.45 right now."
        assert mutation is None
        assert file_paths == []
        assert polls == []


# ---------------------------------------------------------------------------
# Event models
# ---------------------------------------------------------------------------


class TestEventModels:
    def test_file_attachment_event(self) -> None:
        from dan.server.chat_manager import ChatFileAttachmentEvent

        evt = ChatFileAttachmentEvent(path="/tmp/f.csv", filename="f.csv", size=512)
        assert evt.type == "chat_file_attachment"
        assert evt.path == "/tmp/f.csv"
        assert evt.filename == "f.csv"
        assert evt.size == 512

    def test_multi_part_event(self) -> None:
        from dan.server.chat_manager import ChatMultiPartEvent

        evt = ChatMultiPartEvent(parts=["Hello", "World"])
        assert evt.type == "chat_multi_part"
        assert evt.parts == ["Hello", "World"]
