"""Provider resolution for Diane live execution."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from dan.cli import resolve_config
from dan.providers import LLMProvider, ProviderConfig
from dan.providers.factory import build_provider_registry


@dataclass
class _LiveProviderConfig:
    llm_api_key: str
    llm_base_url: str | None
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    model_provider_map: dict[str, str] = field(default_factory=dict)


def _model_provider_map() -> dict[str, str]:
    try:
        value = json.loads(os.environ.get("DAN_MODEL_PROVIDER_MAP", "{}"))
    except json.JSONDecodeError:
        return {}
    return {str(key): str(provider) for key, provider in value.items()} if isinstance(value, dict) else {}


def build_gateway_backed_live_provider(
    model: str,
    *,
    api_key: str | None,
    base_url: str | None,
) -> LLMProvider:
    """Resolve the configured provider for a Diane model."""

    if base_url and base_url.rstrip("/") == "https://openrouter.ai/api/v1":
        api_key = api_key or os.environ.get("DAN_OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            configured = resolve_config()
            if str(configured.get("base_url") or "").rstrip("/") == "https://openrouter.ai/api/v1":
                api_key = configured.get("api_key")
        if not api_key:
            raise ValueError("Set OPENROUTER_API_KEY on the Dear Diane server to use OpenRouter models")
    resolved = resolve_config(api_key=api_key, base_url=base_url)
    providers: dict[str, ProviderConfig] = {}
    anthropic_key = os.environ.get("DAN_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    google_key = os.environ.get("DAN_GOOGLE_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if anthropic_key:
        providers["anthropic"] = ProviderConfig(api_key=anthropic_key)
    if google_key:
        providers["google"] = ProviderConfig(api_key=google_key)
    registry = build_provider_registry(
        _LiveProviderConfig(
            llm_api_key=str(resolved.get("api_key") or ""),
            llm_base_url=str(resolved.get("base_url") or "") or None,
            providers=providers,
            model_provider_map=({**_model_provider_map(), model: "default"}
                                if base_url
                                else _model_provider_map()),
        )
    )
    return registry.resolve(model)
