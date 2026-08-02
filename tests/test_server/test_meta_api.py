"""Integration tests for /api/experiences/* and /api/meta/* endpoints.

These tests use the ASGI test client with the real FastAPI app, but do
not require an external LLM provider — they exercise the CRUD and
lifecycle endpoints that work against in-process stores.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DAN_GRAPHS_DIR", tempfile.mkdtemp())
os.environ.setdefault("DAN_CHECKPOINT_DIR", tempfile.mkdtemp())

import dan.server.audit as audit_module  # noqa: E402
from dan.builder import workflow  # noqa: E402
from dan.server.app import app, lifespan  # noqa: E402
from dan.server.audit import ChatAuditRecord, ChatAuditStore, ToolCallRecord  # noqa: E402
from dan.server.graph_store import GraphStore  # noqa: E402
from dan.server.routers import experiences as experiences_module  # noqa: E402


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


# ------------------------------------------------------------------
# Experience CRUD endpoints
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_experiences_empty(client: AsyncClient):
    resp = await client.get("/api/experiences")
    assert resp.status_code == 200
    body = resp.json()
    assert "experiences" in body
    assert isinstance(body["experiences"], list)


@pytest.mark.asyncio
async def test_get_experience_not_found(client: AsyncClient):
    resp = await client.get("/api/experiences/nonexistent-wf")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_experience_not_found(client: AsyncClient):
    resp = await client.delete("/api/experiences/nonexistent-wf")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_refresh_experience_missing_graph(client: AsyncClient):
    resp = await client.post("/api/experiences/no-graph/refresh")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_refresh_experience_creates_and_returns(client: AsyncClient):
    """Create a graph, then refresh its experience — should succeed."""
    graph_data = {
        "version": "dan_graph_v1",
        "metadata": {"name": "Exp Test", "description": "test"},
        "nodes": [
            {"id": "n1", "name": "Writer", "node_type": "llm_operator",
             "model": "gpt-4", "prompt_template": "Write something"},
        ],
        "edges": [],
        "entry_points": ["n1"],
        "exit_points": ["n1"],
        "sub_graphs": {},
        "shared_context": [],
        "artifact_refs": [],
    }
    create_resp = await client.post("/api/graphs", json={"graph_id": "exp-test"})
    assert create_resp.status_code == 200
    await client.put("/api/graphs/exp-test", json=graph_data)

    resp = await client.post("/api/experiences/exp-test/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "refreshed"
    assert body["experience"]["workflow_id"] == "exp-test"
    assert body["experience"]["name"].startswith("Exp Test")

    get_resp = await client.get("/api/experiences/exp-test")
    assert get_resp.status_code == 200
    assert get_resp.json()["workflow_id"] == "exp-test"


@pytest.mark.asyncio
async def test_experience_lifecycle(client: AsyncClient):
    """Refresh → list → get → delete → verify deleted."""
    graph_data = {
        "version": "dan_graph_v1",
        "metadata": {"name": "Lifecycle Test"},
        "nodes": [
            {"id": "n1", "name": "Step", "node_type": "llm_operator",
             "model": "gpt-4", "prompt_template": "Do"},
        ],
        "edges": [],
        "entry_points": ["n1"],
        "exit_points": ["n1"],
        "sub_graphs": {},
        "shared_context": [],
        "artifact_refs": [],
    }
    await client.post("/api/graphs", json={"graph_id": "lifecycle-wf"})
    await client.put("/api/graphs/lifecycle-wf", json=graph_data)

    await client.post("/api/experiences/lifecycle-wf/refresh")

    list_resp = await client.get("/api/experiences")
    assert list_resp.status_code == 200
    wf_ids = [e["workflow_id"] for e in list_resp.json()["experiences"]]
    assert "lifecycle-wf" in wf_ids

    get_resp = await client.get("/api/experiences/lifecycle-wf")
    assert get_resp.status_code == 200

    del_resp = await client.delete("/api/experiences/lifecycle-wf")
    assert del_resp.status_code == 200

    get_resp2 = await client.get("/api/experiences/lifecycle-wf")
    assert get_resp2.status_code == 404


@pytest.mark.asyncio
async def test_search_experiences_requires_query(client: AsyncClient):
    resp = await client.post("/api/experiences/search", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_trace_draft_endpoint_compiles_run_ready_draft(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def _patched_init(self, base_dir=None):
        self._base = Path(base_dir or tmp_path)
        self._base.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ChatAuditStore, "__init__", _patched_init)

    store = ChatAuditStore()
    store.append(
        ChatAuditRecord(
            surface_id="chat-surface",
            turn_id="trace-turn-1",
            user_message="Search for DAN architecture notes and save a summary to /tmp/summary.md",
            tool_calls=[
                ToolCallRecord(
                    tool_name="search_web",
                    args={"query": "DAN architecture notes"},
                    result_summary="results",
                ),
                ToolCallRecord(
                    tool_name="save_file",
                    args={"path": "/tmp/summary.md"},
                    result_summary="saved",
                ),
            ],
        )
    )

    resp = await client.post(
        "/api/experiences/trace-draft",
        json={"turn_id": "trace-turn-1", "compile": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "run_ready_draft"
    assert body["draft"]["source_turn_id"] == "trace-turn-1"
    assert body["compiled"]["validated"] is True
    assert body["compiled"]["run_ready"] is True
    assert body["compiled"]["run_readiness_issues"] == []


@pytest.mark.asyncio
async def test_trace_draft_endpoint_compiles_code_execution_trace(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def _patched_init(self, base_dir=None):
        self._base = Path(base_dir or tmp_path)
        self._base.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ChatAuditStore, "__init__", _patched_init)

    store = ChatAuditStore()
    store.append(
        ChatAuditRecord(
            surface_id="chat-surface",
            turn_id="trace-turn-code-1",
            user_message="Load a CSV, transform it with Python, then save the final report.",
            tool_calls=[
                ToolCallRecord(
                    tool_name="run_python",
                    args={
                        "job": {
                            "input_file": "./input/data.csv",
                            "output_file": "./build/intermediate.json",
                        }
                    },
                    result_summary="transformed",
                ),
                ToolCallRecord(
                    tool_name="save_file",
                    args={"path": "./reports/final.md"},
                    result_summary="saved",
                ),
            ],
        )
    )

    resp = await client.post(
        "/api/experiences/trace-draft",
        json={"turn_id": "trace-turn-code-1", "compile": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "run_ready_draft"
    assert body["compiled"]["validated"] is True
    assert body["compiled"]["run_ready"] is True
    assert body["compiled"]["run_readiness_issues"] == []
    assert body["compiled"]["graph"] is not None


@pytest.mark.asyncio
async def test_graph_validate_endpoint_surfaces_code_specific_failure_mode(
    client: AsyncClient,
):
    wf = workflow("placeholder_contract")
    wf.code(
        "compute",
        code='result = {"status": "placeholder", "task": "compute metrics"}',
    )
    graph_data = wf.build().model_dump(mode="json")

    create_resp = await client.post("/api/graphs", json={"graph_id": "validate-placeholder"})
    assert create_resp.status_code == 200

    save_resp = await client.put("/api/graphs/validate-placeholder", json=graph_data)
    assert save_resp.status_code == 200

    resp = await client.post("/api/graphs/validate-placeholder/validate")
    assert resp.status_code == 200
    body = resp.json()

    assert body["run_ready"] is False
    assert body["run_readiness_failure_mode"] == "non_runnable_code"
    assert any("placeholder" in issue.lower() for issue in body["run_readiness_issues"])


@pytest.mark.asyncio
async def test_trace_draft_endpoint_returns_no_draft_for_thin_trace(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def _patched_init(self, base_dir=None):
        self._base = Path(base_dir or tmp_path)
        self._base.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ChatAuditStore, "__init__", _patched_init)

    store = ChatAuditStore()
    store.append(
        ChatAuditRecord(
            surface_id="chat-surface",
            turn_id="trace-turn-2",
            user_message="Read this file",
            tool_calls=[
                ToolCallRecord(
                    tool_name="read_file",
                    args={"path": "/tmp/input.txt"},
                    result_summary="loaded",
                ),
            ],
        )
    )

    resp = await client.post(
        "/api/experiences/trace-draft",
        json={"turn_id": "trace-turn-2", "compile": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "no_draft"
    assert "draft" not in body


def _research_write_record() -> ChatAuditRecord:
    return ChatAuditRecord(
        surface_id="chat",
        turn_id="turn-promote-1",
        workflow_id="seed-workflow",
        run_id="run-promote-1",
        user_message="Research climate adaptation grants and write a markdown summary to /tmp/grants.md",
        tool_calls=[
            ToolCallRecord(
                tool_name="search_web",
                args={"query": "climate adaptation grants 2026"},
            ),
            ToolCallRecord(
                tool_name="write_file",
                args={
                    "path": "/tmp/grants.md",
                    "content": "# grants\n- item",
                },
            ),
        ],
    )


class _FakeAuditStore:
    def __init__(self, record: ChatAuditRecord) -> None:
        self._record = record

    def load_by_turn(self, turn_id: str) -> ChatAuditRecord | None:
        if turn_id == self._record.turn_id:
            return self._record
        return None


def _patch_audit_store(monkeypatch: pytest.MonkeyPatch, record: ChatAuditRecord) -> None:
    monkeypatch.setattr(audit_module, "ChatAuditStore", lambda: _FakeAuditStore(record))


@pytest.mark.asyncio
async def test_promote_trace_draft_persists_run_ready_workflow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record = _research_write_record()
    store = GraphStore(str(tmp_path / "graphs"))
    _patch_audit_store(monkeypatch, record)
    monkeypatch.setattr(experiences_module, "get_graph_store", lambda: store)

    result = await experiences_module.promote_trace_draft({"turn_id": record.turn_id})

    assert result["status"] == "promoted_workflow"
    assert result["workflow_id"] == "distilled-research-climate-adaptation-grants-write-markdown"
    assert result["compiled"]["validated"] is True
    assert result["compiled"]["run_ready"] is True
    assert result["promotion"]["saved"] is True
    saved = store.get_graph(result["workflow_id"])
    assert saved is not None
    assert saved["metadata"]["name"] == (
        "Research climate adaptation grants and write a markdown summary to {path}"
    )
    assert "trace-promoted" in saved["metadata"]["tags"]
    assert "turn-promote-1" in saved["metadata"]["description"]
    assert result["compiled"]["graph"]["metadata"]["name"] == saved["metadata"]["name"]


@pytest.mark.asyncio
async def test_promote_trace_draft_blocks_non_run_ready_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record = _research_write_record()
    store = GraphStore(str(tmp_path / "graphs"))
    _patch_audit_store(monkeypatch, record)
    monkeypatch.setattr(experiences_module, "get_graph_store", lambda: store)
    monkeypatch.setattr(
        experiences_module,
        "_compile_trace_draft",
        lambda draft, workflow_id=None: experiences_module._TraceDraftBuildResult(
            response={
                "status": "validated_draft",
                "draft": draft.model_dump(mode="json"),
                "promotion": {},
                "compiled": {
                    "validated": True,
                    "run_ready": False,
                    "graph": {"version": "dan_graph_v1"},
                    "warnings": [],
                    "errors": [],
                    "auto_fixes_applied": [],
                    "run_readiness_issues": ["Missing runnable semantics."],
                },
            },
            contract_report=SimpleNamespace(run_ready=False, graph_dict={"version": "dan_graph_v1"}),
        ),
    )

    result = await experiences_module.promote_trace_draft({"turn_id": record.turn_id})

    assert result["status"] == "promotion_blocked"
    assert result["promotion"]["saved"] is False
    assert result["promotion"]["reason"] == (
        "Only run-ready distilled workflows can be promoted into saved workflow artifacts."
    )
    assert result["compiled"]["validated"] is True
    assert result["compiled"]["run_ready"] is False
    assert store.list_graphs() == []


@pytest.mark.asyncio
async def test_promote_trace_draft_rejects_existing_explicit_workflow_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    record = _research_write_record()
    store = GraphStore(str(tmp_path / "graphs"))
    _patch_audit_store(monkeypatch, record)
    monkeypatch.setattr(experiences_module, "get_graph_store", lambda: store)

    workflow_id = "distilled-climate-grants"
    saved = await experiences_module.promote_trace_draft({
        "turn_id": record.turn_id,
        "workflow_id": workflow_id,
    })
    assert saved["workflow_id"] == workflow_id

    with pytest.raises(HTTPException) as exc_info:
        await experiences_module.promote_trace_draft({
            "turn_id": record.turn_id,
            "workflow_id": workflow_id,
        })

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == f"Graph '{workflow_id}' already exists"


# ------------------------------------------------------------------
# Meta-orchestrator session endpoints
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_meta_sessions_empty(client: AsyncClient):
    resp = await client.get("/api/meta/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert "sessions" in body
    assert isinstance(body["sessions"], list)


@pytest.mark.asyncio
async def test_get_meta_session_not_found(client: AsyncClient):
    resp = await client.get("/api/meta/sessions/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_meta_session_events_not_found(client: AsyncClient):
    resp = await client.get("/api/meta/sessions/nonexistent/events")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pause_meta_session_not_found(client: AsyncClient):
    resp = await client.post("/api/meta/sessions/nonexistent/pause")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_meta_session_not_found(client: AsyncClient):
    resp = await client.delete("/api/meta/sessions/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_meta_run_requires_goal(client: AsyncClient):
    resp = await client.post("/api/meta/run", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_meta_plan_requires_goal(client: AsyncClient):
    resp = await client.post("/api/meta/plan", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_meta_discover_endpoint(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    async def _discover_workflows(self, goal: str, top_k: int = 5):
        return []

    monkeypatch.setattr(
        "dan.meta.discovery.DiscoveryService.discover_workflows",
        _discover_workflows,
    )

    resp = await client.get("/api/meta/discover")
    assert resp.status_code == 200
    body = resp.json()
    assert "tools" in body
    assert "patterns" in body
    assert "skills" in body
    assert "workflows" in body
    assert isinstance(body["patterns"], list)


@pytest.mark.asyncio
async def test_meta_validate_plan_rejects_unknown_action(client: AsyncClient):
    resp = await client.post("/api/meta/validate-plan", json={"action": "INVALID"})
    assert resp.status_code == 422
