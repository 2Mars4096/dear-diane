"""Tests for progressive response UX module (plan 31-14)."""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from dan.server.concierge.progress_ux import (
    CheckpointOption,
    CheckpointOptions,
    CLIProgressRenderer,
    EditorProgressRenderer,
    InteractionRequest,
    ProgressPhase,
    ProgressRenderer,
    ProgressSession,
    TelegramProgressRenderer,
    VerbosityLevel,
    WhatsAppProgressRenderer,
    filter_result,
    format_plan_disclosure,
    format_quick_confirm,
    format_result_checkpoint,
    generate_preflight_questions,
    get_user_verbosity_override,
    handle_checkpoint_timeout,
    handle_progress_command,
    reset_user_verbosity_override,
    resolve_verbosity,
    result_checkpoint_enabled,
    should_checkpoint_result,
    should_preflight_clarify,
    wait_for_interaction,
)


# =========================================================================
# ProgressPhase model
# =========================================================================


class TestProgressPhaseModel:
    def test_defaults(self):
        phase = ProgressPhase(id="p1", name="Planning")
        assert phase.status == "pending"
        assert phase.started_at is None
        assert phase.completed_at is None
        assert phase.elapsed_seconds == 0.0
        assert phase.summary is None
        assert phase.sub_steps == []

    def test_all_fields(self):
        now = datetime.now(timezone.utc)
        phase = ProgressPhase(
            id="p1",
            name="Execution",
            status="active",
            started_at=now,
            elapsed_seconds=5.2,
            summary="Done",
            sub_steps=["step1", "step2"],
        )
        assert phase.status == "active"
        assert phase.started_at == now
        assert phase.elapsed_seconds == 5.2
        assert len(phase.sub_steps) == 2

    def test_status_literals(self):
        for status in ("pending", "active", "completed", "failed"):
            phase = ProgressPhase(id="p", name="n", status=status)
            assert phase.status == status

    def test_serialization_round_trip(self):
        phase = ProgressPhase(
            id="p1", name="Test", status="completed",
            started_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
            completed_at=datetime(2026, 3, 10, tzinfo=timezone.utc),
            elapsed_seconds=3.5,
            summary="OK",
            sub_steps=["a", "b"],
        )
        data = phase.model_dump()
        restored = ProgressPhase.model_validate(data)
        assert restored == phase


# =========================================================================
# CheckpointOptions model
# =========================================================================


class TestCheckpointModels:
    def test_checkpoint_option_defaults(self):
        opt = CheckpointOption(label="Full", value="full")
        assert opt.is_default is False
        assert opt.is_safe_default is False

    def test_checkpoint_options_structure(self):
        opts = CheckpointOptions(
            summary="How much detail?",
            options=[
                CheckpointOption(label="Full", value="full", is_default=True),
                CheckpointOption(label="Summary", value="summary"),
            ],
        )
        assert len(opts.options) == 2
        assert opts.options[0].is_default is True

    def test_interaction_request_defaults(self):
        req = InteractionRequest(
            kind="required_clarification",
            checkpoint=CheckpointOptions(
                summary="Which file?",
                options=[
                    CheckpointOption(label="A", value="a"),
                    CheckpointOption(label="B", value="b"),
                ],
            ),
        )
        assert req.timeout_seconds == 300.0
        assert req.kind == "required_clarification"

    def test_interaction_request_advisory(self):
        req = InteractionRequest(
            kind="advisory_checkpoint",
            checkpoint=CheckpointOptions(
                summary="Proceed?",
                options=[
                    CheckpointOption(label="Yes", value="yes", is_safe_default=True),
                    CheckpointOption(label="No", value="no"),
                ],
            ),
            timeout_seconds=60.0,
        )
        assert req.kind == "advisory_checkpoint"
        assert req.timeout_seconds == 60.0

    def test_safe_default_flag(self):
        opt = CheckpointOption(label="Safe", value="safe", is_safe_default=True)
        assert opt.is_safe_default is True


# =========================================================================
# ProgressSession
# =========================================================================


class TestProgressSession:
    def _make_session(self, throttle: float = 0.0) -> ProgressSession:
        renderer = CLIProgressRenderer()
        return ProgressSession(renderer, surface_type="cli", throttle_seconds=throttle)

    def test_start_phase(self):
        session = self._make_session()
        phase = session.start_phase("p1", "Planning")
        assert phase.id == "p1"
        assert phase.status == "active"
        assert phase.started_at is not None
        assert session.get_current_phase() is phase

    def test_update_phase(self):
        session = self._make_session()
        session.start_phase("p1", "Exec")
        session.update_phase("p1", "Step 1")
        phase = session.get_current_phase()
        assert phase is not None
        assert "Step 1" in phase.sub_steps

    def test_complete_phase(self):
        session = self._make_session()
        session.start_phase("p1", "Exec")
        session.complete_phase("p1", "Done")
        phases = session.get_all_phases()
        assert phases[0].status == "completed"
        assert phases[0].summary == "Done"
        assert phases[0].completed_at is not None
        assert session.get_current_phase() is None

    def test_multiple_phases(self):
        session = self._make_session()
        session.start_phase("p1", "Plan")
        session.complete_phase("p1", "Plan done")
        session.start_phase("p2", "Execute")
        session.complete_phase("p2", "Execute done")
        session.start_phase("p3", "Result")
        assert len(session.get_all_phases()) == 3
        assert session.get_current_phase().id == "p3"

    def test_elapsed_total(self):
        session = self._make_session()
        assert session.elapsed_total() >= 0.0

    def test_throttling_blocks_rapid_updates(self):
        session = self._make_session(throttle=10.0)
        session.start_phase("p1", "Test")
        session.update_phase("p1", "first")
        session.update_phase("p1", "second")
        session.update_phase("p1", "third")
        phase = session.get_current_phase()
        assert phase is not None
        assert len(phase.sub_steps) == 1

    def test_throttling_allows_after_interval(self):
        session = self._make_session(throttle=0.0)
        session.start_phase("p1", "Test")
        session.update_phase("p1", "first")
        session.update_phase("p1", "second")
        phase = session.get_current_phase()
        assert phase is not None
        assert len(phase.sub_steps) == 2

    def test_update_nonexistent_phase(self):
        session = self._make_session()
        session.update_phase("nonexistent", "nope")

    def test_complete_nonexistent_phase(self):
        session = self._make_session()
        session.complete_phase("nonexistent", "nope")

    def test_get_current_phase_none(self):
        session = self._make_session()
        assert session.get_current_phase() is None

    def test_elapsed_seconds_tracked(self):
        session = self._make_session()
        phase = session.start_phase("p1", "Test")
        time.sleep(0.05)
        session.update_phase("p1", "step")
        updated = session.get_current_phase()
        assert updated is not None
        assert updated.elapsed_seconds > 0

    def test_phase_order_preserved(self):
        session = self._make_session()
        session.start_phase("a", "A")
        session.start_phase("b", "B")
        session.start_phase("c", "C")
        ids = [p.id for p in session.get_all_phases()]
        assert ids == ["a", "b", "c"]


# =========================================================================
# CLIProgressRenderer
# =========================================================================


class TestCLIProgressRenderer:
    @pytest.mark.asyncio
    async def test_announce_plan(self):
        r = CLIProgressRenderer()
        await r.announce_plan(["Step 1", "Step 2"], estimated_time=10.0)
        assert len(r.output) == 1
        assert "Step 1" in r.output[0]
        assert "~10s" in r.output[0]

    @pytest.mark.asyncio
    async def test_announce_plan_no_eta(self):
        r = CLIProgressRenderer()
        await r.announce_plan(["A", "B"])
        assert "Plan:" in r.output[0]
        assert "~" not in r.output[0]

    @pytest.mark.asyncio
    async def test_phase_update(self):
        r = CLIProgressRenderer()
        await r.phase_update("p1", "Running script")
        assert r.output[0] == "▶ Running script..."

    @pytest.mark.asyncio
    async def test_phase_complete(self):
        r = CLIProgressRenderer()
        await r.phase_complete("p1", "Script done (3s)")
        assert r.output[0] == "✓ Script done (3s)"

    @pytest.mark.asyncio
    async def test_checkpoint_returns_default(self):
        r = CLIProgressRenderer()
        opts = CheckpointOptions(
            summary="Detail level?",
            options=[
                CheckpointOption(label="Full", value="full", is_default=True),
                CheckpointOption(label="Summary", value="summary"),
            ],
        )
        result = await r.checkpoint(opts)
        assert result == "full"
        assert "[default]" in r.output[0]

    @pytest.mark.asyncio
    async def test_checkpoint_no_default(self):
        r = CLIProgressRenderer()
        opts = CheckpointOptions(
            summary="Choose:",
            options=[
                CheckpointOption(label="A", value="a"),
                CheckpointOption(label="B", value="b"),
            ],
        )
        result = await r.checkpoint(opts)
        assert result is None

    @pytest.mark.asyncio
    async def test_deliver_result(self):
        r = CLIProgressRenderer()
        await r.deliver_result("Here is the result.")
        assert r.output[0] == "Here is the result."

    @pytest.mark.asyncio
    async def test_heartbeat(self):
        r = CLIProgressRenderer()
        await r.heartbeat(45.0, "Still processing")
        assert "45s" in r.output[0]
        assert "Still processing" in r.output[0]

    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        r = CLIProgressRenderer()
        await r.announce_plan(["A", "B"])
        await r.phase_update("p1", "Doing A")
        await r.phase_complete("p1", "A done")
        await r.phase_update("p2", "Doing B")
        await r.phase_complete("p2", "B done")
        await r.deliver_result("Final output")
        assert len(r.output) == 6


# =========================================================================
# TelegramProgressRenderer
# =========================================================================


class TestTelegramProgressRenderer:
    def _make_renderer(self) -> tuple[TelegramProgressRenderer, AsyncMock, AsyncMock]:
        send_fn = AsyncMock(side_effect=lambda text: len(text))
        edit_fn = AsyncMock()
        renderer = TelegramProgressRenderer(send_fn, edit_fn)
        return renderer, send_fn, edit_fn

    @pytest.mark.asyncio
    async def test_announce_plan_sends_message(self):
        r, send_fn, _ = self._make_renderer()
        await r.announce_plan(["Step 1", "Step 2"], estimated_time=5.0)
        send_fn.assert_called_once()
        text = send_fn.call_args[0][0]
        assert "Step 1" in text
        assert "~5s" in text

    @pytest.mark.asyncio
    async def test_phase_update_edits_message(self):
        r, send_fn, edit_fn = self._make_renderer()
        await r.announce_plan(["Step 1"])
        await r.phase_update("p1", "Running step 1")
        edit_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_phase_complete_edits(self):
        r, send_fn, edit_fn = self._make_renderer()
        await r.announce_plan(["Step 1"])
        await r.phase_complete("p1", "Done in 3s")
        edit_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_checkpoint_sends_new_message(self):
        r, send_fn, _ = self._make_renderer()
        opts = CheckpointOptions(
            summary="Proceed?",
            options=[
                CheckpointOption(label="Yes", value="yes", is_default=True),
                CheckpointOption(label="No", value="no"),
            ],
        )
        result = await r.checkpoint(opts)
        assert result == "yes"
        assert send_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_deliver_result_sends_message(self):
        r, send_fn, _ = self._make_renderer()
        await r.deliver_result("Result text here")
        send_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_long_content_truncated(self):
        r, send_fn, _ = self._make_renderer()
        long_text = "x" * 5000
        await r.deliver_result(long_text)
        sent_text = send_fn.call_args[0][0]
        assert len(sent_text) <= 4096
        assert "[truncated]" in sent_text

    @pytest.mark.asyncio
    async def test_char_limit_forces_new_message(self):
        r, send_fn, edit_fn = self._make_renderer()
        r._current_message_id = 1
        r._current_text = "x" * 4090
        await r.phase_update("p1", "a very long label")
        send_fn.assert_called_once()
        edit_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_heartbeat_edits_existing(self):
        r, send_fn, edit_fn = self._make_renderer()
        await r.announce_plan(["Step"])
        await r.heartbeat(30.0, "Still going")
        edit_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_messages_sent_tracking(self):
        r, _, _ = self._make_renderer()
        await r.announce_plan(["A"])
        await r.deliver_result("Done")
        assert len(r.messages_sent) == 2


# =========================================================================
# WhatsAppProgressRenderer
# =========================================================================


class TestWhatsAppProgressRenderer:
    def _make_renderer(self) -> tuple[WhatsAppProgressRenderer, AsyncMock]:
        send_fn = AsyncMock()
        renderer = WhatsAppProgressRenderer(send_fn)
        return renderer, send_fn

    @pytest.mark.asyncio
    async def test_bookend_pattern(self):
        """WhatsApp sends exactly: 1 start message + 1 result message."""
        r, send_fn = self._make_renderer()
        await r.announce_plan(["A", "B", "C"])
        await r.phase_update("p1", "Doing A")
        await r.phase_complete("p1", "A done")
        await r.phase_update("p2", "Doing B")
        await r.phase_complete("p2", "B done")
        await r.deliver_result("Final result")

        assert send_fn.call_count == 2
        assert len(r.messages_sent) == 2

    @pytest.mark.asyncio
    async def test_phase_updates_suppressed(self):
        r, send_fn = self._make_renderer()
        await r.phase_update("p1", "silent")
        send_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_phase_complete_suppressed(self):
        r, send_fn = self._make_renderer()
        await r.phase_complete("p1", "silent")
        send_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_heartbeat_only_once(self):
        r, send_fn = self._make_renderer()
        await r.heartbeat(400.0, "Still working")
        await r.heartbeat(500.0, "Still working more")
        assert send_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_heartbeat_suppressed_below_threshold(self):
        r, send_fn = self._make_renderer()
        await r.heartbeat(60.0, "Not long enough")
        send_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_checkpoint_sends_message(self):
        r, send_fn = self._make_renderer()
        opts = CheckpointOptions(
            summary="Which format?",
            options=[
                CheckpointOption(label="CSV", value="csv", is_default=True),
                CheckpointOption(label="JSON", value="json"),
            ],
        )
        result = await r.checkpoint(opts)
        assert result == "csv"
        assert send_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_announce_plan_with_eta(self):
        r, send_fn = self._make_renderer()
        await r.announce_plan(["Run regression"], estimated_time=30.0)
        text = send_fn.call_args[0][0]
        assert "~30s" in text

    @pytest.mark.asyncio
    async def test_announce_plan_without_eta(self):
        r, send_fn = self._make_renderer()
        await r.announce_plan(["Do something"])
        text = send_fn.call_args[0][0]
        assert "ETA" not in text


# =========================================================================
# EditorProgressRenderer
# =========================================================================


class TestEditorProgressRenderer:
    @pytest.mark.asyncio
    async def test_announce_plan(self):
        send_fn = AsyncMock()
        r = EditorProgressRenderer(send_fn)
        await r.announce_plan(["Step 1", "Step 2"], estimated_time=8.0)
        send_fn.assert_called_once()
        assert "**Plan**" in r.output[0]
        assert "~8s" in r.output[0]

    @pytest.mark.asyncio
    async def test_phase_update(self):
        send_fn = AsyncMock()
        r = EditorProgressRenderer(send_fn)
        await r.phase_update("p1", "Loading data")
        assert "▸ Loading data" in r.output[0]

    @pytest.mark.asyncio
    async def test_phase_complete(self):
        send_fn = AsyncMock()
        r = EditorProgressRenderer(send_fn)
        await r.phase_complete("p1", "Data loaded")
        assert "✓ Data loaded" in r.output[0]

    @pytest.mark.asyncio
    async def test_deliver_result(self):
        send_fn = AsyncMock()
        r = EditorProgressRenderer(send_fn)
        await r.deliver_result("Output")
        assert r.output[0] == "Output"

    @pytest.mark.asyncio
    async def test_heartbeat(self):
        send_fn = AsyncMock()
        r = EditorProgressRenderer(send_fn)
        await r.heartbeat(12.0, "Working")
        assert "12s" in r.output[0]

    @pytest.mark.asyncio
    async def test_checkpoint(self):
        send_fn = AsyncMock()
        r = EditorProgressRenderer(send_fn)
        opts = CheckpointOptions(
            summary="Review?",
            options=[
                CheckpointOption(label="Yes", value="yes"),
                CheckpointOption(label="No", value="no", is_default=True),
            ],
        )
        result = await r.checkpoint(opts)
        assert result == "no"
        assert "(default)" in r.output[0]


# =========================================================================
# Verbosity resolution
# =========================================================================


class TestVerbosityResolution:
    def test_editor_defaults_to_full(self):
        assert resolve_verbosity("editor") == "full"

    def test_cli_defaults_to_full(self):
        assert resolve_verbosity("cli") == "full"

    def test_telegram_defaults_to_compact(self):
        assert resolve_verbosity("telegram") == "compact"

    def test_whatsapp_defaults_to_minimal(self):
        assert resolve_verbosity("whatsapp") == "minimal"

    def test_whatsapp_web_defaults_to_minimal(self):
        assert resolve_verbosity("whatsapp-web") == "minimal"

    def test_unknown_surface_defaults_to_compact(self):
        assert resolve_verbosity("unknown_surface") == "compact"

    def test_env_var_override(self):
        with patch.dict(os.environ, {"DAN_PROGRESS_VERBOSITY": "minimal"}):
            assert resolve_verbosity("editor") == "minimal"

    def test_env_var_override_case_insensitive(self):
        with patch.dict(os.environ, {"DAN_PROGRESS_VERBOSITY": "FULL"}):
            assert resolve_verbosity("telegram") == "full"

    def test_env_var_invalid_ignored(self):
        with patch.dict(os.environ, {"DAN_PROGRESS_VERBOSITY": "bogus"}):
            assert resolve_verbosity("editor") == "full"


# =========================================================================
# Pre-flight clarification
# =========================================================================


class TestPreflightClarification:
    def test_disabled_returns_none(self):
        with patch.dict(os.environ, {"DAN_PREFLIGHT_CLARIFY": "0"}):
            result = generate_preflight_questions("anything", {"estimated_time": 100.0})
            assert result is None

    def test_below_threshold_returns_none(self):
        result = generate_preflight_questions(
            "run analysis",
            {"estimated_time": 2.0},
        )
        assert result is None

    def test_above_threshold_with_dataset(self):
        result = generate_preflight_questions(
            "Analyze the dataset",
            {"estimated_time": 30.0},
        )
        assert result is not None
        assert any("column" in q.lower() for q in result)

    def test_above_threshold_with_model(self):
        result = generate_preflight_questions(
            "Train a model on this data",
            {"estimated_time": 60.0},
        )
        assert result is not None
        assert any("model" in q.lower() for q in result)

    def test_above_threshold_with_multiple_files(self):
        result = generate_preflight_questions(
            "Process the data",
            {"estimated_time": 15.0, "multiple_files": True},
        )
        assert result is not None
        assert any("file" in q.lower() for q in result)

    def test_no_questions_generated_returns_none(self):
        result = generate_preflight_questions(
            "simple task with nothing triggering",
            {"estimated_time": 20.0},
        )
        assert result is None

    def test_custom_threshold(self):
        with patch.dict(os.environ, {"DAN_PREFLIGHT_THRESHOLD_SECONDS": "5"}):
            result = generate_preflight_questions(
                "Analyze the dataset",
                {"estimated_time": 7.0},
            )
            assert result is not None

    def test_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DAN_PREFLIGHT_CLARIFY", None)
            result = generate_preflight_questions(
                "Analyze the dataset",
                {"estimated_time": 30.0},
            )
            assert result is not None


# =========================================================================
# Anti-noise: short tasks suppressed
# =========================================================================


class TestAntiNoise:
    def test_session_tracks_elapsed(self):
        """Tasks completing quickly should not show progress.

        The anti-noise pattern checks ``session.elapsed_total() < 3.0`` before
        emitting the first progress block.  We verify the timer is accurate.
        """
        session = ProgressSession(
            CLIProgressRenderer(), surface_type="cli", throttle_seconds=0.0,
        )
        assert session.elapsed_total() < 1.0

    def test_throttle_prevents_burst(self):
        session = ProgressSession(
            CLIProgressRenderer(), surface_type="cli", throttle_seconds=100.0,
        )
        session.start_phase("p1", "Test")
        for i in range(50):
            session.update_phase("p1", f"step-{i}")
        phase = session.get_current_phase()
        assert phase is not None
        assert len(phase.sub_steps) == 1


# =========================================================================
# Command parsing
# =========================================================================


class TestCommandParsing:
    def setup_method(self):
        reset_user_verbosity_override()

    def teardown_method(self):
        reset_user_verbosity_override()

    def test_show_current_default(self):
        result = handle_progress_command("/progress")
        assert "auto per surface" in result

    def test_set_full(self):
        result = handle_progress_command("/progress full")
        assert "full" in result
        assert get_user_verbosity_override() == "full"

    def test_set_compact(self):
        result = handle_progress_command("/progress compact")
        assert "compact" in result
        assert get_user_verbosity_override() == "compact"

    def test_set_minimal(self):
        result = handle_progress_command("/progress minimal")
        assert "minimal" in result
        assert get_user_verbosity_override() == "minimal"

    def test_invalid_level(self):
        result = handle_progress_command("/progress verbose")
        assert "Unknown" in result
        assert get_user_verbosity_override() is None

    def test_show_after_set(self):
        handle_progress_command("/progress compact")
        result = handle_progress_command("/progress")
        assert "compact" in result

    def test_overrides_are_scoped_per_surface(self):
        handle_progress_command("/progress compact", surface_id="user-a")
        assert get_user_verbosity_override("user-a") == "compact"
        assert get_user_verbosity_override("user-b") is None
        assert "auto per surface" in handle_progress_command("/progress", surface_id="user-b")


# =========================================================================
# Protocol conformance
# =========================================================================


class TestProtocolConformance:
    def test_cli_renderer_is_progress_renderer(self):
        assert isinstance(CLIProgressRenderer(), ProgressRenderer)

    def test_telegram_renderer_is_progress_renderer(self):
        r = TelegramProgressRenderer(AsyncMock(), AsyncMock())
        assert isinstance(r, ProgressRenderer)

    def test_whatsapp_renderer_is_progress_renderer(self):
        r = WhatsAppProgressRenderer(AsyncMock())
        assert isinstance(r, ProgressRenderer)

    def test_editor_renderer_is_progress_renderer(self):
        r = EditorProgressRenderer(AsyncMock())
        assert isinstance(r, ProgressRenderer)


# =========================================================================
# Integration: session + renderer full lifecycle
# =========================================================================


class TestIntegration:
    @pytest.mark.asyncio
    async def test_cli_full_lifecycle(self):
        """Simulate a 3-phase task through CLI renderer."""
        renderer = CLIProgressRenderer()
        session = ProgressSession(renderer, surface_type="cli", throttle_seconds=0.0)

        await renderer.announce_plan(["Fetch data", "Process", "Report"], estimated_time=15.0)
        p1 = session.start_phase("fetch", "Fetch data")
        await renderer.phase_update("fetch", "Connecting")
        session.update_phase("fetch", "Connecting")
        await renderer.phase_update("fetch", "Downloading 3 files")
        session.update_phase("fetch", "Downloading 3 files")
        await renderer.phase_complete("fetch", "Fetch done (2s)")
        session.complete_phase("fetch", "Fetch done (2s)")

        p2 = session.start_phase("process", "Process")
        await renderer.phase_update("process", "Analyzing")
        session.update_phase("process", "Analyzing")
        await renderer.phase_complete("process", "Process done (5s)")
        session.complete_phase("process", "Process done (5s)")

        p3 = session.start_phase("report", "Report")
        await renderer.deliver_result("Here is the report.")
        session.complete_phase("report", "Delivered")

        assert len(session.get_all_phases()) == 3
        assert all(p.status == "completed" for p in session.get_all_phases())
        assert len(renderer.output) >= 6

    @pytest.mark.asyncio
    async def test_telegram_lifecycle(self):
        send_fn = AsyncMock(side_effect=lambda text: 1)
        edit_fn = AsyncMock()
        renderer = TelegramProgressRenderer(send_fn, edit_fn)

        await renderer.announce_plan(["Step 1", "Step 2"])
        await renderer.phase_update("p1", "Running step 1")
        await renderer.phase_complete("p1", "Step 1 done")
        await renderer.deliver_result("Result")

        assert send_fn.call_count >= 2
        assert edit_fn.call_count >= 1

    @pytest.mark.asyncio
    async def test_whatsapp_lifecycle(self):
        send_fn = AsyncMock()
        renderer = WhatsAppProgressRenderer(send_fn)

        await renderer.announce_plan(["A", "B"])
        await renderer.phase_update("p1", "Working")
        await renderer.phase_complete("p1", "Done")
        await renderer.phase_update("p2", "More work")
        await renderer.phase_complete("p2", "Also done")
        await renderer.deliver_result("Final answer")

        assert send_fn.call_count == 2
        assert len(renderer.messages_sent) == 2

    @pytest.mark.asyncio
    async def test_checkpoint_interaction(self):
        renderer = CLIProgressRenderer()
        opts = CheckpointOptions(
            summary="How much detail?",
            options=[
                CheckpointOption(label="Full table", value="full"),
                CheckpointOption(label="Key variables only", value="key", is_default=True),
            ],
        )
        choice = await renderer.checkpoint(opts)
        assert choice == "key"


# =========================================================================
# Edge cases
# =========================================================================


class TestEdgeCases:
    def test_session_with_zero_throttle(self):
        session = ProgressSession(
            CLIProgressRenderer(), surface_type="cli", throttle_seconds=0.0,
        )
        session.start_phase("p1", "Test")
        for i in range(10):
            session.update_phase("p1", f"s{i}")
        phase = session.get_current_phase()
        assert phase is not None
        assert len(phase.sub_steps) == 10

    @pytest.mark.asyncio
    async def test_telegram_no_current_message(self):
        """phase_update without prior send creates a new message."""
        send_fn = AsyncMock(side_effect=lambda text: 42)
        edit_fn = AsyncMock()
        r = TelegramProgressRenderer(send_fn, edit_fn)
        await r.phase_update("p1", "First")
        send_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_telegram_phase_complete_no_current(self):
        send_fn = AsyncMock(side_effect=lambda text: 42)
        edit_fn = AsyncMock()
        r = TelegramProgressRenderer(send_fn, edit_fn)
        await r.phase_complete("p1", "Done")
        send_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_whatsapp_heartbeat_at_boundary(self):
        """Heartbeat at exactly the threshold should be sent."""
        send_fn = AsyncMock()
        r = WhatsAppProgressRenderer(send_fn)
        await r.heartbeat(300.0, "Exactly at threshold")
        send_fn.assert_called_once()

    def test_verbosity_level_type(self):
        level: VerbosityLevel = "full"
        assert level in ("full", "compact", "minimal")

    @pytest.mark.asyncio
    async def test_editor_announce_no_eta(self):
        send_fn = AsyncMock()
        r = EditorProgressRenderer(send_fn)
        await r.announce_plan(["Only step"])
        assert "~" not in r.output[0]


# =========================================================================
# Task 3-3: Plan Disclosure
# =========================================================================


class TestFormatPlanDisclosure:
    def test_basic_plan(self):
        text = format_plan_disclosure(["Fetch data", "Process", "Report"])
        assert "Plan:" in text
        assert "1. Fetch data" in text
        assert "2. Process" in text
        assert "3. Report" in text

    def test_plan_with_eta(self):
        text = format_plan_disclosure(["A", "B"], estimated_time=15.0)
        assert "~15s" in text

    def test_plan_without_eta(self):
        text = format_plan_disclosure(["A"])
        assert "~" not in text

    def test_empty_steps(self):
        text = format_plan_disclosure([])
        assert "Plan:" in text

    def test_single_step(self):
        text = format_plan_disclosure(["Only step"])
        assert "1. Only step" in text


class TestDiscloseplan:
    @pytest.mark.asyncio
    async def test_short_plan_no_review(self):
        renderer = CLIProgressRenderer()
        session = ProgressSession(renderer, surface_type="cli", throttle_seconds=0.0)
        result = await session.disclose_plan(["A", "B"], estimated_time=5.0)
        assert result is None
        assert len(renderer.output) == 1

    @pytest.mark.asyncio
    async def test_complex_plan_offers_review(self):
        renderer = CLIProgressRenderer()
        session = ProgressSession(renderer, surface_type="cli", throttle_seconds=0.0)
        result = await session.disclose_plan(
            ["A", "B", "C", "D"],
            estimated_time=20.0,
        )
        assert result == "proceed"
        assert len(renderer.output) == 2

    @pytest.mark.asyncio
    async def test_offer_review_explicit_true(self):
        renderer = CLIProgressRenderer()
        session = ProgressSession(renderer, surface_type="cli", throttle_seconds=0.0)
        result = await session.disclose_plan(["A"], offer_review=True)
        assert result == "proceed"
        assert len(renderer.output) == 2

    @pytest.mark.asyncio
    async def test_offer_review_explicit_false(self):
        renderer = CLIProgressRenderer()
        session = ProgressSession(renderer, surface_type="cli", throttle_seconds=0.0)
        result = await session.disclose_plan(
            ["A", "B", "C", "D", "E"],
            offer_review=False,
        )
        assert result is None
        assert len(renderer.output) == 1

    @pytest.mark.asyncio
    async def test_exactly_three_steps_no_review(self):
        renderer = CLIProgressRenderer()
        session = ProgressSession(renderer, surface_type="cli", throttle_seconds=0.0)
        result = await session.disclose_plan(["A", "B", "C"])
        assert result is None

    @pytest.mark.asyncio
    async def test_four_steps_triggers_review(self):
        renderer = CLIProgressRenderer()
        session = ProgressSession(renderer, surface_type="cli", throttle_seconds=0.0)
        result = await session.disclose_plan(["A", "B", "C", "D"])
        assert result is not None

    @pytest.mark.asyncio
    async def test_checkpoint_text_contains_review(self):
        renderer = CLIProgressRenderer()
        session = ProgressSession(renderer, surface_type="cli", throttle_seconds=0.0)
        await session.disclose_plan(["A", "B", "C", "D"])
        assert any("Review" in o for o in renderer.output)


# =========================================================================
# Task 3-5: Result Checkpoint
# =========================================================================


class TestShouldCheckpointResult:
    def test_below_threshold(self):
        assert should_checkpoint_result("short") is False

    def test_above_default_threshold(self):
        assert should_checkpoint_result("x" * 1001) is True

    def test_exactly_at_threshold(self):
        assert should_checkpoint_result("x" * 1000) is False

    def test_custom_threshold(self):
        assert should_checkpoint_result("x" * 50, threshold=40) is True
        assert should_checkpoint_result("x" * 30, threshold=40) is False


class TestFormatResultCheckpoint:
    def test_basic_checkpoint(self):
        opts = format_result_checkpoint("x" * 2000)
        assert len(opts.options) == 3
        values = {o.value for o in opts.options}
        assert "full" in values
        assert "summary" in values
        assert "focus" in values

    def test_summary_is_preview(self):
        content = "Hello world " * 200
        opts = format_result_checkpoint(content)
        assert opts.summary.endswith("...")
        assert len(opts.summary) <= 504

    def test_user_focus_in_label(self):
        opts = format_result_checkpoint("x" * 2000, user_focus="regression coefficients")
        focus_opt = [o for o in opts.options if o.value == "focus"][0]
        assert "regression coefficients" in focus_opt.label

    def test_no_user_focus_default_label(self):
        opts = format_result_checkpoint("x" * 2000)
        focus_opt = [o for o in opts.options if o.value == "focus"][0]
        assert "key findings" in focus_opt.label.lower()

    def test_summary_option_is_default(self):
        opts = format_result_checkpoint("x" * 2000)
        default_opts = [o for o in opts.options if o.is_default]
        assert len(default_opts) == 1
        assert default_opts[0].value == "summary"

    def test_summary_option_is_safe_default(self):
        opts = format_result_checkpoint("x" * 2000)
        safe = [o for o in opts.options if o.is_safe_default]
        assert len(safe) == 1
        assert safe[0].value == "summary"

    def test_short_content_no_ellipsis(self):
        opts = format_result_checkpoint("Short content", user_focus=None)
        assert not opts.summary.endswith("...")


# =========================================================================
# Task 4-2: Pre-flight cost/time threshold
# =========================================================================


class TestShouldPreflightClarify:
    def test_below_both_thresholds(self):
        assert should_preflight_clarify(5.0, 0.01) is False

    def test_above_time_threshold(self):
        assert should_preflight_clarify(15.0, 0.01) is True

    def test_above_cost_threshold(self):
        assert should_preflight_clarify(5.0, 0.50) is True

    def test_above_both_thresholds(self):
        assert should_preflight_clarify(20.0, 1.00) is True

    def test_exactly_at_time_threshold(self):
        assert should_preflight_clarify(10.0, 0.0) is True

    def test_exactly_at_cost_threshold(self):
        assert should_preflight_clarify(0.0, 0.10) is True

    def test_custom_config_thresholds(self):
        cfg = {"threshold_seconds": 60, "cost_threshold": 1.0}
        assert should_preflight_clarify(30.0, 0.50, cfg) is False
        assert should_preflight_clarify(60.0, 0.50, cfg) is True
        assert should_preflight_clarify(30.0, 1.00, cfg) is True

    def test_disabled_returns_false(self):
        with patch.dict(os.environ, {"DAN_PREFLIGHT_CLARIFY": "0"}):
            assert should_preflight_clarify(100.0, 10.0) is False

    def test_env_var_cost_threshold(self):
        with patch.dict(os.environ, {"DAN_PREFLIGHT_COST_THRESHOLD": "1.00"}):
            assert should_preflight_clarify(5.0, 0.50) is False
            assert should_preflight_clarify(5.0, 1.00) is True


# =========================================================================
# Task 4-3: Enhanced question generation
# =========================================================================


class TestEnhancedPreflightQuestions:
    def test_file_operations_ask_directory(self):
        result = generate_preflight_questions(
            "Move the file to another directory",
            {"estimated_time": 30.0},
        )
        assert result is not None
        assert any("directory" in q.lower() or "file" in q.lower() for q in result)

    def test_analysis_asks_key_variables(self):
        result = generate_preflight_questions(
            "Run a statistical analysis on this data",
            {"estimated_time": 20.0},
        )
        assert result is not None
        assert any("variable" in q.lower() or "metric" in q.lower() for q in result)

    def test_web_access_asks_url(self):
        result = generate_preflight_questions(
            "Fetch data from the web API",
            {"estimated_time": 15.0},
        )
        assert result is not None
        assert any("url" in q.lower() or "domain" in q.lower() for q in result)

    def test_max_three_questions(self):
        result = generate_preflight_questions(
            "Analyze the dataset and fetch url from web and process the file in directory",
            {"estimated_time": 30.0, "multiple_files": True},
        )
        assert result is not None
        assert len(result) <= 3

    def test_cost_based_trigger(self):
        result = generate_preflight_questions(
            "Analyze the dataset",
            {"estimated_time": 2.0, "estimated_cost": 0.50},
        )
        assert result is not None

    def test_neither_cost_nor_time_above(self):
        result = generate_preflight_questions(
            "Analyze the dataset",
            {"estimated_time": 2.0, "estimated_cost": 0.01},
        )
        assert result is None

    def test_backward_compat_no_plan_summary(self):
        result = generate_preflight_questions(
            "Analyze the dataset",
            {"estimated_time": 30.0},
        )
        assert result is not None


# =========================================================================
# Task 4-4: Quick-confirm mode
# =========================================================================


class TestFormatQuickConfirm:
    def test_basic_structure(self):
        req = format_quick_confirm(
            questions=["Which directory?", "Which model?"],
            defaults=["/data", "xgboost"],
        )
        assert req.kind == "advisory_checkpoint"
        assert req.timeout_seconds == 5.0
        assert "/data" in req.checkpoint.summary
        assert "xgboost" in req.checkpoint.summary

    def test_options_include_proceed(self):
        req = format_quick_confirm(["Q1"], ["D1"])
        values = {o.value for o in req.checkpoint.options}
        assert "proceed" in values
        assert "change" in values

    def test_proceed_is_safe_default(self):
        req = format_quick_confirm(["Q1"], ["D1"])
        proceed = [o for o in req.checkpoint.options if o.value == "proceed"][0]
        assert proceed.is_default is True
        assert proceed.is_safe_default is True

    def test_empty_defaults_filled(self):
        req = format_quick_confirm(["Q1", "Q2"], [])
        assert "(auto)" in req.checkpoint.summary

    def test_single_question(self):
        req = format_quick_confirm(["Which file?"], ["report.csv"])
        assert "report.csv" in req.checkpoint.summary

    def test_summary_ends_with_ok(self):
        req = format_quick_confirm(["Q"], ["D"])
        assert req.checkpoint.summary.strip().endswith("OK?")


# =========================================================================
# Task 5-3: Checkpoint auto-proceed / timeout
# =========================================================================


class TestHandleCheckpointTimeout:
    def test_returns_safe_default(self):
        cp = CheckpointOptions(
            summary="Choose:",
            options=[
                CheckpointOption(label="A", value="a"),
                CheckpointOption(label="B", value="b", is_safe_default=True),
            ],
        )
        assert handle_checkpoint_timeout(cp) == "b"

    def test_no_safe_default_returns_none(self):
        cp = CheckpointOptions(
            summary="Choose:",
            options=[
                CheckpointOption(label="A", value="a"),
                CheckpointOption(label="B", value="b"),
            ],
        )
        assert handle_checkpoint_timeout(cp) is None

    def test_first_safe_default_wins(self):
        cp = CheckpointOptions(
            summary="Choose:",
            options=[
                CheckpointOption(label="A", value="a", is_safe_default=True),
                CheckpointOption(label="B", value="b", is_safe_default=True),
            ],
        )
        assert handle_checkpoint_timeout(cp) == "a"

    def test_empty_options(self):
        cp = CheckpointOptions(summary="Choose:", options=[])
        assert handle_checkpoint_timeout(cp) is None

    def test_with_result_checkpoint(self):
        cp = format_result_checkpoint("x" * 2000)
        result = handle_checkpoint_timeout(cp)
        assert result == "summary"


# =========================================================================
# Task 5-4: Result filtering
# =========================================================================


class TestFilterResult:
    def test_full_returns_everything(self):
        content = "A" * 2000
        assert filter_result(content, "full") == content

    def test_summary_truncates(self):
        content = "word " * 500
        result = filter_result(content, "summary")
        assert len(result) <= 504
        assert result.endswith("...")

    def test_summary_short_content(self):
        result = filter_result("short", "summary")
        assert result == "short"

    def test_focus_filters_by_keywords(self):
        lines = [
            "Revenue was $1M in Q1",
            "Employee count: 50",
            "Revenue grew 20% YoY",
            "Office is in SF",
        ]
        content = "\n".join(lines)
        result = filter_result(content, "focus", "What about revenue?")
        assert "Revenue" in result
        assert "Employee" not in result
        assert "Office" not in result

    def test_focus_no_question_falls_back(self):
        content = "x" * 2000
        result = filter_result(content, "focus", "")
        assert len(result) <= 504

    def test_focus_no_matches_falls_back(self):
        content = "nothing relevant here\n" * 100
        result = filter_result(content, "focus", "quantum entanglement effects")
        assert len(result) <= 504

    def test_unknown_selection_returns_full(self):
        content = "some content"
        assert filter_result(content, "unknown_level") == content

    def test_case_insensitive_selection(self):
        content = "word " * 500
        result = filter_result(content, "SUMMARY")
        assert result.endswith("...")

    def test_whitespace_in_selection(self):
        content = "word " * 500
        result = filter_result(content, "  summary  ")
        assert result.endswith("...")

    def test_focus_limits_lines(self):
        lines = [f"revenue line {i}" for i in range(100)]
        content = "\n".join(lines)
        result = filter_result(content, "focus", "What about revenue?")
        assert result.count("\n") <= 29


# =========================================================================
# Task 3-5 gate: result_checkpoint_enabled
# =========================================================================


class TestResultCheckpointEnabled:
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DAN_RESULT_CHECKPOINT", None)
            assert result_checkpoint_enabled() is False

    def test_enabled_when_set_to_1(self):
        with patch.dict(os.environ, {"DAN_RESULT_CHECKPOINT": "1"}):
            assert result_checkpoint_enabled() is True

    def test_disabled_when_set_to_0(self):
        with patch.dict(os.environ, {"DAN_RESULT_CHECKPOINT": "0"}):
            assert result_checkpoint_enabled() is False

    def test_disabled_for_arbitrary_value(self):
        with patch.dict(os.environ, {"DAN_RESULT_CHECKPOINT": "yes"}):
            assert result_checkpoint_enabled() is False

    def test_whitespace_stripped(self):
        with patch.dict(os.environ, {"DAN_RESULT_CHECKPOINT": " 1 "}):
            assert result_checkpoint_enabled() is True


# =========================================================================
# Task 5-3: wait_for_interaction — async timeout / auto-proceed
# =========================================================================


class TestWaitForInteraction:
    @pytest.mark.asyncio
    async def test_advisory_checkpoint_auto_proceeds(self):
        renderer = CLIProgressRenderer()
        session = ProgressSession(renderer, surface_type="cli", throttle_seconds=0.0)
        interaction = InteractionRequest(
            kind="advisory_checkpoint",
            checkpoint=CheckpointOptions(
                summary="Proceed?",
                options=[
                    CheckpointOption(label="Go", value="go", is_default=True, is_safe_default=True),
                    CheckpointOption(label="Stop", value="stop"),
                ],
            ),
            timeout_seconds=5.0,
        )
        value, timed_out = await wait_for_interaction(interaction, session, "cli")
        assert value == "go"
        assert timed_out is False

    @pytest.mark.asyncio
    async def test_required_clarification_returns_renderer_response(self):
        renderer = CLIProgressRenderer()
        session = ProgressSession(renderer, surface_type="cli", throttle_seconds=0.0)
        interaction = InteractionRequest(
            kind="required_clarification",
            checkpoint=CheckpointOptions(
                summary="Which file?",
                options=[
                    CheckpointOption(label="A", value="a", is_default=True),
                    CheckpointOption(label="B", value="b"),
                ],
            ),
        )
        value, timed_out = await wait_for_interaction(interaction, session, "cli")
        assert value == "a"
        assert timed_out is False

    @pytest.mark.asyncio
    async def test_required_clarification_timeout_uses_safe_default(self):
        slow_renderer = AsyncMock(spec=ProgressRenderer)

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)
            return None

        slow_renderer.checkpoint = _hang
        session = ProgressSession(slow_renderer, surface_type="telegram", throttle_seconds=0.0)
        interaction = InteractionRequest(
            kind="required_clarification",
            checkpoint=CheckpointOptions(
                summary="Choose:",
                options=[
                    CheckpointOption(label="A", value="a"),
                    CheckpointOption(label="Safe", value="safe", is_safe_default=True),
                ],
            ),
            timeout_seconds=0.1,
        )
        value, timed_out = await wait_for_interaction(
            interaction, session, "telegram", timeout_override=0.1,
        )
        assert value == "safe"
        assert timed_out is True

    @pytest.mark.asyncio
    async def test_required_clarification_timeout_no_safe_default(self):
        slow_renderer = AsyncMock(spec=ProgressRenderer)

        async def _hang(*args, **kwargs):
            await asyncio.sleep(100)
            return None

        slow_renderer.checkpoint = _hang
        session = ProgressSession(slow_renderer, surface_type="telegram", throttle_seconds=0.0)
        interaction = InteractionRequest(
            kind="required_clarification",
            checkpoint=CheckpointOptions(
                summary="Choose:",
                options=[
                    CheckpointOption(label="A", value="a"),
                    CheckpointOption(label="B", value="b"),
                ],
            ),
            timeout_seconds=0.1,
        )
        value, timed_out = await wait_for_interaction(
            interaction, session, "telegram", timeout_override=0.1,
        )
        assert value is None
        assert timed_out is True

    @pytest.mark.asyncio
    async def test_advisory_always_returns_fast(self):
        """Advisory checkpoints never block, even with a slow renderer."""
        slow_renderer = AsyncMock(spec=ProgressRenderer)
        slow_renderer.checkpoint = AsyncMock(return_value="proceed")
        session = ProgressSession(slow_renderer, surface_type="cli", throttle_seconds=0.0)
        interaction = InteractionRequest(
            kind="advisory_checkpoint",
            checkpoint=CheckpointOptions(
                summary="OK?",
                options=[
                    CheckpointOption(label="OK", value="proceed", is_default=True, is_safe_default=True),
                ],
            ),
            timeout_seconds=5.0,
        )
        value, timed_out = await wait_for_interaction(interaction, session, "cli")
        assert value == "proceed"
        assert timed_out is False


# =========================================================================
# Task 5-4: apply_result_filter (async wrapper with LLM fallback)
# =========================================================================


class TestApplyResultFilter:
    @pytest.mark.asyncio
    async def test_full_selection_returns_everything(self):
        from dan.server.concierge.progress_ux import apply_result_filter
        content = "A" * 2000
        result = await apply_result_filter(content, "full", "What?")
        assert result == content

    @pytest.mark.asyncio
    async def test_summary_selection_truncates(self):
        from dan.server.concierge.progress_ux import apply_result_filter
        content = "word " * 500
        result = await apply_result_filter(content, "summary", "What?")
        assert len(result) <= 504
        assert result.endswith("...")

    @pytest.mark.asyncio
    async def test_focus_selection_filters_keywords(self):
        from dan.server.concierge.progress_ux import apply_result_filter
        lines = [
            "Revenue was $1M in Q1",
            "Employee count: 50",
            "Revenue grew 20% YoY",
        ]
        content = "\n".join(lines)
        result = await apply_result_filter(content, "focus", "What about revenue?")
        assert "Revenue" in result
        assert "Employee" not in result

    @pytest.mark.asyncio
    async def test_llm_fn_used_when_provided(self):
        from dan.server.concierge.progress_ux import apply_result_filter

        async def _mock_llm(prompt: str) -> str:
            return "LLM-formatted result"

        result = await apply_result_filter(
            "x" * 2000, "summary", "Q?", llm_fn=_mock_llm,
        )
        assert result == "LLM-formatted result"

    @pytest.mark.asyncio
    async def test_llm_fn_failure_falls_back_to_deterministic(self):
        from dan.server.concierge.progress_ux import apply_result_filter

        async def _failing_llm(prompt: str) -> str:
            raise RuntimeError("LLM unavailable")

        content = "word " * 500
        result = await apply_result_filter(
            content, "summary", "Q?", llm_fn=_failing_llm,
        )
        assert len(result) <= 504
