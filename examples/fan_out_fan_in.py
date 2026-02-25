"""Fan-out / fan-in pattern: ForEach + Code reduce.

# Architecture
#
#   [gen_subtopics]  LLM — generates a list of subtopics (JSON schema)
#          |
#          v
#   [research]       ForEach(parallelism=3) — processes each subtopic
#     |  body:
#     |    [unpack] Code — extracts item fields
#     |       v
#     |    [summarize] LLM — writes a summary for one subtopic
#          |
#          v
#   [aggregate]      Code — merges all summaries into a single report
#
#   Topology: fan-out via ForEach, fan-in via code reducer.
#   Demonstrates: parallelism, MergeStrategy.APPEND, sub-graph iteration.

Usage:
    python examples/fan_out_fan_in.py
    python examples/fan_out_fan_in.py --topic "artificial intelligence"
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dan.builder import workflow
from dan.models.context import MergeStrategy
from dan.engine import Engine, EngineConfig

SUBTOPICS_SCHEMA = {
    "type": "object",
    "properties": {
        "subtopics": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["subtopics"],
}

UNPACK_CODE = """\
if isinstance(item, str):
    subtopic = item
elif isinstance(item, dict):
    subtopic = str(item.get("subtopic", item.get("name", str(item))))
else:
    subtopic = str(item)
result = {"subtopic": subtopic}
"""

AGGREGATE_CODE = """\
items = results if isinstance(results, list) else []
summaries = []
for r in items:
    if isinstance(r, dict):
        summaries.append(str(r.get("text", r.get("summary", str(r)))))
    else:
        summaries.append(str(r))
combined = "\\n\\n---\\n\\n".join(summaries)
result = {"report": combined, "subtopic_count": len(summaries)}
"""


def build_fan_out_fan_in():
    """Build a fan-out/fan-in workflow with ForEach parallelism."""
    wf = workflow(
        "fan_out_fan_in",
        description="ForEach + Reduce pattern with parallel subtopic research",
        tags=["template", "fan-out", "parallel"],
    )

    gen = wf.llm(
        "gen_subtopics",
        prompt=(
            "Given the topic: {topic}\n\n"
            "Generate exactly 4 specific subtopics worth researching. "
            "Return them as a JSON list under the key 'subtopics'."
        ),
        output_schema=SUBTOPICS_SCHEMA,
        input_ports=[{"name": "topic"}],
    )

    with wf.for_each(
        "research",
        items=gen["subtopics"],
        parallelism=3,
        merge_strategy=MergeStrategy.APPEND,
    ) as body:
        unpack = body.code(
            "unpack",
            code=UNPACK_CODE,
            input_ports=[{"name": "item"}],
            output_ports=[{"name": "subtopic"}],
        )
        summarize = body.llm(
            "summarize",
            prompt=(
                "Write a concise 50-word summary about: {subtopic}\n\n"
                "Focus on the key facts and current state of knowledge."
            ),
            input_ports=[{"name": "subtopic"}],
        )
        body.edge(unpack["subtopic"], summarize["subtopic"])

    from dan.builder.refs import NodeRef
    research_ref = NodeRef("research", "for_each", wf)

    aggregate = wf.code(
        "aggregate",
        code=AGGREGATE_CODE,
        input_ports=[{"name": "results"}],
        output_ports=[{"name": "report"}, {"name": "subtopic_count"}],
    )
    wf.edge(research_ref["results"], aggregate["results"])

    return wf.build()


async def main(topic: str = "sustainable energy") -> None:
    graph = build_fan_out_fan_in()

    graphs_dir = Path("graphs")
    graphs_dir.mkdir(exist_ok=True)
    graph_path = graphs_dir / "fan_out_fan_in.json"
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

    parser = argparse.ArgumentParser(description="Fan-out/fan-in template")
    parser.add_argument("--topic", default="sustainable energy")
    args = parser.parse_args()
    asyncio.run(main(args.topic))
