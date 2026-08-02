from __future__ import annotations

import json
from pathlib import Path

import pytest

from dan.builder.decompiler import decompile
from dan.models.graph import Graph


REPRESENTATIVE_GRAPHS = [
    "graphs/three_step_chain.json",
    "graphs/paper_writing.json",
    "graphs/batch_paper_writing.json",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _normalize(value: object) -> object:
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _dump_list(items: list[object]) -> list[object]:
    dumped: list[object] = []
    for item in items:
        if hasattr(item, "model_dump"):
            dumped.append(getattr(item, "model_dump")(mode="json"))  # type: ignore[misc]
        else:
            dumped.append(item)
    return dumped


def _edge_signature(edge: object) -> str:
    payload = edge.model_dump(mode="json")
    payload.pop("id", None)
    return json.dumps(_normalize(payload), sort_keys=True)


def _find_subgraph(graph: Graph, key: str) -> Graph | None:
    if key in graph.sub_graphs:
        return graph.sub_graphs[key]
    for sub_graph in graph.sub_graphs.values():
        found = _find_subgraph(sub_graph, key)
        if found is not None:
            return found
    return None


def _assert_graph_equivalent(
    original: Graph,
    rebuilt: Graph,
    *,
    root_original: Graph | None = None,
    root_rebuilt: Graph | None = None,
    nested: bool = False,
    context: str = "root",
) -> None:
    """Compare graphs structurally while ignoring generated nested scope names."""

    root_original = root_original or original
    root_rebuilt = root_rebuilt or rebuilt

    assert original.version == rebuilt.version, context
    if not nested:
        assert original.metadata.model_dump(mode="json") == rebuilt.metadata.model_dump(
            mode="json"
        ), context

    assert original.entry_points == rebuilt.entry_points, context
    assert original.exit_points == rebuilt.exit_points, context
    assert _normalize(_dump_list(original.shared_context)) == _normalize(
        _dump_list(rebuilt.shared_context)
    ), context
    assert _normalize(_dump_list(original.artifact_refs)) == _normalize(
        _dump_list(rebuilt.artifact_refs)
    ), context
    assert _normalize(_dump_list(original.hyperedges)) == _normalize(
        _dump_list(rebuilt.hyperedges)
    ), context

    original_nodes = {node.id: node for node in original.nodes}
    rebuilt_nodes = {node.id: node for node in rebuilt.nodes}
    assert original_nodes.keys() == rebuilt_nodes.keys(), context

    for node_id in sorted(original_nodes):
        left = original_nodes[node_id].model_dump(mode="json")
        right = rebuilt_nodes[node_id].model_dump(mode="json")
        left_body = left.pop("body_graph", None)
        right_body = right.pop("body_graph", None)
        assert _normalize(left) == _normalize(right), f"{context}.node[{node_id}]"

        if left_body is not None or right_body is not None:
            assert left_body is not None and right_body is not None, (
                f"{context}.node[{node_id}].body_graph"
            )
            left_sub = _find_subgraph(root_original, left_body)
            right_sub = _find_subgraph(root_rebuilt, right_body)
            assert left_sub is not None, f"{context}.node[{node_id}].body_graph.lookup"
            assert right_sub is not None, f"{context}.node[{node_id}].body_graph.lookup"
            _assert_graph_equivalent(
                left_sub,
                right_sub,
                root_original=root_original,
                root_rebuilt=root_rebuilt,
                nested=True,
                context=f"{context}.node[{node_id}].body_graph",
            )

    assert sorted(_edge_signature(edge) for edge in original.edges) == sorted(
        _edge_signature(edge) for edge in rebuilt.edges
    ), context


@pytest.mark.parametrize("rel_path", REPRESENTATIVE_GRAPHS, ids=lambda p: Path(p).stem)
def test_representative_graph_corpus_round_trips_through_builder_dsl(rel_path: str) -> None:
    repo = _repo_root()
    original = Graph.model_validate(
        json.loads((repo / rel_path).read_text(encoding="utf-8"))
    )

    namespace: dict[str, object] = {}
    exec(decompile(original), namespace)  # noqa: S102
    rebuilt = namespace["graph"]

    assert isinstance(rebuilt, Graph)
    _assert_graph_equivalent(original, rebuilt)
