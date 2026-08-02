from __future__ import annotations

from unittest.mock import patch

from dan.server.runtime_config import (
    build_engine_config_from_env,
    provider_readiness_summary,
)


def test_build_engine_config_uses_provider_alias_envs() -> None:
    with patch.dict(
        "os.environ",
        {
            "OPENAI_API_KEY": "openai-key",
            "ANTHROPIC_API_KEY": "anthropic-key",
            "GOOGLE_API_KEY": "google-key",
        },
        clear=True,
    ):
        config = build_engine_config_from_env()

    assert config.llm_api_key == "openai-key"
    assert set(config.providers) >= {"openai", "anthropic", "google"}


def test_provider_readiness_summary_reports_default_model_resolution_issue() -> None:
    with patch.dict("os.environ", {}, clear=True):
        config = build_engine_config_from_env()
        summary = provider_readiness_summary(config)

    assert summary["status"] == "degraded"
    assert summary["issues"]
    assert "claude-sonnet-4-6" in summary["issues"][0]["message"]


def test_provider_readiness_summary_resolves_configured_claude_provider() -> None:
    with patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "anthropic-key"},
        clear=True,
    ):
        config = build_engine_config_from_env()
        summary = provider_readiness_summary(config)

    assert summary["status"] == "ok"
    assert summary["default_model_provider"] == "anthropic"
