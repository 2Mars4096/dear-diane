"""Tests for Telegram surface hint resolution and capabilities."""

from __future__ import annotations

import pytest

from dan.server.chat_manager import _resolve_surface_hints


def test_resolve_surface_hints_supports_bot_specific_telegram_surfaces() -> None:
    hints = _resolve_surface_hints("telegram:research-bot", "test-model")
    assert "Surface: Telegram" in hints
    assert "research-bot" in hints
    assert "Telegram supports Markdown" in hints


# ── telegram_poll capability handler ──────────────────────────────


@pytest.mark.asyncio
async def test_telegram_poll_handler_valid() -> None:
    from dan.server.capability_handlers import handle_telegram_poll
    from dan.server.capability_registry import CapabilityContext

    ctx = CapabilityContext(workflow_id="test")
    result = await handle_telegram_poll(
        {"question": "Pick one", "options": ["A", "B", "C"]},
        ctx,
    )
    assert result.success is True
    assert result.data["poll_request"] is True
    assert result.data["question"] == "Pick one"
    assert result.data["options"] == ["A", "B", "C"]
    assert result.data["is_anonymous"] is False


@pytest.mark.asyncio
async def test_telegram_poll_handler_rejects_empty_question() -> None:
    from dan.server.capability_handlers import handle_telegram_poll
    from dan.server.capability_registry import CapabilityContext

    ctx = CapabilityContext(workflow_id="test")
    result = await handle_telegram_poll({"question": "", "options": ["A", "B"]}, ctx)
    assert result.success is False
    assert "required" in result.message.lower()


@pytest.mark.asyncio
async def test_telegram_poll_handler_rejects_too_few_options() -> None:
    from dan.server.capability_handlers import handle_telegram_poll
    from dan.server.capability_registry import CapabilityContext

    ctx = CapabilityContext(workflow_id="test")
    result = await handle_telegram_poll({"question": "Q?", "options": ["A"]}, ctx)
    assert result.success is False
    assert "2" in result.message


@pytest.mark.asyncio
async def test_telegram_poll_handler_rejects_too_many_options() -> None:
    from dan.server.capability_handlers import handle_telegram_poll
    from dan.server.capability_registry import CapabilityContext

    ctx = CapabilityContext(workflow_id="test")
    opts = [f"Option {i}" for i in range(11)]
    result = await handle_telegram_poll({"question": "Q?", "options": opts}, ctx)
    assert result.success is False
    assert "10" in result.message
