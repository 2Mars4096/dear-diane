"""Tests for Plan 18-4 token analytics: breakdown, savings, waste detection, playbooks."""

from __future__ import annotations

import pytest

from dan.providers.cost_tracker import CostTracker, TokenBreakdown, TokenSaving
from dan.engine.token_optimization import (
    OptimizationPlaybook,
    PlaybookEntry,
    TokenWasteAnalyzer,
    TokenOptimizationReport,
    WasteFinding,
)


# ---------------------------------------------------------------------------
# TokenBreakdown / TokenSaving model tests
# ---------------------------------------------------------------------------

class TestTokenBreakdown:
    def test_to_dict_roundtrip(self):
        bd = TokenBreakdown(
            system_tokens=100,
            user_tokens=500,
            assistant_tokens=50,
            output_tokens=200,
            context_edge_tokens=300,
            memory_tokens=80,
            rag_tokens=60,
            total_input_tokens=1000,
            total_output_tokens=200,
        )
        d = bd.to_dict()
        assert d["system_tokens"] == 100
        assert d["context_edge_tokens"] == 300
        assert d["memory_tokens"] == 80
        assert d["rag_tokens"] == 60
        assert d["total_input_tokens"] == 1000

    def test_defaults_zero(self):
        bd = TokenBreakdown()
        d = bd.to_dict()
        assert all(v == 0 for v in d.values())


class TestTokenSaving:
    def test_to_dict(self):
        ts = TokenSaving(
            action="context_deferred",
            tokens_saved=5000,
            cost_saved=0.01,
            node_id="n1",
            details={"ports": ["data", "context"]},
        )
        d = ts.to_dict()
        assert d["action"] == "context_deferred"
        assert d["tokens_saved"] == 5000
        assert d["node_id"] == "n1"


# ---------------------------------------------------------------------------
# CostTracker breakdown / savings integration
# ---------------------------------------------------------------------------

class TestCostTrackerAnalytics:
    def test_record_and_retrieve_breakdown(self):
        ct = CostTracker()
        bd = TokenBreakdown(system_tokens=50, user_tokens=200, total_input_tokens=250)
        ct.record_breakdown("node_a", bd)
        assert ct.get_breakdown("node_a") is bd
        assert ct.get_breakdown("node_b") is None

    def test_all_breakdowns(self):
        ct = CostTracker()
        ct.record_breakdown("n1", TokenBreakdown(system_tokens=10))
        ct.record_breakdown("n2", TokenBreakdown(system_tokens=20))
        bds = ct.all_breakdowns()
        assert len(bds) == 2
        assert bds["n1"]["system_tokens"] == 10
        assert bds["n2"]["system_tokens"] == 20

    def test_record_and_list_savings(self):
        ct = CostTracker()
        ct.record_saving(TokenSaving(action="deferred", tokens_saved=1000, node_id="n1"))
        ct.record_saving(TokenSaving(action="pruned", tokens_saved=500, node_id="n2"))
        savings = ct.all_savings()
        assert len(savings) == 2

    def test_savings_summary(self):
        ct = CostTracker()
        ct.record_saving(TokenSaving(action="deferred", tokens_saved=1000, cost_saved=0.01))
        ct.record_saving(TokenSaving(action="deferred", tokens_saved=2000, cost_saved=0.02))
        ct.record_saving(TokenSaving(action="pruned", tokens_saved=500, cost_saved=0.005))
        s = ct.savings_summary()
        assert s["total_tokens_saved"] == 3500
        assert s["total_cost_saved"] == pytest.approx(0.035)
        assert s["by_action"]["deferred"] == 3000
        assert s["by_action"]["pruned"] == 500
        assert s["count"] == 3

    def test_snapshot_restore_includes_analytics(self):
        ct = CostTracker()
        ct.record_breakdown("n1", TokenBreakdown(system_tokens=42, total_input_tokens=100))
        ct.record_saving(TokenSaving(action="deferred", tokens_saved=1000))
        snap = ct.snapshot()
        assert "node_breakdowns" in snap
        assert "savings" in snap

        ct2 = CostTracker()
        ct2.restore(snap)
        bd = ct2.get_breakdown("n1")
        assert bd is not None
        assert bd.system_tokens == 42
        assert len(ct2.all_savings()) == 1


# ---------------------------------------------------------------------------
# TokenWasteAnalyzer tests
# ---------------------------------------------------------------------------

class TestTokenWasteAnalyzer:
    def test_empty_run_no_findings(self):
        analyzer = TokenWasteAnalyzer()
        report = analyzer.analyze(node_breakdowns={}, node_configs={})
        assert report.total_saveable_tokens == 0
        assert len(report.findings) == 0

    def test_detect_oversized_system(self):
        analyzer = TokenWasteAnalyzer()
        report = analyzer.analyze(
            node_breakdowns={
                "n1": {"system_tokens": 800, "total_input_tokens": 1000, "total_output_tokens": 200},
            },
            node_configs={},
        )
        cats = [f.category for f in report.findings]
        assert "oversized_system" in cats

    def test_no_oversized_system_when_below_threshold(self):
        analyzer = TokenWasteAnalyzer()
        report = analyzer.analyze(
            node_breakdowns={
                "n1": {"system_tokens": 100, "total_input_tokens": 1000, "total_output_tokens": 200},
            },
            node_configs={},
        )
        cats = [f.category for f in report.findings]
        assert "oversized_system" not in cats

    def test_detect_unused_memory_rag(self):
        analyzer = TokenWasteAnalyzer()
        report = analyzer.analyze(
            node_breakdowns={
                "n1": {
                    "memory_tokens": 400,
                    "rag_tokens": 200,
                    "total_input_tokens": 1000,
                    "total_output_tokens": 50,
                },
            },
            node_configs={},
        )
        cats = [f.category for f in report.findings]
        assert "unused_memory_rag" in cats

    def test_detect_loop_growth(self):
        analyzer = TokenWasteAnalyzer()
        events = [
            {"event_type": "iteration_started", "node_id": "loop1", "data": {"loop_id": "loop1", "context_tokens": 1000}},
            {"event_type": "iteration_started", "node_id": "loop1", "data": {"loop_id": "loop1", "context_tokens": 1500}},
            {"event_type": "iteration_started", "node_id": "loop1", "data": {"loop_id": "loop1", "context_tokens": 2500}},
        ]
        report = analyzer.analyze(node_breakdowns={}, node_configs={}, events=events)
        cats = [f.category for f in report.findings]
        assert "loop_growth" in cats
        growth_finding = [f for f in report.findings if f.category == "loop_growth"][0]
        assert growth_finding.details["growth_factor"] == 2.5

    def test_detect_jit_opportunity(self):
        analyzer = TokenWasteAnalyzer()
        events = [
            {"event_type": "tool_call_started", "node_id": "n1", "data": {"tool_name": "search"}},
        ]
        configs = {
            "n1": {
                "tools": [f"tool_{i}" for i in range(10)],
                "jit_tool_loading": False,
            },
        }
        report = analyzer.analyze(
            node_breakdowns={}, node_configs=configs, events=events,
        )
        cats = [f.category for f in report.findings]
        assert "jit_opportunity" in cats

    def test_no_jit_finding_when_already_enabled(self):
        analyzer = TokenWasteAnalyzer()
        configs = {
            "n1": {
                "tools": [f"tool_{i}" for i in range(10)],
                "jit_tool_loading": True,
            },
        }
        report = analyzer.analyze(node_breakdowns={}, node_configs=configs)
        cats = [f.category for f in report.findings]
        assert "jit_opportunity" not in cats

    def test_detect_duplicate_context(self):
        analyzer = TokenWasteAnalyzer()
        edges = [
            {"source": "a", "target": "b"},
            {"source": "a", "target": "b"},
        ]
        breakdowns = {"b": {"context_edge_tokens": 1000}}
        report = analyzer.analyze(
            node_breakdowns=breakdowns, node_configs={}, graph_edges=edges,
        )
        cats = [f.category for f in report.findings]
        assert "duplicate" in cats

    def test_detect_memoization_opportunity(self):
        analyzer = TokenWasteAnalyzer()
        events = [
            {"event_type": "node_started", "node_id": "n1", "data": {"input_hash": "abc123"}},
            {"event_type": "node_started", "node_id": "n1", "data": {"input_hash": "abc123"}},
            {"event_type": "node_started", "node_id": "n1", "data": {"input_hash": "def456"}},
        ]
        report = analyzer.analyze(
            node_breakdowns={}, node_configs={}, events=events,
        )
        cats = [f.category for f in report.findings]
        assert "memoization_opportunity" in cats

    def test_detect_reference_opportunity(self):
        analyzer = TokenWasteAnalyzer()
        edges = [{"id": "e1", "edge_type": "context", "source": "a", "target": "b", "pass_by_reference": False}]
        breakdowns = {"b": {"context_edge_tokens": 15000}}
        report = analyzer.analyze(
            node_breakdowns=breakdowns, node_configs={}, graph_edges=edges,
        )
        cats = [f.category for f in report.findings]
        assert "reference_opportunity" in cats

    def test_report_sorted_by_savings_descending(self):
        analyzer = TokenWasteAnalyzer()
        report = analyzer.analyze(
            node_breakdowns={
                "n1": {"system_tokens": 900, "total_input_tokens": 1000, "total_output_tokens": 50},
                "n2": {"memory_tokens": 500, "rag_tokens": 500, "total_input_tokens": 1000, "total_output_tokens": 50},
            },
            node_configs={},
        )
        d = report.to_dict()
        savings = [f["estimated_saveable_tokens"] for f in d["findings"]]
        assert savings == sorted(savings, reverse=True)

    def test_to_dict_shape(self):
        report = TokenOptimizationReport(
            findings=[
                WasteFinding(category="test", node_id="n1", description="test finding", estimated_saveable_tokens=100),
            ],
            total_saveable_tokens=100,
        )
        d = report.to_dict()
        assert "findings" in d
        assert "total_saveable_tokens" in d
        assert "count" in d
        assert d["count"] == 1


# ---------------------------------------------------------------------------
# EngineConfig: approval mode field
# ---------------------------------------------------------------------------

class TestOptimizationApprovalMode:
    def test_default_always_approve(self):
        from dan.engine.executor import EngineConfig
        cfg = EngineConfig()
        assert cfg.optimization_rule_approval_mode == "always_approve"

    def test_auto_accept(self):
        from dan.engine.executor import EngineConfig
        cfg = EngineConfig(optimization_rule_approval_mode="auto_accept")
        assert cfg.optimization_rule_approval_mode == "auto_accept"


# ---------------------------------------------------------------------------
# OptimizationPlaybook tests (18-4 Task 5)
# ---------------------------------------------------------------------------

class TestOptimizationPlaybook:
    def _make_report(self) -> TokenOptimizationReport:
        return TokenOptimizationReport(
            findings=[
                WasteFinding(
                    category="jit_opportunity",
                    node_id="n1",
                    description="Node has 10 tools but only 2 called",
                    estimated_saveable_tokens=1600,
                    suggestion="Enable jit_tool_loading on n1",
                ),
                WasteFinding(
                    category="oversized_system",
                    node_id="n2",
                    description="System prompt is 80% of input",
                    estimated_saveable_tokens=500,
                    suggestion="Extract system prompt parts into agent_context_tools",
                ),
            ],
            total_saveable_tokens=2100,
        )

    def test_record_findings(self):
        pb = OptimizationPlaybook()
        report = self._make_report()
        entries = pb.record_findings("run1", report)
        assert len(entries) == 2
        assert entries[0].run_id == "run1"
        assert not entries[0].applied

    def test_mark_applied_and_evaluate(self):
        pb = OptimizationPlaybook()
        report = self._make_report()
        pb.record_findings("run1", report)
        pb.mark_applied("run1", "n1", pre_tokens=10000)
        entry = pb.evaluate_effectiveness("run1", "n1", post_tokens=7000)
        assert entry is not None
        assert entry.applied
        assert entry.effectiveness == pytest.approx(0.3)

    def test_promote_effective_above_threshold(self):
        pb = OptimizationPlaybook(savings_threshold=0.1)
        report = self._make_report()
        pb.record_findings("run1", report)
        pb.mark_applied("run1", "n1", pre_tokens=10000)
        pb.evaluate_effectiveness("run1", "n1", post_tokens=7000)
        principles = pb.promote_effective("run1", workflow_id="wf1")
        assert len(principles) == 1
        p = principles[0]
        assert "token_optimization" in p["tags"]
        assert p["workflow_id"] == "wf1"
        assert p["repair_level"] == "parameter_fix"
        assert p["suggested_parameter_changes"] == {"jit_tool_loading": True}
        assert p["auto_activate"] is False
        assert p["approval_mode"] == "always_approve"

    def test_no_promotion_below_threshold(self):
        pb = OptimizationPlaybook(savings_threshold=0.5)
        report = self._make_report()
        pb.record_findings("run1", report)
        pb.mark_applied("run1", "n1", pre_tokens=10000)
        pb.evaluate_effectiveness("run1", "n1", post_tokens=9500)
        principles = pb.promote_effective("run1")
        assert len(principles) == 0

    def test_no_double_promotion(self):
        pb = OptimizationPlaybook(savings_threshold=0.1)
        report = self._make_report()
        pb.record_findings("run1", report)
        pb.mark_applied("run1", "n1", pre_tokens=10000)
        pb.evaluate_effectiveness("run1", "n1", post_tokens=5000)
        first = pb.promote_effective("run1")
        assert len(first) == 1
        second = pb.promote_effective("run1")
        assert len(second) == 0

    def test_approval_mode_property(self):
        pb = OptimizationPlaybook(approval_mode="auto_accept")
        assert pb.approval_mode == "auto_accept"

    def test_invalid_approval_mode_raises(self):
        with pytest.raises(ValueError):
            OptimizationPlaybook(approval_mode="invalid")

    def test_auto_accept_promoted_principle_marks_auto_activate(self):
        pb = OptimizationPlaybook(approval_mode="auto_accept", savings_threshold=0.1)
        report = self._make_report()
        pb.record_findings("run1", report)
        pb.mark_applied("run1", "n1", pre_tokens=10000)
        pb.evaluate_effectiveness("run1", "n1", post_tokens=7000)
        principles = pb.promote_effective("run1", workflow_id="wf1")
        assert principles and principles[0]["auto_activate"] is True

    def test_summary(self):
        pb = OptimizationPlaybook()
        report = self._make_report()
        pb.record_findings("run1", report)
        s = pb.summary()
        assert s["total_entries"] == 2
        assert s["runs_tracked"] == 1

    def test_generate_mutation_jit(self):
        from dan.server.graph_mutator import MutationPlan
        pb = OptimizationPlaybook()
        finding = WasteFinding(
            category="jit_opportunity",
            node_id="n1",
            description="test",
            suggestion="Enable jit",
        )
        mut = pb.generate_mutation(finding)
        assert mut is not None
        assert mut["op"] == "edit_node"
        assert mut["updates"]["jit_tool_loading"] is True
        MutationPlan.model_validate({"operations": [mut]})

    def test_generate_mutation_reference(self):
        from dan.server.graph_mutator import MutationPlan
        pb = OptimizationPlaybook()
        finding = WasteFinding(
            category="reference_opportunity",
            node_id="n2",
            description="test",
            suggestion="Use pass_by_reference",
            details={"source": "n1", "edge_id": "e1"},
        )
        mut = pb.generate_mutation(finding)
        assert mut is not None
        assert mut["op"] == "edit_edge"
        assert mut["updates"]["pass_by_reference"] is True
        MutationPlan.model_validate({"operations": [mut]})

    def test_generate_mutation_loop_growth(self):
        pb = OptimizationPlaybook()
        finding = WasteFinding(
            category="loop_growth",
            node_id="loop1",
            description="test",
            suggestion="Add compaction",
        )
        mut = pb.generate_mutation(finding)
        assert mut is not None
        assert mut["updates"]["compaction_rule"]["strategy"] == "sliding_window"
        assert mut["updates"]["compaction_rule"]["require_persistent_recall"] is True

    def test_generate_mutation_memoization(self):
        pb = OptimizationPlaybook()
        finding = WasteFinding(
            category="memoization_opportunity",
            node_id="n3",
            description="test",
            suggestion="Enable memoize",
        )
        mut = pb.generate_mutation(finding)
        assert mut is not None
        assert mut["updates"]["memoize"] is True

    def test_generate_mutation_returns_none_for_unknown(self):
        pb = OptimizationPlaybook()
        finding = WasteFinding(
            category="unknown_category",
            node_id="n1",
            description="test",
        )
        assert pb.generate_mutation(finding) is None


# ---------------------------------------------------------------------------
# Analytics event emission tests (scheduler integration)
# ---------------------------------------------------------------------------


class TestAnalyticsEventEmission:
    """Verify that the scheduler emits TOKEN_BREAKDOWN_RECORDED,
    WASTE_DETECTED, and OPTIMIZATION_REPORT_READY events."""

    @pytest.mark.asyncio
    async def test_token_breakdown_recorded_event_emitted(self):
        """After an LLM node completes, TOKEN_BREAKDOWN_RECORDED is emitted."""
        from dan.engine import Engine, EngineConfig, NodeResult, NodeStatus
        from dan.engine.checkpoint import NullCheckpointStore
        from dan.engine.events import EventType
        from dan.engine.executor import ExecutionContext
        from dan.models.graph import Graph
        from dan.models.nodes import LLMOperator
        from dan.models.ports import OutputPort

        events: list = []

        async def _capture(event):
            events.append(event)

        class FakeLLMExecutor:
            async def execute(self, node, inputs, context: ExecutionContext):
                if context.cost_tracker is not None:
                    bd = TokenBreakdown(
                        system_tokens=50, user_tokens=100,
                        total_input_tokens=150, total_output_tokens=30,
                    )
                    context.cost_tracker.record_breakdown(node.id, bd)
                return NodeResult(
                    outputs={"text": "hello"},
                    status=NodeStatus.COMPLETED,
                    metadata={"usage": {"prompt_tokens": 150, "completion_tokens": 30}},
                )

        node = LLMOperator(
            id="llm1", name="LLM1",
            model="mock-model",
            prompt_template="test",
            input_ports=[],
            output_ports=[OutputPort(name="text", schema={"type": "string"})],
        )
        graph = Graph(nodes=[node], edges=[], entry_points=["llm1"])

        engine = Engine(
            config=EngineConfig(checkpoint_enabled=False),
            checkpoint_store=NullCheckpointStore(),
            event_callback=_capture,
        )
        engine.executor_registry.register("llm_operator", FakeLLMExecutor())

        await engine.run(graph)

        bd_events = [e for e in events if e.event_type == EventType.TOKEN_BREAKDOWN_RECORDED]
        assert len(bd_events) >= 1
        assert bd_events[0].node_id == "llm1"
        assert bd_events[0].data["system_tokens"] == 50

    @pytest.mark.asyncio
    async def test_optimization_report_ready_emitted_at_run_end(self):
        """OPTIMIZATION_REPORT_READY is emitted after run completes when
        breakdowns exist with detectable waste patterns."""
        from dan.engine import Engine, EngineConfig, NodeResult, NodeStatus
        from dan.engine.checkpoint import NullCheckpointStore
        from dan.engine.events import EventType
        from dan.engine.executor import ExecutionContext
        from dan.models.graph import Graph
        from dan.models.nodes import LLMOperator
        from dan.models.ports import OutputPort

        events: list = []

        async def _capture(event):
            events.append(event)

        class FakeLLMExecutor:
            async def execute(self, node, inputs, context: ExecutionContext):
                if context.cost_tracker is not None:
                    bd = TokenBreakdown(
                        system_tokens=600, user_tokens=100,
                        total_input_tokens=1000, total_output_tokens=50,
                    )
                    context.cost_tracker.record_breakdown(node.id, bd)
                return NodeResult(
                    outputs={"text": "ok"},
                    status=NodeStatus.COMPLETED,
                    metadata={"usage": {"prompt_tokens": 1000, "completion_tokens": 50}},
                )

        node = LLMOperator(
            id="n1", name="Node1",
            model="mock-model",
            prompt_template="test",
            input_ports=[],
            output_ports=[OutputPort(name="text", schema={"type": "string"})],
        )
        graph = Graph(nodes=[node], edges=[], entry_points=["n1"])

        engine = Engine(
            config=EngineConfig(checkpoint_enabled=False),
            checkpoint_store=NullCheckpointStore(),
            event_callback=_capture,
        )
        engine.executor_registry.register("llm_operator", FakeLLMExecutor())

        await engine.run(graph)

        report_events = [e for e in events if e.event_type == EventType.OPTIMIZATION_REPORT_READY]
        assert len(report_events) == 1
        report_data = report_events[0].data
        assert "findings" in report_data
        assert "total_saveable_tokens" in report_data

    @pytest.mark.asyncio
    async def test_waste_detected_events_emitted(self):
        """WASTE_DETECTED is emitted per-finding when breakdowns have detectable waste."""
        from dan.engine import Engine, EngineConfig, NodeResult, NodeStatus
        from dan.engine.checkpoint import NullCheckpointStore
        from dan.engine.events import EventType
        from dan.engine.executor import ExecutionContext
        from dan.models.graph import Graph
        from dan.models.nodes import LLMOperator
        from dan.models.ports import OutputPort

        events: list = []

        async def _capture(event):
            events.append(event)

        class FakeLLMExecutor:
            async def execute(self, node, inputs, context: ExecutionContext):
                if context.cost_tracker is not None:
                    bd = TokenBreakdown(
                        system_tokens=800, user_tokens=100,
                        total_input_tokens=1000, total_output_tokens=50,
                    )
                    context.cost_tracker.record_breakdown(node.id, bd)
                return NodeResult(
                    outputs={"text": "ok"},
                    status=NodeStatus.COMPLETED,
                    metadata={"usage": {"prompt_tokens": 1000, "completion_tokens": 50}},
                )

        node = LLMOperator(
            id="n1", name="Node1",
            model="mock-model",
            prompt_template="test",
            input_ports=[],
            output_ports=[OutputPort(name="text", schema={"type": "string"})],
        )
        graph = Graph(nodes=[node], edges=[], entry_points=["n1"])

        engine = Engine(
            config=EngineConfig(checkpoint_enabled=False),
            checkpoint_store=NullCheckpointStore(),
            event_callback=_capture,
        )
        engine.executor_registry.register("llm_operator", FakeLLMExecutor())

        await engine.run(graph)

        waste_events = [e for e in events if e.event_type == EventType.WASTE_DETECTED]
        assert len(waste_events) >= 1
        for we in waste_events:
            assert "category" in we.data
            assert "node_id" in we.data

    @pytest.mark.asyncio
    async def test_no_analytics_events_when_no_breakdowns(self):
        """No analytics events when no LLM nodes ran."""
        from dan.engine import Engine, EngineConfig, NodeResult, NodeStatus
        from dan.engine.checkpoint import NullCheckpointStore
        from dan.engine.events import EventType
        from dan.engine.executor import ExecutionContext
        from dan.models.graph import Graph
        from dan.models.nodes import CodeOperator
        from dan.models.ports import InputPort, OutputPort

        events: list = []

        async def _capture(event):
            events.append(event)

        class FakeCodeExecutor:
            async def execute(self, node, inputs, context: ExecutionContext):
                return NodeResult(
                    outputs={"result": "done"},
                    status=NodeStatus.COMPLETED,
                )

        node = CodeOperator(
            id="c1", name="Code1",
            code="pass",
            input_ports=[],
            output_ports=[OutputPort(name="result", schema={"type": "string"})],
        )
        graph = Graph(nodes=[node], edges=[], entry_points=["c1"])

        engine = Engine(
            config=EngineConfig(checkpoint_enabled=False),
            checkpoint_store=NullCheckpointStore(),
            event_callback=_capture,
        )
        engine.executor_registry.register("code_operator", FakeCodeExecutor())

        await engine.run(graph)

        analytics_types = {
            EventType.TOKEN_BREAKDOWN_RECORDED,
            EventType.WASTE_DETECTED,
            EventType.OPTIMIZATION_REPORT_READY,
        }
        analytics_events = [e for e in events if e.event_type in analytics_types]
        assert len(analytics_events) == 0
