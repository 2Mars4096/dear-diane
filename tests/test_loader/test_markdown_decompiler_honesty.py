from __future__ import annotations

from dan.loader.decompiler import decompile_to_markdown
from dan.models.control_flow import OrchestratorNode, ParallelSubagentsNode
from dan.models.edges import DataEdge
from dan.models.graph import Graph, GraphMetadata
from dan.models.nodes import LLMOperator


def _llm(node_id: str, prompt: str = "Do work") -> LLMOperator:
    return LLMOperator(
        id=node_id,
        name=node_id,
        model="claude-sonnet-4-6",
        prompt_template=prompt,
    )


def test_parallel_subagents_multi_node_branch_emits_lossiness_warning(tmp_path) -> None:
    branch_a = Graph(
        nodes=[_llm("a1"), _llm("a2")],
        edges=[
            DataEdge(
                id="a_edge",
                source_node_id="a1",
                source_port="text",
                target_node_id="a2",
                target_port="input",
            )
        ],
        entry_points=["a1"],
        exit_points=["a2"],
    )
    branch_b = Graph(
        nodes=[_llm("b1")],
        edges=[],
        entry_points=["b1"],
        exit_points=["b1"],
    )
    graph = Graph(
        metadata=GraphMetadata(name="parallel_lossy"),
        nodes=[
            _llm("source"),
            ParallelSubagentsNode(
                id="parallel",
                name="parallel",
                branch_graphs=["parallel_a", "parallel_b"],
            ),
        ],
        edges=[
            DataEdge(
                id="root_edge",
                source_node_id="source",
                source_port="text",
                target_node_id="parallel",
                target_port="input",
            )
        ],
        sub_graphs={"parallel_a": branch_a, "parallel_b": branch_b},
        entry_points=["source"],
        exit_points=["parallel"],
    )

    result = decompile_to_markdown(graph, tmp_path / "markdown")

    assert any(
        "parallel_subagents 'parallel' has multi-node branches" in d.message
        for d in result.diagnostics
    )


def test_orchestrator_nondefault_config_emits_lossiness_warning(tmp_path) -> None:
    team_graph = Graph(
        nodes=[_llm("researcher")],
        edges=[],
        entry_points=["researcher"],
        exit_points=["researcher"],
    )
    graph = Graph(
        metadata=GraphMetadata(name="orchestrator_lossy"),
        nodes=[
            _llm("source"),
            OrchestratorNode(
                id="coord",
                name="coord",
                teams={"research": "research_team"},
                orchestrator_prompt="Coordinate the teams",
                completion_condition="any_done",
                input_mappings={"input": "task"},
            ),
        ],
        edges=[
            DataEdge(
                id="coord_edge",
                source_node_id="source",
                source_port="text",
                target_node_id="coord",
                target_port="input",
            )
        ],
        sub_graphs={"research_team": team_graph},
        entry_points=["source"],
        exit_points=["coord"],
    )

    result = decompile_to_markdown(graph, tmp_path / "markdown")

    assert any(
        "Orchestrator 'coord' markdown export is lossy" in d.message
        for d in result.diagnostics
    )
