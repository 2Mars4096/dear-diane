"""Review-revise loop using a WhileLoop node.

# Architecture
#
#   [draft]      LLM — writes an initial paragraph about the topic
#       |
#       v
#   [review_loop]  WhileLoop(condition="quality_score < 8", max_iterations=3)
#     |  body:
#     |    [loop_in]  Code — pass-through to unpack loop state
#     |       v
#     |    [review]   LLM — evaluates draft quality (JSON: quality_score, feedback)
#     |       v
#     |    [revise]   LLM — improves draft based on feedback
#     |       v
#     |    [loop_out] Code — repack state for next iteration
#
#   Topology: linear with a while-loop for iterative improvement.
#   Demonstrates: while_loop context manager, condition-based iteration,
#                 convergence via quality threshold.

Usage:
    python examples/review_revise.py
    python examples/review_revise.py --topic "climate change adaptation"
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dan.builder import workflow
from dan.engine import Engine, EngineConfig
from dan.models.context import CompactionRule, CompactionStrategy, FailurePolicy

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "quality_score": {"type": "integer"},
        "feedback": {"type": "string"},
    },
    "required": ["quality_score", "feedback"],
}

LOOP_IN_CODE = """\
result = {
    "draft": draft,
    "quality_score": quality_score,
    "feedback": feedback,
}
"""

LOOP_OUT_CODE = """\
result = {
    "draft": revised_draft,
    "quality_score": quality_score,
    "feedback": feedback,
}
"""


def build_review_revise():
    """Build a review-revise while-loop workflow."""
    wf = workflow(
        "review_revise",
        description="Iterative draft improvement via review-revise while-loop",
        tags=["template", "while-loop", "iteration"],
    )

    draft = wf.llm(
        "draft",
        prompt=(
            "Write a well-structured paragraph (80-120 words) about: {topic}\n\n"
            "Include a clear thesis, supporting evidence, and a conclusion."
        ),
        input_ports=[{"name": "topic"}],
    )

    init = wf.code(
        "init_state",
        code='result = {"draft": text, "quality_score": 0, "feedback": "Initial draft."}',
        input_ports=[{"name": "text"}],
        output_ports=[{"name": "draft"}, {"name": "quality_score"}, {"name": "feedback"}],
    )
    wf.edge(draft["text"], init["text"])

    with wf.while_loop(
        "review_loop",
        condition="quality_score < 8",
        max_iterations=3,
        compaction=CompactionRule(strategy=CompactionStrategy.SLIDING_WINDOW, window_size=2),
        failure_policy=FailurePolicy(max_iterations=3, stagnation_threshold=2),
        input_ports=[
            {"name": "draft"},
            {"name": "quality_score"},
            {"name": "feedback"},
        ],
        output_ports=[
            {"name": "draft"},
            {"name": "quality_score"},
            {"name": "feedback"},
        ],
    ) as body:
        loop_in = body.code(
            "loop_in",
            code=LOOP_IN_CODE,
            input_ports=[
                {"name": "draft"},
                {"name": "quality_score"},
                {"name": "feedback"},
            ],
            output_ports=[
                {"name": "draft"},
                {"name": "quality_score"},
                {"name": "feedback"},
            ],
        )

        review = body.llm(
            "review",
            prompt=(
                "Review this draft paragraph:\n\n{draft}\n\n"
                "Rate quality from 1-10 and provide specific improvement feedback. "
                "A score of 8+ means the paragraph is publication-ready."
            ),
            output_schema=REVIEW_SCHEMA,
            input_ports=[{"name": "draft"}],
        )
        body.edge(loop_in["draft"], review["draft"])

        revise = body.llm(
            "revise",
            prompt=(
                "Revise this paragraph based on feedback.\n\n"
                "Current draft:\n{draft}\n\n"
                "Feedback (score {quality_score}/10):\n{feedback}\n\n"
                "Write an improved version addressing all feedback points."
            ),
            input_ports=[
                {"name": "draft"},
                {"name": "quality_score"},
                {"name": "feedback"},
            ],
        )
        body.edge(loop_in["draft"], revise["draft"])
        body.edge(review["quality_score"], revise["quality_score"])
        body.edge(review["feedback"], revise["feedback"])

        loop_out = body.code(
            "loop_out",
            code=LOOP_OUT_CODE,
            input_ports=[
                {"name": "revised_draft"},
                {"name": "quality_score"},
                {"name": "feedback"},
            ],
            output_ports=[
                {"name": "draft"},
                {"name": "quality_score"},
                {"name": "feedback"},
            ],
        )
        body.edge(revise["text"], loop_out["revised_draft"])
        body.edge(review["quality_score"], loop_out["quality_score"])
        body.edge(review["feedback"], loop_out["feedback"])

    from dan.builder.refs import NodeRef
    loop_ref = NodeRef("review_loop", "while_loop", wf)
    wf.edge(init["draft"], loop_ref["draft"])
    wf.edge(init["quality_score"], loop_ref["quality_score"])
    wf.edge(init["feedback"], loop_ref["feedback"])

    return wf.build()


async def main(topic: str = "sustainable energy") -> None:
    graph = build_review_revise()

    graphs_dir = Path("graphs")
    graphs_dir.mkdir(exist_ok=True)
    graph_path = graphs_dir / "review_revise.json"
    graph_path.write_text(json.dumps(graph.model_dump(mode="json"), indent=2))
    print(f"Graph saved to {graph_path}")

    api_key = os.environ.get("DAN_LLM_API_KEY", "")
    if api_key:
        config = EngineConfig(llm_api_key=api_key)
        engine = Engine(config)
        result = await engine.run(graph, inputs={"topic": topic})
        print(f"Success: {result.success}")
        print(f"Outputs: {json.dumps(result.outputs, indent=2, default=str)}")
    else:
        print("Set DAN_LLM_API_KEY to run the workflow")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Review-revise loop template")
    parser.add_argument("--topic", default="sustainable energy")
    args = parser.parse_args()
    asyncio.run(main(args.topic))
