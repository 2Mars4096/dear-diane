"""Tests for model-selection machinery: CostTracker, capabilities, ModelSelector, CascadeHandler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest

from dan.providers import CompletionResult, StreamChunk
from dan.providers.capabilities import (
    CAPABILITIES,
    LATENCY_TIER,
    ModelCapabilityRegistry,
)
from dan.providers.cost_tracker import BudgetExceededError, CostTracker
from dan.providers.costs import COST_PER_1K_TOKENS
from dan.providers.model_policy import (
    BudgetPolicy,
    CapabilityPolicy,
    CascadePolicy,
    RouterPolicy,
    StaticPolicy,
)
from dan.providers.model_selector import CascadeHandler, ModelSelector
from dan.providers.registry import ProviderRegistry


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@dataclass
class _StubEngineConfig:
    llm_default_model: str = "claude-sonnet-4-6"
    default_model_policy: Any = None


@dataclass
class _StubNode:
    id: str = "n1"
    name: str = "stub"
    description: str = ""
    model: str = ""
    model_policy: Any = None


class _FakeProvider:
    """Minimal provider satisfying the LLMProvider protocol."""

    def __init__(self, response_text: str = "") -> None:
        self._response = response_text

    async def complete(self, messages, model, temperature=0.7, max_tokens=None, **kw):
        return CompletionResult(text=self._response, model=model)

    async def stream(self, messages, model, temperature=0.7, max_tokens=None, **kw):
        yield StreamChunk(delta=self._response, accumulated=self._response, done=True)


def _make_registry(fake_text: str = "gpt-4o") -> ProviderRegistry:
    reg = ProviderRegistry()
    reg.register("default", _FakeProvider(fake_text))
    return reg


# ===================================================================
# 1. CostTracker
# ===================================================================


class TestCostTracker:
    def test_record_known_model(self):
        ct = CostTracker()
        cost = ct.record("n1", "gpt-4o", {"prompt_tokens": 1000, "completion_tokens": 500})
        assert cost > 0
        assert ct.total_cost() == cost
        assert ct.node_cost("n1") == cost

    def test_record_unknown_model_returns_zero(self):
        ct = CostTracker()
        cost = ct.record("n1", "llama-99", {"prompt_tokens": 100, "completion_tokens": 100})
        assert cost == 0.0
        assert ct.total_cost() == 0.0

    def test_record_none_usage(self):
        ct = CostTracker()
        assert ct.record("n1", "gpt-4o", None) == 0.0

    def test_accumulates_across_nodes(self):
        ct = CostTracker()
        c1 = ct.record("n1", "gpt-4o", {"prompt_tokens": 1000, "completion_tokens": 0})
        c2 = ct.record("n2", "gpt-4o-mini", {"prompt_tokens": 1000, "completion_tokens": 0})
        assert ct.total_cost() == pytest.approx(c1 + c2)
        assert ct.node_cost("n1") == pytest.approx(c1)
        assert ct.node_cost("n2") == pytest.approx(c2)

    def test_accumulates_within_same_node(self):
        ct = CostTracker()
        ct.record("n1", "gpt-4o", {"prompt_tokens": 500, "completion_tokens": 0})
        ct.record("n1", "gpt-4o", {"prompt_tokens": 500, "completion_tokens": 0})
        expected = 2 * (500 * 0.0025 / 1000)
        assert ct.node_cost("n1") == pytest.approx(expected)

    def test_node_cost_unknown_node(self):
        ct = CostTracker()
        assert ct.node_cost("nonexistent") == 0.0

    def test_remaining_budget_no_budget(self):
        ct = CostTracker()
        assert ct.remaining_budget() is None

    def test_remaining_budget_with_budget(self):
        ct = CostTracker(run_budget=1.0)
        ct.record("n1", "gpt-4o", {"prompt_tokens": 1000, "completion_tokens": 0})
        remaining = ct.remaining_budget()
        assert remaining is not None
        assert remaining < 1.0
        assert remaining > 0.0

    def test_is_over_budget(self):
        ct = CostTracker(run_budget=0.0001)
        assert not ct.is_over_budget()
        ct.record("n1", "gpt-4o", {"prompt_tokens": 10000, "completion_tokens": 10000})
        assert ct.is_over_budget()

    def test_is_over_budget_no_limit(self):
        ct = CostTracker()
        ct.record("n1", "gpt-4o", {"prompt_tokens": 999999, "completion_tokens": 999999})
        assert not ct.is_over_budget()

    def test_halt_on_budget_exceeded(self):
        ct = CostTracker(run_budget=0.0001, on_budget_exceeded="halt")
        with pytest.raises(BudgetExceededError):
            ct.record("n1", "gpt-4o", {"prompt_tokens": 100000, "completion_tokens": 100000})

    def test_warn_on_budget_exceeded_does_not_raise(self):
        ct = CostTracker(run_budget=0.0001, on_budget_exceeded="warn")
        ct.record("n1", "gpt-4o", {"prompt_tokens": 100000, "completion_tokens": 100000})
        assert ct.is_over_budget()

    def test_switch_on_budget_exceeded_does_not_raise(self):
        ct = CostTracker(run_budget=0.0001, on_budget_exceeded="switch")
        ct.record("n1", "gpt-4o", {"prompt_tokens": 100000, "completion_tokens": 100000})
        assert ct.is_over_budget()

    def test_invalid_on_budget_exceeded(self):
        with pytest.raises(ValueError, match="on_budget_exceeded"):
            CostTracker(on_budget_exceeded="explode")

    def test_snapshot_restore(self):
        ct = CostTracker(run_budget=5.0, on_budget_exceeded="halt")
        ct.record("n1", "gpt-4o", {"prompt_tokens": 1000, "completion_tokens": 500})
        ct.record("n2", "gpt-4o-mini", {"prompt_tokens": 2000, "completion_tokens": 1000})
        snap = ct.snapshot()

        ct2 = CostTracker()
        ct2.restore(snap)
        assert ct2.total_cost() == pytest.approx(ct.total_cost())
        assert ct2.node_cost("n1") == pytest.approx(ct.node_cost("n1"))
        assert ct2.node_cost("n2") == pytest.approx(ct.node_cost("n2"))
        assert ct2.remaining_budget() == pytest.approx(ct.remaining_budget())

    def test_snapshot_roundtrip_empty(self):
        ct = CostTracker()
        snap = ct.snapshot()
        ct2 = CostTracker(run_budget=99.0)
        ct2.restore(snap)
        assert ct2.total_cost() == 0.0
        assert ct2.remaining_budget() is None


# ===================================================================
# 2. ModelCapabilityRegistry
# ===================================================================


class TestModelCapabilityRegistry:
    def test_static_capabilities_loaded(self):
        reg = ModelCapabilityRegistry()
        assert "code" in reg.get_capabilities("gpt-4o")
        assert "vision" in reg.get_capabilities("gpt-4o")

    def test_unknown_model_empty_set(self):
        reg = ModelCapabilityRegistry()
        assert reg.get_capabilities("nonexistent-model") == set()

    def test_register_custom_model(self):
        reg = ModelCapabilityRegistry()
        reg.register("my-model", {"code", "vision"}, latency="fast")
        assert reg.get_capabilities("my-model") == {"code", "vision"}
        assert reg.get_latency("my-model") == "fast"

    def test_filter_by_single_capability(self):
        reg = ModelCapabilityRegistry()
        matches = reg.filter(required=["reasoning"])
        assert len(matches) > 0
        for m in matches:
            assert "reasoning" in reg.get_capabilities(m)

    def test_filter_by_multiple_capabilities(self):
        reg = ModelCapabilityRegistry()
        matches = reg.filter(required=["code", "vision", "tool_use"])
        for m in matches:
            caps = reg.get_capabilities(m)
            assert {"code", "vision", "tool_use"} <= caps

    def test_filter_no_match(self):
        reg = ModelCapabilityRegistry()
        matches = reg.filter(required=["teleportation"])
        assert matches == []

    def test_filter_cheapest(self):
        reg = ModelCapabilityRegistry()
        matches = reg.filter(required=["code", "tool_use"], prefer="cheapest")
        assert len(matches) >= 2
        costs = []
        for m in matches:
            rates = COST_PER_1K_TOKENS.get(m)
            if rates:
                costs.append((rates["prompt"] + rates["completion"]) / 2)
        for i in range(len(costs) - 1):
            assert costs[i] <= costs[i + 1] + 1e-10

    def test_filter_fastest(self):
        reg = ModelCapabilityRegistry()
        matches = reg.filter(required=["code"], prefer="fastest")
        tiers = [LATENCY_TIER.get(m, "medium") for m in matches]
        tier_rank = {"fast": 0, "medium": 1, "slow": 2}
        ranks = [tier_rank[t] for t in tiers]
        for i in range(len(ranks) - 1):
            assert ranks[i] <= ranks[i + 1]

    def test_filter_strongest(self):
        reg = ModelCapabilityRegistry()
        matches = reg.filter(required=["code"], prefer="strongest")
        assert len(matches) > 0
        cap_counts = [len(reg.get_capabilities(m)) for m in matches]
        for i in range(len(cap_counts) - 1):
            assert cap_counts[i] >= cap_counts[i + 1]

    def test_register_overwrites(self):
        reg = ModelCapabilityRegistry()
        reg.register("gpt-4o", {"code"}, latency="fast")
        assert reg.get_capabilities("gpt-4o") == {"code"}
        assert reg.get_latency("gpt-4o") == "fast"


# ===================================================================
# 3. ModelSelector.select()
# ===================================================================


class TestModelSelectorSelect:
    @pytest.mark.asyncio
    async def test_static_policy(self):
        ms = ModelSelector(_make_registry())
        result = await ms.select(StaticPolicy(model="gpt-4o"))
        assert result.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_budget_policy_under_budget(self):
        ct = CostTracker(run_budget=10.0)
        ms = ModelSelector(_make_registry(), cost_tracker=ct)
        policy = BudgetPolicy(preferred_model="gpt-4o", fallback_model="gpt-4o-mini")
        result = await ms.select(policy)
        assert result.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_budget_policy_over_budget(self):
        ct = CostTracker(run_budget=0.0001)
        ct.record("n1", "gpt-4o", {"prompt_tokens": 100000, "completion_tokens": 100000})
        ms = ModelSelector(_make_registry(), cost_tracker=ct)
        policy = BudgetPolicy(preferred_model="gpt-4o", fallback_model="gpt-4o-mini")
        result = await ms.select(policy)
        assert result.model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_budget_policy_no_tracker_uses_preferred(self):
        ms = ModelSelector(_make_registry(), cost_tracker=None)
        policy = BudgetPolicy(preferred_model="gpt-4o", fallback_model="gpt-4o-mini")
        result = await ms.select(policy)
        assert result.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_cascade_returns_first(self):
        ms = ModelSelector(_make_registry())
        policy = CascadePolicy(models=["gpt-4o", "gpt-4o-mini", "o3-mini"])
        result = await ms.select(policy)
        assert result.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_capability_policy(self):
        cap_reg = ModelCapabilityRegistry()
        ms = ModelSelector(_make_registry(), capability_registry=cap_reg)
        policy = CapabilityPolicy(required_capabilities=["code", "reasoning"])
        result = await ms.select(policy)
        assert "reasoning" in cap_reg.get_capabilities(result.model)

    @pytest.mark.asyncio
    async def test_capability_policy_no_registry_raises(self):
        ms = ModelSelector(_make_registry(), capability_registry=None)
        policy = CapabilityPolicy(required_capabilities=["code"])
        with pytest.raises(RuntimeError, match="ModelCapabilityRegistry"):
            await ms.select(policy)

    @pytest.mark.asyncio
    async def test_router_policy(self):
        reg = _make_registry(fake_text="gpt-4o-mini")
        ms = ModelSelector(reg)
        policy = RouterPolicy(
            router_model="gpt-4o",
            candidates=["gpt-4o", "gpt-4o-mini"],
        )
        result = await ms.select(policy)
        assert result.model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_router_policy_fallback_on_bad_response(self):
        reg = _make_registry(fake_text="potato")
        ms = ModelSelector(reg)
        policy = RouterPolicy(
            router_model="gpt-4o",
            candidates=["gpt-4o", "gpt-4o-mini"],
        )
        result = await ms.select(policy)
        assert result.model == "gpt-4o"


# ===================================================================
# 4. ModelSelector.resolve_effective_policy()
# ===================================================================


class TestResolveEffectivePolicy:
    def test_node_policy_highest_precedence(self):
        ms = ModelSelector(_make_registry())
        node = _StubNode(model="gpt-4o", model_policy=BudgetPolicy(
            preferred_model="gpt-4o", fallback_model="gpt-4o-mini",
        ))
        cfg = _StubEngineConfig()
        policy = ms.resolve_effective_policy(node, cfg)
        assert isinstance(policy, BudgetPolicy)

    def test_node_model_becomes_static(self):
        ms = ModelSelector(_make_registry())
        node = _StubNode(model="o3-mini")
        cfg = _StubEngineConfig()
        policy = ms.resolve_effective_policy(node, cfg)
        assert isinstance(policy, StaticPolicy)
        assert policy.model == "o3-mini"

    def test_engine_default_policy_used(self):
        ms = ModelSelector(_make_registry())
        node = _StubNode(model="")
        engine_policy = CapabilityPolicy(required_capabilities=["code"])
        cfg = _StubEngineConfig(default_model_policy=engine_policy)
        policy = ms.resolve_effective_policy(node, cfg)
        assert isinstance(policy, CapabilityPolicy)

    def test_falls_back_to_engine_default_model(self):
        ms = ModelSelector(_make_registry())
        node = _StubNode(model="")
        cfg = _StubEngineConfig(llm_default_model="gemini-2.5-pro")
        policy = ms.resolve_effective_policy(node, cfg)
        assert isinstance(policy, StaticPolicy)
        assert policy.model == "gemini-2.5-pro"

    def test_backward_compat_bare_model_string(self):
        """A node with a bare model string (no policy) resolves to StaticPolicy."""
        ms = ModelSelector(_make_registry())
        node = _StubNode(model="claude-opus-4", model_policy=None)
        cfg = _StubEngineConfig()
        policy = ms.resolve_effective_policy(node, cfg)
        assert isinstance(policy, StaticPolicy)
        assert policy.model == "claude-opus-4"


# ===================================================================
# 5. CascadeHandler
# ===================================================================


class TestCascadeHandler:
    def test_current_model_is_first(self):
        policy = CascadePolicy(models=["gpt-4o", "gpt-4o-mini", "o3-mini"])
        ch = CascadeHandler(policy)
        assert ch.current_model() == "gpt-4o"

    def test_advance_through_models(self):
        policy = CascadePolicy(models=["gpt-4o", "gpt-4o-mini", "o3-mini"])
        ch = CascadeHandler(policy)
        assert ch.advance() == "gpt-4o-mini"
        assert ch.current_model() == "gpt-4o-mini"
        assert ch.advance() == "o3-mini"
        assert ch.current_model() == "o3-mini"
        assert ch.advance() is None

    def test_should_cascade_on_error(self):
        policy = CascadePolicy(
            models=["gpt-4o", "gpt-4o-mini"],
            cascade_on=["error", "timeout"],
        )
        ch = CascadeHandler(policy)
        assert ch.should_cascade(RuntimeError("Something failed"))

    def test_should_cascade_on_timeout(self):
        policy = CascadePolicy(
            models=["gpt-4o", "gpt-4o-mini"],
            cascade_on=["error", "timeout"],
        )
        ch = CascadeHandler(policy)

        class TimeoutError(Exception):
            pass

        assert ch.should_cascade(TimeoutError("Request timed out"))

    def test_should_not_cascade_on_unrelated_error(self):
        policy = CascadePolicy(
            models=["gpt-4o", "gpt-4o-mini"],
            cascade_on=["timeout"],
        )
        ch = CascadeHandler(policy)
        assert not ch.should_cascade(ValueError("bad value"))

    def test_should_not_cascade_when_exhausted(self):
        policy = CascadePolicy(models=["gpt-4o"])
        ch = CascadeHandler(policy)
        assert not ch.should_cascade(RuntimeError("error"))

    def test_max_attempts_respected(self):
        policy = CascadePolicy(
            models=["a", "b", "c"],
            max_attempts=1,
        )
        ch = CascadeHandler(policy)
        assert ch.advance() == "b"
        assert not ch.should_cascade(RuntimeError("error"))

    def test_should_cascade_matches_error_string(self):
        policy = CascadePolicy(
            models=["gpt-4o", "gpt-4o-mini"],
            cascade_on=["rate_limit"],
        )
        ch = CascadeHandler(policy)
        assert ch.should_cascade(Exception("rate_limit exceeded"))

    def test_cascade_error_type_match(self):
        """Cascade triggers when exception class name contains the trigger."""
        policy = CascadePolicy(
            models=["a", "b"],
            cascade_on=["error"],
        )
        ch = CascadeHandler(policy)
        assert ch.should_cascade(RuntimeError("oops"))


# ===================================================================
# 6. Backward compatibility
# ===================================================================


class TestBackwardCompat:
    def test_node_with_model_no_policy(self):
        """Legacy node spec: model string, no model_policy field at all."""
        ms = ModelSelector(_make_registry())

        @dataclass
        class LegacyNode:
            id: str = "n1"
            name: str = "legacy"
            model: str = "gpt-4o"

        policy = ms.resolve_effective_policy(LegacyNode(), _StubEngineConfig())
        assert isinstance(policy, StaticPolicy)
        assert policy.model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_resolved_static_works_in_select(self):
        ms = ModelSelector(_make_registry())
        node = _StubNode(model="gpt-4o-mini")
        cfg = _StubEngineConfig()
        policy = ms.resolve_effective_policy(node, cfg)
        result = await ms.select(policy, node)
        assert result.model == "gpt-4o-mini"

    def test_cost_table_has_capability_models(self):
        """Every model in CAPABILITIES should be resolvable in the cost table."""
        for model in CAPABILITIES:
            from dan.providers.costs import estimate_cost
            cost = estimate_cost(model, 1000, 1000)
            assert cost is not None, f"Model {model!r} not in cost table"
