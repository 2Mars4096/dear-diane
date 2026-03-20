from __future__ import annotations

from dan.builder import workflow
from dan.loader import compile_workflow
from dan.loader.decompiler import decompile_to_markdown


def test_agent_team_markdown_agent_compiles_to_agent_team_node(tmp_path) -> None:
    workflow_path = tmp_path / "workflow.md"
    team_path = tmp_path / "review_team.md"
    researcher_path = tmp_path / "researcher.md"
    writer_path = tmp_path / "writer.md"

    workflow_path.write_text(
        """---
name: agent_team_workflow
format_version: 1
---

## Agents

- [review_team](review_team.md)
""",
        encoding="utf-8",
    )
    team_path.write_text(
        """---
type: agent_team
moderator_prompt: Coordinate the specialists.
turn_strategy: free_form
shared_context_keys:
  - conversation_history
---

## Agents

- [researcher](researcher.md)
- [writer](writer.md)
""",
        encoding="utf-8",
    )
    researcher_path.write_text(
        """---
type: llm
---

Research the topic.
""",
        encoding="utf-8",
    )
    writer_path.write_text(
        """---
type: llm
---

Draft the answer.
""",
        encoding="utf-8",
    )

    result = compile_workflow(workflow_path)

    assert result.graph is not None, result.diagnostics
    node = result.graph.node_by_id("review_team")
    assert node is not None
    assert node.node_type == "agent_team"
    assert set(node.agents.keys()) == {"researcher", "writer"}
    assert set(result.graph.sub_graphs.keys()) >= {"review_team_researcher", "review_team_writer"}


def test_agent_team_node_round_trips_through_markdown(tmp_path) -> None:
    wf = workflow("agent_team_roundtrip")
    with wf.team(
        "review_team",
        moderator_prompt="Coordinate the specialists.",
        turn_strategy="free_form",
        shared_context_keys=["conversation_history"],
    ) as team:
        with team.agent("researcher") as sub:
            sub.llm("research", prompt="Research the topic.")
        with team.agent("writer") as sub:
            sub.llm("write", prompt="Draft the answer.")

    graph = wf.build()

    outdir = tmp_path / "markdown"
    decompiled = decompile_to_markdown(graph, outdir)
    assert not any(
        "Unsupported node type 'agent_team'" in diagnostic.message
        for diagnostic in decompiled.diagnostics
    )

    compiled = compile_workflow(outdir / "workflow.md")
    assert compiled.graph is not None, compiled.diagnostics
    node = compiled.graph.node_by_id("review_team")
    assert node is not None
    assert node.node_type == "agent_team"
    assert set(node.agents.keys()) == {"researcher", "writer"}
