"""Graph equivalence checker and round-trip validation helper.

Compares two DAN ``Graph`` objects for structural equivalence
(ignoring cosmetic fields like positions, timestamps, and metadata
by default) and provides a ``round_trip_check`` helper that builds,
decompiles, rebuilds, and compares a workflow in one call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dan.models.graph import Graph


@dataclass
class EquivalenceResult:
    """Outcome of a structural graph comparison."""

    equivalent: bool
    mismatches: list[str] = field(default_factory=list)


class GraphEquivalenceChecker:
    """Compare two ``Graph`` instances for structural equivalence.

    By default cosmetic differences (positions, metadata, timestamps)
    are ignored so that functionally identical graphs compare equal.
    """

    def __init__(
        self,
        ignore_positions: bool = True,
        ignore_metadata: bool = True,
        ignore_timestamps: bool = True,
    ) -> None:
        self.ignore_positions = ignore_positions
        self.ignore_metadata = ignore_metadata
        self.ignore_timestamps = ignore_timestamps

    def check(self, graph_a: Graph, graph_b: Graph) -> EquivalenceResult:
        """Return an ``EquivalenceResult`` comparing *graph_a* to *graph_b*."""
        mismatches: list[str] = []
        self._compare_nodes(graph_a, graph_b, mismatches)
        self._compare_edges(graph_a, graph_b, mismatches)
        self._compare_entry_exit(graph_a, graph_b, mismatches)
        self._compare_sub_graphs(graph_a, graph_b, mismatches)
        return EquivalenceResult(equivalent=len(mismatches) == 0, mismatches=mismatches)

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def _compare_nodes(self, a: Graph, b: Graph, mismatches: list[str]) -> None:
        a_map = {n.id: n for n in a.nodes}
        b_map = {n.id: n for n in b.nodes}
        a_ids = set(a_map)
        b_ids = set(b_map)

        for nid in sorted(a_ids - b_ids):
            mismatches.append(f"missing node '{nid}' in graph_b")
        for nid in sorted(b_ids - a_ids):
            mismatches.append(f"extra node '{nid}' in graph_b (not in graph_a)")

        for nid in sorted(a_ids & b_ids):
            na, nb = a_map[nid], b_map[nid]
            if na.node_type != nb.node_type:
                mismatches.append(
                    f"node '{nid}' type mismatch: "
                    f"'{na.node_type}' vs '{nb.node_type}'"
                )

            a_in = sorted(p.name for p in na.input_ports)
            b_in = sorted(p.name for p in nb.input_ports)
            if a_in != b_in:
                mismatches.append(
                    f"node '{nid}' input port mismatch: {a_in} vs {b_in}"
                )

            a_out = sorted(p.name for p in na.output_ports)
            b_out = sorted(p.name for p in nb.output_ports)
            if a_out != b_out:
                mismatches.append(
                    f"node '{nid}' output port mismatch: {a_out} vs {b_out}"
                )

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------

    @staticmethod
    def _edge_key(e) -> tuple[str, str, str, str, str]:
        """Canonical tuple for comparing edges regardless of ``id``."""
        return (
            e.source_node_id,
            e.target_node_id,
            e.source_port,
            e.target_port,
            e.edge_type,
        )

    def _compare_edges(self, a: Graph, b: Graph, mismatches: list[str]) -> None:
        a_edges = {self._edge_key(e) for e in a.edges}
        b_edges = {self._edge_key(e) for e in b.edges}

        for key in sorted(a_edges - b_edges):
            src, tgt, sp, tp, et = key
            mismatches.append(
                f"missing edge {src}.{sp} -> {tgt}.{tp} ({et}) in graph_b"
            )
        for key in sorted(b_edges - a_edges):
            src, tgt, sp, tp, et = key
            mismatches.append(
                f"extra edge {src}.{sp} -> {tgt}.{tp} ({et}) in graph_b"
            )

    # ------------------------------------------------------------------
    # Entry / exit points
    # ------------------------------------------------------------------

    def _compare_entry_exit(self, a: Graph, b: Graph, mismatches: list[str]) -> None:
        a_entry = set(a.entry_points)
        b_entry = set(b.entry_points)
        if a_entry != b_entry:
            mismatches.append(
                f"entry_points mismatch: {sorted(a_entry)} vs {sorted(b_entry)}"
            )

        a_exit = set(a.exit_points)
        b_exit = set(b.exit_points)
        if a_exit != b_exit:
            mismatches.append(
                f"exit_points mismatch: {sorted(a_exit)} vs {sorted(b_exit)}"
            )

    # ------------------------------------------------------------------
    # Sub-graphs (recursive)
    # ------------------------------------------------------------------

    def _compare_sub_graphs(self, a: Graph, b: Graph, mismatches: list[str]) -> None:
        a_keys = set(a.sub_graphs)
        b_keys = set(b.sub_graphs)

        for key in sorted(a_keys - b_keys):
            mismatches.append(f"missing sub_graph '{key}' in graph_b")
        for key in sorted(b_keys - a_keys):
            mismatches.append(f"extra sub_graph '{key}' in graph_b")

        for key in sorted(a_keys & b_keys):
            sub_result = self.check(a.sub_graphs[key], b.sub_graphs[key])
            for m in sub_result.mismatches:
                mismatches.append(f"sub_graph '{key}': {m}")


# ------------------------------------------------------------------
# Round-trip helper
# ------------------------------------------------------------------


def round_trip_check(builder_code: str) -> EquivalenceResult:
    """Execute *builder_code*, decompile, rebuild, and compare.

    The code must assign the resulting ``Graph`` to a variable named
    ``graph``.  Returns an ``EquivalenceResult`` describing any
    structural mismatches.
    """
    from dan.builder import decompile  # noqa: local to avoid circular at import time

    ns_a: dict = {}
    exec(builder_code, ns_a)  # noqa: S102
    graph_a: Graph = ns_a["graph"]

    decompiled = decompile(graph_a)

    ns_b: dict = {}
    exec(decompiled, ns_b)  # noqa: S102
    graph_b: Graph = ns_b["graph"]

    checker = GraphEquivalenceChecker()
    return checker.check(graph_a, graph_b)
