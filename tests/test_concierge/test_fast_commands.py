"""Tests for fast-command path — commands skip expensive LLM/memory prep.

Tests the dispatch mechanism: slash commands detected by registry are handled
inline without running parallel memory retrieval, context resolution,
or speculative reuse search.
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import MagicMock, patch

import pytest

from dan.engine.correction_memory import CorrectionStore
from dan.engine.memory_kernel import MemoryKernel
from dan.engine.user_profile import UserProfile
from dan.server.chat_manager import ChatCompleteEvent
from dan.server.concierge.command_registry import CommandDescriptor, CommandRegistry
from dan.server.concierge.dispatcher import _is_bypass_command
from dan.server.concierge.models import SurfaceMessage, TaskTurn
from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.runtime import Concierge
from dan.server.telemetry import InMemoryTelemetryStore, TelemetryEvent


def _make_msg(text: str, external_id: str = "test-surface") -> SurfaceMessage:
    return SurfaceMessage(surface="cli", external_id=external_id, text=text)


def registry_test_handler(text: str) -> str:
    return f"registry handled: {text}"


def _build_concierge(tmp_path, *, memory_kernel=None, **kw) -> Concierge:
    chat_manager = MagicMock()

    async def _fake_send(**kwargs: Any) -> AsyncIterator:
        yield ChatCompleteEvent(
            message_id="m1", content="LLM response",
            token_usage={}, context_window=0, graph_revision="",
        )

    chat_manager.send_message = MagicMock(side_effect=lambda **k: _fake_send(**k))
    chat_manager.send_message_with_tools = MagicMock(side_effect=lambda **k: _fake_send(**k))

    cap_ctx = MagicMock()
    cap_ctx.graph_store = None
    cap_ctx.run_manager = None
    cap_ctx.activity_tracker = None
    cap_ctx.experience_store = None
    cap_ctx.experience_index = None
    cap_ctx.llm_provider = None

    project_store = ProjectStore(base_dir=tmp_path / "projects")

    return Concierge(
        project_store=project_store,
        chat_manager=chat_manager,
        capability_context=cap_ctx,
        memory_kernel=memory_kernel,
        **kw,
    )


async def _collect(concierge: Concierge, msg: SurfaceMessage) -> list[Any]:
    events = []
    async for event in concierge.process(msg):
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# _is_fast_command detection
# ---------------------------------------------------------------------------

class TestIsFastCommand:
    def test_regular_message_is_not_fast(self, tmp_path):
        c = _build_concierge(tmp_path)
        c._current_surface_id = "s1"
        assert not c._is_fast_command("hello world")

    def test_question_is_not_fast(self, tmp_path):
        c = _build_concierge(tmp_path)
        c._current_surface_id = "s1"
        assert not c._is_fast_command("what time is it?")

    def test_preference_confirm_is_fast_when_pending(self, tmp_path):
        c = _build_concierge(tmp_path)
        c._current_surface_id = "s1"
        c._pending_preference_surface["s1"] = [MagicMock()]
        assert c._is_fast_command("confirm")

    def test_preference_confirm_not_fast_without_pending(self, tmp_path):
        c = _build_concierge(tmp_path)
        c._current_surface_id = "s1"
        assert not c._is_fast_command("confirm")


# ---------------------------------------------------------------------------
# Fast-command path skips expensive prep
# ---------------------------------------------------------------------------

class TestFastCommandSkipsPrep:
    """Verify that commands bypass _retrieve_memory_context entirely."""

    @pytest.mark.asyncio
    async def test_regular_message_goes_through_dispatcher(self, tmp_path):
        """Non-command messages go through the tiered dispatcher, not the fast path."""
        c = _build_concierge(tmp_path)
        events = await _collect(c, _make_msg("hello world"))
        assert any(getattr(e, "type", "") == "chat_complete" for e in events)

    @pytest.mark.asyncio
    async def test_registry_only_chat_command_dispatches(self, tmp_path, monkeypatch):
        import dan.server.concierge.runtime as runtime_module

        registry = CommandRegistry()
        registry.register(CommandDescriptor(
            name="/registry-test",
            kind="chat",
            handler="tests.test_concierge.test_fast_commands.registry_test_handler",
            help_text="Registry-only test command",
        ))
        monkeypatch.setattr(runtime_module, "get_default_registry", lambda: registry)

        c = _build_concierge(tmp_path)
        events = await _collect(c, _make_msg("/registry-test hello"))

        assert len(events) == 1
        assert events[0].content == "registry handled: /registry-test hello"

    @pytest.mark.asyncio
    async def test_progress_override_is_scoped_to_request_surface(self, tmp_path):
        from dan.server.concierge.progress_ux import reset_user_verbosity_override

        reset_user_verbosity_override()
        c = _build_concierge(tmp_path)

        user_a_events = await _collect(c, _make_msg("/progress compact", external_id="user-a"))
        user_b_events = await _collect(c, _make_msg("/progress", external_id="user-b"))
        user_a_status = await _collect(c, _make_msg("/progress", external_id="user-a"))

        assert "compact" in user_a_events[0].content
        assert "auto per surface" in user_b_events[0].content
        assert "compact" in user_a_status[0].content

    @pytest.mark.asyncio
    async def test_domains_command_dispatches_and_updates_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DAN_PROFILE_PATH", str(tmp_path / "profile.json"))
        profile = UserProfile()
        memory_kernel = MemoryKernel(base_dir=str(tmp_path / "memory-kernel"))
        c = _build_concierge(
            tmp_path,
            user_profile=profile,
            memory_kernel=memory_kernel,
        )

        events = await _collect(c, _make_msg("/domains add scientific writing"))

        assert len(events) == 1
        assert "paper_rendering" in events[0].content
        assert profile.common_domains == ["paper_rendering"]
        assert memory_kernel.get("fact:domain:paper_rendering") is not None


# ---------------------------------------------------------------------------
# Dispatcher bypass for command prefixes
# ---------------------------------------------------------------------------

class TestDispatcherBypass:
    """Commands should bypass dispatcher queueing too."""

    def test_regular_message_does_not_bypass(self):
        assert not _is_bypass_command(_make_msg("hello world"))


# ---------------------------------------------------------------------------
# Correction filtering
# ---------------------------------------------------------------------------

class TestCorrectionFiltering:
    @pytest.mark.asyncio
    async def test_weak_correction_signals_are_not_stored(self, tmp_path):
        c = _build_concierge(tmp_path)
        project = c.project_store.create_project("lit-review", "test-surface")
        task = c.project_store.add_task(project.project_id, "outline", "test-surface")
        c.project_store.append_turn(
            project.project_id,
            task.task_id,
            TaskTurn(role="assistant", content="Here is the outline draft.", intent="conversation"),
            "test-surface",
        )
        c._correction_store = CorrectionStore()

        await _collect(
            c,
            SurfaceMessage(
                surface="cli",
                external_id="test-surface",
                text="Actually",
                metadata={
                    "trigger_context": {
                        "project_id": project.project_id,
                        "task_id": task.task_id,
                    }
                },
            ),
        )

        assert c._correction_store.count() == 0


# ---------------------------------------------------------------------------
# /cost command
# ---------------------------------------------------------------------------

class TestCostCommand:
    @pytest.mark.asyncio
    async def test_cost_no_telemetry_store(self, tmp_path):
        c = _build_concierge(tmp_path, telemetry_store=None)
        events = await _collect(c, _make_msg("/cost"))
        assert len(events) == 1
        assert "not enabled" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_cost_no_data(self, tmp_path):
        store = InMemoryTelemetryStore()
        c = _build_concierge(tmp_path, telemetry_store=store)
        events = await _collect(c, _make_msg("/cost"))
        assert len(events) == 1
        assert "no token usage" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_cost_with_token_data(self, tmp_path):
        store = InMemoryTelemetryStore()
        await store.record(TelemetryEvent(
            event_type="chat_turn",
            session_id="test-surface",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            estimated_cost=0.0008,
        ))
        c = _build_concierge(tmp_path, telemetry_store=store)
        events = await _collect(c, _make_msg("/cost"))

        assert len(events) == 1
        content = events[0].content
        assert "gpt-4o" in content
        assert "150" in content
        assert "$0.0008" in content

    @pytest.mark.asyncio
    async def test_cost_multi_model_breakdown(self, tmp_path):
        store = InMemoryTelemetryStore()
        await store.record(TelemetryEvent(
            event_type="chat_turn",
            session_id="test-surface",
            model="gpt-4o",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            estimated_cost=0.002,
        ))
        await store.record(TelemetryEvent(
            event_type="chat_turn",
            session_id="test-surface",
            model="claude-sonnet-4",
            prompt_tokens=500,
            completion_tokens=200,
            total_tokens=700,
            estimated_cost=0.005,
        ))
        await store.record(TelemetryEvent(
            event_type="chat_turn",
            session_id="other-surface",
            model="gpt-4o",
            prompt_tokens=999,
            completion_tokens=999,
            total_tokens=1998,
            estimated_cost=0.1,
        ))
        c = _build_concierge(tmp_path, telemetry_store=store)
        events = await _collect(c, _make_msg("/cost"))

        assert len(events) == 1
        content = events[0].content
        assert "gpt-4o" in content
        assert "claude-sonnet-4" in content
        assert "1,000" in content  # grand total = 300 + 700
        assert "1,998" not in content  # other session excluded


# ---------------------------------------------------------------------------
# /retry command
# ---------------------------------------------------------------------------

class TestRetryCommand:
    @pytest.mark.asyncio
    async def test_retry_no_project(self, tmp_path):
        c = _build_concierge(tmp_path)
        events = await _collect(c, _make_msg("/retry"))
        assert len(events) == 1
        assert "no active project" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_retry_no_user_messages(self, tmp_path):
        c = _build_concierge(tmp_path)
        project = c.project_store.create_project("test", "test-surface")
        c.project_store.add_task(project.project_id, "task", "test-surface")
        events = await _collect(c, _make_msg("/retry"))
        assert len(events) == 1
        assert "no prior user message" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_retry_replays_last_user_message(self, tmp_path):
        c = _build_concierge(tmp_path)
        project = c.project_store.create_project("test", "test-surface")
        task = c.project_store.add_task(project.project_id, "task", "test-surface")
        c.project_store.append_turn(
            project.project_id,
            task.task_id,
            TaskTurn(
                role="user",
                content="What is 2+2?",
                intent="ask",
                metadata={
                    "mode": "build",
                    "mentions": [{"type": "file", "identifier": "notes.txt"}],
                    "selected_path": "/tmp/input.txt",
                    "surface_context": {
                        "mode": "development",
                        "workspace_id": "ws-1",
                        "active_file": {
                            "path": "src/math.py",
                            "content": "answer = 4",
                            "language": "python",
                        },
                    },
                },
            ),
            "test-surface",
        )
        c.project_store.append_turn(
            project.project_id, task.task_id,
            TaskTurn(role="assistant", content="4", intent=None),
            "test-surface",
        )

        captured: dict[str, Any] = {}
        original_dispatch = c._tiered_dispatcher.dispatch

        async def _capture_dispatch(msg: SurfaceMessage):
            captured["text"] = msg.text
            captured["metadata"] = dict(msg.metadata)
            async for event in original_dispatch(msg):
                yield event

        c._tiered_dispatcher.dispatch = _capture_dispatch

        events = await _collect(c, _make_msg("/retry"))
        assert captured.get("text") == "What is 2+2?"
        assert captured["metadata"]["mode"] == "build"
        assert captured["metadata"]["mentions"] == [
            {"type": "file", "identifier": "notes.txt"},
        ]
        assert captured["metadata"]["selected_path"] == "/tmp/input.txt"
        assert captured["metadata"]["surface_context"]["active_file"]["path"] == "src/math.py"
        assert len(events) >= 1
