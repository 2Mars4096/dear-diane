"""Tests for LoopCompactor and loop compaction wiring (18-3 tasks 3-3..3-5)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from dan.engine.memory_pipeline import MemoryItem, MemoryPolicyConfig, ShortTermMemory
from dan.engine.token_optimization import LoopCompactor, TokenBudgetAdvisor
from dan.models.context import CompactionRule, CompactionStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payloads(n: int) -> list[dict[str, Any]]:
    return [{"iteration": i, "result": f"output_{i}", "score": i * 0.1} for i in range(n)]


class FakeStateStore:
    """Minimal async state store for testing."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, Any]] = []

    async def write(self, scope: str, key: str, value: Any) -> None:
        self.writes.append((scope, key, value))

    async def read(self, scope: str, key: str) -> Any | None:
        for s, k, v in reversed(self.writes):
            if s == scope and k == key:
                return v
        return None


# ===========================================================================
# CompactionStrategy.NONE
# ===========================================================================


class TestNoneStrategy:
    def test_none_returns_all(self):
        rule = CompactionRule(strategy=CompactionStrategy.NONE)
        compactor = LoopCompactor(rule)
        payloads = _payloads(5)
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=4)
        )
        assert compacted == payloads
        assert meta["strategy"] == "none"
        assert meta["items_persisted"] == 0


# ===========================================================================
# CompactionStrategy.SLIDING_WINDOW
# ===========================================================================


class TestSlidingWindowStrategy:
    def test_keeps_last_n(self):
        rule = CompactionRule(
            strategy=CompactionStrategy.SLIDING_WINDOW,
            window_size=3,
            require_persistent_recall=False,
        )
        compactor = LoopCompactor(rule)
        payloads = _payloads(7)
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=6)
        )
        assert len(compacted) == 3
        assert compacted[0]["iteration"] == 4
        assert meta["strategy"] == "sliding_window"

    def test_persists_evicted_to_memory(self):
        stm = ShortTermMemory(MemoryPolicyConfig(max_tokens=100_000))
        rule = CompactionRule(
            strategy=CompactionStrategy.SLIDING_WINDOW,
            window_size=2,
        )
        compactor = LoopCompactor(rule, memory=stm)
        payloads = _payloads(5)
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="loop1", iteration=4)
        )
        assert len(compacted) == 2
        assert meta["items_persisted"] == 3
        assert stm.count == 3

    def test_fallback_to_none_without_memory(self):
        """When require_persistent_recall=True but no memory/state, fall back to none."""
        rule = CompactionRule(
            strategy=CompactionStrategy.SLIDING_WINDOW,
            window_size=2,
            require_persistent_recall=True,
        )
        compactor = LoopCompactor(rule)
        payloads = _payloads(5)
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=4)
        )
        assert len(compacted) == 5
        assert meta["strategy"] == "none"

    def test_no_fallback_when_recall_false(self):
        """When require_persistent_recall=False, compaction proceeds without memory."""
        rule = CompactionRule(
            strategy=CompactionStrategy.SLIDING_WINDOW,
            window_size=2,
            require_persistent_recall=False,
        )
        compactor = LoopCompactor(rule)
        payloads = _payloads(5)
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=4)
        )
        assert len(compacted) == 2
        assert meta["strategy"] == "sliding_window"


# ===========================================================================
# CompactionStrategy.KEEP_LAST
# ===========================================================================


class TestKeepLastStrategy:
    def test_keeps_only_last(self):
        rule = CompactionRule(
            strategy=CompactionStrategy.KEEP_LAST,
            require_persistent_recall=False,
        )
        compactor = LoopCompactor(rule)
        payloads = _payloads(5)
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=4)
        )
        assert len(compacted) == 1
        assert compacted[0]["iteration"] == 4
        assert meta["strategy"] == "keep_last"

    def test_keep_last_persists_evicted(self):
        stm = ShortTermMemory(MemoryPolicyConfig(max_tokens=100_000))
        rule = CompactionRule(strategy=CompactionStrategy.KEEP_LAST)
        compactor = LoopCompactor(rule, memory=stm)
        payloads = _payloads(4)
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=3)
        )
        assert len(compacted) == 1
        assert meta["items_persisted"] == 3
        assert stm.count == 3

    def test_keep_last_fallback_without_memory(self):
        rule = CompactionRule(
            strategy=CompactionStrategy.KEEP_LAST,
            require_persistent_recall=True,
        )
        compactor = LoopCompactor(rule)
        payloads = _payloads(3)
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=2)
        )
        assert len(compacted) == 3
        assert meta["strategy"] == "none"


# ===========================================================================
# CompactionStrategy.SUMMARIZE
# ===========================================================================


class TestSummarizeStrategy:
    def test_summarize_cadence_default_5(self):
        """Summarize triggers when (iteration+1) % 5 == 0 (i.e. iteration 4, 9, 14...)."""
        rule = CompactionRule(strategy=CompactionStrategy.SUMMARIZE)
        compactor = LoopCompactor(rule)
        payloads = _payloads(5)

        # iteration=3: not a summarize point
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=3)
        )
        assert len(compacted) == 5

        # iteration=4: triggers summarize ((4+1)%5 == 0)
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=4)
        )
        assert len(compacted) == 2
        assert compacted[0]["__summary__"] is True
        assert "keys=" in compacted[0]["description"]
        assert compacted[1] == payloads[-1]

    def test_summarize_custom_cadence(self):
        rule = CompactionRule(
            strategy=CompactionStrategy.SUMMARIZE,
            summarize_every_n=3,
        )
        compactor = LoopCompactor(rule)
        payloads = _payloads(4)

        # iteration=2: triggers ((2+1)%3 == 0)
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=2)
        )
        assert len(compacted) == 2
        assert compacted[0]["__summary__"] is True
        assert compacted[0]["covered_iterations"] == 3

    def test_summarize_structural_content(self):
        rule = CompactionRule(
            strategy=CompactionStrategy.SUMMARIZE,
            summarize_every_n=2,
        )
        compactor = LoopCompactor(rule)
        payloads = [
            {"draft": "v1", "score": 0.5},
            {"draft": "v2", "score": 0.7},
            {"draft": "v3", "score": 0.9},
        ]
        compacted, _ = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=1)
        )
        summary = compacted[0]
        assert summary["__summary__"] is True
        assert "draft" in summary["keys"]
        assert "score" in summary["keys"]

    def test_summarize_single_item_noop(self):
        rule = CompactionRule(
            strategy=CompactionStrategy.SUMMARIZE,
            summarize_every_n=1,
        )
        compactor = LoopCompactor(rule)
        payloads = _payloads(1)
        compacted, _ = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=0)
        )
        assert compacted == payloads


# ===========================================================================
# CompactionStrategy.DIFF_BASED
# ===========================================================================


class TestDiffBasedStrategy:
    def test_diff_keeps_deltas(self):
        rule = CompactionRule(strategy=CompactionStrategy.DIFF_BASED)
        compactor = LoopCompactor(rule)
        payloads = [
            {"draft": "v1", "score": 0.5, "metadata": "same"},
            {"draft": "v2", "score": 0.7, "metadata": "same"},
            {"draft": "v3", "score": 0.9, "metadata": "same"},
        ]
        compacted, meta = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=2)
        )
        assert len(compacted) == 3
        assert compacted[0] == payloads[0]
        assert "draft" in compacted[1]
        assert "score" in compacted[1]
        assert "metadata" not in compacted[1]

    def test_diff_single_item(self):
        rule = CompactionRule(strategy=CompactionStrategy.DIFF_BASED)
        compactor = LoopCompactor(rule)
        payloads = _payloads(1)
        compacted, _ = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=0)
        )
        assert compacted == payloads

    def test_diff_unchanged_marker(self):
        rule = CompactionRule(strategy=CompactionStrategy.DIFF_BASED)
        compactor = LoopCompactor(rule)
        payloads = [{"x": 1}, {"x": 1}]
        compacted, _ = asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=1)
        )
        assert compacted[1].get("__unchanged__") is True


# ===========================================================================
# persist_evicted
# ===========================================================================


class TestPersistEvicted:
    def test_persist_to_memory(self):
        stm = ShortTermMemory(MemoryPolicyConfig(max_tokens=100_000))
        rule = CompactionRule(strategy=CompactionStrategy.NONE)
        compactor = LoopCompactor(rule, memory=stm)
        evicted = _payloads(3)
        count = compactor.persist_evicted(evicted, "node1", "run1")
        assert count == 3
        assert stm.count == 3
        for item in stm.items:
            assert item.source_node_id == "node1"
            assert item.source_run_id == "run1"

    def test_persist_no_memory_returns_zero(self):
        rule = CompactionRule(strategy=CompactionStrategy.NONE)
        compactor = LoopCompactor(rule)
        count = compactor.persist_evicted(_payloads(3), "node1")
        assert count == 0


# ===========================================================================
# State store integration
# ===========================================================================


class TestStateStoreIntegration:
    def test_writes_compaction_metadata(self):
        store = FakeStateStore()
        stm = ShortTermMemory(MemoryPolicyConfig(max_tokens=100_000))
        rule = CompactionRule(
            strategy=CompactionStrategy.SLIDING_WINDOW,
            window_size=2,
        )
        compactor = LoopCompactor(rule, memory=stm, state_store=store)
        payloads = _payloads(5)
        asyncio.run(
            compactor.compact(payloads, loop_node_id="n1", iteration=4)
        )
        assert len(store.writes) == 1
        scope, key, value = store.writes[0]
        assert scope == "n1"
        assert "iter_4" in key
        assert value["strategy"] == "sliding_window"


# ===========================================================================
# TokenBudgetAdvisor cross-source budget
# ===========================================================================


class TestTokenBudgetAdvisorCrossSource:
    def test_estimate_source_tokens_with_system_prompt(self):
        advisor = TokenBudgetAdvisor(total_budget=10000)

        class FakeNode:
            system_prompt = "You are a helpful assistant."
            prompt_template = "Answer: {question}"

        estimates = advisor.estimate_source_tokens(FakeNode(), None)
        assert estimates["system_prompt"] > 0
        assert estimates["edge_data"] > 0
        assert estimates["memory_retrieval"] == 0

    def test_estimate_with_memory(self):
        advisor = TokenBudgetAdvisor(total_budget=10000)
        stm = ShortTermMemory(MemoryPolicyConfig(max_tokens=100_000))
        stm.append(MemoryItem(content="test memory content " * 20))

        class FakeNode:
            system_prompt = ""
            prompt_template = ""

        class FakeContext:
            short_term_memory = stm
            hyperedge_resolver = None

        estimates = advisor.estimate_source_tokens(FakeNode(), FakeContext())
        assert estimates["memory_retrieval"] > 0

    def test_compute_variable_budget(self):
        advisor = TokenBudgetAdvisor(total_budget=10000)

        class FakeNode:
            system_prompt = "System prompt text " * 10
            prompt_template = "Template {x}"

        result = advisor.compute_variable_budget(FakeNode(), None, node_id="n1")
        assert "source_estimates" in result
        assert "fixed_tokens" in result
        assert "variable_budget" in result
        assert result["variable_budget"] <= 10000
