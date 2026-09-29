from __future__ import annotations

from fastapi.testclient import TestClient

from dan.server.app import create_app


def test_retained_product_api_supports_work_notes_sessions_and_agent_runs(
    monkeypatch,
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    graphs = tmp_path / "graphs"
    notes = tmp_path / "notes"
    notes.mkdir()
    monkeypatch.setenv("DAN_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(graphs))
    monkeypatch.setenv("DAN_NOTES_WORKSPACE_ROOT", str(notes))

    with TestClient(create_app()) as client:
        assert client.get("/health").json()["status"] == "ok"

        created = client.post(
            "/api/chats/_scratch",
            json={"title": "Archive cutover", "mode": "agent"},
        )
        assert created.status_code == 200
        thread = created.json()
        thread_id = thread["id"]
        assert thread["mode"] == "agent"

        updated = client.put(
            f"/api/chats/_scratch/{thread_id}",
            json={
                "messages": [
                    {"role": "user", "content": "Build through Diane"},
                    {"role": "assistant", "content": "Ready"},
                ]
            },
        )
        assert updated.json() == {"status": "updated"}
        assert client.get(f"/api/chats/_scratch/{thread_id}").status_code == 200
        assert client.post(f"/api/chats/_scratch/{thread_id}/archive").json()["archived"] is True

        write = client.put(
            "/api/workspace-notes/write",
            json={"path": "cutover.md", "content": "# Universal product\n"},
        )
        assert write.status_code == 200
        note = client.get("/api/workspace-notes/read", params={"path": "cutover.md"})
        assert note.status_code == 200
        assert "Universal product" in note.json()["content"]

        accepted = client.post(
            "/api/v2/agent-runs",
            json={
                "workflow_id": "_scratch",
                "thread_id": thread_id,
                "message": "Create a concise implementation plan",
                "mode": "agent",
                "surface_context": {"workspace_root": str(workspace)},
            },
        )
        assert accepted.status_code == 200
        payload = accepted.json()
        assert payload["status"] == "accepted"
        assert payload["v2_control_plane"]["run_id"]
        assert client.get("/api/v2/tasks").json()["tasks"]

        assert client.delete(f"/api/chats/_scratch/{thread_id}").json() == {
            "status": "deleted"
        }


def test_archived_only_delete_preserves_active_chats(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from dan.server.app import create_app
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        thread = client.post("/api/chats/project", json={"title": "Keep active"}).json()
        url = f"/api/chats/project/{thread['id']}"
        assert client.delete(url + "?archived_only=true").status_code == 409
        assert client.get(url).status_code == 200
        assert client.post(url + "/archive", json={"archived": True}).status_code == 200
        assert client.delete(url + "?archived_only=true").status_code == 200
        assert client.get(url).status_code == 404


def test_sidecar_thread_keeps_parent_lineage_and_separate_messages(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from dan.server.app import create_app
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        parent = client.post("/api/chats/project", json={"title": "Main chat"}).json()
        side = client.post("/api/chats/project", json={"title": "Sidecar", "parent_thread_id": parent["id"], "branch_type":"explore"}).json()
        threads = client.get("/api/chats").json()["threads"]
        assert next(t for t in threads if t["id"]==side["id"])["parent_thread_id"]==parent["id"]
        client.put(f"/api/chats/project/{side['id']}", json={"messages":[{"role":"user","content":"Side question"}]})
        assert client.get(f"/api/chats/project/{parent['id']}").json()["messages"]==[]
