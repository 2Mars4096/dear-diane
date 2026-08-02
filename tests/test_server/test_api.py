"""Integration tests for the FastAPI server — graph CRUD, runs, and WebSocket."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from dan.builder import workflow

os.environ["DAN_GRAPHS_DIR"] = tempfile.mkdtemp()
os.environ["DAN_CHECKPOINT_DIR"] = tempfile.mkdtemp()

from dan.server.app import app, lifespan  # noqa: E402


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


def _run_ready_graph_dict(name: str) -> dict[str, Any]:
    wf = workflow(name)
    wf.code("compute", code="result = 'ok'")
    return wf.build().model_dump(mode="json")


# ------------------------------------------------------------------
# Graph CRUD
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_graphs_empty(client: AsyncClient):
    resp = await client.get("/api/graphs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["graphs"] == []
    assert body["last_opened"] is None


@pytest.mark.asyncio
async def test_create_and_get_graph(client: AsyncClient):
    resp = await client.post("/api/graphs", json={"graph_id": "test-graph"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["graph_id"] == "test-graph"
    assert data["data"]["version"] == "dan_graph_v1"

    resp = await client.get("/api/graphs/test-graph")
    assert resp.status_code == 200
    assert resp.json()["graph_id"] == "test-graph"


@pytest.mark.asyncio
async def test_create_duplicate_graph(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "dup"})
    resp = await client.post("/api/graphs", json={"graph_id": "dup"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_graph(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "upd"})
    graph_data = {
        "version": "dan_graph_v1",
        "metadata": {"name": "upd", "description": "updated"},
        "nodes": [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "shared_context": [],
        "artifact_refs": [],
    }
    resp = await client.put("/api/graphs/upd", json=graph_data)
    assert resp.status_code == 200
    assert resp.json()["status"] == "saved"

    resp = await client.get("/api/graphs/upd")
    assert resp.json()["data"]["metadata"]["description"] == "updated"


@pytest.mark.asyncio
async def test_save_graph_as_promotes_graph_to_named_workflow(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "_scratch"})
    graph_data = {
        "version": "dan_graph_v1",
        "metadata": {"name": "code_gen_validation", "description": "working draft"},
        "nodes": [],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "shared_context": [],
        "artifact_refs": [],
    }
    await client.put("/api/graphs/_scratch", json=graph_data)

    resp = await client.post(
        "/api/graphs/_scratch/save-as",
        json={"new_name": "Daily Equity Watchlist"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["graph_id"] == "daily-equity-watchlist"
    assert body["source_graph_id"] == "_scratch"
    assert body["data"]["metadata"]["name"] == "Daily Equity Watchlist"

    get_resp = await client.get("/api/graphs/daily-equity-watchlist")
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["metadata"]["name"] == "Daily Equity Watchlist"


@pytest.mark.asyncio
async def test_save_graph_as_dedupes_name_based_graph_id(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "_scratch"})
    await client.post("/api/graphs", json={"graph_id": "daily-equity-watchlist"})

    resp = await client.post(
        "/api/graphs/_scratch/save-as",
        json={"new_name": "Daily Equity Watchlist"},
    )
    assert resp.status_code == 200
    assert resp.json()["graph_id"] == "daily-equity-watchlist-2"


@pytest.mark.asyncio
async def test_update_graph_invalid_returns_400(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "upd-bad"})
    graph_data = {
        "version": "dan_graph_v1",
        "metadata": {"name": "upd-bad"},
        "nodes": [{"id": "n1", "name": "No tool", "node_type": "tool_operator"}],
        "edges": [],
        "sub_graphs": {},
        "entry_points": [],
        "exit_points": [],
        "shared_context": [],
        "artifact_refs": [],
        "hyperedges": [],
    }
    resp = await client.put("/api/graphs/upd-bad", json=graph_data)
    assert resp.status_code == 400
    assert "validation failed" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_graph(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "del"})
    resp = await client.delete("/api/graphs/del")
    assert resp.status_code == 200

    resp = await client.get("/api/graphs/del")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent(client: AsyncClient):
    resp = await client.delete("/api/graphs/nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_nonexistent(client: AsyncClient):
    resp = await client.get("/api/graphs/nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_graphs_after_create(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "g1"})
    await client.post("/api/graphs", json={"graph_id": "g2"})
    resp = await client.get("/api/graphs")
    ids = [g["graph_id"] for g in resp.json()["graphs"]]
    assert "g1" in ids
    assert "g2" in ids


@pytest.mark.asyncio
async def test_last_opened_tracked(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "lo"})
    await client.get("/api/graphs/lo")
    resp = await client.get("/api/graphs")
    assert resp.json()["last_opened"] == "lo"


@pytest.mark.asyncio
async def test_apply_mutation_success(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "mut-apply"})
    base = {
        "version": "dan_graph_v1",
        "metadata": {"name": "mut-apply"},
        "nodes": [
            {
                "id": "n1",
                "node_type": "llm_operator",
                "name": "A",
                "input_ports": [{"name": "input", "schema": {}}],
                "output_ports": [{"name": "text", "schema": {}}],
                "position": {"x": 0, "y": 0},
                "ui": {},
                "metadata": {},
                "model": "claude-sonnet-4-6",
                "prompt_template": "Hi",
            },
        ],
        "edges": [],
        "sub_graphs": {},
        "entry_points": ["n1"],
        "exit_points": ["n1"],
        "shared_context": [],
        "artifact_refs": [],
    }
    await client.put("/api/graphs/mut-apply", json=base)

    plan = {
        "operations": [
            {
                "op": "add_node",
                "node_type": "llm_operator",
                "name": "B",
                "config": {
                    "model": "claude-sonnet-4-6",
                    "prompt_template": "World",
                    "input_ports": [{"name": "input", "schema": {}}],
                    "output_ports": [{"name": "text", "schema": {}}],
                },
            },
            {
                "op": "add_edge",
                "edge_type": "data",
                "source_id": "n1",
                "source_port": "text",
                "target_id": "b",
                "target_port": "input",
            },
        ],
        "description": "Add B after A",
    }
    resp = await client.post(
        "/api/graphs/mut-apply/apply-mutation",
        json={"mutation_plan": plan},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["new_graph"] is not None
    nodes = body["new_graph"]["nodes"]
    assert len(nodes) == 2
    names = {n["name"] for n in nodes}
    assert "A" in names
    assert "B" in names


@pytest.mark.asyncio
async def test_apply_mutation_returns_diagnostics_on_auto_create_port(client: AsyncClient):
    """When add_edge targets a non-existent port, auto-create adds a diagnostic."""
    await client.post("/api/graphs", json={"graph_id": "mut-diag"})
    base = {
        "version": "dan_graph_v1",
        "metadata": {"name": "mut-diag"},
        "nodes": [
            {
                "id": "n1",
                "node_type": "llm_operator",
                "name": "A",
                "input_ports": [{"name": "input", "schema": {}}],
                "output_ports": [{"name": "text", "schema": {}}],
                "position": {"x": 0, "y": 0},
                "ui": {},
                "metadata": {},
                "model": "claude-sonnet-4-6",
                "prompt_template": "Hi",
            },
            {
                "id": "n2",
                "node_type": "code_operator",
                "name": "B",
                "input_ports": [{"name": "input", "schema": {}, "required": False}],
                "output_ports": [{"name": "result", "schema": {}}],
                "position": {"x": 200, "y": 0},
                "ui": {},
                "metadata": {},
                "code": "pass",
                "language": "python",
                "sandbox_config": {},
            },
        ],
        "edges": [],
        "sub_graphs": {},
        "entry_points": ["n1", "n2"],
        "exit_points": ["n1", "n2"],
        "shared_context": [],
        "artifact_refs": [],
    }
    await client.put("/api/graphs/mut-diag", json=base)

    plan = {
        "operations": [
            {
                "op": "add_edge",
                "source_id": "n1",
                "source_port": "text",
                "target_id": "n2",
                "target_port": "typo_port",
            },
        ],
        "description": "Edge to non-existent port (triggers auto-create + diagnostic)",
    }
    resp = await client.post(
        "/api/graphs/mut-diag/apply-mutation",
        json={"mutation_plan": plan},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "diagnostics" in body
    assert len(body["diagnostics"]) == 1
    assert "typo_port" in body["diagnostics"][0]
    assert "n2" in body["diagnostics"][0]
    assert "Verify spelling" in body["diagnostics"][0]


@pytest.mark.asyncio
async def test_apply_mutation_nonexistent(client: AsyncClient):
    plan = {"operations": [], "description": "empty"}
    resp = await client.post(
        "/api/graphs/nope/apply-mutation",
        json={"mutation_plan": plan},
    )
    assert resp.status_code == 404


# ------------------------------------------------------------------
# Runs
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_run_on_empty_graph(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "run-test"})
    resp = await client.post("/api/runs", json={"graph_id": "run-test"})
    assert resp.status_code == 422
    assert "not run-ready" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_start_run_nonexistent_graph(client: AsyncClient):
    resp = await client.post("/api/runs", json={"graph_id": "nope"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_run(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "run-get"})
    await client.put("/api/graphs/run-get", json=_run_ready_graph_dict("run-get"))
    start = await client.post("/api/runs", json={"graph_id": "run-get"})
    run_id = start.json()["run_id"]

    await asyncio.sleep(0.1)

    resp = await client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == run_id


@pytest.mark.asyncio
async def test_list_runs(client: AsyncClient):
    await client.post("/api/graphs", json={"graph_id": "run-list"})
    await client.put("/api/graphs/run-list", json=_run_ready_graph_dict("run-list"))
    await client.post("/api/runs", json={"graph_id": "run-list"})
    resp = await client.get("/api/runs")
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) >= 1


@pytest.mark.asyncio
async def test_get_run_nonexistent(client: AsyncClient):
    resp = await client.get("/api/runs/nope")
    assert resp.status_code == 404
