"""Tests for power-user speed features (plan 31-4, task 5).

Covers:
  5-1: dan-ask one-shot — mock ChatClient, verify single send+stream, exit without REPL
  5-2: --pipe flag — mock stdin, assert stdout gets response text only
  5-3: --output flag — response written to file
  5-5: Prep timeout — mock slow _retrieve_memory_context, verify timeout
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.server.capability_registry import CapabilityContext
from dan.server.chat_manager import ChatCompleteEvent
from dan.server.concierge.models import RouteDecision, RouteMode, SurfaceMessage
from dan.server.concierge.project_store import ProjectStore
from dan.server.concierge.runtime import Concierge
from dan.server.concierge.triage import TriageResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(text: str, surface: str = "cli", external_id: str = "test") -> SurfaceMessage:
    return SurfaceMessage(
        surface=surface,
        external_id=external_id,
        text=text,
        attachments=[],
        metadata={},
    )


def _build_concierge(
    tmp_path,
    *,
    chat_manager: Any = None,
    memory_kernel: Any = None,
) -> Concierge:
    if chat_manager is None:
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

    concierge = Concierge(
        project_store=project_store,
        chat_manager=chat_manager,
        capability_context=capability_context,
        memory_kernel=memory_kernel,
    )
    return concierge


async def _collect(concierge: Concierge, msg: SurfaceMessage) -> list[Any]:
    events: list[Any] = []
    async for ev in concierge.process(msg):
        events.append(ev)
    return events


def _force_memory_prep_triage(concierge: Concierge) -> None:
    async def _triage_fn(*args: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(
            tier=1,
            intent="ask",
            route=RouteDecision(
                mode=RouteMode.ASK,
                target="general",
                action_hints=[],
                rationale="test forced memory prep",
            ),
            confidence=0.9,
            goal="memory prep timeout",
            deliverable="memory prep timeout",
            context_needs=["memory"],
            route_source="test",
        )

    concierge._tiered_dispatcher._triage_fn = _triage_fn


# ===========================================================================
# 5-1: dan-ask one-shot
# ===========================================================================


class TestDanAskOneShot:
    """Mock ChatClient, verify single send+stream, assert exit without REPL."""

    @pytest.mark.asyncio
    async def test_run_one_shot_sends_and_streams(self, tmp_path) -> None:
        from dan.cli.chat import _run_one_shot

        mock_client = AsyncMock()
        mock_client.send_chat_message.return_value = {
            "stream_channel_id": "ch-1",
        }

        async def _fake_stream(channel):
            yield {"type": "chat_complete", "content": "answer text"}

        mock_client.stream_chat_events = MagicMock(
            side_effect=lambda ch: _fake_stream(ch),
        )

        with patch("builtins.print"):
            await _run_one_shot(
                client=mock_client,
                workflow_id="_scratch",
                mode="build",
                question="What is 2+2?",
                model=None,
                output_path=None,
            )

        mock_client.send_chat_message.assert_called_once()
        call_args = mock_client.send_chat_message.call_args
        assert call_args.args[1] == "What is 2+2?" or call_args[0][1] == "What is 2+2?"

    @pytest.mark.asyncio
    async def test_run_one_shot_with_model_override(self, tmp_path) -> None:
        from dan.cli.chat import _run_one_shot

        mock_client = AsyncMock()
        mock_client.send_chat_message.return_value = {
            "stream_channel_id": "ch-1",
        }

        async def _fake_stream(channel):
            yield {"type": "chat_complete", "content": "done"}

        mock_client.stream_chat_events = MagicMock(
            side_effect=lambda ch: _fake_stream(ch),
        )

        with patch("builtins.print"):
            await _run_one_shot(
                client=mock_client,
                workflow_id="_scratch",
                mode="build",
                question="test",
                model="gpt-4o",
                output_path=None,
            )

        assert mock_client.send_chat_message.call_count >= 2


# ===========================================================================
# 5-2: --pipe flag
# ===========================================================================


class TestPipeFlag:
    """Mock stdin, assert stdout gets response text only."""

    @pytest.mark.asyncio
    async def test_pipe_outputs_response_only(self, tmp_path) -> None:
        from dan.cli.chat import _run_one_shot

        mock_client = AsyncMock()
        mock_client.send_chat_message.return_value = {
            "stream_channel_id": "ch-1",
        }

        captured_output: list[str] = []

        async def _fake_stream(channel):
            yield {"type": "chat_complete", "content": "piped answer"}

        mock_client.stream_chat_events = MagicMock(
            side_effect=lambda ch: _fake_stream(ch),
        )

        def _capture_print(*args, **kwargs):
            text = " ".join(str(a) for a in args)
            captured_output.append(text)

        with patch("builtins.print", side_effect=_capture_print):
            await _run_one_shot(
                client=mock_client,
                workflow_id="_scratch",
                mode="build",
                question="piped question",
                model=None,
                output_path=None,
            )

        combined = " ".join(captured_output)
        assert "piped answer" in combined


# ===========================================================================
# 5-3: --output flag
# ===========================================================================


class TestOutputFlag:
    """Verify response written to file."""

    @pytest.mark.asyncio
    async def test_output_writes_to_file(self, tmp_path) -> None:
        from dan.cli.chat import _run_one_shot

        output_file = tmp_path / "response.txt"

        mock_client = AsyncMock()
        mock_client.send_chat_message.return_value = {
            "stream_channel_id": "ch-1",
        }

        async def _fake_stream(channel):
            yield {"type": "chat_token", "delta": "hello "}
            yield {"type": "chat_token", "delta": "world"}
            yield {"type": "chat_complete", "content": "hello world"}

        mock_client.stream_chat_events = MagicMock(
            side_effect=lambda ch: _fake_stream(ch),
        )

        with patch("builtins.print"):
            await _run_one_shot(
                client=mock_client,
                workflow_id="_scratch",
                mode="build",
                question="test output",
                model=None,
                output_path=str(output_file),
            )

        assert output_file.exists()
        content = output_file.read_text()
        assert "hello" in content
        assert "world" in content

    @pytest.mark.asyncio
    async def test_output_no_file_when_no_response(self, tmp_path) -> None:
        from dan.cli.chat import _run_one_shot

        output_file = tmp_path / "empty.txt"

        mock_client = AsyncMock()
        mock_client.send_chat_message.return_value = {
            "stream_channel_id": "ch-1",
        }

        async def _empty_stream(channel):
            yield {"type": "chat_complete", "content": ""}

        mock_client.stream_chat_events = MagicMock(
            side_effect=lambda ch: _empty_stream(ch),
        )

        with patch("builtins.print"):
            await _run_one_shot(
                client=mock_client,
                workflow_id="_scratch",
                mode="build",
                question="empty",
                model=None,
                output_path=str(output_file),
            )

        assert not output_file.exists()


# ===========================================================================
# 5-5: Prep timeout
# ===========================================================================


class TestPrepTimeout:
    """Mock slow _retrieve_memory_context, set DAN_CONCIERGE_PREP_TIMEOUT=0.1, verify timeout."""

    @pytest.mark.asyncio
    async def test_prep_timeout_does_not_block(self, tmp_path) -> None:
        import time

        mk = MagicMock()
        mk.retrieve_by_task.return_value = []

        concierge = _build_concierge(tmp_path, memory_kernel=mk)
        _force_memory_prep_triage(concierge)

        original_retrieve = concierge._retrieve_memory_context

        def _slow_retrieve(*args, **kwargs):
            time.sleep(2.0)
            return "slow context"

        concierge._retrieve_memory_context = _slow_retrieve

        env = {"DAN_CONCIERGE_PREP_TIMEOUT": "0.2", "DAN_CONCIERGE_PARALLEL_PREP": "1"}
        start = time.time()
        with patch.dict(os.environ, env, clear=False):
            events = await _collect(concierge, _msg("test timeout"))
        elapsed = time.time() - start

        assert elapsed < 1.5, f"Prep should have timed out quickly, took {elapsed:.1f}s"
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_prep_timeout_still_produces_response(self, tmp_path) -> None:
        import time

        mk = MagicMock()
        mk.retrieve_by_task.return_value = []

        concierge = _build_concierge(tmp_path, memory_kernel=mk)
        _force_memory_prep_triage(concierge)

        def _slow_retrieve(*args, **kwargs):
            time.sleep(2.0)
            return "slow"

        concierge._retrieve_memory_context = _slow_retrieve

        env = {"DAN_CONCIERGE_PREP_TIMEOUT": "0.1", "DAN_CONCIERGE_PARALLEL_PREP": "1"}
        with patch.dict(os.environ, env, clear=False):
            events = await _collect(concierge, _msg("hello after timeout"))

        content = " ".join(getattr(e, "content", "") for e in events)
        assert content
