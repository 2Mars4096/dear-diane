"""Tests for fast-command path — commands skip expensive LLM/memory prep.

Tests the dispatch mechanism: slash commands detected by registry are handled
inline without running parallel memory retrieval, context resolution,
or speculative reuse search.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator
from unittest.mock import MagicMock, patch

import pytest

from dan.engine.correction_memory import CorrectionStore
from dan.engine.memory_kernel import MemoryKernel
from dan.engine.user_profile import UserProfile
from dan.llm_core.types import GatewayCall
from dan.server.capability_registry import CapabilityResult
from dan.server.chat_manager import ChatCompleteEvent
from dan.server.concierge.command_registry import CommandDescriptor, CommandRegistry
from dan.server.concierge.dispatcher import _is_bypass_command
from dan.server.concierge.models import ResolvedContext, SurfaceMessage, TaskTurn
from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.runtime import Concierge
from dan.server.concierge.scheduler import ScheduleHistoryStore, ScheduleStore
from dan.server.telemetry import InMemoryTelemetryStore, TelemetryEvent, TelemetryQuery


def _make_msg(
    text: str,
    external_id: str = "test-surface",
    *,
    surface: str = "cli",
) -> SurfaceMessage:
    return SurfaceMessage(surface=surface, external_id=external_id, text=text)


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


def test_goal_command_binds_goal_to_current_task(tmp_path):
    c = _build_concierge(tmp_path)
    project = c.project_store.create_project("lit-review", "cli-user")
    c.project_store.add_task(project.project_id, "outline", "cli-user")

    response = c.handle_goal_command(_make_msg("/goal finish the rollout", external_id="cli-user"))

    assert "Goal stored" in response
    assert len(c._concierge_state.active_goals) == 1
    goal = c._concierge_state.active_goals[0]
    assert goal.project_id == project.project_id

    loaded = c.project_store.get_project(project.project_id, "cli-user")
    assert loaded is not None
    assert loaded.tasks[0].goal_id == goal.id


def test_assistant_progress_projects_from_goal_ledger(tmp_path):
    c = _build_concierge(tmp_path)
    project = c.project_store.create_project("lit-review", "cli-user")
    c.project_store.add_task(project.project_id, "outline", "cli-user")
    c.handle_goal_command(_make_msg("/goal finish the rollout", external_id="cli-user"))

    refreshed_project = c.project_store.get_project(project.project_id, "cli-user")
    assert refreshed_project is not None
    context = ResolvedContext(
        project=refreshed_project,
        task=refreshed_project.tasks[0],
        is_new_project=False,
        is_new_task=False,
        confidence=1.0,
        domain=refreshed_project.domain,
    )

    c._record_assistant_turn(
        context,
        _make_msg("continue", external_id="cli-user"),
        "[x] write tests\n[ ] update docs\nblocked by CI\nArtifact: /tmp/report.md",
    )

    goal = c._concierge_state.active_goals[0]
    assert goal.progress.completed_steps == ["write tests"]
    assert goal.progress.pending_steps == ["update docs"]
    assert goal.progress.current_blocker == "blocked by CI"
    assert goal.progress.artifacts["report.md"] == "/tmp/report.md"

    loaded = c.project_store.get_project(project.project_id, "cli-user")
    assert loaded is not None
    stored_task = loaded.tasks[0]
    assert stored_task.goal_id == goal.id
    assert stored_task.completed_steps == ["write tests"]
    assert stored_task.pending_steps == ["update docs"]
    assert stored_task.current_blocker == "blocked by CI"


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
    async def test_schedule_workflow_current_uses_linked_project_workflow_without_metadata(
        self,
        tmp_path,
    ):
        c = _build_concierge(tmp_path)
        c._schedule_store = ScheduleStore(path=str(tmp_path / "schedules.json"))
        c._schedule_history_store = ScheduleHistoryStore(path=str(tmp_path / "schedule-history.json"))
        project = c.project_store.create_project("demo", "user-a")
        c.project_store.link_workflow(project.project_id, "wf-current", "user-a")

        events = await _collect(
            c,
            _make_msg("/schedule workflow current daily at 9am", external_id="user-a"),
        )

        assert len(events) == 1
        assert "Scheduled workflow" in events[0].content
        entries = c._schedule_store.list_all()
        assert len(entries) == 1
        assert entries[0].workflow_id == "wf-current"

    @pytest.mark.asyncio
    async def test_nl_workflow_schedule_followup_bridges_to_schedule_command(
        self,
        tmp_path,
    ):
        c = _build_concierge(tmp_path)
        c._schedule_store = ScheduleStore(path=str(tmp_path / "schedules.json"))
        c._schedule_history_store = ScheduleHistoryStore(path=str(tmp_path / "schedule-history.json"))
        project = c.project_store.create_project("demo", "user-a")
        c.project_store.link_workflow(project.project_id, "wf-current", "user-a")

        events = await _collect(
            c,
            _make_msg("schedule it daily at 8am", external_id="user-a"),
        )

        assert len(events) == 1
        assert "Scheduled workflow" in events[0].content
        entries = c._schedule_store.list_all()
        assert len(entries) == 1
        assert entries[0].workflow_id == "wf-current"
        assert entries[0].trigger == "daily at 8am"
        c.chat_manager.send_message.assert_not_called()
        c.chat_manager.send_message_with_tools.assert_not_called()

    @pytest.mark.asyncio
    async def test_nl_workflow_schedule_followup_uses_default_daily_trigger(
        self,
        tmp_path,
    ):
        c = _build_concierge(tmp_path)
        c._schedule_store = ScheduleStore(path=str(tmp_path / "schedules.json"))
        c._schedule_history_store = ScheduleHistoryStore(path=str(tmp_path / "schedule-history.json"))
        project = c.project_store.create_project("demo", "user-a")
        c.project_store.link_workflow(project.project_id, "wf-current", "user-a")

        events = await _collect(
            c,
            _make_msg("set up automated daily execution", external_id="user-a"),
        )

        assert len(events) == 1
        assert "Scheduled workflow" in events[0].content
        entries = c._schedule_store.list_all()
        assert len(entries) == 1
        assert entries[0].workflow_id == "wf-current"
        assert entries[0].trigger == "every day at 9am"

    @pytest.mark.asyncio
    async def test_nl_workflow_schedule_followup_executes_embedded_schedule_and_continues_turn(
        self,
        tmp_path,
    ):
        c = _build_concierge(tmp_path)
        c._schedule_store = ScheduleStore(path=str(tmp_path / "schedules.json"))
        c._schedule_history_store = ScheduleHistoryStore(path=str(tmp_path / "schedule-history.json"))
        project = c.project_store.create_project("demo", "user-a")
        c.project_store.link_workflow(project.project_id, "wf-current", "user-a")

        events = await _collect(
            c,
            _make_msg(
                "delete the obsolete workflows. schedule it daily at 8am",
                external_id="user-a",
            ),
        )

        assert any(getattr(event, "type", "") == "chat_complete" for event in events)
        entries = c._schedule_store.list_all()
        assert len(entries) == 1
        assert entries[0].workflow_id == "wf-current"
        assert entries[0].trigger == "daily at 8am"
        c.chat_manager.send_message.assert_not_called()
        c.chat_manager.send_message_with_tools.assert_called_once()
        forwarded_message = c.chat_manager.send_message_with_tools.call_args.kwargs["message"]
        assert forwarded_message == "delete the obsolete workflows"
        forwarded_system = c.chat_manager.send_message_with_tools.call_args.kwargs["extra_system_instructions"]
        assert "Workflow scheduling was already completed during routing" in forwarded_system
        assert "Scheduled workflow" in forwarded_system

    @pytest.mark.asyncio
    async def test_nl_workflow_schedule_followup_handles_comma_separated_mixed_turn(
        self,
        tmp_path,
    ):
        c = _build_concierge(tmp_path)
        c._schedule_store = ScheduleStore(path=str(tmp_path / "schedules.json"))
        c._schedule_history_store = ScheduleHistoryStore(path=str(tmp_path / "schedule-history.json"))
        project = c.project_store.create_project("demo", "user-a")
        c.project_store.link_workflow(project.project_id, "wf-current", "user-a")

        events = await _collect(
            c,
            _make_msg(
                "Please do delete obsolete workflows. set up automated daily exeuction, increase news search depth, you can try 15+ or even more. report looks good for now",
                external_id="user-a",
            ),
        )

        assert any(getattr(event, "type", "") == "chat_complete" for event in events)
        entries = c._schedule_store.list_all()
        assert len(entries) == 1
        assert entries[0].workflow_id == "wf-current"
        assert entries[0].trigger == "every day at 9am"
        c.chat_manager.send_message.assert_not_called()
        c.chat_manager.send_message_with_tools.assert_called_once()
        forwarded_message = c.chat_manager.send_message_with_tools.call_args.kwargs["message"]
        assert "delete obsolete workflows" in forwarded_message
        assert "increase news search depth" in forwarded_message
        assert "automated daily exeuction" not in forwarded_message
        forwarded_system = c.chat_manager.send_message_with_tools.call_args.kwargs["extra_system_instructions"]
        assert "Workflow scheduling was already completed during routing" in forwarded_system

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
    async def test_autonomy_command_sets_session_override(self, tmp_path):
        c = _build_concierge(tmp_path)

        events = await _collect(c, _make_msg("/autonomy aggressive", external_id="user-a"))
        status = await _collect(c, _make_msg("/autonomy", external_id="user-a"))

        assert "aggressive" in events[0].content
        assert "Session autonomy preference: `aggressive`" in status[0].content

    @pytest.mark.asyncio
    async def test_autonomy_session_override_isolated_by_surface(self, tmp_path):
        c = _build_concierge(tmp_path)

        await _collect(c, _make_msg("/autonomy aggressive", external_id="shared-user", surface="cli"))
        other_surface_status = await _collect(
            c,
            _make_msg("/autonomy", external_id="shared-user", surface="telegram:test"),
        )
        original_surface_status = await _collect(
            c,
            _make_msg("/autonomy", external_id="shared-user", surface="cli"),
        )

        assert "Session autonomy preference: `auto`" in other_surface_status[0].content
        assert "Session autonomy preference: `aggressive`" in original_surface_status[0].content

    @pytest.mark.asyncio
    async def test_autonomy_session_override_isolated_by_external_id(self, tmp_path):
        c = _build_concierge(tmp_path)

        await _collect(c, _make_msg("/autonomy aggressive", external_id="user-a"))
        other_user_status = await _collect(c, _make_msg("/autonomy", external_id="user-b"))
        original_user_status = await _collect(c, _make_msg("/autonomy", external_id="user-a"))

        assert "Session autonomy preference: `auto`" in other_user_status[0].content
        assert "Session autonomy preference: `aggressive`" in original_user_status[0].content

    @pytest.mark.asyncio
    async def test_autonomy_command_sets_project_override(self, tmp_path):
        c = _build_concierge(tmp_path)
        project = c.project_store.create_project("demo", "user-a")

        await _collect(c, _make_msg("/autonomy careful --project", external_id="user-a"))

        stored = c.project_store.get_project(project.project_id, "user-a")
        assert stored is not None
        assert stored.autonomy_preference == "careful"

    @pytest.mark.asyncio
    async def test_autonomy_auto_clears_last_effective_marker(self, tmp_path):
        c = _build_concierge(tmp_path)

        await _collect(c, _make_msg("/autonomy aggressive", external_id="user-a"))
        await _collect(c, _make_msg("/autonomy auto", external_id="user-a"))
        status = await _collect(c, _make_msg("/autonomy", external_id="user-a"))

        assert "Session autonomy preference: `auto`" in status[0].content
        assert "Last effective autonomy" not in status[0].content

    @pytest.mark.asyncio
    async def test_autonomy_project_update_refuses_ambiguous_multi_project_surface(self, tmp_path):
        c = _build_concierge(tmp_path)
        _ = c.project_store.create_project("demo-a", "user-a")
        _ = c.project_store.create_project("demo-b", "user-a")

        events = await _collect(c, _make_msg("/autonomy careful --project", external_id="user-a"))

        assert "Multiple active projects" in events[0].content

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

    @pytest.mark.asyncio
    async def test_search_command_runs_direct_grounded_search(self, tmp_path, monkeypatch):
        async def _fake_handle_web_search(args, ctx):
            assert args["query"] == "latest dan"
            assert args["fetch_content"] is True
            assert getattr(ctx, "grounding_required", False) is True
            return CapabilityResult(success=True, message="grounded results")

        monkeypatch.setattr(
            "dan.server.capabilities.web.handle_web_search",
            _fake_handle_web_search,
        )

        c = _build_concierge(tmp_path)
        events = await _collect(c, _make_msg("/search latest dan"))

        assert len(events) == 1
        assert events[0].content == "grounded results"


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

    @pytest.mark.asyncio
    async def test_correction_creates_pending_prompt_adaptation_and_approve_applies_it(
        self,
        tmp_path,
        monkeypatch,
    ):
        prompt_key = "prompts/runtime.unified_system"
        monkeypatch.setenv("DAN_BEHAVIOR_DIR", str(tmp_path / "behavior"))
        monkeypatch.setenv("DAN_LEARNING_TIER", "1")
        monkeypatch.setenv("DAN_PROMPT_PROPOSAL_MIN_NEGATIVE", "1")

        kernel = MemoryKernel(base_dir=str(tmp_path / "memory"))
        c = _build_concierge(tmp_path, memory_kernel=kernel)

        await _collect(c, _make_msg("Explain the rollout"))
        await _collect(c, _make_msg("Too long, keep it short"))

        assert c._correction_store.count() == 1
        pending = c._adaptation_registry.list_pending()
        assert len(pending) == 1
        candidate = pending[0]
        assert candidate.parameter_key == prompt_key
        assert isinstance(candidate.before_value, str) and candidate.before_value
        assert isinstance(candidate.after_value, str) and candidate.after_value != candidate.before_value
        assert "learned response adjustments" in candidate.after_value.lower()

        events = await _collect(c, _make_msg(f"/approve {candidate.id}"))

        assert len(events) == 1
        assert "approved and applied" in events[0].content.lower()
        applied = c._adaptation_registry.get(candidate.id)
        assert applied is not None
        assert applied.status == "applied"
        assert c._behavior_store.get(prompt_key) == candidate.after_value

        c_restarted = _build_concierge(
            tmp_path,
            memory_kernel=MemoryKernel(base_dir=str(tmp_path / "memory")),
        )
        assert c_restarted._behavior_store.get(prompt_key) == candidate.after_value
        restarted_candidate = c_restarted._adaptation_registry.get(candidate.id)
        assert restarted_candidate is not None
        assert restarted_candidate.status == "applied"
        assert c_restarted._correction_store.count() == 1

    @pytest.mark.asyncio
    async def test_prompt_auto_apply_rolls_back_after_measured_regression(
        self,
        tmp_path,
        monkeypatch,
    ):
        prompt_key = "prompts/runtime.unified_system"
        monkeypatch.setenv("DAN_BEHAVIOR_DIR", str(tmp_path / "behavior"))
        monkeypatch.setenv("DAN_LEARNING_TIER", "2")
        monkeypatch.setenv("DAN_PROMPT_PROPOSAL_MIN_NEGATIVE", "1")

        kernel = MemoryKernel(base_dir=str(tmp_path / "memory"))
        c = _build_concierge(tmp_path, memory_kernel=kernel)
        param = c._param_registry.get_by_key(prompt_key)
        assert param is not None
        param.min_evidence_count = 2

        await _collect(c, _make_msg("First request"))
        await _collect(c, _make_msg("Second request"))
        await _collect(c, _make_msg("Too long, keep it short"))

        applied = c._adaptation_registry.list_applied()
        assert len(applied) == 1
        candidate = applied[0]
        assert c._behavior_store.get(prompt_key) == candidate.after_value

        await _collect(c, _make_msg("No, that's still wrong"))
        c_restarted = _build_concierge(
            tmp_path,
            memory_kernel=MemoryKernel(base_dir=str(tmp_path / "memory")),
        )
        await _collect(c_restarted, _make_msg("No, do it again"))

        rolled_back = c_restarted._adaptation_registry.get(candidate.id)
        assert rolled_back is not None
        assert rolled_back.status == "rolled_back"
        assert c_restarted._behavior_store.get(prompt_key) == candidate.before_value


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

    @pytest.mark.asyncio
    async def test_cost_surfaces_latest_citation_summary(self, tmp_path):
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
        c.capability_context.workflow_id = "_scratch"
        c.chat_manager._chat_store = MagicMock()
        c.chat_manager._chat_store.get_thread_meta.return_value = {
            "latest_citation_summary": {
                "ran": True,
                "verified": 4,
                "unverified": 1,
            }
        }

        events = await _collect(c, _make_msg("/cost"))

        assert len(events) == 1
        assert "Citations" in events[0].content
        assert "4 verified, 1 unverified" in events[0].content

    @pytest.mark.asyncio
    async def test_cost_prefers_request_workflow_metadata_for_citation_summary(self, tmp_path):
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
        c.capability_context.workflow_id = "stale-context-workflow"
        project = c.project_store.create_project("test", "test-surface")
        c.project_store.link_workflow(project.project_id, "linked-project-workflow", "test-surface")
        c.chat_manager._chat_store = MagicMock()
        c.chat_manager._chat_store.get_thread_meta.side_effect = lambda workflow_id, thread_id: (
            {
                "latest_citation_summary": {
                    "ran": True,
                    "verified": 2,
                    "unverified": 0,
                }
            }
            if (workflow_id, thread_id) == ("request-workflow", "request-thread")
            else {}
        )

        msg = _make_msg("/cost")
        msg.metadata = {"workflow_id": "request-workflow", "thread_id": "request-thread"}
        events = await _collect(c, msg)

        assert len(events) == 1
        assert "2 verified, 0 unverified" in events[0].content
        c.chat_manager._chat_store.get_thread_meta.assert_called_with(
            "request-workflow",
            "request-thread",
        )

    @pytest.mark.asyncio
    async def test_cost_prefers_session_id_over_broader_thread_metadata(self, tmp_path):
        store = InMemoryTelemetryStore()
        await store.record(TelemetryEvent(
            event_type="chat_turn",
            session_id="lane-1",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            estimated_cost=0.0008,
        ))
        c = _build_concierge(tmp_path, telemetry_store=store)
        c.chat_manager._chat_store = MagicMock()
        c.chat_manager._chat_store.get_thread_meta.side_effect = lambda workflow_id, thread_id: (
            {
                "latest_citation_summary": {
                    "ran": True,
                    "verified": 3,
                    "unverified": 0,
                }
            }
            if (workflow_id, thread_id) == ("request-workflow", "lane-1")
            else {}
        )

        msg = _make_msg("/cost")
        msg.session_id = "lane-1"
        msg.metadata = {"workflow_id": "request-workflow", "thread_id": "conversation-1"}
        events = await _collect(c, msg)

        assert len(events) == 1
        assert "3 verified, 0 unverified" in events[0].content
        c.chat_manager._chat_store.get_thread_meta.assert_called_with(
            "request-workflow",
            "lane-1",
        )


class TestAnalyticsCommand:
    @pytest.mark.asyncio
    async def test_analytics_no_telemetry_store(self, tmp_path):
        c = _build_concierge(tmp_path, telemetry_store=None)
        events = await _collect(c, _make_msg("/analytics"))
        assert len(events) == 1
        assert "not enabled" in events[0].content.lower()

    @pytest.mark.asyncio
    async def test_analytics_groups_by_concierge_stage_and_tier(self, tmp_path):
        store = InMemoryTelemetryStore()
        now = datetime.now(timezone.utc)
        await store.record(TelemetryEvent(
            event_type="chat_turn",
            timestamp=now - timedelta(days=1),
            total_tokens=300,
            estimated_cost=0.003,
            duration_ms=800,
            metadata={
                "concierge_stage": "workflow_build",
                "session_tier": 2,
                "route_source": "fast_lexical",
            },
        ))
        await store.record(TelemetryEvent(
            event_type="chat_turn",
            timestamp=now - timedelta(days=1),
            total_tokens=120,
            estimated_cost=0.001,
            duration_ms=400,
            metadata={
                "concierge_stage": "conversation",
                "session_tier": 1,
                "route_source": "llm",
            },
        ))
        c = _build_concierge(tmp_path, telemetry_store=store)
        events = await _collect(
            c,
            _make_msg("/analytics --by concierge_stage,session_tier,route_source --days 30"),
        )

        assert len(events) == 1
        content = events[0].content
        assert "Telemetry Analytics" in content
        assert "workflow_build" in content
        assert "session_tier=2" in content
        assert "route_source=fast_lexical" in content
        assert "conversation" in content

    @pytest.mark.asyncio
    async def test_analytics_reports_session_modes_models_and_hours(self, tmp_path):
        store = InMemoryTelemetryStore()
        timestamp = datetime(2026, 3, 25, 10, 15, tzinfo=timezone.utc)
        await store.record(TelemetryEvent(
            event_type="chat_turn",
            session_id="test-surface",
            model="gpt-4.1",
            model_used="gpt-4.1",
            chat_mode="plan",
            total_tokens=120,
            estimated_cost=0.0012,
            timestamp=timestamp,
        ))
        await store.record(TelemetryEvent(
            event_type="gateway_call",
            session_id="test-surface",
            model="gpt-4.1",
            model_used="gpt-4.1",
            chat_mode="plan",
            total_tokens=120,
            estimated_cost=0.0012,
            timestamp=timestamp,
        ))

        c = _build_concierge(tmp_path, telemetry_store=store)
        events = await _collect(c, _make_msg("/analytics"))

        assert len(events) == 1
        content = events[0].content
        assert "Session Analytics" in content
        assert "gateway calls" in content
        assert "`plan`" in content
        assert "`gpt-4.1`" in content
        assert "10:00 UTC" in content


class TestGatewayTelemetry:
    @pytest.mark.asyncio
    async def test_process_records_gateway_call_telemetry_with_turn_context(self, tmp_path):
        store = InMemoryTelemetryStore()
        c = _build_concierge(tmp_path, telemetry_store=store)

        async def _fake_dispatch(_msg: SurfaceMessage):
            c._record_gateway_call_telemetry(GatewayCall(
                model="gpt-4.1",
                started_at=0.0,
                elapsed_ms=25.0,
                usage={
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "total_tokens": 100,
                },
            ))
            yield ChatCompleteEvent(
                message_id="m1",
                content="done",
                token_usage={
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "total_tokens": 100,
                },
                estimated_cost=0.001,
                context_window=0,
                graph_revision="",
            )

        c._tiered_dispatcher.dispatch = _fake_dispatch

        msg = _make_msg("plan the rollout", external_id="session-1")
        msg.metadata = {"mode": "plan"}
        await _collect(c, msg)
        await asyncio.sleep(0)

        events = await store.query(TelemetryQuery(session_id="session-1", limit=10))
        gateway_events = [event for event in events if event.event_type == "gateway_call"]
        turn_events = [event for event in events if event.event_type == "chat_turn"]

        assert len(gateway_events) == 1
        assert gateway_events[0].chat_mode == "plan"
        assert gateway_events[0].model_used == "gpt-4.1"
        assert len(turn_events) == 1
        assert turn_events[0].chat_mode == "plan"
        assert turn_events[0].model_used == "gpt-4.1"


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

    @pytest.mark.asyncio
    async def test_retry_drops_stale_autonomy_metadata(self, tmp_path):
        c = _build_concierge(tmp_path)
        project = c.project_store.create_project("test", "test-surface")
        task = c.project_store.add_task(project.project_id, "task", "test-surface")
        c.project_store.append_turn(
            project.project_id,
            task.task_id,
            TaskTurn(
                role="user",
                content="Do the task",
                intent="agent",
                metadata={
                    "autonomy_preference": "careful",
                    "autonomy_resolution": {"effective_level": "careful"},
                },
            ),
            "test-surface",
        )

        captured: dict[str, Any] = {}
        original_dispatch = c._tiered_dispatcher.dispatch

        async def _capture_dispatch(msg: SurfaceMessage):
            captured["metadata"] = dict(msg.metadata)
            async for event in original_dispatch(msg):
                yield event

        c._tiered_dispatcher.dispatch = _capture_dispatch

        _ = await _collect(c, _make_msg("/retry"))

        assert "autonomy_preference" not in captured["metadata"]
        assert "autonomy_resolution" not in captured["metadata"]
