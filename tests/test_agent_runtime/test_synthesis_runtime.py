from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from dan.agent_runtime.synthesis_runtime import (
    cheap_llm_complete,
    maybe_llm_synthesize,
    plan_decomposition,
)


class _FakeProvider:
    def __init__(self, response_text: str, *, error: Exception | None = None) -> None:
        self._response_text = response_text
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                **kwargs,
            }
        )
        if self._error is not None:
            raise self._error
        return SimpleNamespace(
            text=self._response_text,
            usage={"prompt_tokens": 11, "completion_tokens": 7},
        )


class _FakeProviderRegistry:
    def __init__(self, provider: _FakeProvider) -> None:
        self._provider = provider
        self.resolved_models: list[str] = []

    def resolve(self, model: str) -> _FakeProvider:
        self.resolved_models.append(model)
        return self._provider


@pytest.mark.asyncio
async def test_cheap_llm_complete_uses_model_gateway_when_available() -> None:
    gateway = MagicMock()
    gateway.complete = AsyncMock(return_value=SimpleNamespace(text="via-gateway"))

    class FakeChatManager:
        def __init__(self) -> None:
            self.model_gateway = gateway

    concierge = SimpleNamespace(chat_manager=FakeChatManager(), _resource_tracker=None)
    session = SimpleNamespace(
        msg=SimpleNamespace(
            external_id="cli-user",
            session_id="",
            metadata={},
        ),
        context=None,
    )

    text, usage = await cheap_llm_complete(
        concierge,
        session,
        system_prompt="system",
        user_prompt="user",
        model_override="chat-model",
    )

    assert text == "via-gateway"
    assert usage == {}
    gateway.complete.assert_awaited_once()
    assert gateway.complete.await_args.kwargs["model"] == "chat-model"
    assert gateway.complete.await_args.kwargs["temperature"] == 0.0
    assert gateway.complete.await_args.kwargs["pii_session"] is not None


@pytest.mark.asyncio
async def test_plan_decomposition_falls_back_to_split_when_llm_unavailable() -> None:
    class FakeChatManager:
        default_llm_model = "chat-model"
        _resource_tracker = None

        def resolve_llm_provider(
            self,
            *,
            model: str | None = None,
            pii_session_key: str | None = None,
        ) -> Any:
            raise AssertionError("provider fallback should not be used")

    concierge = SimpleNamespace(chat_manager=FakeChatManager(), _resource_tracker=None)
    session = SimpleNamespace(
        task="Search docs and summarize findings",
        triage=SimpleNamespace(goal="Search docs and summarize findings", deliverable="Findings summary"),
    )

    subtasks, usage = await plan_decomposition(
        concierge,
        session,
        model_override="chat-model",
    )

    assert subtasks == ["Search docs", "summarize findings"]
    assert usage == {}


@pytest.mark.asyncio
async def test_maybe_llm_synthesize_uses_model_gateway_when_available() -> None:
    gateway = MagicMock()
    gateway.complete = AsyncMock(
        return_value=SimpleNamespace(
            text="Unified summary.",
            usage={"prompt_tokens": 11, "completion_tokens": 7},
        )
    )

    class FakeChatManager:
        def __init__(self) -> None:
            self.model_gateway = gateway

    concierge = SimpleNamespace(chat_manager=FakeChatManager(), _resource_tracker=None)
    session = SimpleNamespace(
        msg=SimpleNamespace(
            external_id="cli-user",
            session_id="",
            metadata={},
        ),
        task="Summarize all work",
        triage=SimpleNamespace(
            goal="Summarize all work",
            deliverable="Unified summary",
            subtasks=["Part one", "Part two", "Part three"],
        ),
    )
    child_results = {
        "child-1": SimpleNamespace(content="Completed the first part.", error="", token_usage={}),
        "child-2": SimpleNamespace(content="Completed the second part.", error="", token_usage={}),
        "child-3": SimpleNamespace(content="Completed the third part.", error="", token_usage={}),
    }
    manager = SimpleNamespace(
        get=lambda child_id: SimpleNamespace(task=child_results[child_id].content),
    )

    text, usage = await maybe_llm_synthesize(
        concierge,
        session,
        child_results,
        manager,
        model_override="chat-model",
    )

    assert text == "Unified summary."
    assert usage == {"prompt_tokens": 11, "completion_tokens": 7}
    gateway.complete.assert_awaited_once()
    assert gateway.complete.await_args.kwargs["model"] == "chat-model"
    assert gateway.complete.await_args.kwargs["temperature"] == 0.0
    assert gateway.complete.await_args.kwargs["pii_session"] is not None
