from __future__ import annotations

from types import SimpleNamespace

from dan.agent_runtime.synthesis import (
    deterministic_synthesis_gap_review,
    parse_subtask_decomposition_response,
    parse_synthesis_review_response,
)


class _Manager:
    def __init__(self, children):
        self._children = dict(children)

    def get(self, child_id):
        return self._children[child_id]


def test_parse_subtask_decomposition_response_accepts_json_object_shape() -> None:
    content = '{"subtasks":["Search the docs","Summarize the findings"]}'

    parsed = parse_subtask_decomposition_response(
        content,
        fallback_task="Fallback task",
    )

    assert parsed == ["Search the docs", "Summarize the findings"]


def test_parse_subtask_decomposition_response_accepts_bullet_fallback() -> None:
    content = "- Search the docs\n- Summarize the findings"

    parsed = parse_subtask_decomposition_response(
        content,
        fallback_task="Fallback task",
    )

    assert parsed == ["Search the docs", "Summarize the findings"]


def test_parse_synthesis_review_response_accepts_fenced_json() -> None:
    content = '```json\n{"decision":"remediate","reason":"child output is incomplete"}\n```'

    decision, reason = parse_synthesis_review_response(content)

    assert decision == "remediate"
    assert reason == "child output is incomplete"


def test_deterministic_synthesis_gap_review_flags_missing_planned_subtasks() -> None:
    session = SimpleNamespace(
        task="Research the API and summarize it",
        triage=SimpleNamespace(
            subtasks=["Search the docs", "Summarize the findings"],
            goal="Research the API and summarize it",
            deliverable="A concise summary",
        ),
    )
    manager = _Manager(
        {
            "child-1": SimpleNamespace(task="Search the docs"),
        }
    )
    child_results = {
        "child-1": SimpleNamespace(content="Found the API reference.", error=""),
    }

    review = deterministic_synthesis_gap_review(session, child_results, manager)

    assert review.hard_gap_reason == "planned subtasks were not completed: Summarize the findings"


def test_deterministic_synthesis_gap_review_flags_unresolved_followup_signals() -> None:
    session = SimpleNamespace(
        task="Review the migration and finalize the report",
        triage=SimpleNamespace(
            subtasks=[],
            goal="Review the migration and finalize the report",
            deliverable="Final migration report",
        ),
    )
    manager = _Manager(
        {
            "child-1": SimpleNamespace(task="Review the migration"),
            "child-2": SimpleNamespace(task="Finalize the report"),
        }
    )
    child_results = {
        "child-1": SimpleNamespace(content="Migration review done. TODO: verify rollback path.", error=""),
        "child-2": SimpleNamespace(content="Final report drafted with remaining next steps.", error=""),
    }

    review = deterministic_synthesis_gap_review(session, child_results, manager)

    assert review.hard_gap_reason is None
    assert review.ambiguous_gap_reason is not None
    assert "unresolved follow-up work" in review.ambiguous_gap_reason
