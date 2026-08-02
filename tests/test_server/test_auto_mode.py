"""Tests for auto-mode detection and normalize_chat_mode."""

import pytest

from dan.server.chat_manager import (
    build_debug_context,
    detect_chat_mode,
    normalize_chat_mode,
    recent_run_failed_for_workflow,
)


# ---------------------------------------------------------------------------
# detect_chat_mode — fixture-driven
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message, recent_run_failed, expected",
    [
        # Debug patterns — non-question context
        ("There's an error in my workflow", False, "debug"),
        ("Fix the broken node", False, "debug"),
        ("My run crashed — not working at all", False, "debug"),
        ("wrong output from the summarizer", False, "debug"),
        ("this bug is blocking me", False, "debug"),
        ("the pipeline failed", False, "debug"),
        # recent_run_failed overrides everything
        ("add a new node", True, "debug"),
        ("tell me about the graph", True, "debug"),
        # Questions with error/fix words → ask (not debug)
        ("How do I fix the layout?", False, "ask"),
        ("What does this error mean?", False, "ask"),
        ("Why is the output empty?", False, "ask"),
        # Question patterns
        ("What does this node do?", False, "ask"),
        ("How does data flow here?", False, "ask"),
        ("Explain the summarizer node", False, "ask"),
        ("describe the topology", False, "ask"),
        ("Is this a good approach?", False, "ask"),
        # Plan patterns
        ("Plan a new data pipeline", False, "plan"),
        ("What's the best approach for this?", False, "ask"),  # question wins over plan keyword
        ("I need a strategy for scaling", False, "plan"),
        ("Propose a new architecture", False, "plan"),
        ("design a better flow", False, "plan"),
        # Default → agent
        ("Add a new LLM node after the summarizer", False, "agent"),
        ("Build a research workflow with three stages", False, "agent"),
        ("Connect the output port to the next node", False, "agent"),
        ("", False, "agent"),
    ],
)
def test_detect_chat_mode(message: str, recent_run_failed: bool, expected: str):
    result = detect_chat_mode(message, recent_run_failed=recent_run_failed)
    assert result == expected, f"detect_chat_mode({message!r}) = {result!r}, expected {expected!r}"


# ---------------------------------------------------------------------------
# Priority ordering: debug > ask > plan > agent
# ---------------------------------------------------------------------------

def test_question_with_error_routes_to_ask():
    """Questions containing 'error'/'fix' route to ask, not debug."""
    assert detect_chat_mode("Why is there an error?") == "ask"
    assert detect_chat_mode("How do I fix this?") == "ask"


def test_debug_takes_priority_with_strong_signal():
    """Non-question messages with debug keywords route to debug."""
    assert detect_chat_mode("Fix the broken node") == "debug"
    assert detect_chat_mode("The pipeline crashed again") == "debug"


def test_ask_takes_priority_over_plan():
    result = detect_chat_mode("What is the plan for this?")
    assert result == "ask"


# ---------------------------------------------------------------------------
# normalize_chat_mode
# ---------------------------------------------------------------------------

def test_normalize_auto_passes_through():
    assert normalize_chat_mode("auto") == "auto"


def test_normalize_legacy_aliases():
    assert normalize_chat_mode("build") == "agent"
    assert normalize_chat_mode("mutate") == "agent"


def test_normalize_canonical_modes_unchanged():
    for mode in ("ask", "agent", "plan", "debug"):
        assert normalize_chat_mode(mode) == mode


def test_normalize_unknown_mode_passes_through():
    assert normalize_chat_mode("future_mode") == "future_mode"


def test_recent_run_failed_for_workflow_uses_newest_matching_run():
    runs = [
        {"graph_id": "wf-1", "status": "completed", "started_at": 10},
        {"graph_id": "wf-1", "status": "failed", "started_at": 20},
        {"graph_id": "wf-2", "status": "failed", "started_at": 30},
    ]

    assert recent_run_failed_for_workflow(runs, "wf-1") is True


def test_recent_run_failed_for_workflow_ignores_older_failure_when_newer_run_succeeds():
    runs = [
        {"graph_id": "wf-1", "status": "failed", "started_at": 10},
        {"graph_id": "wf-1", "status": "completed", "started_at": 20},
    ]

    assert recent_run_failed_for_workflow(runs, "wf-1") is False


def test_build_debug_context_uses_newest_failed_run():
    runs = [
        {"graph_id": "wf-1", "run_id": "newer", "status": "failed", "started_at": 20},
        {"graph_id": "wf-1", "run_id": "older", "status": "failed", "started_at": 10},
    ]

    context = build_debug_context(runs, "wf-1")

    assert "Last failed run: newer" in context
    assert "Last failed run: older" not in context
