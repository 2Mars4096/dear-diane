"""Tests for cost estimation utilities."""

from __future__ import annotations

from dan.providers.costs import COST_PER_1K_TOKENS, estimate_cost


class TestEstimateCost:
    def test_known_model_exact(self):
        cost = estimate_cost("gpt-4o", prompt_tokens=1000, completion_tokens=500)
        assert cost is not None
        expected = (1000 * 0.0025 + 500 * 0.01) / 1000
        assert abs(cost - expected) < 1e-10

    def test_known_model_anthropic(self):
        cost = estimate_cost("claude-sonnet-4", prompt_tokens=2000, completion_tokens=1000)
        assert cost is not None
        assert cost > 0

    def test_unknown_model_returns_none(self):
        assert estimate_cost("llama-3.2-70b", 100, 100) is None

    def test_prefix_matching(self):
        cost = estimate_cost("gpt-4o-2024-11-20", prompt_tokens=1000, completion_tokens=1000)
        assert cost is not None

    def test_zero_tokens(self):
        cost = estimate_cost("gpt-4o", prompt_tokens=0, completion_tokens=0)
        assert cost == 0.0

    def test_cost_table_has_major_models(self):
        required = ["gpt-4o", "gpt-4o-mini", "claude-opus-4", "claude-sonnet-4", "gemini-2.0-flash"]
        for model in required:
            assert model in COST_PER_1K_TOKENS, f"Missing model in cost table: {model}"
