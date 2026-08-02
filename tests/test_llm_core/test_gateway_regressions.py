"""Regression tests locking gateway behaviour for real usage patterns.

Covers:
  - Model override routing parity (gateway vs registry direct)
  - PII treatment uniformity across gateway paths
  - Concern chain ordering (PII → budget → retry+timeout → telemetry → fallback)
  - Registry construction single-source-of-truth (build_gateway ≡ build_provider_registry)
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest

from dan.llm_core import (
    CompletionResult,
    GatewayConfig,
    ModelGateway,
    StreamChunk,
    build_gateway,
)
from dan.providers import ProviderConfig
from dan.providers.cost_tracker import CostTracker
from dan.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class RecordingProvider:
    """Minimal LLMProvider that records every call for assertion."""

    def __init__(
        self,
        name: str = "unnamed",
        text: str = "ok",
        usage: dict[str, int] | None = None,
        fail_times: int = 0,
        fail_exc: type[Exception] = RuntimeError,
    ) -> None:
        self.name = name
        self._text = text
        self._usage = usage or {"prompt_tokens": 10, "completion_tokens": 5}
        self._fail_times = fail_times
        self._fail_exc = fail_exc
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        self.calls.append({"messages": messages, "model": model})
        if len(self.calls) <= self._fail_times:
            raise self._fail_exc(f"Fail #{len(self.calls)}")
        return CompletionResult(text=self._text, usage=self._usage, model=model)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append({"messages": messages, "model": model})

        async def _gen() -> AsyncIterator[StreamChunk]:
            words = self._text.split()
            acc = ""
            for i, w in enumerate(words):
                acc += ("" if i == 0 else " ") + w
                yield StreamChunk(
                    delta=w,
                    accumulated=acc,
                    done=(i == len(words) - 1),
                    usage=self._usage if i == len(words) - 1 else None,
                )

        return _gen()


@dataclass
class MockPIISession:
    """Duck-typed PII session with invertible tokenize/detokenize."""

    prefix: str = "TOKENIZED:"

    def tokenize(self, text: str) -> str:
        return self.prefix + text

    def detokenize(self, text: str) -> str:
        return text.replace(self.prefix, "")


def _make_engine_config(
    *,
    api_key: str = "test-key",
    base_url: str = "https://test.example.com/v1",
    providers: dict[str, ProviderConfig] | None = None,
    model_provider_map: dict[str, str] | None = None,
) -> MagicMock:
    cfg = MagicMock()
    cfg.llm_api_key = api_key
    cfg.llm_base_url = base_url
    cfg.providers = providers or {}
    cfg.model_provider_map = model_provider_map or {}
    return cfg


_NO_CONCERNS = GatewayConfig(
    pii_enabled=False,
    retry_enabled=False,
    telemetry_enabled=False,
    budget_enabled=False,
)


# ===================================================================
# 1. Model override routing parity (Task 5-1)
# ===================================================================


class TestModelOverrideRoutingParity:
    """Model overrides route to the same provider whether called via
    gateway.complete() or registry.resolve() directly."""

    @pytest.mark.asyncio
    async def test_override_routes_same_provider_gateway_vs_registry(self) -> None:
        openai_prov = RecordingProvider(name="openai")
        anthropic_prov = RecordingProvider(name="anthropic")

        registry = ProviderRegistry()
        registry.register("openai", openai_prov)
        registry.register("anthropic", anthropic_prov)
        registry.set_model_override("special-model", "anthropic")

        gw = ModelGateway(registry, config=_NO_CONCERNS)

        registry_resolved = registry.resolve("special-model")
        assert registry_resolved is anthropic_prov

        await gw.complete(
            [{"role": "user", "content": "hi"}], "special-model"
        )
        assert len(anthropic_prov.calls) == 1
        assert len(openai_prov.calls) == 0

    @pytest.mark.asyncio
    async def test_prefix_routing_parity(self) -> None:
        """claude-* routes to anthropic, gpt-* routes to openai via prefix."""
        openai_prov = RecordingProvider(name="openai")
        anthropic_prov = RecordingProvider(name="anthropic")

        registry = ProviderRegistry()
        registry.register("openai", openai_prov)
        registry.register("anthropic", anthropic_prov)

        gw = ModelGateway(registry, config=_NO_CONCERNS)

        assert registry.resolve_name("claude-3.5-sonnet") == "anthropic"
        assert registry.resolve_name("gpt-4o") == "openai"

        await gw.complete(
            [{"role": "user", "content": "a"}], "claude-3.5-sonnet"
        )
        assert len(anthropic_prov.calls) == 1
        assert len(openai_prov.calls) == 0

        await gw.complete(
            [{"role": "user", "content": "b"}], "gpt-4o"
        )
        assert len(openai_prov.calls) == 1

    @pytest.mark.asyncio
    async def test_override_takes_precedence_over_prefix(self) -> None:
        openai_prov = RecordingProvider(name="openai")
        anthropic_prov = RecordingProvider(name="anthropic")

        registry = ProviderRegistry()
        registry.register("openai", openai_prov)
        registry.register("anthropic", anthropic_prov)
        registry.set_model_override("claude-3.5-sonnet", "openai")

        gw = ModelGateway(registry, config=_NO_CONCERNS)

        assert registry.resolve_name("claude-3.5-sonnet") == "openai"

        await gw.complete(
            [{"role": "user", "content": "hi"}], "claude-3.5-sonnet"
        )
        assert len(openai_prov.calls) == 1
        assert len(anthropic_prov.calls) == 0


# ===================================================================
# 2. PII treatment uniformity (Task 5-2)
# ===================================================================


class TestPIIParity:
    """All gateway paths get the same PII treatment."""

    @pytest.mark.asyncio
    async def test_complete_applies_pii(self) -> None:
        provider = RecordingProvider(text="TOKENIZED:response")
        pii = MockPIISession()
        gw = ModelGateway(
            self._registry(provider),
            config=GatewayConfig(
                pii_enabled=True, retry_enabled=False, telemetry_enabled=False
            ),
            pii_session=pii,
        )

        result = await gw.complete(
            [{"role": "user", "content": "hello"}], "test-model"
        )

        sent = provider.calls[0]["messages"][0]["content"]
        assert sent == "TOKENIZED:hello"
        assert result.text == "response"

    @pytest.mark.asyncio
    async def test_stream_applies_pii(self) -> None:
        provider = RecordingProvider(text="TOKENIZED:word")
        pii = MockPIISession()
        gw = ModelGateway(
            self._registry(provider),
            config=GatewayConfig(
                pii_enabled=True, retry_enabled=False, telemetry_enabled=False
            ),
            pii_session=pii,
        )

        chunks: list[StreamChunk] = []
        async for chunk in gw.stream(
            [{"role": "user", "content": "hello"}], "test-model"
        ):
            chunks.append(chunk)

        sent = provider.calls[0]["messages"][0]["content"]
        assert sent == "TOKENIZED:hello"
        assert chunks[0].delta == "word"

    @pytest.mark.asyncio
    async def test_pii_disabled_per_call(self) -> None:
        provider = RecordingProvider(text="raw")
        pii = MockPIISession()
        gw = ModelGateway(
            self._registry(provider),
            config=GatewayConfig(
                pii_enabled=True, retry_enabled=False, telemetry_enabled=False
            ),
            pii_session=pii,
        )

        result = await gw.complete(
            [{"role": "user", "content": "hello"}], "test-model", pii=False
        )

        sent = provider.calls[0]["messages"][0]["content"]
        assert sent == "hello"
        assert result.text == "raw"

    @pytest.mark.asyncio
    async def test_no_pii_session_skips_gracefully(self) -> None:
        provider = RecordingProvider(text="plain")
        gw = ModelGateway(
            self._registry(provider),
            config=GatewayConfig(
                pii_enabled=True, retry_enabled=False, telemetry_enabled=False
            ),
        )

        result = await gw.complete(
            [{"role": "user", "content": "hello"}], "test-model"
        )

        sent = provider.calls[0]["messages"][0]["content"]
        assert sent == "hello"
        assert result.text == "plain"

    @pytest.mark.asyncio
    async def test_pii_applied_before_retry(self) -> None:
        """PII tokenization happens once before retry loop — retried calls
        send the same tokenized messages."""
        provider = RecordingProvider(text="ok", fail_times=1)
        pii = MockPIISession()
        gw = ModelGateway(
            self._registry(provider),
            config=GatewayConfig(
                pii_enabled=True,
                retry_enabled=True,
                retry_max_attempts=3,
                retry_backoff_base=0.01,
                telemetry_enabled=False,
            ),
            pii_session=pii,
        )

        await gw.complete(
            [{"role": "user", "content": "hello"}], "test-model"
        )

        assert len(provider.calls) == 2
        first_msg = provider.calls[0]["messages"][0]["content"]
        second_msg = provider.calls[1]["messages"][0]["content"]
        assert first_msg == "TOKENIZED:hello"
        assert second_msg == "TOKENIZED:hello"

    @staticmethod
    def _registry(provider: RecordingProvider) -> ProviderRegistry:
        reg = ProviderRegistry()
        reg.register("default", provider)
        return reg


# ===================================================================
# 3. Concern chain order (Task 5-3)
# ===================================================================


class TestConcernChainOrder:
    """Verify the concern chain order:
    PII → budget → retry+timeout → telemetry → fallback."""

    @pytest.mark.asyncio
    async def test_pii_before_provider_call(self) -> None:
        """Provider receives tokenized messages, not raw."""
        provider = RecordingProvider(text="TOKENIZED:resp")
        pii = MockPIISession()
        gw = ModelGateway(
            self._registry(provider),
            config=GatewayConfig(
                pii_enabled=True, retry_enabled=False, telemetry_enabled=False
            ),
            pii_session=pii,
        )

        await gw.complete(
            [{"role": "user", "content": "sensitive"}], "test-model"
        )

        sent = provider.calls[0]["messages"][0]["content"]
        assert sent.startswith("TOKENIZED:")

    @pytest.mark.asyncio
    async def test_budget_checked_before_call(self) -> None:
        """Budget check blocks the call before the provider is ever invoked."""
        provider = RecordingProvider()
        tracker = CostTracker(run_budget=0.001, on_budget_exceeded="warn")
        tracker.record(
            "prior", "gpt-4o",
            {"prompt_tokens": 100_000, "completion_tokens": 50_000},
        )

        gw = ModelGateway(
            self._registry(provider),
            config=GatewayConfig(
                budget_enabled=True,
                retry_enabled=False,
                pii_enabled=False,
                telemetry_enabled=False,
            ),
            cost_tracker=tracker,
        )

        from dan.providers import BudgetExceededError

        with pytest.raises(BudgetExceededError):
            await gw.complete(
                [{"role": "user", "content": "hi"}], "test-model"
            )

        assert len(provider.calls) == 0

    @pytest.mark.asyncio
    async def test_telemetry_after_successful_call(self) -> None:
        """Telemetry callback fires only AFTER the provider returns."""
        call_order: list[str] = []

        class TelemetryProvider(RecordingProvider):
            async def complete(self, *a: Any, **kw: Any) -> CompletionResult:
                call_order.append("provider")
                return await super().complete(*a, **kw)

        def on_telemetry(call_meta: Any) -> None:
            call_order.append("telemetry")

        provider = TelemetryProvider(text="done")
        gw = ModelGateway(
            self._registry(provider),
            config=GatewayConfig(
                telemetry_enabled=True,
                retry_enabled=False,
                pii_enabled=False,
            ),
            telemetry_callback=on_telemetry,
        )

        await gw.complete(
            [{"role": "user", "content": "hi"}], "test-model"
        )

        assert call_order == ["provider", "telemetry"]

    @pytest.mark.asyncio
    async def test_fallback_after_retry_exhaustion(self) -> None:
        """Primary must exhaust all retries before fallback is attempted."""
        primary = RecordingProvider(name="primary", fail_times=10)
        fallback = RecordingProvider(name="fallback", text="fallback ok")

        registry = ProviderRegistry()
        registry.register("default", primary)
        registry.register("openai", fallback)
        registry.set_model_override("gpt-4o-mini", "openai")

        gw = ModelGateway(
            registry,
            config=GatewayConfig(
                fallback_model="gpt-4o-mini",
                retry_enabled=True,
                retry_max_attempts=3,
                retry_backoff_base=0.01,
                pii_enabled=False,
                telemetry_enabled=False,
            ),
        )

        result = await gw.complete(
            [{"role": "user", "content": "hi"}], "test-model"
        )

        assert result.text == "fallback ok"
        assert len(primary.calls) == 3
        assert len(fallback.calls) >= 1

    @staticmethod
    def _registry(provider: RecordingProvider) -> ProviderRegistry:
        reg = ProviderRegistry()
        reg.register("default", provider)
        return reg


# ===================================================================
# 4. Registry construction single-source-of-truth (Task 5-4)
# ===================================================================


class TestRegistryConstructionParity:
    """build_gateway(engine_config) produces same provider set as
    build_provider_registry(engine_config)."""

    def test_same_providers_registered(self) -> None:
        cfg = _make_engine_config(
            providers={
                "openai": ProviderConfig(api_key="sk-oai"),
            },
        )

        from dan.providers.factory import build_provider_registry

        direct_registry = build_provider_registry(cfg)
        gw = build_gateway(cfg)

        assert set(gw.registry.provider_names()) == set(
            direct_registry.provider_names()
        )

    def test_same_model_resolution(self) -> None:
        cfg = _make_engine_config(
            providers={
                "openai": ProviderConfig(api_key="sk-oai"),
            },
            model_provider_map={"special": "openai"},
        )

        from dan.providers.factory import build_provider_registry

        direct_registry = build_provider_registry(cfg)
        for model, prov in cfg.model_provider_map.items():
            direct_registry.set_model_override(model, prov)

        gw = build_gateway(cfg, model_provider_map=cfg.model_provider_map)

        test_models = ["gpt-4o", "special", "unknown-model"]
        for model in test_models:
            assert gw.registry.resolve_name(model) == direct_registry.resolve_name(
                model
            ), f"Mismatch for model {model!r}"
