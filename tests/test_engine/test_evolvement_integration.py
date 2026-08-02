"""Integration tests for the self-evolvement loop (29-6 §12-9 through §12-12).

These tests exercise full feedback cycles end-to-end using real MemoryKernel
instances backed by tmp_path. No LLM keys required.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from dan.engine.memory_kernel import (
    MemoryItem,
    MemoryKernel,
    MemoryLifecycle,
    MemoryScope,
    MemoryType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockGraphStore:
    """Minimal graph store that PatternExtractor can consume."""

    def __init__(self, graphs: dict[str, dict[str, Any]]) -> None:
        self._graphs = graphs

    def list_graphs(self) -> list[dict[str, str]]:
        return [{"graph_id": gid} for gid in self._graphs]

    def get_graph(self, graph_id: str) -> dict[str, Any]:
        return self._graphs[graph_id]


def _linear_graph(name: str) -> dict[str, Any]:
    return {
        "metadata": {"name": name},
        "nodes": [
            {"id": "input", "node_type": "input"},
            {"id": "process", "node_type": "llm_operator"},
            {"id": "output", "node_type": "llm_operator"},
        ],
        "edges": [
            {"source_node_id": "input", "target_node_id": "process"},
            {"source_node_id": "process", "target_node_id": "output"},
        ],
    }


# ---------------------------------------------------------------------------
# 12-9: Pattern extraction and reuse cycle
# ---------------------------------------------------------------------------


class TestPatternReuseCycle:
    def test_pattern_extraction_and_reuse_cycle(self, tmp_path):
        """5 similar workflows built -> pattern extracted -> 6th build reuses pattern."""
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        graphs: dict[str, dict[str, Any]] = {}
        for i in range(5):
            graphs[f"wf-{i}"] = _linear_graph(f"analysis-{i}")
            kernel.store_workflow_asset(
                f"Workflow analysis-{i}",
                workflow_id=f"wf-{i}",
                success_rate=1.0,
            )

        from dan.engine.pattern_extractor import PatternExtractor

        store = MockGraphStore(graphs)
        extractor = PatternExtractor(kernel, store)
        assert extractor.should_run(threshold=5)

        patterns = extractor.extract_patterns()
        assert len(patterns) >= 1

        stored_patterns = kernel.list_by_type(MemoryType.WORKFLOW_PATTERN)
        assert len(stored_patterns) >= 1

        scored = kernel.retrieve_by_task(
            "build an analysis pipeline", task_type="workflow_build",
        )
        pattern_results = [
            s for s in scored
            if s.item.memory_type == MemoryType.WORKFLOW_PATTERN
        ]
        assert len(pattern_results) >= 1

    def test_pattern_linked_to_source_workflows(self, tmp_path):
        """Extracted patterns carry related_ids back to source workflows."""
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        graphs = {f"wf-{i}": _linear_graph(f"a-{i}") for i in range(4)}
        for gid in graphs:
            kernel.store_workflow_asset(f"asset {gid}", workflow_id=gid, success_rate=1.0)

        from dan.engine.pattern_extractor import PatternExtractor

        ext = PatternExtractor(kernel, MockGraphStore(graphs))
        patterns = ext.extract_patterns()
        assert len(patterns) >= 1
        assert len(patterns[0].related_ids) >= 2

    def test_consolidation_triggers_pattern_extraction(self, tmp_path):
        """MemoryKernel.run_consolidation with a graph_store runs PatternExtractor."""
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        graphs = {f"wf-{i}": _linear_graph(f"a-{i}") for i in range(12)}
        for gid in graphs:
            kernel.store_workflow_asset(f"asset {gid}", workflow_id=gid, success_rate=1.0)

        result = kernel.run_consolidation(graph_store=MockGraphStore(graphs))
        assert result["patterns_extracted"] >= 1


# ---------------------------------------------------------------------------
# 12-10: Preference extraction -> applied in next build -> user confirms
# ---------------------------------------------------------------------------


class TestPreferenceExtractionAndConfirmation:
    def test_preference_extraction_and_confirmation_cycle(self, tmp_path):
        """Preference extracted from conversation -> applied in next build -> user confirms."""
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        from dan.engine.memory_extractor import MemoryExtractor

        extractor = MemoryExtractor()
        candidates = extractor.extract("I prefer Claude for writing tasks", "OK, noted.")
        prefs = [c for c in candidates if c.memory_type == "preference"]
        assert len(prefs) >= 1

        for p in prefs:
            kernel.store_preference(p.content, tags=p.tags)

        scored = kernel.retrieve_by_task(
            "write a report", task_type="general_conversation",
        )
        pref_results = [
            s for s in scored
            if s.item.memory_type == MemoryType.PREFERENCE
        ]
        assert any(
            "claude" in s.item.content.lower() for s in pref_results
        )

        all_prefs = kernel.list_by_type(MemoryType.PREFERENCE)
        claude_pref = [p for p in all_prefs if "claude" in p.content.lower()][0]
        kernel.confirm_preference(claude_pref.id)

        confirmed = kernel.get(claude_pref.id)
        assert confirmed.importance >= 0.9
        assert confirmed.provenance.confirmed_by_user is True

    def test_rejected_preference_is_archived(self, tmp_path):
        """Rejected preferences get archived lifecycle."""
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        pref = kernel.store_preference("models: drafting -> gpt-4")
        kernel.reject_preference(pref.id)
        rejected = kernel.get(pref.id)
        assert rejected.lifecycle == MemoryLifecycle.ARCHIVE
        assert rejected.metadata.get("rejected_by_user") is True

    def test_confirmed_preference_outranks_inferred(self, tmp_path):
        """A confirmed preference cannot be superseded by an inferred one."""
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))
        pref = kernel.store_preference("models: drafting -> claude", confirmed=True)
        assert pref is not None

        inferred = kernel.store_preference("models: drafting -> gpt-4", confirmed=False)
        assert inferred is None  # should be skipped (confirmed wins)

        all_prefs = kernel.list_by_type(MemoryType.PREFERENCE)
        assert len(all_prefs) == 1
        assert "claude" in all_prefs[0].content.lower()


# ---------------------------------------------------------------------------
# 12-11: Prompt optimization cycle (mock — only PromptTracker, env-gated)
# ---------------------------------------------------------------------------


class TestPromptOptimizationCycle:
    @pytest.fixture(autouse=True)
    def _enable_prompt_opt(self, monkeypatch):
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "1")

    def test_prompt_tracking_cycle(self, tmp_path):
        """20 runs of same node -> tracker accumulates data -> history retrievable."""
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        from dan.engine.outcome_trackers import PromptTracker

        tracker = PromptTracker(kernel)

        for i in range(20):
            tracker.record(
                node_id="writer",
                prompt_hash=f"hash_{i % 2}",
                input_summary="write about topic",
                output_summary=f"output {i}",
                outcome=(i % 3 != 0),
                tokens_used=100 + i,
                latency_ms=500.0 + i * 10,
            )

        history = tracker.get_history("writer", limit=25)
        assert len(history) == 20

        success_count = sum(1 for h in history if h["outcome"])
        failure_count = len(history) - success_count
        assert success_count > 0
        assert failure_count > 0

    def test_prompt_tracker_respects_env_gate(self, tmp_path, monkeypatch):
        """When DAN_PROMPT_OPTIMIZATION is off, record() returns None."""
        monkeypatch.setenv("DAN_PROMPT_OPTIMIZATION", "0")
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        from dan.engine.outcome_trackers import PromptTracker

        tracker = PromptTracker(kernel)
        result = tracker.record(
            node_id="writer",
            prompt_hash="h1",
            input_summary="x",
            output_summary="y",
            outcome=True,
            tokens_used=100,
            latency_ms=500.0,
        )
        assert result is None
        assert tracker.get_history("writer") == []

    def test_prompt_history_scoped_to_node(self, tmp_path):
        """History for node A doesn't include node B entries."""
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        from dan.engine.outcome_trackers import PromptTracker

        tracker = PromptTracker(kernel)
        tracker.record("node-a", "h1", "in", "out", True, 100, 500.0)
        tracker.record("node-b", "h2", "in", "out", True, 100, 500.0)

        history_a = tracker.get_history("node-a")
        assert len(history_a) == 1
        assert history_a[0]["node_id"] == "node-a"


# ---------------------------------------------------------------------------
# 12-12: Model recommendation cycle
# ---------------------------------------------------------------------------


class TestModelRecommendationCycle:
    @pytest.fixture(autouse=True)
    def _enable_model_learning(self, monkeypatch):
        monkeypatch.setenv("DAN_MODEL_LEARNING", "1")

    def test_model_recommendation_cycle(self, tmp_path):
        """Model recommendation picks the better model after sufficient evidence."""
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        from dan.engine.outcome_trackers import ModelOutcomeTracker, ModelRecommender

        tracker = ModelOutcomeTracker(kernel)
        recommender = ModelRecommender(tracker)

        for _ in range(15):
            tracker.record(
                "node-1", "llm_operator", "write report",
                "claude-sonnet", 0.9, 0.01, 500.0,
            )
            tracker.record(
                "node-1", "llm_operator", "write report",
                "gpt-4o-mini", 0.6, 0.005, 300.0,
            )

        suggestion = recommender.suggest("node-1")
        assert suggestion is not None
        assert "claude" in suggestion.lower()

    def test_insufficient_data_returns_none(self, tmp_path):
        """With fewer than 15 records, recommend nothing."""
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        from dan.engine.outcome_trackers import ModelOutcomeTracker, ModelRecommender

        tracker = ModelOutcomeTracker(kernel)
        recommender = ModelRecommender(tracker)

        for _ in range(5):
            tracker.record(
                "node-1", "llm_operator", "task",
                "claude-sonnet", 0.9, 0.01, 500.0,
            )

        assert recommender.suggest("node-1") is None

    def test_recommendation_persisted_as_preference(self, tmp_path):
        """After suggesting, recommendation is stored as a PREFERENCE item."""
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        from dan.engine.outcome_trackers import ModelOutcomeTracker, ModelRecommender

        tracker = ModelOutcomeTracker(kernel)
        recommender = ModelRecommender(tracker)

        for _ in range(15):
            tracker.record(
                "node-1", "llm_operator", "write report",
                "claude-sonnet", 0.9, 0.01, 500.0,
            )
            tracker.record(
                "node-1", "llm_operator", "write report",
                "gpt-4o-mini", 0.6, 0.005, 300.0,
            )

        recommender.suggest("node-1")

        rec = recommender.get_recommendation("node-1")
        assert rec is not None
        assert rec["model"] == "claude-sonnet"

    def test_env_gate_disables_recommendation(self, tmp_path, monkeypatch):
        """When DAN_MODEL_LEARNING is off, suggest() returns None."""
        monkeypatch.setenv("DAN_MODEL_LEARNING", "0")
        kernel = MemoryKernel(base_dir=str(tmp_path / "mem"))

        from dan.engine.outcome_trackers import ModelOutcomeTracker, ModelRecommender

        tracker = ModelOutcomeTracker(kernel)
        recommender = ModelRecommender(tracker)

        for _ in range(20):
            tracker.record(
                "node-1", "llm_operator", "task",
                "claude-sonnet", 0.9, 0.01, 500.0,
            )

        assert recommender.suggest("node-1") is None
