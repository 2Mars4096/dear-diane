"""Tests for publish session management and PublishedHumanRenderer."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from dan.engine.executor import HumanRenderRequest, HumanRenderResponse
from dan.publish.session import (
    PublishSession,
    PublishSessionStore,
    PublishedHumanRenderer,
    SessionStatus,
    submit_human_input,
)


# ---------------------------------------------------------------------------
# PublishSession
# ---------------------------------------------------------------------------

class TestPublishSession:
    def test_defaults(self):
        s = PublishSession()
        assert s.status == SessionStatus.RUNNING
        assert s.result is None
        assert s.error is None
        assert s.pending_request is None

    def test_to_dict_basic(self):
        s = PublishSession(workflow_id="wf1")
        d = s.to_dict()
        assert d["workflow_id"] == "wf1"
        assert d["status"] == "running"
        assert "result" not in d
        assert "pending_prompt" not in d

    def test_to_dict_with_pending_request(self):
        req = HumanRenderRequest(
            request_id="r1",
            node_id="h",
            prompt="Review this",
            render_mode="approval",
        )
        s = PublishSession(pending_request=req)
        d = s.to_dict()
        assert "pending_prompt" in d
        assert d["pending_prompt"]["request_id"] == "r1"
        assert d["pending_prompt"]["prompt"] == "Review this"

    def test_to_dict_with_result(self):
        s = PublishSession(result={"key": "val"}, status=SessionStatus.COMPLETED)
        d = s.to_dict()
        assert d["result"] == {"key": "val"}
        assert d["status"] == "completed"

    def test_to_dict_with_error(self):
        s = PublishSession(error="boom", status=SessionStatus.FAILED)
        d = s.to_dict()
        assert d["error"] == "boom"

    def test_touch(self):
        s = PublishSession()
        old = s.updated_at
        import time
        time.sleep(0.01)
        s.touch()
        assert s.updated_at > old


# ---------------------------------------------------------------------------
# PublishSessionStore
# ---------------------------------------------------------------------------

class TestPublishSessionStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        assert session.workflow_id == "wf1"
        assert session.status == SessionStatus.RUNNING

        fetched = await store.get(session.session_id)
        assert fetched is session

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        store = PublishSessionStore()
        assert await store.get("no-such-id") is None

    @pytest.mark.asyncio
    async def test_update_status(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        await store.update_status(session.session_id, SessionStatus.AWAITING_INPUT)
        fetched = await store.get(session.session_id)
        assert fetched is not None
        assert fetched.status == SessionStatus.AWAITING_INPUT

    @pytest.mark.asyncio
    async def test_set_result(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        await store.set_result(session.session_id, {"answer": 42})
        fetched = await store.get(session.session_id)
        assert fetched is not None
        assert fetched.status == SessionStatus.COMPLETED
        assert fetched.result == {"answer": 42}

    @pytest.mark.asyncio
    async def test_set_error(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        await store.set_error(session.session_id, "oops")
        fetched = await store.get(session.session_id)
        assert fetched is not None
        assert fetched.status == SessionStatus.FAILED
        assert fetched.error == "oops"

    @pytest.mark.asyncio
    async def test_remove(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        await store.remove(session.session_id)
        assert await store.get(session.session_id) is None

    @pytest.mark.asyncio
    async def test_list_sessions(self):
        store = PublishSessionStore()
        await store.create("wf1")
        await store.create("wf1")
        await store.create("wf2")

        all_sessions = await store.list_sessions()
        assert len(all_sessions) == 3

        wf1_sessions = await store.list_sessions("wf1")
        assert len(wf1_sessions) == 2


# ---------------------------------------------------------------------------
# PublishedHumanRenderer
# ---------------------------------------------------------------------------

class TestPublishedHumanRenderer:
    @pytest.mark.asyncio
    async def test_render_receives_submitted_input(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        renderer = PublishedHumanRenderer(store, timeout=5.0)
        renderer.active_session_id = session.session_id

        request = HumanRenderRequest(
            request_id="r1",
            node_id="h",
            prompt="Review?",
        )

        async def submit_after_delay():
            await asyncio.sleep(0.05)
            await submit_human_input(store, session.session_id, {"response": "approved"})

        asyncio.create_task(submit_after_delay())
        response = await renderer.render(request)

        assert response.request_id == "r1"
        assert response.data == {"response": "approved"}
        assert response.source == "human"

    @pytest.mark.asyncio
    async def test_render_timeout_with_default(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        renderer = PublishedHumanRenderer(store, timeout=0.05)
        renderer.active_session_id = session.session_id

        request = HumanRenderRequest(
            request_id="r2",
            node_id="h",
            prompt="Review?",
            default_action="auto_approve",
        )

        response = await renderer.render(request)
        assert response.source == "timeout"
        assert response.data == {"response": "auto_approve"}

    @pytest.mark.asyncio
    async def test_render_timeout_no_default_raises(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        renderer = PublishedHumanRenderer(store, timeout=0.05)
        renderer.active_session_id = session.session_id

        request = HumanRenderRequest(
            request_id="r3",
            node_id="h",
            prompt="Review?",
        )

        with pytest.raises(asyncio.TimeoutError):
            await renderer.render(request)

    @pytest.mark.asyncio
    async def test_render_no_active_session_raises(self):
        store = PublishSessionStore()
        renderer = PublishedHumanRenderer(store)

        request = HumanRenderRequest(request_id="r4")
        with pytest.raises(RuntimeError, match="no active session"):
            await renderer.render(request)

    @pytest.mark.asyncio
    async def test_session_transitions_during_render(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        renderer = PublishedHumanRenderer(store, timeout=5.0)
        renderer.active_session_id = session.session_id

        request = HumanRenderRequest(request_id="r5", node_id="h", prompt="?")

        async def check_and_submit():
            await asyncio.sleep(0.02)
            s = await store.get(session.session_id)
            assert s is not None
            assert s.status == SessionStatus.AWAITING_INPUT
            assert s.pending_request is not None
            await submit_human_input(store, session.session_id, {"ok": True})

        asyncio.create_task(check_and_submit())
        response = await renderer.render(request)

        assert response.data == {"ok": True}
        assert session.status == SessionStatus.RUNNING
        assert session.pending_request is None


# ---------------------------------------------------------------------------
# submit_human_input
# ---------------------------------------------------------------------------

class TestSubmitHumanInput:
    @pytest.mark.asyncio
    async def test_submit_to_awaiting_session(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        session.status = SessionStatus.AWAITING_INPUT
        session._input_event = asyncio.Event()

        result = await submit_human_input(store, session.session_id, {"x": 1})
        assert result is not None
        assert session._input_event.is_set()
        assert session._input_data == {"x": 1}

    @pytest.mark.asyncio
    async def test_submit_to_non_awaiting_returns_none(self):
        store = PublishSessionStore()
        session = await store.create("wf1")
        result = await submit_human_input(store, session.session_id, {"x": 1})
        assert result is None

    @pytest.mark.asyncio
    async def test_submit_to_nonexistent_returns_none(self):
        store = PublishSessionStore()
        result = await submit_human_input(store, "bad-id", {"x": 1})
        assert result is None
