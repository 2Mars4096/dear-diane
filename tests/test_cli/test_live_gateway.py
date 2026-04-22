from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

import dan.cli.live_gateway as live_gateway
from dan.providers import CompletionResult


class _FakeGateway:
    def __init__(self) -> None:
        self.resolve_calls: list[str] = []
        self.complete_calls: list[dict[str, object]] = []
        self.provider = SimpleNamespace(
            label="gateway-backed-provider",
            apply_cache_hints=lambda messages: [dict(message) for message in messages],
        )

    def resolve(self, model: str):
        self.resolve_calls.append(model)
        return self.provider

    async def complete(self, **kwargs):
        self.complete_calls.append(dict(kwargs))
        return CompletionResult(
            text="ok",
            model=str(kwargs.get("model") or ""),
            finish_reason="stop",
        )


def test_build_gateway_backed_live_provider_uses_shared_gateway(monkeypatch) -> None:
    engine_config = SimpleNamespace(
        llm_api_key="",
        llm_base_url="",
        model_provider_map={},
    )
    fake_gateway = _FakeGateway()
    build_gateway_calls: list[dict[str, object]] = []

    def _fake_build_gateway(**kwargs):
        build_gateway_calls.append(dict(kwargs))
        return fake_gateway

    monkeypatch.setattr(live_gateway, "build_engine_config_from_env", lambda: engine_config)
    monkeypatch.setattr(live_gateway, "build_gateway", _fake_build_gateway)

    provider = live_gateway.build_gateway_backed_live_provider(
        "gpt-test",
        api_key="sk-live",
        base_url="https://example.invalid/v1",
    )

    assert engine_config.llm_api_key == "sk-live"
    assert engine_config.llm_base_url == "https://example.invalid/v1"
    assert len(build_gateway_calls) == 1
    assert build_gateway_calls[0]["engine_config"] is engine_config
    gateway_config = build_gateway_calls[0]["gateway_config"]
    assert gateway_config.pii_enabled is False
    assert gateway_config.retry_enabled is False
    assert gateway_config.telemetry_enabled is False
    assert gateway_config.budget_enabled is False
    assert gateway_config.fallback_model is None
    assert fake_gateway.resolve_calls == ["gpt-test"]
    assert provider.label == "gateway-backed-provider"

    result = asyncio.run(
        provider.complete(
            messages=[{"role": "user", "content": "hello"}],
            model="gpt-test",
            temperature=0.2,
        )
    )

    assert result.text == "ok"
    assert fake_gateway.complete_calls == [
        {
            "messages": [{"role": "user", "content": "hello"}],
            "model": "gpt-test",
            "temperature": 0.2,
            "max_tokens": None,
        }
    ]


@pytest.mark.parametrize(
    ("module_name",),
    [
        ("dan.cli.code",),
        ("dan.cli.research",),
        ("dan.cli.organism",),
    ],
)
def test_live_provider_wrappers_delegate_to_shared_helper(
    module_name: str,
    monkeypatch,
) -> None:
    sentinel = object()
    helper_calls: list[tuple[str, str | None, str | None]] = []

    def _fake_helper(model: str, *, api_key: str | None, base_url: str | None):
        helper_calls.append((model, api_key, base_url))
        return sentinel

    monkeypatch.setattr(
        "dan.cli.live_gateway.build_gateway_backed_live_provider",
        _fake_helper,
    )

    module = importlib.import_module(module_name)
    provider = module._build_live_provider(
        "gpt-test",
        api_key="sk-live",
        base_url="https://example.invalid/v1",
    )

    assert provider is sentinel
    assert helper_calls == [
        ("gpt-test", "sk-live", "https://example.invalid/v1"),
    ]
