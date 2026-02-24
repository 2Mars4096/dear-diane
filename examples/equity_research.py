"""Automated Equity Research Report — progressive workflow wrapping demo.

Demonstrates ``import_workflow()`` to build increasingly complex workflows
by wrapping simpler ones as composite nodes:

  Level 1: Data Gatherer   — Router + Tools + Code  (control edges)
  Level 2: Section Analyst  — imports Level 1, adds WhileLoop draft/review
  Level 3: Report Orchestrator — imports Level 2 inside ForEach, adds
           Reduce + HumanInTheLoop + Tool export  (context edges)

Input: a stock ticker (e.g. "NVDA") — all data from public APIs.

Usage:
    python examples/equity_research.py
    python examples/equity_research.py --ticker AAPL
    python examples/equity_research.py --build-only   # just validate the graph
"""

from __future__ import annotations

import asyncio
import json
import os
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
    ContextDeclaration,
    ContextMode,
    FailurePolicy,
    MergeStrategy,
)
from dan.models.graph import Graph

load_dotenv()

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

PLANNER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_name": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["company_name", "sections"],
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "polished_section": {"type": "string"},
        "accuracy_score": {"type": "number"},
        "feedback": {"type": "string"},
    },
    "required": ["polished_section", "accuracy_score", "feedback"],
}

# ---------------------------------------------------------------------------
# Code snippets for CodeOperator nodes
# ---------------------------------------------------------------------------

ENTRY_PASSTHROUGH = """\
result = {'section_name': section_name, 'ticker': ticker}
"""

NORMALIZE_CODE = """\
parts = []
for raw in [sec_data, market_data, news_data]:
    if raw:
        parts.append(str(raw))
clean = '\\n'.join(parts) if parts else 'No data retrieved.'
result = {'clean_data': clean[:8000]}
"""

INIT_REVIEW_CODE = """\
result = {
    'clean_data': clean_data,
    'section_name': section_name,
    'accuracy_score': 0.0,
    'feedback': 'Initial draft needed.',
}
"""

LOOP_STATE_CODE = """\
result = {
    'clean_data': clean_data,
    'section_name': section_name,
    'accuracy_score': accuracy_score,
    'feedback': feedback,
}
"""

ORCHESTRATOR_ENTRY_CODE = """\
result = {'ticker': ticker}
"""

PACK_TASKS_CODE = """\
tasks = [{'section_name': s, 'ticker': ticker} for s in sections]
result = {'tasks': tasks}
"""

UNPACK_TASK_CODE = """\
if isinstance(item, dict):
    result = {
        'section_name': str(item.get('section_name', '')),
        'ticker': str(item.get('ticker', '')),
    }
else:
    result = {'section_name': str(item), 'ticker': ''}
"""

FORMAT_REPORT_CODE = """\
body_parts = []
for s in sections:
    if isinstance(s, dict):
        body_parts.append(str(s.get('polished_section', s.get('text', str(s)))))
    else:
        body_parts.append(str(s))
body = '\\n\\n---\\n\\n'.join(body_parts)
header = f'# Equity Research Report: {company_name} ({ticker})\\n\\n'
disclaimer = '\\n\\n---\\n*Disclaimer: Auto-generated report. Not investment advice.*'
result = {'report': header + body + disclaimer, 'title': f'{ticker}_equity_research'}
"""

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

WRITER_PROMPT = (
    "You are an equity research analyst writing the '{section_name}' section "
    "of a professional report.\n\n"
    "Source data:\n{clean_data}\n\n"
    "Previous reviewer feedback:\n{feedback}\n\n"
    "Write an institutional-quality section. Cite specific numbers and sources."
)

REVIEWER_PROMPT = (
    "You are a senior equity research reviewer. Fact-check this section "
    "against the source data.\n\n"
    "Section draft:\n{draft}\n\n"
    "Source data:\n{clean_data}\n\n"
    "Score accuracy from 0.0 to 1.0. If below 0.85, provide specific "
    "revision feedback and a revised draft. Return valid JSON."
)

PLANNER_PROMPT = (
    "You are a senior equity research analyst. Given ticker {ticker}, "
    "produce a structured report outline.\n\n"
    "Include these sections: Executive Summary, Business Overview, "
    "Financial Analysis, Competitive Landscape, Risk Factors, "
    "Valuation & Price Target.\n\n"
    "Return valid JSON with company_name and sections array."
)


# ---------------------------------------------------------------------------
# Tool functions (mock implementations for demo — swap with real APIs)
# ---------------------------------------------------------------------------


async def fetch_sec_filing(ticker: str = "", **kw: Any) -> dict[str, Any]:
    """Fetch SEC EDGAR filing data (mock)."""
    return {
        "raw_data": (
            f"SEC 10-K for {ticker}: Revenue $60.9B (+122% YoY), "
            f"Gross margin 73.0%, Operating income $32.97B, "
            f"Data Center revenue $47.5B (+217%), "
            f"R&D expense $8.7B, Total assets $65.7B."
        ),
    }


async def fetch_market_data(ticker: str = "", **kw: Any) -> dict[str, Any]:
    """Fetch Yahoo Finance market data (mock)."""
    return {
        "raw_data": (
            f"Market data for {ticker}: Price $875.28, Market cap $2.15T, "
            f"P/E 65.3, EV/EBITDA 50.1, 52-week range $460-$975, "
            f"Avg volume 42.8M shares, Beta 1.68."
        ),
    }


async def fetch_news_articles(ticker: str = "", **kw: Any) -> dict[str, Any]:
    """Fetch recent news (mock)."""
    return {
        "raw_data": (
            f"Recent news for {ticker}: "
            f"(1) Announced new Blackwell Ultra GPU architecture, "
            f"(2) Expanded partnerships with major cloud providers, "
            f"(3) Automotive and robotics revenue growing 30% QoQ, "
            f"(4) Supply constraints easing with TSMC capacity expansion."
        ),
    }


async def save_report(content: str = "", title: str = "", **kw: Any) -> dict[str, Any]:
    """Save final report to disk."""
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    slug = title.replace(" ", "_")[:60] if title else "report"
    path = out_dir / f"{slug}.md"
    path.write_text(content, encoding="utf-8")
    return {"file_path": str(path), "title": title}


# ═══════════════════════════════════════════════════════════════════════════
# Level 1 — Data Gatherer
# ═══════════════════════════════════════════════════════════════════════════
#
# Standalone workflow:  entry → Router → 3 Tools (control-gated) → Normalize
# Demonstrates: CONTROL EDGES (router conditionally activates one tool)
# ═══════════════════════════════════════════════════════════════════════════


def build_data_gatherer() -> Graph:
    """Build a standalone data-fetching workflow.

    Takes (section_name, ticker) → returns (clean_data).
    A Router decides which data source to query, and control edges
    gate exactly one of three Tool nodes.
    """
    wf = workflow(
        "data_gatherer",
        description="Route a data request to SEC, market, or news source",
        tags=["equity-research", "data"],
    )

    entry = wf.code(
        "entry",
        code=ENTRY_PASSTHROUGH,
        input_ports=[{"name": "section_name"}, {"name": "ticker"}],
        output_ports=[{"name": "section_name"}, {"name": "ticker"}],
    )

    router = wf.router(
        "source_router",
        model="gpt-4o-mini",
        route_descriptions={
            "sec_edgar": "Financial Analysis, Risk Factors, or Business Overview — needs SEC filings",
            "market_data": "Valuation or Price Target — needs stock prices and multiples",
            "news": "Competitive Landscape or Executive Summary — needs recent news",
        },
        input_ports=[{"name": "section_name"}],
    )
    wf.edge(entry["section_name"], router["section_name"])

    sec_tool = wf.tool(
        "fetch_sec", tool_id="fetch_sec_filing",
        input_ports=[{"name": "ticker"}],
        output_ports=[{"name": "raw_data"}],
    )
    market_tool = wf.tool(
        "fetch_market", tool_id="fetch_market_data",
        input_ports=[{"name": "ticker"}],
        output_ports=[{"name": "raw_data"}],
    )
    news_tool = wf.tool(
        "fetch_news", tool_id="fetch_news_articles",
        input_ports=[{"name": "ticker"}],
        output_ports=[{"name": "raw_data"}],
    )

    # Data edges: ticker flows to all tools
    wf.edge(entry["ticker"], sec_tool["ticker"])
    wf.edge(entry["ticker"], market_tool["ticker"])
    wf.edge(entry["ticker"], news_tool["ticker"])

    # ★ CONTROL EDGES — router conditionally activates one tool
    wf.control_edge(router["route"], sec_tool["route"],
                    condition="route == 'sec_edgar'")
    wf.control_edge(router["route"], market_tool["route"],
                    condition="route == 'market_data'")
    wf.control_edge(router["route"], news_tool["route"],
                    condition="route == 'news'")

    normalize = wf.code(
        "normalize",
        code=NORMALIZE_CODE,
        input_ports=[
            {"name": "sec_data"},
            {"name": "market_data"},
            {"name": "news_data"},
        ],
        output_ports=[{"name": "clean_data"}],
    )
    wf.edge(sec_tool["raw_data"], normalize["sec_data"])
    wf.edge(market_tool["raw_data"], normalize["market_data"])
    wf.edge(news_tool["raw_data"], normalize["news_data"])

    return wf.build()


# ═══════════════════════════════════════════════════════════════════════════
# Level 2 — Section Analyst
# ═══════════════════════════════════════════════════════════════════════════
#
# Imports Level 1 as a composite node, adds a WhileLoop draft-review cycle.
# Demonstrates: DATA EDGES (draft flowing through the loop)
# ═══════════════════════════════════════════════════════════════════════════


def build_section_analyst(data_gatherer: Graph) -> Graph:
    """Build a section-drafting workflow that wraps the data gatherer.

    Takes (section_name, ticker) → returns (polished_section, accuracy_score).
    Internally imports the data_gatherer as a single composite node, then
    iteratively writes and reviews until accuracy_score >= 0.85.
    """
    wf = workflow(
        "section_analyst",
        description="Fetch data and iteratively draft one report section",
        tags=["equity-research", "section"],
    )

    # Entry splits inputs to data_gatherer and the review loop
    entry = wf.code(
        "entry",
        code=ENTRY_PASSTHROUGH,
        input_ports=[{"name": "section_name"}, {"name": "ticker"}],
        output_ports=[{"name": "section_name"}, {"name": "ticker"}],
    )

    # ★ IMPORT Level 1 as a composite node
    dg = wf.import_workflow("data_gatherer", data_gatherer)
    wf.edge(entry["section_name"], dg["section_name"])
    wf.edge(entry["ticker"], dg["ticker"])

    # Initialise loop state with defaults
    init_review = wf.code(
        "init_review",
        code=INIT_REVIEW_CODE,
        input_ports=[{"name": "clean_data"}, {"name": "section_name"}],
        output_ports=[
            {"name": "clean_data"},
            {"name": "section_name"},
            {"name": "accuracy_score"},
            {"name": "feedback"},
        ],
    )
    wf.edge(dg["clean_data"], init_review["clean_data"])
    wf.edge(entry["section_name"], init_review["section_name"])

    # WhileLoop: write → review → repeat until good enough
    with wf.while_loop(
        "draft_review",
        condition="accuracy_score < 0.85",
        max_iterations=3,
        compaction=CompactionRule(
            strategy=CompactionStrategy.SLIDING_WINDOW, window_size=2,
        ),
        failure_policy=FailurePolicy(max_iterations=3, stagnation_threshold=2),
        input_ports=[
            {"name": "clean_data"},
            {"name": "section_name"},
            {"name": "accuracy_score"},
            {"name": "feedback"},
        ],
        output_ports=[
            {"name": "polished_section"},
            {"name": "accuracy_score"},
            {"name": "feedback"},
        ],
    ) as loop:
        state = loop.code(
            "loop_state",
            code=LOOP_STATE_CODE,
            input_ports=[
                {"name": "clean_data"},
                {"name": "section_name"},
                {"name": "accuracy_score"},
                {"name": "feedback"},
            ],
            output_ports=[
                {"name": "clean_data"},
                {"name": "section_name"},
                {"name": "accuracy_score"},
                {"name": "feedback"},
            ],
        )

        writer = loop.llm(
            "section_writer",
            prompt=WRITER_PROMPT,
            input_ports=[
                {"name": "clean_data"},
                {"name": "section_name"},
                {"name": "feedback"},
            ],
        )
        loop.edge(state["clean_data"], writer["clean_data"])
        loop.edge(state["section_name"], writer["section_name"])
        loop.edge(state["feedback"], writer["feedback"])

        reviewer = loop.llm(
            "fact_checker",
            prompt=REVIEWER_PROMPT,
            output_schema=REVIEW_SCHEMA,
            input_ports=[{"name": "draft"}, {"name": "clean_data"}],
        )
        loop.edge(writer["text"], reviewer["draft"])
        loop.edge(state["clean_data"], reviewer["clean_data"])

    # Wire init state into the loop
    loop_ref = NodeRef("draft_review", "while_loop", wf)
    for port in ("clean_data", "section_name", "accuracy_score", "feedback"):
        wf.edge(init_review[port], loop_ref[port])

    return wf.build()


# ═══════════════════════════════════════════════════════════════════════════
# Level 3 — Report Orchestrator
# ═══════════════════════════════════════════════════════════════════════════
#
# Imports Level 2 inside a ForEach, adds Planner + Reduce + Human + Tool.
# Demonstrates: CONTEXT EDGES (cross-section knowledge sharing)
# ═══════════════════════════════════════════════════════════════════════════


def build_report_orchestrator(section_analyst: Graph) -> Graph:
    """Build the master report workflow that wraps the section analyst.

    Takes (ticker) → produces a full equity research report.
    The Planner breaks the ticker into sections, ForEach fans out over
    sections (each running the imported section_analyst composite),
    Reduce merges results, HumanInTheLoop reviews, and a Tool exports.
    """
    wf = workflow(
        "equity_report",
        description="Automated equity research report from a stock ticker",
        tags=["equity-research", "report"],
    )

    # Shared context: accumulated findings across parallel sections
    wf.context(
        "findings_kb",
        json_schema={"type": "array"},
        description="Key findings shared across parallel section drafters",
    )

    # ── Planner: ticker → structured outline ──────────────────────
    planner = wf.llm(
        "planner",
        prompt=PLANNER_PROMPT,
        output_schema=PLANNER_SCHEMA,
        input_ports=[{"name": "ticker"}],
    )

    # Pack section names + ticker into per-section task objects
    pack = wf.code(
        "pack_tasks",
        code=PACK_TASKS_CODE,
        input_ports=[{"name": "sections"}, {"name": "ticker"}],
        output_ports=[{"name": "tasks"}],
    )
    wf.edge(planner["sections"], pack["sections"])
    wf.edge(planner["ticker"], pack["ticker"])

    # ── ForEach: parallel section analysis ────────────────────────
    with wf.for_each(
        "section_pipeline",
        items=pack["tasks"],
        parallelism=3,
        merge_strategy=MergeStrategy.APPEND,
        write_set=[ContextDeclaration(key="findings_kb", mode=ContextMode.APPEND)],
        read_set=[ContextDeclaration(key="findings_kb", mode=ContextMode.READ)],
    ) as body:
        unpack = body.code(
            "unpack_task",
            code=UNPACK_TASK_CODE,
            input_ports=[{"name": "item"}],
            output_ports=[{"name": "section_name"}, {"name": "ticker"}],
        )

        # ★ IMPORT Level 2 as a composite node inside the ForEach body
        analyst = body.import_workflow("section_analyst", section_analyst)
        body.edge(unpack["section_name"], analyst["section_name"])
        body.edge(unpack["ticker"], analyst["ticker"])

    # ── Reduce: merge all section outputs ─────────────────────────
    pipeline_ref = NodeRef("section_pipeline", "for_each", wf)
    reducer = wf.reduce("merge_sections", reducer="concat")
    wf.edge(pipeline_ref["results"], reducer["input"])

    # ★ CONTEXT EDGE — ForEach publishes findings to shared context
    wf.context_edge(
        pipeline_ref["results"],
        reducer["input"],
        context_key="findings_kb",
        mode=ContextMode.APPEND,
    )

    # ── Format: assemble final document ───────────────────────────
    formatter = wf.code(
        "format_report",
        code=FORMAT_REPORT_CODE,
        input_ports=[
            {"name": "sections"},
            {"name": "company_name"},
            {"name": "ticker"},
        ],
        output_ports=[{"name": "report"}, {"name": "title"}],
    )
    wf.edge(reducer["result"], formatter["sections"])
    wf.edge(planner["company_name"], formatter["company_name"])
    wf.edge(planner["ticker"], formatter["ticker"])

    # ── Human review ──────────────────────────────────────────────
    human = wf.human_in_the_loop(
        "senior_review",
        prompt="Review this equity research report for accuracy and completeness:",
        timeout_seconds=7200,
        default_action="approve",
    )
    wf.edge(formatter["report"], human["input"])

    # ── Export ────────────────────────────────────────────────────
    export = wf.tool(
        "export_report",
        tool_id="save_report",
        input_ports=[{"name": "content"}, {"name": "title"}],
        output_ports=[{"name": "file_path"}, {"name": "title"}],
    )
    wf.edge(human["response"], export["content"])
    wf.edge(formatter["title"], export["title"])

    return wf.build()


# ---------------------------------------------------------------------------
# Progressive build
# ---------------------------------------------------------------------------


def build_all(*, save_json: bool = True) -> Graph:
    """Build all three levels, wrapping each into the next.

    When *save_json* is True (default), each level is saved as a
    separate JSON file under ``graphs/`` so the editor can load them.
    """
    graphs_dir = Path("graphs")
    graphs_dir.mkdir(exist_ok=True)

    print("Building Level 1: Data Gatherer …")
    dg = build_data_gatherer()
    print(f"  ✓ {len(dg.nodes)} nodes, {len(dg.edges)} edges")
    if save_json:
        p = graphs_dir / "equity_data_gatherer.json"
        p.write_text(dg.model_dump_json(indent=2), encoding="utf-8")
        print(f"  → saved {p}")

    print("Building Level 2: Section Analyst (imports Level 1) …")
    sa = build_section_analyst(dg)
    print(f"  ✓ {len(sa.nodes)} nodes, {len(sa.edges)} edges, "
          f"{len(sa.sub_graphs)} sub-graphs")
    if save_json:
        p = graphs_dir / "equity_section_analyst.json"
        p.write_text(sa.model_dump_json(indent=2), encoding="utf-8")
        print(f"  → saved {p}")

    print("Building Level 3: Report Orchestrator (imports Level 2) …")
    report = build_report_orchestrator(sa)
    print(f"  ✓ {len(report.nodes)} nodes, {len(report.edges)} edges, "
          f"{len(report.sub_graphs)} sub-graphs")
    if save_json:
        p = graphs_dir / "equity_report_orchestrator.json"
        p.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        print(f"  → saved {p}")

    return report


# ---------------------------------------------------------------------------
# Engine wiring
# ---------------------------------------------------------------------------


def create_engine(
    *,
    event_callback: Any = None,
    human_input_callback: Any = None,
) -> Engine:
    config = EngineConfig(
        llm_base_url=os.getenv("DAN_LLM_BASE_URL", "https://api.vectorengine.ai/v1"),
        llm_api_key=os.getenv("DAN_LLM_API_KEY", ""),
        llm_default_model=os.getenv("DAN_LLM_DEFAULT_MODEL", "claude-sonnet-4-6"),
        checkpoint_enabled=False,
    )

    tool_registry = ToolRegistry()
    tool_registry.register("fetch_sec_filing", fetch_sec_filing)
    tool_registry.register("fetch_market_data", fetch_market_data)
    tool_registry.register("fetch_news_articles", fetch_news_articles)
    tool_registry.register("save_report", save_report)

    exec_registry = ExecutorRegistry()
    exec_registry.register("tool_operator", ToolExecutor(tool_registry))

    return Engine(
        config=config,
        executor_registry=exec_registry,
        checkpoint_store=NullCheckpointStore(),
        human_input_callback=human_input_callback,
        event_callback=event_callback,
    )


async def log_event(event: EngineEvent) -> None:
    node_label = f" [{event.node_id}]" if event.node_id else ""
    if event.event_type == EventType.RUN_STARTED:
        print(f"\n{'='*60}\n  Run started — {event.data.get('node_count', '?')} nodes\n{'='*60}")
    elif event.event_type == EventType.NODE_STARTED:
        print(f"  → Starting{node_label} ({event.node_type})")
    elif event.event_type == EventType.NODE_COMPLETED:
        print(f"  ← Completed{node_label}")
    elif event.event_type == EventType.NODE_FAILED:
        print(f"  !! FAILED{node_label}: {str(event.data.get('error', ''))[:120]}")
    elif event.event_type == EventType.RUN_COMPLETED:
        print(f"\n{'='*60}\n  Run completed\n{'='*60}")
    elif event.event_type == EventType.RUN_FAILED:
        print(f"\n{'='*60}\n  Run FAILED: {event.data.get('errors', {})}\n{'='*60}")


async def cli_human_input(prompt: str) -> str:
    print(f"\n{'='*72}\n{prompt}\n{'='*72}")
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, input, "Your response: ")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(ticker: str, build_only: bool = False) -> None:
    graph = build_all(save_json=True)

    if build_only:
        print("Build-only mode — skipping execution.")
        return

    engine = create_engine(
        event_callback=log_event,
        human_input_callback=cli_human_input,
    )
    result = await engine.run(graph, inputs={"ticker": ticker})

    if result.success:
        print("\nOutputs:")
        for k in ("file_path", "title"):
            if k in result.outputs:
                print(f"  {k}: {result.outputs[k]}")
    else:
        print(f"\nWorkflow failed!\nErrors: {json.dumps(result.errors, indent=2)}")
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Equity research report demo")
    parser.add_argument("--ticker", default="NVDA")
    parser.add_argument("--build-only", action="store_true",
                        help="Build and validate graph without executing")
    args = parser.parse_args()

    asyncio.run(main(args.ticker, args.build_only))
