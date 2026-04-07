from __future__ import annotations

from types import SimpleNamespace

from dan.server.concierge.followup_classifier import FollowUpResolution, FollowUpType
from dan.server.concierge.models import SurfaceMessage
from dan.server.concierge.runtime.dispatch_policy import select_dispatch_mode
from dan.server.concierge.task_registry import DispatchMode


def _msg(text: str = "do the work", metadata: dict | None = None) -> SurfaceMessage:
    return SurfaceMessage(
        surface="cli",
        external_id="user-1",
        text=text,
        metadata=metadata or {},
    )


def _triage(
    *,
    intent: str = "agent",
    tier: int = 1,
    target: str = "general",
    action_hints: list[str] | None = None,
    is_social: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        intent=intent,
        tier=tier,
        is_social=is_social,
        route=SimpleNamespace(
            target=target,
            action_hints=action_hints or [],
        ),
    )


def test_agent_file_turn_stays_foreground() -> None:
    mode = select_dispatch_mode(
        _msg(),
        _triage(target="file", action_hints=["read_file", "write_file"]),
        None,
    )
    assert mode == DispatchMode.FOREGROUND


def test_plan_turn_stays_foreground() -> None:
    mode = select_dispatch_mode(
        _msg(metadata={"mode": "plan"}),
        _triage(intent="plan"),
        None,
    )
    assert mode == DispatchMode.FOREGROUND


def test_workflow_query_only_stays_foreground() -> None:
    mode = select_dispatch_mode(
        _msg("what does this workflow do?"),
        _triage(target="workflow", action_hints=["workflow_query", "search_web"]),
        None,
    )
    assert mode == DispatchMode.FOREGROUND


def test_workflow_run_backgrounds_explicit_long_running_work() -> None:
    mode = select_dispatch_mode(
        _msg("run the workflow"),
        _triage(target="workflow", action_hints=["workflow_run"]),
        None,
    )
    assert mode == DispatchMode.BACKGROUND


def test_detached_workflow_edit_still_requires_skip_confirm() -> None:
    foreground = select_dispatch_mode(
        _msg(metadata={"detached": True}),
        _triage(target="workflow", action_hints=["workflow_edit"]),
        None,
    )
    background = select_dispatch_mode(
        _msg(metadata={"detached": True, "skip_confirm": True}),
        _triage(target="workflow", action_hints=["workflow_edit"]),
        None,
    )

    assert foreground == DispatchMode.FOREGROUND
    assert background == DispatchMode.BACKGROUND


def test_status_follow_up_stays_inline() -> None:
    mode = select_dispatch_mode(
        _msg("status"),
        _triage(),
        FollowUpResolution(follow_up_type=FollowUpType.QUERY_STATUS),
    )
    assert mode == DispatchMode.INLINE
