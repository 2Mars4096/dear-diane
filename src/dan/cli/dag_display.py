"""ASCII DAG renderer and graph display utilities for dan-chat."""

from __future__ import annotations

import sys
from collections import defaultdict, deque
from typing import Any


def _supports_unicode() -> bool:
    enc = getattr(sys.stdout, "encoding", "") or ""
    return "utf" in enc.lower()


def _try_get_console():
    """Return a Rich Console or None."""
    try:
        from dan.cli import _try_import_rich
        Console, _ = _try_import_rich()
        return Console() if Console else None
    except Exception:
        return None


# Box-drawing character sets
_UNICODE_BOX = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│"}
_ASCII_BOX = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"}

_UNICODE_EDGE = {"v": "│", "h": "─", "fork": "├", "end": "└", "down": "┬", "arrow": "→"}
_ASCII_EDGE = {"v": "|", "h": "-", "fork": "|", "end": "\\", "down": "+", "arrow": "->"}

_NODE_TYPE_COLORS = {
    "llm_operator": "bright_cyan",
    "tool_operator": "bright_green",
    "code_operator": "bright_yellow",
    "composite": "bright_magenta",
    "if_else": "bright_red",
    "while_loop": "bright_red",
    "for_each": "bright_blue",
    "gate": "bright_red",
    "human_in_the_loop": "bright_white",
    "input_node": "green",
    "validator": "yellow",
    "rag_operator": "cyan",
    "reflection": "magenta",
    "parallel_subagents": "blue",
    "orchestrator": "bright_magenta",
    "router": "bright_yellow",
    "reduce": "bright_blue",
    "vote": "bright_green",
}


def _topological_levels(graph_dict: dict) -> list[list[dict]]:
    """Group nodes into topological levels using Kahn's algorithm."""
    nodes = graph_dict.get("nodes", [])
    edges = graph_dict.get("edges", [])

    if not nodes:
        return []

    node_map = {n.get("id", ""): n for n in nodes}
    node_ids = set(node_map.keys())

    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    out_adj: dict[str, list[str]] = defaultdict(list)

    for e in edges:
        src = e.get("source_node_id") or e.get("source", "")
        tgt = e.get("target_node_id") or e.get("target", "")
        if src in node_ids and tgt in node_ids:
            in_degree[tgt] += 1
            out_adj[src].append(tgt)

    queue: deque[str] = deque(nid for nid in node_ids if in_degree[nid] == 0)
    levels: list[list[dict]] = []

    while queue:
        level_nodes = []
        next_queue: deque[str] = deque()
        for nid in queue:
            level_nodes.append(node_map[nid])
        for nid in queue:
            for dep in out_adj.get(nid, []):
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    next_queue.append(dep)
        levels.append(sorted(level_nodes, key=lambda n: n.get("name", n.get("id", ""))))
        queue = next_queue

    # Add orphans (cycle members) that weren't visited
    visited = {n.get("id") for level in levels for n in level}
    orphans = [node_map[nid] for nid in node_ids if nid not in visited]
    if orphans:
        levels.append(sorted(orphans, key=lambda n: n.get("name", n.get("id", ""))))

    return levels


def _node_label(node: dict, max_len: int = 30) -> str:
    ntype = node.get("node_type") or node.get("type", "?")
    short_type = ntype.replace("_operator", "").replace("_node", "")
    name = node.get("name") or node.get("label") or node.get("id", "?")
    if len(name) > max_len:
        name = name[: max_len - 1] + "…" if _supports_unicode() else name[: max_len - 2] + ".."
    return f"[{short_type}] {name}"


def _node_box(label: str, box: dict, sub_count: int = 0) -> list[str]:
    suffix = f" ({sub_count} sub)" if sub_count else ""
    content = label + suffix
    width = len(content) + 2
    top = box["tl"] + box["h"] * width + box["tr"]
    mid = box["v"] + " " + content + " " + box["v"]
    bot = box["bl"] + box["h"] * width + box["br"]
    return [top, mid, bot]


def _build_edge_index(graph_dict: dict) -> dict[str, list[str]]:
    """Return {source_node_id: [target_node_id, ...]}."""
    edges = graph_dict.get("edges", [])
    index: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        src = e.get("source_node_id") or e.get("source", "")
        tgt = e.get("target_node_id") or e.get("target", "")
        if src and tgt:
            index[src].append(tgt)
    return index


def render_dag(graph_dict: dict, *, use_color: bool = True, max_width: int = 120) -> str:
    """Render a graph dict as an ASCII DAG string."""
    levels = _topological_levels(graph_dict)
    if not levels:
        return "  (empty graph)"

    unicode = _supports_unicode()
    box = _UNICODE_BOX if unicode else _ASCII_BOX
    edge_chars = _UNICODE_EDGE if unicode else _ASCII_EDGE

    sub_graphs = graph_dict.get("sub_graphs", {})
    edge_index = _build_edge_index(graph_dict)

    lines: list[str] = []
    gap = "   "

    # Pre-compute box rows and column widths for each level
    level_box_rows: list[list[list[str]]] = []
    level_col_widths: list[list[int]] = []
    for level in levels:
        box_rows: list[list[str]] = []
        for node in level:
            nid = node.get("id", "")
            label = _node_label(node)
            sub_count = sum(1 for k in sub_graphs if k.startswith(nid))
            node_lines = _node_box(label, box, sub_count)
            box_rows.append(node_lines)
        col_widths = [max(len(line) for line in rows) for rows in box_rows]
        level_box_rows.append(box_rows)
        level_col_widths.append(col_widths)

    # Track which column index each node is at within its level
    node_col: dict[str, int] = {}
    node_level_idx: dict[str, int] = {}
    for li, level in enumerate(levels):
        for ci, node in enumerate(level):
            nid = node.get("id", "")
            node_col[nid] = ci
            node_level_idx[nid] = li

    def _col_center(level_idx: int, col_idx: int) -> int:
        """Compute the character position of a column's center in its level."""
        cw = level_col_widths[level_idx]
        return sum(cw[:col_idx]) + len(gap) * col_idx + cw[col_idx] // 2

    for li, level in enumerate(levels):
        box_rows = level_box_rows[li]
        col_widths = level_col_widths[li]

        max_h = max(len(r) for r in box_rows) if box_rows else 0
        for row_i in range(max_h):
            parts = []
            for col_i, rows in enumerate(box_rows):
                cell = rows[row_i] if row_i < len(rows) else ""
                padded = cell.ljust(col_widths[col_i])
                parts.append(padded)
            combined = gap.join(parts)
            if len(combined) > max_width:
                combined = combined[: max_width - 1] + ("\u2026" if unicode else "~")
            lines.append("  " + combined)

        # Draw edges to next level
        if li < len(levels) - 1:
            next_ids = {n.get("id") for n in levels[li + 1]}
            edge_lines: list[str] = []
            for node in level:
                nid = node.get("id", "")
                targets = [t for t in edge_index.get(nid, []) if t in next_ids]
                if not targets:
                    continue
                src_center = _col_center(li, node_col[nid])
                for t in targets:
                    t_center = _col_center(li + 1, node_col[t])
                    if src_center == t_center:
                        edge_lines.append(f"  {' ' * src_center}{edge_chars['v']}")
                    else:
                        lo, hi = min(src_center, t_center), max(src_center, t_center)
                        seg = " " * lo + edge_chars["end"] + edge_chars["h"] * (hi - lo - 1) + edge_chars["fork"]
                        edge_lines.append(f"  {seg}")

            if edge_lines:
                for el in edge_lines[:5]:
                    if len(el) <= max_width:
                        lines.append(el)
            else:
                lines.append(f"  {' ' * 2}{edge_chars['v']}")

    # Renderer is intentionally pure: return text only.
    # Caller controls any terminal styling/printing behavior.
    return "\n".join(lines)


def _strip_rich_markup(text: str) -> str:
    """Remove Rich markup tags like [bold], [/], [bright_cyan] from text."""
    import re
    return re.sub(r"\[/?[a-z_]+\]", "", text)


def render_stats(graph_dict: dict) -> str:
    """Render graph statistics: node count, edge count, sub-graph count, entry/exit points."""
    nodes = graph_dict.get("nodes", [])
    edges = graph_dict.get("edges", [])
    sub_graphs = graph_dict.get("sub_graphs", {})
    entry_points = graph_dict.get("entry_points", [])
    exit_points = graph_dict.get("exit_points", [])

    type_counts: dict[str, int] = defaultdict(int)
    for n in nodes:
        ntype = n.get("node_type") or n.get("type", "unknown")
        type_counts[ntype] += 1

    edge_type_counts: dict[str, int] = defaultdict(int)
    for e in edges:
        etype = e.get("edge_type", "data")
        edge_type_counts[etype] += 1

    lines = [
        f"  Nodes: {len(nodes)}",
        f"  Edges: {len(edges)}",
        f"  Sub-graphs: {len(sub_graphs)}",
    ]
    if entry_points:
        lines.append(f"  Entry points: {', '.join(str(e) for e in entry_points)}")
    if exit_points:
        lines.append(f"  Exit points: {', '.join(str(e) for e in exit_points)}")

    if type_counts:
        lines.append("  ---")
        lines.append("  Node types:")
        for ntype, count in sorted(type_counts.items()):
            lines.append(f"    {ntype}: {count}")

    if edge_type_counts:
        lines.append("  Edge types:")
        for etype, count in sorted(edge_type_counts.items()):
            lines.append(f"    {etype}: {count}")

    return "\n".join(lines)


def format_workflow_table(graphs: list[dict], current_id: str | None = None) -> str:
    """Format workflow list as a table with Name, Nodes, Edges, Last Modified."""
    if not graphs:
        return "  No saved workflows."

    # Sort by updated_at descending
    sorted_graphs = sorted(graphs, key=lambda g: g.get("updated_at", ""), reverse=True)

    # Compute column widths
    headers = ["ID", "Name", "Nodes", "Edges", "Last Modified"]
    rows: list[list[str]] = []
    for g in sorted_graphs:
        gid = g.get("graph_id", "?")
        name = g.get("name", "")
        node_count = str(g.get("node_count", len(g.get("nodes", []))))
        edge_count = str(g.get("edge_count", len(g.get("edges", []))))
        updated = (g.get("updated_at") or "")[:19]
        marker = " *" if gid == current_id else ""
        rows.append([gid + marker, name, node_count, edge_count, updated])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # Build table
    unicode = _supports_unicode()
    h_char = "─" if unicode else "-"
    sep = "  " + "  ".join(h_char * w for w in col_widths)

    lines = ["  " + "  ".join(h.ljust(w) for h, w in zip(headers, col_widths))]
    lines.append(sep)
    for row in rows:
        lines.append("  " + "  ".join(cell.ljust(w) for cell, w in zip(row, col_widths)))

    return "\n".join(lines)
