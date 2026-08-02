"""Tests for Plan 14-3 — Long-Chain Memory Pipeline.

Covers:
  - MemoryItem token estimation
  - ShortTermMemory buffer (append, compaction, snapshot/restore)
  - CompactionStrategy activation (sliding_window, keep_last, diff_based, none)
  - apply_compaction standalone function
  - ConsolidationPipeline (should_consolidate, consolidate)
  - ExecutionContext.remember() / recall() integration
  - estimate_tokens utility
"""

from __future__ import annotations

from typing import Any

import pytest

from dan.models.context import CompactionStrategy
from dan.engine.memory_pipeline import (
    ConsolidationPipeline,
    MemoryItem,
    MemoryPolicyConfig,
    ShortTermMemory,
    apply_compaction,
)
from dan.utils.tokens import estimate_tokens


# =====================================================================
# Token utility tests
# =====================================================================


class TestEstimateTokens:
    def test_non_empty_string(self):
        count = estimate_tokens("Hello world, this is a test")
        assert count > 0

    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_approximation(self):
        text = "a" * 400
        count = estimate_tokens(text)
        assert 50 <= count <= 500


# =====================================================================
# MemoryItem tests
# =====================================================================


class TestMemoryItem:
    def test_auto_token_count(self):
        item = MemoryItem(content="hello world this is a test")
        assert item.token_count > 0

    def test_explicit_token_count(self):
        item = MemoryItem(content="hello", token_count=42)
        assert item.token_count == 42

    def test_empty_content(self):
        item = MemoryItem(content="")
        assert item.token_count == 0

    def test_metadata(self):
        item = MemoryItem(
            content="test", metadata={"key": "val"},
            source_node_id="n1", source_run_id="r1",
        )
        assert item.metadata == {"key": "val"}
        assert item.source_node_id == "n1"


# =====================================================================
# ShortTermMemory tests
# =====================================================================


class TestShortTermMemory:
    def test_append_and_count(self):
        stm = ShortTermMemory()
        stm.append(MemoryItem(content="a", token_count=10))
        stm.append(MemoryItem(content="b", token_count=20))
        assert stm.count == 2
        assert stm.total_tokens == 30

    def test_get_recent(self):
        stm = ShortTermMemory()
        for i in range(5):
            stm.append(MemoryItem(content=str(i), token_count=10))
        recent = stm.get_recent(3)
        assert len(recent) == 3
        assert recent[0].content == "2"
        assert recent[2].content == "4"

    def test_to_text(self):
        stm = ShortTermMemory()
        stm.append(MemoryItem(content="hello", token_count=5))
        stm.append(MemoryItem(content="world", token_count=5))
        assert stm.to_text() == "hello\nworld"
        assert stm.to_text(1) == "world"

    def test_clear(self):
        stm = ShortTermMemory()
        stm.append(MemoryItem(content="x", token_count=10))
        stm.clear()
        assert stm.count == 0
        assert stm.total_tokens == 0

    def test_sliding_window_compaction(self):
        config = MemoryPolicyConfig(
            strategy=CompactionStrategy.SLIDING_WINDOW,
            max_tokens=50,
        )
        stm = ShortTermMemory(config)
        for i in range(10):
            stm.append(MemoryItem(content=f"item_{i}", token_count=10))
        assert stm.total_tokens <= 50
        assert stm.count == 5

    def test_keep_last_compaction(self):
        config = MemoryPolicyConfig(
            strategy=CompactionStrategy.KEEP_LAST,
            window_size=3,
            max_tokens=10,
        )
        stm = ShortTermMemory(config)
        for i in range(10):
            stm.append(MemoryItem(content=f"item_{i}", token_count=5))
        assert stm.count == 3
        assert stm.items[-1].content == "item_9"

    def test_diff_based_compaction(self):
        config = MemoryPolicyConfig(
            strategy=CompactionStrategy.DIFF_BASED,
            max_tokens=25,
        )
        stm = ShortTermMemory(config)
        for i in range(10):
            stm.append(MemoryItem(content=f"item_{i}", token_count=10))
        assert stm.count == 3
        assert "compacted" in stm.items[1].content

    def test_none_strategy_no_eviction(self):
        config = MemoryPolicyConfig(
            strategy=CompactionStrategy.NONE,
            max_tokens=10,
        )
        stm = ShortTermMemory(config)
        for i in range(5):
            stm.append(MemoryItem(content=f"item_{i}", token_count=10))
        assert stm.count == 5
        assert stm.total_tokens == 50

    def test_snapshot_and_restore(self):
        stm = ShortTermMemory()
        stm.append(MemoryItem(content="a", token_count=10, source_node_id="n1"))
        stm.append(MemoryItem(content="b", token_count=20, source_run_id="r1"))
        snap = stm.snapshot()
        assert len(snap) == 2

        stm2 = ShortTermMemory()
        stm2.restore(snap)
        assert stm2.count == 2
        assert stm2.total_tokens == 30
        assert stm2.items[0].source_node_id == "n1"


# =====================================================================
# apply_compaction standalone tests
# =====================================================================


class TestApplyCompaction:
    @staticmethod
    def _items(n: int, tokens_each: int = 10) -> list[MemoryItem]:
        return [
            MemoryItem(content=f"item_{i}", token_count=tokens_each)
            for i in range(n)
        ]

    def test_sliding_window(self):
        items = self._items(10)
        result = apply_compaction(
            items, CompactionStrategy.SLIDING_WINDOW, max_tokens=50,
        )
        total = sum(i.token_count for i in result)
        assert total <= 50

    def test_keep_last(self):
        items = self._items(10)
        result = apply_compaction(
            items, CompactionStrategy.KEEP_LAST, window_size=3,
        )
        assert len(result) == 3
        assert result[-1].content == "item_9"

    def test_diff_based(self):
        items = self._items(10)
        result = apply_compaction(items, CompactionStrategy.DIFF_BASED)
        assert len(result) == 3
        assert "compacted" in result[1].content
        assert result[1].metadata["compacted_count"] == 8

    def test_none_passthrough(self):
        items = self._items(5)
        result = apply_compaction(items, CompactionStrategy.NONE)
        assert len(result) == 5

    def test_summarize_falls_back_to_sliding_window(self):
        items = self._items(10)
        result = apply_compaction(
            items, CompactionStrategy.SUMMARIZE, max_tokens=50,
        )
        total = sum(i.token_count for i in result)
        assert total <= 50


# =====================================================================
# ConsolidationPipeline tests
# =====================================================================


class TestConsolidationPipeline:
    def test_should_not_consolidate_below_threshold(self):
        config = MemoryPolicyConfig(consolidation_threshold=10)
        pipeline = ConsolidationPipeline(config)
        stm = ShortTermMemory(config)
        for i in range(5):
            stm.append(MemoryItem(content=f"item_{i}", token_count=1))
        assert pipeline.should_consolidate(stm) is False

    def test_should_consolidate_at_threshold(self):
        config = MemoryPolicyConfig(consolidation_threshold=5)
        pipeline = ConsolidationPipeline(config)
        stm = ShortTermMemory(MemoryPolicyConfig(
            strategy=CompactionStrategy.NONE, max_tokens=999999,
        ))
        for i in range(5):
            stm.append(MemoryItem(content=f"item_{i}", token_count=1))
        assert pipeline.should_consolidate(stm) is True

    def test_consolidation_keeps_recent(self):
        config = MemoryPolicyConfig(
            consolidation_threshold=5,
            window_size=3,
        )
        pipeline = ConsolidationPipeline(config)
        stm = ShortTermMemory(MemoryPolicyConfig(
            strategy=CompactionStrategy.NONE, max_tokens=999999,
        ))
        for i in range(10):
            stm.append(MemoryItem(content=f"item_{i}", token_count=5))

        result = pipeline.consolidate(stm)
        assert result.items_consolidated == 7
        assert result.items_remaining == 3
        assert len(result.long_term_entries) > 0
        assert stm.count == 3
        assert stm.items[0].content == "item_7"

    def test_consolidation_disabled(self):
        config = MemoryPolicyConfig(consolidation_enabled=False)
        pipeline = ConsolidationPipeline(config)
        stm = ShortTermMemory()
        for i in range(100):
            stm.append(MemoryItem(content=f"item_{i}", token_count=1))
        assert pipeline.should_consolidate(stm) is False


# =====================================================================
# ExecutionContext integration
# =====================================================================


class TestContextMemoryPipeline:
    def test_remember_and_recall(self):
        from dan.engine.executor import ExecutionContext, EngineConfig
        from dan.engine.state import ExecutionState
        from dan.engine.context_runtime import (
            ArtifactStore, LocalStateManager, SharedContextStore,
        )
        from dan.models.graph import Graph

        graph = Graph(nodes=[], edges=[])
        state = ExecutionState(graph)
        stm = ShortTermMemory()

        ctx = ExecutionContext(
            state=state,
            config=EngineConfig(llm_api_key="test"),
            shared_context=SharedContextStore(),
            artifacts=ArtifactStore(),
            local_state=LocalStateManager(),
            short_term_memory=stm,
        )

        ctx.remember("first observation", source_node_id="n1")
        ctx.remember("second observation", source_node_id="n2")

        text = ctx.recall()
        assert "first observation" in text
        assert "second observation" in text

        assert ctx.recall(1) == "second observation"

    def test_remember_noop_without_pipeline(self):
        from dan.engine.executor import ExecutionContext, EngineConfig
        from dan.engine.state import ExecutionState
        from dan.engine.context_runtime import (
            ArtifactStore, LocalStateManager, SharedContextStore,
        )
        from dan.models.graph import Graph

        graph = Graph(nodes=[], edges=[])
        state = ExecutionState(graph)

        ctx = ExecutionContext(
            state=state,
            config=EngineConfig(llm_api_key="test"),
            shared_context=SharedContextStore(),
            artifacts=ArtifactStore(),
            local_state=LocalStateManager(),
        )

        ctx.remember("something")
        assert ctx.recall() == ""
