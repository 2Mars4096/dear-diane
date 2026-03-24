"""Centralized gateway factory — THE one way to build a fully-configured gateway.

Every surface (server startup, CLI/local mode, tests) should call
:func:`build_gateway` instead of assembling provider registries ad hoc.

Import rules: this module may only import from ``dan.providers`` and
``dan.llm_core`` — never from ``server/``, ``engine/``, ``concierge/``,
or ``cli/``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from dan.llm_core.config import GatewayConfig
from dan.llm_core.gateway import ModelGateway
from dan.providers import ProviderConfig
from dan.providers.openai_provider import OpenAIProvider
from dan.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)


def _registry_from_engine_config(engine_config: Any) -> ProviderRegistry:
    """Build a full registry by delegating to the canonical provider factory."""
    from dan.providers.factory import build_provider_registry

    return build_provider_registry(engine_config)


def _registry_from_params(
    api_key: str | None = None,
    base_url: str | None = None,
    providers: dict[str, Any] | None = None,
) -> ProviderRegistry:
    """Build a minimal registry from explicit key/url/provider-dict params."""
    registry = ProviderRegistry()

    if api_key or base_url:
        default_config = ProviderConfig(
            api_key=api_key or "",
            base_url=base_url,
        )
        registry.register("default", OpenAIProvider(default_config))

    if providers:
        from dan.providers.factory import create_provider

        for name, value in providers.items():
            if name == "default" and registry.has_provider("default"):
                continue
            if isinstance(value, ProviderConfig):
                cfg = value
            elif isinstance(value, dict):
                cfg = ProviderConfig(**value)
            else:
                cfg = ProviderConfig(api_key=str(value))
            provider = create_provider(name, cfg)
            if provider is not None:
                registry.register(name, provider)

    return registry


def build_gateway(
    engine_config: Any = None,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    providers: dict[str, Any] | None = None,
    model_provider_map: dict[str, str] | None = None,
    gateway_config: GatewayConfig | None = None,
    cost_tracker: Any | None = None,
    pii_session: Any | None = None,
    telemetry_callback: Callable[..., Any] | None = None,
) -> ModelGateway:
    """Build a fully-configured :class:`ModelGateway`.

    This is the preferred single entry point for constructing model access.
    Accepts either an *engine_config* (for full configuration) or individual
    parameters (for simple/test usage).

    Parameters
    ----------
    engine_config:
        An :class:`~dan.engine.executor.EngineConfig` instance.  When
        provided, the full provider set (including per-provider keys,
        model-provider overrides, and OpenAI-compatible fallback) is
        built from it via :func:`~dan.providers.factory.build_provider_registry`.
    api_key / base_url:
        Shortcuts for constructing a single-provider "default" registry
        when *engine_config* is not supplied.
    providers:
        Additional named providers to register.  Values may be
        :class:`~dan.providers.ProviderConfig` instances, plain dicts
        (forwarded to ``ProviderConfig(**d)``), or bare API-key strings.
    model_provider_map:
        Explicit model → provider-name overrides applied on top of
        whatever the registry already contains.
    gateway_config:
        Override the default :class:`GatewayConfig` concern toggles.
    cost_tracker:
        Optional :class:`~dan.providers.CostTracker` for budget tracking.
    pii_session:
        Optional PII tokenization session (future gateway concern).
    telemetry_callback:
        Optional callback invoked after each gateway call with a
        :class:`~dan.llm_core.types.GatewayCall` instance.
    """
    if engine_config is not None:
        registry = _registry_from_engine_config(engine_config)
    else:
        registry = _registry_from_params(
            api_key=api_key,
            base_url=base_url,
            providers=providers,
        )

    if model_provider_map:
        for model, provider_name in model_provider_map.items():
            registry.set_model_override(model, provider_name)

    config = gateway_config or GatewayConfig()

    return ModelGateway(
        registry=registry,
        config=config,
        cost_tracker=cost_tracker,
        pii_session=pii_session,
        telemetry_callback=telemetry_callback,
    )
