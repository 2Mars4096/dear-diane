from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.server.concierge.runtime import Concierge
from dan.server.concierge.tier_executors import MultiStepExecutor


@pytest.mark.asyncio
async def test_triage_llm_complete_uses_model_gateway_when_available() -> None:
    gateway = MagicMock()
    gateway.complete = AsyncMock(return_value=SimpleNamespace(text="via-gateway"))

    class FakeChatManager:
        default_llm_model = "default-model"

        def __init__(self) -> None:
            self.model_gateway = gateway

        def resolve_llm_provider(
            self,
            *,
            model: str | None = None,
            pii_session_key: str | None = None,
        ) -> Any:
            raise AssertionError("provider fallback should not be used")

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

    assert result == "via-gateway"
    gateway.complete.assert_awaited_once()
    assert gateway.complete.await_args.kwargs["model"] == "triage-model"
    assert gateway.complete.await_args.kwargs["temperature"] == 0.0
    assert gateway.complete.await_args.kwargs["max_tokens"] == 256
    assert gateway.complete.await_args.kwargs["pii_session"] is not None


@pytest.mark.asyncio
async def test_triage_llm_complete_falls_back_to_public_provider_seam() -> None:
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
async def test_cheap_llm_complete_uses_model_gateway_when_available() -> None:
    gateway = MagicMock()
    gateway.complete = AsyncMock(
        return_value=SimpleNamespace(
            text="synthetic response",
            usage={"prompt_tokens": 11, "completion_tokens": 7},
        )
    )

    class FakeChatManager:
        default_llm_model = "chat-model"
        _resource_tracker = None

        def __init__(self) -> None:
            self.model_gateway = gateway

        def resolve_llm_provider(
            self,
            *,
            model: str | None = None,
            pii_session_key: str | None = None,
        ) -> Any:
            raise AssertionError("provider fallback should not be used")

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
    gateway.complete.assert_awaited_once()
    assert gateway.complete.await_args.kwargs["model"] == "chat-model"
    assert gateway.complete.await_args.kwargs["temperature"] == 0.0
    assert gateway.complete.await_args.kwargs["pii_session"] is not None


@pytest.mark.asyncio
async def test_cheap_llm_complete_falls_back_to_public_provider_seam() -> None:
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


@pytest.mark.asyncio
async def test_domain_reflection_uses_model_gateway_when_available() -> None:
    gateway = MagicMock()
    gateway.complete = AsyncMock(return_value=SimpleNamespace(text="reflection"))

    class FakeChatManager:
        def __init__(self) -> None:
            self.model_gateway = gateway

        def resolve_llm_provider(
            self,
            *,
            model: str | None = None,
            pii_session_key: str | None = None,
        ) -> Any:
            raise AssertionError("provider fallback should not be used")

    class FakeDomainReflector:
        def __init__(self, *, memory_kernel: Any, llm: Any, feature_enabled: Any) -> None:
            self._llm = llm

        def reflect(self, domain: str, turns: list[Any]) -> list[Any]:
            assert domain == "research"
            assert self._llm.complete("reflect prompt", max_tokens=123) == "reflection"
            return []

    concierge = Concierge(
        project_store=MagicMock(),
        chat_manager=FakeChatManager(),
        capability_context=MagicMock(),
        memory_kernel=MagicMock(),
    )
    concierge._current_surface_id = "cli-user"
    context = SimpleNamespace(
        task=SimpleNamespace(turns=[]),
        project=SimpleNamespace(project_id="project-1"),
    )

    with (
        patch.object(concierge, "_resolve_triage_model", return_value="triage-model"),
        patch.object(concierge, "_maybe_run_domain_template_upgrade", return_value=None),
        patch.object(concierge, "_feature_enabled", return_value=True),
        patch("dan.server.concierge.domain_learning.DomainReflector", FakeDomainReflector),
    ):
        await concierge._domain_reflect_async(context, "research")

    gateway.complete.assert_awaited_once()
    assert gateway.complete.await_args.kwargs["model"] == "triage-model"
    assert gateway.complete.await_args.kwargs["temperature"] == 0.2
    assert gateway.complete.await_args.kwargs["max_tokens"] == 123
    assert gateway.complete.await_args.kwargs["pii_session"] is not None


@pytest.mark.asyncio
async def test_domain_reflection_falls_back_to_public_provider_seam() -> None:
    provider = MagicMock()
    provider.complete = AsyncMock(return_value=SimpleNamespace(text="reflection"))
    resolve_calls: list[dict[str, Any]] = []

    class FakeChatManager:
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

    class FakeDomainReflector:
        def __init__(self, *, memory_kernel: Any, llm: Any, feature_enabled: Any) -> None:
            self._llm = llm

        def reflect(self, domain: str, turns: list[Any]) -> list[Any]:
            assert domain == "research"
            assert self._llm.complete("reflect prompt", max_tokens=123) == "reflection"
            return []

    concierge = Concierge(
        project_store=MagicMock(),
        chat_manager=FakeChatManager(),
        capability_context=MagicMock(),
        memory_kernel=MagicMock(),
    )
    concierge._current_surface_id = "cli-user"
    context = SimpleNamespace(
        task=SimpleNamespace(turns=[]),
        project=SimpleNamespace(project_id="project-1"),
    )

    with (
        patch.object(concierge, "_resolve_triage_model", return_value="triage-model"),
        patch.object(concierge, "_maybe_run_domain_template_upgrade", return_value=None),
        patch.object(concierge, "_feature_enabled", return_value=True),
        patch("dan.server.concierge.domain_learning.DomainReflector", FakeDomainReflector),
    ):
        await concierge._domain_reflect_async(context, "research")

    assert resolve_calls == [
        {"model": "triage-model", "pii_session_key": "cli-user"}
    ]
    provider.complete.assert_awaited_once()
    assert provider.complete.await_args.kwargs["temperature"] == 0.2
    assert provider.complete.await_args.kwargs["max_tokens"] == 123
