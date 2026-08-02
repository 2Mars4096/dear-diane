"""Mutation quality CI — deterministic end-to-end scenarios for the graph mutation pipeline.

Tests exercise: MutationPlan → GraphMutator.apply() → Graph.model_validate() → validate_graph().
Every scenario asserts structural integrity, Pydantic parse success, and validation pass.
"""

from __future__ import annotations

import pytest

from dan.models.graph import Graph
from dan.worker.model import Worker
from dan.server.graph_mutator import (
    AddEdge,
    AddNode,
    EditNode,
    GraphMutator,
    MutationPlan,
    MutationResult,
    RemoveEdge,
    RemoveNode,
    SetNodePosition,
)
from dan.validation.graph import validate_graph

try:
    from dan.server.graph_mutator import ExpandPattern

    HAS_PATTERNS = True
except ImportError:
    HAS_PATTERNS = False


@pytest.fixture(autouse=True)
def _pin_legacy_mutation_quality_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep this legacy-shape quality basket stable under suite-wide env drift."""
    monkeypatch.delenv("DAN_WORKER_BUILDER", raising=False)


# ---------------------------------------------------------------------------
# Seed graphs
# ---------------------------------------------------------------------------


def _empty_graph() -> dict:
    return {
        "version": "dan_graph_v1",
        "metadata": {"name": "empty", "description": ""},
        "nodes": [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "shared_context": [],
        "artifact_refs": [],
    }


def _two_node_graph() -> dict:
    return {
        "version": "dan_graph_v1",
        "metadata": {"name": "test", "description": ""},
        "nodes": [
            {
                "id": "writer",
                "node_type": "llm_operator",
                "name": "Writer",
                "description": "",
                "input_ports": [{"name": "input", "schema": {}, "required": False}],
                "output_ports": [{"name": "text", "schema": {}}],
                "position": {"x": 0, "y": 0},
                "ui": {},
                "metadata": {},
                "model": "claude-sonnet-4-6",
                "prompt_template": "Write about: {input}",
                "system_prompt": "",
                "temperature": 0.7,
            },
            {
                "id": "formatter",
                "node_type": "code_operator",
                "name": "Formatter",
                "description": "",
                "input_ports": [{"name": "input", "schema": {}, "required": False}],
                "output_ports": [{"name": "result", "schema": {}}],
                "position": {"x": 200, "y": 0},
                "ui": {},
                "metadata": {},
                "code": "result = input.upper()",
                "language": "python",
                "sandbox_config": {},
            },
        ],
        "edges": [
            {
                "id": "writer.text->formatter.input",
                "edge_type": "data",
                "source_node_id": "writer",
                "source_port": "text",
                "target_node_id": "formatter",
                "target_port": "input",
                "ui": {},
                "metadata": {},
            },
        ],
        "sub_graphs": {},
        "entry_points": ["writer"],
        "exit_points": ["formatter"],
        "shared_context": [],
        "artifact_refs": [],
    }


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

_WARNING_KEYWORDS = ("warning", "deprecated", "untyped")


def _assert_mutation_valid(
    result: MutationResult,
    extra_ignore: tuple[str, ...] = (),
) -> Graph:
    """Assert that a mutation result is successful, parseable, and passes validation."""
    assert result.success, f"Apply failed: {result.errors}"
    assert result.new_graph is not None

    parsed = Graph.model_validate(result.new_graph)

    errors = validate_graph(parsed)
    ignore_patterns = _WARNING_KEYWORDS + extra_ignore
    fatal = [e for e in errors if not any(w in e.lower() for w in ignore_patterns)]
    assert not fatal, f"Validation errors: {fatal}"

    return parsed


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------


class TestMutationQuality:
    """Deterministic mutation scenarios covering the full apply → validate pipeline."""

    def test_add_single_llm_node_to_empty(self):
        plan = MutationPlan(operations=[
            AddNode(node_type="llm_operator", name="Writer"),
        ])
        result = GraphMutator().apply(_empty_graph(), plan)
        parsed = _assert_mutation_valid(result)

        assert len(parsed.nodes) == 1
        node = parsed.nodes[0]
        assert isinstance(node, Worker)
        assert node.id == "writer"
        assert any(p.name == "input" for p in node.input_ports)
        assert any(p.name == "text" for p in node.output_ports)
        assert node.llm_hints is not None

    def test_add_single_code_node_to_empty(self):
        plan = MutationPlan(operations=[
            AddNode(
                node_type="code_operator",
                name="Formatter",
                config={"code": "out = x"},
            ),
        ])
        result = GraphMutator().apply(_empty_graph(), plan)
        parsed = _assert_mutation_valid(result)

        assert len(parsed.nodes) == 1
        node = parsed.nodes[0]
        assert isinstance(node, Worker)
        assert node.id == "formatter"
        assert node.code == "out = x"
        assert any(p.name == "result" for p in node.output_ports)

    def test_add_and_connect_two_nodes(self):
        plan = MutationPlan(operations=[
            AddNode(node_type="llm_operator", name="Writer"),
            AddNode(node_type="llm_operator", name="Reviewer"),
            AddEdge(
                source_id="writer",
                source_port="text",
                target_id="reviewer",
                target_port="input",
            ),
        ])
        result = GraphMutator().apply(_empty_graph(), plan)
        parsed = _assert_mutation_valid(result)

        assert len(parsed.nodes) == 2
        assert len(parsed.edges) == 1
        assert "writer" in parsed.entry_points
        assert "reviewer" in parsed.exit_points

    def test_three_node_pipeline(self):
        plan = MutationPlan(operations=[
            AddNode(node_type="llm_operator", name="Drafter"),
            AddNode(node_type="llm_operator", name="Editor"),
            AddNode(node_type="llm_operator", name="Polisher"),
            AddEdge(
                source_id="drafter",
                source_port="text",
                target_id="editor",
                target_port="input",
            ),
            AddEdge(
                source_id="editor",
                source_port="text",
                target_id="polisher",
                target_port="input",
            ),
        ])
        result = GraphMutator().apply(_empty_graph(), plan)
        parsed = _assert_mutation_valid(result)

        assert len(parsed.nodes) == 3
        assert len(parsed.edges) == 2
        assert "drafter" in parsed.entry_points
        assert "polisher" in parsed.exit_points

    def test_add_gate_if_else(self):
        plan = MutationPlan(operations=[
            AddNode(
                node_type="gate",
                name="Decision Gate",
                config={"condition": "score > 0.8"},
            ),
        ])
        result = GraphMutator().apply(_empty_graph(), plan)
        parsed = _assert_mutation_valid(result)

        gate = parsed.node_by_id("decision-gate")
        assert gate is not None
        assert gate.gate_mode == "if_else"
        assert gate.condition == "score > 0.8"
        port_names = {p.name for p in gate.output_ports}
        assert "true" in port_names
        assert "false" in port_names

    def test_add_gate_while(self):
        plan = MutationPlan(operations=[
            AddNode(
                node_type="gate",
                name="Loop Guard",
                config={
                    "gate_mode": "while",
                    "condition": "len(results) < 3",
                    "output_ports": [
                        {"name": "continue", "schema": {}},
                        {"name": "done", "schema": {}},
                    ],
                },
            ),
        ])
        result = GraphMutator().apply(_empty_graph(), plan)
        parsed = _assert_mutation_valid(result)

        gate = parsed.node_by_id("loop-guard")
        assert gate is not None
        assert gate.gate_mode == "while"
        assert gate.max_iterations >= 1
        port_names = {p.name for p in gate.output_ports}
        assert "continue" in port_names
        assert "done" in port_names

    def test_add_rag_node_with_llm(self):
        plan = MutationPlan(operations=[
            AddNode(
                node_type="rag_operator",
                name="Retriever",
                config={"collection": "docs"},
            ),
            AddNode(node_type="llm_operator", name="Answerer"),
            AddEdge(
                source_id="retriever",
                source_port="chunks",
                target_id="answerer",
                target_port="input",
            ),
        ])
        result = GraphMutator().apply(_empty_graph(), plan)
        parsed = _assert_mutation_valid(result)

        assert len(parsed.nodes) == 2
        rag = parsed.node_by_id("retriever")
        assert rag is not None
        assert isinstance(rag, Worker)
        assert any(p.name == "chunks" for p in rag.output_ports)
        assert any(p.name == "scores" for p in rag.output_ports)
        assert rag.metadata["rag_collection"] == "docs"
        assert len(parsed.edges) == 1

    def test_add_validator_node(self):
        plan = MutationPlan(operations=[
            AddNode(node_type="validator", name="Checker"),
            AddEdge(
                source_id="writer",
                source_port="text",
                target_id="checker",
                target_port="data",
            ),
        ])
        result = GraphMutator().apply(_two_node_graph(), plan)
        parsed = _assert_mutation_valid(result)

        assert len(parsed.nodes) == 3
        validator = parsed.node_by_id("checker")
        assert validator is not None
        assert isinstance(validator, Worker)
        port_names = {p.name for p in validator.output_ports}
        assert "valid" in port_names
        assert "invalid" in port_names
        assert len(parsed.edges) == 2

    def test_add_input_node(self):
        plan = MutationPlan(operations=[
            AddNode(node_type="input", name="Inputs"),
        ])
        result = GraphMutator().apply(_empty_graph(), plan)
        parsed = _assert_mutation_valid(result)

        node = parsed.node_by_id("inputs")
        assert node is not None
        assert isinstance(node, Worker)
        assert any(p.name == "input" for p in node.output_ports)
        assert node.id in parsed.entry_points

    def test_remove_node_and_rewire(self):
        plan = MutationPlan(operations=[
            RemoveNode(node_id="formatter"),
            AddNode(node_type="llm_operator", name="Reviewer"),
            AddEdge(
                source_id="writer",
                source_port="text",
                target_id="reviewer",
                target_port="input",
            ),
        ])
        result = GraphMutator().apply(_two_node_graph(), plan)
        parsed = _assert_mutation_valid(result)

        assert len(parsed.nodes) == 2
        assert parsed.node_by_id("formatter") is None
        assert parsed.node_by_id("reviewer") is not None
        assert len(parsed.edges) == 1
        assert parsed.edges[0].target_node_id == "reviewer"

    def test_edit_node_prompt(self):
        plan = MutationPlan(operations=[
            EditNode(
                node_id="writer",
                updates={"prompt_template": "Write a poem about: {input}"},
            ),
        ])
        result = GraphMutator().apply(_two_node_graph(), plan)
        parsed = _assert_mutation_valid(result)

        writer = parsed.node_by_id("writer")
        assert writer.prompt_template == "Write a poem about: {input}"

    def test_edit_node_model(self):
        plan = MutationPlan(operations=[
            EditNode(node_id="writer", updates={"model": "gpt-4o"}),
        ])
        result = GraphMutator().apply(_two_node_graph(), plan)
        parsed = _assert_mutation_valid(result)

        writer = parsed.node_by_id("writer")
        assert writer.model == "gpt-4o"

    def test_set_positions(self):
        plan = MutationPlan(operations=[
            SetNodePosition(node_id="writer", x=100, y=200),
            SetNodePosition(node_id="formatter", x=300, y=400),
        ])
        result = GraphMutator().apply(_two_node_graph(), plan)
        parsed = _assert_mutation_valid(result)

        writer = parsed.node_by_id("writer")
        assert writer.position.x == 100.0
        assert writer.position.y == 200.0
        formatter = parsed.node_by_id("formatter")
        assert formatter.position.x == 300.0
        assert formatter.position.y == 400.0

    def test_remove_and_add_edge(self):
        plan = MutationPlan(operations=[
            AddNode(
                node_type="code_operator",
                name="Prettifier",
                config={"code": "out = s.strip()"},
            ),
            AddEdge(
                source_id="writer",
                source_port="text",
                target_id="prettifier",
                target_port="input",
            ),
            RemoveEdge(
                source_id="writer",
                source_port="text",
                target_id="formatter",
                target_port="input",
            ),
        ])
        result = GraphMutator().apply(_two_node_graph(), plan)
        parsed = _assert_mutation_valid(result)

        assert len(parsed.nodes) == 3
        assert len(parsed.edges) == 1
        assert parsed.edges[0].target_node_id == "prettifier"
        assert parsed.edges[0].source_node_id == "writer"

    def test_complex_five_node_workflow(self):
        plan = MutationPlan(operations=[
            AddNode(node_type="input", name="Workflow Input"),
            AddNode(node_type="llm_operator", name="Researcher"),
            AddNode(node_type="llm_operator", name="Writer"),
            AddNode(node_type="llm_operator", name="Reviewer"),
            AddNode(
                node_type="code_operator",
                name="Formatter",
                config={"code": "out = text.strip()"},
            ),
            AddEdge(
                source_id="workflow-input",
                source_port="input",
                target_id="researcher",
                target_port="input",
            ),
            AddEdge(
                source_id="researcher",
                source_port="text",
                target_id="writer",
                target_port="input",
            ),
            AddEdge(
                source_id="writer",
                source_port="text",
                target_id="reviewer",
                target_port="input",
            ),
            AddEdge(
                source_id="reviewer",
                source_port="text",
                target_id="formatter",
                target_port="input",
            ),
        ])
        result = GraphMutator().apply(_empty_graph(), plan)
        parsed = _assert_mutation_valid(result)

        assert len(parsed.nodes) == 5
        assert len(parsed.edges) == 4
        assert "workflow-input" in parsed.entry_points
        assert "formatter" in parsed.exit_points

        node_ids = {n.id for n in parsed.nodes}
        assert node_ids == {
            "workflow-input",
            "researcher",
            "writer",
            "reviewer",
            "formatter",
        }

    def test_add_for_each_with_body(self):
        plan = MutationPlan(operations=[
            AddNode(node_type="for_each", name="Processor"),
            AddNode(node_type="llm_operator", name="Item Handler"),
        ])
        result = GraphMutator().apply(_empty_graph(), plan)
        parsed = _assert_mutation_valid(result, extra_ignore=("sub-graph",))

        assert len(parsed.nodes) == 2
        fe = parsed.node_by_id("processor")
        assert fe is not None
        assert fe.node_type == "for_each"
        assert any(p.name == "items" for p in fe.input_ports)
        assert any(p.name == "results" for p in fe.output_ports)

    def test_add_router_node(self):
        plan = MutationPlan(operations=[
            AddNode(
                node_type="router",
                name="Smart Router",
                config={
                    "route_descriptions": {
                        "technical": "Route to technical review",
                        "editorial": "Route to editorial review",
                    },
                },
            ),
        ])
        result = GraphMutator().apply(_empty_graph(), plan)
        parsed = _assert_mutation_valid(result)

        router = parsed.node_by_id("smart-router")
        assert router is not None
        assert isinstance(router, Worker)
        assert "technical" in router.metadata["route_descriptions"]
        assert "editorial" in router.metadata["route_descriptions"]
        assert any(p.name == "route" for p in router.output_ports)

    def test_mixed_add_edit_position(self):
        plan = MutationPlan(operations=[
            AddNode(node_type="llm_operator", name="Reviewer"),
            EditNode(
                node_id="writer",
                updates={"prompt_template": "Compose a story about: {input}"},
            ),
            SetNodePosition(node_id="formatter", x=500, y=500),
        ])
        result = GraphMutator().apply(_two_node_graph(), plan)
        parsed = _assert_mutation_valid(result)

        assert len(parsed.nodes) == 3
        assert parsed.node_by_id("reviewer") is not None

        writer = parsed.node_by_id("writer")
        assert writer.prompt_template == "Compose a story about: {input}"

        formatter = parsed.node_by_id("formatter")
        assert formatter.position.x == 500.0
        assert formatter.position.y == 500.0
