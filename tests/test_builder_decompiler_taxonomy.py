from __future__ import annotations

from dan.builder.decompiler import decompile
from dan.models.control_flow import GoalLoopNode
from dan.models.graph import Graph
from dan.models.nodes import LLMOperator


def test_builder_decompiler_emits_goal_loop_context_manager() -> None:
    body_graph = Graph(
        nodes=[
            LLMOperator(
                id="score_step",
                name="Score Step",
                model="claude-sonnet-4-6",
                prompt_template="Score this draft",
            )
        ],
        edges=[],
        entry_points=["score_step"],
        exit_points=["score_step"],
    )
    graph = Graph(
        nodes=[
            GoalLoopNode(
                id="goal",
                name="Goal Loop",
                goal_text="Reach the target score",
                body_graph="goal_body",
            )
        ],
        edges=[],
        sub_graphs={"goal_body": body_graph},
        entry_points=["goal"],
        exit_points=["goal"],
    )

    code = decompile(graph)

    assert "with wf.goal_loop('goal'" in code
    assert "goal_text='Reach the target score'" in code
    assert "score_step = _goal_body.llm(" in code
    assert "goal = wf.llm('goal'" not in code
