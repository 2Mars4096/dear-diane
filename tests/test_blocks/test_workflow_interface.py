"""Tests for derive_workflow_interface()."""

from __future__ import annotations

from dan.models.graph import Graph
from dan.utils.workflow_interface import WorkflowInterface, derive_workflow_interface


class TestDeriveInterface:
    def test_input_from_input_node(self, simple_workflow: Graph) -> None:
        iface = derive_workflow_interface(simple_workflow)
        assert isinstance(iface, WorkflowInterface)
        props = iface.input_schema.get("properties", {})
        assert "topic" in props
        assert props["topic"]["type"] == "string"
        assert "required" in iface.input_schema
        assert "topic" in iface.input_schema["required"]

    def test_output_from_exit_node(self, simple_workflow: Graph) -> None:
        iface = derive_workflow_interface(simple_workflow)
        props = iface.output_schema.get("properties", {})
        assert "result" in props

    def test_name_and_description(self, simple_workflow: Graph) -> None:
        iface = derive_workflow_interface(simple_workflow)
        assert iface.name == "simple-pipe"
        assert iface.description == "A→B pipeline"

    def test_placeholder_detection(self, placeholder_workflow: Graph) -> None:
        iface = derive_workflow_interface(placeholder_workflow)
        props = iface.input_schema.get("properties", {})
        assert "topic" in props
        assert "style" in props
        assert props["topic"]["type"] == "string"

    def test_human_node_detection(self, human_workflow: Graph) -> None:
        iface = derive_workflow_interface(human_workflow)
        assert iface.has_human_nodes is True

    def test_no_human_nodes(self, simple_workflow: Graph) -> None:
        iface = derive_workflow_interface(simple_workflow)
        assert iface.has_human_nodes is False

    def test_empty_graph(self) -> None:
        g = Graph.model_validate({
            "version": "dan_graph_v1",
            "metadata": {"name": "empty"},
            "nodes": [],
            "edges": [],
        })
        iface = derive_workflow_interface(g)
        assert iface.input_schema["properties"] == {}
        assert iface.output_schema["properties"] == {}
        assert iface.has_human_nodes is False

    def test_multi_entry_merges(self) -> None:
        """Multiple entry nodes with different input variables are merged."""
        g = Graph.model_validate({
            "version": "dan_graph_v1",
            "metadata": {"name": "multi"},
            "nodes": [
                {
                    "id": "i1",
                    "name": "in1",
                    "node_type": "input",
                    "variables": [{"name": "a", "type": "string"}],
                    "output_ports": [{"name": "result"}],
                },
                {
                    "id": "i2",
                    "name": "in2",
                    "node_type": "input",
                    "variables": [{"name": "b", "type": "number"}],
                    "output_ports": [{"name": "result"}],
                },
            ],
            "edges": [],
            "entry_points": ["i1", "i2"],
            "exit_points": ["i1", "i2"],
        })
        iface = derive_workflow_interface(g)
        props = iface.input_schema["properties"]
        assert "a" in props
        assert "b" in props
        assert props["b"]["type"] == "number"

    def test_multi_exit_merges(self) -> None:
        """Multiple exit nodes produce merged output schema."""
        g = Graph.model_validate({
            "version": "dan_graph_v1",
            "metadata": {"name": "multi-exit"},
            "nodes": [
                {
                    "id": "a",
                    "name": "a",
                    "node_type": "llm_operator",
                    "model": "test",
                    "prompt_template": "x",
                    "output_ports": [{"name": "result"}],
                },
                {
                    "id": "b",
                    "name": "b",
                    "node_type": "llm_operator",
                    "model": "test",
                    "prompt_template": "y",
                    "output_ports": [{"name": "summary"}],
                },
            ],
            "edges": [],
            "entry_points": ["a", "b"],
            "exit_points": ["a", "b"],
        })
        iface = derive_workflow_interface(g)
        props = iface.output_schema["properties"]
        assert "result" in props
        assert "summary" in props

    def test_inferred_entry_exit(self) -> None:
        """When entry/exit_points are empty, infer from edge topology."""
        g = Graph.model_validate({
            "version": "dan_graph_v1",
            "metadata": {"name": "inferred"},
            "nodes": [
                {
                    "id": "a",
                    "name": "a",
                    "node_type": "llm_operator",
                    "model": "test",
                    "prompt_template": "Go: {question}",
                    "input_ports": [{"name": "input"}],
                    "output_ports": [{"name": "result"}],
                },
                {
                    "id": "b",
                    "name": "b",
                    "node_type": "llm_operator",
                    "model": "test",
                    "prompt_template": "Refine",
                    "input_ports": [{"name": "input"}],
                    "output_ports": [{"name": "result"}],
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source_node_id": "a",
                    "source_port": "result",
                    "target_node_id": "b",
                    "target_port": "input",
                    "edge_type": "data",
                },
            ],
        })
        iface = derive_workflow_interface(g)
        assert "question" in iface.input_schema["properties"]
        assert "result" in iface.output_schema["properties"]

    def test_input_variable_default_and_description(self) -> None:
        g = Graph.model_validate({
            "version": "dan_graph_v1",
            "metadata": {"name": "defaults"},
            "nodes": [
                {
                    "id": "inp",
                    "name": "inp",
                    "node_type": "input",
                    "variables": [
                        {"name": "lang", "type": "string", "default": "en", "description": "Language code"},
                    ],
                    "output_ports": [{"name": "result"}],
                },
            ],
            "edges": [],
            "entry_points": ["inp"],
            "exit_points": ["inp"],
        })
        iface = derive_workflow_interface(g)
        lang = iface.input_schema["properties"]["lang"]
        assert lang["default"] == "en"
        assert lang["description"] == "Language code"
