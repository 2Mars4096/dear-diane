from __future__ import annotations

from dan.builder import workflow
from dan.loader import compile_workflow
from dan.loader.decompiler import decompile_to_markdown


def _assert_human_contract(
    node,
    *,
    render_mode: str,
    render_target: str,
    instructions: str,
) -> None:
    assert node.node_type in {"human", "worker"}
    if node.node_type == "human":
        assert node.render_mode == render_mode
        assert node.render_target == render_target
        assert node.instructions == instructions
        assert node.output_schema is not None
        return

    metadata = node.metadata or {}
    assert metadata.get("human_render_mode") == render_mode
    assert metadata.get("human_render_target") == render_target
    assert metadata.get("human_instructions") == instructions
    assert metadata.get("human_output_schema") is not None


def test_markdown_human_agent_compiles_to_canonical_human_node(tmp_path) -> None:
    workflow_path = tmp_path / "workflow.md"
    agent_path = tmp_path / "review.md"

    workflow_path.write_text(
        """---
name: human_workflow
format_version: 1
---

## Agents

- [review](review.md)
""",
        encoding="utf-8",
    )
    agent_path.write_text(
        """---
type: human
timeout_seconds: 30
default_action: approve
render_mode: approval
render_target: both
instructions: Approve or reject the draft.
output_schema:
  type: object
  properties:
    approved:
      type: boolean
---

> Accepts: draft (string)
> Returns: response (string)

Review the draft.
""",
        encoding="utf-8",
    )

    result = compile_workflow(workflow_path)

    assert result.graph is not None, result.diagnostics
    node = result.graph.node_by_id("review")
    assert node is not None
    _assert_human_contract(
        node,
        render_mode="approval",
        render_target="both",
        instructions="Approve or reject the draft.",
    )


def test_human_node_round_trips_through_markdown_without_legacy_warning(tmp_path) -> None:
    wf = workflow("human_roundtrip")
    wf.human(
        "review",
        prompt="Review the draft.",
        render_mode="approval",
        render_target="both",
        instructions="Approve or reject the draft.",
        output_schema={
            "type": "object",
            "properties": {"approved": {"type": "boolean"}},
            "required": ["approved"],
        },
    )
    graph = wf.build()

    outdir = tmp_path / "markdown"
    decompiled = decompile_to_markdown(graph, outdir)

    assert not any(
        "recompiling markdown currently yields 'human_in_the_loop' semantics"
        in diagnostic.message
        for diagnostic in decompiled.diagnostics
    )

    compiled = compile_workflow(outdir / "workflow.md")
    assert compiled.graph is not None, compiled.diagnostics
    node = compiled.graph.node_by_id("review")
    assert node is not None
    _assert_human_contract(
        node,
        render_mode="approval",
        render_target="both",
        instructions="Approve or reject the draft.",
    )
