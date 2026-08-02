"""Portal contract regression tests (plan 31-25, tasks 0-1 / 0-5)."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from dan.server.concierge.models import SurfaceMessage


def _make_request(**overrides):
    """Build a ChatMessageRequest with sensible defaults.

    Import is deferred so the test module loads even when the full server
    dependency tree is not installed — the import error surfaces as a clear
    test failure rather than a collection-time crash.
    """
    from dan.server.app import ChatMessageRequest

    defaults = {"workflow_id": "wf-1", "message": "hello"}
    defaults.update(overrides)
    return ChatMessageRequest(**defaults)


# ------------------------------------------------------------------
# 1. surface → surface_type + surface_id parsing
# ------------------------------------------------------------------

class TestSurfaceParsing:
    def test_surface_splits_into_type_and_id(self):
        req = _make_request(surface="telegram:bot123")
        assert req.surface_type == "telegram"
        assert req.surface_id == "bot123"

    def test_surface_with_multiple_colons_splits_on_first(self):
        req = _make_request(surface="custom:id:extra")
        assert req.surface_type == "custom"
        assert req.surface_id == "id:extra"

    def test_surface_without_colon_leaves_fields_none(self):
        req = _make_request(surface="web")
        assert req.surface_type is None
        assert req.surface_id is None

    def test_empty_surface_alias_rejected(self):
        with pytest.raises(ValidationError):
            _make_request(surface=":")

    def test_no_surface_leaves_all_none(self):
        req = _make_request()
        assert req.surface is None
        assert req.surface_type is None
        assert req.surface_id is None


# ------------------------------------------------------------------
# 2. surface_type + surface_id → surface computation
# ------------------------------------------------------------------

class TestSurfaceComputation:
    def test_type_and_id_compute_surface(self):
        req = _make_request(surface_type="whatsapp", surface_id="+1234")
        assert req.surface == "whatsapp:+1234"

    def test_all_three_explicit_match_canonical_surface(self):
        req = _make_request(
            surface="web:sess-99",
            surface_type="web",
            surface_id="sess-99",
        )
        assert req.surface == "web:sess-99"
        assert req.surface_type == "web"
        assert req.surface_id == "sess-99"

    def test_mismatched_surface_and_new_fields_rejected(self):
        with pytest.raises(ValidationError):
            _make_request(
                surface="cli:local",
                surface_type="web",
                surface_id="sess-99",
            )

    def test_partial_surface_identifier_rejected(self):
        with pytest.raises(ValidationError):
            _make_request(surface_type="telegram")

    def test_partial_surface_identifier_can_be_completed_from_surface(self):
        req = _make_request(
            surface="telegram:bot1",
            surface_type="telegram",
        )
        assert req.surface == "telegram:bot1"
        assert req.surface_type == "telegram"
        assert req.surface_id == "bot1"


# ------------------------------------------------------------------
# 3. session_id ↔ thread_id bidirectional sync
# ------------------------------------------------------------------

class TestSessionThreadSync:
    def test_session_id_populates_thread_id(self):
        req = _make_request(session_id="s-1")
        assert req.thread_id == "s-1"

    def test_thread_id_populates_session_id(self):
        req = _make_request(thread_id="t-2")
        assert req.session_id == "t-2"

    def test_both_set_same_value_preserved(self):
        req = _make_request(session_id="same-id", thread_id="same-id")
        assert req.session_id == "same-id"
        assert req.thread_id == "same-id"

    def test_distinct_session_and_thread_are_allowed(self):
        req = _make_request(session_id="lane-3", thread_id="thread-3")
        assert req.session_id == "lane-3"
        assert req.thread_id == "thread-3"

    def test_neither_set_stays_none(self):
        req = _make_request()
        assert req.session_id is None
        assert req.thread_id is None


# ------------------------------------------------------------------
# 4. System messages in history are filtered out
# ------------------------------------------------------------------

class TestHistoryFiltering:
    def test_system_messages_stripped(self, caplog):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "secret"},
            {"role": "assistant", "content": "hey"},
        ]
        with caplog.at_level(logging.WARNING):
            req = _make_request(history=history)

        assert len(req.history) == 2
        assert all(m["role"] != "system" for m in req.history)
        assert "Stripped 1 system message" in caplog.text

    def test_no_system_messages_no_warning(self, caplog):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hey"},
        ]
        with caplog.at_level(logging.WARNING):
            req = _make_request(history=history)

        assert len(req.history) == 2
        assert "system message" not in caplog.text

    def test_invalid_roles_are_stripped(self, caplog):
        history = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "metadata"},
            {"role": "assistant", "content": "hey"},
        ]
        with caplog.at_level(logging.WARNING):
            req = _make_request(history=history)

        assert req.history == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hey"},
        ]
        assert "non-user/assistant" in caplog.text

    def test_empty_history_stays_empty(self):
        req = _make_request()
        assert req.history == []


# ------------------------------------------------------------------
# 5. SurfaceMessage accepts new fields
# ------------------------------------------------------------------

class TestSurfaceMessageNewFields:
    def test_default_values(self):
        msg = SurfaceMessage(surface="server", external_id="x", text="hi")
        assert msg.surface_type == ""
        assert msg.surface_id == ""
        assert msg.session_id == ""

    def test_explicit_values(self):
        msg = SurfaceMessage(
            surface="telegram:bot1",
            surface_type="telegram",
            surface_id="bot1",
            session_id="sess-42",
            external_id="ext-1",
            text="hello",
        )
        assert msg.surface_type == "telegram"
        assert msg.surface_id == "bot1"
        assert msg.session_id == "sess-42"

    def test_backward_compat_without_new_fields(self):
        msg = SurfaceMessage(
            surface="server",
            external_id="chat-1",
            text="test",
            metadata={"k": "v"},
        )
        assert msg.surface == "server"
        assert msg.metadata == {"k": "v"}
