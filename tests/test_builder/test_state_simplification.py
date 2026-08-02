"""Tests for builder DSL & serialization: state_schema, spread_edge, backward compat.

Covers Plan 7-6 items:
  7-8.  Backward compat — existing workflow with gates/edges still builds and validates.
  7-13. Builder DSL — state_schema / state_defaults on gate, spread / spread_edge on edges.
  Bonus: JSON serialization round-trips for GateNode and DataEdge new fields.
"""

import json

import pytest

from dan.builder import workflow
from dan.models.control_flow import GateNode
from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.validation.graph import validate_graph


_VALIDATION_WARNING_PATTERNS = (
    "schema safety bypassed",
    "untyped data edge",
    "deprecated",
)


def _fatal_errors(graph: Graph) -> list[str]:
    """Run validate_graph and filter out known non-fatal warnings."""
    errors = validate_graph(graph)
    return [
        e for e in errors
        if not any(p in e.lower() for p in _VALIDATION_WARNING_PATTERNS)
    ]


# ── 7-8. Backward compat: existing workflow builder still runs ─────────


class TestBackwardCompat:
    """Smoke tests: graphs built WITHOUT state_schema or spread still work."""

    def test_simple_gate_workflow_builds(self):
        wf = workflow("compat_test")
        start = wf.code("start", code="result = {'value': 1}")
        gate = wf.gate("check", condition="value > 0", gate_mode="if_else")
        end = wf.code("end", code="result = {'done': True}")
        start >> gate >> end
        graph = wf.build()

        assert len(graph.nodes) == 3
        assert graph.node_by_id("check") is not None
        assert graph.node_by_id("check").node_type == "gate"

    def test_simple_gate_workflow_model_validate(self):
        wf = workflow("compat_validate")
        start = wf.code("start", code="result = {'x': 1}")
        gate = wf.gate("g", condition="x > 0")
        end = wf.code("end", code="result = {'y': 2}")
        start >> gate >> end
        graph = wf.build()

        json_str = graph.model_dump_json()
        restored = Graph.model_validate_json(json_str)
        assert len(restored.nodes) == len(graph.nodes)
        assert restored.version == "dan_graph_v1"

    def test_simple_gate_workflow_no_fatal_validation_errors(self):
        wf = workflow("compat_valid")
        start = wf.code("start", code="result = {'v': 1}")
        gate = wf.gate("g", condition="v > 0", gate_mode="if_else")
        end = wf.code("end", code="result = {'done': True}")
        start >> gate >> end
        graph = wf.build()

        fatal = _fatal_errors(graph)
        assert fatal == [], f"Unexpected fatal errors: {fatal}"

    def test_while_gate_loop_backward_compat(self):
        """A while-mode gate with a back-edge cycle — pre-existing pattern."""
        wf = workflow("compat_while")
        init = wf.code("init", code="result = {'counter': 0}")
        gate = wf.gate("loop_gate", condition="counter < 5", gate_mode="while", max_iterations=10)
        body = wf.code("inc", code="result = {'counter': counter + 1}")

        init >> gate
        wf.edge(gate["continue"], body["input"])
        wf.edge(body["result"], gate["input"])
        wf.edge(gate["done"], wf.code("finish", code="result = {'ok': True}")["input"])

        graph = wf.build()
        fatal = _fatal_errors(graph)
        assert fatal == [], f"Unexpected fatal errors: {fatal}"

    def test_explicit_edge_without_spread_backward_compat(self):
        wf = workflow("compat_edge")
        a = wf.code("a", code="result = {'out': 1}")
        b = wf.code("b", code="result = {'out': 2}")
        wf.edge(a["result"], b["input"])
        graph = wf.build()

        data_edges = [e for e in graph.edges if isinstance(e, DataEdge)]
        assert len(data_edges) == 1
        assert data_edges[0].spread is False


# ── 7-13. Builder DSL: state_schema and spread_edge support ────────────


class TestGateStateSchema:
    """Builder DSL: wf.gate(..., state_schema=..., state_defaults=...)."""

    def test_gate_with_state_schema(self):
        wf = workflow("schema_test")
        schema = {"type": "object", "properties": {"counter": {"type": "integer"}}}
        defaults = {"counter": 0}

        gate = wf.gate(
            "my_gate",
            condition="counter < 3",
            gate_mode="while",
            state_schema=schema,
            state_defaults=defaults,
        )
        graph = wf.build()

        node = graph.node_by_id("my_gate")
        assert isinstance(node, GateNode)
        assert node.state_schema == {"counter": {"type": "integer"}}
        assert node.state_defaults == defaults

    def test_gate_without_state_schema_is_none(self):
        wf = workflow("no_schema")
        wf.gate("plain_gate", condition="True")
        graph = wf.build()

        node = graph.node_by_id("plain_gate")
        assert isinstance(node, GateNode)
        assert node.state_schema is None
        assert node.state_defaults is None

    def test_gate_state_schema_only_no_defaults(self):
        wf = workflow("schema_only")
        schema = {"type": "object", "properties": {"score": {"type": "number"}}}

        wf.gate("g", condition="score > 0.5", state_schema=schema)
        graph = wf.build()

        node = graph.node_by_id("g")
        assert node.state_schema == {"score": {"type": "number"}}
        assert node.state_defaults is None

    def test_gate_state_defaults_only_no_schema(self):
        wf = workflow("defaults_only")
        defaults = {"tries": 0}

        wf.gate("g", condition="tries < 3", state_defaults=defaults)
        graph = wf.build()

        node = graph.node_by_id("g")
        assert node.state_schema is None
        assert node.state_defaults == defaults

    def test_gate_state_schema_complex(self):
        wf = workflow("complex_schema")
        schema = {
            "type": "object",
            "properties": {
                "counter": {"type": "integer"},
                "best_score": {"type": "number"},
                "history": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["counter"],
        }
        defaults = {"counter": 0, "best_score": 0.0, "history": []}

        wf.gate("g", condition="counter < 10", state_schema=schema, state_defaults=defaults)
        graph = wf.build()

        node = graph.node_by_id("g")
        assert node.state_schema["best_score"]["type"] == "number"
        assert node.state_defaults["history"] == []


class TestSpreadEdge:
    """Builder DSL: wf.edge(..., spread=True) and wf.spread_edge(...)."""

    def test_edge_with_spread_true(self):
        wf = workflow("spread_test")
        a = wf.code("a", code="result = {'items': [1,2,3]}")
        b = wf.code("b", code="result = {'v': item}")
        wf.edge(a["items"], b["input"], spread=True)
        graph = wf.build()

        data_edges = [e for e in graph.edges if isinstance(e, DataEdge)]
        spread_edges = [e for e in data_edges if e.spread]
        assert len(spread_edges) == 1
        assert spread_edges[0].source_port == "items"

    def test_edge_default_spread_false(self):
        wf = workflow("no_spread")
        a = wf.code("a", code="result = {'out': 1}")
        b = wf.code("b", code="result = {'out': 2}")
        wf.edge(a["result"], b["input"])
        graph = wf.build()

        data_edges = [e for e in graph.edges if isinstance(e, DataEdge)]
        assert all(e.spread is False for e in data_edges)

    def test_spread_edge_shorthand(self):
        wf = workflow("spread_shorthand")
        a = wf.code("a", code="result = {'items': [1,2,3]}")
        b = wf.code("b", code="result = {'v': item}")
        wf.spread_edge(a["items"], b["input"])
        graph = wf.build()

        data_edges = [e for e in graph.edges if isinstance(e, DataEdge)]
        spread_edges = [e for e in data_edges if e.spread]
        assert len(spread_edges) == 1

    def test_spread_edge_equivalent_to_edge_spread_true(self):
        """spread_edge(src, tgt) should produce same edge as edge(src, tgt, spread=True)."""
        wf1 = workflow("w1")
        a1 = wf1.code("a", code="result = {'out': 1}")
        b1 = wf1.code("b", code="result = {'out': 2}")
        wf1.edge(a1["out"], b1["input"], spread=True)
        g1 = wf1.build()

        wf2 = workflow("w2")
        a2 = wf2.code("a", code="result = {'out': 1}")
        b2 = wf2.code("b", code="result = {'out': 2}")
        wf2.spread_edge(a2["out"], b2["input"])
        g2 = wf2.build()

        e1 = [e for e in g1.edges if isinstance(e, DataEdge)][0]
        e2 = [e for e in g2.edges if isinstance(e, DataEdge)][0]
        assert e1.spread == e2.spread is True
        assert e1.source_port == e2.source_port
        assert e1.target_port == e2.target_port

    def test_mixed_spread_and_normal_edges(self):
        wf = workflow("mixed")
        a = wf.code("a", code="result = {'items': [1,2], 'meta': 'x'}")
        b = wf.code("b", code="result = {'v': 1}")
        c = wf.code("c", code="result = {'v': 2}")
        wf.spread_edge(a["items"], b["input"])
        wf.edge(a["meta"], c["input"])
        graph = wf.build()

        data_edges = [e for e in graph.edges if isinstance(e, DataEdge)]
        assert len(data_edges) == 2
        spread_count = sum(1 for e in data_edges if e.spread)
        normal_count = sum(1 for e in data_edges if not e.spread)
        assert spread_count == 1
        assert normal_count == 1


# ── Bonus: JSON serialization ──────────────────────────────────────────


class TestGateNodeSerialization:
    """GateNode with state_schema round-trips through JSON."""

    def test_gate_with_state_schema_serializes(self):
        wf = workflow("ser_gate")
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
        defaults = {"n": 0}
        wf.gate("g", condition="n < 5", state_schema=schema, state_defaults=defaults)
        graph = wf.build()

        raw = graph.model_dump(mode="json")
        gate_data = next(n for n in raw["nodes"] if n["id"] == "g")
        assert gate_data["state_schema"] == {"n": {"type": "integer"}}
        assert gate_data["state_defaults"] == defaults

    def test_gate_without_state_schema_serializes_as_null(self):
        wf = workflow("ser_gate_none")
        wf.gate("g", condition="True")
        graph = wf.build()

        raw = graph.model_dump(mode="json")
        gate_data = next(n for n in raw["nodes"] if n["id"] == "g")
        assert gate_data["state_schema"] is None
        assert gate_data["state_defaults"] is None

    def test_gate_json_round_trip(self):
        wf = workflow("rt_gate")
        schema = {"type": "object", "properties": {"x": {"type": "number"}}}
        defaults = {"x": 1.0}
        wf.gate("g", condition="x > 0", state_schema=schema, state_defaults=defaults)
        graph = wf.build()

        json_str = graph.model_dump_json()
        restored = Graph.model_validate_json(json_str)
        node = restored.node_by_id("g")
        assert isinstance(node, GateNode)
        assert node.state_schema == {"x": {"type": "number"}}
        assert node.state_defaults == defaults


class TestDataEdgeSerialization:
    """DataEdge spread field round-trips through JSON."""

    def test_spread_edge_serializes(self):
        wf = workflow("ser_spread")
        a = wf.code("a", code="result = {'items': [1]}")
        b = wf.code("b", code="result = {'v': 1}")
        wf.spread_edge(a["items"], b["input"])
        graph = wf.build()

        raw = graph.model_dump(mode="json")
        data_edges = [e for e in raw["edges"] if e["edge_type"] == "data"]
        spread_edges = [e for e in data_edges if e.get("spread") is True]
        assert len(spread_edges) == 1

    def test_non_spread_edge_serializes_as_false(self):
        wf = workflow("ser_no_spread")
        a = wf.code("a", code="result = {'out': 1}")
        b = wf.code("b", code="result = {'v': 1}")
        wf.edge(a["result"], b["input"])
        graph = wf.build()

        raw = graph.model_dump(mode="json")
        data_edges = [e for e in raw["edges"] if e["edge_type"] == "data"]
        assert len(data_edges) == 1
        assert data_edges[0]["spread"] is False

    def test_spread_edge_json_round_trip(self):
        wf = workflow("rt_spread")
        a = wf.code("a", code="result = {'items': [1,2]}")
        b = wf.code("b", code="result = {'v': 1}")
        wf.spread_edge(a["items"], b["input"])
        graph = wf.build()

        json_str = graph.model_dump_json()
        restored = Graph.model_validate_json(json_str)

        data_edges = [e for e in restored.edges if isinstance(e, DataEdge)]
        assert any(e.spread is True for e in data_edges)

    def test_full_graph_with_both_features_round_trips(self):
        """End-to-end: gate with state_schema + spread edge, serialize and restore."""
        wf = workflow("full_rt")
        src = wf.code("src", code="result = {'items': [1,2,3]}")
        gate = wf.gate(
            "g",
            condition="counter < len(items)",
            gate_mode="while",
            state_schema={"counter": {"type": "integer"}},
            state_defaults={"counter": 0},
        )
        sink = wf.code("sink", code="result = {'done': True}")

        wf.spread_edge(src["items"], gate["input"])
        wf.edge(gate["done"], sink["input"])
        graph = wf.build()

        json_str = graph.model_dump_json()
        restored = Graph.model_validate_json(json_str)

        gate_node = restored.node_by_id("g")
        assert isinstance(gate_node, GateNode)
        assert gate_node.state_schema is not None
        assert gate_node.state_defaults == {"counter": 0}

        data_edges = [e for e in restored.edges if isinstance(e, DataEdge)]
        assert any(e.spread is True for e in data_edges)
        assert any(e.spread is False for e in data_edges)
