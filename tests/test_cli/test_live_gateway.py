from __future__ import annotations

from types import SimpleNamespace

import dan.cli.live_gateway as live_gateway


def test_build_live_provider_resolves_directly_from_provider_registry(monkeypatch) -> None:
    sentinel = object()
    registry = SimpleNamespace(resolve=lambda model: sentinel if model == "gpt-test" else None)
    captured: dict[str, object] = {}

    def fake_build_provider_registry(config):
        captured["config"] = config
        return registry

    monkeypatch.setattr(live_gateway, "build_provider_registry", fake_build_provider_registry)
    monkeypatch.setattr(
        live_gateway,
        "resolve_config",
        lambda **_kwargs: {
            "api_key": "sk-live",
            "base_url": "https://example.invalid/v1",
        },
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("GOOGLE_API_KEY", "sk-google")
    monkeypatch.setenv("DAN_MODEL_PROVIDER_MAP", '{"gpt-test":"default"}')

    provider = live_gateway.build_gateway_backed_live_provider(
        "gpt-test",
        api_key="ignored-by-fake-resolver",
        base_url=None,
    )

    assert provider is sentinel
    config = captured["config"]
    assert config.llm_api_key == "sk-live"
    assert config.llm_base_url == "https://example.invalid/v1"
    assert set(config.providers) == {"anthropic", "google"}
    assert config.model_provider_map == {"gpt-test": "default"}


def test_invalid_model_provider_map_falls_back_to_empty(monkeypatch) -> None:
    monkeypatch.setenv("DAN_MODEL_PROVIDER_MAP", "not-json")

    assert live_gateway._model_provider_map() == {}
