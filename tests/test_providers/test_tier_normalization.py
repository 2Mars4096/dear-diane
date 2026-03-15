"""Tests for normalize_tier_map() in tier_defaults."""

from __future__ import annotations

import pytest

from dan.providers.tier_defaults import normalize_tier_map


class TestNormalizeTierMapNamed:
    """Named (canonical) tier maps pass through unchanged."""

    def test_full_canonical_map(self):
        raw = {
            "micro": "model-a",
            "routine": "model-b",
            "reasoning": "model-c",
            "critical": "model-d",
        }
        assert normalize_tier_map(raw) == raw

    def test_partial_canonical_map(self):
        raw = {"micro": "model-a", "routine": "model-b"}
        assert normalize_tier_map(raw) == raw


class TestNormalizeTierMapNumeric:
    """Numeric shorthand maps normalize correctly."""

    def test_all_three_numeric(self):
        raw = {"1": "model-a", "2": "model-b", "3": "model-c"}
        result = normalize_tier_map(raw)
        assert result == {
            "micro": "model-a",
            "routine": "model-b",
            "reasoning": "model-c",
            "critical": "model-c",
        }

    def test_single_numeric_key(self):
        raw = {"1": "fast-model"}
        assert normalize_tier_map(raw) == {"micro": "fast-model"}

    def test_numeric_2(self):
        raw = {"2": "mid-model"}
        assert normalize_tier_map(raw) == {"routine": "mid-model"}


class TestThreeTierToFourTierExpansion:
    """Numeric '3' maps to both reasoning and critical."""

    def test_3_expands_to_reasoning_and_critical(self):
        raw = {"3": "big-model"}
        result = normalize_tier_map(raw)
        assert result is not None
        assert result["reasoning"] == "big-model"
        assert result["critical"] == "big-model"

    def test_3_does_not_overwrite_explicit_critical(self):
        raw = {"3": "big-model", "critical": "mega-model"}
        result = normalize_tier_map(raw)
        assert result is not None
        assert result["reasoning"] == "big-model"
        assert result["critical"] == "mega-model"


class TestNormalizeTierMapMixed:
    """Mixed maps with some numeric and some named keys."""

    def test_numeric_and_named_mixed(self):
        raw = {"1": "model-a", "routine": "model-b", "3": "model-c"}
        result = normalize_tier_map(raw)
        assert result == {
            "micro": "model-a",
            "routine": "model-b",
            "reasoning": "model-c",
            "critical": "model-c",
        }

    def test_named_takes_precedence_via_setdefault(self):
        raw = {"micro": "explicit-micro", "1": "numeric-micro"}
        result = normalize_tier_map(raw)
        assert result is not None
        assert result["micro"] == "explicit-micro"


class TestNormalizeTierMapEdgeCases:
    """Empty/None input."""

    def test_none_returns_none(self):
        assert normalize_tier_map(None) is None

    def test_empty_dict_returns_none(self):
        assert normalize_tier_map({}) is None

    def test_unknown_keys_ignored(self):
        raw = {"foo": "bar", "baz": "qux"}
        assert normalize_tier_map(raw) is None
