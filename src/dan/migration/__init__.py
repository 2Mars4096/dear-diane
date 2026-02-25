"""Graph migration helpers for legacy node/edge formats."""

from dan.migration.gate_migration import (
    migrate_graph,
    migrate_if_else_to_gate,
    migrate_while_loop_to_flat_gate,
)

__all__ = [
    "migrate_graph",
    "migrate_if_else_to_gate",
    "migrate_while_loop_to_flat_gate",
]
