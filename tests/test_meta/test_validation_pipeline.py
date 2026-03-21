"""Tests for the validation pipeline: validate_codegen_output().

Exercises schema validation, design-time checks, and error classification
using real graph dicts built from the builder DSL.
"""

from __future__ import annotations

import copy

import pytest

from dan.builder import workflow
from dan.meta.diagnosis import GenerationErrorType
from dan.meta.planner import ValidationResult, validate_codegen_output
from dan.models.graph import Graph


# ---------------------------------------------------------------------------
# Fixtures — graph dicts built from the builder DSL
# ---------------------------------------------------------------------------


def _build_valid_chain_dict() -> dict:
    """Two-node LLM chain with typed ports — passes all validation cleanly."""
    wf = workflow("test_valid_chain")
    a = wf.llm(
        "step_a",
        prompt="Hello {topic}",
        input_ports=[{"name": "topic", "json_schema": {"type": "string"}}],
        output_ports=[{"name": "text", "json_schema": {"type": "string"}}],
    )
    b = wf.llm(
        "step_b",
        prompt="Expand: {text}",
        input_ports=[{"name": "text", "json_schema": {"type": "string"}}],
        output_ports=[{"name": "text", "json_schema": {"type": "string"}}],
    )
    wf.edge(a["text"], b["text"])
    graph = wf.build()
    return graph.model_dump(mode="json")


def _build_untyped_chain_dict() -> dict:
    """Two-node chain with untyped ports — triggers 'untyped data edge' warnings."""
    wf = workflow("test_untyped")
    a = wf.llm("step_a", prompt="Hello")
    b = wf.llm("step_b", prompt="World")
    a >> b
    graph = wf.build()
    return graph.model_dump(mode="json")


def _build_chain_with_unreachable_dict() -> dict:
    """Valid chain plus an orphan node that nothing connects to."""
    d = _build_valid_chain_dict()
    d["nodes"].append(
        {
            "id": "orphan",
            "node_type": "llm_operator",
            "name": "orphan",
            "model": "",
            "prompt_template": "",
            "system_prompt": "",
            "temperature": 0.7,
            "input_ports": [],
            "output_ports": [],
        }
    )
    return d


def _build_cycle_dict() -> dict:
    """Two-node graph with a data-edge cycle through non-loop nodes."""
    d = _build_valid_chain_dict()
    d["edges"].append(
        {
            "id": "back_edge",
            "edge_type": "data",
            "source_node_id": "step_b",
            "source_port": "text",
            "target_node_id": "step_a",
            "target_port": "topic",
        }
    )
    return d


def _build_empty_graph_dict() -> dict:
    """Minimal valid graph with no nodes — valid but degenerate."""
    wf = workflow("empty")
    graph = wf.build()
    return graph.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Test: Valid graph → success=True, graph=Graph
# ---------------------------------------------------------------------------


class TestValidGraph:
    def test_success_true(self):
        result = validate_codegen_output(_build_valid_chain_dict())
        assert isinstance(result, ValidationResult)
        assert result.success is True
        assert result.graph is not None
        assert isinstance(result.graph, Graph)

    def test_no_errors(self):
        result = validate_codegen_output(_build_valid_chain_dict())
        assert len(result.errors) == 0
        assert len(result.fatal_errors) == 0
        assert len(result.recoverable_errors) == 0

    def test_graph_preserves_nodes(self):
        result = validate_codegen_output(_build_valid_chain_dict())
        node_ids = {n.id for n in result.graph.nodes}
        assert "step_a" in node_ids
        assert "step_b" in node_ids


# ---------------------------------------------------------------------------
# Test: Invalid schema → success=False, fatal_errors non-empty
# ---------------------------------------------------------------------------


class TestInvalidSchema:
    def test_bogus_node_type_fails(self):
        d = {"nodes": [{"id": "x", "node_type": "nonexistent_type"}]}
        result = validate_codegen_output(d)
        assert result.success is False
        assert len(result.fatal_errors) > 0
        assert result.graph is None
        assert all(
            e.error_type == GenerationErrorType.schema_mismatch
            for e in result.fatal_errors
        )

    def test_nodes_wrong_type_fails(self):
        d = {"version": "dan_graph_v1", "nodes": "not_a_list"}
        result = validate_codegen_output(d)
        assert result.success is False
        assert len(result.fatal_errors) > 0
        assert result.graph is None

    def test_fatal_errors_are_not_recoverable(self):
        d = {"nodes": [{"id": "x", "node_type": "nonexistent_type"}]}
        result = validate_codegen_output(d)
        for err in result.fatal_errors:
            assert err.recoverable is False


# ---------------------------------------------------------------------------
# Test: Graph with unreachable node → success=False, recoverable_errors
# ---------------------------------------------------------------------------


class TestUnreachableNode:
    def test_unreachable_detected(self):
        result = validate_codegen_output(_build_chain_with_unreachable_dict())
        assert result.success is False
        assert len(result.recoverable_errors) > 0

    def test_reachability_error_type(self):
        result = validate_codegen_output(_build_chain_with_unreachable_dict())
        reachability = [
            e
            for e in result.recoverable_errors
            if e.error_type == GenerationErrorType.reachability
        ]
        assert len(reachability) > 0
        assert any("orphan" in e.message for e in reachability)

    def test_graph_still_returned(self):
        """Recoverable errors don't null out the graph (no fatal errors)."""
        result = validate_codegen_output(_build_chain_with_unreachable_dict())
        assert result.graph is not None
        assert len(result.fatal_errors) == 0


# ---------------------------------------------------------------------------
# Test: Graph with warning only → success=True, warnings non-empty
# ---------------------------------------------------------------------------


class TestWarningOnly:
    def test_success_with_warnings(self):
        result = validate_codegen_output(_build_untyped_chain_dict())
        assert result.success is True
        assert len(result.warnings) > 0

    def test_untyped_edge_warning_message(self):
        result = validate_codegen_output(_build_untyped_chain_dict())
        assert any("untyped" in w.lower() for w in result.warnings)

    def test_no_classified_errors(self):
        result = validate_codegen_output(_build_untyped_chain_dict())
        assert len(result.errors) == 0
        assert len(result.fatal_errors) == 0
        assert len(result.recoverable_errors) == 0


# ---------------------------------------------------------------------------
# Test: Graph with cycle → fatal_errors (not recoverable)
# ---------------------------------------------------------------------------


class TestCycleError:
    def test_cycle_detected_as_fatal(self):
        result = validate_codegen_output(_build_cycle_dict())
        assert result.success is False
        cycle_errors = [
            e for e in result.fatal_errors if e.error_type == GenerationErrorType.cycle
        ]
        assert len(cycle_errors) > 0

    def test_cycle_errors_not_recoverable(self):
        result = validate_codegen_output(_build_cycle_dict())
        cycle_errors = [
            e for e in result.errors if e.error_type == GenerationErrorType.cycle
        ]
        for err in cycle_errors:
            assert err.recoverable is False

    def test_graph_nulled_on_fatal(self):
        result = validate_codegen_output(_build_cycle_dict())
        assert result.graph is None


# ---------------------------------------------------------------------------
# Test: Empty graph (valid but degenerate) → success=True
# ---------------------------------------------------------------------------


class TestEmptyGraph:
    def test_empty_graph_succeeds(self):
        result = validate_codegen_output(_build_empty_graph_dict())
        assert result.success is True
        assert result.graph is not None
        assert result.run_ready is False

    def test_empty_graph_has_no_nodes(self):
        result = validate_codegen_output(_build_empty_graph_dict())
        assert len(result.graph.nodes) == 0
        assert len(result.graph.edges) == 0

    def test_empty_graph_no_errors(self):
        result = validate_codegen_output(_build_empty_graph_dict())
        assert len(result.errors) == 0
        assert len(result.warnings) == 0
