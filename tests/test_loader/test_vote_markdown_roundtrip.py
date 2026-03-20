from __future__ import annotations

from dan.builder import workflow
from dan.loader import compile_workflow
from dan.loader.decompiler import decompile_to_markdown


def test_vote_markdown_agent_compiles_to_vote_node(tmp_path) -> None:
    workflow_path = tmp_path / "workflow.md"
    agent_path = tmp_path / "quality_check.md"

    workflow_path.write_text(
        """---
name: vote_workflow
format_version: 1
---

## Agents

- [quality_check](quality_check.md)
""",
        encoding="utf-8",
    )
    agent_path.write_text(
        """---
type: vote
candidates:
  - claude-sonnet-4-6
  - gpt-4o
vote_strategy: judge
judge_model: claude-opus-4
parallelism: 2
---

Evaluate this analysis and provide your assessment: {input}
""",
        encoding="utf-8",
    )

    result = compile_workflow(workflow_path)

    assert result.graph is not None, result.diagnostics
    node = result.graph.node_by_id("quality_check")
    assert node is not None
    assert node.node_type == "vote"
    assert node.candidates == ["claude-sonnet-4-6", "gpt-4o"]
    assert node.vote_strategy == "judge"
    assert node.vote_config is not None
    assert node.vote_config.judge_model == "claude-opus-4"


def test_vote_node_round_trips_through_markdown(tmp_path) -> None:
    wf = workflow("vote_roundtrip")
    wf.vote(
        "quality_check",
        prompt="Evaluate this analysis and provide your assessment: {input}",
        candidates=["claude-sonnet-4-6", "gpt-4o"],
        strategy="judge",
        vote_config={"judge_model": "claude-opus-4"},
        parallelism=2,
    )
    graph = wf.build()

    outdir = tmp_path / "markdown"
    decompiled = decompile_to_markdown(graph, outdir)

    assert not any(
        "Unsupported node type 'vote'" in diagnostic.message
        for diagnostic in decompiled.diagnostics
    )

    compiled = compile_workflow(outdir / "workflow.md")
    assert compiled.graph is not None, compiled.diagnostics
    node = compiled.graph.node_by_id("quality_check")
    assert node is not None
    assert node.node_type == "vote"
    assert node.candidates == ["claude-sonnet-4-6", "gpt-4o"]
    assert node.vote_strategy == "judge"
    assert node.vote_config is not None
    assert node.vote_config.judge_model == "claude-opus-4"
