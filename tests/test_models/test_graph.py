"""Graph container tests — construction, lookup helpers, and JSON round-trips."""

import json

from dan.models.graph import Graph, GraphMetadata, Node, Edge
from dan.models.nodes import LLMOperator
from dan.models.edges import DataEdge
from dan.models.ports import InputPort, OutputPort
from dan.models.context import SharedContextDeclaration


def _make_chain() -> Graph:
    """Two LLM nodes connected by a data edge."""
    a = LLMOperator(
        id="a",
        name="Generator",
        model="gpt-4o",
        prompt_template="Generate: {topic}",
        output_ports=[OutputPort(name="text", json_schema={"type": "string"})],
    )
    b = LLMOperator(
        id="b",
        name="Refiner",
        model="gpt-4o",
        prompt_template="Refine: {text}",
        input_ports=[InputPort(name="text", json_schema={"type": "string"})],
        output_ports=[OutputPort(name="refined", json_schema={"type": "string"})],
    )
    edge = DataEdge(id="e1", source_node_id="a", source_port="text", target_node_id="b", target_port="text")
    return Graph(
        metadata=GraphMetadata(name="chain"),
        nodes=[a, b],
        edges=[edge],
        entry_points=["a"],
        exit_points=["b"],
    )


class TestGraphConstruction:
    def test_empty_graph(self):
        g = Graph()
        assert g.version == "dan_graph_v1"
        assert g.nodes == []
        assert g.edges == []

    def test_chain(self):
        g = _make_chain()
        assert len(g.nodes) == 2
        assert len(g.edges) == 1

    def test_node_by_id(self):
        g = _make_chain()
        assert g.node_by_id("a") is not None
        assert g.node_by_id("a").name == "Generator"
        assert g.node_by_id("missing") is None

    def test_edges_from_to(self):
        g = _make_chain()
        assert len(g.edges_from("a")) == 1
        assert len(g.edges_to("b")) == 1
        assert len(g.edges_from("b")) == 0


class TestGraphSerialization:
    def test_json_round_trip(self):
        g = _make_chain()
        json_str = g.model_dump_json()
        restored = Graph.model_validate_json(json_str)
        assert restored.version == "dan_graph_v1"
        assert len(restored.nodes) == 2
        assert len(restored.edges) == 1
        assert restored.node_by_id("a").node_type == "llm_operator"

    def test_dict_round_trip(self):
        g = _make_chain()
        data = g.model_dump()
        restored = Graph.model_validate(data)
        assert restored == g

    def test_version_present_in_json(self):
        g = Graph()
        data = json.loads(g.model_dump_json())
        assert data["version"] == "dan_graph_v1"

    def test_discriminated_union_deserialization(self):
        """Nodes deserialise to the correct concrete type based on node_type."""
        g = _make_chain()
        data = g.model_dump()
        restored = Graph.model_validate(data)
        node_a = restored.node_by_id("a")
        assert isinstance(node_a, LLMOperator)
        assert node_a.model == "gpt-4o"

    def test_shared_context_in_json(self):
        g = Graph(
            shared_context=[SharedContextDeclaration(key="outline", json_schema={"type": "string"})],
        )
        data = g.model_dump()
        assert len(data["shared_context"]) == 1
        assert data["shared_context"][0]["key"] == "outline"


class TestSubGraphs:
    def test_sub_graph_round_trip(self):
        inner = _make_chain()
        outer = Graph(
            metadata=GraphMetadata(name="outer"),
            sub_graphs={"inner_chain": inner},
        )
        data = outer.model_dump()
        restored = Graph.model_validate(data)
        assert "inner_chain" in restored.sub_graphs
        assert len(restored.sub_graphs["inner_chain"].nodes) == 2
