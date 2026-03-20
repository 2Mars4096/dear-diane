"""Tests for advanced markdown features: composite agents and linked JSON Schema."""
from pathlib import Path

from dan.loader.compiler import compile_workflow
from dan.loader.parser import parse_agent_file

FIXTURES = Path(__file__).parent.parent / "fixtures" / "markdown"


class TestCompositeAgent:
    def test_parse_composite_agent(self):
        spec = parse_agent_file(FIXTURES / "composite" / "outer_composite.md")
        assert spec.agent_type == "composite"
        assert "inner_a" in spec.internal_agents
        assert "inner_b" in spec.internal_agents
        assert len(spec.internal_flow_lines) >= 1

    def test_compile_composite_workflow(self):
        r = compile_workflow(FIXTURES / "composite" / "workflow.md")
        assert r.graph is not None, f"Failed: {[d.message for d in r.diagnostics]}"

    def test_composite_creates_subgraph(self):
        r = compile_workflow(FIXTURES / "composite" / "workflow.md")
        assert r.graph is not None
        composite_nodes = [n for n in r.graph.nodes if n.node_type == "composite"]
        assert len(composite_nodes) == 1
        assert composite_nodes[0].body_graph in r.graph.sub_graphs

    def test_composite_subgraph_has_inner_nodes(self):
        r = compile_workflow(FIXTURES / "composite" / "workflow.md")
        assert r.graph is not None
        composite = [n for n in r.graph.nodes if n.node_type == "composite"][0]
        sub = r.graph.sub_graphs[composite.body_graph]
        inner_ids = {n.id for n in sub.nodes}
        assert "inner_a" in inner_ids
        assert "inner_b" in inner_ids

    def test_composite_subgraph_has_edges(self):
        r = compile_workflow(FIXTURES / "composite" / "workflow.md")
        assert r.graph is not None
        composite = [n for n in r.graph.nodes if n.node_type == "composite"][0]
        sub = r.graph.sub_graphs[composite.body_graph]
        assert len(sub.edges) >= 1


class TestLinkedJsonSchema:
    def test_parse_schema_port(self):
        spec = parse_agent_file(FIXTURES / "schema_agent.md")
        schema_ports = [p for p in spec.output_ports if p.schema_path]
        assert len(schema_ports) == 1
        assert schema_ports[0].schema_path == "schemas/outline.json"

    def test_compile_schema_workflow(self):
        r = compile_workflow(FIXTURES / "schema_workflow.md")
        assert r.graph is not None, f"Failed: {[d.message for d in r.diagnostics]}"

    def test_schema_loaded_into_port(self):
        r = compile_workflow(FIXTURES / "schema_workflow.md")
        assert r.graph is not None
        planner = r.graph.node_by_id("planner")
        assert planner is not None
        outline_ports = [p for p in planner.output_ports if p.name == "outline"]
        assert len(outline_ports) == 1
        schema = outline_ports[0].json_schema
        assert schema["type"] == "object"
        assert "sections" in schema.get("properties", {})

    def test_missing_schema_file_produces_diagnostic(self):
        """A schema reference to a non-existent file should produce an error."""
        import tempfile
        agent_md = (
            "---\ntype: llm\nmodel: test\n---\n\n"
            "> Returns: data (schema: nonexistent.json)\n\n"
            "test\n"
        )
        workflow_md = (
            "---\nname: test\nformat_version: 1\n---\n\n"
            "## Agents\n\n- [agent](agent.md)\n\n## Flow\n\n"
        )
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "agent.md").write_text(agent_md)
            (td / "workflow.md").write_text(workflow_md)
            r = compile_workflow(td / "workflow.md")
            error_msgs = [d.message for d in r.diagnostics if d.level == "error"]
            assert any("schema" in m.lower() or "nonexistent" in m.lower() for m in error_msgs)
