"""Tests for concierge policy — cost estimation and resolve_policy."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from dan.server.concierge.models import IntentCategory
from dan.server.concierge.policy import (
    ActionPolicy,
    ExecutionPolicy,
    estimate_action_cost,
    resolve_policy,
)


class TestEstimateActionCost:
    def test_plan_with_long_horizon_goal_returns_high_cost(self):
        assert estimate_action_cost(IntentCategory.PLAN, "build me an ETL pipeline", action_hints=["long_horizon_goal"]) == 2.0

    def test_plan_simple_mutation(self):
        cost = estimate_action_cost(IntentCategory.PLAN, "add a new node")
        assert cost == pytest.approx(0.05)

    def test_plan_full_build(self):
        cost = estimate_action_cost(IntentCategory.PLAN, "create a workflow for data processing")
        assert cost == pytest.approx(0.50)

    def test_plan_edit(self):
        cost = estimate_action_cost(IntentCategory.PLAN, "edit the prompt in node 3")
        assert cost == pytest.approx(0.05)

    def test_ask_returns_zero(self):
        assert estimate_action_cost(IntentCategory.ASK, "hello") == 0.0

    def test_agent_returns_zero(self):
        assert estimate_action_cost(IntentCategory.AGENT, "find my report") == 0.0


class TestResolvePolicyCostEstimation:
    def _resolve(
        self,
        intent: IntentCategory,
        text: str,
        surface: str = "web",
        estimated_cost: float = 0.0,
        cost_confirm_threshold: float = 1.0,
        action_hints: list[str] | None = None,
    ) -> tuple[ActionPolicy, ExecutionPolicy]:
        return resolve_policy(
            intent=intent,
            action_hints=action_hints,
            text=text,
            context=None,
            user_profile=None,
            surface=surface,
            estimated_cost=estimated_cost,
            cost_confirm_threshold=cost_confirm_threshold,
        )

    def test_auto_estimates_plan_with_long_horizon_above_threshold(self):
        action, _ = self._resolve(IntentCategory.PLAN, "build a pipeline", action_hints=["long_horizon_goal"])
        assert action == ActionPolicy.CONFIRM

    def test_explicit_cost_below_threshold_stays_auto(self):
        action, _ = self._resolve(IntentCategory.PLAN, "build a thing", estimated_cost=0.5)
        assert action == ActionPolicy.AUTO

    def test_explicit_cost_above_threshold_confirms(self):
        action, _ = self._resolve(IntentCategory.ASK, "hi", estimated_cost=5.0)
        assert action == ActionPolicy.CONFIRM

    def test_env_threshold_lowers_barrier(self):
        with patch.dict(os.environ, {"DAN_COST_CONFIRM_THRESHOLD": "0.01"}):
            action, _ = self._resolve(IntentCategory.PLAN, "add a node")
            assert action == ActionPolicy.CONFIRM

    def test_env_threshold_invalid_value_ignored(self):
        with patch.dict(os.environ, {"DAN_COST_CONFIRM_THRESHOLD": "not_a_number"}):
            action, _ = self._resolve(IntentCategory.ASK, "hello")
            assert action == ActionPolicy.AUTO

    def test_destructive_keyword_forces_confirm(self):
        action, _ = self._resolve(IntentCategory.AGENT, "delete everything")
        assert action == ActionPolicy.CONFIRM

    def test_messaging_surface_mutation_confirms(self):
        action, _ = self._resolve(IntentCategory.PLAN, "do a thing", surface="whatsapp")
        assert action == ActionPolicy.CONFIRM

    def test_zero_cost_intent_on_web_stays_auto(self):
        action, _ = self._resolve(IntentCategory.ASK, "hello there")
        assert action == ActionPolicy.AUTO
