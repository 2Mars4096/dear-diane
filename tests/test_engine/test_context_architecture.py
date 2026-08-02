"""Tests for 18-3 context architecture helpers."""

from __future__ import annotations

import pytest

from dan.engine.token_optimization import HistoryManager, LoopCompactor, TokenBudgetAdvisor
from dan.models.context import CompactionRule, CompactionStrategy
from dan.models.nodes import HistoryPolicy


class TestHistoryManager:
    def test_assemble_history_keeps_recent_and_summary(self):
        mgr = HistoryManager()
        policy = HistoryPolicy(inline_recent=2, summarize_older=True, keep_system=True)
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        assembled, older = mgr.assemble_history(messages, policy, context_tools_available=True)
        assert older
        assert assembled[0]["role"] == "system"
        assert any("History summary" in m.get("content", "") for m in assembled if m["role"] == "system")
        assert any(m.get("content") == "a2" for m in assembled)


class TestLoopCompactor:
    @pytest.mark.asyncio
    async def test_sliding_window_compaction(self):
        class _Mem:
            def __init__(self):
                self.items = []

            def append(self, item):
                self.items.append(item)

        rule = CompactionRule(strategy=CompactionStrategy.SLIDING_WINDOW, window_size=2)
        compactor = LoopCompactor(rule, memory=_Mem())  # persistent recall available
        payloads = [{"i": 1}, {"i": 2}, {"i": 3}]
        compacted, metrics = await compactor.compact(payloads, loop_node_id="loop1", iteration=3)
        assert len(compacted) == 2
        assert compacted[-1]["i"] == 3
        assert metrics["strategy"] == "sliding_window"

    @pytest.mark.asyncio
    async def test_diff_based_compaction(self):
        rule = CompactionRule(strategy=CompactionStrategy.DIFF_BASED)
        compactor = LoopCompactor(rule)
        payloads = [{"a": 1, "b": 1}, {"a": 1, "b": 2}, {"a": 2, "b": 2}]
        compacted, _ = await compactor.compact(payloads, loop_node_id="loop1", iteration=1)
        assert compacted[0] == {"a": 1, "b": 1}
        assert compacted[1] == {"b": 2}
        assert compacted[2] == {"a": 2}

    def test_lossy_strategy_requires_persistent_recall(self):
        rule = CompactionRule(
            strategy=CompactionStrategy.KEEP_LAST,
            require_persistent_recall=True,
        )
        compactor = LoopCompactor(rule, memory=None, state_store=None)
        ok, reason = compactor.validate_safety()
        assert ok is False
        assert "persistent recall" in reason


class TestTokenBudgetAdvisor:
    def test_proportional_allocation(self):
        advisor = TokenBudgetAdvisor(total_budget=1000, strategy="proportional")
        a = advisor.compute_advisory(node_id="n1", node_type="llm_operator", remaining_nodes=4)
        b = advisor.compute_advisory(node_id="n2", node_type="llm_operator", remaining_nodes=4)
        assert a is not None and b is not None
        assert a > 0 and b > 0

    def test_no_budget_returns_none(self):
        advisor = TokenBudgetAdvisor(total_budget=None)
        assert advisor.compute_advisory(node_id="n1", node_type="llm_operator") is None

    def test_adaptive_reallocation_updates_summary(self):
        advisor = TokenBudgetAdvisor(total_budget=300, strategy="adaptive")
        advisor.compute_advisory(node_id="a", node_type="llm_operator", remaining_nodes=3)
        advisor.compute_advisory(node_id="b", node_type="llm_operator", remaining_nodes=3)
        advisor.compute_advisory(node_id="c", node_type="llm_operator", remaining_nodes=3)
        advisor.record_actual("a", 20)
        summary = advisor.summary()
        assert summary["strategy"] == "adaptive"
        assert summary["actual"]["a"] == 20
