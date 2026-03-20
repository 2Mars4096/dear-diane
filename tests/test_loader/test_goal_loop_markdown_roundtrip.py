from __future__ import annotations

from dan.builder import workflow
from dan.loader import compile_workflow
from dan.loader.decompiler import decompile_to_markdown


def test_goal_loop_round_trips_through_markdown(tmp_path) -> None:
    wf = workflow("goal_loop_roundtrip")
    with wf.goal_loop(
        "refine",
        goal_text="Reach a higher score",
        metric_name="score",
        target_value=0.9,
    ) as body:
        body.llm(
            "score_step",
            prompt="Return a score",
            output_schema={
                "type": "object",
                "properties": {"score": {"type": "number"}},
                "required": ["score"],
            },
        )

    graph = wf.build()
    outdir = tmp_path / "markdown"
    result = decompile_to_markdown(graph, outdir)

    assert not any("Unsupported node type 'goal_loop'" in d.message for d in result.diagnostics)

    goal_file = outdir / "refine.md"
    content = goal_file.read_text(encoding="utf-8")
    assert "type: goal_loop" in content
    assert "goal_text: Reach a higher score" in content
    assert "## Agents" in content

    compiled = compile_workflow(outdir / "workflow.md")

    assert compiled.graph is not None, compiled.diagnostics
    goal_node = compiled.graph.node_by_id("refine")
    assert goal_node is not None
    assert goal_node.node_type == "goal_loop"
    assert goal_node.body_graph in compiled.graph.sub_graphs
    body_graph = compiled.graph.sub_graphs[goal_node.body_graph]
    assert {node.id for node in body_graph.nodes} == {"score_step"}


def test_human_node_markdown_export_round_trips_canonically(tmp_path) -> None:
    wf = workflow("human_export")
    wf.human(
        "review",
        prompt="Review the output",
        render_mode="approval",
        instructions="Approve or reject",
    )

    graph = wf.build()
    outdir = tmp_path / "markdown"
    result = decompile_to_markdown(graph, outdir)

    assert not any(
        "legacy markdown type 'human'" in diagnostic.message
        for diagnostic in result.diagnostics
    )

    compiled = compile_workflow(outdir / "workflow.md")
    assert compiled.graph is not None, compiled.diagnostics
    review_node = compiled.graph.node_by_id("review")
    assert review_node is not None
    assert review_node.node_type == "human"
    assert review_node.render_mode == "approval"
    assert review_node.instructions == "Approve or reject"
