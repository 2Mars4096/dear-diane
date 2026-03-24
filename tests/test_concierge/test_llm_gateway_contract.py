from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.server.concierge.runtime import Concierge
from dan.server.concierge.tier_executors import MultiStepExecutor


@pytest.mark.asyncio
async def test_triage_llm_complete_uses_public_chat_manager_gateway() -> None:
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=SimpleNamespace(text="ok"))
    resolve_calls: list[dict[str, Any]] = []

    class FakeChatManager:
        default_llm_model = "default-model"

        def resolve_llm_provider(
            self,
            *,
            model: str | None = None,
            pii_session_key: str | None = None,
        ) -> Any:
            resolve_calls.append(
                {
                    "model": model,
                    "pii_session_key": pii_session_key,
                }
            )
            return provider

    concierge = Concierge(
        project_store=MagicMock(),
        chat_manager=FakeChatManager(),
        capability_context=MagicMock(),
    )
    concierge._current_surface_id = "cli-user"

    with patch.object(concierge, "_resolve_triage_model", return_value="triage-model"):
        result = await concierge._triage_llm_complete(
            [{"role": "user", "content": "hello"}]
        )

    assert result == "ok"
    assert resolve_calls == [
        {"model": "triage-model", "pii_session_key": "cli-user"}
    ]


@pytest.mark.asyncio
async def test_cheap_llm_complete_uses_public_chat_manager_gateway() -> None:
    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=SimpleNamespace(
            text="synthetic response",
            usage={"prompt_tokens": 11, "completion_tokens": 7},
        )
    )
    resolve_calls: list[dict[str, Any]] = []

    class FakeChatManager:
        default_llm_model = "chat-model"
        _resource_tracker = None

        def resolve_llm_provider(
            self,
            *,
            model: str | None = None,
            pii_session_key: str | None = None,
        ) -> Any:
            resolve_calls.append(
                {
                    "model": model,
                    "pii_session_key": pii_session_key,
                }
            )
            return provider

    concierge = SimpleNamespace(
        chat_manager=FakeChatManager(),
        _resource_tracker=None,
        _tier_resolver=None,
    )
    executor = MultiStepExecutor(concierge, dispatcher=MagicMock())
    session = SimpleNamespace(
        msg=SimpleNamespace(
            external_id="cli-user",
            session_id="",
            metadata={},
        ),
        context=None,
    )

    text, usage = await executor._cheap_llm_complete(
        session,
        system_prompt="system",
        user_prompt="user",
    )

    assert text == "synthetic response"
    assert usage == {"prompt_tokens": 11, "completion_tokens": 7}
    assert resolve_calls == [
        {"model": "chat-model", "pii_session_key": "cli-user"}
    ]
