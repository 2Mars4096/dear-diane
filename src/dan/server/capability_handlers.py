"""Capability handlers — thin wrappers around existing subsystems.

Each handler has signature:
    async def handler(args: dict, context: CapabilityContext) -> CapabilityResult

Sub-plans (25-2, 25-3, 25-4) add domain-specific handlers here.
"""

from __future__ import annotations

import json
import logging
import os
import atexit
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from dan.server.capability_registry import (
    ALL_MODES,
    READ_ONLY_MODES,
    CapabilityContext,
    CapabilityResult,
    ChatCapabilityRegistry,
    build_tool_schema,
)

logger = logging.getLogger(__name__)

# Write modes for publish/export/import tools
WRITE_MODES = ["agent", "build", "mutate"]


# ── Formatting helpers ─────────────────────────────────────────────

_EXPORT_CLEANUP_DELAY = 300  # seconds before temp export dirs are removed


def _schedule_export_cleanup(path: str) -> None:
    """Schedule removal of a temp export directory after a delay."""
    def _cleanup() -> None:
        shutil.rmtree(path, ignore_errors=True)
    timer = threading.Timer(_EXPORT_CLEANUP_DELAY, _cleanup)
    timer.daemon = True
    timer.start()


def _truncate(text: str, limit: int = 500) -> str:
    return text[:limit] + "…" if len(text) > limit else text


# ── Graph tools ────────────────────────────────────────────────────

LIST_GRAPHS_SCHEMA = build_tool_schema(
    name="list_graphs",
    description=(
        "List all saved workflows. Use when the user asks "
        "'what workflows exist?', 'show my workflows', or 'list graphs'."
    ),
    parameters={
        "type": "object",
        "properties": {},
    },
)


async def handle_list_graphs(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    graphs = ctx.graph_store.list_graphs()
    if not graphs:
        return CapabilityResult(
            success=True,
            message="No workflows found.",
            data=[],
            output_preview="No workflows found.",
        )
    lines = []
    for g in graphs:
        gid = g.get("graph_id", g.get("id", "?"))
        node_count = len(g.get("nodes", []))
        edge_count = len(g.get("edges", []))
        lines.append(f"- **{gid}** ({node_count} nodes, {edge_count} edges)")
    text = f"Found {len(graphs)} workflow(s):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=graphs,
        output_preview=_truncate(text),
    )


# ── Activity tools ─────────────────────────────────────────────────

GET_ACTIVITY_SCHEMA = build_tool_schema(
    name="get_activity",
    description=(
        "Show current run activity: active runs, recent completions, and "
        "connected surfaces. Use when the user asks 'what's running?', "
        "'show activity', or 'any active runs?'."
    ),
    parameters={
        "type": "object",
        "properties": {},
    },
)


async def handle_get_activity(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.activity_tracker is None:
        return CapabilityResult(
            success=False,
            message="Activity tracker not available.",
        )
    snapshot = ctx.activity_tracker.get_activity()
    active = snapshot.get("active", [])
    recent = snapshot.get("recent", [])
    surfaces = snapshot.get("connected_surfaces", [])
    parts = []
    if active:
        parts.append(f"**Active runs ({len(active)}):**")
        for r in active[:10]:
            rid = r.get("run_id", "?")
            gid = r.get("graph_id", "?")
            status = r.get("status", "?")
            parts.append(f"  - {rid} ({gid}) — {status}")
    else:
        parts.append("No active runs.")

    if recent:
        parts.append(f"\n**Recent ({len(recent)}):**")
        for r in recent[:5]:
            rid = r.get("run_id", "?")
            gid = r.get("graph_id", "?")
            status = r.get("status", "?")
            parts.append(f"  - {rid} ({gid}) — {status}")

    if surfaces:
        parts.append(f"\n**Connected surfaces:** {len(surfaces)}")

    text = "\n".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=snapshot,
        output_preview=_truncate(text),
    )


# ── Experience tools (25-2) ─────────────────────────────────────────

SEARCH_WORKFLOW_HISTORY_SCHEMA = build_tool_schema(
    name="search_workflow_history",
    description=(
        "Search past workflows by semantic similarity. Use when the user asks "
        "'have we done X before?', 'show workflows similar to Y', or 'find past work on Z'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural-language search query"},
            "top_k": {"type": "integer", "description": "Max results to return", "default": 5},
        },
        "required": ["query"],
    },
)

GET_WORKFLOW_DETAILS_SCHEMA = build_tool_schema(
    name="get_workflow_details",
    description=(
        "Get detailed info about a specific workflow: name, description, success rate, "
        "node types, tools used, failure/success patterns. Use when the user asks "
        "'tell me about workflow X' or 'what's the success rate of Y?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "Workflow ID to look up"},
        },
        "required": ["workflow_id"],
    },
)

SEARCH_RUN_HISTORY_SCHEMA = build_tool_schema(
    name="search_run_history",
    description=(
        "Search past runs by workflow, status, or date range. Use when the user asks "
        "'show failed runs', 'recent runs', 'runs for workflow X', or 'runs from last week'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "Filter by workflow ID (optional)"},
            "status": {"type": "string", "enum": ["completed", "failed", "cancelled"], "description": "Filter by status"},
            "limit": {"type": "integer", "description": "Max results", "default": 20},
            "offset": {"type": "integer", "description": "Skip N results", "default": 0},
            "after": {"type": "number", "description": "Unix timestamp: only runs after this time"},
            "before": {"type": "number", "description": "Unix timestamp: only runs before this time"},
        },
    },
)

GET_LEARNED_PRINCIPLES_SCHEMA = build_tool_schema(
    name="get_learned_principles",
    description=(
        "Query causal principles learned from past failures. Use when the user asks "
        "'what have we learned from failures?', 'show principles for workflow X', "
        "or 'what do we know about tool/timeout errors?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Substring filter on condition/action/reason (optional)"},
            "workflow_id": {"type": "string", "description": "Scope to workflow, or omit for global"},
            "min_confidence": {"type": "number", "description": "Min confidence 0–1", "default": 0.3},
            "limit": {"type": "integer", "description": "Max results", "default": 10},
        },
    },
)

DISCOVER_CAPABILITIES_SCHEMA = build_tool_schema(
    name="discover_capabilities",
    description=(
        "Discover available tools, skills, patterns, and relevant past workflows. "
        "Use when the user asks 'what can DAN do?', 'what patterns exist for RAG?', "
        "or 'what tools/skills are available?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query for workflows and self-knowledge"},
            "top_k": {"type": "integer", "description": "Max workflow matches", "default": 5},
        },
        "required": ["query"],
    },
)


def _format_experience_summary(exp: Any, score: float | None = None) -> str:
    """Format a WorkflowExperience for chat display."""
    parts = [f"**{exp.workflow_id}**"]
    if score is not None:
        parts[0] += f" (score: {score:.2f})"
    if exp.name:
        parts.append(f"  Name: {exp.name}")
    desc = _truncate(exp.description, 200) if exp.description else ""
    if desc:
        parts.append(f"  Description: {desc}")
    if exp.run_count > 0:
        rate = 100 * exp.success_count / exp.run_count
        parts.append(f"  Success rate: {rate:.0f}% ({exp.success_count}/{exp.run_count} runs)")
    if exp.node_types_used:
        parts.append(f"  Node types: {', '.join(exp.node_types_used[:5])}")
    if exp.tools_used:
        parts.append(f"  Tools: {', '.join(exp.tools_used[:5])}")
    return " — ".join(parts)


async def handle_search_workflow_history(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    query = str(args.get("query", "")).strip()
    top_k = max(1, min(int(args.get("top_k", 5)), 20))

    if not query:
        return CapabilityResult(success=False, message="query is required.")

    store = ctx.experience_store
    index = ctx.experience_index

    if store is None and index is None:
        return CapabilityResult(
            success=False,
            message="Experience store not available. Semantic search requires embedding provider.",
        )

    if index is not None:
        try:
            hits = await index.search_similar(query, top_k=top_k)
            results: list[tuple[str, float]] = hits
        except Exception as exc:
            logger.debug("ExperienceIndex.search_similar failed, falling back to lexical", exc_info=True)
            results = []
            index = None
    else:
        results = []

    if not results and store is not None:
        experiences = await store.list_experiences()
        q_lower = query.lower()
        scored: list[tuple[Any, float]] = []
        for exp in experiences:
            text = f"{exp.name} {exp.description} {' '.join(exp.tags or [])}".lower()
            if q_lower in text:
                pos = text.find(q_lower)
                rank = 1.0 - (pos / max(len(text), 1)) * 0.3
                scored.append((exp, min(1.0, rank)))
        scored.sort(key=lambda x: x[1], reverse=True)
        results = [(e.workflow_id, s) for e, s in scored[:top_k]]

    if not results:
        return CapabilityResult(
            success=True,
            message="No similar workflows found.",
            data=[],
            output_preview="No similar workflows found.",
        )

    lines = []
    data: list[dict[str, Any]] = []
    for wf_id, score in results:
        exp = None
        if store is not None:
            exp = await store.load_experience(wf_id)
        if exp is None:
            lines.append(f"- **{wf_id}** (score: {score:.2f}) — no experience record")
            data.append({"workflow_id": wf_id, "score": score})
        else:
            line = f"- {_format_experience_summary(exp, score)}"
            lines.append(line)
            data.append({
                "workflow_id": wf_id,
                "score": score,
                "name": exp.name,
                "description": exp.description,
                "success_rate": exp.success_count / exp.run_count if exp.run_count > 0 else None,
            })

    text = f"Found {len(lines)} similar workflow(s):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=data,
        output_preview=_truncate(text),
    )


async def handle_get_workflow_details(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    workflow_id = str(args.get("workflow_id", "")).strip()
    if not workflow_id:
        return CapabilityResult(success=False, message="workflow_id is required.")

    store = ctx.experience_store
    if store is None:
        return CapabilityResult(success=False, message="Experience store not available.")

    exp = await store.load_experience(workflow_id)
    if exp is None:
        return CapabilityResult(
            success=True,
            message=f"No experience recorded for workflow '{workflow_id}'.",
            data=None,
            output_preview=f"No experience recorded for '{workflow_id}'.",
        )

    parts = [
        f"**{exp.workflow_id}**",
        f"Name: {exp.name or '(none)'}",
        f"Description: {_truncate(exp.description or '', 300)}",
    ]
    if exp.run_count > 0:
        rate = 100 * exp.success_count / exp.run_count
        parts.append(f"Success rate: {rate:.0f}% ({exp.success_count}/{exp.run_count} runs)")
    if exp.last_run_at:
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(exp.last_run_at, tz=timezone.utc)
        parts.append(f"Last run: {dt.isoformat()}")
    if exp.node_types_used:
        parts.append(f"Node types: {', '.join(exp.node_types_used)}")
    if exp.tools_used:
        parts.append(f"Tools used: {', '.join(exp.tools_used)}")
    if exp.failure_patterns:
        parts.append(f"Failure patterns: {'; '.join(exp.failure_patterns[:3])}")
    if exp.success_patterns:
        parts.append(f"Success patterns: {'; '.join(exp.success_patterns[:3])}")

    text = "\n".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=exp.model_dump() if hasattr(exp, "model_dump") else exp,
        output_preview=_truncate(text),
    )


async def handle_search_run_history(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    run_store = ctx.run_store
    if run_store is None:
        return CapabilityResult(success=False, message="Run store not available.")

    workflow_id = args.get("workflow_id") or None
    if workflow_id is not None:
        workflow_id = str(workflow_id).strip() or None
    status = args.get("status")
    limit = max(1, min(int(args.get("limit", 20)), 100))
    offset = max(0, int(args.get("offset", 0)))
    after = args.get("after")
    before = args.get("before")
    if after is not None:
        after = float(after)
    if before is not None:
        before = float(before)

    summaries = run_store.list_summaries(
        workflow_id=workflow_id,
        status=status,
        after=after,
        before=before,
        limit=limit,
        offset=offset,
    )

    if not summaries:
        return CapabilityResult(
            success=True,
            message="No runs found.",
            data=[],
            output_preview="No runs found.",
        )

    lines = []
    for s in summaries:
        rid = s.get("run_id", "?")
        gid = s.get("workflow_id", s.get("graph_id", "?"))
        st = s.get("status", "?")
        elapsed = s.get("elapsed_seconds")
        cost = s.get("total_cost")
        tokens = s.get("total_tokens")
        err = s.get("error", "")
        line = f"- **{rid}** ({gid}) — {st}"
        if elapsed is not None:
            line += f", {elapsed:.1f}s"
        if cost is not None:
            line += f", ${cost:.4f}"
        if tokens is not None:
            line += f", {tokens} tokens"
        if err:
            line += f" — error: {_truncate(err, 80)}"
        lines.append(line)

    text = f"Found {len(summaries)} run(s):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=summaries,
        output_preview=_truncate(text),
    )


async def handle_get_learned_principles(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    principle_store = ctx.principle_store
    if principle_store is None:
        return CapabilityResult(success=False, message="Principle store not available.")

    query = str(args.get("query", "")).strip()
    workflow_id = args.get("workflow_id")
    use_global = not (workflow_id and str(workflow_id).strip())
    scope = "global" if use_global else "workflow"
    wf_id = "_global" if use_global else str(workflow_id).strip()
    min_confidence = max(0.0, min(1.0, float(args.get("min_confidence", 0.3))))
    limit = max(1, min(int(args.get("limit", 10)), 50))

    principles = await principle_store.load_principles(
        wf_id,
        min_confidence=min_confidence,
        scope=scope,
    )

    if query:
        q_lower = query.lower()
        principles = [
            p for p in principles
            if q_lower in (p.condition or "").lower()
            or q_lower in (p.action or "").lower()
            or q_lower in (p.reason or "").lower()
        ]

    principles = sorted(principles, key=lambda p: p.confidence, reverse=True)[:limit]

    if not principles:
        return CapabilityResult(
            success=True,
            message="No learned principles found.",
            data=[],
            output_preview="No learned principles found.",
        )

    lines = []
    for i, p in enumerate(principles, 1):
        cond = _truncate(p.condition or "", 100)
        act = _truncate(p.action or "", 100)
        conf = int(p.confidence * 100)
        wf = p.workflow_id or "unknown"
        lines.append(f"{i}. If {cond} → {act} (confidence: {conf}%, from {wf})")

    scope_label = "global" if scope == "global" else f"workflow {wf_id}"
    text = f"{len(principles)} principle(s) ({scope_label}):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=[p.model_dump() if hasattr(p, "model_dump") else p for p in principles],
        output_preview=_truncate(text),
    )


async def handle_discover_capabilities(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    discovery = ctx.discovery_service
    if discovery is None:
        return CapabilityResult(success=False, message="Discovery service not available.")

    query = str(args.get("query", "")).strip()
    top_k = max(1, min(int(args.get("top_k", 5)), 20))

    result = await discovery.discover_all(query, top_k=top_k)

    parts = []
    if result.tools:
        parts.append("**Tools:**")
        for t in result.tools[:10]:
            parts.append(f"  - {t.tool_id}: {_truncate(t.description or '', 80)}")
    if result.skills:
        parts.append("\n**Skills:**")
        for s in result.skills[:10]:
            parts.append(f"  - {s.name}: {_truncate(s.description or '', 80)}")
    if result.patterns:
        parts.append("\n**Patterns:**")
        for p in result.patterns[:10]:
            parts.append(f"  - {p.name}: {_truncate(p.description or '', 80)}")
    if result.workflows:
        parts.append("\n**Relevant workflows:**")
        for w in result.workflows[:top_k]:
            rate = f", {int((w.success_rate or 0) * 100)}% success" if w.success_rate is not None else ""
            parts.append(f"  - {w.workflow_id} (score: {w.score:.2f}{rate})")
    if result.self_knowledge_formatted:
        sk = _truncate(result.self_knowledge_formatted, 500)
        parts.append(f"\n**Self-knowledge:** {sk}")

    text = "\n".join(parts) if parts else "No capabilities found."
    return CapabilityResult(
        success=True,
        message=text,
        data={
            "tools": [t.model_dump() for t in result.tools],
            "skills": [s.model_dump() for s in result.skills],
            "patterns": [p.model_dump() for p in result.patterns],
            "workflows": [w.model_dump() for w in result.workflows],
        },
        output_preview=_truncate(text),
    )


def register_experience_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register experience/discovery tools (25-2)."""
    registry.register(
        "search_workflow_history",
        SEARCH_WORKFLOW_HISTORY_SCHEMA,
        handle_search_workflow_history,
        modes=list(ALL_MODES),
        category="experience",
    )
    registry.register(
        "get_workflow_details",
        GET_WORKFLOW_DETAILS_SCHEMA,
        handle_get_workflow_details,
        modes=list(ALL_MODES),
        category="experience",
    )
    registry.register(
        "search_run_history",
        SEARCH_RUN_HISTORY_SCHEMA,
        handle_search_run_history,
        modes=list(ALL_MODES),
        category="experience",
    )
    registry.register(
        "get_learned_principles",
        GET_LEARNED_PRINCIPLES_SCHEMA,
        handle_get_learned_principles,
        modes=list(ALL_MODES),
        category="experience",
    )
    registry.register(
        "discover_capabilities",
        DISCOVER_CAPABILITIES_SCHEMA,
        handle_discover_capabilities,
        modes=list(ALL_MODES),
        category="experience",
    )


# ── Publish & Share tools (25-3) ─────────────────────────────────────

PUBLISH_WORKFLOW_SCHEMA = build_tool_schema(
    name="publish_workflow",
    description=(
        "Publish a workflow to the MCP/HTTP endpoint. Use when the user asks "
        "'publish this workflow', 'make it available as MCP', or 'publish as API'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"},
            "name_override": {"type": "string", "description": "Override workflow name in registry"},
            "api_key": {"type": "string", "description": "Optional API key for auth"},
            "rate_limit": {"type": "integer", "description": "Optional max requests per minute"},
        },
    },
)

UNPUBLISH_WORKFLOW_SCHEMA = build_tool_schema(
    name="unpublish_workflow",
    description=(
        "Remove a workflow from the publish registry. Use when the user asks "
        "'unpublish', 'stop publishing', or 'remove from API'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"},
        },
    },
)

EXPORT_WORKFLOW_SCHEMA = build_tool_schema(
    name="export_workflow",
    description=(
        "Export a workflow as block, markdown, or Python. Use when the user asks "
        "'export as block', 'export to markdown', 'export as Python', or 'share as block'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"},
            "format": {
                "type": "string",
                "enum": ["block", "markdown", "python"],
                "description": "Export format",
            },
            "name": {"type": "string", "description": "Block/workflow name (for block format)"},
            "version": {"type": "string", "description": "Block version (for block format)", "default": "0.1.0"},
            "node_id": {"type": "string", "description": "Composite node ID (for composite block export)"},
        },
        "required": ["format"],
    },
)

SHARE_WORKFLOW_SCHEMA = build_tool_schema(
    name="share_workflow",
    description=(
        "Generate shareable config: MCP client config, API docs, or OpenAPI spec. "
        "Use when the user asks 'share as MCP config', 'API docs', 'OpenAPI spec', or 'how to connect'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"},
            "format": {
                "type": "string",
                "enum": ["mcp_config", "api_docs", "openapi"],
                "description": "Share format",
            },
            "base_url": {"type": "string", "description": "Base URL for API docs", "default": "http://localhost:8001"},
        },
        "required": ["format"],
    },
)

LIST_PUBLISHED_SCHEMA = build_tool_schema(
    name="list_published",
    description=(
        "List all currently published workflows. Use when the user asks "
        "'what's published?', 'list published workflows', or 'show published APIs'."
    ),
    parameters={"type": "object", "properties": {}},
)

GET_PUBLISH_STATUS_SCHEMA = build_tool_schema(
    name="get_publish_status",
    description=(
        "Check whether a workflow is published and return saved config. Use when the user asks "
        "'is this published?', 'publish status', or 'check if published'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "graph_id": {"type": "string", "description": "Workflow ID (defaults to active workflow)"},
        },
    },
)

IMPORT_BLOCK_SCHEMA = build_tool_schema(
    name="import_block",
    description=(
        "Install a block from a path or URL. Use when the user asks "
        "'import block', 'install block from X', or 'add block from path'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Path to block dir/tarball or URL"},
            "scope": {"type": "string", "enum": ["user", "workspace"], "description": "Install scope", "default": "user"},
            "workspace": {"type": "string", "description": "Workspace path (required when scope=workspace)"},
            "force": {"type": "boolean", "description": "Overwrite existing", "default": False},
        },
        "required": ["source"],
    },
)

LIST_BLOCKS_SCHEMA = build_tool_schema(
    name="list_blocks",
    description=(
        "List all installed blocks. Use when the user asks "
        "'list blocks', 'what blocks are installed?', or 'show blocks'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "scope": {"type": "string", "description": "Filter by scope (optional, not yet used)"},
        },
    },
)


def _resolve_graph(ctx: CapabilityContext, graph_id: str | None) -> tuple[str, dict | None, "Graph | None"]:
    """Resolve graph_id to graph data and Graph model. Returns (graph_id, raw_data, graph_model)."""
    gid = (graph_id or ctx.workflow_id or "").strip()
    if not gid:
        return ("", None, None)
    if ctx.graph_store is None:
        return (gid, None, None)
    data = ctx.graph_store.get_graph(gid)
    if data is None:
        return (gid, None, None)
    try:
        from dan.models.graph import Graph
        graph = Graph.model_validate(data)
        return (gid, data, graph)
    except Exception:
        return (gid, data, None)


async def handle_publish_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    _, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        gid = (args.get("graph_id") or ctx.workflow_id or "").strip()
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    gid = (args.get("graph_id") or ctx.workflow_id or "").strip()
    name_override = args.get("name_override")
    api_key = args.get("api_key")
    rate_limit = args.get("rate_limit")
    try:
        slug = ctx.publish_registry.register(
            graph,
            name_override=name_override,
            api_key=api_key,
            rate_limit=rate_limit,
        )
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Publish failed: {exc}")
    graphs_dir = ctx.graphs_dir or "./graphs"
    pub_config = {"enabled": True, "api_key": api_key, "rate_limit": rate_limit}
    pub_path = Path(graphs_dir) / f"{gid}.publish.json"
    try:
        pub_path.write_text(json.dumps(pub_config, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to persist .publish.json: %s", exc)
    return CapabilityResult(
        success=True,
        message=f"Published workflow as **{slug}**. Config saved to {pub_path.name}.",
        data={"slug": slug, "graph_id": gid},
        output_preview=f"Published as {slug}",
    )


async def handle_unpublish_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    gid, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    from dan.publish.schema import slugify as _slugify
    from dan.utils.workflow_interface import derive_workflow_interface
    try:
        iface = derive_workflow_interface(graph)
        slug = _slugify(iface.name)
    except Exception:
        slug = gid
    ctx.publish_registry.unregister(slug)
    graphs_dir = ctx.graphs_dir or "./graphs"
    pub_path = Path(graphs_dir) / f"{gid}.publish.json"
    if pub_path.exists():
        try:
            pub_path.unlink()
        except Exception as exc:
            logger.warning("Failed to remove .publish.json: %s", exc)
    return CapabilityResult(
        success=True,
        message=f"Unpublished workflow **{gid}**.",
        data={"graph_id": gid},
        output_preview=f"Unpublished {gid}",
    )


async def handle_export_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    _, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        gid = (args.get("graph_id") or ctx.workflow_id or "").strip()
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    fmt = (args.get("format") or "block").lower()
    name = args.get("name") or ""
    version = args.get("version") or "0.1.0"
    node_id = args.get("node_id")
    gid = (args.get("graph_id") or ctx.workflow_id or "").strip()

    if fmt == "block":
        from dan.blocks import export_workflow_block, export_composite_block
        output_dir = Path(tempfile.mkdtemp(prefix="dan-export-"))
        try:
            if node_id:
                block_dir = export_composite_block(graph, node_id, output_dir, name=name, version=version)
            else:
                block_dir = export_workflow_block(graph, output_dir, name=name, version=version)
            _schedule_export_cleanup(str(output_dir))
            return CapabilityResult(
                success=True,
                message=f"Exported as block to **{block_dir}**.",
                data={"path": str(block_dir), "format": "block"},
                output_preview=f"Block at {block_dir}",
            )
        except Exception as exc:
            shutil.rmtree(str(output_dir), ignore_errors=True)
            return CapabilityResult(success=False, message=f"Block export failed: {exc}")

    if fmt == "markdown":
        from dan.loader.decompiler import decompile_to_markdown
        output_dir = Path(tempfile.mkdtemp(prefix="dan-export-md-"))
        try:
            result = decompile_to_markdown(graph, output_dir)
            paths = [str(p) for p in result.files]
            _schedule_export_cleanup(str(output_dir))
            return CapabilityResult(
                success=True,
                message=f"Exported to markdown: {', '.join(paths)}",
                data={"paths": paths, "format": "markdown"},
                output_preview=f"Markdown at {paths[0] if paths else output_dir}",
            )
        except Exception as exc:
            shutil.rmtree(str(output_dir), ignore_errors=True)
            return CapabilityResult(success=False, message=f"Markdown export failed: {exc}")

    if fmt == "python":
        from dan.builder.decompiler import decompile as decompile_to_python
        output_dir = Path(tempfile.mkdtemp(prefix="dan-export-py-"))
        try:
            code = decompile_to_python(graph)
            out_file = output_dir / "workflow.py"
            out_file.write_text(code, encoding="utf-8")
            _schedule_export_cleanup(str(output_dir))
            return CapabilityResult(
                success=True,
                message=f"Exported to Python: **{out_file}**",
                data={"path": str(out_file), "format": "python"},
                output_preview=f"Python at {out_file}",
            )
        except Exception as exc:
            shutil.rmtree(str(output_dir), ignore_errors=True)
            return CapabilityResult(success=False, message=f"Python export failed: {exc}")

    return CapabilityResult(success=False, message=f"Unknown format: {fmt}")


async def handle_share_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    gid, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    fmt = (args.get("format") or "mcp_config").lower()
    base_url = args.get("base_url") or "http://localhost:8001"
    graphs_dir = ctx.graphs_dir or "./graphs"
    workflow_path = str(Path(graphs_dir) / f"{gid}.json")

    from dan.publish.portal import generate_mcp_config, generate_api_docs, generate_openapi_spec
    from dan.utils.workflow_interface import derive_workflow_interface

    iface = derive_workflow_interface(graph)
    interfaces = [iface]

    if fmt == "mcp_config":
        config = generate_mcp_config(workflow_path, name=iface.name, interfaces=interfaces)
        return CapabilityResult(
            success=True,
            message="MCP client config (paste into .cursor/mcp.json or claude_desktop_config.json):\n```json\n" + json.dumps(config, indent=2) + "\n```",
            data=config,
            output_preview="MCP config generated",
        )
    if fmt == "api_docs":
        docs = generate_api_docs(interfaces, base_url=base_url)
        return CapabilityResult(
            success=True,
            message=f"API documentation:\n\n{docs}",
            data={"markdown": docs},
            output_preview=_truncate(docs),
        )
    if fmt == "openapi":
        spec = generate_openapi_spec(interfaces)
        return CapabilityResult(
            success=True,
            message="OpenAPI spec:\n```json\n" + json.dumps(spec, indent=2) + "\n```",
            data=spec,
            output_preview="OpenAPI spec generated",
        )
    return CapabilityResult(success=False, message=f"Unknown format: {fmt}")


async def handle_list_published(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    items = ctx.publish_registry.list_all()
    if not items:
        return CapabilityResult(
            success=True,
            message="No published workflows.",
            data=[],
            output_preview="No published workflows.",
        )
    lines = []
    for w in items:
        human = " (has HumanNode)" if w.has_human_nodes else ""
        lines.append(f"- **{w.workflow_id}** — {w.name}: {_truncate(w.description or '', 80)}{human}")
    text = f"Published workflows ({len(items)}):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=[{"workflow_id": w.workflow_id, "name": w.name, "description": w.description, "has_human_nodes": w.has_human_nodes} for w in items],
        output_preview=_truncate(text),
    )


async def handle_get_publish_status(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.publish_registry is None:
        return CapabilityResult(success=False, message="Publish registry not available.")
    gid, _, graph = _resolve_graph(ctx, args.get("graph_id"))
    if graph is None:
        return CapabilityResult(
            success=False,
            message=f"Graph not found: {gid or '(no workflow_id)'}",
        )
    from dan.publish.schema import slugify as _slugify
    from dan.utils.workflow_interface import derive_workflow_interface
    try:
        iface = derive_workflow_interface(graph)
        slug = _slugify(iface.name)
    except Exception:
        slug = gid
    published = ctx.publish_registry.is_published(slug)
    result = {"graph_id": gid, "published": published, "workflow_id": slug if published else None}
    graphs_dir = ctx.graphs_dir or "./graphs"
    pub_path = Path(graphs_dir) / f"{gid}.publish.json"
    if pub_path.exists():
        try:
            result["config"] = json.loads(pub_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    text = f"**{gid}**: {'published' if published else 'not published'}" + (f" (slug: {slug})" if published else "")
    return CapabilityResult(
        success=True,
        message=text,
        data=result,
        output_preview=text,
    )


async def handle_import_block(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.block_registry is None:
        return CapabilityResult(success=False, message="Block registry not available.")
    source = str(args.get("source", "")).strip()
    if not source:
        return CapabilityResult(success=False, message="source is required")
    scope = args.get("scope") or "user"
    workspace_str = args.get("workspace")
    workspace = Path(workspace_str) if workspace_str else None
    if scope == "workspace" and workspace is None:
        workspace = Path(os.environ.get("DAN_WORKSPACE_ROOT", os.getcwd()))
    force = bool(args.get("force", False))
    try:
        from dan.blocks import import_block as _import_block
        installed = _import_block(source, scope=scope, workspace=workspace, force=force)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Import failed: {exc}")
    ctx.block_registry.scan()
    return CapabilityResult(
        success=True,
        message=f"Imported block **{installed.name}@{installed.version}** to {installed.install_path}.",
        data={"name": installed.name, "version": installed.version, "block_type": installed.block_type, "install_path": str(installed.install_path)},
        output_preview=f"{installed.name}@{installed.version}",
    )


async def handle_list_blocks(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.block_registry is None:
        return CapabilityResult(success=False, message="Block registry not available.")
    ctx.block_registry.scan()
    blocks = ctx.block_registry.list_blocks()
    if not blocks:
        return CapabilityResult(
            success=True,
            message="No blocks installed.",
            data=[],
            output_preview="No blocks installed.",
        )
    lines = []
    for b in blocks:
        desc = (b.metadata.description or "")[:60] if b.metadata else ""
        lines.append(f"- **{b.name}@{b.version}** ({b.block_type}): {desc}")
    text = f"Installed blocks ({len(blocks)}):\n" + "\n".join(lines)
    return CapabilityResult(
        success=True,
        message=text,
        data=[{"name": b.name, "version": b.version, "block_type": b.block_type, "description": (b.metadata.description or "") if b.metadata else ""} for b in blocks],
        output_preview=_truncate(text),
    )


def register_publish_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register publish/share/export tools (25-3)."""
    registry.register(
        "publish_workflow",
        PUBLISH_WORKFLOW_SCHEMA,
        handle_publish_workflow,
        modes=WRITE_MODES,
        category="publish",
    )
    registry.register(
        "unpublish_workflow",
        UNPUBLISH_WORKFLOW_SCHEMA,
        handle_unpublish_workflow,
        modes=WRITE_MODES,
        category="publish",
    )
    registry.register(
        "export_workflow",
        EXPORT_WORKFLOW_SCHEMA,
        handle_export_workflow,
        modes=WRITE_MODES,
        category="publish",
    )
    registry.register(
        "share_workflow",
        SHARE_WORKFLOW_SCHEMA,
        handle_share_workflow,
        modes=list(ALL_MODES),
        category="publish",
    )
    registry.register(
        "list_published",
        LIST_PUBLISHED_SCHEMA,
        handle_list_published,
        modes=list(ALL_MODES),
        category="publish",
    )
    registry.register(
        "get_publish_status",
        GET_PUBLISH_STATUS_SCHEMA,
        handle_get_publish_status,
        modes=list(ALL_MODES),
        category="publish",
    )
    registry.register(
        "import_block",
        IMPORT_BLOCK_SCHEMA,
        handle_import_block,
        modes=WRITE_MODES,
        category="blocks",
    )
    registry.register(
        "list_blocks",
        LIST_BLOCKS_SCHEMA,
        handle_list_blocks,
        modes=list(ALL_MODES),
        category="blocks",
    )


# ── Run lifecycle tools (25-4) ──────────────────────────────────────

RUN_WRITE_MODES = ["agent", "build", "mutate", "debug"]


def _find_persisted_run(run_store: Any, run_id: str) -> dict[str, Any] | None:
    """Look up a single run by scanning workflow directories for its summary file."""
    base = getattr(run_store, "_base", None)
    if base is None:
        return None
    from pathlib import Path
    base = Path(base)
    if not base.exists():
        return None
    for wf_dir in base.iterdir():
        if not wf_dir.is_dir():
            continue
        summary = run_store.load_summary(wf_dir.name, run_id)
        if summary is not None:
            return summary
    return None


def get_pending_run_disambiguation(run_manager: Any) -> str | None:
    """Return a disambiguation message when multiple runs have pending HumanNode inputs."""
    pending = run_manager.get_all_pending_human_inputs()
    if len(pending) <= 1:
        return None
    lines = ["Multiple runs have pending input. Please specify a run_id:"]
    seen_runs = set()
    for p in pending:
        data = p.get("data", {})
        run_id = data.get("run_id") or p.get("run_id", "?")
        if run_id in seen_runs:
            continue
        seen_runs.add(run_id)
        node_id = data.get("node_id", p.get("node_id", "?"))
        prompt_preview = (data.get("prompt", "") or "")[:80]
        lines.append(f"  - **{run_id}** (node: {node_id}) — {prompt_preview}")
    return "\n".join(lines)


def resolve_run_reference(
    ref: str,
    run_manager: Any,
    run_store: Any | None = None,
) -> str | None:
    """Map 'latest', 'last_failed', 'paused' to concrete run_id."""
    if ref in ("latest", "last_failed", "paused"):
        runs = run_manager.list_runs()
        if ref == "latest":
            if not runs:
                if run_store:
                    summaries = run_store.list_summaries(limit=1)
                    return summaries[0]["run_id"] if summaries else None
                return None
            sorted_runs = sorted(runs, key=lambda r: r.get("started_at", 0), reverse=True)
            return sorted_runs[0]["run_id"]
        elif ref == "last_failed":
            failed = [r for r in runs if r.get("status") == "failed"]
            if failed:
                failed.sort(key=lambda r: r.get("started_at", 0), reverse=True)
                return failed[0]["run_id"]
            if run_store:
                summaries = run_store.list_summaries(status="failed", limit=1)
                return summaries[0]["run_id"] if summaries else None
            return None
        elif ref == "paused":
            pending = run_manager.get_all_pending_human_inputs()
            if len(pending) == 1:
                return (pending[0].get("data") or {}).get("run_id") or pending[0].get("run_id")
            # 0 or 2+ → return None (ambiguous or none)
            return None
    return ref


# Run lifecycle schemas
START_RUN_SCHEMA = build_tool_schema(
    name="start_run",
    description=(
        "Start a workflow run. Use when the user says 'run it', 'execute', or 'start the workflow'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "string", "description": "Workflow/graph ID to run (default: current chat workflow)"},
            "inputs": {"type": "object", "description": "Optional input values for the workflow"},
            "run_id": {"type": "string", "description": "Optional custom run ID"},
            "session_id": {"type": "string", "description": "Optional session ID"},
        },
    },
)

GET_RUN_STATUS_SCHEMA = build_tool_schema(
    name="get_run_status",
    description=(
        "Get status of a run. Supports run_id or 'latest', 'last_failed', 'paused'. "
        "Use when the user asks 'status of the run', 'how did it go?', or 'what's running?'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID or 'latest'|'last_failed'|'paused'"},
        },
        "required": ["run_id"],
    },
)

LIST_ACTIVE_RUNS_SCHEMA = build_tool_schema(
    name="list_active_runs",
    description=(
        "List active and recent runs. Use when the user asks 'what's running?', "
        "'show active runs', or 'list runs'."
    ),
    parameters={"type": "object", "properties": {}},
)

CANCEL_RUN_SCHEMA = build_tool_schema(
    name="cancel_run",
    description="Cancel a running workflow. Use when the user says 'cancel run X' or 'stop the run'.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID to cancel"},
        },
        "required": ["run_id"],
    },
)

RESUME_RUN_SCHEMA = build_tool_schema(
    name="resume_run",
    description="Resume a checkpointed run. Use when the user says 'resume run X' or 'continue the run'.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID to resume"},
            "workflow_id": {"type": "string", "description": "Workflow/graph ID"},
            "session_id": {"type": "string", "description": "Optional session ID"},
        },
        "required": ["run_id", "workflow_id"],
    },
)

GET_RUN_LOGS_SCHEMA = build_tool_schema(
    name="get_run_logs",
    description="Get event logs for a run. Use when the user asks 'show logs', 'what happened?', or 'run output'.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID or 'latest'|'last_failed'|'paused'"},
            "limit": {"type": "integer", "description": "Max events to return", "default": 50},
            "node_id": {"type": "string", "description": "Filter by node ID (optional)"},
        },
        "required": ["run_id"],
    },
)

GET_RUN_CHECKPOINTS_SCHEMA = build_tool_schema(
    name="get_run_checkpoints",
    description="Get checkpoint info for a run (completed nodes, staleness). Use for partial reruns.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID or 'latest'|'last_failed'|'paused'"},
        },
        "required": ["run_id"],
    },
)

RERUN_FROM_CHECKPOINT_SCHEMA = build_tool_schema(
    name="rerun_from_checkpoint",
    description="Partial rerun from a checkpoint. Use when the user says 'rerun from node X' or 'retry downstream'.",
    parameters={
        "type": "object",
        "properties": {
            "source_run_id": {"type": "string", "description": "Run ID with checkpoint"},
            "workflow_id": {"type": "string", "description": "Workflow/graph ID"},
            "scope_type": {
                "type": "string",
                "enum": ["downstream_of", "single_node", "subgraph"],
                "description": "Rerun scope type",
            },
            "target_node_id": {"type": "string", "description": "Target node for downstream_of or single_node"},
            "sub_graph_key": {"type": "string", "description": "Sub-graph key for subgraph scope"},
            "session_id": {"type": "string", "description": "Optional session ID"},
        },
        "required": ["source_run_id", "workflow_id", "scope_type"],
    },
)

SUBMIT_HUMAN_INPUT_SCHEMA = build_tool_schema(
    name="submit_human_input",
    description="Submit response for a pending HumanNode. Use when the user provides input for a paused run.",
    parameters={
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID"},
            "request_id": {"type": "string", "description": "Request ID from human_input_needed event"},
            "response": {"type": "object", "description": "User response (e.g. {\"approved\": true} or {\"text\": \"...\"})"},
        },
        "required": ["run_id", "request_id", "response"],
    },
)


async def handle_start_run(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    graph_id = str(args.get("workflow_id", "")).strip() or ctx.workflow_id
    if not graph_id:
        return CapabilityResult(success=False, message="workflow_id is required or set current workflow context.")
    graph_dict = ctx.graph_store.get_graph(graph_id)
    if graph_dict is None:
        return CapabilityResult(success=False, message=f"Workflow '{graph_id}' not found.")
    from dan.models.graph import Graph
    try:
        graph = Graph.model_validate(graph_dict)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Invalid graph: {exc}")
    inputs = args.get("inputs")
    run_id = args.get("run_id")
    session_id = args.get("session_id")
    try:
        record = await ctx.run_manager.start_run(
            graph, graph_id=graph_id, inputs=inputs, run_id=run_id, session_id=session_id
        )
    except Exception as exc:
        logger.exception("start_run failed")
        return CapabilityResult(success=False, message=f"Start run failed: {exc}")
    return CapabilityResult(
        success=True,
        message=f"Run started: {record.run_id}",
        data={"run_id": record.run_id, "status": record.status.value},
        output_preview=f"Run {record.run_id} started.",
        stream_channel_id=f"run-{record.run_id}",
    )


async def handle_get_run_status(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    ref = str(args.get("run_id", "")).strip()
    if not ref:
        return CapabilityResult(success=False, message="run_id is required.")
    run_id = resolve_run_reference(ref, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message=f"Could not resolve run reference '{ref}'.")
    record = ctx.run_manager.get_run(run_id)
    if record is None and ctx.run_store:
        snap = _find_persisted_run(ctx.run_store, run_id)
        if snap is None:
            return CapabilityResult(success=False, message=f"Run '{run_id}' not found.")
    elif record is None:
        return CapabilityResult(success=False, message=f"Run '{run_id}' not found.")
    else:
        snap = record.snapshot()
    parts = [
        f"**{snap.get('run_id', '?')}** ({snap.get('graph_id', '?')})",
        f"Status: {snap.get('status', '?')}",
    ]
    node_statuses = snap.get("node_statuses", {})
    if node_statuses:
        done = sum(1 for s in node_statuses.values() if s in ("completed", "failed", "skipped"))
        parts.append(f"Nodes: {done}/{len(node_statuses)}")
    if snap.get("elapsed_seconds") is not None:
        parts.append(f"Elapsed: {snap['elapsed_seconds']:.1f}s")
    if snap.get("total_tokens"):
        parts.append(f"Tokens: {snap['total_tokens']}")
    if snap.get("total_cost") is not None:
        parts.append(f"Cost: ${snap['total_cost']:.4f}")
    if snap.get("error"):
        parts.append(f"Error: {_truncate(snap['error'], 200)}")
    text = " | ".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=snap,
        output_preview=_truncate(text),
    )


async def handle_list_active_runs(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.activity_tracker is None:
        return CapabilityResult(
            success=False,
            message="Activity tracker not available.",
        )
    snapshot = ctx.activity_tracker.get_activity()
    data = snapshot.model_dump() if hasattr(snapshot, "model_dump") else (snapshot if isinstance(snapshot, dict) else {})
    active = data.get("active", [])
    recent = data.get("recent", [])
    surfaces = data.get("connected_surfaces", [])
    parts = []
    if active:
        parts.append(f"**Active runs ({len(active)}):**")
        for r in active[:10]:
            rid = r.get("run_id", "?")
            gid = r.get("graph_id", "?")
            status = r.get("status", "?")
            parts.append(f"  - {rid} ({gid}) — {status}")
    else:
        parts.append("No active runs.")
    if recent:
        parts.append(f"\n**Recent ({len(recent)}):**")
        for r in recent[:5]:
            rid = r.get("run_id", "?")
            gid = r.get("graph_id", "?")
            status = r.get("status", "?")
            parts.append(f"  - {rid} ({gid}) — {status}")
    if surfaces:
        parts.append(f"\n**Connected surfaces:** {len(surfaces)}")
    text = "\n".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=data,
        output_preview=_truncate(text),
    )


async def handle_cancel_run(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    run_id = str(args.get("run_id", "")).strip()
    if not run_id:
        return CapabilityResult(success=False, message="run_id is required.")
    ref = run_id
    run_id = resolve_run_reference(run_id, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message="Could not resolve run reference.")
    ok = ctx.run_manager.cancel_run(run_id)
    return CapabilityResult(
        success=True,
        message=f"Run {run_id} {'cancelled' if ok else 'not found or already finished'}.",
        data={"run_id": run_id, "cancelled": ok},
        output_preview=f"Cancelled {run_id}" if ok else f"Run {run_id} not found.",
    )


async def handle_resume_run(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    run_id = str(args.get("run_id", "")).strip()
    workflow_id = str(args.get("workflow_id", "")).strip()
    if not run_id or not workflow_id:
        return CapabilityResult(success=False, message="run_id and workflow_id are required.")
    graph = ctx.graph_store.load_as_model(workflow_id)
    if graph is None:
        return CapabilityResult(success=False, message=f"Workflow '{workflow_id}' not found.")
    session_id = args.get("session_id")
    try:
        record = await ctx.run_manager.resume_run(
            graph, graph_id=workflow_id, run_id=run_id, session_id=session_id
        )
    except Exception as exc:
        logger.exception("resume_run failed")
        return CapabilityResult(success=False, message=f"Resume failed: {exc}")
    return CapabilityResult(
        success=True,
        message=f"Run resumed: {record.run_id}",
        data={"run_id": record.run_id, "status": record.status.value},
        output_preview=f"Run {record.run_id} resumed.",
    )


async def handle_get_run_logs(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    ref = str(args.get("run_id", "")).strip()
    if not ref:
        return CapabilityResult(success=False, message="run_id is required.")
    run_id = resolve_run_reference(ref, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message=f"Could not resolve run reference '{ref}'.")
    limit = max(1, min(int(args.get("limit", 50)), 500))
    node_id = args.get("node_id")
    if node_id is not None:
        node_id = str(node_id).strip() or None
    record = ctx.run_manager.get_run(run_id)
    workflow_id = None
    events = []
    if record is not None:
        workflow_id = record.graph_id
        events = list(record.events) if hasattr(record, "events") else []
    if workflow_id is None and ctx.run_store:
        persisted = _find_persisted_run(ctx.run_store, run_id)
        if persisted is not None:
            workflow_id = persisted.get("graph_id")
    if workflow_id and ctx.run_store:
        loaded = ctx.run_store.load_events(workflow_id, run_id, node_id=node_id)
        if loaded:
            events = loaded
    if node_id:
        events = [e for e in events if e.get("node_id") == node_id]
    events = events[-limit:]
    lines = []
    for e in events:
        etype = e.get("event_type", "?")
        nid = e.get("node_id", "")
        ts = e.get("timestamp", 0)
        data = e.get("data") or {}
        line = f"[{ts:.0f}] {etype}"
        if nid:
            line += f" node={nid}"
        if data:
            line += f" {_truncate(str(data), 80)}"
        lines.append(line)
    text = "\n".join(lines) if lines else "No events."
    return CapabilityResult(
        success=True,
        message=text,
        data={"events": events},
        output_preview=_truncate(text),
    )


async def handle_get_run_checkpoints(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    ref = str(args.get("run_id", "")).strip()
    if not ref:
        return CapabilityResult(success=False, message="run_id is required.")
    run_id = resolve_run_reference(ref, ctx.run_manager, ctx.run_store)
    if run_id is None:
        if ref == "paused":
            disambig = get_pending_run_disambiguation(ctx.run_manager)
            if disambig:
                return CapabilityResult(success=False, message=disambig)
        return CapabilityResult(success=False, message=f"Could not resolve run reference '{ref}'.")
    try:
        info = await ctx.run_manager.get_checkpoint_info(run_id)
    except Exception as exc:
        return CapabilityResult(success=False, message=f"Checkpoint info failed: {exc}")
    if info is None:
        return CapabilityResult(
            success=True,
            message="No checkpoint found for this run.",
            data=None,
            output_preview="No checkpoint.",
        )
    parts = [
        f"Run: {info.get('run_id', '?')}",
        f"Completed nodes: {info.get('completed_node_ids', [])}",
        f"Node output keys: {info.get('node_output_keys', [])}",
    ]
    if info.get("graph_revision"):
        parts.append(f"Graph revision: {info['graph_revision']}")
    text = "\n".join(parts)
    return CapabilityResult(
        success=True,
        message=text,
        data=info,
        output_preview=_truncate(text),
    )


async def handle_rerun_from_checkpoint(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    source_run_id = str(args.get("source_run_id", "")).strip()
    workflow_id = str(args.get("workflow_id", "")).strip()
    scope_type = str(args.get("scope_type", "")).strip()
    if not source_run_id or not workflow_id or not scope_type:
        return CapabilityResult(success=False, message="source_run_id, workflow_id, and scope_type are required.")
    from dan.engine.checkpoint import RerunScope
    scope = RerunScope(
        scope_type=scope_type,
        target_node_id=args.get("target_node_id") or None,
        sub_graph_key=args.get("sub_graph_key") or None,
    )
    graph = ctx.graph_store.load_as_model(workflow_id)
    if graph is None:
        return CapabilityResult(success=False, message=f"Workflow '{workflow_id}' not found.")
    session_id = args.get("session_id")
    try:
        record = await ctx.run_manager.rerun_from_checkpoint(
            graph, graph_id=workflow_id, source_run_id=source_run_id, scope=scope, session_id=session_id
        )
    except ValueError as exc:
        return CapabilityResult(success=False, message=f"Invalid scope or checkpoint: {exc}")
    except RuntimeError as exc:
        return CapabilityResult(success=False, message=f"Stale checkpoint: {exc}")
    except Exception as exc:
        logger.exception("rerun_from_checkpoint failed")
        return CapabilityResult(success=False, message=f"Rerun failed: {exc}")
    return CapabilityResult(
        success=True,
        message=f"Rerun started: {record.run_id}",
        data={"run_id": record.run_id, "status": record.status.value, "source_run_id": source_run_id},
        output_preview=f"Rerun {record.run_id} started from checkpoint.",
    )


async def handle_submit_human_input(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.run_manager is None:
        return CapabilityResult(success=False, message="Run manager not available.")
    run_id = str(args.get("run_id", "")).strip()
    request_id = str(args.get("request_id", "")).strip()
    response = args.get("response")
    if not run_id or not request_id:
        return CapabilityResult(success=False, message="run_id and request_id are required.")
    if response is None or not isinstance(response, dict):
        return CapabilityResult(success=False, message="response must be a dict.")
    ok = ctx.run_manager.submit_human_input(run_id, request_id, response)
    return CapabilityResult(
        success=True,
        message=f"Input {'submitted' if ok else 'rejected (already resolved or wrong run)'}.",
        data={"submitted": ok},
        output_preview="Submitted." if ok else "Rejected.",
    )


def register_run_lifecycle_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register run lifecycle tools (25-4)."""
    registry.register(
        "start_run",
        START_RUN_SCHEMA,
        handle_start_run,
        modes=RUN_WRITE_MODES,
        category="run",
    )
    registry.register(
        "get_run_status",
        GET_RUN_STATUS_SCHEMA,
        handle_get_run_status,
        modes=list(ALL_MODES),
        category="run",
    )
    registry.register(
        "list_active_runs",
        LIST_ACTIVE_RUNS_SCHEMA,
        handle_list_active_runs,
        modes=list(ALL_MODES),
        category="run",
    )
    registry.register(
        "cancel_run",
        CANCEL_RUN_SCHEMA,
        handle_cancel_run,
        modes=RUN_WRITE_MODES,
        category="run",
    )
    registry.register(
        "resume_run",
        RESUME_RUN_SCHEMA,
        handle_resume_run,
        modes=RUN_WRITE_MODES,
        category="run",
    )
    registry.register(
        "get_run_logs",
        GET_RUN_LOGS_SCHEMA,
        handle_get_run_logs,
        modes=list(ALL_MODES),
        category="run",
    )
    registry.register(
        "get_run_checkpoints",
        GET_RUN_CHECKPOINTS_SCHEMA,
        handle_get_run_checkpoints,
        modes=list(ALL_MODES),
        category="run",
    )
    registry.register(
        "rerun_from_checkpoint",
        RERUN_FROM_CHECKPOINT_SCHEMA,
        handle_rerun_from_checkpoint,
        modes=RUN_WRITE_MODES,
        category="run",
    )
    registry.register(
        "submit_human_input",
        SUBMIT_HUMAN_INPUT_SCHEMA,
        handle_submit_human_input,
        modes=RUN_WRITE_MODES,
        category="run",
    )


# ── Registry setup ─────────────────────────────────────────────────

def register_base_capabilities(registry: ChatCapabilityRegistry) -> None:
    """Register the foundational read-only tools (25-1)."""
    registry.register(
        "list_graphs",
        LIST_GRAPHS_SCHEMA,
        handle_list_graphs,
        modes=list(ALL_MODES),
        category="graph",
    )
    registry.register(
        "get_activity",
        GET_ACTIVITY_SCHEMA,
        handle_get_activity,
        modes=list(ALL_MODES),
        category="run",
    )
