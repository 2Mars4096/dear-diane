"""End-to-end paper-writing workflow using the DAN builder DSL.

Demonstrates all core node types:
  - LLMOperator (5 nodes) with structured and unstructured outputs
  - ForEach parallel section writing with APPEND merge
  - WhileLoop iterative review-revise cycle
  - CodeOperator for assembly and formatting
  - ToolOperator with custom ToolRegistry wiring

Usage:
    python examples/paper_writing.py "supply chain resilience"
    python examples/paper_writing.py --topic "deep learning optimization" --max-review 5
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from dan.builder import workflow
from dan.builder.refs import NodeRef
from dan.engine import Engine, EngineConfig, EngineEvent, EventType
from dan.engine.checkpoint import NullCheckpointStore
from dan.engine.executor import ExecutorRegistry
from dan.executors.tool import ToolExecutor, ToolRegistry
from dan.models.context import (
    CompactionRule,
    CompactionStrategy,
    FailurePolicy,
    MergeStrategy,
)

load_dotenv()
logger = logging.getLogger(__name__)


# ── Tool functions ────────────────────────────────────────────────────


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


async def save_paper(content: str, title: str, **kwargs: Any) -> dict[str, str]:
    """Save the final paper as a markdown file."""
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    slug = _slugify(title)
    md_path = output_dir / f"{slug}.md"
    md_path.write_text(content, encoding="utf-8")
    return {"saved_path": str(md_path), "title": title}


# ── Code snippets executed by CodeOperator nodes ─────────────────────

ASSEMBLE_CODE = """\
sections_content = []
for section_result in results:
    text = section_result.get('text', str(section_result))
    sections_content.append(text)

body = '\\n\\n'.join(sections_content)
draft = '# ' + str(title) + '\\n\\n## Abstract\\n\\n' + str(abstract) + '\\n\\n' + body
result = {'draft': draft, 'verdict': 'pending', 'feedback': 'Initial draft, no previous feedback.'}
"""

FORMAT_CODE = """\
result = {'content': draft, 'title': title}
"""

REVIEW_PROMPT = (
    "You are a rigorous academic paper reviewer and skilled reviser.\n\n"
    "Current draft:\n{draft}\n\n"
    "Previous feedback: {feedback}\n\n"
    "Tasks:\n"
    "1. Review the draft critically for clarity, argument structure, evidence, and writing quality.\n"
    "2. Revise the draft to address all issues you identified.\n"
    "3. If the draft is now publication-ready, set verdict to 'accept'. Otherwise, set to 'revise'.\n\n"
    "You MUST output valid JSON with exactly these keys:\n"
    '  {{"verdict": "accept" or "revise", "feedback": "your review comments", "draft": "the full revised paper text"}}'
)

# ── Schemas ───────────────────────────────────────────────────────────

OUTLINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "abstract": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "abstract", "sections"],
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "revise"]},
        "feedback": {"type": "string"},
        "draft": {"type": "string"},
    },
    "required": ["verdict", "feedback", "draft"],
}


# ── Workflow definition ───────────────────────────────────────────────


def build_paper_workflow(max_review_iterations: int = 3) -> Any:
    """Build the paper-writing workflow graph and return (graph, wf)."""
    wf = workflow(
        "paper_writing",
        description=(
            "Multi-agent paper writing with literature survey, "
            "parallel section writing, and iterative review-revise."
        ),
        tags=["paper-writing", "multi-agent", "demo"],
    )

    # 1. Idea generation
    idea = wf.llm(
        "idea_gen",
        prompt=(
            "You are a research AI. Generate a compelling research idea about: {topic}\n\n"
            "Output a 2-3 paragraph description of the research idea, "
            "including its novelty and potential impact."
        ),
    )

    # 2. Literature survey (f-string magic creates edge idea_gen:text -> lit_survey)
    lit = wf.llm(
        "lit_survey",
        prompt=(
            f"You are a literature review specialist.\n\n"
            f"Survey relevant literature for the following research idea:\n\n{idea}\n\n"
            f"Identify 5-7 relevant papers/topics and explain how they relate. "
            f"Include key findings and gaps the research idea could address."
        ),
    )

    # 3. Outline planner (structured output -> title, abstract, sections)
    outline = wf.llm(
        "outline_planner",
        prompt=(
            f"You are an expert academic paper planner.\n\n"
            f"Research Idea:\n{idea}\n\n"
            f"Literature Background:\n{lit}\n\n"
            f"Create a detailed paper outline. Output valid JSON with keys:\n"
            f'  {{"title": "paper title", "abstract": "150-word abstract", '
            f'"sections": ["Introduction", "Related Work", ...]}}'
        ),
        output_schema=OUTLINE_SCHEMA,
    )

    # 4. Parallel section writing (ForEach over outline.sections)
    with wf.for_each(
        "section_writers",
        items=outline["sections"],
        parallelism=3,
        merge_strategy=MergeStrategy.APPEND,
    ) as section_body:
        section_body.llm(
            "write_section",
            prompt=(
                "Write a detailed section for an academic paper.\n\n"
                "Section title: {item}\n\n"
                "Write 3-5 paragraphs of substantive content. "
                "Use formal academic tone with clear topic sentences."
            ),
            input_ports=[{"name": "item"}, {"name": "index"}],
        )

    # 5. Assembly (Code node combines sections + outline metadata into draft)
    assembler = wf.code(
        "assembler",
        code=ASSEMBLE_CODE,
        input_ports=[
            {"name": "results"},
            {"name": "title"},
            {"name": "abstract"},
        ],
        output_ports=[
            {"name": "draft"},
            {"name": "verdict"},
            {"name": "feedback"},
        ],
    )

    section_writers_ref = NodeRef("section_writers", "for_each", wf)
    wf.edge(section_writers_ref["results"], assembler["results"])
    wf.edge(outline["title"], assembler["title"])
    wf.edge(outline["abstract"], assembler["abstract"])

    # 6. Review-revise loop (WhileLoop with structured LLM output)
    with wf.while_loop(
        "review_loop",
        condition="verdict != 'accept'",
        max_iterations=max_review_iterations,
        compaction=CompactionRule(
            strategy=CompactionStrategy.SLIDING_WINDOW, window_size=2
        ),
        failure_policy=FailurePolicy(
            max_iterations=max_review_iterations, stagnation_threshold=2
        ),
        input_ports=[
            {"name": "draft"},
            {"name": "verdict"},
            {"name": "feedback"},
        ],
        output_ports=[
            {"name": "draft"},
            {"name": "verdict"},
            {"name": "feedback"},
        ],
    ) as loop_body:
        loop_body.llm(
            "review_and_revise",
            prompt=REVIEW_PROMPT,
            output_schema=REVIEW_SCHEMA,
            input_ports=[{"name": "draft"}, {"name": "feedback"}],
        )

    review_ref = NodeRef("review_loop", "while_loop", wf)
    wf.edge(assembler["draft"], review_ref["draft"])
    wf.edge(assembler["verdict"], review_ref["verdict"])
    wf.edge(assembler["feedback"], review_ref["feedback"])

    # 7. Format output (Code node)
    format_node = wf.code(
        "format_output",
        code=FORMAT_CODE,
        input_ports=[{"name": "draft"}, {"name": "title"}],
        output_ports=[{"name": "content"}, {"name": "title"}],
    )

    wf.edge(review_ref["draft"], format_node["draft"])
    wf.edge(outline["title"], format_node["title"])

    # 8. Save paper (Tool node)
    save = wf.tool(
        "save_paper",
        tool_id="save_paper",
        input_ports=[{"name": "content"}, {"name": "title"}],
        output_ports=[{"name": "saved_path"}, {"name": "title"}],
    )

    wf.edge(format_node["content"], save["content"])
    wf.edge(format_node["title"], save["title"])

    graph = wf.build()
    return graph


# ── Engine setup ──────────────────────────────────────────────────────


def create_engine(
    *, checkpoint_enabled: bool = False, event_callback: Any = None
) -> Engine:
    """Create an Engine with custom ToolRegistry wiring."""
    config = EngineConfig(
        llm_base_url=os.getenv("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
        llm_api_key=os.getenv("DAN_LLM_API_KEY", ""),
        llm_default_model=os.getenv("DAN_LLM_DEFAULT_MODEL", "claude-sonnet-4-6"),
        checkpoint_enabled=checkpoint_enabled,
    )

    tool_registry = ToolRegistry()
    tool_registry.register("save_paper", save_paper)

    exec_registry = ExecutorRegistry()
    exec_registry.register("tool_operator", ToolExecutor(tool_registry))

    checkpoint_store = None if checkpoint_enabled else NullCheckpointStore()

    return Engine(
        config=config,
        executor_registry=exec_registry,
        checkpoint_store=checkpoint_store,
        event_callback=event_callback,
    )


# ── Event logger ──────────────────────────────────────────────────────


async def log_event(event: EngineEvent) -> None:
    """Print engine events for progress tracking."""
    node_label = f" [{event.node_id}]" if event.node_id else ""
    if event.event_type == EventType.RUN_STARTED:
        n = event.data.get("node_count", "?")
        print(f"\n{'='*60}")
        print(f"  Run started — {n} nodes")
        print(f"{'='*60}")
    elif event.event_type == EventType.NODE_STARTED:
        print(f"  -> Starting{node_label} ({event.node_type})")
    elif event.event_type == EventType.NODE_COMPLETED:
        meta = event.data.get("metadata", {})
        extra = ""
        if "iterations" in meta:
            extra = f" ({meta['iterations']} iterations)"
        elif "attempts" in meta:
            extra = f" (attempt {meta['attempts']})"
        print(f"  <- Completed{node_label}{extra}")
    elif event.event_type == EventType.NODE_FAILED:
        err = event.data.get("error", "")
        print(f"  !! FAILED{node_label}: {err[:120]}")
    elif event.event_type == EventType.RUN_COMPLETED:
        print(f"\n{'='*60}")
        print(f"  Run completed successfully")
        print(f"{'='*60}")
    elif event.event_type == EventType.RUN_FAILED:
        print(f"\n{'='*60}")
        print(f"  Run FAILED: {event.data.get('errors', {})}")
        print(f"{'='*60}")


# ── Main ──────────────────────────────────────────────────────────────


async def main(topic: str, max_review: int = 3) -> None:
    graph = build_paper_workflow(max_review_iterations=max_review)

    # Save graph JSON for visual editor
    graphs_dir = Path("graphs")
    graphs_dir.mkdir(exist_ok=True)
    graph_path = graphs_dir / "paper_writing.json"
    graph_path.write_text(graph.model_dump_json(indent=2), encoding="utf-8")
    print(f"Graph saved to {graph_path}")

    engine = create_engine(event_callback=log_event)
    result = await engine.run(graph, inputs={"topic": topic})

    if result.success:
        saved = result.outputs.get("saved_path", "")
        print(f"\nPaper saved to: {saved}")
        print(f"Node statuses: {result.node_statuses}")
    else:
        print(f"\nWorkflow failed!")
        print(f"Errors: {json.dumps(result.errors, indent=2)}")
        print(f"Node statuses: {result.node_statuses}")
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Paper-writing workflow demo")
    parser.add_argument("topic", nargs="?", default="supply chain resilience under climate change")
    parser.add_argument("--max-review", type=int, default=3)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    asyncio.run(main(args.topic, args.max_review))
