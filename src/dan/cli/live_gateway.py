"""Shared gateway-backed live provider resolution for CLI surfaces."""

from __future__ import annotations

from dan.cli import resolve_config
from dan.executors.provider_runtime import _GatewayBackedProviderAdapter
from dan.llm_core.config import GatewayConfig
from dan.llm_core.factory import build_gateway
from dan.providers import LLMProvider
from dan.server.runtime_config import build_engine_config_from_env


def _live_gateway_config() -> GatewayConfig:
    """Keep live CLI resolution on the shared gateway seam without extra concerns."""

    return GatewayConfig(
        pii_enabled=False,
        retry_enabled=False,
        telemetry_enabled=False,
        budget_enabled=False,
        fallback_model=None,
    )


def build_gateway_backed_live_provider(
    model: str,
    *,
    api_key: str | None,
    base_url: str | None,
) -> LLMProvider:
    """Build a provider-shaped live surface through the shared gateway."""

    config = resolve_config(api_key=api_key, base_url=base_url)
    engine_config = build_engine_config_from_env()
    if str(config.get("api_key") or "").strip():
        engine_config.llm_api_key = str(config["api_key"]).strip()
    if str(config.get("base_url") or "").strip():
        engine_config.llm_base_url = str(config["base_url"]).strip()
    gateway = build_gateway(
        engine_config=engine_config,
        gateway_config=_live_gateway_config(),
    )
    return _GatewayBackedProviderAdapter(
        gateway,
        context=engine_config,
        model=model,
    )
