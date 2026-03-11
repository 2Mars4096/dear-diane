"""Structural mutation macros for progressive NL refinement.

High-level graph transformations that map NL intents to reliable,
validated modifications — bridging the gap between fine-grained
GraphMutator edits and full-rebuild codegen.
"""

from __future__ import annotations

import copy
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class MutationMacroResult:
    """Result of a structural mutation macro."""
    success: bool
    graph_dict: dict[str, Any] | None = None
    error: str | None = None
    nodes_added: list[str] = field(default_factory=list)
    nodes_removed: list[str] = field(default_factory=list)
    edges_added: int = 0
    edges_removed: int = 0


# ---------------------------------------------------------------------------
# Graph dict helpers
# ---------------------------------------------------------------------------

def _find_node(graph: dict, node_id: str) -> dict | None:
    for n in graph.get("nodes", []):
        if n.get("id") == node_id:
            return n
    return None


def _find_node_in_subgraphs(graph: dict, node_id: str) -> tuple[dict, dict] | None:
    """Search for a node inside sub_graphs. Returns (sub_graph_dict, node_dict)."""
    for sg in graph.get("sub_graphs", {}).values():
        if isinstance(sg, dict):
            node = _find_node(sg, node_id)
            if node:
                return sg, node
            result = _find_node_in_subgraphs(sg, node_id)
            if result:
                return result
    return None


def _get_downstream_edges(graph: dict, node_id: str) -> list[dict]:
    """Get all data edges where node_id is the source."""
    return [
        e for e in graph.get("edges", {}).get("data", [])
        if e.get("source_node_id") == node_id
    ]


def _get_upstream_edges(graph: dict, node_id: str) -> list[dict]:
    """Get all data edges where node_id is the target."""
    return [
        e for e in graph.get("edges", {}).get("data", [])
        if e.get("target_node_id") == node_id
    ]


def _remove_edges_involving(graph: dict, node_id: str) -> list[dict]:
    """Remove all data edges involving node_id. Returns removed edges."""
    data_edges = graph.get("edges", {}).get("data", [])
    removed = [
        e for e in data_edges
        if e.get("source_node_id") == node_id or e.get("target_node_id") == node_id
    ]
    graph["edges"]["data"] = [e for e in data_edges if e not in removed]
    return removed


def _add_data_edge(graph: dict, src_id: str, src_port: str, tgt_id: str, tgt_port: str) -> None:
    if "edges" not in graph:
        graph["edges"] = {"data": [], "control": [], "context": []}
    graph["edges"].setdefault("data", []).append({
        "source_node_id": src_id,
        "source_port": src_port,
        "target_node_id": tgt_id,
        "target_port": tgt_port,
    })


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------------------
# Atomic macro wrapper
# ---------------------------------------------------------------------------

def _atomic_macro(fn):
    """Wrap a macro so it operates on a deep copy; restores on failure."""
    import functools

    @functools.wraps(fn)
    def wrapper(graph: dict, *args, **kwargs) -> MutationMacroResult:
        snapshot = copy.deepcopy(graph)
        try:
            result = fn(graph, *args, **kwargs)
            if not result.success:
                graph.clear()
                graph.update(snapshot)
            return result
        except Exception as exc:
            graph.clear()
            graph.update(snapshot)
            return MutationMacroResult(success=False, error=str(exc))
    return wrapper


# ---------------------------------------------------------------------------
# Structural mutation macros
# ---------------------------------------------------------------------------

@_atomic_macro
def wrap_in_review_loop(
    graph: dict,
    node_id: str,
    reviewer_prompt: str = "Review the output for quality and correctness.",
    max_rounds: int = 3,
) -> MutationMacroResult:
    """Insert a review loop after the target node.

    Creates a reviewer LLM node and a gate wrapping the target node's
    downstream flow with iterative quality review.
    """
    node = _find_node(graph, node_id)
    if node is None:
        return MutationMacroResult(success=False, error=f"Node '{node_id}' not found")

    reviewer_id = _gen_id("reviewer")
    gate_id = _gen_id("review_gate")

    downstream = _get_downstream_edges(graph, node_id)

    reviewer_node = {
        "id": reviewer_id,
        "node_type": "llm_operator",
        "config": {
            "name": "reviewer",
            "prompt_template": reviewer_prompt,
            "system_prompt": "",
            "temperature": 0.3,
        },
    }

    gate_node = {
        "id": gate_id,
        "node_type": "gate",
        "config": {
            "name": "review_gate",
            "condition": "quality_score >= 8",
            "gate_mode": "while",
            "max_iterations": max_rounds,
        },
    }

    graph["nodes"].extend([reviewer_node, gate_node])

    _add_data_edge(graph, node_id, "text", reviewer_id, "text")
    _add_data_edge(graph, reviewer_id, "text", gate_id, "data")

    for edge in downstream:
        graph["edges"]["data"].remove(edge)
        _add_data_edge(graph, gate_id, "true", edge["target_node_id"], edge["target_port"])

    return MutationMacroResult(
        success=True,
        graph_dict=graph,
        nodes_added=[reviewer_id, gate_id],
        edges_added=2 + len(downstream),
        edges_removed=len(downstream),
    )


@_atomic_macro
def fan_out_node(
    graph: dict,
    node_id: str,
    items_expr: str = "items",
    parallelism: int = 3,
) -> MutationMacroResult:
    """Wrap the target node in a for_each loop with a reduce node downstream."""
    node = _find_node(graph, node_id)
    if node is None:
        return MutationMacroResult(success=False, error=f"Node '{node_id}' not found")

    fe_id = _gen_id("fan_out")
    reduce_id = _gen_id("reduce")

    downstream = _get_downstream_edges(graph, node_id)
    upstream = _get_upstream_edges(graph, node_id)

    fe_node = {
        "id": fe_id,
        "node_type": "for_each",
        "config": {
            "name": f"fan_out_{node_id}",
            "parallelism": parallelism,
            "body_graph": f"{fe_id}_body",
            "merge_strategy": "append",
        },
    }

    reduce_node = {
        "id": reduce_id,
        "node_type": "reduce",
        "config": {
            "name": f"reduce_{node_id}",
            "reducer": "concatenate",
        },
    }

    graph["nodes"].extend([fe_node, reduce_node])

    for edge in upstream:
        graph["edges"]["data"].remove(edge)
        _add_data_edge(graph, edge["source_node_id"], edge["source_port"], fe_id, "items")

    _add_data_edge(graph, fe_id, "results", reduce_id, "data")

    for edge in downstream:
        graph["edges"]["data"].remove(edge)
        _add_data_edge(graph, reduce_id, "result", edge["target_node_id"], edge["target_port"])

    return MutationMacroResult(
        success=True,
        graph_dict=graph,
        nodes_added=[fe_id, reduce_id],
        edges_added=1 + len(upstream) + len(downstream),
        edges_removed=len(upstream) + len(downstream),
    )


@_atomic_macro
def insert_validator(
    graph: dict,
    source_id: str,
    target_id: str,
    rules: list[dict] | None = None,
) -> MutationMacroResult:
    """Insert a validator node on the edge between source and target."""
    source = _find_node(graph, source_id)
    target = _find_node(graph, target_id)
    if source is None:
        return MutationMacroResult(success=False, error=f"Source node '{source_id}' not found")
    if target is None:
        return MutationMacroResult(success=False, error=f"Target node '{target_id}' not found")

    edge_to_split = None
    for e in graph.get("edges", {}).get("data", []):
        if e["source_node_id"] == source_id and e["target_node_id"] == target_id:
            edge_to_split = e
            break

    if edge_to_split is None:
        return MutationMacroResult(success=False, error=f"No edge between '{source_id}' and '{target_id}'")

    validator_id = _gen_id("validator")
    validator_node = {
        "id": validator_id,
        "node_type": "validator",
        "config": {
            "name": f"validate_{source_id}_to_{target_id}",
            "validation_rules": rules or [{"rule_type": "format_check", "config": {}}],
            "on_failure": "route",
            "strict_mode": False,
        },
    }

    graph["nodes"].append(validator_node)
    graph["edges"]["data"].remove(edge_to_split)

    _add_data_edge(graph, source_id, edge_to_split["source_port"], validator_id, "data")
    _add_data_edge(graph, validator_id, "valid", target_id, edge_to_split["target_port"])

    return MutationMacroResult(
        success=True,
        graph_dict=graph,
        nodes_added=[validator_id],
        edges_added=2,
        edges_removed=1,
    )


@_atomic_macro
def insert_tool(
    graph: dict,
    anchor_id: str,
    tool_id: str,
    config: dict | None = None,
    position: str = "after",
) -> MutationMacroResult:
    """Insert a tool node before or after the anchor node."""
    anchor = _find_node(graph, anchor_id)
    if anchor is None:
        return MutationMacroResult(success=False, error=f"Anchor node '{anchor_id}' not found")

    tool_node_id = _gen_id(f"tool_{tool_id}")
    tool_node = {
        "id": tool_node_id,
        "node_type": "tool_operator",
        "config": {
            "name": tool_id,
            "tool_id": tool_id,
            "tool_config": config or {},
        },
    }
    graph["nodes"].append(tool_node)

    if position == "after":
        downstream = _get_downstream_edges(graph, anchor_id)
        for edge in downstream:
            graph["edges"]["data"].remove(edge)
            _add_data_edge(graph, tool_node_id, "result", edge["target_node_id"], edge["target_port"])
        _add_data_edge(graph, anchor_id, "text", tool_node_id, "data")
        edges_added = 1 + len(downstream)
        edges_removed = len(downstream)
    else:
        upstream = _get_upstream_edges(graph, anchor_id)
        for edge in upstream:
            graph["edges"]["data"].remove(edge)
            _add_data_edge(graph, edge["source_node_id"], edge["source_port"], tool_node_id, "data")
        _add_data_edge(graph, tool_node_id, "result", anchor_id, "text")
        edges_added = 1 + len(upstream)
        edges_removed = len(upstream)

    return MutationMacroResult(
        success=True,
        graph_dict=graph,
        nodes_added=[tool_node_id],
        edges_added=edges_added,
        edges_removed=edges_removed,
    )


@_atomic_macro
def parallelize(
    graph: dict,
    node_ids: list[str],
) -> MutationMacroResult:
    """Wrap listed nodes in a parallel_subagents block."""
    for nid in node_ids:
        if _find_node(graph, nid) is None:
            return MutationMacroResult(success=False, error=f"Node '{nid}' not found")

    if len(node_ids) < 2:
        return MutationMacroResult(success=False, error="Need at least 2 nodes to parallelize")

    par_id = _gen_id("parallel")
    merge_id = _gen_id("merge")

    all_upstream: list[dict] = []
    all_downstream: list[dict] = []
    for nid in node_ids:
        for e in _get_upstream_edges(graph, nid):
            if e["source_node_id"] not in node_ids:
                all_upstream.append(e)
        for e in _get_downstream_edges(graph, nid):
            if e["target_node_id"] not in node_ids:
                all_downstream.append(e)

    par_node = {
        "id": par_id,
        "node_type": "parallel_subagents",
        "config": {
            "name": "parallel_block",
            "branch_graphs": [f"{par_id}_{nid}" for nid in node_ids],
            "parallelism": len(node_ids),
            "merge_strategy": "append",
        },
    }
    merge_node = {
        "id": merge_id,
        "node_type": "reduce",
        "config": {"name": "parallel_merge", "reducer": "concatenate"},
    }

    graph["nodes"].extend([par_node, merge_node])

    for e in all_upstream:
        if e in graph["edges"]["data"]:
            graph["edges"]["data"].remove(e)
        _add_data_edge(graph, e["source_node_id"], e["source_port"], par_id, "input")

    for e in all_downstream:
        if e in graph["edges"]["data"]:
            graph["edges"]["data"].remove(e)
        _add_data_edge(graph, merge_id, "result", e["target_node_id"], e["target_port"])

    _add_data_edge(graph, par_id, "results", merge_id, "data")

    return MutationMacroResult(
        success=True,
        graph_dict=graph,
        nodes_added=[par_id, merge_id],
        edges_added=1 + len(all_upstream) + len(all_downstream),
        edges_removed=len(all_upstream) + len(all_downstream),
    )


@_atomic_macro
def unwrap_loop(
    graph: dict,
    loop_node_id: str,
) -> MutationMacroResult:
    """Remove a while_loop/goal_loop, reconnecting its content directly."""
    node = _find_node(graph, loop_node_id)
    if node is None:
        return MutationMacroResult(success=False, error=f"Node '{loop_node_id}' not found")

    if node.get("node_type") not in ("while_loop", "goal_loop"):
        return MutationMacroResult(success=False, error=f"Node '{loop_node_id}' is not a loop node")

    upstream = _get_upstream_edges(graph, loop_node_id)
    downstream = _get_downstream_edges(graph, loop_node_id)

    graph["nodes"] = [n for n in graph["nodes"] if n["id"] != loop_node_id]
    _remove_edges_involving(graph, loop_node_id)

    for up in upstream:
        for down in downstream:
            _add_data_edge(
                graph, up["source_node_id"], up["source_port"],
                down["target_node_id"], down["target_port"],
            )

    return MutationMacroResult(
        success=True,
        graph_dict=graph,
        nodes_removed=[loop_node_id],
        edges_added=len(upstream) * len(downstream),
        edges_removed=len(upstream) + len(downstream),
    )


# ---------------------------------------------------------------------------
# Node resolution
# ---------------------------------------------------------------------------

def resolve_node(graph: dict, reference_text: str) -> list[str]:
    """Resolve a natural-language node reference to node IDs.

    Returns a list of candidate node IDs, best match first.
    """
    text_lower = reference_text.lower().strip()
    nodes = graph.get("nodes", [])
    candidates: list[tuple[float, str]] = []

    for node in nodes:
        nid = node.get("id", "")
        name = node.get("config", {}).get("name", nid)
        prompt = node.get("config", {}).get("prompt_template", "")

        score = 0.0

        if text_lower == nid.lower():
            score = 1.0
        elif text_lower == name.lower():
            score = 0.95
        elif text_lower in nid.lower() or nid.lower() in text_lower:
            score = 0.7
        elif text_lower in name.lower() or name.lower() in text_lower:
            score = 0.65
        elif prompt and text_lower in prompt.lower():
            score = 0.4

        if score > 0:
            candidates.append((score, nid))

    if text_lower in ("the last step", "last step", "final step", "the last node"):
        if nodes:
            candidates.append((0.8, nodes[-1]["id"]))
    elif text_lower in ("the first step", "first step", "the first node"):
        if nodes:
            candidates.append((0.8, nodes[0]["id"]))
    elif re.match(r"step\s*(\d+)", text_lower):
        m = re.match(r"step\s*(\d+)", text_lower)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(nodes):
                candidates.append((0.8, nodes[idx]["id"]))

    candidates.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    result: list[str] = []
    for _, nid in candidates:
        if nid not in seen:
            seen.add(nid)
            result.append(nid)
    return result


# ---------------------------------------------------------------------------
# Macro dispatcher
# ---------------------------------------------------------------------------

_MACRO_KEYWORDS_UNSORTED: dict[str, str] = {
    "add review loop": "wrap_in_review_loop",
    "add a review loop": "wrap_in_review_loop",
    "review loop": "wrap_in_review_loop",
    "fan out": "fan_out_node",
    "parallelize": "parallelize",
    "run in parallel": "parallelize",
    "add validation": "insert_validator",
    "add a validation gate": "insert_validator",
    "insert validator": "insert_validator",
    "add tool": "insert_tool",
    "insert tool": "insert_tool",
    "add a tool step": "insert_tool",
    "remove loop": "unwrap_loop",
    "unwrap loop": "unwrap_loop",
    "remove the review loop": "unwrap_loop",
}

# longest-first so "remove the review loop" matches before "review loop"
_MACRO_KEYWORDS: dict[str, str] = dict(
    sorted(_MACRO_KEYWORDS_UNSORTED.items(), key=lambda kv: -len(kv[0]))
)

_MACRO_FUNCTIONS = {
    "wrap_in_review_loop": wrap_in_review_loop,
    "fan_out_node": fan_out_node,
    "insert_validator": insert_validator,
    "insert_tool": insert_tool,
    "parallelize": parallelize,
    "unwrap_loop": unwrap_loop,
}


@dataclass
class DispatchResult:
    """Result of macro dispatch."""
    matched: bool
    macro_name: str | None = None
    result: MutationMacroResult | None = None
    error: str | None = None


def dispatch_structural_mutation(
    graph: dict,
    user_text: str,
    *,
    target_node_hint: str | None = None,
) -> DispatchResult:
    """Map a user's NL follow-up to a structural macro and execute it.

    Returns DispatchResult with matched=False if no macro matches,
    allowing the caller to fall through to codegen.
    """
    text_lower = user_text.lower()

    macro_name = None
    for keyword, name in _MACRO_KEYWORDS.items():
        if keyword in text_lower:
            macro_name = name
            break

    if macro_name is None:
        return DispatchResult(matched=False)

    macro_fn = _MACRO_FUNCTIONS.get(macro_name)
    if macro_fn is None:
        return DispatchResult(matched=False)

    extracted_number: int | None = None
    num_match = re.search(r"(?:with\s+)?(\d+)\s*(?:rounds?|iterations?|times|workers?|parallel)", text_lower)
    if num_match:
        extracted_number = int(num_match.group(1))
    else:
        num_match = re.search(r"(?:parallelism|max.?rounds?|iterations?)\s*(?:=|of|:)?\s*(\d+)", text_lower)
        if num_match:
            extracted_number = int(num_match.group(1))

    target_node_ids: list[str] = []
    if target_node_hint:
        target_node_ids = resolve_node(graph, target_node_hint)
    else:
        patterns = [
            r"(?:after|to|on|around)\s+(?:the\s+)?(.+?)(?:\s+step|\s+node)",
            r"(?:the\s+)?(.+?)\s+(?:step|node)",
        ]
        for pat in patterns:
            m = re.search(pat, text_lower)
            if m:
                ref = m.group(1).strip()
                target_node_ids = resolve_node(graph, ref)
                if target_node_ids:
                    break

    if not target_node_ids and macro_name not in ("parallelize",):
        nodes = graph.get("nodes", [])
        if nodes:
            target_node_ids = [nodes[-1]["id"]]

    try:
        if macro_name == "wrap_in_review_loop":
            kwargs: dict[str, Any] = {}
            if extracted_number is not None:
                kwargs["max_rounds"] = extracted_number
            result = wrap_in_review_loop(graph, target_node_ids[0] if target_node_ids else "", **kwargs)
        elif macro_name == "fan_out_node":
            kwargs = {}
            if extracted_number is not None:
                kwargs["parallelism"] = extracted_number
            result = fan_out_node(graph, target_node_ids[0] if target_node_ids else "", **kwargs)
        elif macro_name == "insert_validator":
            if len(target_node_ids) >= 2:
                result = insert_validator(graph, target_node_ids[0], target_node_ids[1])
            elif target_node_ids:
                up_edges = _get_upstream_edges(graph, target_node_ids[0])
                if up_edges:
                    result = insert_validator(graph, up_edges[0]["source_node_id"], target_node_ids[0])
                else:
                    result = MutationMacroResult(success=False, error="Cannot determine where to insert validator")
            else:
                result = MutationMacroResult(success=False, error="No target node for validator")
        elif macro_name == "insert_tool":
            tool_match = re.search(r"(?:tool|step)\s+(\w+)", text_lower)
            tool_id = tool_match.group(1) if tool_match else "web_search"
            pos = "before" if "before" in text_lower else "after"
            result = insert_tool(graph, target_node_ids[0] if target_node_ids else "", tool_id, position=pos)
        elif macro_name == "parallelize":
            if len(target_node_ids) >= 2:
                result = parallelize(graph, target_node_ids[:min(len(target_node_ids), 5)])
            else:
                result = MutationMacroResult(success=False, error="Need at least 2 nodes to parallelize")
        elif macro_name == "unwrap_loop":
            result = unwrap_loop(graph, target_node_ids[0] if target_node_ids else "")
        else:
            return DispatchResult(matched=False)
    except Exception as exc:
        return DispatchResult(
            matched=True,
            macro_name=macro_name,
            error=str(exc),
        )

    return DispatchResult(
        matched=True,
        macro_name=macro_name,
        result=result,
    )


# ---------------------------------------------------------------------------
# Graph summary (for codegen context injection)
# ---------------------------------------------------------------------------

def summarize_graph(graph: dict) -> str:
    """Produce a compact text summary of a graph for codegen context."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", {}).get("data", [])

    lines = [f"Workflow: {graph.get('metadata', {}).get('name', 'unnamed')}"]
    lines.append(f"Nodes ({len(nodes)}):")
    for n in nodes:
        nid = n.get("id", "?")
        ntype = n.get("node_type", "?")
        prompt = n.get("config", {}).get("prompt_template", "")
        snippet = (prompt[:50] + "...") if len(prompt) > 50 else prompt
        lines.append(f"  - {nid} ({ntype}): {snippet}")

    lines.append(f"Edges ({len(edges)}):")
    for e in edges[:20]:
        lines.append(
            f"  - {e.get('source_node_id')}:{e.get('source_port')} -> "
            f"{e.get('target_node_id')}:{e.get('target_port')}"
        )

    loop_nodes = [n for n in nodes if n.get("node_type") in ("while_loop", "goal_loop", "for_each")]
    if loop_nodes:
        lines.append(f"Loops: {', '.join(n['id'] for n in loop_nodes)}")

    parallel_nodes = [n for n in nodes if n.get("node_type") == "parallel_subagents"]
    if parallel_nodes:
        lines.append(f"Parallel blocks: {', '.join(n['id'] for n in parallel_nodes)}")

    return "\n".join(lines)
