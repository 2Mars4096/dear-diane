"""Durable Work/Notes chat-session CRUD."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from dan.server.chat_store import ChatMessage, ChatStore

router = APIRouter(tags=["sessions"])


def _store(request: Request) -> ChatStore:
    store = getattr(request.app.state, "chat_store", None)
    if not isinstance(store, ChatStore):
        raise HTTPException(status_code=503, detail="Session store unavailable")
    return store


@router.get("/api/chats")
async def list_all_chat_threads(request: Request, q: str = "") -> dict[str, Any]:
    query = q.strip().casefold()
    threads = _store(request).list_all_threads()
    if query:
        threads = [row for row in threads if query in
                   f"{row['id']} {row['title']} {row['workflow_id']}".casefold()]
    return {"threads": threads}


@router.get("/api/chats/{workflow_id}")
async def list_chat_threads(workflow_id: str, request: Request) -> dict[str, Any]:
    return {"threads": _store(request).list_threads(workflow_id)}


@router.get("/api/chats/{workflow_id}/{thread_id}")
async def get_chat_thread(
    workflow_id: str,
    thread_id: str,
    request: Request,
) -> dict[str, Any]:
    thread = _store(request).get_thread(workflow_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread.model_dump(mode="json")


@router.post("/api/chats/{workflow_id}")
async def create_chat_thread(
    workflow_id: str,
    request: Request,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = _store(request)
    payload = body or {}
    thread = store.create_thread(workflow_id, title=str(payload.get("title") or ""))
    mode = store._normalize_mode(payload.get("mode"))
    store.set_mode(workflow_id, thread.id, mode)
    parent = str(payload.get("parent_thread_id") or "")
    if parent and store.get_thread(workflow_id, parent) is not None:
        store.set_branch_lineage(workflow_id, thread.id, parent_thread_id=parent,
            branch_point_message_id=str(payload.get("branch_point_message_id") or ""),
            branch_type=str(payload.get("branch_type") or "explore"))
    result = thread.model_dump(mode="json")
    result["mode"] = mode
    return result


@router.put("/api/chats/{workflow_id}/{thread_id}")
async def update_chat_thread(
    workflow_id: str,
    thread_id: str,
    request: Request,
    body: dict[str, Any],
) -> dict[str, str]:
    store = _store(request)
    thread = store.get_thread(workflow_id, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    if "title" in body:
        store.update_thread_title(workflow_id, thread_id, str(body.get("title") or ""))
        thread = store.get_thread(workflow_id, thread_id) or thread
    if "messages" in body:
        thread.messages = [ChatMessage.model_validate(item) for item in body["messages"]]
        thread.updated_at = datetime.now(timezone.utc)
        store.save_thread(thread, auto_title="title" not in body)
    if "mode" in body:
        store.set_mode(workflow_id, thread_id, body.get("mode"))
    return {"status": "updated"}


@router.post("/api/chats/{workflow_id}/{thread_id}/archive")
async def archive_chat_thread(
    workflow_id: str,
    thread_id: str,
    request: Request,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    archived = bool((body or {}).get("archived", True))
    if not _store(request).set_archived(workflow_id, thread_id, archived):
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"status": "updated", "archived": archived}


@router.delete("/api/chats/{workflow_id}/{thread_id}")
async def delete_chat_thread(
    workflow_id: str,
    thread_id: str,
    request: Request,
    archived_only: bool = False,
) -> dict[str, str]:
    store = _store(request)
    if archived_only and not store.get_thread_meta(workflow_id, thread_id).get("archived", False):
        raise HTTPException(409, "Session is no longer archived; it was not deleted")
    if not store.delete_thread(workflow_id, thread_id):
        raise HTTPException(status_code=404, detail="Thread not found")
    return {"status": "deleted"}
