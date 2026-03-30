from __future__ import annotations

from types import SimpleNamespace

import pytest

from dan.models.graph import Graph
from dan.server.capabilities.introspection import handle_inspect_node
from dan.server.capability_registry import CapabilityContext


@pytest.mark.asyncio
async def test_handle_inspect_node_serializes_typed_code_operator() -> None:
    graph = Graph.model_validate({
        "version": "dan_graph_v1",
        "nodes": [
            {
                "id": "read_watchlist_csv",
                "node_type": "code_operator",
                "name": "Read Watchlist CSV",
                "code": "result = {'rows': []}",
                "language": "python",
                "input_ports": [
                    {"name": "csv_path", "json_schema": {"type": "string"}, "required": True},
                ],
                "output_ports": [
                    {"name": "rows", "json_schema": {"type": "array"}},
                ],
            }
        ],
        "edges": [],
        "entry_points": ["read_watchlist_csv"],
        "exit_points": ["read_watchlist_csv"],
    })

    ctx = CapabilityContext(
        workflow_id="_scratch",
        graph_store=SimpleNamespace(load_as_model=lambda workflow_id: graph),
    )

    result = await handle_inspect_node(
        {"workflow_id": "_scratch", "node_id": "read_watchlist_csv"},
        ctx,
    )

    assert result.success is True
    assert result.data["node_id"] == "read_watchlist_csv"
    assert result.data["type"] == "code_operator"
    assert result.data["node_type"] == "code_operator"
    assert result.data["config"]["code"] == "result = {'rows': []}"
    assert result.data["config"]["language"] == "python"
    assert result.data["input_ports"][0]["name"] == "csv_path"
    assert result.data["output_ports"][0]["name"] == "rows"
    assert result.data["upstream_variables"][0]["variable_name"] == "csv_path"
    assert result.data["upstream_variables"][0]["connected"] is False
