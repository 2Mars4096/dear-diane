from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dan.builder import workflow
from dan.builder.compiler import BuildError
from dan.builder.decompiler import decompile
from dan.meta.planner import validate_codegen_output
from dan.models.graph import Graph


REFERENCE_GRAPHS = [
    pytest.param("graphs/three_step_chain.json", id="three_step_chain"),
    pytest.param("graphs/build-probe-simple-a.json", id="build_probe_simple_a"),
    pytest.param("graphs/paper_writing.json", id="paper_writing"),
    pytest.param("graphs/a8c217118e87.json", id="drb2_deep_research_evaluator"),
    pytest.param(
        "graphs/batch_paper_writing.json",
        id="batch_paper_writing",
        marks=pytest.mark.xfail(
            reason=(
                "Shared imported-workflow body_graphs are duplicated during "
                "builder roundtrip by src/dan/builder/decompiler.py::_emit_subgraph_node"
            ),
            strict=True,
        ),
    ),
    pytest.param(
        "graphs/vibe_research_multi_dept.json",
        id="vibe_research_multi_dept",
        marks=pytest.mark.xfail(
            reason=(
                "Nested subgraph schema mismatches slip past "
                "src/dan/validation/graph.py::validate_graph and only surface "
                "during builder recompile"
            ),
            raises=BuildError,
            strict=True,
        ),
    ),
]

RUN_READY_GRAPH_FIXTURES = [
    pytest.param("graphs/three_step_chain.json", id="three_step_chain"),
    pytest.param("graphs/build-probe-simple-a.json", id="build_probe_simple_a"),
    pytest.param("graphs/paper_writing.json", id="paper_writing"),
    pytest.param("graphs/a8c217118e87.json", id="drb2_deep_research_evaluator"),
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _prune_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _prune_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_prune_none(item) for item in value]
    return value


def _resolve_subgraph(root: Graph, key: str) -> Graph | None:
    if key in root.sub_graphs:
        return root.sub_graphs[key]
    for subgraph in root.sub_graphs.values():
        found = _resolve_subgraph(subgraph, key)
        if found is not None:
            return found
    return None


def _normalize_edge(edge: Any) -> dict[str, Any]:
    payload = edge.model_dump(mode="json")
    for key in ("id", "ui", "metadata"):
        payload.pop(key, None)
    return _prune_none(payload)


def _node_signature(node: Any, root: Graph) -> dict[str, Any]:
    payload = node.model_dump(mode="json")
    body_graph_key = payload.pop("body_graph", None)
    for key in ("position", "ui", "metadata"):
        payload.pop(key, None)
    signature = _prune_none(payload)
    if body_graph_key:
        subgraph = _resolve_subgraph(root, body_graph_key)
        assert subgraph is not None
        signature["body_graph"] = _graph_signature(subgraph, root=root)
    return signature


def _graph_signature(graph: Graph, *, root: Graph | None = None) -> dict[str, Any]:
    root_graph = root or graph
    metadata = _prune_none(graph.metadata.model_dump(mode="json"))
    return {
        "metadata": metadata,
        "entry_points": sorted(graph.entry_points),
        "exit_points": sorted(graph.exit_points),
        "shared_context": sorted(
            (_prune_none(item.model_dump(mode="json")) for item in graph.shared_context),
            key=lambda item: item.get("key", ""),
        ),
        "artifact_refs": sorted(
            (_prune_none(item.model_dump(mode="json")) for item in graph.artifact_refs),
            key=lambda item: (item.get("name", ""), item.get("path", "")),
        ),
        "hyperedges": sorted(
            (_prune_none(item.model_dump(mode="json")) for item in graph.hyperedges),
            key=lambda item: (item.get("name", ""), item.get("hook", "")),
        ),
        "nodes": [
            _node_signature(node, root_graph)
            for node in sorted(graph.nodes, key=lambda item: item.id)
        ],
        "edges": sorted(
            (_normalize_edge(edge) for edge in graph.edges),
            key=lambda item: (
                item.get("source_node_id", ""),
                item.get("source_port", ""),
                item.get("target_node_id", ""),
                item.get("target_port", ""),
                item.get("edge_type", ""),
            ),
        ),
    }


def _load_graph(rel_path: str) -> Graph:
    raw = json.loads((_repo_root() / rel_path).read_text(encoding="utf-8"))
    return Graph.model_validate(raw)


def _roundtrip_through_builder(graph: Graph) -> Graph:
    namespace: dict[str, Any] = {}
    exec(decompile(graph), namespace)  # noqa: S102
    rebuilt = namespace["graph"]
    assert isinstance(rebuilt, Graph)
    return rebuilt


def _build_conditional_branch_graph() -> Graph:
    wf = workflow("conditional_branch_roundtrip")
    _gate_ref, then_ref, else_ref = wf.branch(
        condition="1 > 0",
        then_prompt="Handle the true case.",
        else_prompt="Handle the false case.",
        name="check",
    )
    merge = wf.llm("merge", prompt="Combine the branch result.")
    then_ref >> merge
    else_ref >> merge
    return wf.build()


def _assert_run_ready(graph: Graph, *, context: str) -> None:
    validation = validate_codegen_output(graph.model_dump(mode="json"))
    assert validation.success is True, context
    assert validation.run_ready is True, context


@pytest.mark.parametrize("rel_path", REFERENCE_GRAPHS)
def test_reference_graphs_roundtrip_through_builder_decompile_and_recompile(
    rel_path: str,
) -> None:
    graph = _load_graph(rel_path)
    rebuilt = _roundtrip_through_builder(graph)

    assert _graph_signature(rebuilt) == _graph_signature(graph)


@pytest.mark.parametrize("rel_path", RUN_READY_GRAPH_FIXTURES)
def test_run_ready_reference_graphs_stay_run_ready_after_builder_roundtrip(
    rel_path: str,
) -> None:
    graph = _load_graph(rel_path)
    rebuilt = _roundtrip_through_builder(graph)

    _assert_run_ready(graph, context=f"{rel_path}.original")
    _assert_run_ready(rebuilt, context=f"{rel_path}.rebuilt")


def test_conditional_branch_graph_roundtrips_through_builder_with_equivalence_and_run_readiness() -> None:
    graph = _build_conditional_branch_graph()
    rebuilt = _roundtrip_through_builder(graph)

    assert _graph_signature(rebuilt) == _graph_signature(graph)
    _assert_run_ready(graph, context="conditional_branch.original")
    _assert_run_ready(rebuilt, context="conditional_branch.rebuilt")
