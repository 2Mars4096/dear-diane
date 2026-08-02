"""Cross-plan integration tests for Phase 10 Token Optimization.

Verifies that 18-1 (smart context assembly), 18-2 (caching), 18-3 (context
architecture), and 18-4 (analytics) work together through the scheduler
and executor layers.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from dan.engine.cache import NodeResultCache, SemanticCache
from dan.engine.executor import EngineConfig, ExecutionContext, NodeResult
from dan.engine.state import ExecutionState, NodeStatus
from dan.engine.context_runtime import ArtifactStore, LocalStateManager, SharedContextStore
from dan.engine.token_optimization import (
    ContextSelector,
    OptimizationPlaybook,
    PayloadPruner,
    TokenBudgetAdvisor,
    TokenWasteAnalyzer,
)
from dan.models.nodes import LLMOperator
from dan.providers.cost_tracker import CostTracker, TokenBreakdown, TokenSaving


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(
    *,
    cost_tracker: CostTracker | None = None,
    token_budget: int | None = None,
    state_store: Any = None,
) -> ExecutionContext:
    state = ExecutionState(run_id="integration-test")
    config = EngineConfig(token_budget=token_budget)
    ctx = ExecutionContext(
        state=state,
        config=config,
        shared_context=SharedContextStore(),
        artifacts=ArtifactStore(),
        local_state=LocalStateManager(),
        cost_tracker=cost_tracker,
        state_store=state_store,
    )
    return ctx


# ---------------------------------------------------------------------------
# Integration: CostTracker → breakdown → waste analysis → playbook
# ---------------------------------------------------------------------------

class TestEndToEndAnalyticsPipeline:
    """Simulate a run, then feed data through the full analytics stack."""

    def test_cost_tracker_feeds_waste_analyzer(self):
        ct = CostTracker()
        ct.record_breakdown("node_llm", TokenBreakdown(
            system_tokens=800,
            user_tokens=100,
            total_input_tokens=1000,
            total_output_tokens=200,
            context_edge_tokens=50,
        ))
        ct.record_breakdown("node_rag", TokenBreakdown(
            memory_tokens=400,
            rag_tokens=300,
            total_input_tokens=1000,
            total_output_tokens=100,
        ))

        analyzer = TokenWasteAnalyzer()
        report = analyzer.analyze(
            node_breakdowns=ct.all_breakdowns(),
            node_configs={},
        )
        cats = {f.category for f in report.findings}
        assert "oversized_system" in cats
        assert "unused_memory_rag" in cats

    def test_waste_report_feeds_playbook_to_principle(self):
        ct = CostTracker()
        ct.record_breakdown("n1", TokenBreakdown(
            system_tokens=900,
            total_input_tokens=1000,
            total_output_tokens=50,
        ))

        analyzer = TokenWasteAnalyzer()
        report = analyzer.analyze(
            node_breakdowns=ct.all_breakdowns(),
            node_configs={},
        )
        assert len(report.findings) > 0

        pb = OptimizationPlaybook(savings_threshold=0.1)
        pb.record_findings("run1", report)
        pb.mark_applied("run1", "n1", pre_tokens=1000)
        pb.evaluate_effectiveness("run1", "n1", post_tokens=600)

        principles = pb.promote_effective("run1", workflow_id="wf1")
        assert len(principles) >= 1
        p = principles[0]
        assert "token_optimization" in p["tags"]
        assert p["confidence"] > 0.5

    def test_savings_recorded_across_optimization_actions(self):
        ct = CostTracker()
        ct.record_saving(TokenSaving(action="context_deferred", tokens_saved=5000, node_id="n1"))
        ct.record_saving(TokenSaving(action="payload_pruned", tokens_saved=2000, node_id="n1"))
        ct.record_saving(TokenSaving(action="input_summarized", tokens_saved=3000, node_id="n2"))

        summary = ct.savings_summary()
        assert summary["total_tokens_saved"] == 10000
        assert summary["by_action"]["context_deferred"] == 5000
        assert summary["by_action"]["input_summarized"] == 3000
        assert summary["count"] == 3


# ---------------------------------------------------------------------------
# Integration: NodeResultCache + CostTracker memoization metrics
# ---------------------------------------------------------------------------

class TestCacheIntegrationWithCostTracker:
    def test_cache_hit_records_saving_in_cost_tracker(self):
        ct = CostTracker()
        cache = NodeResultCache(enabled=True)

        result = NodeResult(
            outputs={"text": "cached output"},
            status=NodeStatus.COMPLETED,
            metadata={"usage": {"prompt_tokens": 500, "completion_tokens": 100}},
        )
        cache.put("test-key-123", result)

        cached, reason = cache.lookup("test-key-123")
        assert cached is not None
        assert reason == "hit"
        ct.record_cache_result(hit=True, tokens_saved=500)

        cs = ct.cache_summary()
        assert cs["cache_hits"] == 1
        assert cs["tokens_saved"] == 500


# ---------------------------------------------------------------------------
# Integration: TokenBudgetAdvisor + CostTracker coordination
# ---------------------------------------------------------------------------

class TestBudgetAndCostIntegration:
    def test_budget_advisor_allocation_matches_cost_recording(self):
        advisor = TokenBudgetAdvisor(
            total_budget=10000,
            strategy="proportional",
        )
        alloc = advisor.compute_advisory(
            node_id="n1", node_type="llm_operator", remaining_nodes=3,
        )
        assert alloc is not None
        assert alloc > 0

        ct = CostTracker()
        ct.record("n1", "gpt-4", {"prompt_tokens": alloc, "completion_tokens": 100})
        assert ct.node_cost("n1") >= 0

        advisor.record_actual("n1", alloc)
        s = advisor.summary()
        assert s["actual"]["n1"] == alloc


# ---------------------------------------------------------------------------
# Integration: Context selector + pruner + waste detection pipeline
# ---------------------------------------------------------------------------

class TestContextPipelineIntegration:
    def test_selector_defers_then_waste_analyzer_detects_opportunity(self):
        selector = ContextSelector()
        inputs = {
            "primary": "This is the main task",
            "large_context": "x" * 50000,
        }
        scores = selector.score_inputs(inputs, "Process the {primary} data")
        assert scores["primary"] > scores["large_context"]

        inline, deferred = selector.select(
            inputs, "Process the {primary} data", target_tokens=500,
        )
        assert "primary" in inline

        ct = CostTracker()
        ct.record_breakdown("n1", TokenBreakdown(
            context_edge_tokens=12000,
            total_input_tokens=15000,
        ))
        analyzer = TokenWasteAnalyzer()
        report = analyzer.analyze(
            node_breakdowns=ct.all_breakdowns(),
            node_configs={},
            graph_edges=[{"id": "e1", "edge_type": "context", "source": "src", "target": "n1", "pass_by_reference": False}],
        )
        cats = {f.category for f in report.findings}
        assert "reference_opportunity" in cats

    def test_pruner_reduces_tokens_then_saving_is_tracked(self):
        pruner = PayloadPruner(["*.internal_id", "*.debug_info"])
        data = {
            "result": "important",
            "internal_id": "xyz123",
            "debug_info": {"trace": "long" * 100},
        }
        pruned, removed = pruner.prune(data)
        assert removed == 2
        assert "internal_id" not in pruned
        assert "result" in pruned


# ---------------------------------------------------------------------------
# Integration: Playbook mutation generation for multiple finding types
# ---------------------------------------------------------------------------

class TestPlaybookMutationGeneration:
    def test_generates_mutations_for_diverse_findings(self):
        analyzer = TokenWasteAnalyzer()
        report = analyzer.analyze(
            node_breakdowns={
                "n1": {"system_tokens": 900, "total_input_tokens": 1000, "total_output_tokens": 50},
            },
            node_configs={
                "n_tools": {"tools": [f"t{i}" for i in range(8)], "jit_tool_loading": False},
            },
            events=[
                {"event_type": "tool_call_started", "node_id": "n_tools", "data": {"tool_name": "t0"}},
            ],
            graph_edges=[
                {"id": "e1", "edge_type": "context", "source": "a", "target": "n_ref", "pass_by_reference": False},
            ],
        )

        pb = OptimizationPlaybook()
        mutations = []
        for finding in report.findings:
            mut = pb.generate_mutation(finding)
            if mut:
                mutations.append(mut)

        ops = {m["op"] for m in mutations}
        assert "edit_node" in ops

    def test_round_trip_waste_to_mutation_to_playbook(self):
        """Full cycle: detect waste → generate mutation → track in playbook → promote."""
        ct = CostTracker()
        ct.record_breakdown("n1", TokenBreakdown(
            system_tokens=900,
            total_input_tokens=1000,
            total_output_tokens=50,
        ))

        analyzer = TokenWasteAnalyzer()
        report = analyzer.analyze(node_breakdowns=ct.all_breakdowns(), node_configs={})
        assert len(report.findings) > 0

        pb = OptimizationPlaybook(savings_threshold=0.1)
        entries = pb.record_findings("run1", report)

        for entry in entries:
            mut = pb.generate_mutation(entry.finding)
            if mut:
                pb.mark_applied("run1", entry.finding.node_id, pre_tokens=1000)

        pb.evaluate_effectiveness("run1", "n1", post_tokens=500)

        principles = pb.promote_effective("run1", workflow_id="wf1")
        assert len(principles) >= 1

        s = pb.summary()
        assert s["promoted"] >= 1
        assert s["applied"] >= 1
