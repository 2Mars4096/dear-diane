"""Unit tests for the builder compiler — marker resolution, port generation, edge wiring."""

import pytest

from dan.builder import workflow, BuildError
from dan.builder.compiler import DEFAULT_OUTPUT_PORTS, default_output_port
from dan.models.edges import DataEdge


class TestOutputContractMap:
    def test_all_node_types_covered(self):
        expected = {
            "worker",
            "llm_operator", "tool_operator", "code_operator",
            "if_else", "gate", "while_loop", "for_each", "parallel_subagents",
            "reduce", "router", "human_in_the_loop", "composite",
            "rag_operator", "validator", "orchestrator", "reflection",
            "goal_loop", "agent_team", "human", "input", "vote",
        }
        assert set(DEFAULT_OUTPUT_PORTS.keys()) == expected

    def test_llm_default_is_text(self):
        assert DEFAULT_OUTPUT_PORTS["llm_operator"] == "text"

    def test_foreach_default_is_results(self):
        assert DEFAULT_OUTPUT_PORTS["for_each"] == "results"

    def test_if_else_default_is_true(self):
        assert DEFAULT_OUTPUT_PORTS["if_else"] == "true"


class TestGateWhileModeDefaultOutput:
    """Plan 7-8 Task 3: gate >> body uses 'continue' for while-mode gates."""

    def test_gate_while_mode_rshift_uses_continue_port(self):
        """gate >> body with gate_mode='while' wires gate:continue -> body:input."""
        wf = workflow("while_chain")
        gate = wf.gate("loop_gate", condition="counter < 3", gate_mode="while")
        body = wf.code("body", code="result = counter + 1", input_ports=[{"name": "counter"}])
        # Use explicit port for body since default is "input" but we want "counter"
        wf.edge(gate["continue"], body["counter"])
        graph = wf.build()

        gate_to_body = [
            e for e in graph.edges
            if e.source_node_id == "loop_gate" and e.target_node_id == "body"
        ]
        assert len(gate_to_body) == 1
        assert gate_to_body[0].source_port == "continue"
        assert gate_to_body[0].target_port == "counter"

    def test_gate_while_mode_rshift_default_chain(self):
        """gate >> body with gate_mode='while' uses continue when chaining (body has default input)."""
        wf = workflow("while_chain")
        src = wf.code("src", code="result = 0", output_ports=[{"name": "value"}])
        gate = wf.gate("loop_gate", condition="counter < 3", gate_mode="while",
                       input_ports=[{"name": "counter"}])
        body = wf.code("body", code="result = input")  # default input port "input"
        wf.edge(src["value"], gate["counter"])
        gate >> body
        graph = wf.build()

        gate_to_body = [
            e for e in graph.edges
            if e.source_node_id == "loop_gate" and e.target_node_id == "body"
        ]
        assert len(gate_to_body) == 1
        assert gate_to_body[0].source_port == "continue"
        assert gate_to_body[0].target_port == "input"

    def test_gate_if_else_mode_rshift_uses_true_port(self):
        """gate >> body with gate_mode='if_else' wires gate:true -> body:input."""
        wf = workflow("if_chain")
        src = wf.code("src", code="result = 1", output_ports=[{"name": "value"}])
        gate = wf.gate("check", condition="x > 0", gate_mode="if_else",
                       input_ports=[{"name": "x"}])
        body = wf.code("body", code="result = input")
        wf.edge(src["value"], gate["x"])
        gate >> body
        graph = wf.build()

        gate_to_body = [
            e for e in graph.edges
            if e.source_node_id == "check" and e.target_node_id == "body"
        ]
        assert len(gate_to_body) == 1
        assert gate_to_body[0].source_port == "true"

    def test_default_output_port_while_mode_returns_continue(self):
        """default_output_port('gate', gate_mode='while') -> 'continue'."""
        assert default_output_port("gate", gate_mode="while") == "continue"

    def test_default_output_port_if_else_mode_returns_true(self):
        """default_output_port('gate', gate_mode='if_else') -> 'true'."""
        assert default_output_port("gate", gate_mode="if_else") == "true"

    def test_default_output_port_if_else_node_returns_true(self):
        """default_output_port('if_else') -> 'true' for the canonical branch node."""
        assert default_output_port("if_else") == "true"

    def test_default_output_port_gate_no_mode_returns_true(self):
        """default_output_port('gate') without gate_mode -> 'true' (if_else default)."""
        assert default_output_port("gate") == "true"


class TestSimpleChainCompilation:
    def test_two_node_chain_with_rshift(self):
        wf = workflow("test")
        a = wf.llm("a", model="gpt-4o", prompt="Hello {topic}")
        b = wf.llm("b", prompt="Refine")
        a >> b
        graph = wf.build()

        assert len(graph.nodes) == 2
        assert len(graph.edges) == 1
        edge = graph.edges[0]
        assert isinstance(edge, DataEdge)
        assert edge.source_node_id == "a"
        assert edge.source_port == "text"
        assert edge.target_node_id == "b"
        assert edge.target_port == "input"

    def test_three_node_chain(self):
        wf = workflow("test")
        a = wf.llm("a", model="m", prompt="A")
        b = wf.llm("b", prompt="B")
        c = wf.llm("c", prompt="C")
        a >> b >> c
        graph = wf.build()

        assert len(graph.nodes) == 3
        assert len(graph.edges) == 2
        assert graph.entry_points == ["a"]
        assert graph.exit_points == ["c"]


class TestFStringMagic:
    def test_marker_creates_edge_and_port(self):
        wf = workflow("test")
        ideas = wf.llm("idea_gen", model="m", prompt="Ideas")
        outline = wf.llm("planner", prompt=f"Plan for: {ideas}")
        graph = wf.build()

        assert len(graph.edges) == 1
        edge = graph.edges[0]
        assert edge.source_node_id == "idea_gen"
        assert edge.source_port == "text"
        assert edge.target_node_id == "planner"

        planner = graph.node_by_id("planner")
        port_names = {p.name for p in planner.input_ports}
        assert "idea_gen" in port_names

        assert "idea_gen" in planner.prompt_template
        assert "<<dan:" not in planner.prompt_template

    def test_multiple_refs_in_prompt(self):
        wf = workflow("test")
        a = wf.llm("src_a", model="m", prompt="A")
        b = wf.llm("src_b", model="m", prompt="B")
        c = wf.llm("consumer", prompt=f"Use {a} and {b}")
        graph = wf.build()

        assert len(graph.edges) == 2
        consumer = graph.node_by_id("consumer")
        port_names = {p.name for p in consumer.input_ports}
        assert "src_a" in port_names
        assert "src_b" in port_names

    def test_portref_in_fstring(self):
        wf = workflow("test")
        a = wf.llm("gen", model="m", prompt="Generate")
        b = wf.llm("use", prompt=f"Use: {a['sections']}")
        graph = wf.build()

        edge = graph.edges[0]
        assert edge.source_port == "sections"


class TestExplicitEdge:
    def test_edge_method(self):
        wf = workflow("test")
        a = wf.llm("a", model="m", prompt="A")
        b = wf.llm("b", prompt="B")
        wf.edge(a["custom_out"], b["custom_in"])
        graph = wf.build()

        edge = graph.edges[0]
        assert edge.source_port == "custom_out"
        assert edge.target_port == "custom_in"


class TestAutoPortGeneration:
    def test_missing_ports_auto_created(self):
        wf = workflow("test")
        a = wf.code("a", code="result = {'value': 1}")
        b = wf.code("b", code="result = {'value': value * 2}")
        a >> b
        graph = wf.build()

        node_a = graph.node_by_id("a")
        node_b = graph.node_by_id("b")
        out_ports = {p.name for p in node_a.output_ports}
        in_ports = {p.name for p in node_b.input_ports}
        assert "result" in out_ports
        assert "input" in in_ports


class TestEntryExitPoints:
    def test_auto_detected(self):
        wf = workflow("test")
        a = wf.llm("first", model="m", prompt="A")
        b = wf.llm("mid", prompt="B")
        c = wf.llm("last", prompt="C")
        a >> b >> c
        graph = wf.build()

        assert graph.entry_points == ["first"]
        assert graph.exit_points == ["last"]

    def test_parallel_entries(self):
        wf = workflow("test")
        a = wf.llm("a", model="m", prompt="A")
        b = wf.llm("b", model="m", prompt="B")
        c = wf.llm("c", prompt="C")
        a >> c
        b >> c
        graph = wf.build()

        assert sorted(graph.entry_points) == ["a", "b"]
        assert graph.exit_points == ["c"]


class TestDuplicateNodeId:
    def test_raises(self):
        wf = workflow("test")
        wf.llm("dup", model="m", prompt="A")
        with pytest.raises(BuildError, match="Duplicate"):
            wf.llm("dup", model="m", prompt="B")


class TestSharedContext:
    def test_context_declaration(self):
        wf = workflow("test")
        wf.context("outline", json_schema={"type": "string"}, description="Paper outline")
        wf.llm("node", model="m", prompt="Hi")
        graph = wf.build()

        assert len(graph.shared_context) == 1
        assert graph.shared_context[0].key == "outline"


class TestBuildIdempotence:
    def test_repeated_build_is_stable(self):
        wf = workflow("idempotent")
        a = wf.llm("a", model="m", prompt="A")
        wf.llm("b", prompt=f"Use {a}")

        g1 = wf.build()
        g2 = wf.build()

        assert len(g1.edges) == 1
        assert len(g2.edges) == 1
        assert g1.edges[0].source_node_id == g2.edges[0].source_node_id
        assert g1.edges[0].target_node_id == g2.edges[0].target_node_id
        assert g2.node_by_id("b").prompt_template == "Use {a}"
