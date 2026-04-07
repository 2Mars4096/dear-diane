"""Post-hoc graph materializer — converts executed Graph to readable builder DSL.

Wraps ``dan.builder.decompiler.decompile()`` with optional convenience-method
detection: when the graph topology matches a known pattern, emits higher-level
API calls (``wf.chain()``, ``wf.review_loop()``, etc.) for readability.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from dan.models.edges import DataEdge
from dan.models.graph import Graph
from dan.worker.presets import worker_to_legacy

logger = logging.getLogger(__name__)


def _semantic_node_type(node: Any) -> str:
    if getattr(node, "node_type", None) == "worker":
        legacy = worker_to_legacy(node)
        if legacy is not None:
            return legacy.node_type
    return getattr(node, "node_type", "")


def graph_to_builder_code(graph: Graph, *, use_convenience: bool = True) -> str:
    """Convert a Graph to executable builder DSL code.

    When *use_convenience* is True, detects common patterns and emits
    convenience methods.  Falls back to the lossless ``decompile()`` output
    when detection fails or use_convenience is False.
    """
    from dan.builder.decompiler import decompile

    base_code = decompile(graph)

    if not use_convenience:
        return base_code

    try:
        improved = _apply_convenience_patterns(graph, base_code)
        if improved:
            return improved
    except Exception:
        logger.debug(
            "Convenience pattern detection failed, using base decompile",
            exc_info=True,
        )

    return base_code


def materialize_graph(
    graph: Graph,
    *,
    graph_store: Any | None = None,
    workflow_id: str | None = None,
) -> dict:
    """Generate builder code and optionally persist the graph."""
    code = graph_to_builder_code(graph)
    result: dict[str, Any] = {
        "builder_code": code,
        "node_count": len(graph.nodes),
        "materialized": True,
    }

    if graph_store is not None and workflow_id is not None:
        try:
            graph_store.save_graph(workflow_id, graph.model_dump(mode="json"))
            result["persisted"] = True
        except Exception:
            logger.warning("Failed to persist materialized graph", exc_info=True)
            result["persisted"] = False

    return result


# ---------------------------------------------------------------------------
# Pattern detection internals
# ---------------------------------------------------------------------------

def _detect_linear_chains(graph: Graph) -> list[list[str]] | None:
    """Detect sequences of 2+ ``llm_operator`` nodes connected linearly.

    Returns a list of chains (each a list of node IDs) or None if no
    qualifying chain exists.
    """
    llm_ids = {n.id for n in graph.nodes if _semantic_node_type(n) == "llm_operator"}
    if len(llm_ids) < 2:
        return None

    out_edges: dict[str, list[str]] = defaultdict(list)
    in_edges: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        if isinstance(edge, DataEdge):
            out_edges[edge.source_node_id].append(edge.target_node_id)
            in_edges[edge.target_node_id].append(edge.source_node_id)

    out_single: dict[str, str] = {}
    in_single: dict[str, str] = {}
    for nid in llm_ids:
        targets = [t for t in out_edges.get(nid, []) if t in llm_ids]
        if len(targets) == 1:
            out_single[nid] = targets[0]
        sources = [s for s in in_edges.get(nid, []) if s in llm_ids]
        if len(sources) == 1:
            in_single[nid] = sources[0]

    heads = [nid for nid in out_single if nid not in in_single]
    if not heads:
        return None

    visited: set[str] = set()
    chains: list[list[str]] = []
    for start in sorted(heads):
        if start in visited:
            continue
        chain = [start]
        visited.add(start)
        cur = start
        while cur in out_single:
            nxt = out_single[cur]
            if nxt in visited:
                break
            chain.append(nxt)
            visited.add(nxt)
            cur = nxt
        if len(chain) >= 2:
            chains.append(chain)

    return chains or None


def _detect_review_loops(graph: Graph) -> list[str]:
    """Return IDs of ``while_loop`` nodes whose body looks like writer+reviewer."""
    from dan.models.control_flow import WhileLoopNode

    hits: list[str] = []
    for node in graph.nodes:
        if not isinstance(node, WhileLoopNode):
            continue
        body_key = getattr(node, "body_graph", None)
        if not body_key:
            continue
        sub = graph.sub_graphs.get(body_key)
        if sub is None:
            continue
        body_types = {_semantic_node_type(n) for n in sub.nodes}
        body_names = {(n.name or n.id).lower() for n in sub.nodes}
        if "llm_operator" not in body_types:
            continue
        has_writer = any("writ" in nm for nm in body_names)
        has_reviewer = any("review" in nm for nm in body_names)
        if has_writer and has_reviewer:
            hits.append(node.id)
    return hits


def _apply_convenience_patterns(graph: Graph, base_code: str) -> str | None:
    """Annotate the decompiled code with detected convenience patterns.

    Adds comments indicating where ``wf.chain()`` or ``wf.review_loop()``
    could express the topology more concisely.  The underlying code remains
    the lossless decompiler output so round-trips are exact.

    Returns annotated code or None if no patterns were detected.
    """
    chains = _detect_linear_chains(graph)
    review_ids = _detect_review_loops(graph)

    if not chains and not review_ids:
        return None

    chain_heads: dict[str, list[str]] = {}
    if chains:
        for chain in chains:
            chain_heads[chain[0]] = chain

    lines = base_code.splitlines()
    result_lines: list[str] = []
    modifications = 0

    for line in lines:
        if chain_heads and ("= wf.llm(" in line or "= wf.worker(" in line):
            for head_id, chain in chain_heads.items():
                if repr(head_id) in line:
                    ids_str = " >> ".join(chain)
                    result_lines.append(
                        f"# Chain pattern detected ({len(chain)} nodes): {ids_str}"
                    )
                    result_lines.append(
                        f"# Equivalent to: wf.chain({', '.join(repr(nid) for nid in chain)})"
                    )
                    modifications += 1
                    break

        if review_ids and "wf.while_loop(" in line:
            for rid in review_ids:
                if repr(rid) in line:
                    result_lines.append("# Review loop pattern detected")
                    modifications += 1
                    break

        result_lines.append(line)

    if modifications == 0:
        return None

    return "\n".join(result_lines)
