from __future__ import annotations

from types import SimpleNamespace

from dan.providers import ProviderConfig
from dan.server.runtime_config import provider_readiness_summary


def _config(
    *,
    llm_default_model: str,
    providers: dict[str, ProviderConfig] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        llm_api_key="test-key",
        llm_base_url="https://api.example.com/v1",
        llm_default_model=llm_default_model,
        providers=providers or {},
        model_provider_map={},
    )


def test_provider_readiness_reports_runtime_fallback_for_missing_strict_prefix() -> None:
    summary = provider_readiness_summary(
        _config(llm_default_model="claude-sonnet-4-6"),
    )

    assert summary["status"] == "degraded"
    assert summary["default_model_provider"] == "default"
    assert any(
        "fall back to the configured `default` provider" in issue["message"]
        for issue in summary["issues"]
    )


def test_provider_readiness_is_clean_when_named_provider_is_registered() -> None:
    summary = provider_readiness_summary(
        _config(
            llm_default_model="claude-sonnet-4-6",
            providers={"anthropic": ProviderConfig(api_key="sk-anthropic")},
        ),
    )

    assert summary["status"] == "ok"
    assert summary["default_model_provider"] == "anthropic"
    assert summary["issues"] == []
