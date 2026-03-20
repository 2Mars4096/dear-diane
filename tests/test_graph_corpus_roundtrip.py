from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from dan.models.graph import Graph


REPRESENTATIVE_GRAPHS = [
    "graphs/three_step_chain.json",
    "graphs/paper_writing.json",
    "graphs/batch_paper_writing.json",
    "graphs/vibe_research_multi_dept.json",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _collect_signature(graph_dict: dict) -> dict:
    node_types: Counter[str] = Counter()
    subgraph_keys: list[str] = []

    def visit(g: dict) -> None:
        for node in g.get("nodes", []):
            node_types[str(node.get("node_type", ""))] += 1
        for key, sub in g.get("sub_graphs", {}).items():
            subgraph_keys.append(str(key))
            if isinstance(sub, dict):
                visit(sub)

    visit(graph_dict)
    return {
        "version": graph_dict.get("version"),
        "top_node_count": len(graph_dict.get("nodes", [])),
        "top_edge_count": len(graph_dict.get("edges", [])),
        "top_entry_points": tuple(graph_dict.get("entry_points", [])),
        "top_exit_points": tuple(graph_dict.get("exit_points", [])),
        "all_node_types": node_types,
        "all_subgraph_keys": tuple(sorted(subgraph_keys)),
    }


def test_representative_graph_corpus_round_trips_through_graph_model() -> None:
    repo = _repo_root()

    for rel_path in REPRESENTATIVE_GRAPHS:
        raw = json.loads((repo / rel_path).read_text(encoding="utf-8"))
        graph = Graph.model_validate(raw)
        dumped = graph.model_dump(mode="json")

        assert _collect_signature(dumped) == _collect_signature(raw), rel_path
