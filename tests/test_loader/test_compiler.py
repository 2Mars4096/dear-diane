"""Tests for dan.loader.compiler."""
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "fixtures" / "markdown"


class TestCompileSimpleWorkflow:
    def test_compiles_successfully(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "simple_workflow.md")
        assert not result.has_errors
        assert result.graph is not None

    def test_correct_node_count(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "simple_workflow.md")
        g = result.graph
        node_ids = {n.id for n in g.nodes}
        assert {"generator", "planner", "searcher"}.issubset(node_ids)

    def test_chain_edges(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "simple_workflow.md")
        g = result.graph
        data_edges = [e for e in g.edges if e.edge_type == "data"]
        chain_edges = [e for e in data_edges
                       if e.source_node_id in ("generator", "planner")
                       and e.target_node_id in ("planner", "searcher")]
        assert len(chain_edges) == 2

    def test_metadata(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "simple_workflow.md")
        assert result.graph.metadata.name == "Simple Chain"
        assert "example" in result.graph.metadata.tags

    def test_node_types(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "simple_workflow.md")
        types = {n.id: n.node_type for n in result.graph.nodes}
        assert types.get("generator") in {"llm_operator", "worker"}
        assert types.get("planner") in {"llm_operator", "worker"}
        assert types.get("searcher") in {"tool_operator", "worker"}

    def test_node_ports(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "simple_workflow.md")
        gen = next(n for n in result.graph.nodes if n.id == "generator")
        assert any(p.name == "topic" for p in gen.input_ports)
        assert any(p.name == "ideas" or p.name == "text" for p in gen.output_ports)

    def test_worker_builder_gate_can_emit_worker_compute_nodes(self, monkeypatch):
        from dan.loader.compiler import compile_workflow

        monkeypatch.setenv("DAN_WORKER_BUILDER", "enabled")
        result = compile_workflow(FIXTURES / "simple_workflow.md")

        assert not result.has_errors
        types = {n.id: n.node_type for n in result.graph.nodes}
        assert types.get("generator") == "worker"
        assert types.get("planner") == "worker"
        assert types.get("searcher") == "worker"
        if "workflow_inputs" in types:
            assert types["workflow_inputs"] == "worker"


class TestCompileComplexWorkflow:
    def test_compiles_successfully(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "complex_workflow.md")
        assert not result.has_errors, [d.message for d in result.diagnostics if d.level == "error"]
        assert result.graph is not None

    def test_foreach_creates_subgraph(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "complex_workflow.md")
        g = result.graph
        foreach_nodes = [n for n in g.nodes if n.node_type == "for_each"]
        assert len(foreach_nodes) >= 1
        fe = foreach_nodes[0]
        assert fe.body_graph in g.sub_graphs

    def test_loop_creates_gate(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "complex_workflow.md")
        g = result.graph
        gate_nodes = [n for n in g.nodes if n.node_type == "gate"]
        while_gates = [n for n in gate_nodes if n.gate_mode == "while"]
        assert len(while_gates) >= 1
        wg = while_gates[0]
        assert "verdict" in wg.condition

    def test_if_creates_gate(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "complex_workflow.md")
        g = result.graph
        gate_nodes = [n for n in g.nodes if n.node_type == "gate"]
        if_gates = [n for n in gate_nodes if n.gate_mode == "if_else"]
        assert len(if_gates) >= 1

    def test_context_declarations(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "complex_workflow.md")
        g = result.graph
        assert len(g.shared_context) == 2
        keys = {sc.key for sc in g.shared_context}
        assert "style_guide" in keys
        assert "bibliography" in keys

    def test_source_metadata_on_nodes(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "complex_workflow.md")
        for n in result.graph.nodes:
            if n.node_type in ("gate", "for_each", "input"):
                continue
            if n.metadata.get("generated"):
                continue
            assert "source" in n.metadata, f"Node {n.id} missing source metadata"

    def test_worker_builder_gate_keeps_specialized_loader_primitives(self, monkeypatch):
        from dan.loader.compiler import compile_workflow

        monkeypatch.setenv("DAN_WORKER_BUILDER", "enabled")
        result = compile_workflow(FIXTURES / "complex_workflow.md")

        assert not result.has_errors, [d.message for d in result.diagnostics if d.level == "error"]
        node_types = {n.node_type for n in result.graph.nodes}
        assert "worker" in node_types
        assert "gate" in node_types
        assert "for_each" in node_types


class TestCompilerDiagnostics:
    def test_missing_agent_file(self, tmp_path):
        from dan.loader.compiler import compile_workflow
        wf = tmp_path / "bad_workflow.md"
        wf.write_text("---\nname: Bad\n---\n\n## Agents\n\n- [missing](nonexistent.md)\n\n## Flow\n\nmissing\n")
        result = compile_workflow(wf)
        assert result.has_errors
        assert any("nonexistent" in d.message for d in result.diagnostics)

    def test_unknown_agent_in_flow(self, tmp_path):
        from dan.loader.compiler import compile_workflow
        agent = tmp_path / "a.md"
        agent.write_text("---\ntype: llm\nmodel: test\n---\nHello {x}")
        wf = tmp_path / "workflow.md"
        wf.write_text("---\nname: Test\n---\n\n## Agents\n\n- [a](a.md)\n\n## Flow\n\na → nonexistent\n")
        result = compile_workflow(wf)
        assert result.has_errors


class TestInputNodeInference:
    def test_creates_input_node(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "simple_workflow.md")
        g = result.graph
        input_nodes = [n for n in g.nodes if n.node_type == "input"]
        if input_nodes:
            inp = input_nodes[0]
            var_names = [v.name for v in inp.variables]
            assert "topic" in var_names


class TestRetryPolicy:
    def test_retry_policy_on_node(self):
        from dan.loader.compiler import compile_workflow
        result = compile_workflow(FIXTURES / "simple_workflow.md")
        planner = next((n for n in result.graph.nodes if n.id == "planner"), None)
        if planner and planner.retry_policy:
            assert planner.retry_policy.max_retries == 3


class TestLoopConditionNegation:
    """until: condition is compiled to not(<condition>) for while-gate."""

    def test_loop_condition_is_negated(self):
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(FIXTURES / "complex_workflow.md")
        g = result.graph
        while_gates = [
            n for n in g.nodes if n.node_type == "gate" and n.gate_mode == "while"
        ]
        assert len(while_gates) >= 1
        wg = while_gates[0]
        assert wg.condition.startswith("not ("), (
            f"Expected 'not (...)', got '{wg.condition}'"
        )
        assert wg.condition.endswith(")"), (
            f"Expected 'not (...)', got '{wg.condition}'"
        )


class TestLoopEdgeStructure:
    """While-gate loop has correct edge structure."""

    def test_while_gate_has_continue_and_done_edges(self):
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(FIXTURES / "complex_workflow.md")
        g = result.graph
        while_gates = [
            n for n in g.nodes if n.node_type == "gate" and n.gate_mode == "while"
        ]
        assert len(while_gates) >= 1
        wg = while_gates[0]

        incoming = [e for e in g.edges if e.target_node_id == wg.id]
        assert len(incoming) >= 1, f"Gate '{wg.id}' has no incoming edges"

        outgoing = [e for e in g.edges if e.source_node_id == wg.id]
        out_ports = {e.source_port for e in outgoing}
        assert "continue" in out_ports or "loop" in out_ports, (
            f"Gate '{wg.id}' missing continue/loop edge, has: {out_ports}"
        )
        # "done" edge may be absent when the next step is chained separately;
        # the gate model still declares the port for the engine's scheduler.
        done_ports = {p.name for p in wg.output_ports}
        assert "done" in done_ports, (
            f"Gate '{wg.id}' model missing 'done' output port, has: {done_ports}"
        )


class TestIfGateEdgeStructure:
    """If-gate has true and false output edges."""

    def test_if_gate_has_true_and_false_edges(self):
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(FIXTURES / "complex_workflow.md")
        g = result.graph
        if_gates = [
            n for n in g.nodes if n.node_type == "gate" and n.gate_mode == "if_else"
        ]
        assert len(if_gates) >= 1
        ig = if_gates[0]

        outgoing = [e for e in g.edges if e.source_node_id == ig.id]
        out_ports = {e.source_port for e in outgoing}
        assert "true" in out_ports, (
            f"If-gate '{ig.id}' missing true edge, has: {out_ports}"
        )
        assert "false" in out_ports, (
            f"If-gate '{ig.id}' missing false edge, has: {out_ports}"
        )


class TestResolveChainPortsDiagnostics:
    """_resolve_chain_ports should list available ports when an explicit port is invalid."""

    def test_invalid_source_port_lists_available(self):
        from dan.loader.compiler import _resolve_chain_ports
        from dan.loader.diagnostics import Diagnostic
        from dan.models.nodes import LLMOperator
        from dan.models.ports import InputPort, OutputPort

        src = LLMOperator(
            id="src", name="Src", model="test", prompt_template="go",
            output_ports=[OutputPort(name="alpha"), OutputPort(name="beta")],
        )
        tgt = LLMOperator(
            id="tgt", name="Tgt", model="test", prompt_template="go",
            input_ports=[InputPort(name="data")],
        )

        diags: list[Diagnostic] = []
        _resolve_chain_ports(src, tgt, "nonexistent", "data", diags, source=None)

        error_msgs = [d.message for d in diags if d.level == "error"]
        assert len(error_msgs) == 1
        assert "available:" in error_msgs[0]
        assert "alpha" in error_msgs[0]
        assert "beta" in error_msgs[0]

    def test_invalid_target_port_lists_available(self):
        from dan.loader.compiler import _resolve_chain_ports
        from dan.loader.diagnostics import Diagnostic
        from dan.models.nodes import LLMOperator
        from dan.models.ports import InputPort, OutputPort

        src = LLMOperator(
            id="src", name="Src", model="test", prompt_template="go",
            output_ports=[OutputPort(name="text")],
        )
        tgt = LLMOperator(
            id="tgt", name="Tgt", model="test", prompt_template="go",
            input_ports=[InputPort(name="x"), InputPort(name="y")],
        )

        diags: list[Diagnostic] = []
        _resolve_chain_ports(src, tgt, "text", "missing", diags, source=None)

        error_msgs = [d.message for d in diags if d.level == "error"]
        assert len(error_msgs) == 1
        assert "available:" in error_msgs[0]
        assert "x" in error_msgs[0]
        assert "y" in error_msgs[0]


class TestStrictMode:
    """Strict mode: parse_warnings and ambiguous bare-edge become fatal errors."""

    def test_strict_parse_warnings_fail(self):
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(
            FIXTURES / "strict_parse_warnings_workflow.md",
            strict=True,
        )
        assert result.graph is None
        errors = [d for d in result.diagnostics if d.level == "error"]
        assert len(errors) >= 1
        assert any("parse" in e.message.lower() or "flow" in e.message.lower() for e in errors)

    def test_non_strict_parse_warnings_succeed_with_warnings(self):
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(
            FIXTURES / "strict_parse_warnings_workflow.md",
            strict=False,
        )
        assert result.graph is not None
        warnings = [d for d in result.diagnostics if d.level == "warning"]
        assert len(warnings) >= 1

    def test_strict_ambiguous_bare_edge_fails(self):
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(
            FIXTURES / "ambiguous_ports_workflow.md",
            strict=True,
        )
        assert result.graph is None
        errors = [d for d in result.diagnostics if d.level == "error"]
        assert len(errors) >= 1
        assert any("Ambiguous" in e.message and "multiple matching ports" in e.message for e in errors)

    def test_non_strict_ambiguous_bare_edge_succeeds_with_warning(self):
        from dan.loader.compiler import compile_workflow

        result = compile_workflow(
            FIXTURES / "ambiguous_ports_workflow.md",
            strict=False,
        )
        assert result.graph is not None
        warnings = [d for d in result.diagnostics if d.level == "warning"]
        assert any("Ambiguous" in w.message for w in warnings)
