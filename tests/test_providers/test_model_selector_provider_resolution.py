from __future__ import annotations

from types import SimpleNamespace

import pytest

from dan.providers.model_policy import TierPolicy
from dan.providers.model_selector import ModelSelector
from dan.providers.registry import ProviderRegistry


def test_provider_registry_resolve_name_maps_kimi_to_openai() -> None:
    registry = ProviderRegistry()
    registry.register("openai", object())

    assert registry.resolve_name("kimi-k2.6") == "openai"


@pytest.mark.asyncio
async def test_model_selector_uses_selected_model_provider_for_tier_params() -> None:
    registry = ProviderRegistry()
    registry.register("anthropic", object())
    registry.register("openai", object())

    selector = ModelSelector(registry)
    node = SimpleNamespace(
        id="node_critical",
        node_type="llm_operator",
        task_tier="critical",
        output_ports=[],
        prompt_template="",
        system_prompt="",
    )
    context = SimpleNamespace(
        config=SimpleNamespace(
            providers={"anthropic": object(), "openai": object()},
            model_provider_map={},
            tier_map={
                "micro": "gpt-4o-mini",
                "routine": "gpt-4o",
                "reasoning": "kimi-k2.6",
                "critical": "kimi-k2.6",
            },
            tier_params=None,
        ),
        graph=None,
        tool_registry=None,
    )

    result = await selector.select(TierPolicy(), node=node, context=context)

    assert result.model == "kimi-k2.6"
    assert result.tier_params == {}


@pytest.mark.asyncio
async def test_model_selector_keeps_anthropic_critical_defaults_for_claude() -> None:
    registry = ProviderRegistry()
    registry.register("anthropic", object())
    registry.register("openai", object())

    selector = ModelSelector(registry)
    node = SimpleNamespace(
        id="node_critical",
        node_type="llm_operator",
        task_tier="critical",
        output_ports=[],
        prompt_template="",
        system_prompt="",
    )
    context = SimpleNamespace(
        config=SimpleNamespace(
            providers={"anthropic": object(), "openai": object()},
            model_provider_map={},
            tier_map={
                "micro": "gpt-4o-mini",
                "routine": "gpt-4o",
                "reasoning": "claude-opus-4",
                "critical": "claude-opus-4",
            },
            tier_params=None,
        ),
        graph=None,
        tool_registry=None,
    )

    result = await selector.select(TierPolicy(), node=node, context=context)

    assert result.model == "claude-opus-4"
    assert result.tier_params == {"extended_thinking": True, "max_tokens": 8192}
