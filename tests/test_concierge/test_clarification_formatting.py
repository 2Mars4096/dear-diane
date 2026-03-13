"""Tests for clarification formatting (31-25 §4-1)."""

from __future__ import annotations

from dan.server.concierge.runtime import _format_clarification_text


class TestFormatClarificationText:
    """Verify _format_clarification_text renders options as a numbered list."""

    def test_with_options_renders_numbered_list(self):
        question = "Which file should I use?"
        options = ["src/main.py", "src/utils.py", "src/app.py"]
        result = _format_clarification_text(question, options)
        assert result == (
            "Which file should I use?\n"
            "\n"
            "1. src/main.py\n"
            "2. src/utils.py\n"
            "3. src/app.py"
        )

    def test_without_options_returns_plain_question(self):
        question = "Could you clarify what you mean?"
        assert _format_clarification_text(question, None) == question

    def test_empty_options_returns_plain_question(self):
        assert _format_clarification_text("Question?", []) == "Question?"

    def test_numbered_format_blank_line_after_question(self):
        result = _format_clarification_text("Pick one:", ["A", "B"])
        lines = result.split("\n")
        assert lines[0] == "Pick one:"
        assert lines[1] == ""
        assert lines[2] == "1. A"
        assert lines[3] == "2. B"
        assert len(lines) == 4

    def test_single_option(self):
        result = _format_clarification_text("Confirm?", ["Yes"])
        assert "1. Yes" in result
        assert result.startswith("Confirm?\n\n")

    def test_many_options_numbering(self):
        options = [f"Option {c}" for c in "ABCDEFGHIJ"]
        result = _format_clarification_text("Choose:", options)
        for i, opt in enumerate(options, 1):
            assert f"{i}. {opt}" in result
