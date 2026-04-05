from __future__ import annotations

from dan.server.concierge.followup_classifier import FollowUpType, classify_follow_up
from dan.server.concierge.models import PendingAction, TaskTurn
from dan.server.concierge.pending_actions import resolve_pending_reply
from dan.server.concierge.task_registry import ConciergeTask, DispatchMode, TaskState


def _concierge_task(task_id: str, title: str, *, summary: str = "", state: TaskState = TaskState.RUNNING) -> ConciergeTask:
    return ConciergeTask(
        task_id=task_id,
        project_id="proj-1",
        title=title,
        summary=summary,
        state=state,
        creator_surface="cli",
        dispatch_mode=DispatchMode.BACKGROUND,
        metadata={"original_text": summary or title},
    )


def test_pending_reply_metadata_keeps_task_scope() -> None:
    pending = PendingAction(
        kind="clarify",
        intent="ask",
        original_text="Pick the file to continue",
        task_id="task_alpha123456",
        attempt_session_id="session-1",
        replay_context=[
            TaskTurn(role="user", content="Update the README"),
            TaskTurn(role="assistant", content="I found multiple files"),
        ],
    )

    resolution = resolve_pending_reply(pending, "use README.md")

    assert resolution.action == "resume"
    assert resolution.metadata["pending_task_id"] == "task_alpha123456"
    assert resolution.metadata["pending_attempt_session_id"] == "session-1"


def test_followup_classifier_prefers_waiting_input_task() -> None:
    waiting = _concierge_task(
        "task_wait123456",
        "Update README links",
        state=TaskState.WAITING_INPUT,
    )

    decision = classify_follow_up(
        "use README.md",
        tasks=[waiting],
        waiting_task_id=waiting.task_id,
    )

    assert decision.follow_up_type == FollowUpType.ANSWER_CLARIFICATION
    assert decision.task_id == waiting.task_id


def test_followup_classifier_distinguishes_retry_and_supersede() -> None:
    tasks = [
        _concierge_task(
            "task_retry12345a",
            "Fix flaky checkout tests",
            summary="fix flaky checkout tests in CI",
        )
    ]

    retry = classify_follow_up("retry task_retry12345a", tasks=tasks)
    supersede = classify_follow_up(
        "Actually task_retry12345a should update the docs instead",
        tasks=tasks,
    )

    assert retry.follow_up_type == FollowUpType.RETRY_TASK
    assert retry.task_id == "task_retry12345a"
    assert supersede.follow_up_type == FollowUpType.SUPERSEDE_TASK
    assert supersede.task_id == "task_retry12345a"


def test_followup_classifier_uses_similarity_for_refinement() -> None:
    tasks = [
        _concierge_task(
            "task_debug123456",
            "Debug flaky checkout tests",
            summary="investigate flaky checkout test failure in CI",
        ),
        _concierge_task(
            "task_docs123456",
            "Refresh onboarding docs",
            summary="refresh onboarding docs for the release",
        ),
    ]

    decision = classify_follow_up(
        "continue debugging the flaky checkout test failure",
        tasks=tasks,
    )

    assert decision.follow_up_type == FollowUpType.REFINE_TASK
    assert decision.task_id == "task_debug123456"


def test_followup_classifier_requests_disambiguation_for_ambiguous_retry() -> None:
    tasks = [
        _concierge_task("task_sales12345", "Prepare weekly sales report"),
        _concierge_task("task_finan12345", "Prepare weekly finance report"),
    ]

    decision = classify_follow_up(
        "retry the weekly report",
        tasks=tasks,
    )

    assert decision.follow_up_type == FollowUpType.RETRY_TASK
    assert decision.requires_disambiguation is True
    assert decision.candidate_task_ids == ["task_sales12345", "task_finan12345"]
    assert "Which task did you mean?" in str(decision.prompt)
