"""Tests for outcome trackers — PromptTracker, ModelOutcomeTracker,
ModelRecommender, TopologyOutcomeTracker, TopologyAdvisor (29-6 §7, §8, §10).
"""

from __future__ import annotations

import os
import time

import pytest

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryKernel,
    MemoryScope,
    MemoryType,
)
from dan.engine.outcome_trackers import (
    ModelOutcomeTracker,
    ModelRecommender,
    PromptTracker,
    TopologyAdvisor,
    TopologyOutcomeTracker,
    _is_enabled,
)


@pytest.fixture()
def kernel(tmp_path):
    return MemoryKernel(base_dir=str(tmp_path / "mem"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure env vars are clean before each test; individual tests set as needed."""
    monkeypatch.delenv("DAN_PROMPT_OPTIMIZATION", raising=False)
    monkeypatch.delenv("DAN_MODEL_LEARNING", raising=False)
    monkeypatch.delenv("DAN_TOPOLOGY_LEARNING", raising=False)


# ===================================================================
# Env-var gating
# ===================================================================


class TestEnvGating:
    def test_disabled_by_default(self):
        assert not _is_enabled("DAN_PROMPT_OPTIMIZATION")
        assert not _is_enabled("DAN_MODEL_LEARNING")
        assert not _is_enabled("DAN_TOPOLOGY_LEARNING")

    def test_enabled_when_set(self, monkeypatch):
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "1")
        assert _is_enabled("DAN_PROMPT_OPTIMIZATION")

    def test_prompt_tracker_noop_when_disabled(self, kernel):
        tracker = PromptTracker(kernel)
        result = tracker.record("n1", "abc", "in", "out", True, 100, 500.0)
        assert result is None
        assert tracker.get_history("n1") == []

    def test_model_tracker_noop_when_disabled(self, kernel):
        tracker = ModelOutcomeTracker(kernel)
        result = tracker.record("n1", "llm", "desc", "gpt-4", 0.9, 0.01, 500.0)
        assert result is None

    def test_model_recommender_noop_when_disabled(self, kernel):
        tracker = ModelOutcomeTracker(kernel)
        recommender = ModelRecommender(tracker)
        assert recommender.suggest("n1") is None

    def test_topology_tracker_noop_when_disabled(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        result = tracker.record("sig123", True)
        assert result is None

    def test_topology_advisor_noop_when_disabled(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        advisor = TopologyAdvisor(tracker)
        assert advisor.suggest({"nodes": [], "edges": []}) == []


# ===================================================================
# PromptTracker (7-1, 7-8)
# ===================================================================


class TestPromptTracker:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "1")

    def test_record_stores_episode(self, kernel):
        tracker = PromptTracker(kernel)
        item = tracker.record(
            node_id="llm-1",
            prompt_hash="abc123",
            input_summary="Summarize the document",
            output_summary="The document discusses...",
            outcome=True,
            tokens_used=500,
            latency_ms=1200.0,
        )
        assert item is not None
        assert item.memory_type == MemoryType.EPISODE
        assert item.scope == MemoryScope.WORKFLOW
        assert item.metadata["node_id"] == "llm-1"
        assert item.metadata["prompt_hash"] == "abc123"
        assert item.metadata["outcome"] is True
        assert item.metadata["tokens_used"] == 500
        assert item.metadata["latency_ms"] == 1200.0

    def test_get_history_returns_records(self, kernel):
        tracker = PromptTracker(kernel)
        for i in range(5):
            tracker.record(
                node_id="llm-1",
                prompt_hash=f"hash-{i}",
                input_summary=f"input-{i}",
                output_summary=f"output-{i}",
                outcome=i % 2 == 0,
                tokens_used=100 * (i + 1),
                latency_ms=200.0 * (i + 1),
            )
        # Another node — should not appear
        tracker.record("llm-2", "other", "in", "out", True, 50, 100.0)

        history = tracker.get_history("llm-1")
        assert len(history) == 5
        assert all(h["node_id"] == "llm-1" for h in history)

    def test_get_history_respects_limit(self, kernel):
        tracker = PromptTracker(kernel)
        for i in range(10):
            tracker.record("n1", f"h{i}", "in", "out", True, 100, 200.0)

        history = tracker.get_history("n1", limit=3)
        assert len(history) == 3

    def test_prompt_hash_deterministic(self):
        h1 = PromptTracker.prompt_hash("Hello, world!")
        h2 = PromptTracker.prompt_hash("Hello, world!")
        h3 = PromptTracker.prompt_hash("Different text")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 16


# ===================================================================
# ModelOutcomeTracker (8-1, 8-2)
# ===================================================================


class TestModelOutcomeTracker:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("DAN_MODEL_LEARNING", "1")

    def test_record_stores_episode(self, kernel):
        tracker = ModelOutcomeTracker(kernel)
        item = tracker.record(
            node_id="n1",
            node_type="llm",
            task_description="Summarize text",
            model="gpt-4o",
            quality_score=0.95,
            cost=0.002,
            latency_ms=800.0,
        )
        assert item is not None
        assert item.memory_type == MemoryType.EPISODE
        assert item.metadata["model"] == "gpt-4o"
        assert item.metadata["quality_score"] == 0.95
        assert item.metadata["cost"] == 0.002

    def test_get_node_records(self, kernel):
        tracker = ModelOutcomeTracker(kernel)
        tracker.record("n1", "llm", "task", "gpt-4o", 0.9, 0.01, 500.0)
        tracker.record("n1", "llm", "task", "claude-3", 0.95, 0.02, 400.0)
        tracker.record("n2", "llm", "other", "gpt-4o", 0.8, 0.01, 600.0)

        records = tracker.get_node_records("n1")
        assert len(records) == 2
        assert all(r["node_id"] == "n1" for r in records)

    def test_quality_scoring_range(self, kernel):
        tracker = ModelOutcomeTracker(kernel)
        item = tracker.record("n1", "llm", "task", "model", 1.0, 0.0, 0.0)
        assert item.metadata["quality_score"] == 1.0

        item2 = tracker.record("n1", "llm", "task", "model", 0.0, 0.05, 2000.0)
        assert item2.metadata["quality_score"] == 0.0


# ===================================================================
# ModelRecommender (8-3..8-8)
# ===================================================================


class TestModelRecommender:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("DAN_MODEL_LEARNING", "1")

    def _seed_records(self, kernel, node_id, model, count, quality, cost, latency):
        tracker = ModelOutcomeTracker(kernel)
        for _ in range(count):
            tracker.record(node_id, "llm", "task", model, quality, cost, latency)
        return tracker

    def test_insufficient_data_returns_none(self, kernel):
        tracker = ModelOutcomeTracker(kernel)
        for i in range(10):  # fewer than 15
            tracker.record("n1", "llm", "t", "gpt-4o", 0.9, 0.01, 500.0)

        recommender = ModelRecommender(tracker)
        assert recommender.suggest("n1") is None

    def test_suggest_with_enough_data(self, kernel):
        tracker = self._seed_records(kernel, "n1", "gpt-4o", 10, 0.7, 0.02, 800.0)
        self._seed_records(kernel, "n1", "claude-3", 8, 0.95, 0.03, 500.0)
        # Total = 18 >= 15

        recommender = ModelRecommender(tracker)
        best = recommender.suggest("n1")
        assert best is not None
        # Claude has higher quality (0.95 vs 0.7), lower latency — should win
        assert best == "claude-3"

    def test_suggest_prefers_cheaper_when_quality_equal(self, kernel):
        tracker = self._seed_records(kernel, "n1", "model-a", 8, 0.8, 0.05, 500.0)
        self._seed_records(kernel, "n1", "model-b", 8, 0.8, 0.01, 500.0)

        recommender = ModelRecommender(tracker)
        best = recommender.suggest("n1")
        # Same quality & latency, model-b is cheaper
        assert best == "model-b"

    def test_persists_recommendation_as_preference(self, kernel):
        tracker = self._seed_records(kernel, "n1", "gpt-4o", 15, 0.9, 0.01, 500.0)
        recommender = ModelRecommender(tracker)
        recommender.suggest("n1")

        prefs = kernel.list_by_type(MemoryType.PREFERENCE, scope=MemoryScope.WORKFLOW)
        assert len(prefs) == 1
        assert prefs[0].metadata["model"] == "gpt-4o"
        assert prefs[0].metadata["node_id"] == "n1"

    def test_updates_existing_recommendation(self, kernel):
        tracker = self._seed_records(kernel, "n1", "gpt-4o", 15, 0.9, 0.01, 500.0)
        recommender = ModelRecommender(tracker)
        recommender.suggest("n1")

        # Add more records favoring a new model
        self._seed_records(kernel, "n1", "claude-3", 10, 0.99, 0.005, 300.0)
        recommender.suggest("n1")

        prefs = kernel.list_by_type(MemoryType.PREFERENCE, scope=MemoryScope.WORKFLOW)
        model_prefs = [p for p in prefs if p.metadata.get("tracker") == "model_recommendation"]
        assert len(model_prefs) == 1  # updated, not duplicated
        assert model_prefs[0].metadata["model"] == "claude-3"

    def test_get_recommendation_retrieves_persisted(self, kernel):
        tracker = self._seed_records(kernel, "n1", "gpt-4o", 15, 0.9, 0.01, 500.0)
        recommender = ModelRecommender(tracker)
        recommender.suggest("n1")

        rec = recommender.get_recommendation("n1")
        assert rec is not None
        assert rec["model"] == "gpt-4o"

    def test_get_recommendation_returns_none_when_empty(self, kernel):
        tracker = ModelOutcomeTracker(kernel)
        recommender = ModelRecommender(tracker)
        assert recommender.get_recommendation("n1") is None


# ===================================================================
# TopologyOutcomeTracker (10-1, 10-2)
# ===================================================================


class TestTopologyOutcomeTracker:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("DAN_TOPOLOGY_LEARNING", "1")

    def test_record_stores_episode(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        item = tracker.record(
            topology_signature="llm:1|output:1|llm->output",
            outcome=True,
        )
        assert item is not None
        assert item.memory_type == MemoryType.EPISODE
        assert item.metadata["topology_signature"] == "llm:1|output:1|llm->output"
        assert item.metadata["outcome"] is True
        assert item.metadata["failure_node"] is None

    def test_record_with_failure(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        item = tracker.record(
            topology_signature="sig-abc",
            outcome=False,
            failure_node="llm-node",
            failure_type="validation",
        )
        assert item.metadata["failure_node"] == "llm-node"
        assert item.metadata["failure_type"] == "validation"

    def test_get_records_all(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        tracker.record("sig-a", True)
        tracker.record("sig-b", False, "n1", "timeout")
        tracker.record("sig-a", True)

        records = tracker.get_records()
        assert len(records) == 3

    def test_get_records_by_signature(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        tracker.record("sig-a", True)
        tracker.record("sig-b", False)
        tracker.record("sig-a", False, "n1", "error")

        records = tracker.get_records(topology_signature="sig-a")
        assert len(records) == 2
        assert all(r["topology_signature"] == "sig-a" for r in records)

    def test_compute_signature_deterministic(self):
        graph = {
            "nodes": [
                {"id": "n1", "node_type": "input"},
                {"id": "n2", "node_type": "llm"},
            ],
            "edges": [
                {"source_node_id": "n1", "target_node_id": "n2"},
            ],
        }
        sig1 = TopologyOutcomeTracker.compute_signature(graph)
        sig2 = TopologyOutcomeTracker.compute_signature(graph)
        assert sig1 == sig2
        assert len(sig1) > 0

    def test_compute_signature_differs_for_different_graphs(self):
        graph_a = {
            "nodes": [
                {"id": "n1", "node_type": "input"},
                {"id": "n2", "node_type": "llm"},
            ],
            "edges": [{"source_node_id": "n1", "target_node_id": "n2"}],
        }
        graph_b = {
            "nodes": [
                {"id": "n1", "node_type": "input"},
                {"id": "n2", "node_type": "output"},
            ],
            "edges": [{"source_node_id": "n1", "target_node_id": "n2"}],
        }
        assert TopologyOutcomeTracker.compute_signature(graph_a) != TopologyOutcomeTracker.compute_signature(graph_b)


# ===================================================================
# TopologyAdvisor (10-3..10-6)
# ===================================================================


class TestTopologyAdvisor:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("DAN_TOPOLOGY_LEARNING", "1")

    def _build_graph(self):
        return {
            "nodes": [
                {"id": "input-1", "node_type": "input"},
                {"id": "llm-1", "node_type": "llm"},
                {"id": "output-1", "node_type": "output"},
            ],
            "edges": [
                {"source_node_id": "input-1", "target_node_id": "llm-1"},
                {"source_node_id": "llm-1", "target_node_id": "output-1"},
            ],
        }

    def test_no_suggestion_insufficient_data(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        tracker.record("sig-a", True)  # only 1 record
        advisor = TopologyAdvisor(tracker)
        assert advisor.suggest(self._build_graph()) == []

    def test_suggest_adds_validator(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        # 6 records with llm-1 failing often (> 30% failure rate)
        for _ in range(3):
            tracker.record("sig-a", False, failure_node="llm-1", failure_type="validation")
        for _ in range(3):
            tracker.record("sig-a", True, failure_node="llm-1")

        advisor = TopologyAdvisor(tracker)
        suggestions = advisor.suggest(self._build_graph())
        assert len(suggestions) >= 1
        assert any("validator" in s.lower() for s in suggestions)

    def test_suggest_timeout_wrapper(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        for _ in range(5):
            tracker.record("sig-x", False, failure_node="llm-1", failure_type="timeout")
        for _ in range(2):
            tracker.record("sig-x", True, failure_node="llm-1")

        advisor = TopologyAdvisor(tracker)
        suggestions = advisor.suggest(self._build_graph())
        assert any("timeout" in s.lower() for s in suggestions)

    def test_no_validator_suggestion_when_validator_exists(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        for _ in range(5):
            tracker.record("sig-a", False, failure_node="llm-1", failure_type="validation")
        for _ in range(2):
            tracker.record("sig-a", True, failure_node="llm-1")

        graph = {
            "nodes": [
                {"id": "input-1", "node_type": "input"},
                {"id": "llm-1", "node_type": "llm"},
                {"id": "val-1", "node_type": "validator"},
                {"id": "output-1", "node_type": "output"},
            ],
            "edges": [
                {"source_node_id": "input-1", "target_node_id": "llm-1"},
                {"source_node_id": "llm-1", "target_node_id": "val-1"},
                {"source_node_id": "val-1", "target_node_id": "output-1"},
            ],
        }

        advisor = TopologyAdvisor(tracker)
        suggestions = advisor.suggest(graph)
        validator_suggestions = [s for s in suggestions if "validator" in s.lower() and "llm-1" in s]
        assert len(validator_suggestions) == 0

    def test_low_success_rate_topology_suggestion(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        graph = self._build_graph()
        sig = TopologyOutcomeTracker.compute_signature(graph)

        for _ in range(4):
            tracker.record(sig, False, failure_node="llm-1", failure_type="error")
        tracker.record(sig, True, failure_node="llm-1")

        advisor = TopologyAdvisor(tracker)
        suggestions = advisor.suggest(graph)
        assert any("success rate" in s.lower() for s in suggestions)

    def test_record_suggestion_outcome(self, kernel):
        tracker = TopologyOutcomeTracker(kernel)
        advisor = TopologyAdvisor(tracker)
        item = advisor.record_suggestion_outcome(
            suggestion="Add validator after LLM node",
            accepted=True,
            outcome_improved=True,
        )
        assert item is not None
        assert item.metadata["accepted"] is True
        assert item.metadata["outcome_improved"] is True

    def test_record_suggestion_outcome_disabled(self, kernel, monkeypatch):
        monkeypatch.delenv("DAN_TOPOLOGY_LEARNING", raising=False)
        tracker = TopologyOutcomeTracker(kernel)
        advisor = TopologyAdvisor(tracker)
        assert advisor.record_suggestion_outcome("test", False) is None


# ===================================================================
# Integration: override hierarchy (8-6)
# ===================================================================


class TestOverrideHierarchy:
    """Verify the override hierarchy concept: explicit > empirical > TierPolicy > default.

    The actual override application happens at the TierPolicy/engine level.
    Here we verify the recommender correctly stores and retrieves recommendations
    that can be used in that hierarchy.
    """

    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("DAN_MODEL_LEARNING", "1")

    def test_recommendation_stored_as_workflow_preference(self, kernel):
        tracker = ModelOutcomeTracker(kernel)
        for _ in range(15):
            tracker.record("n1", "llm", "task", "gpt-4o", 0.9, 0.01, 500.0)

        recommender = ModelRecommender(tracker)
        best = recommender.suggest("n1")

        assert best == "gpt-4o"
        prefs = kernel.list_by_type(MemoryType.PREFERENCE, scope=MemoryScope.WORKFLOW)
        assert len(prefs) == 1
        assert prefs[0].scope == MemoryScope.WORKFLOW
        assert prefs[0].lifecycle.value == "durable"
