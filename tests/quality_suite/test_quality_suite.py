"""Tests for the generation quality suite runners, report, and baseline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.meta.intent_schema import StageType, WorkflowIntent

from tests.quality_suite.codegen_runner import (
    IntentResult,
    check_structure_constraints,
    check_topology,
    fixture_to_workflow_intent,
    run_codegen_evaluation,
)
from tests.quality_suite.intent_runner import run_intent_evaluation
from tests.quality_suite.loader import load_golden_intents
from tests.quality_suite.report import (
    GenerationQualityReport,
    build_report,
    compare_to_baseline,
)

pytestmark = pytest.mark.quality_suite

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_BASELINE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "generation_quality_baseline.json"
)


@pytest.fixture(scope="module")
def golden_intents() -> list[dict]:
    return load_golden_intents()


@pytest.fixture(scope="module")
def codegen_results(golden_intents: list[dict]) -> list[IntentResult]:
    return run_codegen_evaluation(golden_intents)


@pytest.fixture(scope="module")
def intent_results(golden_intents: list[dict]) -> list[IntentResult]:
    return run_intent_evaluation(golden_intents)


# ---------------------------------------------------------------------------
# 1. Codegen runner produces results for all 18 intents
# ---------------------------------------------------------------------------


class TestCodegenRunner:
    def test_produces_results_for_all_intents(
        self, golden_intents: list[dict], codegen_results: list[IntentResult]
    ):
        assert len(codegen_results) == len(golden_intents)
        assert len(codegen_results) == 18

    def test_all_results_have_codegen_path(
        self, codegen_results: list[IntentResult]
    ):
        for r in codegen_results:
            assert r.path == "codegen"

    def test_results_have_valid_fields(
        self, codegen_results: list[IntentResult]
    ):
        for r in codegen_results:
            assert r.intent_file
            assert r.family
            assert r.variant
            assert r.latency_ms >= 0


# ---------------------------------------------------------------------------
# 2. Intent runner produces results for all 18 intents
# ---------------------------------------------------------------------------


class TestIntentRunner:
    def test_produces_results_for_all_intents(
        self, golden_intents: list[dict], intent_results: list[IntentResult]
    ):
        assert len(intent_results) == len(golden_intents)
        assert len(intent_results) == 18

    def test_all_results_have_intent_path(
        self, intent_results: list[IntentResult]
    ):
        for r in intent_results:
            assert r.path == "intent"

    def test_passing_results_have_coverage_info(
        self, intent_results: list[IntentResult]
    ):
        for r in intent_results:
            if r.passed:
                assert "coverage" in r.topology_check


# ---------------------------------------------------------------------------
# 3. Reports have correct pass/fail counts
# ---------------------------------------------------------------------------


class TestReportBuilding:
    def test_codegen_report_counts(
        self, codegen_results: list[IntentResult]
    ):
        report = build_report(codegen_results, "codegen")
        assert report.total_intents == 18
        assert report.passed + report.failed == 18
        assert report.path == "codegen"
        assert 0.0 <= report.pass_rate <= 1.0

    def test_intent_report_counts(
        self, intent_results: list[IntentResult]
    ):
        report = build_report(intent_results, "intent")
        assert report.total_intents == 18
        assert report.passed + report.failed == 18
        assert report.path == "intent"

    def test_intent_report_has_coverage_stats(
        self, intent_results: list[IntentResult]
    ):
        report = build_report(intent_results, "intent")
        assert "covered" in report.coverage_stats
        assert "fallback" in report.coverage_stats

    def test_failure_modes_populated_for_failures(
        self, codegen_results: list[IntentResult]
    ):
        report = build_report(codegen_results, "codegen")
        if report.failed > 0:
            assert len(report.failure_modes) > 0
            for fm in report.failure_modes:
                assert fm.count > 0
                assert fm.error_type

    def test_empty_results_report(self):
        report = build_report([], "codegen")
        assert report.total_intents == 0
        assert report.passed == 0
        assert report.pass_rate == 0.0


# ---------------------------------------------------------------------------
# 4. Baseline comparison detects regressions
# ---------------------------------------------------------------------------


class TestBaselineComparison:
    def test_regression_detected_when_total_mismatch(self):
        report = GenerationQualityReport(
            path="codegen",
            total_intents=17,
            passed=17,
            failed=0,
            pass_rate=1.0,
        )
        baseline = {"total": 18, "min_pass": 15, "max_fail": 3}
        regressions = compare_to_baseline(report, baseline)
        assert len(regressions) >= 1
        assert any("total intents" in r for r in regressions)

    def test_regression_detected_when_pass_too_low(self):
        report = GenerationQualityReport(
            path="codegen",
            total_intents=18,
            passed=10,
            failed=8,
            pass_rate=10 / 18,
        )
        baseline = {"total": 18, "min_pass": 15, "max_fail": 3}
        regressions = compare_to_baseline(report, baseline)
        assert len(regressions) >= 1
        assert any("pass count" in r for r in regressions)

    def test_regression_detected_when_fail_too_high(self):
        report = GenerationQualityReport(
            path="codegen",
            total_intents=18,
            passed=14,
            failed=4,
            pass_rate=14 / 18,
        )
        baseline = {"total": 18, "min_pass": 12, "max_fail": 3}
        regressions = compare_to_baseline(report, baseline)
        assert len(regressions) >= 1
        assert any("fail count" in r for r in regressions)

    def test_no_regression_when_thresholds_met(self):
        report = GenerationQualityReport(
            path="codegen",
            total_intents=18,
            passed=17,
            failed=1,
            pass_rate=17 / 18,
        )
        baseline = {"total": 18, "min_pass": 15, "max_fail": 3}
        regressions = compare_to_baseline(report, baseline)
        assert regressions == []

    def test_empty_baseline_no_regression(self):
        report = GenerationQualityReport(
            path="codegen",
            total_intents=18,
            passed=5,
            failed=13,
            pass_rate=5 / 18,
        )
        assert compare_to_baseline(report, {}) == []

    def test_coverage_regression_detected(self):
        report = GenerationQualityReport(
            path="intent",
            total_intents=18,
            passed=15,
            failed=3,
            pass_rate=15 / 18,
            coverage_stats={"covered": 8, "fallback": 10, "total": 18},
        )
        baseline = {"total": 18, "min_pass": 12, "min_covered": 10}
        regressions = compare_to_baseline(report, baseline)
        assert len(regressions) >= 1
        assert any("covered count" in r for r in regressions)


# ---------------------------------------------------------------------------
# 5. Round-trip check runs for passing intents
# ---------------------------------------------------------------------------


class TestRoundTripIntegration:
    def test_passing_codegen_results_exercised_round_trip(
        self, codegen_results: list[IntentResult]
    ):
        passing = [r for r in codegen_results if r.passed]
        assert len(passing) > 0, "Need at least one passing codegen result"
        for r in passing:
            assert r.topology_check.get("match") is True

    def test_passing_intent_results_exercised_round_trip(
        self, intent_results: list[IntentResult]
    ):
        passing = [r for r in intent_results if r.passed]
        assert len(passing) > 0, "Need at least one passing intent result"
        for r in passing:
            assert r.topology_check.get("match") is True


# ---------------------------------------------------------------------------
# 6. fixture_to_workflow_intent produces valid intents
# ---------------------------------------------------------------------------


class TestFixtureConversion:
    def test_simple_transform_fixture(self):
        fixture = {
            "intent": "Write a blog post",
            "family": "paper_writing",
            "variant": "simple",
            "expected": {
                "min_nodes": 3,
                "node_types": ["llm_operator"],
                "topology": {
                    "has_loops": False,
                    "has_fan_out": False,
                    "has_tools": False,
                    "has_rag": False,
                    "has_human_in_loop": False,
                },
            },
        }
        intent = fixture_to_workflow_intent(fixture)
        assert isinstance(intent, WorkflowIntent)
        assert intent.goal == "Write a blog post"
        stage_types = {s.stage_type for s in intent.stages}
        assert StageType.transform in stage_types
        assert StageType.review_loop not in stage_types

    def test_complex_topology_fixture(self):
        fixture = {
            "intent": "Complex pipeline",
            "family": "multi_step_analysis",
            "variant": "complex",
            "expected": {
                "min_nodes": 10,
                "node_types": ["llm_operator"],
                "topology": {
                    "has_loops": True,
                    "has_fan_out": True,
                    "has_tools": True,
                    "has_rag": True,
                    "has_human_in_loop": True,
                },
            },
        }
        intent = fixture_to_workflow_intent(fixture)
        stage_types = {s.stage_type for s in intent.stages}
        assert StageType.review_loop in stage_types
        assert StageType.fan_out in stage_types
        assert StageType.tool_call in stage_types
        assert StageType.rag_retrieval in stage_types
        assert StageType.human_approval in stage_types

    def test_all_golden_intents_convert(self, golden_intents: list[dict]):
        for fixture in golden_intents:
            intent = fixture_to_workflow_intent(fixture)
            assert isinstance(intent, WorkflowIntent)
            assert len(intent.stages) >= 2


# ---------------------------------------------------------------------------
# 7. Topology check detects mismatches
# ---------------------------------------------------------------------------


class TestTopologyCheck:
    def test_matching_topology(self):
        from dan.models.graph import Graph
        from dan.models.nodes import LLMOperator
        from dan.models.ports import InputPort, OutputPort
        from dan.models.control_flow import WhileLoopNode

        wl = WhileLoopNode(
            id="loop",
            name="loop",
            condition="x < 5",
            body_graph="loop_body",
        )
        g = Graph(
            nodes=[
                LLMOperator(
                    id="a", name="a", model="m",
                    prompt_template="p",
                    input_ports=[InputPort(name="input")],
                    output_ports=[OutputPort(name="text")],
                ),
                wl,
            ],
            entry_points=["a"],
        )
        topo = check_topology(g, {"has_loops": True, "has_fan_out": False})
        assert topo["match"] is True

    def test_mismatching_topology(self):
        from dan.models.graph import Graph
        from dan.models.nodes import LLMOperator
        from dan.models.ports import InputPort, OutputPort

        g = Graph(
            nodes=[
                LLMOperator(
                    id="a", name="a", model="m",
                    prompt_template="p",
                    input_ports=[InputPort(name="input")],
                    output_ports=[OutputPort(name="text")],
                ),
            ],
            entry_points=["a"],
        )
        topo = check_topology(g, {"has_loops": True})
        assert topo["match"] is False


# ---------------------------------------------------------------------------
# 8. Structure constraints enforce min_nodes + node_types
# ---------------------------------------------------------------------------


class TestStructureConstraints:
    def test_helper_detects_missing_types_and_min_nodes(self):
        from dan.models.graph import Graph
        from dan.models.nodes import LLMOperator
        from dan.models.ports import InputPort, OutputPort

        g = Graph(
            nodes=[
                LLMOperator(
                    id="a", name="a", model="m",
                    prompt_template="p",
                    input_ports=[InputPort(name="input")],
                    output_ports=[OutputPort(name="text")],
                ),
            ],
            entry_points=["a"],
        )
        constraints = check_structure_constraints(
            g,
            {"min_nodes": 2, "node_types": ["llm_operator", "tool_operator"]},
        )
        assert constraints["match"] is False
        assert constraints["node_count_ok"] is False
        assert constraints["node_types_ok"] is False
        assert "tool_operator" in constraints["missing_node_types"]

    def test_codegen_runner_enforces_structure_constraints(self):
        fixture = {
            "_source_file": "test-constraints.json",
            "intent": "Test structure constraints",
            "family": "paper_writing",
            "variant": "simple",
            "expected": {
                "min_nodes": 2,
                "node_types": ["llm_operator", "tool_operator"],
                "topology": {
                    "has_loops": False,
                    "has_fan_out": False,
                    "has_tools": False,
                    "has_rag": False,
                    "has_human_in_loop": False,
                },
            },
        }
        mock_code = (
            'from dan.builder import workflow\n'
            'wf = workflow("structure_test")\n'
            'wf.llm("only_llm", prompt="Hello")\n'
            'graph = wf.build()\n'
        )
        results = run_codegen_evaluation(
            [fixture],
            mock_responses={"paper_writing_simple": mock_code},
        )
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].error_type == "structure_mismatch"
        assert results[0].error_message is not None
        assert "structure mismatch" in results[0].error_message

    def test_intent_runner_enforces_structure_constraints(self):
        fixture = {
            "_source_file": "test-constraints-intent.json",
            "intent": "Intent structure constraints",
            "family": "paper_writing",
            "variant": "simple",
            "expected": {
                "min_nodes": 2,
                "node_types": ["llm_operator", "tool_operator"],
                "topology": {
                    "has_loops": False,
                    "has_fan_out": False,
                    "has_tools": False,
                    "has_rag": False,
                    "has_human_in_loop": False,
                },
            },
        }
        results = run_intent_evaluation([fixture])
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].error_type == "structure_mismatch"
        assert results[0].error_message is not None
        assert "structure mismatch" in results[0].error_message

    def test_codegen_runner_sets_topology_mismatch_error_type(self):
        fixture = {
            "_source_file": "test-topology.json",
            "intent": "Topology mismatch case",
            "family": "paper_writing",
            "variant": "simple",
            "expected": {
                "min_nodes": 1,
                "node_types": ["llm_operator"],
                "topology": {
                    "has_loops": True,
                    "has_fan_out": False,
                    "has_tools": False,
                    "has_rag": False,
                    "has_human_in_loop": False,
                },
            },
        }
        mock_code = (
            'from dan.builder import workflow\n'
            'wf = workflow("topology_test")\n'
            'wf.llm("only_llm", prompt="Hello")\n'
            'graph = wf.build()\n'
        )
        results = run_codegen_evaluation(
            [fixture],
            mock_responses={"paper_writing_simple": mock_code},
        )
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].error_type == "topology_mismatch"


# ---------------------------------------------------------------------------
# 9. Mock response override works
# ---------------------------------------------------------------------------


class TestMockResponseOverride:
    def test_mock_response_used_when_provided(self):
        fixture = {
            "_source_file": "test.json",
            "intent": "Test intent",
            "family": "paper_writing",
            "variant": "simple",
            "expected": {
                "min_nodes": 2,
                "node_types": ["llm_operator"],
                "topology": {},
            },
        }
        mock_code = (
            'from dan.builder import workflow\n'
            'wf = workflow("mock_test")\n'
            'a = wf.llm("gen", prompt="Hello")\n'
            'b = wf.llm("out", prompt="Summarize")\n'
            'a >> b\n'
            'graph = wf.build()\n'
        )
        results = run_codegen_evaluation(
            [fixture],
            mock_responses={"paper_writing_simple": mock_code},
        )
        assert len(results) == 1
        assert results[0].path == "codegen"


# ---------------------------------------------------------------------------
# 10. Baseline file is valid JSON
# ---------------------------------------------------------------------------


class TestBaselineFile:
    def test_baseline_file_exists_and_is_valid(self):
        assert _BASELINE_PATH.exists(), f"Baseline not found: {_BASELINE_PATH}"
        baseline = json.loads(_BASELINE_PATH.read_text())
        assert "codegen" in baseline
        assert "intent" in baseline
        assert baseline["codegen"]["total"] == 18
        assert baseline["intent"]["total"] == 18
