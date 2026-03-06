"""Mutation diff display for dan-chat — formats mutation plans as colored diffs."""

from __future__ import annotations

import sys
from typing import Any


_ANSI_GREEN = "\033[32m"
_ANSI_RED = "\033[31m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RESET = "\033[0m"


def _supports_color() -> bool:
    """Check if terminal supports ANSI color."""
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        return "ANSICON" in __import__("os").environ or "WT_SESSION" in __import__("os").environ
    return True


def format_mutation_diff(mutation_plan: dict[str, Any]) -> str:
    """Format a mutation plan as a colored diff-style display.

    Operations types: add_node, remove_node, edit_node, add_edge, remove_edge.
    """
    ops = mutation_plan.get("operations", [])
    desc = mutation_plan.get("description", "Graph mutation")
    color = _supports_color()

    def _green(text: str) -> str:
        return f"{_ANSI_GREEN}{text}{_ANSI_RESET}" if color else text

    def _red(text: str) -> str:
        return f"{_ANSI_RED}{text}{_ANSI_RESET}" if color else text

    def _yellow(text: str) -> str:
        return f"{_ANSI_YELLOW}{text}{_ANSI_RESET}" if color else text

    lines = [f"  {desc}", "  ---"]

    add_count = 0
    remove_count = 0
    edit_count = 0

    for op in ops:
        op_type = op.get("op") or op.get("type", "")
        name = op.get("name", op.get("node_id", ""))
        node_type = op.get("node_type", "")

        if op_type == "add_node":
            type_suffix = f" ({node_type})" if node_type else ""
            lines.append(_green(f"  + Node: {name}{type_suffix}"))
            add_count += 1

        elif op_type == "remove_node":
            lines.append(_red(f"  - Node: {name}"))
            remove_count += 1

        elif op_type == "edit_node":
            changes = op.get("changes", op.get("updates", {}))
            change_desc = ""
            if isinstance(changes, dict):
                keys = list(changes.keys())[:3]
                change_desc = f" [{', '.join(keys)}]" if keys else ""
            lines.append(_yellow(f"  ~ Node: {name}{change_desc}"))
            edit_count += 1

        elif op_type == "add_edge":
            src = op.get("source_node_id") or op.get("source", "?")
            src_port = op.get("source_port", "")
            tgt = op.get("target_node_id") or op.get("target", "?")
            tgt_port = op.get("target_port", "")
            src_label = f"{src}.{src_port}" if src_port else src
            tgt_label = f"{tgt}.{tgt_port}" if tgt_port else tgt
            lines.append(_green(f"  + Edge: {src_label} → {tgt_label}"))
            add_count += 1

        elif op_type == "remove_edge":
            src = op.get("source_node_id") or op.get("source", "?")
            src_port = op.get("source_port", "")
            tgt = op.get("target_node_id") or op.get("target", "?")
            tgt_port = op.get("target_port", "")
            src_label = f"{src}.{src_port}" if src_port else src
            tgt_label = f"{tgt}.{tgt_port}" if tgt_port else tgt
            lines.append(_red(f"  - Edge: {src_label} → {tgt_label}"))
            remove_count += 1

        else:
            lines.append(f"  ? {op_type}: {name}")

    lines.append("  ---")
    summary_parts = []
    if add_count:
        summary_parts.append(_green(f"+{add_count}") if color else f"+{add_count}")
    if remove_count:
        summary_parts.append(_red(f"-{remove_count}") if color else f"-{remove_count}")
    if edit_count:
        summary_parts.append(_yellow(f"~{edit_count}") if color else f"~{edit_count}")
    lines.append(f"  {', '.join(summary_parts)}" if summary_parts else "  (no changes)")

    return "\n".join(lines)
