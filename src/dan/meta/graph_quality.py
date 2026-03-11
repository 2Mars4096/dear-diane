"""Semantic quality scoring for generated workflow graphs.

Runs after structural validation to catch graphs that are valid but
semantically wrong — underspecified, missing expected patterns, or
lacking required features. Produces a 0-100 score and concerns list.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class QualityCheck(BaseModel):
    """Result of a single quality dimension check."""

    score: int = Field(ge=0, le=100, description="0-100 score for this dimension")
    explanation: str = ""
    concerns: list[str] = Field(default_factory=list)


class GraphQualityReport(BaseModel):
    """Aggregate quality report with overall score and per-check breakdown."""

    overall_score: int = Field(ge=0, le=100)
    concerns: list[str] = Field(default_factory=list)
    node_count: QualityCheck | None = None
    pattern_presence: QualityCheck | None = None
    tool_coverage: QualityCheck | None = None
    topology: QualityCheck | None = None


# ---------------------------------------------------------------------------
# Tier minimums (eval harness)
# ---------------------------------------------------------------------------

_TIER_MIN_NODES: dict[str, int] = {
    "T1": 2,
    "T2": 3,
    "T3": 4,
    "T4": 6,
    "T5": 8,
    "T2R": 3,
    "pilot": 2,
}


# ---------------------------------------------------------------------------
# Pattern keywords → expected graph features
# ---------------------------------------------------------------------------

# "review loop" → gate/while_loop + loop edges (control edges forming a cycle)
_PATTERN_KEYWORDS: dict[str, list[tuple[str, list[str]]]] = {
    "review loop": [
        ("gate_or_loop", ["gate", "while_loop", "goal_loop"]),
        ("loop_edge", ["control"]),  # control edges for loop-back
    ],
    "in parallel": [
        ("fan_out", ["for_each", "parallel_subagents"]),
    ],
    "parallel": [
        ("fan_out", ["for_each", "parallel_subagents"]),
    ],
    "rag": [
        ("rag_node", ["rag_operator"]),
    ],
    "code": [
        ("code_node", ["code_operator"]),
    ],
    "code execution": [
        ("code_node", ["code_operator"]),
    ],
}


# ---------------------------------------------------------------------------
# Tool keywords → expected node types
# ---------------------------------------------------------------------------

_TOOL_KEYWORDS: dict[str, list[str]] = {
    "web search": ["tool_operator", "llm_operator"],  # tool-augmented LLM or tool node
    "web_search": ["tool_operator", "llm_operator"],
    "pdf": ["tool_operator", "rag_operator"],
    "file": ["tool_operator", "rag_operator", "code_operator"],
    "code execution": ["code_operator"],
}


# ---------------------------------------------------------------------------
# Check primitives
# ---------------------------------------------------------------------------


def _get_nodes_edges(graph_dict: dict) -> tuple[list[dict], list[dict]]:
    """Extract nodes and edges from graph dict, handling nested structure.

    Handles graph_dict from Graph.model_dump() — edges is a list.
    """
    nodes = graph_dict.get("nodes", [])
    edges = graph_dict.get("edges", [])
    if not isinstance(nodes, list):
        nodes = []
    if not isinstance(edges, list):
        edges = []
    return nodes, edges


def _edge_source_target(edge: dict) -> tuple[str, str]:
    """Return (source_node_id, target_node_id) from edge dict."""
    src = edge.get("source_node_id") or edge.get("source", "")
    tgt = edge.get("target_node_id") or edge.get("target", "")
    if isinstance(src, dict):
        src = src.get("id", src.get("node_id", ""))
    if isinstance(tgt, dict):
        tgt = tgt.get("id", tgt.get("node_id", ""))
    return str(src), str(tgt)


def check_node_count(
    graph_dict: dict,
    prompt_text: str,
    tier: str | None = None,
) -> QualityCheck:
    """Check if node count is adequate for the prompt.

    When tier is provided: T1≥2, T2≥3, T3≥4, T4≥6.
    When tier is None: infer from prompt (N steps→≥N nodes, simple/quick tolerate fewer).
    """
    nodes, _ = _get_nodes_edges(graph_dict)
    count = len(nodes)
    prompt_lower = (prompt_text or "").lower()

    if tier:
        tier_upper = tier.upper().strip()
        min_nodes = _TIER_MIN_NODES.get(tier_upper, 2)
    else:
        # Infer from prompt
        steps_match = re.search(r"(\d+)\s*step", prompt_lower)
        if steps_match:
            min_nodes = max(2, int(steps_match.group(1)))
        elif any(kw in prompt_lower for kw in ("simple", "quick", "basic")):
            min_nodes = 2
        else:
            # Heuristic: word count suggests complexity
            words = len(prompt_lower.split())
            if words < 15:
                min_nodes = 2
            elif words < 30:
                min_nodes = 3
            elif words < 50:
                min_nodes = 4
            else:
                min_nodes = 5

    if count >= min_nodes:
        return QualityCheck(
            score=100,
            explanation=f"Node count {count} meets minimum {min_nodes}",
            concerns=[],
        )
    if count == 0:
        return QualityCheck(
            score=0,
            explanation="Graph has no nodes",
            concerns=["Graph has no nodes"],
        )
    # Linear scale: 0 at 0 nodes, 100 at min_nodes
    score = max(0, min(100, int(100 * count / min_nodes)))
    return QualityCheck(
        score=score,
        explanation=f"Node count {count} below minimum {min_nodes} for prompt complexity",
        concerns=[f"Only {count} node(s), expected ≥{min_nodes}"],
    )


def check_pattern_presence(graph_dict: dict, prompt_text: str) -> QualityCheck:
    """Check that prompt-implied patterns are present in the graph.

    Keywords: "review loop"→gate/loop, "in parallel"→ForEach/fan-out,
    "RAG"→RAG node, "code"→Code node.
    """
    nodes, edges = _get_nodes_edges(graph_dict)
    prompt_lower = (prompt_text or "").lower()
    node_types = {n.get("node_type", "") for n in nodes if isinstance(n, dict)}
    edge_types = {e.get("edge_type", e.get("type", "")) for e in edges if isinstance(e, dict)}

    present: list[str] = []
    missing: list[str] = []
    concerns: list[str] = []

    for keyword, expectations in _PATTERN_KEYWORDS.items():
        if keyword not in prompt_lower:
            continue
        for pattern_name, expected_node_or_edge_types in expectations:
            if pattern_name == "loop_edge":
                has_control = "control" in edge_types
                if has_control:
                    present.append(f"{keyword}→control edges")
                else:
                    missing.append(f"{keyword}→control/loop edges")
                    concerns.append(f"Prompt mentions '{keyword}' but no control edges for loop")
            else:
                found = any(t in node_types for t in expected_node_or_edge_types)
                if found:
                    present.append(f"{keyword}→{pattern_name}")
                else:
                    missing.append(f"{keyword}→{expected_node_or_edge_types[0]}")
                    concerns.append(
                        f"Prompt mentions '{keyword}' but graph lacks "
                        f"{', '.join(expected_node_or_edge_types)}"
                    )

    if not present and not missing:
        return QualityCheck(
            score=100,
            explanation="No pattern keywords detected in prompt",
            concerns=[],
        )
    if missing and not present:
        score = max(0, 50 - len(missing) * 25)
        return QualityCheck(
            score=score,
            explanation=f"Missing expected patterns: {', '.join(missing)}",
            concerns=concerns,
        )
    if missing:
        ratio = len(present) / (len(present) + len(missing))
        score = int(50 + 50 * ratio)
        return QualityCheck(
            score=score,
            explanation=f"Present: {present}; Missing: {missing}",
            concerns=concerns,
        )
    return QualityCheck(
        score=100,
        explanation=f"All expected patterns present: {present}",
        concerns=[],
    )


def check_tool_coverage(graph_dict: dict, prompt_text: str) -> QualityCheck:
    """Check that tool-heavy prompts produce tool/code nodes."""
    nodes, _ = _get_nodes_edges(graph_dict)
    prompt_lower = (prompt_text or "").lower()
    node_types = {n.get("node_type", "") for n in nodes if isinstance(n, dict)}

    tool_node_types = {"tool_operator", "code_operator", "rag_operator"}
    has_tool = any(t in node_types for t in tool_node_types)

    mentioned: list[str] = []
    for kw in _TOOL_KEYWORDS:
        if kw in prompt_lower:
            mentioned.append(kw)

    if not mentioned:
        return QualityCheck(
            score=100,
            explanation="No tool keywords in prompt",
            concerns=[],
        )
    if has_tool:
        return QualityCheck(
            score=100,
            explanation=f"Tool/code nodes present for: {', '.join(mentioned)}",
            concerns=[],
        )
    return QualityCheck(
        score=30,
        explanation=f"Prompt mentions {mentioned} but graph has no tool/code/rag nodes",
        concerns=[f"Expected tool/code nodes for: {', '.join(mentioned)}"],
    )


def check_topology(graph_dict: dict) -> QualityCheck:
    """Check connectivity, penalize isolated nodes, single-node for multi-step, missing entry/terminal."""
    nodes, edges = _get_nodes_edges(graph_dict)
    node_ids = {n.get("id", "") for n in nodes if isinstance(n, dict) and n.get("id")}
    count = len(node_ids)

    concerns: list[str] = []
    score = 100

    if count == 0:
        return QualityCheck(score=0, explanation="Empty graph", concerns=["No nodes"])

    if count == 1:
        return QualityCheck(
            score=40,
            explanation="Single-node graph — likely underspecified",
            concerns=["Single node with no edges; multi-step workflows need multiple nodes"],
        )

    # Build undirected adjacency for connectivity (both directions)
    adj: dict[str, set[str]] = {nid: set() for nid in node_ids}
    for e in edges:
        if not isinstance(e, dict):
            continue
        src, tgt = _edge_source_target(e)
        if src in node_ids and tgt in node_ids:
            adj[src].add(tgt)
            adj[tgt].add(src)

    # Find reachable from any node (weak connectivity)
    if node_ids:
        start = next(iter(node_ids))
        reachable: set[str] = set()
        stack = [start]
        while stack:
            n = stack.pop()
            if n in reachable:
                continue
            reachable.add(n)
            for out in adj.get(n, set()):
                stack.append(out)
        isolated = node_ids - reachable
        if isolated and len(reachable) > 1:
            concerns.append(f"Isolated nodes: {', '.join(sorted(isolated)[:5])}")
            score -= 20
        elif len(reachable) < count:
            # Disconnected components
            concerns.append("Graph has disconnected components")
            score -= 15

    # Entry/exit: graph may use entry_points/exit_points or implicit input/terminal
    entry_points = graph_dict.get("entry_points", [])
    exit_points = graph_dict.get("exit_points", [])
    has_input = any(n.get("node_type") == "input" for n in nodes if isinstance(n, dict))
    if not entry_points and not has_input and count > 1:
        # Not necessarily bad — builder may use implicit entry
        pass

    if concerns:
        score = max(0, score)
    return QualityCheck(
        score=score,
        explanation="Topology check" + ("; " + "; ".join(concerns) if concerns else " — OK"),
        concerns=concerns,
    )


def compute_quality_report(
    graph_dict: dict,
    prompt_text: str,
    tier: str | None = None,
) -> GraphQualityReport:
    """Run all quality checks and aggregate into a report.

    All inputs are raw dicts. Uses Graph.model_validate() only when needed
    (topology may need it for full validation; we work with raw dict for speed).
    """
    node_check = check_node_count(graph_dict, prompt_text, tier)
    pattern_check = check_pattern_presence(graph_dict, prompt_text)
    tool_check = check_tool_coverage(graph_dict, prompt_text)
    topo_check = check_topology(graph_dict)

    all_concerns: list[str] = []
    all_concerns.extend(node_check.concerns)
    all_concerns.extend(pattern_check.concerns)
    all_concerns.extend(tool_check.concerns)
    all_concerns.extend(topo_check.concerns)

    scores = [node_check.score, pattern_check.score, tool_check.score, topo_check.score]
    overall = int(sum(scores) / len(scores)) if scores else 0

    return GraphQualityReport(
        overall_score=overall,
        concerns=all_concerns,
        node_count=node_check,
        pattern_presence=pattern_check,
        tool_coverage=tool_check,
        topology=topo_check,
    )
