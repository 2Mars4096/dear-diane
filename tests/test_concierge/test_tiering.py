"""Tests for ConciergeTierResolver and stage→tier→model resolution."""

from __future__ import annotations

import pytest

from dan.server.concierge.tiering import CONCIERGE_STAGE_TIERS, ConciergeTierResolver


FULL_TIER_MAP = {
    "micro": "gpt-4o-mini",
    "routine": "gpt-4o",
    "reasoning": "o3-mini",
    "critical": "o3",
}

FALLBACK = "claude-sonnet-4-6"


class TestResolveTier:
    """Stage → tier name resolution."""

    def test_known_stages(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_tier("classification") == "micro"
        assert resolver.resolve_tier("intent_extraction") == "reasoning"
        assert resolver.resolve_tier("conversation") == "routine"
        assert resolver.resolve_tier("workflow_build") == "reasoning"

    def test_unknown_stage_defaults_to_routine(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_tier("unknown_stage") == "routine"


class TestResolveModel:
    """Stage → tier → model resolution."""

    def test_known_stage_resolves_model(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_model("classification") == "gpt-4o-mini"
        assert resolver.resolve_model("intent_extraction") == "o3-mini"
        assert resolver.resolve_model("conversation") == "gpt-4o"

    def test_unknown_stage_uses_routine_tier(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_model("unknown_stage") == "gpt-4o"

    def test_missing_tier_in_map_uses_fallback(self):
        partial_map = {"micro": "gpt-4o-mini"}
        resolver = ConciergeTierResolver(partial_map, FALLBACK)
        assert resolver.resolve_model("classification") == "gpt-4o-mini"
        assert resolver.resolve_model("conversation") == FALLBACK

    def test_empty_tier_map_always_falls_back(self):
        resolver = ConciergeTierResolver({}, FALLBACK)
        assert resolver.resolve_model("classification") == FALLBACK
        assert resolver.resolve_model("workflow_build") == FALLBACK


class TestClassifierUsesMicroTier:
    """7-1: Verify classifier stage resolves to the micro-tier model."""

    def test_custom_map_classifier_resolves_micro(self):
        custom_map = {
            "micro": "my-cheap-model",
            "routine": "my-mid-model",
            "reasoning": "my-big-model",
            "critical": "my-biggest-model",
        }
        resolver = ConciergeTierResolver(custom_map, FALLBACK)
        assert resolver.resolve_model("classification") == "my-cheap-model"

    def test_default_map_classifier(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_tier("classification") == "micro"
        assert resolver.resolve_model("classification") == "gpt-4o-mini"


class TestSolverUsesReasoningTier:
    """7-2: Verify goal_resolver stage resolves to the reasoning-tier model."""

    def test_goal_resolver_uses_reasoning(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_tier("goal_resolver") == "reasoning"
        assert resolver.resolve_model("goal_resolver") == "o3-mini"

    def test_custom_reasoning_model(self):
        custom_map = {**FULL_TIER_MAP, "reasoning": "deepseek-r1"}
        resolver = ConciergeTierResolver(custom_map, FALLBACK)
        assert resolver.resolve_model("goal_resolver") == "deepseek-r1"


class TestHandlerModelOverrides:
    """7-3: Verify handler stages resolve to expected tier models."""

    def test_file_review_uses_routine(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_tier("file_review") == "routine"
        assert resolver.resolve_model("file_review") == "gpt-4o"

    def test_conversation_uses_routine(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_tier("conversation") == "routine"
        assert resolver.resolve_model("conversation") == "gpt-4o"

    def test_direct_task_uses_routine(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_tier("direct_task") == "routine"
        assert resolver.resolve_model("direct_task") == "gpt-4o"

    def test_workflow_build_uses_reasoning(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_tier("workflow_build") == "reasoning"
        assert resolver.resolve_model("workflow_build") == "o3-mini"

    def test_conversation_plan_uses_reasoning(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_tier("conversation_plan") == "reasoning"
        assert resolver.resolve_model("conversation_plan") == "o3-mini"

    def test_conversation_debug_uses_reasoning(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_tier("conversation_debug") == "reasoning"
        assert resolver.resolve_model("conversation_debug") == "o3-mini"

    def test_experience_fallback_uses_routine(self):
        resolver = ConciergeTierResolver(FULL_TIER_MAP, FALLBACK)
        assert resolver.resolve_tier("experience_fallback") == "routine"
        assert resolver.resolve_model("experience_fallback") == "gpt-4o"


class TestIntegrationWithNormalize:
    """End-to-end: numeric tier map → normalize → resolver."""

    def test_numeric_map_round_trip(self):
        from dan.providers.tier_defaults import normalize_tier_map

        raw = {"1": "fast-model", "2": "mid-model", "3": "big-model"}
        normalized = normalize_tier_map(raw)
        assert normalized is not None

        resolver = ConciergeTierResolver(normalized, FALLBACK)
        assert resolver.resolve_model("classification") == "fast-model"
        assert resolver.resolve_model("conversation") == "mid-model"
        assert resolver.resolve_model("intent_extraction") == "big-model"
        assert resolver.resolve_model("workflow_build") == "big-model"

    def test_all_stage_tiers_are_valid(self):
        from dan.providers.tier_defaults import _TIER_KEYS

        for tier in CONCIERGE_STAGE_TIERS.values():
            assert tier in _TIER_KEYS, f"stage tier {tier!r} not in canonical set"

    def test_numeric_map_all_stages_smoke(self):
        """7-4: Build resolver with numeric DAN_TIER_MAP and verify all stages."""
        from dan.providers.tier_defaults import normalize_tier_map

        raw = {"1": "cheap-v1", "2": "standard-v2", "3": "powerful-v3"}
        normalized = normalize_tier_map(raw)
        assert normalized is not None

        resolver = ConciergeTierResolver(normalized, FALLBACK)

        expected = {
            "classification": "cheap-v1",         # micro
            "intent_extraction": "powerful-v3",    # reasoning
            "goal_resolver": "powerful-v3",        # reasoning
            "file_review": "standard-v2",          # routine
            "direct_task": "standard-v2",          # routine
            "experience_fallback": "standard-v2",  # routine
            "conversation": "standard-v2",         # routine
            "conversation_plan": "powerful-v3",    # reasoning
            "conversation_debug": "powerful-v3",   # reasoning
            "workflow_build": "powerful-v3",        # reasoning
        }
        for stage, expected_model in expected.items():
            resolved = resolver.resolve_model(stage)
            assert resolved == expected_model, (
                f"stage={stage}: expected {expected_model}, got {resolved}"
            )
