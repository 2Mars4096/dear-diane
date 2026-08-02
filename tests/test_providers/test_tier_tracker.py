"""Tests for TierSuccessTracker (18-5 task 4-2)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from dan.providers.tier_tracker import NodeTierStats, TierSuccessTracker


def test_record_success_increments_consecutive_successes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tracker = TierSuccessTracker(Path(tmp))
        tracker.record_success("n1", "llm_operator", "routine")
        tracker.record_success("n1", "llm_operator", "routine")
        stats = tracker.get_stats("n1")
        assert stats is not None
        assert stats.consecutive_successes == 2
        assert stats.consecutive_failures == 0
        assert stats.total_runs == 2
        assert stats.total_successes == 2


def test_record_failure_resets_consecutive_successes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tracker = TierSuccessTracker(Path(tmp))
        for _ in range(5):
            tracker.record_success("n1", "llm_operator", "reasoning")
        tracker.record_failure("n1", "llm_operator", "reasoning")
        stats = tracker.get_stats("n1")
        assert stats is not None
        assert stats.consecutive_successes == 0
        assert stats.consecutive_failures == 1
        assert stats.total_runs == 6
        assert stats.total_successes == 5


def test_suggest_deescalation_after_threshold() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tracker = TierSuccessTracker(Path(tmp))
        for _ in range(4):
            tracker.record_success("n1", "llm_operator", "reasoning")
        assert tracker.suggest_deescalation("n1", "reasoning", threshold=5) is None
        tracker.record_success("n1", "llm_operator", "reasoning")
        suggested = tracker.suggest_deescalation("n1", "reasoning", threshold=5)
        assert suggested == "routine"


def test_suggest_deescalation_never_below_micro() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tracker = TierSuccessTracker(Path(tmp))
        for _ in range(10):
            tracker.record_success("n1", "llm_operator", "micro")
        suggested = tracker.suggest_deescalation("n1", "micro", threshold=5)
        assert suggested is None


def test_suggest_deescalation_tier_order() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tracker = TierSuccessTracker(Path(tmp))
        for tier in ["critical", "reasoning", "routine"]:
            for _ in range(5):
                tracker.record_success("n1", "llm_operator", tier)
            suggested = tracker.suggest_deescalation("n1", tier, threshold=5)
            if tier == "critical":
                assert suggested == "reasoning"
            elif tier == "reasoning":
                assert suggested == "routine"
            elif tier == "routine":
                assert suggested == "micro"
            tracker.record_failure("n1", "llm_operator", tier)  # reset for next tier


def test_persistence_round_trip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        tracker1 = TierSuccessTracker(path)
        tracker1.record_success("n1", "llm_operator", "routine")
        tracker1.record_success("n1", "llm_operator", "routine")
        tracker1.save()

        tracker2 = TierSuccessTracker(path)
        stats = tracker2.get_stats("n1")
        assert stats is not None
        assert stats.consecutive_successes == 2
        assert stats.node_type == "llm_operator"
        assert stats.current_tier == "routine"


def test_stats_tracking_accuracy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tracker = TierSuccessTracker(Path(tmp))
        tracker.record_success("a", "llm_operator", "micro")
        tracker.record_success("a", "llm_operator", "micro")
        tracker.record_failure("a", "llm_operator", "micro")
        tracker.record_success("b", "router", "routine")
        tracker.record_success("b", "router", "routine")

        stats_a = tracker.get_stats("a")
        assert stats_a is not None
        assert stats_a.node_id == "a"
        assert stats_a.node_type == "llm_operator"
        assert stats_a.current_tier == "micro"
        assert stats_a.consecutive_successes == 0  # reset by failure
        assert stats_a.consecutive_failures == 1
        assert stats_a.total_runs == 3
        assert stats_a.total_successes == 2

        stats_b = tracker.get_stats("b")
        assert stats_b is not None
        assert stats_b.node_id == "b"
        assert stats_b.node_type == "router"
        assert stats_b.consecutive_successes == 2
        assert stats_b.total_runs == 2


def test_get_stats_returns_none_for_unknown_node() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tracker = TierSuccessTracker(Path(tmp))
        assert tracker.get_stats("unknown") is None


def test_node_tier_stats_serialization() -> None:
    stats = NodeTierStats(
        node_id="n1",
        node_type="llm_operator",
        current_tier="reasoning",
        consecutive_successes=3,
        consecutive_failures=0,
        total_runs=5,
        total_successes=4,
        last_suggested_tier="routine",
    )
    d = stats.to_dict()
    assert d["node_id"] == "n1"
    assert d["consecutive_successes"] == 3
    assert d["last_suggested_tier"] == "routine"

    restored = NodeTierStats.from_dict(d)
    assert restored.node_id == stats.node_id
    assert restored.consecutive_successes == stats.consecutive_successes
