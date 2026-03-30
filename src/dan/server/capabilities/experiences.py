"""Experience & workflow catalog capability handlers."""
from __future__ import annotations

import logging
from typing import Any

from dan.server.capability_registry import CapabilityContext, CapabilityResult
from dan.server.capabilities._helpers import _truncate

logger = logging.getLogger(__name__)


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
        except Exception:
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


async def handle_list_my_workflows(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    graphs = ctx.graph_store.list_graphs()
    if not graphs:
        return CapabilityResult(
            success=True,
            message="No saved workflows yet. Use the build tools to create one!",
        )
    lines: list[str] = []
    for i, g in enumerate(graphs, 1):
        name = g.get("name", g.get("graph_id", "?"))
        desc = g.get("description", "")
        gid = g.get("graph_id", "?")
        data = ctx.graph_store.get_graph(gid)
        node_count = len(data.get("nodes", [])) if data else 0
        edge_count = len(data.get("edges", [])) if data else 0
        desc_part = f" — {_truncate(desc, 120)}" if desc else ""
        lines.append(f"{i}. **{name}** (id: `{gid}`, {node_count} nodes, {edge_count} edges){desc_part}")
    text = f"Found {len(graphs)} workflow(s):\n" + "\n".join(lines)
    return CapabilityResult(success=True, message=text, output_preview=_truncate(text))


async def handle_search_workflows(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    query = str(args.get("query", "")).strip()
    if not query:
        return CapabilityResult(success=False, message="query is required.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    graphs = ctx.graph_store.list_graphs()
    if not graphs:
        return CapabilityResult(success=True, message="No saved workflows to search.")
    q_lower = query.lower()
    matches: list[dict[str, Any]] = []
    for g in graphs:
        name = g.get("name", "")
        desc = g.get("description", "")
        searchable = f"{name} {desc} {g.get('graph_id', '')}".lower()
        if q_lower in searchable:
            matches.append(g)
    if not matches:
        return CapabilityResult(
            success=True,
            message=f"No workflows matched '{query}'.",
        )
    lines: list[str] = []
    for i, g in enumerate(matches, 1):
        name = g.get("name", g.get("graph_id", "?"))
        desc = g.get("description", "")
        gid = g.get("graph_id", "?")
        desc_part = f" — {_truncate(desc, 120)}" if desc else ""
        lines.append(f"{i}. **{name}** (id: `{gid}`){desc_part}")
    text = f"Found {len(matches)} workflow(s) matching '{query}':\n" + "\n".join(lines)
    return CapabilityResult(success=True, message=text, output_preview=_truncate(text))


async def handle_show_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    workflow_id = str(args.get("workflow_id", "")).strip()
    if not workflow_id:
        return CapabilityResult(success=False, message="workflow_id is required.")
    if ctx.graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")
    data = ctx.graph_store.get_graph(workflow_id)
    if data is None:
        return CapabilityResult(
            success=False,
            message=f"Workflow '{workflow_id}' not found.",
        )
    meta = data.get("metadata", {})
    name = meta.get("name", workflow_id)
    desc = meta.get("description", "")
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    parts = [f"**{name}** (id: `{workflow_id}`)"]
    if desc:
        parts.append(f"Description: {desc}")
    parts.append(f"Nodes: {len(nodes)} | Edges: {len(edges)}")

    try:
        from dan.cli.dag_display import render_dag
        dag = render_dag(data)
        parts.append(f"\nStructure:\n{dag}")
    except Exception:
        if nodes:
            node_names = [n.get("name") or n.get("id", "?") for n in nodes[:20]]
            parts.append("Nodes: " + ", ".join(node_names))

    text = "\n".join(parts)
    return CapabilityResult(success=True, message=text, data=data, output_preview=_truncate(text))


async def handle_fork_workflow(
    args: dict[str, Any],
    ctx: CapabilityContext,
) -> CapabilityResult:
    graph_store = ctx.graph_store
    if graph_store is None:
        return CapabilityResult(success=False, message="Graph store not available.")

    workflow_id = args.get("workflow_id", "").strip()
    if not workflow_id:
        return CapabilityResult(success=False, message="workflow_id is required.")

    graph_dict = graph_store.get_graph(workflow_id)
    if graph_dict is None:
        return CapabilityResult(success=False, message=f"Workflow '{workflow_id}' not found.")

    new_name = args.get("new_name", "").strip() or f"{workflow_id}_fork"

    import uuid as _uuid

    new_id = _uuid.uuid4().hex[:12]
    forked = dict(graph_dict)
    if "metadata" in forked:
        forked["metadata"] = dict(forked["metadata"])
        forked["metadata"]["name"] = new_name
        forked["metadata"]["forked_from"] = workflow_id
    else:
        forked["metadata"] = {"name": new_name, "forked_from": workflow_id}

    graph_store.save_graph(new_id, forked)

    msg = f"Forked '{workflow_id}' as '{new_name}' (ID: {new_id})."
    return CapabilityResult(
        success=True,
        message=msg,
        data={"workflow_id": new_id, "name": new_name, "forked_from": workflow_id},
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
    data = snapshot.model_dump() if hasattr(snapshot, "model_dump") else (
        snapshot if isinstance(snapshot, dict) else {}
    )
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
