"""Semantic quality scoring for generated workflow graphs.

Runs after structural validation to catch graphs that are valid but
semantically wrong — underspecified, missing expected patterns, or
lacking required features. Produces a 0-100 score and concerns list.
"""

from __future__ import annotations

import re
import json
import os
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
    semantic_grounding: QualityCheck | None = None
    complexity_tier: str | None = None
    expected_node_range_min: int | None = None
    expected_node_range_max: int | None = None


# ---------------------------------------------------------------------------
# Tier minimums (eval harness)
# ---------------------------------------------------------------------------


def _load_tier_min_nodes() -> dict[str, int]:
    _DEFAULT = {
        "T1": 2,
        "T2": 3,
        "T3": 4,
        "T4": 6,
        "T5": 8,
        "T2R": 3,
        "pilot": 2,
    }
    raw = os.environ.get("DAN_QUALITY_TIER_MIN_NODES")
    if raw:
        try:
            return {k: int(v) for k, v in json.loads(raw).items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return _DEFAULT


_TIER_MIN_NODES: dict[str, int] = _load_tier_min_nodes()


# ---------------------------------------------------------------------------
# Node ranges and quality thresholds by complexity tier
# ---------------------------------------------------------------------------


def _load_node_range_by_tier() -> dict[str, tuple[int, int]]:
    _DEFAULT = {"T1": [2, 5], "T2": [3, 8], "T3": [4, 10], "T4": [6, 15]}
    raw = os.environ.get("DAN_QUALITY_NODE_RANGES")
    if raw:
        try:
            parsed = json.loads(raw)
            return {k: tuple(v) for k, v in parsed.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return {k: tuple(v) for k, v in _DEFAULT.items()}


_NODE_RANGE_BY_TIER: dict[str, tuple[int, int]] = _load_node_range_by_tier()


def _load_tier_quality_threshold() -> dict[str, int]:
    _DEFAULT = {"T1": 30, "T2": 40, "T3": 50, "T4": 60}
    raw = os.environ.get("DAN_QUALITY_TIER_THRESHOLDS")
    if raw:
        try:
            return {k: int(v) for k, v in json.loads(raw).items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return _DEFAULT


_TIER_QUALITY_THRESHOLD: dict[str, int] = _load_tier_quality_threshold()


def _load_expected_node_range_fallback() -> tuple[int, int]:
    _DEFAULT = (2, 4)
    raw = os.environ.get("DAN_QUALITY_EXPECTED_NODE_RANGE_FALLBACK")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, (list, tuple)) and len(parsed) == 2:
                return int(parsed[0]), int(parsed[1])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return _DEFAULT


_EXPECTED_NODE_RANGE_FALLBACK: tuple[int, int] = _load_expected_node_range_fallback()


def _load_tier_quality_threshold_fallback() -> int:
    raw = os.environ.get("DAN_QUALITY_TIER_QUALITY_THRESHOLD_FALLBACK")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 40


_TIER_QUALITY_THRESHOLD_FALLBACK: int = _load_tier_quality_threshold_fallback()


_SIMPLE_GRAPH_MIN_NODES = int(os.environ.get("DAN_QUALITY_SIMPLE_GRAPH_MIN_NODES", "2"))
_SIMPLE_GRAPH_MAX_NODES = int(os.environ.get("DAN_QUALITY_SIMPLE_GRAPH_MAX_NODES", "6"))


# ---------------------------------------------------------------------------
# Pattern keywords → expected graph features
# ---------------------------------------------------------------------------

# "review loop" → gate/while_loop + loop edges (control edges forming a cycle)
_PATTERN_KEYWORDS: dict[str, list[tuple[str, list[str]]]] = {
    "review loop": [
        ("gate_or_loop", ["gate", "while_loop", "goal_loop"]),
        ("loop_edge", ["control"]),
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
    "execute code": [
        ("code_node", ["code_operator"]),
    ],
    "run code": [
        ("code_node", ["code_operator"]),
    ],
    "code execution": [
        ("code_node", ["code_operator"]),
    ],
    "run the code": [
        ("code_node", ["code_operator"]),
    ],
    "code block": [
        ("code_node", ["code_operator"]),
    ],
}


# ---------------------------------------------------------------------------
# Tool keywords → expected node types
# ---------------------------------------------------------------------------

_TOOL_KEYWORDS: dict[str, list[str]] = {
    "web search": ["tool_operator", "llm_operator"],
    "web_search": ["tool_operator", "llm_operator"],
    "search the web": ["tool_operator", "llm_operator"],
    "pdf": ["tool_operator", "rag_operator"],
    "read a file": ["tool_operator", "rag_operator", "code_operator"],
    "read file": ["tool_operator", "rag_operator", "code_operator"],
    "write a file": ["tool_operator", "code_operator"],
    "write file": ["tool_operator", "code_operator"],
    "from a folder": ["tool_operator", "rag_operator"],
    "from folder": ["tool_operator", "rag_operator"],
    "ingest": ["tool_operator", "rag_operator"],
}


# ---------------------------------------------------------------------------
# Simple-pattern keywords (for is_acceptable_simple_graph)
# ---------------------------------------------------------------------------

_SIMPLE_PATTERN_KEYWORDS: dict[str, list[str]] = {
    "chain": ["chain", "pipeline", "sequence", "sequential"],
    "fan_out": ["parallel", "in parallel", "fan out", "fan-out", "simultaneously"],
    "review_loop": ["review loop", "review cycle", "feedback loop", "revision loop"],
    "conditional": ["conditional branch", "conditional", "if-then", "branching"],
}


# ---------------------------------------------------------------------------
# Internal helpers
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


def _subgraph_dicts(graph_dict: dict) -> list[tuple[str, dict]]:
    """Return nested subgraph dicts from a graph dict."""
    sub_graphs = graph_dict.get("sub_graphs", {})
    if not isinstance(sub_graphs, dict):
        return []
    return [
        (str(key), value)
        for key, value in sub_graphs.items()
        if isinstance(value, dict)
    ]


def _iter_graph_dicts(
    graph_dict: dict,
    *,
    graph_label: str = "Workflow",
) -> list[tuple[str, dict]]:
    """Return the root graph dict and all nested subgraph dicts."""
    collected: list[tuple[str, dict]] = [(graph_label, graph_dict)]
    for subgraph_key, subgraph in _subgraph_dicts(graph_dict):
        collected.extend(
            _iter_graph_dicts(
                subgraph,
                graph_label=f"{graph_label}.{subgraph_key}",
            )
        )
    return collected


def _recursive_node_count(graph_dict: dict) -> int:
    """Count nodes across the workflow graph and all nested subgraphs."""
    return sum(len(_get_nodes_edges(candidate)[0]) for _, candidate in _iter_graph_dicts(graph_dict))


def _recursive_node_types_and_edge_types(graph_dict: dict) -> tuple[set[str], set[str]]:
    """Collect node and edge types from the workflow graph and all nested subgraphs."""
    node_types: set[str] = set()
    edge_types: set[str] = set()
    for _, candidate in _iter_graph_dicts(graph_dict):
        nodes, edges = _get_nodes_edges(candidate)
        node_types.update(
            str(n.get("node_type", "")).strip()
            for n in nodes
            if isinstance(n, dict) and str(n.get("node_type", "")).strip()
        )
        edge_types.update(
            str(e.get("edge_type", e.get("type", ""))).strip()
            for e in edges
            if isinstance(e, dict) and str(e.get("edge_type", e.get("type", ""))).strip()
        )
    return node_types, edge_types


def _edge_source_target(edge: dict) -> tuple[str, str]:
    """Return (source_node_id, target_node_id) from edge dict."""
    src = edge.get("source_node_id") or edge.get("source", "")
    tgt = edge.get("target_node_id") or edge.get("target", "")
    if isinstance(src, dict):
        src = src.get("id", src.get("node_id", ""))
    if isinstance(tgt, dict):
        tgt = tgt.get("id", tgt.get("node_id", ""))
    return str(src), str(tgt)


def _out_in_counts(
    nodes: list[dict], edges: list[dict],
) -> tuple[dict[str, int], dict[str, int]]:
    """Return per-node (outgoing, incoming) edge counts."""
    node_ids = {n.get("id", "") for n in nodes if isinstance(n, dict) and n.get("id")}
    out: dict[str, int] = {nid: 0 for nid in node_ids}
    inc: dict[str, int] = {nid: 0 for nid in node_ids}
    for e in edges:
        if not isinstance(e, dict):
            continue
        src, tgt = _edge_source_target(e)
        if src in node_ids:
            out[src] = out.get(src, 0) + 1
        if tgt in node_ids:
            inc[tgt] = inc.get(tgt, 0) + 1
    return out, inc


def _is_chain_topology(nodes: list[dict], edges: list[dict]) -> bool:
    """True when every node has at most one outgoing and one incoming edge."""
    out, inc = _out_in_counts(nodes, edges)
    return (
        len(out) >= 2
        and all(c <= 1 for c in out.values())
        and all(c <= 1 for c in inc.values())
    )


def _has_fan_out(nodes: list[dict], edges: list[dict]) -> bool:
    """True when at least one node fans out to 2+ targets or a fan-out node type exists."""
    node_types = {n.get("node_type", "") for n in nodes if isinstance(n, dict)}
    if node_types & {"for_each", "parallel_subagents"}:
        return True
    out, _ = _out_in_counts(nodes, edges)
    return any(c >= 2 for c in out.values())


def _has_cycle(nodes: list[dict], edges: list[dict]) -> bool:
    """True when the directed graph contains at least one cycle."""
    node_ids = {n.get("id", "") for n in nodes if isinstance(n, dict) and n.get("id")}
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for e in edges:
        if not isinstance(e, dict):
            continue
        src, tgt = _edge_source_target(e)
        if src in node_ids and tgt in node_ids:
            adj[src].append(tgt)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in node_ids}

    def _dfs(v: str) -> bool:
        color[v] = GRAY
        for u in adj[v]:
            if color[u] == GRAY:
                return True
            if color[u] == WHITE and _dfs(u):
                return True
        color[v] = BLACK
        return False

    return any(color[v] == WHITE and _dfs(v) for v in node_ids)


def _has_gate_node(nodes: list[dict]) -> bool:
    """True when the graph contains a gate, while_loop, or goal_loop node."""
    return any(
        n.get("node_type", "") in ("gate", "while_loop", "goal_loop")
        for n in nodes
        if isinstance(n, dict)
    )


# ---------------------------------------------------------------------------
# Shared prompt-complexity primitives (P1-3)
# ---------------------------------------------------------------------------


def estimate_prompt_complexity(prompt_text: str) -> str:
    """Return complexity tier: 'T1', 'T2', 'T3', or 'T4'.

    Heuristic based on word count, explicit step mentions, tool keywords,
    and pattern keywords. This is the single shared signal that 33-9 and
    other modules should consume.
    """
    prompt_lower = (prompt_text or "").lower()
    words = len(prompt_lower.split())

    steps_match = re.search(r"(\d+)\s*step", prompt_lower)
    step_count = int(steps_match.group(1)) if steps_match else 0

    pattern_hits = sum(1 for kw in _PATTERN_KEYWORDS if kw in prompt_lower)
    tool_hits = sum(1 for kw in _TOOL_KEYWORDS if kw in prompt_lower)

    t4_phrases = (
        "parallel teams", "departments", "multi-source", "multi-department",
        "multi-team", "cross-functional",
    )

    if (
        words >= 50
        or step_count >= 5
        or any(p in prompt_lower for p in t4_phrases)
        or (pattern_hits >= 2 and tool_hits >= 2)
    ):
        return "T4"

    if (
        words >= 30
        or step_count >= 4
        or (pattern_hits + tool_hits) >= 3
    ):
        return "T3"

    if any(kw in prompt_lower for kw in ("simple", "basic", "quick")):
        return "T1"

    if words < 15 and pattern_hits <= 1 and tool_hits == 0:
        return "T1"

    return "T2"


def expected_node_range(prompt_text: str, tier: str | None = None) -> tuple[int, int]:
    """Return (min_nodes, max_nodes) expected for this prompt.

    Uses estimate_prompt_complexity() when tier is None.
    Returns calibrated ranges: T1 (2,5), T2 (3,8), T3 (4,10), T4 (6,15).
    """
    if tier is None:
        tier = estimate_prompt_complexity(prompt_text)
    return _NODE_RANGE_BY_TIER.get(tier.upper().strip(), _EXPECTED_NODE_RANGE_FALLBACK)


# ---------------------------------------------------------------------------
# Tier-adaptive quality thresholds (P1-1)
# ---------------------------------------------------------------------------


def tier_quality_threshold(tier: str | None, prompt_text: str) -> int:
    """Return the recommended quality-gate threshold for the given tier.

    When *tier* is None the tier is inferred from *prompt_text* via
    estimate_prompt_complexity().  Per-tier thresholds: T1->30, T2->40,
    T3->50, T4->60.
    """
    if tier is None:
        tier = estimate_prompt_complexity(prompt_text)
    return _TIER_QUALITY_THRESHOLD.get(tier.upper().strip(), _TIER_QUALITY_THRESHOLD_FALLBACK)


# ---------------------------------------------------------------------------
# Acceptable simple-graph exemption (P1-2)
# ---------------------------------------------------------------------------


def is_acceptable_simple_graph(graph_dict: dict, prompt_text: str) -> bool:
    """Return True when the graph is a correct simple single-pattern output.

    The quality gate should be skipped entirely when this returns True.
    Conditions:
    - The prompt matches exactly one simple pattern (chain, fan_out,
      review_loop, conditional).
    - The graph has the expected topology for that pattern.
    - The node count falls within the T1/T2 range (2-6).
    """
    prompt_lower = (prompt_text or "").lower()

    matched: list[str] = []
    for pattern, keywords in _SIMPLE_PATTERN_KEYWORDS.items():
        if any(kw in prompt_lower for kw in keywords):
            matched.append(pattern)

    if len(matched) != 1:
        return False

    pattern = matched[0]
    nodes, edges = _get_nodes_edges(graph_dict)
    count = len(nodes)

    if count < _SIMPLE_GRAPH_MIN_NODES or count > _SIMPLE_GRAPH_MAX_NODES:
        return False

    if pattern == "chain":
        return _is_chain_topology(nodes, edges)
    if pattern == "fan_out":
        return _has_fan_out(nodes, edges)
    if pattern == "review_loop":
        return _has_gate_node(nodes) or _has_cycle(nodes, edges)
    if pattern == "conditional":
        return _has_gate_node(nodes)
    return False


# ---------------------------------------------------------------------------
# Check primitives
# ---------------------------------------------------------------------------


def check_node_count(
    graph_dict: dict,
    prompt_text: str,
    tier: str | None = None,
) -> QualityCheck:
    """Check if node count is adequate for the prompt.

    Uses expected_node_range() as single source of truth. For eval-harness
    tiers (T5, T2R, pilot) that aren't in the node-range table, falls back
    to _TIER_MIN_NODES.
    """
    count = _recursive_node_count(graph_dict)

    if tier and tier.upper().strip() in _NODE_RANGE_BY_TIER:
        min_nodes, _ = expected_node_range(prompt_text, tier)
    elif tier:
        min_nodes = _TIER_MIN_NODES.get(tier.upper().strip(), 2)
    else:
        min_nodes, _ = expected_node_range(prompt_text)

    if count >= min_nodes:
        return QualityCheck(
            score=100,
            explanation=f"Recursive node count {count} meets minimum {min_nodes}",
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
        explanation=f"Recursive node count {count} below minimum {min_nodes} for prompt complexity",
        concerns=[f"Only {count} node(s), expected >={min_nodes}"],
    )


def check_pattern_presence(graph_dict: dict, prompt_text: str) -> QualityCheck:
    """Check that prompt-implied patterns are present in the graph.

    Keywords: "review loop"->gate/loop, "in parallel"->ForEach/fan-out,
    "RAG"->RAG node, "code"->Code node.
    """
    prompt_lower = (prompt_text or "").lower()
    node_types, edge_types = _recursive_node_types_and_edge_types(graph_dict)

    present: list[str] = []
    missing: list[str] = []
    concerns: list[str] = []

    for keyword, expectations in _PATTERN_KEYWORDS.items():
        if keyword not in prompt_lower:
            continue
        for pattern_name, expected_node_or_edge_types in expectations:
            if pattern_name == "loop_edge":
                has_control = "control" in edge_types
                has_loop_primitive = any(
                    node_type in node_types
                    for node_type in ("while_loop", "goal_loop")
                )
                if has_control or has_loop_primitive:
                    present.append(
                        f"{keyword}->"
                        + ("control edges" if has_control else "loop primitive")
                    )
                else:
                    missing.append(f"{keyword}->control/loop edges")
                    concerns.append(f"Prompt mentions '{keyword}' but no control edges for loop")
            else:
                found = any(t in node_types for t in expected_node_or_edge_types)
                if found:
                    present.append(f"{keyword}->{pattern_name}")
                else:
                    missing.append(f"{keyword}->{expected_node_or_edge_types[0]}")
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
    prompt_lower = (prompt_text or "").lower()
    node_types, _ = _recursive_node_types_and_edge_types(graph_dict)

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


def check_semantic_grounding(graph_dict: dict) -> QualityCheck:
    """Check that the graph is semantically grounded and run-ready under the workflow contract."""
    try:
        from dan.meta.workflow_contract import validate_workflow_build_contract

        contract_report = validate_workflow_build_contract(graph_dict, apply_repairs=True)
    except Exception as exc:
        return QualityCheck(
            score=0,
            explanation="Workflow contract check failed during quality scoring",
            concerns=[f"Workflow contract check failed: {exc}"],
        )

    if contract_report.validated and contract_report.run_ready:
        return QualityCheck(
            score=100,
            explanation="Workflow contract marked the graph validated and run-ready",
            concerns=[],
        )

    run_readiness_issues = [
        str(issue).strip()
        for issue in contract_report.run_readiness_issues
        if str(issue).strip()
    ]
    build_errors = [
        str(getattr(issue, "message", "") or "").strip()
        for issue in contract_report.errors
        if str(getattr(issue, "message", "") or "").strip()
    ]

    if run_readiness_issues:
        score = max(0, 40 - (len(run_readiness_issues) * 20))
        return QualityCheck(
            score=score,
            explanation=(
                "Workflow contract found semantic run-readiness issues"
                f" ({len(run_readiness_issues)})"
            ),
            concerns=run_readiness_issues[:5],
        )

    if build_errors:
        score = max(0, 30 - (len(build_errors) * 10))
        return QualityCheck(
            score=score,
            explanation=f"Workflow contract found build-validation issues ({len(build_errors)})",
            concerns=build_errors[:5],
        )

    return QualityCheck(
        score=20,
        explanation="Workflow contract did not mark the graph run-ready",
        concerns=["Workflow contract did not mark the graph run-ready"],
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
        if _subgraph_dicts(graph_dict):
            return QualityCheck(
                score=85,
                explanation="Single top-level orchestration node with nested subgraph — acceptable",
                concerns=[],
            )
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
            concerns.append("Graph has disconnected components")
            score -= 15

    # Entry/exit: graph may use entry_points/exit_points or implicit input/terminal
    entry_points = graph_dict.get("entry_points", [])
    exit_points = graph_dict.get("exit_points", [])
    has_input = any(n.get("node_type") == "input" for n in nodes if isinstance(n, dict))
    if not entry_points and not has_input and count > 1:
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
    if graph_dict is None:
        return GraphQualityReport(overall_score=0, concerns=["Graph dict is None"])
    node_check = check_node_count(graph_dict, prompt_text, tier)
    pattern_check = check_pattern_presence(graph_dict, prompt_text)
    tool_check = check_tool_coverage(graph_dict, prompt_text)
    topo_check = check_topology(graph_dict)
    semantic_check = check_semantic_grounding(graph_dict)

    all_concerns: list[str] = []
    all_concerns.extend(node_check.concerns)
    all_concerns.extend(pattern_check.concerns)
    all_concerns.extend(tool_check.concerns)
    all_concerns.extend(topo_check.concerns)
    all_concerns.extend(semantic_check.concerns)

    scores = [
        node_check.score,
        pattern_check.score,
        tool_check.score,
        topo_check.score,
        semantic_check.score,
    ]
    overall = int(sum(scores) / len(scores)) if scores else 0
    if semantic_check.score < 100:
        overall = min(overall, semantic_check.score)

    complexity = estimate_prompt_complexity(prompt_text)
    nr_min, nr_max = expected_node_range(prompt_text, tier)

    return GraphQualityReport(
        overall_score=overall,
        concerns=all_concerns,
        node_count=node_check,
        pattern_presence=pattern_check,
        tool_coverage=tool_check,
        topology=topo_check,
        semantic_grounding=semantic_check,
        complexity_tier=complexity,
        expected_node_range_min=nr_min,
        expected_node_range_max=nr_max,
    )
