"""Tests for the fan-out concurrency utility and parallel turn preparation."""

from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest

from dan.server.concierge.fan_out import fan_out, fan_out_dict


# ---------------------------------------------------------------------------
# fan_out / fan_out_dict unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fan_out_runs_concurrently():
    results: list[str] = []

    async def task_a():
        await asyncio.sleep(0.05)
        results.append("a")
        return "a"

    async def task_b():
        results.append("b")
        return "b"

    out = await fan_out([task_a, task_b])
    assert set(out) == {"a", "b"}


@pytest.mark.asyncio
async def test_fan_out_handles_failure():
    async def good():
        return 1

    async def bad():
        raise ValueError("boom")

    out = await fan_out([good, bad])
    assert out[0] == 1
    assert isinstance(out[1], ValueError)


@pytest.mark.asyncio
async def test_fan_out_timeout():
    async def slow():
        await asyncio.sleep(10)
        return "late"

    async def fast():
        return "fast"

    out = await fan_out([slow, fast], timeout_per=0.1)
    assert isinstance(out[0], asyncio.TimeoutError)
    assert out[1] == "fast"


@pytest.mark.asyncio
async def test_fan_out_dict_named():
    async def a():
        return 1

    async def b():
        return 2

    out = await fan_out_dict({"x": a, "y": b})
    assert out == {"x": 1, "y": 2}


@pytest.mark.asyncio
async def test_fan_out_empty():
    assert await fan_out([]) == []
    assert await fan_out_dict({}) == {}


@pytest.mark.asyncio
async def test_fan_out_preserves_order():
    async def first():
        await asyncio.sleep(0.05)
        return "first"

    async def second():
        return "second"

    out = await fan_out([first, second])
    assert out[0] == "first"
    assert out[1] == "second"


@pytest.mark.asyncio
async def test_fan_out_dict_partial_failure():
    async def ok():
        return 42

    async def fail():
        raise RuntimeError("oops")

    out = await fan_out_dict({"ok": ok, "fail": fail})
    assert out["ok"] == 42
    assert isinstance(out["fail"], RuntimeError)


# ---------------------------------------------------------------------------
# Parallel turn preparation integration test
# ---------------------------------------------------------------------------


def _make_msg(text: str, surface: str = "cli", external_id: str = "test-surface", **metadata: Any) -> "SurfaceMessage":
    from dan.server.concierge.models import SurfaceMessage

    return SurfaceMessage(
        surface=surface,
        external_id=external_id,
        text=text,
        attachments=[],
        metadata=metadata,
    )


def _build_test_concierge(tmp_path, *, memory_kernel=None):
    from dan.server.capability_registry import CapabilityContext
    from dan.server.chat_manager import ChatCompleteEvent
    from dan.server.concierge.project_store import ProjectStore
    from dan.server.concierge.runtime import Concierge

    chat_manager = MagicMock()

    async def _fake_send(**kwargs: Any) -> AsyncIterator:
        yield ChatCompleteEvent(
            message_id="m1",
            content="LLM response",
            token_usage={},
            context_window=0,
            graph_revision="",
        )

    chat_manager.send_message = MagicMock(side_effect=lambda **kw: _fake_send(**kw))
    chat_manager.send_message_with_tools = MagicMock(side_effect=lambda **kw: _fake_send(**kw))

    capability_context = CapabilityContext(workflow_id="_scratch")
    project_store = ProjectStore(base_dir=tmp_path / "projects")

    return Concierge(
        project_store=project_store,
        chat_manager=chat_manager,
        capability_context=capability_context,
        memory_kernel=memory_kernel,
    )


async def _collect_events(concierge, msg) -> list[Any]:
    events: list[Any] = []
    async for event in concierge.process(msg):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_parallel_prep_enabled(tmp_path, monkeypatch):
    """With DAN_CONCIERGE_PARALLEL_PREP=1, process() still produces valid results."""
    monkeypatch.setenv("DAN_CONCIERGE_PARALLEL_PREP", "1")
    concierge = _build_test_concierge(tmp_path)
    msg = _make_msg("hello world")
    events = await _collect_events(concierge, msg)
    assert len(events) >= 1
    assert any(getattr(e, "content", None) for e in events)


@pytest.mark.asyncio
async def test_parallel_prep_disabled(tmp_path, monkeypatch):
    """With DAN_CONCIERGE_PARALLEL_PREP=0, falls back to sequential path."""
    monkeypatch.setenv("DAN_CONCIERGE_PARALLEL_PREP", "0")
    concierge = _build_test_concierge(tmp_path)
    msg = _make_msg("hello world")
    events = await _collect_events(concierge, msg)
    assert len(events) >= 1
    assert any(getattr(e, "content", None) for e in events)


@pytest.mark.asyncio
async def test_parallel_prep_both_paths_same_output(tmp_path, monkeypatch):
    """Both parallel and sequential paths produce equivalent results."""
    msg = _make_msg("describe something")

    monkeypatch.setenv("DAN_CONCIERGE_PARALLEL_PREP", "1")
    c_parallel = _build_test_concierge(tmp_path / "par")
    events_parallel = await _collect_events(c_parallel, msg)

    monkeypatch.setenv("DAN_CONCIERGE_PARALLEL_PREP", "0")
    c_seq = _build_test_concierge(tmp_path / "seq")
    events_seq = await _collect_events(c_seq, msg)

    assert len(events_parallel) == len(events_seq)
    for ep, es in zip(events_parallel, events_seq):
        assert type(ep) is type(es)
