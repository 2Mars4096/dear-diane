"""Tests for task-level model tiering (Plan 18-5).

Covers: TaskTier/TierWeights/TierPolicy models, DifficultyScorer, ImpactScorer,
RecoverabilityScorer, TierScorer end-to-end, ModelSelector integration,
escalation, backward compatibility, resolve_tier_map/resolve_tier_params.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from dan.models.control_flow import HumanNode, RouterNode, ValidatorNode
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.models.nodes import LLMOperator, ReflectionNode, RetryPolicy
from dan.models.ports import InputPort, OutputPort
from dan.providers.model_policy import (
    CascadePolicy,
    StaticPolicy,
    TaskTier,
    TierPolicy,
    TierWeights,
    ModelConstraints,
)
from dan.providers.model_selector import ModelSelector, SelectionResult
from dan.providers.tier_scorer import (
    DifficultyScorer,
    ImpactScorer,
    RecoverabilityScorer,
    TierResult,
    TierScorer,
    _clamp,
    _TIER_ORDER,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _llm_node(id_: str = "n1", **kw) -> LLMOperator:
    defaults = dict(
        id=id_,
        name="test",
        model="test-model",
        prompt_template="test",
        input_ports=[InputPort(name="input")],
        output_ports=[OutputPort(name="text")],
    )
    defaults.update(kw)
    return LLMOperator(**defaults)


def _router_node(id_: str = "r1", **kw) -> RouterNode:
    defaults = dict(
        id=id_,
        name="router",
        model="test-model",
        input_ports=[InputPort(name="input")],
        output_ports=[OutputPort(name="branch")],
    )
    defaults.update(kw)
    return RouterNode(**defaults)


def _reflection_node(id_: str = "ref1", **kw) -> ReflectionNode:
    defaults = dict(
        id=id_,
        name="reflection",
        input_ports=[InputPort(name="input")],
        output_ports=[OutputPort(name="output")],
    )
    defaults.update(kw)
    return ReflectionNode(**defaults)


def _data_edge(
    id_: str,
    source_id: str,
    target_id: str,
    source_port: str = "text",
    target_port: str = "input",
) -> DataEdge:
    return DataEdge(
        id=id_,
        source_node_id=source_id,
        target_node_id=target_id,
        source_port=source_port,
        target_port=target_port,
    )


def _schema_with_props(n: int) -> dict:
    return {
        "type": "object",
        "properties": {f"p{i}": {"type": "string"} for i in range(n)},
    }


# ===================================================================
# 6. TaskTier, TierWeights, TierPolicy serialization
# ===================================================================


class TestTaskTier:
    """TaskTier.from_score() boundary values."""

    def test_from_score_micro(self):
        assert TaskTier.from_score(0.0) == TaskTier.micro
        assert TaskTier.from_score(0.24) == TaskTier.micro

    def test_from_score_routine(self):
        assert TaskTier.from_score(0.25) == TaskTier.routine
        assert TaskTier.from_score(0.49) == TaskTier.routine

    def test_from_score_reasoning(self):
        assert TaskTier.from_score(0.50) == TaskTier.reasoning
        assert TaskTier.from_score(0.74) == TaskTier.reasoning

    def test_from_score_critical(self):
        assert TaskTier.from_score(0.75) == TaskTier.critical
        assert TaskTier.from_score(1.0) == TaskTier.critical

    def test_boundary_precision(self):
        assert TaskTier.from_score(0.2499) == TaskTier.micro
        assert TaskTier.from_score(0.2500) == TaskTier.routine
        assert TaskTier.from_score(0.4999) == TaskTier.routine
        assert TaskTier.from_score(0.5000) == TaskTier.reasoning
        assert TaskTier.from_score(0.7499) == TaskTier.reasoning
        assert TaskTier.from_score(0.7500) == TaskTier.critical


class TestTierWeights:
    def test_default_weights(self):
        w = TierWeights()
        assert w.difficulty == pytest.approx(0.45)
        assert w.impact == pytest.approx(0.35)
        assert w.recoverability == pytest.approx(0.20)

    def test_valid_custom_weights(self):
        w = TierWeights(difficulty=0.60, impact=0.30, recoverability=0.10)
        assert w.difficulty + w.impact + w.recoverability == pytest.approx(1.0)

    def test_invalid_weights_rejected(self):
        with pytest.raises(ValueError, match="must sum to 1.0"):
            TierWeights(difficulty=0.50, impact=0.50, recoverability=0.50)

    def test_tolerance_boundary_accepted(self):
        """abs(sum - 1.0) < 0.01 is acceptable."""
        w = TierWeights(difficulty=0.453, impact=0.35, recoverability=0.20)
        assert w.difficulty == pytest.approx(0.453)

    def test_tolerance_boundary_rejected(self):
        """abs(sum - 1.0) >= 0.01 is rejected."""
        with pytest.raises(ValueError, match="must sum to 1.0"):
            TierWeights(difficulty=0.46, impact=0.35, recoverability=0.20)


class TestTierPolicySerialization:
    def test_round_trip(self):
        policy = TierPolicy(
            tier_map={
                "micro": "gpt-4o-mini",
                "routine": "gpt-4o",
                "reasoning": "o3-mini",
                "critical": "o3",
            },
            weights=TierWeights(difficulty=0.50, impact=0.30, recoverability=0.20),
            constraints=ModelConstraints(max_cost=0.50),
        )
        json_str = policy.model_dump_json()
        restored = TierPolicy.model_validate_json(json_str)
        assert restored.strategy == "tier"
        assert restored.tier_map == policy.tier_map
        assert restored.weights.difficulty == pytest.approx(0.50)
        assert restored.constraints.max_cost == pytest.approx(0.50)

    def test_minimal_round_trip(self):
        policy = TierPolicy()
        restored = TierPolicy.model_validate_json(policy.model_dump_json())
        assert restored.strategy == "tier"
        assert restored.tier_map is None
        assert restored.weights is None


# ===================================================================
# 6-1. Scorer unit tests
# ===================================================================


class TestDifficultyScorer:
    def test_llm_operator_base(self):
        score = DifficultyScorer().score(_llm_node())
        assert score == pytest.approx(0.40)

    def test_router_base(self):
        score = DifficultyScorer().score(_router_node())
        assert score == pytest.approx(0.15)

    def test_reflection_base(self):
        score = DifficultyScorer().score(_reflection_node())
        assert score == pytest.approx(0.70)

    def test_complex_output_schema_increases(self):
        node = _llm_node(
            output_ports=[OutputPort(name="out", json_schema=_schema_with_props(3))]
        )
        score = DifficultyScorer().score(node)
        assert score == pytest.approx(0.45)

    def test_many_props_capped(self):
        node = _llm_node(
            output_ports=[OutputPort(name="out", json_schema=_schema_with_props(20))]
        )
        score = DifficultyScorer().score(node)
        assert score == pytest.approx(0.55)

    def test_tools_increase_score(self):
        score = DifficultyScorer().score(_llm_node(), tool_count=9)
        assert score == pytest.approx(0.55)

    def test_tools_capped(self):
        score = DifficultyScorer().score(_llm_node(), tool_count=30)
        assert score == pytest.approx(0.55)

    def test_score_clamped_upper(self):
        node = _reflection_node(
            output_ports=[OutputPort(name="out", json_schema=_schema_with_props(20))]
        )
        score = DifficultyScorer().score(node, tool_count=20)
        assert score == pytest.approx(1.0)
        assert score <= 1.0

    def test_score_always_non_negative(self):
        score = DifficultyScorer().score(_router_node())
        assert score >= 0.0


class TestImpactScorer:
    def test_terminal_node_high(self):
        node = _llm_node("n1")
        scorer = ImpactScorer(Graph(nodes=[node], edges=[]))
        assert scorer.score("n1") == pytest.approx(0.80)

    def test_internal_node_low(self):
        n1, n2 = _llm_node("n1"), _llm_node("n2")
        scorer = ImpactScorer(
            Graph(nodes=[n1, n2], edges=[_data_edge("e1", "n1", "n2")])
        )
        assert scorer.score("n1") == pytest.approx(0.25)

    def test_fan_out_increases(self):
        src = _llm_node("src")
        targets = [_llm_node(f"t{i}") for i in range(3)]
        edges = [_data_edge(f"e{i}", "src", f"t{i}") for i in range(3)]
        scorer = ImpactScorer(Graph(nodes=[src, *targets], edges=edges))
        assert scorer.score("src") == pytest.approx(0.35)

    def test_fan_out_higher_than_single(self):
        single_scorer = ImpactScorer(
            Graph(
                nodes=[_llm_node("s"), _llm_node("t")],
                edges=[_data_edge("e0", "s", "t")],
            )
        )
        fan3_scorer = ImpactScorer(
            Graph(
                nodes=[_llm_node("s"), *[_llm_node(f"t{i}") for i in range(3)]],
                edges=[_data_edge(f"e{i}", "s", f"t{i}") for i in range(3)],
            )
        )
        assert fan3_scorer.score("s") > single_scorer.score("s")

    def test_loop_body_sub_graph_decreases(self):
        node = _llm_node("sub_n")
        sub = Graph(nodes=[node], edges=[])
        graph = Graph(nodes=[node], edges=[], sub_graphs={"while1__body": sub})
        scorer = ImpactScorer(graph)
        assert scorer.score("sub_n") == pytest.approx(0.65)

    def test_non_loop_sub_graph_no_penalty(self):
        node = _llm_node("sub_n")
        sub = Graph(nodes=[node], edges=[])
        graph = Graph(nodes=[node], edges=[], sub_graphs={"some_composite": sub})
        scorer = ImpactScorer(graph)
        assert scorer.score("sub_n") == pytest.approx(0.80)

    def test_feeds_human_direct(self):
        n1 = _llm_node("n1")
        h = HumanNode(id="h1", name="human")
        scorer = ImpactScorer(
            Graph(nodes=[n1, h], edges=[_data_edge("e1", "n1", "h1")])
        )
        assert scorer.score("n1") == pytest.approx(0.90)

    def test_feeds_human_transitive(self):
        n1 = _llm_node("n1")
        n2 = _llm_node("n2")
        h = HumanNode(id="h1", name="human")
        scorer = ImpactScorer(
            Graph(
                nodes=[n1, n2, h],
                edges=[
                    _data_edge("e1", "n1", "n2"),
                    _data_edge("e2", "n2", "h1"),
                ],
            )
        )
        assert scorer.score("n1") == pytest.approx(0.90)
        assert scorer.score("n2") == pytest.approx(0.90)

    def test_default_for_unknown(self):
        scorer = ImpactScorer(Graph(nodes=[_llm_node("n1")], edges=[]))
        assert scorer.score("nonexistent") == pytest.approx(0.40)


class TestRecoverabilityScorer:
    def test_retry_bonus(self):
        node = _llm_node(retry_policy=RetryPolicy(max_retries=3))
        assert RecoverabilityScorer().score(node) == pytest.approx(0.20)

    def test_output_schema_bonus(self):
        node = _llm_node(
            output_ports=[OutputPort(name="out", json_schema=_schema_with_props(1))]
        )
        assert RecoverabilityScorer().score(node) == pytest.approx(0.25)

    def test_while_loop_body_bonus(self):
        node = _llm_node("loop_n")
        body = Graph(nodes=[node], edges=[])
        graph = Graph(
            nodes=[node], edges=[], sub_graphs={"while1__body": body}
        )
        assert RecoverabilityScorer().score(node, graph=graph) == pytest.approx(0.25)

    def test_combined_retry_schema_loop(self):
        node = _llm_node(
            id_="combo",
            retry_policy=RetryPolicy(max_retries=2),
            output_ports=[OutputPort(name="out", json_schema=_schema_with_props(1))],
        )
        body = Graph(nodes=[node], edges=[])
        graph = Graph(
            nodes=[node], edges=[], sub_graphs={"while1__body": body}
        )
        assert RecoverabilityScorer().score(node, graph=graph) == pytest.approx(0.70)

    def test_clamped_all_bonuses(self):
        node = _llm_node(
            id_="max_r",
            retry_policy=RetryPolicy(max_retries=3),
            output_ports=[OutputPort(name="out", json_schema=_schema_with_props(1))],
            model_policy=CascadePolicy(models=["m1", "m2"]),
        )
        validator = ValidatorNode(id="val", name="validator")
        body = Graph(nodes=[node, validator], edges=[])
        graph = Graph(
            nodes=[node, validator],
            edges=[_data_edge("ev", "max_r", "val")],
            sub_graphs={"while1__body": body},
        )
        score = RecoverabilityScorer().score(node, graph=graph)
        assert 0.0 <= score <= 1.0


# ===================================================================
# 6-2. TierScorer end-to-end
# ===================================================================


class TestTierScorer:
    def test_explicit_task_tier_floor_enforced(self):
        """Explicit task_tier acts as a floor — scoring still runs."""
        node = _llm_node(task_tier="critical")
        result = TierScorer().score_node(node)
        assert result.tier == TaskTier.critical
        assert result.difficulty > 0.0
        assert result.tier_score >= 0.75

    def test_explicit_tier_floor_does_not_prevent_upward(self):
        """Low floor (micro) does not prevent scoring from assigning higher tier."""
        node = _llm_node(task_tier="micro")
        result = TierScorer().score_node(node)
        assert result.tier.value in ("routine", "reasoning", "critical")
        assert result.difficulty > 0.0

    @pytest.mark.parametrize(
        "tier_name",
        ["micro", "routine", "reasoning", "critical"],
    )
    def test_explicit_tier_at_least_floor(self, tier_name):
        """Result tier is always >= the explicit floor."""
        from dan.providers.tier_scorer import _TIER_ORDER
        result = TierScorer().score_node(_llm_node(task_tier=tier_name))
        floor = TaskTier(tier_name)
        assert _TIER_ORDER.index(result.tier) >= _TIER_ORDER.index(floor)

    def test_weights_change_tier(self):
        """Same node, different weights → different tier."""
        node = _llm_node("w", retry_policy=RetryPolicy(max_retries=3))
        graph = Graph(nodes=[node], edges=[])

        default_result = TierScorer(graph=graph).score_node(node)
        # d=0.40, i=0.80, r=0.20 → 0.45*0.40+0.35*0.80+0.20*0.80 = 0.62
        assert default_result.tier_score == pytest.approx(0.62)
        assert default_result.tier == TaskTier.reasoning

        custom = TierWeights(difficulty=0.80, impact=0.10, recoverability=0.10)
        custom_result = TierScorer(graph=graph, weights=custom).score_node(node)
        # 0.80*0.40+0.10*0.80+0.10*0.80 = 0.48
        assert custom_result.tier_score == pytest.approx(0.48)
        assert custom_result.tier == TaskTier.routine

    def test_result_fields_populated(self):
        node = _llm_node()
        graph = Graph(nodes=[node], edges=[])
        result = TierScorer(graph=graph).score_node(node)
        assert isinstance(result, TierResult)
        assert isinstance(result.tier, TaskTier)
        assert 0.0 <= result.tier_score <= 1.0
        assert 0.0 <= result.difficulty <= 1.0
        assert 0.0 <= result.impact <= 1.0
        assert 0.0 <= result.recoverability <= 1.0

    def test_no_graph_defaults(self):
        result = TierScorer().score_node(_llm_node())
        # d=0.40, i=0.40 (default), r=0.0 →
        # 0.45*0.40 + 0.35*0.40 + 0.20*(1-0) = 0.18+0.14+0.20 = 0.52
        assert result.tier_score == pytest.approx(0.52)
        assert result.tier == TaskTier.reasoning


# ===================================================================
# 6-3. TierPolicy integration — graph with mixed nodes
# ===================================================================


class TestTierPolicyIntegration:
    def test_three_node_graph(self):
        router = _router_node("r1")
        n1 = _llm_node("n1")
        n2 = _llm_node("n2")

        graph = Graph(
            nodes=[router, n1, n2],
            edges=[
                _data_edge("e1", "r1", "n1", source_port="branch"),
                _data_edge("e2", "n1", "n2"),
            ],
        )

        scorer = TierScorer(graph=graph)
        r_res = scorer.score_node(router)
        n1_res = scorer.score_node(n1)
        n2_res = scorer.score_node(n2)

        assert n2_res.tier_score > n1_res.tier_score, "terminal node scores higher"
        assert n2_res.tier_score > r_res.tier_score
        assert r_res.tier_score < n1_res.tier_score, "router scores lowest"
        assert n2_res.tier in (TaskTier.reasoning, TaskTier.critical)

    def test_nodes_get_distinct_difficulty(self):
        router = _router_node("r")
        llm = _llm_node("l")
        ref = _reflection_node("f")

        graph = Graph(nodes=[router, llm, ref], edges=[])
        scorer = TierScorer(graph=graph)

        r_d = scorer.score_node(router).difficulty
        l_d = scorer.score_node(llm).difficulty
        f_d = scorer.score_node(ref).difficulty

        assert r_d < l_d < f_d


# ===================================================================
# 6-4. Escalation
# ===================================================================


class TestEscalation:
    @pytest.fixture(autouse=True)
    def _import_escalate(self):
        from dan.executors.llm import _escalate_tier

        self._escalate = _escalate_tier

    def test_micro_to_routine(self):
        assert self._escalate(TaskTier.micro) == TaskTier.routine

    def test_routine_to_reasoning(self):
        assert self._escalate(TaskTier.routine) == TaskTier.reasoning

    def test_reasoning_to_critical(self):
        assert self._escalate(TaskTier.reasoning) == TaskTier.critical

    def test_critical_stays_critical(self):
        assert self._escalate(TaskTier.critical) == TaskTier.critical

    def test_escalation_one_step_only(self):
        tier = TaskTier.micro
        tier = self._escalate(tier)
        assert tier == TaskTier.routine
        tier = self._escalate(tier)
        assert tier == TaskTier.reasoning


# ===================================================================
# 6-6. Backward compatibility
# ===================================================================


class TestBackwardCompatibility:
    def test_no_policy_returns_static(self):
        """Node with model but no model_policy → StaticPolicy."""
        node = _llm_node(model="gpt-4")
        engine_cfg = SimpleNamespace(
            default_model_policy=None, llm_default_model="fallback"
        )
        selector = ModelSelector(provider_registry=MagicMock())
        policy = selector.resolve_effective_policy(node, engine_cfg)
        assert isinstance(policy, StaticPolicy)
        assert policy.model == "gpt-4"

    def test_static_policy_preserved(self):
        node = _llm_node(model_policy=StaticPolicy(model="claude-sonnet-4-6"))
        engine_cfg = SimpleNamespace(
            default_model_policy=None, llm_default_model="default"
        )
        selector = ModelSelector(provider_registry=MagicMock())
        policy = selector.resolve_effective_policy(node, engine_cfg)
        assert isinstance(policy, StaticPolicy)
        assert policy.model == "claude-sonnet-4-6"

    def test_engine_default_fallback(self):
        """Node with empty model and no policy falls through to engine default."""
        node = _llm_node(model="")
        engine_cfg = SimpleNamespace(
            default_model_policy=None, llm_default_model="engine-default"
        )
        selector = ModelSelector(provider_registry=MagicMock())
        policy = selector.resolve_effective_policy(node, engine_cfg)
        assert isinstance(policy, StaticPolicy)
        assert policy.model == "engine-default"


# ===================================================================
# Misc: _clamp helper
# ===================================================================


class TestClamp:
    def test_above_one(self):
        assert _clamp(1.5) == 1.0

    def test_below_zero(self):
        assert _clamp(-0.5) == 0.0

    def test_in_range_unchanged(self):
        assert _clamp(0.42) == pytest.approx(0.42)

    def test_boundary_one(self):
        assert _clamp(1.0) == 1.0

    def test_boundary_zero(self):
        assert _clamp(0.0) == 0.0


# ===================================================================
# 6-3b. ModelSelector.select() integration with SelectionResult
# ===================================================================


class TestModelSelectorTierIntegration:
    def _make_selector(self) -> ModelSelector:
        provider_reg = MagicMock()
        provider_reg.resolve = MagicMock(return_value=MagicMock())
        return ModelSelector(provider_registry=provider_reg)

    def _make_context(self, graph: Graph | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            config=SimpleNamespace(
                llm_default_model="default-model",
                default_model_policy=None,
                providers={"anthropic": {}},
                model_provider_map={},
                tier_map=None,
                tier_params=None,
            ),
            graph=graph,
            tool_registry=None,
        )

    @pytest.mark.asyncio
    async def test_tier_policy_returns_selection_result(self):
        selector = self._make_selector()
        result = await selector.select(TierPolicy(), _llm_node(), self._make_context())
        assert isinstance(result, SelectionResult)
        assert isinstance(result.model, str)
        assert isinstance(result.tier_result, TierResult)
        assert isinstance(result.tier_params, dict)

    @pytest.mark.asyncio
    async def test_static_policy_returns_selection_result(self):
        selector = self._make_selector()
        result = await selector.select(StaticPolicy(model="gpt-4o"))
        assert isinstance(result, SelectionResult)
        assert result.model == "gpt-4o"
        assert result.tier_result is None
        assert result.tier_params == {}

    @pytest.mark.asyncio
    async def test_three_node_graph_difficulty_ordering(self):
        router = _router_node("r1")
        llm = _llm_node("n1")
        ref = _reflection_node("ref1")
        graph = Graph(
            nodes=[router, llm, ref],
            edges=[
                _data_edge("e1", "r1", "n1", source_port="branch"),
                _data_edge("e2", "n1", "ref1"),
            ],
        )
        selector = self._make_selector()
        ctx = self._make_context(graph)
        results = {}
        for node in [router, llm, ref]:
            sr = await selector.select(TierPolicy(), node, ctx)
            results[node.id] = sr.tier_result
        assert results["r1"].difficulty < results["n1"].difficulty < results["ref1"].difficulty


class TestFloorEnforcement:
    def test_floor_at_least_declared_tier(self):
        for tier_name in ("micro", "routine", "reasoning", "critical"):
            result = TierScorer().score_node(_llm_node(task_tier=tier_name))
            floor = TaskTier(tier_name)
            assert _TIER_ORDER.index(result.tier) >= _TIER_ORDER.index(floor)

    def test_floor_populates_real_dimensions(self):
        result = TierScorer().score_node(_llm_node(task_tier="critical"))
        assert result.difficulty > 0.0
        assert result.impact > 0.0

    def test_floor_does_not_cap_upward(self):
        node = _reflection_node(task_tier="micro")
        result = TierScorer(graph=Graph(nodes=[node], edges=[])).score_node(node)
        assert result.tier != TaskTier.micro


# ===================================================================
# 6-6b. Additional backward compatibility
# ===================================================================


class TestBackwardCompatExtended:
    def test_engine_default_tier_policy_resolves(self):
        node = _llm_node(model="")
        engine_cfg = SimpleNamespace(
            default_model_policy=TierPolicy(),
            llm_default_model="fallback",
        )
        selector = ModelSelector(provider_registry=MagicMock())
        policy = selector.resolve_effective_policy(node, engine_cfg)
        assert isinstance(policy, TierPolicy)

    def test_node_model_policy_overrides_engine_tier(self):
        node = _llm_node(model_policy=StaticPolicy(model="override"))
        engine_cfg = SimpleNamespace(
            default_model_policy=TierPolicy(),
            llm_default_model="fallback",
        )
        selector = ModelSelector(provider_registry=MagicMock())
        policy = selector.resolve_effective_policy(node, engine_cfg)
        assert isinstance(policy, StaticPolicy)
        assert policy.model == "override"


# ===================================================================
# resolve_tier_map / resolve_tier_params (tier_defaults.py)
# ===================================================================


class TestResolveTierMap:
    def test_full_user_override_returned_as_is(self):
        from dan.providers.tier_defaults import resolve_tier_map

        override = {"micro": "m1", "routine": "m2", "reasoning": "m3", "critical": "m4"}
        result = resolve_tier_map(["openai"], override)
        assert result == override

    def test_anthropic_preferred_over_openai(self):
        from dan.providers.tier_defaults import resolve_tier_map

        result = resolve_tier_map(["openai", "anthropic"])
        assert result["micro"] == "claude-3-5-haiku-20241022"

    def test_openai_when_only_configured(self):
        from dan.providers.tier_defaults import resolve_tier_map

        result = resolve_tier_map(["openai"])
        assert result["micro"] == "gpt-4o-mini"

    def test_google_when_only_configured(self):
        from dan.providers.tier_defaults import resolve_tier_map

        result = resolve_tier_map(["google"])
        assert result["reasoning"] == "gemini-2.5-pro"

    def test_no_providers_falls_back_to_anthropic(self):
        from dan.providers.tier_defaults import resolve_tier_map

        result = resolve_tier_map([])
        assert result["routine"] == "claude-sonnet-4-6"

    def test_partial_override_merged(self):
        from dan.providers.tier_defaults import resolve_tier_map

        result = resolve_tier_map(["anthropic"], {"micro": "custom-tiny"})
        assert result["micro"] == "custom-tiny"
        assert result["routine"] == "claude-sonnet-4-6"

    def test_unknown_provider_ignored(self):
        from dan.providers.tier_defaults import resolve_tier_map

        result = resolve_tier_map(["unknown_provider"])
        assert "micro" in result


class TestResolveTierParams:
    def test_anthropic_critical_has_extended_thinking(self):
        from dan.providers.tier_defaults import resolve_tier_params

        result = resolve_tier_params("anthropic")
        assert result["critical"]["extended_thinking"] is True

    def test_openai_has_no_default_params(self):
        from dan.providers.tier_defaults import resolve_tier_params

        result = resolve_tier_params("openai")
        assert result == {}

    def test_user_override_merges(self):
        from dan.providers.tier_defaults import resolve_tier_params

        result = resolve_tier_params("anthropic", {"critical": {"max_tokens": 16384}})
        assert result["critical"]["extended_thinking"] is True
        assert result["critical"]["max_tokens"] == 16384

    def test_user_override_adds_new_tier(self):
        from dan.providers.tier_defaults import resolve_tier_params

        result = resolve_tier_params("anthropic", {"routine": {"temperature": 0.5}})
        assert result["routine"]["temperature"] == 0.5
        assert "critical" in result
