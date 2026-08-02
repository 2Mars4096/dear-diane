"""Tests for the furnace API router."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from dan.engine.recipe.session_store import FurnaceSessionStore


@pytest.fixture
def furnace_store(tmp_path: Path) -> FurnaceSessionStore:
    return FurnaceSessionStore(base_dir=tmp_path)


@pytest.fixture
def artifacts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "artifacts"
    d.mkdir()
    return d


@pytest_asyncio.fixture(autouse=True)
async def _reset_furnace_router_state():
    from dan.server.routers import furnace as furnace_router_mod

    yield

    tasks = list(furnace_router_mod._session_tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    furnace_router_mod._session_tasks.clear()
    furnace_router_mod._session_cancel_events.clear()
    furnace_router_mod._session_progress.clear()
    furnace_router_mod._session_locks.clear()


@pytest.fixture
def furnace_app(furnace_store: FurnaceSessionStore, artifacts_dir: Path):
    from fastapi import FastAPI
    from dan.server.routers.furnace import router as furnace_router

    app = FastAPI()
    app.include_router(furnace_router)

    with patch("dan.server.routers.furnace.get_furnace_session_store", return_value=furnace_store):
        with patch("dan.server.routers.furnace.is_furnace_enabled", return_value=True):
            with patch("dan.server.routers.furnace.ARTIFACTS_ROOT", artifacts_dir):
                yield app


@pytest_asyncio.fixture
async def client(furnace_app):
    async with httpx.AsyncClient(
        transport=ASGITransport(app=furnace_app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_session(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Test Session", "topic": "Supply Chain"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "session" in data
    assert data["session"]["name"] == "Test Session"
    assert data["session"]["topic"] == "Supply Chain"
    assert data["session"]["status"] == "paused"
    assert "session_id" in data["session"]
    assert "artifact_dir" in data


@pytest.mark.asyncio
async def test_list_sessions(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/furnace/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert isinstance(data["sessions"], list)

    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "S1", "topic": "T1"},
    )
    assert create_resp.status_code == 200
    resp2 = await client.get("/api/furnace/sessions")
    assert resp2.status_code == 200
    assert len(resp2.json()["sessions"]) >= 1
    assert "tags" in resp2.json()["sessions"][0]


@pytest.mark.asyncio
async def test_get_session(client: httpx.AsyncClient) -> None:
    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Get Test", "topic": "T"},
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["session"]["session_id"]

    resp = await client.get(f"/api/furnace/sessions/{session_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session"]["session_id"] == session_id
    assert data["session"]["name"] == "Get Test"


@pytest.mark.asyncio
async def test_get_session_not_found(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/furnace/sessions/nonexistent-id-xyz")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_sources(client: httpx.AsyncClient) -> None:
    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Add Sources", "topic": "T"},
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["session"]["session_id"]

    resp = await client.post(
        f"/api/furnace/sessions/{session_id}/sources",
        json={"source_ids": ["p1", "p2", "p3"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["added"] == 3
    assert data["total_sources"] == 3


@pytest.mark.asyncio
async def test_update_session_tags(client: httpx.AsyncClient) -> None:
    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Tagged Session", "topic": "T"},
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["session"]["session_id"]

    update_resp = await client.post(
        f"/api/furnace/sessions/{session_id}/tags",
        json={"add": ["Baseline", " keep ", "baseline"]},
    )
    assert update_resp.status_code == 200
    payload = update_resp.json()
    assert payload["tags"] == ["baseline", "keep"]
    assert payload["session"]["tags"] == ["baseline", "keep"]

    remove_resp = await client.post(
        f"/api/furnace/sessions/{session_id}/tags",
        json={"remove": ["keep"]},
    )
    assert remove_resp.status_code == 200
    assert remove_resp.json()["tags"] == ["baseline"]

    detail_resp = await client.get(f"/api/furnace/sessions/{session_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["session"]["tags"] == ["baseline"]


@pytest.mark.asyncio
async def test_create_variant_inherits_parent_sources_and_lineage(
    client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    create_parent = await client.post(
        "/api/furnace/sessions",
        json={"name": "Parent Session", "topic": "Supply Chain"},
    )
    assert create_parent.status_code == 200
    parent_session = create_parent.json()["session"]
    parent_id = parent_session["session_id"]

    tag_parent = await client.post(
        f"/api/furnace/sessions/{parent_id}/tags",
        json={"tags": ["baseline", "favorite"]},
    )
    assert tag_parent.status_code == 200

    pdf_path = tmp_path / "paper-a.pdf"
    pdf_path.touch()
    add_sources = await client.post(
        f"/api/furnace/sessions/{parent_id}/sources",
        json={
            "source_ids": ["smith2024"],
            "pdf_paths": [str(pdf_path)],
            "urls": ["https://example.com/paper-b"],
        },
    )
    assert add_sources.status_code == 200

    parent_detail = await client.get(f"/api/furnace/sessions/{parent_id}")
    assert parent_detail.status_code == 200
    parent_session = parent_detail.json()["session"]

    create_variant = await client.post(
        "/api/furnace/sessions",
        json={
            "name": "Parent Session / variant-1",
            "topic": "Supply Chain",
            "parent_session_id": parent_id,
            "inherit_sources": True,
            "variant_label": "variant-1",
            "target_count": 25,
        },
    )
    assert create_variant.status_code == 200
    variant = create_variant.json()["session"]

    assert variant["corpus_id"] == parent_session["corpus_id"]
    assert variant["variant_label"] == "variant-1"
    assert variant["tags"] == ["baseline", "favorite"]
    assert set(variant["paper_queue"].keys()) == set(parent_session["paper_queue"].keys())
    assert variant["metadata"]["parent_session_id"] == parent_id
    assert variant["metadata"]["family_session_id"] == parent_id
    assert variant["metadata"]["forked_from_recipe_id"] == parent_session["recipe_id"]
    assert variant["metadata"]["pdf_paths"] == parent_session["metadata"]["pdf_paths"]
    assert variant["metadata"]["urls"] == parent_session["metadata"]["urls"]
    assert variant["metadata"]["source_types"] == parent_session["metadata"]["source_types"]
    assert variant["metadata"]["inherited_source_count"] == len(parent_session["paper_queue"])
    assert variant["metadata"]["target_count"] == 25

    listed = await client.get("/api/furnace/sessions")
    assert listed.status_code == 200
    summaries = {s["session_id"]: s for s in listed.json()["sessions"]}
    assert summaries[variant["session_id"]]["recipe_id"] == variant["recipe_id"]
    assert summaries[variant["session_id"]]["variant_label"] == "variant-1"
    assert summaries[variant["session_id"]]["parent_session_id"] == parent_id
    assert summaries[variant["session_id"]]["family_session_id"] == parent_id
    assert summaries[variant["session_id"]]["tags"] == ["baseline", "favorite"]


@pytest.mark.asyncio
async def test_create_variant_parent_not_found(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/furnace/sessions",
        json={
            "name": "Broken Variant",
            "topic": "Supply Chain",
            "parent_session_id": "missing-parent",
            "inherit_sources": True,
        },
    )
    assert resp.status_code == 404
    assert "Parent session" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_start_session(client: httpx.AsyncClient) -> None:
    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Start Test", "topic": "T"},
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["session"]["session_id"]
    await client.post(f"/api/furnace/sessions/{session_id}/pause")

    resp = await client.post(f"/api/furnace/sessions/{session_id}/start")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == session_id
    assert data["status"] == "starting"


@pytest.mark.asyncio
async def test_pause_session(client: httpx.AsyncClient) -> None:
    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Pause Test", "topic": "T"},
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["session"]["session_id"]

    resp = await client.post(f"/api/furnace/sessions/{session_id}/pause")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == session_id
    assert data["status"] == "paused"


@pytest.mark.asyncio
async def test_cancel_session(client: httpx.AsyncClient) -> None:
    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Cancel Test", "topic": "T"},
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["session"]["session_id"]

    resp = await client.post(f"/api/furnace/sessions/{session_id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == session_id
    assert data["status"] == "cancelled"


@pytest.mark.asyncio
async def test_delete_session(client: httpx.AsyncClient) -> None:
    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Delete Test", "topic": "T"},
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["session"]["session_id"]

    delete_resp = await client.delete(f"/api/furnace/sessions/{session_id}")
    assert delete_resp.status_code == 200
    data = delete_resp.json()
    assert data["session_id"] == session_id
    assert data["deleted"] is True

    get_resp = await client.get(f"/api/furnace/sessions/{session_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_add_sources_sanitizes_traversal_ids(client: httpx.AsyncClient) -> None:
    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Traversal Test", "topic": "T"},
    )
    session_id = create_resp.json()["session"]["session_id"]

    resp = await client.post(
        f"/api/furnace/sessions/{session_id}/sources",
        json={"source_ids": ["../../etc/passwd", "../escape"]},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["source_ids"] == ["passwd", "escape"]

    detail = await client.get(f"/api/furnace/sessions/{session_id}")
    queued_ids = list(detail.json()["session"]["paper_queue"].keys())
    assert queued_ids == ["passwd", "escape"]


@pytest.mark.asyncio
async def test_add_sources_avoids_pdf_and_url_collisions(
    client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Collision Test", "topic": "T"},
    )
    session_id = create_resp.json()["session"]["session_id"]

    pdf_a = tmp_path / "a" / "paper.pdf"
    pdf_b = tmp_path / "b" / "paper.pdf"
    pdf_a.parent.mkdir(parents=True)
    pdf_b.parent.mkdir(parents=True)
    pdf_a.touch()
    pdf_b.touch()

    resp = await client.post(
        f"/api/furnace/sessions/{session_id}/sources",
        json={
            "pdf_paths": [str(pdf_a), str(pdf_b)],
            "urls": [
                "https://alpha.example.com/papers/final-report",
                "https://beta.example.com/docs/final-report",
            ],
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert len(payload["source_ids"]) == 4
    assert len(set(payload["source_ids"])) == 4

    detail = await client.get(f"/api/furnace/sessions/{session_id}")
    metadata = detail.json()["session"]["metadata"]
    assert len(metadata["pdf_paths"]) == 2
    assert len(metadata["urls"]) == 2


@pytest.mark.asyncio
async def test_start_session_rejects_duplicate_worker(client: httpx.AsyncClient) -> None:
    from dan.server.routers import furnace as furnace_router_mod

    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Start Duplicate", "topic": "T"},
    )
    session_id = create_resp.json()["session"]["session_id"]

    task = asyncio.create_task(asyncio.sleep(0.05))
    furnace_router_mod._session_tasks[session_id] = task
    try:
        resp = await client.post(f"/api/furnace/sessions/{session_id}/start")
        assert resp.status_code == 200
        assert resp.json()["status"] == "already_running"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_start_session_recovers_stale_active_without_worker(
    client: httpx.AsyncClient,
    furnace_store: FurnaceSessionStore,
) -> None:
    from dan.engine.recipe.models import SessionState
    from dan.server.routers import furnace as furnace_router_mod

    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Recover Active", "topic": "T"},
    )
    session_id = create_resp.json()["session"]["session_id"]

    session = furnace_store.load(session_id)
    assert session is not None
    session.status = SessionState.ACTIVE
    furnace_store.save(session)

    with patch.object(furnace_router_mod, "_schedule_session_worker") as mock_schedule:
        resp = await client.post(f"/api/furnace/sessions/{session_id}/start")

    assert resp.status_code == 200
    assert resp.json()["status"] == "starting"
    mock_schedule.assert_called_once()

    persisted = furnace_store.load(session_id)
    assert persisted is not None
    assert persisted.status == SessionState.QUEUED

    detail = await client.get(f"/api/furnace/sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["session"]["status"] == "paused"


@pytest.mark.asyncio
async def test_cancel_session_rejects_completed_session(
    client: httpx.AsyncClient,
    furnace_store: FurnaceSessionStore,
) -> None:
    from dan.engine.recipe.models import SessionState

    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Completed Session", "topic": "T"},
    )
    session_id = create_resp.json()["session"]["session_id"]

    session = furnace_store.load(session_id)
    assert session is not None
    session.status = SessionState.COMPLETED
    furnace_store.save(session)

    resp = await client.post(f"/api/furnace/sessions/{session_id}/cancel")
    assert resp.status_code == 409
    assert "already completed" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_session_recovers_stale_queued_without_worker(
    client: httpx.AsyncClient,
    furnace_store: FurnaceSessionStore,
) -> None:
    from dan.engine.recipe.models import SessionState

    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Recover Queued", "topic": "T"},
    )
    session_id = create_resp.json()["session"]["session_id"]

    session = furnace_store.load(session_id)
    assert session is not None
    session.status = SessionState.QUEUED
    furnace_store.save(session)

    delete_resp = await client.delete(f"/api/furnace/sessions/{session_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_cancel_stops_after_current_source(
    client: httpx.AsyncClient,
    artifacts_dir: Path,
) -> None:
    from dan.server.routers import furnace as furnace_router_mod

    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Cancel Active", "topic": "T"},
    )
    session_id = create_resp.json()["session"]["session_id"]
    await client.post(
        f"/api/furnace/sessions/{session_id}/sources",
        json={"source_ids": ["p1", "p2"]},
    )

    calls: list[str] = []

    async def _slow_read(source_id: str, session, app, *, cancel_event=None):
        calls.append(source_id)
        await asyncio.sleep(0.05)
        return {"text": f"text for {source_id}", "chunk_summaries": []}

    with patch.object(furnace_router_mod, "_read_source", side_effect=_slow_read):
        start_resp = await client.post(f"/api/furnace/sessions/{session_id}/start")
        assert start_resp.status_code == 200
        await asyncio.sleep(0.01)

        cancel_resp = await client.post(f"/api/furnace/sessions/{session_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelling"

        task = furnace_router_mod._session_tasks[session_id]
        await asyncio.gather(task, return_exceptions=True)

    detail = await client.get(f"/api/furnace/sessions/{session_id}")
    assert detail.json()["session"]["status"] == "cancelled"
    assert calls == ["p1"]
    assert (artifacts_dir / session_id / "p1.txt").exists()
    assert not (artifacts_dir / session_id / "p2.txt").exists()


@pytest.mark.asyncio
async def test_cancelled_reader_does_not_mark_source_skipped(
    client: httpx.AsyncClient,
) -> None:
    from dan.server.routers import furnace as furnace_router_mod

    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "Cancel Reader", "topic": "T"},
    )
    session_id = create_resp.json()["session"]["session_id"]
    await client.post(
        f"/api/furnace/sessions/{session_id}/sources",
        json={"source_ids": ["p1"]},
    )

    async def _cancelled_read(source_id: str, session, app, *, cancel_event=None):
        assert cancel_event is not None
        await cancel_event.wait()
        raise furnace_router_mod.FurnaceCancelled("cancelled during read")

    with patch.object(furnace_router_mod, "_read_source", side_effect=_cancelled_read):
        start_resp = await client.post(f"/api/furnace/sessions/{session_id}/start")
        assert start_resp.status_code == 200
        await asyncio.sleep(0.01)

        cancel_resp = await client.post(f"/api/furnace/sessions/{session_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelling"

        task = furnace_router_mod._session_tasks[session_id]
        await asyncio.gather(task, return_exceptions=True)

    detail = await client.get(f"/api/furnace/sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["session"]["status"] == "cancelled"
    assert detail.json()["session"]["paper_queue"]["p1"] == "pending"


@pytest.mark.asyncio
async def test_publish_progress_fans_out_to_all_subscribers() -> None:
    from dan.server.routers import furnace as furnace_router_mod

    q1: asyncio.Queue[dict] = asyncio.Queue()
    q2: asyncio.Queue[dict] = asyncio.Queue()
    furnace_router_mod._session_progress["session-1"] = {q1, q2}

    furnace_router_mod._publish_progress("session-1", {"type": "phase_started", "phase": "read"})

    event1 = await asyncio.wait_for(q1.get(), timeout=0.1)
    event2 = await asyncio.wait_for(q2.get(), timeout=0.1)
    assert event1["type"] == "phase_started"
    assert event2["type"] == "phase_started"


@pytest.mark.asyncio
async def test_get_recipe_no_artifacts(client: httpx.AsyncClient) -> None:
    create_resp = await client.post(
        "/api/furnace/sessions",
        json={"name": "No Recipe", "topic": "T"},
    )
    assert create_resp.status_code == 200
    session_id = create_resp.json()["session"]["session_id"]

    resp = await client.get(f"/api/furnace/sessions/{session_id}/recipe")
    assert resp.status_code == 404
    assert "No compiled artifacts" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_feature_flag_disabled(
    furnace_store: FurnaceSessionStore,
    artifacts_dir: Path,
) -> None:
    from fastapi import FastAPI
    from dan.server.routers.furnace import router as furnace_router

    app = FastAPI()
    app.include_router(furnace_router)

    with patch("dan.server.routers.furnace.get_furnace_session_store", return_value=furnace_store):
        with patch("dan.server.routers.furnace.is_furnace_enabled", return_value=False):
            with patch("dan.server.routers.furnace.ARTIFACTS_ROOT", artifacts_dir):
                async with httpx.AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                ) as client:
                    resp = await client.post(
                        "/api/furnace/sessions",
                        json={"name": "Disabled", "topic": "T"},
                    )
                    assert resp.status_code == 403
                    assert "disabled" in resp.json()["detail"].lower()
