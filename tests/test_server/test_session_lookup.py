import asyncio
import pytest
from types import SimpleNamespace

from diane.server.chat_store import ChatStore
from diane.server.routers.sessions import list_all_chat_threads, get_chat_thread


def test_lookup_by_id_or_title_preserves_workflow(tmp_path):
    store = ChatStore(tmp_path)
    first = store.create_thread("project-one", title="Review the paper")
    second = store.create_thread("project-two", title="Review the paper")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(chat_store=store)))
    rows = asyncio.run(list_all_chat_threads(request, q=first.id))["threads"]
    assert len(rows) == 1
    assert rows[0]["workflow_id"] == "project-one"
    assert len(asyncio.run(list_all_chat_threads(request, q="REVIEW THE PAPER"))["threads"]) == 2
    assert asyncio.run(list_all_chat_threads(request, q="unknown-session"))["threads"] == []
    result = asyncio.run(get_chat_thread(rows[0]["workflow_id"], rows[0]["id"], request))
    assert result["id"] == first.id
    assert second.id != first.id


@pytest.mark.parametrize("placeholder", ["New Super DAN Session", "Untitled DAN Super session", "New Diane Session", "Untitled Diane session"])
def test_session_gets_first_request_title_once_and_keeps_manual_rename(tmp_path, placeholder):
    from diane.server.routers.sessions import update_chat_thread
    store = ChatStore(tmp_path)
    thread = store.create_thread("project", title=placeholder)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(chat_store=store)))
    messages = [{"role":"user", "content":"Build the\n  report"}]
    asyncio.run(update_chat_thread("project", thread.id, request, {"messages":messages}))
    assert store.get_thread("project", thread.id).title == "Build the report"
    messages.append({"role":"user", "content":"Now fix the chart"})
    asyncio.run(update_chat_thread("project", thread.id, request, {"messages":messages}))
    assert store.get_thread("project", thread.id).title == "Build the report"
    asyncio.run(update_chat_thread("project", thread.id, request, {"title":"Quarterly report"}))
    asyncio.run(update_chat_thread("project", thread.id, request, {"messages":messages}))
    assert store.get_thread("project", thread.id).title == "Quarterly report"


def test_native_store_writes_name_sessions_and_replay_stays_stable(tmp_path):
    from diane.server.chat_store import ChatMessage
    store = ChatStore(tmp_path)
    thread = store.create_thread("project", title="New Diane Session")
    store.append_message("project", thread.id, ChatMessage(role="user", content="  "))
    store.append_message("project", thread.id, ChatMessage(role="user", content="Fix the\n native titles"))
    store.append_message("project", thread.id, ChatMessage(role="user", content="Later request"))
    reloaded = ChatStore(tmp_path)
    assert reloaded.get_thread("project", thread.id).title == "Fix the native titles"
    assert reloaded.list_threads("project")[0]["title"] == "Fix the native titles"
    direct = store.create_thread("project", title="New Diane Session")
    direct.messages = [ChatMessage(role="user", content="Saved by native runtime")]
    store.save_thread(direct)
    assert ChatStore(tmp_path).get_thread("project", direct.id).title == "Saved by native runtime"


def test_legacy_placeholder_titles_resolve_without_rewriting_history(tmp_path):
    from diane.server.chat_store import ChatMessage
    store = ChatStore(tmp_path)
    thread = store.create_thread("project", title="New Diane Session")
    thread.messages = [ChatMessage(role="user", content="First request"),
                       ChatMessage(role="user", content="Later request")]
    path = tmp_path / "chats" / "project" / f"{thread.id}.json"
    path.write_text(thread.model_dump_json())
    original = path.read_bytes()
    store = ChatStore(tmp_path)
    assert store.list_threads("project")[0]["title"] == "First request"
    assert store.get_thread("project", thread.id).title == "First request"
    assert path.read_bytes() == original
