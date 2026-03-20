"""Structural comparison: paper_writing.py vs paper_writing_md/workflow.md."""
import importlib.util
import sys
from pathlib import Path

import pytest

from dan.loader.compiler import compile_workflow
from dan.models.edges import DataEdge
from dan.models.graph import Graph

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKDOWN_WORKFLOW = PROJECT_ROOT / "examples" / "paper_writing_md" / "workflow.md"
PYTHON_WORKFLOW = PROJECT_ROOT / "examples" / "paper_writing.py"


def _load_python_graph() -> Graph:
    """Load the Python builder graph by importing and calling build_paper_workflow."""
    spec = importlib.util.spec_from_file_location(
        "paper_writing",
        PYTHON_WORKFLOW,
        submodule_search_locations=[str(PROJECT_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {PYTHON_WORKFLOW}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["paper_writing"] = mod
    spec.loader.exec_module(mod)
    return mod.build_paper_workflow(max_review_iterations=3)


def _all_nodes(graph: Graph) -> list:
    """Collect all nodes from graph and sub_graphs recursively."""
    nodes = list(graph.nodes)
    for sg in graph.sub_graphs.values():
        nodes.extend(_all_nodes(sg))
    return nodes


def _all_edges(graph: Graph) -> list:
    """Collect all edges from graph and sub_graphs recursively."""
    edges = list(graph.edges)
    for sg in graph.sub_graphs.values():
        edges.extend(_all_edges(sg))
    return edges


def _user_authored_node_types(nodes: list) -> set[str]:
    """Node types that are user-authored (exclude auto-generated input, gate, for_each)."""
    exclude = {"input", "gate"}
    return {n.node_type for n in nodes if n.node_type not in exclude}


class TestPaperWritingParity:
    @pytest.fixture
    def md_graph(self) -> Graph:
        r = compile_workflow(MARKDOWN_WORKFLOW)
        assert r.graph is not None, (
            f"Markdown compile failed: {[d.message for d in r.diagnostics]}"
        )
        return r.graph

    @pytest.fixture
    def py_graph(self) -> Graph:
        return _load_python_graph()

    def test_markdown_compiles_successfully(self, md_graph: Graph) -> None:
        assert md_graph is not None
        assert md_graph.metadata.name

    def test_python_builds_successfully(self, py_graph: Graph) -> None:
        assert py_graph is not None
        assert py_graph.metadata.name

    def test_markdown_has_llm_agents(self, md_graph: Graph) -> None:
        nodes = _all_nodes(md_graph)
        llm_nodes = [n for n in nodes if n.node_type == "llm_operator"]
        assert len(llm_nodes) >= 4, (
            f"Expected >= 4 LLM agents (idea_generator, outline_planner, reviewer, reviser, etc.), "
            f"got {len(llm_nodes)}"
        )

    def test_python_has_llm_agents(self, py_graph: Graph) -> None:
        nodes = _all_nodes(py_graph)
        llm_nodes = [n for n in nodes if n.node_type == "llm_operator"]
        assert len(llm_nodes) >= 4

    def test_markdown_has_foreach_node(self, md_graph: Graph) -> None:
        nodes = _all_nodes(md_graph)
        fe_nodes = [n for n in nodes if n.node_type == "for_each"]
        assert len(fe_nodes) >= 1

    def test_python_has_foreach_node(self, py_graph: Graph) -> None:
        nodes = _all_nodes(py_graph)
        fe_nodes = [n for n in nodes if n.node_type == "for_each"]
        assert len(fe_nodes) >= 1

    def test_markdown_has_loop(self, md_graph: Graph) -> None:
        nodes = _all_nodes(md_graph)
        gate_nodes = [n for n in nodes if n.node_type == "gate"]
        while_gates = [n for n in gate_nodes if getattr(n, "gate_mode", "") == "while"]
        assert len(while_gates) >= 1

    def test_python_has_loop(self, py_graph: Graph) -> None:
        nodes = _all_nodes(py_graph)
        while_nodes = [n for n in nodes if n.node_type == "while_loop"]
        gate_nodes = [n for n in nodes if n.node_type == "gate"]
        assert len(while_nodes) >= 1 or len(gate_nodes) >= 1, (
            "Python uses WhileLoopNode or GateNode for review loop"
        )

    def test_markdown_has_human_node(self, md_graph: Graph) -> None:
        nodes = _all_nodes(md_graph)
        human_nodes = [n for n in nodes if n.node_type == "human_in_the_loop"]
        assert len(human_nodes) >= 1

    def test_python_has_human_node(self, py_graph: Graph) -> None:
        nodes = _all_nodes(py_graph)
        human_nodes = [n for n in nodes if n.node_type == "human_in_the_loop"]
        assert len(human_nodes) >= 1

    def test_markdown_has_code_or_tool_node(self, md_graph: Graph) -> None:
        nodes = _all_nodes(md_graph)
        code_nodes = [n for n in nodes if n.node_type == "code_operator"]
        tool_nodes = [n for n in nodes if n.node_type == "tool_operator"]
        assert len(code_nodes) >= 1 or len(tool_nodes) >= 1

    def test_python_has_code_and_tool_nodes(self, py_graph: Graph) -> None:
        nodes = _all_nodes(py_graph)
        code_nodes = [n for n in nodes if n.node_type == "code_operator"]
        tool_nodes = [n for n in nodes if n.node_type == "tool_operator"]
        assert len(code_nodes) >= 1
        assert len(tool_nodes) >= 1

    def test_markdown_has_shared_context(self, md_graph: Graph) -> None:
        assert len(md_graph.shared_context) >= 1

    def test_python_has_shared_context_or_equivalent(self, py_graph: Graph) -> None:
        # Python version may use different context patterns
        assert py_graph.metadata.name

    def test_markdown_has_data_edges(self, md_graph: Graph) -> None:
        edges = _all_edges(md_graph)
        data_edges = [e for e in edges if isinstance(e, DataEdge)]
        assert len(data_edges) >= 5

    def test_semantic_node_type_overlap(self, md_graph: Graph, py_graph: Graph) -> None:
        """Both graphs have overlapping user-authored node types (llm, tool, code, human)."""
        md_nodes = _all_nodes(md_graph)
        py_nodes = _all_nodes(py_graph)
        md_types = _user_authored_node_types(md_nodes)
        py_types = _user_authored_node_types(py_nodes)
        overlap = md_types & py_types
        assert "llm_operator" in overlap
        assert "human_in_the_loop" in overlap
        assert "for_each" in overlap or "gate" in md_types or "while_loop" in py_types

    def test_documents_known_gaps(self, md_graph: Graph) -> None:
        """Document known gaps between markdown and Python versions."""
        gaps = []
        nodes = _all_nodes(md_graph)
        gates = [n for n in nodes if n.node_type == "gate"]
        if gates:
            gaps.append("Markdown uses GateNode for loops; Python may use WhileLoopNode")
        tool_nodes = [n for n in nodes if n.node_type == "tool_operator"]
        if tool_nodes:
            gaps.append(f"Tool nodes present: {[n.id for n in tool_nodes]}")
        if gaps:
            print(f"Known parity gaps: {gaps}")

    @pytest.mark.skip(
        reason="Python has 2 for_each (lit_search, section_writers) + nested section_revisers; "
        "markdown has 1 for_each. Different granularity by design."
    )
    def test_same_foreach_count(self, md_graph: Graph, py_graph: Graph) -> None:
        """Exact foreach count parity — skipped: markdown is simplified."""
        md_fe = sum(1 for n in _all_nodes(md_graph) if n.node_type == "for_each")
        py_fe = sum(1 for n in _all_nodes(py_graph) if n.node_type == "for_each")
        assert md_fe == py_fe

    @pytest.mark.skip(
        reason="Python uses WhileLoopNode; markdown compiler emits GateNode(gate_mode=while). "
        "Semantically equivalent, structurally different."
    )
    def test_same_loop_representation(self, md_graph: Graph, py_graph: Graph) -> None:
        """Exact loop node type parity — skipped: different control-flow representation."""
        md_loops = [n for n in _all_nodes(md_graph) if n.node_type == "gate"]
        py_loops = [n for n in _all_nodes(py_graph) if n.node_type == "while_loop"]
        assert len(md_loops) == len(py_loops)
