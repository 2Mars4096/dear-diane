from __future__ import annotations

import json
from pathlib import Path


def test_pre_dan_graph_v1_three_step_chain_fixture_preserves_old_shape() -> None:
    fixture = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "migration"
        / "pre_dan_graph_v1_three_step_chain.json"
    )

    data = json.loads(fixture.read_text(encoding="utf-8"))

    assert data["version"] == "1.0.0"
    assert "metadata" in data
    assert "name" in data
    assert "description" in data
    assert "nodes" in data and isinstance(data["nodes"], list)
    assert "edges" in data and isinstance(data["edges"], list)

    first_node = data["nodes"][0]
    assert "type" in first_node
    assert "node_type" not in first_node

    first_edge = data["edges"][0]
    assert "from" in first_edge and "to" in first_edge
    assert "source_node_id" not in first_edge
