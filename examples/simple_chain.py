"""Simple 3-node linear pipeline: LLM → LLM → Code.

# Architecture
#
#   [idea_gen]  LLM — generates creative ideas about a topic
#       |
#       v
#   [expand]    LLM — picks the best idea and writes a 100-word summary
#       |           (JSON-schema output normalization)
#       v
#   [analyze]   Code — counts words and extracts key phrases
#
#   Topology: linear chain using >> operator.
#   Data passing: f-string refs and explicit edge wiring.
#   Demonstrates: basic chaining, output normalization via JSON schema.

Usage:
    python examples/simple_chain.py
    python examples/simple_chain.py --topic "quantum computing"
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dan.builder import workflow
from dan.engine import Engine, EngineConfig

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "best_idea": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["best_idea", "summary"],
}

ANALYZE_CODE = """\
text = summary if isinstance(summary, str) else str(summary)
words = text.split()
key_phrases = words[:5]
result = {"word_count": len(words), "key_phrases": key_phrases}
"""


def build_simple_chain():
    """Build a 3-node linear chain workflow."""
    wf = workflow(
        "simple_chain",
        description="3-node linear pipeline: LLM → LLM → Code",
        tags=["template", "chain", "beginner"],
    )

    ideas = wf.llm(
        "idea_gen",
        prompt=(
            "Generate 3 creative ideas about: {topic}\n\n"
            "Number each idea and provide a one-sentence description."
        ),
        input_ports=[{"name": "topic"}],
    )

    expand = wf.llm(
        "expand",
        prompt=(
            "Here are some ideas:\n{text}\n\n"
            "Pick the single best idea and write a 100-word summary "
            "explaining why it is compelling and what it could achieve."
        ),
        output_schema=SUMMARY_SCHEMA,
        input_ports=[{"name": "text"}],
    )

    analyze = wf.code(
        "analyze",
        code=ANALYZE_CODE,
        input_ports=[{"name": "summary"}],
        output_ports=[{"name": "word_count"}, {"name": "key_phrases"}],
    )

    wf.edge(ideas["text"], expand["text"])
    wf.edge(expand["summary"], analyze["summary"])

    return wf.build()


async def main(topic: str = "sustainable energy") -> None:
    graph = build_simple_chain()

    graphs_dir = Path("graphs")
    graphs_dir.mkdir(exist_ok=True)
    graph_path = graphs_dir / "simple_chain.json"
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

    parser = argparse.ArgumentParser(description="Simple chain template")
    parser.add_argument("--topic", default="sustainable energy")
    args = parser.parse_args()
    asyncio.run(main(args.topic))
