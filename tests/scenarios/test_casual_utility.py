"""Scenario E: Casual & Utility Tasks (Mixed).

Validates 8 quick tasks in one conversation thread:
1. current_datetime for date query
2. Pure text generation (no tools) for greeting
3. web_search for video links
4. shell_command for calculation
5. clipboard write
6. Pure text (no tools) for translation
7. send_email for reminder
8. screenshot capture

Each simple task should use at most 1 tool call.
"""

import pytest
from tests.scenarios.conftest import ToolCallTracker, ScenarioTurn


class TestCasualUtilityScenario:
    """Validate that simple tasks stay fast and use minimal tools."""

    def test_datetime_query(self, tool_tracker):
        """'What day is it today?' should use exactly current_datetime."""
        tool_tracker.record("current_datetime", {}, "2026-03-09T14:30:00-05:00")

        assert tool_tracker.count("current_datetime") == 1
        assert len(tool_tracker.calls) == 1, "Date query needs exactly 1 tool call"

    def test_birthday_greeting_no_tools(self):
        """A greeting request is pure text generation — no tool calls needed."""
        turn = ScenarioTurn(
            user_message="Write me a birthday greeting for my colleague Sarah who loves hiking",
            expected_tool_calls=[],
        )
        assert len(turn.expected_tool_calls) == 0, "Greeting should need zero tool calls"

    def test_cat_video_search(self, tool_tracker):
        """'Find me funny cat videos' should trigger exactly 1 web_search."""
        tool_tracker.record(
            "web_search",
            {"query": "funny cat videos 2026"},
            '{"results": [{"title": "Cats compilation", "url": "https://youtube.com/watch?v=abc"}]}',
        )

        assert tool_tracker.count("web_search") == 1
        assert len(tool_tracker.calls) == 1, "Simple search needs exactly 1 tool call"

    def test_calculation_via_shell(self, tool_tracker):
        """Compound interest should be calculated via shell_command, not mental math."""
        tool_tracker.record(
            "shell_command",
            {"command": 'python3 -c "print(round(10000 * 1.15**7, 2))"'},
            "26600.20",
        )

        assert tool_tracker.count("shell_command") == 1
        result = tool_tracker.calls_for("shell_command")[0]["result"]
        assert "26600" in result

    def test_clipboard_write(self, tool_tracker):
        """'Copy that number' should trigger exactly 1 clipboard call."""
        tool_tracker.record(
            "clipboard",
            {"action": "write", "content": "26600.20"},
            "Copied to clipboard",
        )

        assert tool_tracker.count("clipboard") == 1
        assert len(tool_tracker.calls) == 1

    def test_translation_no_tools(self):
        """Translation is pure text — no tools needed."""
        turn = ScenarioTurn(
            user_message="Translate 'the meeting is postponed to next week' to Chinese",
            expected_tool_calls=[],
        )
        assert len(turn.expected_tool_calls) == 0

    def test_send_email(self, tool_tracker):
        """Email reminder should trigger exactly 1 send_email call."""
        tool_tracker.record(
            "send_email",
            {
                "to": "sarah@example.com",
                "subject": "Team lunch reminder",
                "body": "Hi Sarah, just a reminder about the team lunch tomorrow!",
            },
            "Email sent successfully",
        )

        assert tool_tracker.count("send_email") == 1
        email_call = tool_tracker.calls_for("send_email")[0]
        assert email_call["args"]["to"] == "sarah@example.com"

    def test_screenshot(self, tool_tracker):
        """Screenshot should trigger exactly 1 screenshot call."""
        tool_tracker.record(
            "screenshot",
            {},
            "Screenshot saved to ~/.dan/screenshots/2026-03-09_143000.png",
        )

        assert tool_tracker.count("screenshot") == 1
        assert len(tool_tracker.calls) == 1

    def test_each_task_uses_at_most_one_tool(self):
        """Each of the 8 tasks should use 0 or 1 tool calls."""
        tasks = [
            ScenarioTurn("What day is it?", expected_tool_calls=["current_datetime"]),
            ScenarioTurn("Write a birthday greeting for Sarah", expected_tool_calls=[]),
            ScenarioTurn("Find funny cat videos", expected_tool_calls=["web_search"]),
            ScenarioTurn("15% compound growth on $10k over 7 years?", expected_tool_calls=["shell_command"]),
            ScenarioTurn("Copy that number to clipboard", expected_tool_calls=["clipboard"]),
            ScenarioTurn("Translate to Chinese: 'meeting postponed'", expected_tool_calls=[]),
            ScenarioTurn("Email sarah@example.com about lunch", expected_tool_calls=["send_email"]),
            ScenarioTurn("Take a screenshot", expected_tool_calls=["screenshot"]),
        ]

        for task in tasks:
            assert len(task.expected_tool_calls) <= 1, (
                f"Task '{task.user_message}' should need at most 1 tool, "
                f"got {len(task.expected_tool_calls)}"
            )

    def test_full_conversation_tool_count(self, tool_tracker):
        """Across all 8 tasks, total tool calls should be exactly 6 (2 are text-only)."""
        tool_calls_per_task = [
            ("current_datetime", {}, "2026-03-09"),
            ("web_search", {"query": "funny cat videos"}, '{"results": []}'),
            ("shell_command", {"command": "python3 -c ..."}, "26600.20"),
            ("clipboard", {"action": "write", "content": "26600.20"}, "Copied"),
            ("send_email", {"to": "sarah@example.com"}, "Sent"),
            ("screenshot", {}, "Screenshot saved"),
        ]
        for name, args, result in tool_calls_per_task:
            tool_tracker.record(name, args, result)

        assert len(tool_tracker.calls) == 6, "6 tasks need tools, 2 are text-only"
        unique_tools = set(tool_tracker.tool_names)
        expected_tools = {"current_datetime", "web_search", "shell_command", "clipboard", "send_email", "screenshot"}
        assert unique_tools == expected_tools
