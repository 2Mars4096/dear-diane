from __future__ import annotations

from collections.abc import Iterator

import pytest

from dan.server.concierge.autonomy import AutonomyResolution
from dan.server.concierge.models import SurfaceMessage
from dan.server.concierge.session import (
    SessionManager,
    SessionResult,
    SessionState,
    SessionTier,
)


def _make_msg(
    text: str = "root task",
    *,
    external_id: str = "surface-1",
) -> SurfaceMessage:
    return SurfaceMessage(
        surface="telegram:test",
        external_id=external_id,
        text=text,
    )


def _clock(*values: float) -> Iterator[float]:
    return iter(values)


def test_create_root_sets_root_lookup_and_env_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_SESSION_MAX_DEPTH", "7")
    monkeypatch.setenv("DAN_SESSION_MAX_CHILDREN", "3")
    monkeypatch.setenv("DAN_SESSION_MAX_TOTAL", "11")

    manager = SessionManager()
    root = manager.create_root(
        _make_msg("plan trip", external_id="chat-123"),
        triage={"tier": "multi"},
        tier=SessionTier.MULTI,
    )

    assert root.root_id == root.id
    assert manager.get_root("chat-123") is root
    assert root.max_depth == 7
    assert root.max_children == 3
    assert root.max_total_sessions == 11


def test_create_child_rejects_children_for_single_tier_parent() -> None:
    manager = SessionManager()
    root = manager.create_root(_make_msg(), triage=None, tier=SessionTier.SINGLE)

    with pytest.raises(ValueError, match="cannot have children"):
        manager.create_child(root.id, "child task", SessionTier.SINGLE)


def test_autonomy_resolution_propagates_from_root_to_child() -> None:
    manager = SessionManager()
    autonomy = AutonomyResolution(
        preferred_level="auto",
        effective_level="aggressive",
        source="inferred",
        reason="clear low-risk directive",
    )
    root = manager.create_root(
        _make_msg(),
        triage=None,
        tier=SessionTier.MULTI,
        autonomy_resolution=autonomy,
    )
    child = manager.create_child(root.id, "child task", SessionTier.SINGLE)

    assert root.autonomy_resolution is not None
    assert child.autonomy_resolution is not None
    assert root.autonomy_resolution.effective_level == "aggressive"
    assert child.autonomy_resolution.effective_level == "aggressive"


def test_can_spawn_child_enforces_depth_children_and_total_limits() -> None:
    depth_manager = SessionManager()
    depth_root = depth_manager.create_root(_make_msg(), triage=None, tier=SessionTier.MULTI)
    depth_root.max_depth = 1
    depth_child = depth_manager.create_child(depth_root.id, "depth child", SessionTier.MULTI)
    assert depth_manager.can_spawn_child(depth_child.id) is False

    children_manager = SessionManager()
    children_root = children_manager.create_root(_make_msg(), triage=None, tier=SessionTier.MULTI)
    children_root.max_children = 1
    children_manager.create_child(children_root.id, "first child", SessionTier.SINGLE)
    assert children_manager.can_spawn_child(children_root.id) is False

    total_manager = SessionManager()
    total_root = total_manager.create_root(_make_msg(), triage=None, tier=SessionTier.MULTI)
    total_root.max_total_sessions = 2
    total_manager.create_child(total_root.id, "only child", SessionTier.SINGLE)
    assert total_manager.can_spawn_child(total_root.id) is False


def test_update_state_enforces_transitions_and_stamps_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SessionManager()
    session = manager.create_root(_make_msg(), triage=None, tier=SessionTier.MULTI)
    times = _clock(10.0, 11.0, 12.0, 13.0)
    monkeypatch.setattr("dan.server.concierge.session.time.time", lambda: next(times))

    with pytest.raises(ValueError, match="invalid transition"):
        manager.update_state(session.id, SessionState.COMPLETED)

    manager.update_state(session.id, SessionState.RUNNING)
    manager.update_state(session.id, SessionState.WAITING)
    manager.update_state(session.id, SessionState.RUNNING)
    manager.update_state(session.id, SessionState.COMPLETED)

    assert session.started_at == 10.0
    assert session.completed_at == 13.0

    with pytest.raises(ValueError, match="invalid transition"):
        manager.update_state(session.id, SessionState.RUNNING)


def test_cancel_tree_cancels_non_terminal_descendants_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SessionManager()
    root = manager.create_root(_make_msg(), triage=None, tier=SessionTier.MULTI)
    active_child = manager.create_child(root.id, "active child", SessionTier.MULTI)
    terminal_child = manager.create_child(root.id, "done child", SessionTier.SINGLE)
    grandchild = manager.create_child(active_child.id, "grandchild", SessionTier.SINGLE)

    root.state = SessionState.WAITING
    active_child.state = SessionState.RUNNING
    grandchild.state = SessionState.PENDING
    terminal_child.state = SessionState.COMPLETED
    terminal_child.completed_at = 7.0

    monkeypatch.setattr("dan.server.concierge.session.time.time", lambda: 42.0)
    manager.cancel_tree(root.id)

    assert root.state == SessionState.CANCELLED
    assert active_child.state == SessionState.CANCELLED
    assert grandchild.state == SessionState.CANCELLED
    assert root.completed_at == 42.0
    assert active_child.completed_at == 42.0
    assert grandchild.completed_at == 42.0
    assert terminal_child.state == SessionState.COMPLETED
    assert terminal_child.completed_at == 7.0


def test_prune_completed_removes_finished_trees_after_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = SessionManager()
    root = manager.create_root(
        _make_msg(external_id="chat-ttl"),
        triage=None,
        tier=SessionTier.MULTI,
    )
    child = manager.create_child(root.id, "child", SessionTier.SINGLE)

    root.state = SessionState.COMPLETED
    root.completed_at = 50.0
    child.state = SessionState.COMPLETED
    child.completed_at = 55.0

    monkeypatch.setattr("dan.server.concierge.session.time.time", lambda: 100.0)
    removed = manager.prune_completed(max_age_seconds=30.0)

    assert removed == 2
    assert manager.get(root.id) is None
    assert manager.get(child.id) is None
    assert manager.get_root("chat-ttl") is None


def test_build_trace_includes_root_and_child_duration_and_error_fields() -> None:
    manager = SessionManager()
    root = manager.create_root(_make_msg(), triage=None, tier=SessionTier.MULTI)
    child = manager.create_child(root.id, "child", SessionTier.SINGLE)

    root.state = SessionState.COMPLETED
    root.created_at = 0.5
    root.started_at = 1.0
    root.completed_at = 3.5
    root.result = SessionResult(
        token_usage={"total_tokens": 10},
        tools_used=["file_read"],
        model_used="test-root-model",
    )

    child.state = SessionState.FAILED
    child.created_at = 1.5
    child.started_at = 2.0
    child.completed_at = 5.0
    child.result = SessionResult(
        error="child failed",
        token_usage={"total_tokens": 4},
        tools_used=["web_search"],
        model_used="test-child-model",
    )

    traces = {trace.session_id: trace for trace in manager.build_trace(root.id)}

    assert set(traces) == {root.id, child.id}
    assert traces[root.id].duration_ms == pytest.approx(2500.0)
    assert traces[child.id].duration_ms == pytest.approx(3000.0)
    assert traces[root.id].children == [child.id]
    assert traces[child.id].error == "child failed"


def test_export_tree_returns_nested_children_structure() -> None:
    manager = SessionManager()
    root = manager.create_root(_make_msg(), triage=None, tier=SessionTier.MULTI)
    branch = manager.create_child(root.id, "branch", SessionTier.MULTI)
    leaf = manager.create_child(branch.id, "leaf", SessionTier.SINGLE)
    sibling = manager.create_child(root.id, "sibling", SessionTier.SINGLE)

    exported = manager.export_tree(root.id)

    assert exported["id"] == root.id
    assert exported["tier"] == "MULTI"
    assert [child["id"] for child in exported["children"]] == [branch.id, sibling.id]
    assert exported["children"][0]["children"][0]["id"] == leaf.id
