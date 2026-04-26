from __future__ import annotations

from dan.cli.task_lanes import classify_code_task_lane, classify_research_task_lane


def test_classify_code_task_lane_marks_bounded_review_fast() -> None:
    policy = classify_code_task_lane(
        user_message="Review the current project and summarize the main issues across these scripts.",
        coding_objective="Review the current project and summarize the main issues across these scripts.",
        benchmark_mode=False,
        pending_clarification=False,
        existing_plan_milestones=0,
    )

    assert policy.lane == "fast"
    assert policy.use_fallback_pre_run_planner is True
    assert policy.use_fallback_post_run_review is True


def test_classify_code_task_lane_keeps_edit_work_deep() -> None:
    policy = classify_code_task_lane(
        user_message="Implement the new auth flow and refactor the session store.",
        coding_objective="Implement the new auth flow and refactor the session store.",
        benchmark_mode=False,
        pending_clarification=False,
        existing_plan_milestones=0,
    )

    assert policy.lane == "deep"


def test_classify_code_task_lane_marks_bounded_existing_file_edit_fast() -> None:
    policy = classify_code_task_lane(
        user_message=(
            "Apply a bounded visual refresh, keep changes within existing files, "
            "and avoid new dependencies."
        ),
        coding_objective=(
            "Apply a bounded visual refresh, keep changes within existing files, "
            "and avoid new dependencies."
        ),
        benchmark_mode=False,
        pending_clarification=False,
        existing_plan_milestones=0,
    )

    assert policy.lane == "fast"
    assert policy.use_fallback_pre_run_planner is True
    assert policy.use_fallback_post_run_review is True


def test_classify_code_task_lane_marks_single_file_repair_fast() -> None:
    policy = classify_code_task_lane(
        user_message=(
            "Read index.html from disk, use a shell command for exact counts, "
            "overwrite index.html with a single full-file write if needed, read back "
            "the result, and do not modify styles.css or app.js."
        ),
        coding_objective=(
            "Read index.html from disk, use a shell command for exact counts, "
            "overwrite index.html with a single full-file write if needed, read back "
            "the result, and do not modify styles.css or app.js."
        ),
        benchmark_mode=False,
        pending_clarification=False,
        existing_plan_milestones=0,
    )

    assert policy.lane == "fast"
    assert policy.use_fallback_pre_run_planner is True
    assert policy.use_fallback_post_run_review is True


def test_classify_research_task_lane_marks_document_review_fast() -> None:
    policy = classify_research_task_lane(
        user_message="Review this 80-page paper and summarize the main arguments by section.",
        research_objective="Review this 80-page paper and summarize the main arguments by section.",
        pending_clarification=False,
        depth_profile="standard",
        requested_reader_count=None,
    )

    assert policy.lane == "fast"
    assert policy.use_fallback_pre_run_planner is True
    assert policy.use_fallback_post_run_review is True


def test_classify_research_task_lane_keeps_live_request_deep() -> None:
    policy = classify_research_task_lane(
        user_message="Find the latest current market share numbers for model providers.",
        research_objective="Find the latest current market share numbers for model providers.",
        pending_clarification=False,
        depth_profile="standard",
        requested_reader_count=None,
    )

    assert policy.lane == "deep"
