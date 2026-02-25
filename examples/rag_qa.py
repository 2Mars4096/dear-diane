"""Tool-based RAG Q&A — no vector DB required.

# Architecture
#
#   [read_file]    ToolOperator(file_read) — reads a local file
#       |
#       v
#   [chunk]        ToolOperator(text_chunk) — splits content into chunks
#       |
#       v
#   [answer]       LLM — answers the question using chunks as context
#
#   Topology: 3-node linear pipeline using built-in dan.tools.
#   Demonstrates: ToolOperator nodes with built-in tools (file_read,
#                 text_chunk), tool-to-LLM data flow, zero-dependency RAG.

Usage:
    python examples/rag_qa.py
    python examples/rag_qa.py --question "What is the architecture?" --file README.md
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dan.builder import workflow
from dan.engine import Engine, EngineConfig
from dan.engine.executor import ExecutorRegistry
from dan.executors.tool import ToolExecutor, ToolRegistry

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "string"},
        "relevant_chunks_used": {"type": "integer"},
    },
    "required": ["answer", "confidence"],
}


SETUP_CODE = """\
result = {
    "path": file_path,
    "question": question,
    "chunk_size": 500,
    "overlap": 50,
}
"""


def build_rag_qa():
    """Build a tool-based RAG Q&A workflow."""
    wf = workflow(
        "rag_qa",
        description="Tool-based RAG Q&A using file_read and text_chunk",
        tags=["template", "rag", "tools"],
    )

    setup = wf.code(
        "setup",
        code=SETUP_CODE,
        input_ports=[{"name": "file_path"}, {"name": "question"}],
        output_ports=[
            {"name": "path"},
            {"name": "question"},
            {"name": "chunk_size"},
            {"name": "overlap"},
        ],
    )

    read = wf.tool(
        "read_file",
        tool_id="file_read",
        input_ports=[{"name": "path"}],
        output_ports=[{"name": "content"}, {"name": "size_bytes"}],
    )
    wf.edge(setup["path"], read["path"])

    chunk = wf.tool(
        "chunk",
        tool_id="text_chunk",
        input_ports=[{"name": "text"}, {"name": "chunk_size"}, {"name": "overlap"}],
        output_ports=[{"name": "chunks"}, {"name": "chunk_count"}],
    )
    wf.edge(read["content"], chunk["text"])
    wf.edge(setup["chunk_size"], chunk["chunk_size"])
    wf.edge(setup["overlap"], chunk["overlap"])

    answer = wf.llm(
        "answer",
        prompt=(
            "Answer the following question based ONLY on the provided context.\n\n"
            "Question: {question}\n\n"
            "Context chunks:\n{chunks}\n\n"
            "If the context does not contain enough information, say so. "
            "Include your confidence level (high/medium/low)."
        ),
        output_schema=ANSWER_SCHEMA,
        input_ports=[{"name": "question"}, {"name": "chunks"}],
    )
    wf.edge(setup["question"], answer["question"])
    wf.edge(chunk["chunks"], answer["chunks"])

    return wf.build()


async def main(
    question: str = "What are the main features of this project?",
    file_path: str = "README.md",
) -> None:
    graph = build_rag_qa()

    graphs_dir = Path("graphs")
    graphs_dir.mkdir(exist_ok=True)
    graph_path = graphs_dir / "rag_qa.json"
    graph_path.write_text(json.dumps(graph.model_dump(mode="json"), indent=2))
    print(f"Graph saved to {graph_path}")

    api_key = os.environ.get("DAN_LLM_API_KEY", "")
    if api_key:
        tool_registry = ToolRegistry()
        tool_registry.register_builtin_tools()

        exec_registry = ExecutorRegistry()
        exec_registry.register("tool_operator", ToolExecutor(tool_registry))

        config = EngineConfig(llm_api_key=api_key)
        engine = Engine(config, executor_registry=exec_registry)
        result = await engine.run(
            graph,
            inputs={
                "question": question,
                "file_path": file_path,
            },
        )
        print(f"Success: {result.success}")
        print(f"Outputs: {json.dumps(result.outputs, indent=2, default=str)}")
    else:
        print("Set DAN_LLM_API_KEY to run the workflow")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG Q&A template")
    parser.add_argument("--question", default="What are the main features of this project?")
    parser.add_argument("--file", default="README.md")
    args = parser.parse_args()
    asyncio.run(main(args.question, args.file))
