from __future__ import annotations

from pathlib import Path

from dan.server.chat_store import ChatMessage, ChatStore, ChatThread


def _thread_path(base_dir: Path, workflow_id: str, thread_id: str) -> Path:
    return base_dir / "chats" / workflow_id / f"{thread_id}.json"


def _journal_path(base_dir: Path, workflow_id: str, thread_id: str) -> Path:
    return base_dir / "chats" / workflow_id / f"{thread_id}.journal.jsonl"


def _fail_full_load(*_args, **_kwargs):
    raise AssertionError("_read_thread_from_path should not be used on this hot path")


def test_append_message_uses_cached_state_without_reloading(tmp_path: Path, monkeypatch):
    store = ChatStore(base_dir=tmp_path)
    thread = store.create_thread("wf-1", title="Hot Path")

    monkeypatch.setattr(store, "_read_thread_from_path", _fail_full_load)

    updated = store.append_message(
        "wf-1",
        thread.id,
        ChatMessage(role="user", content="hello from cache"),
    )

    assert updated is not None
    assert [msg.content for msg in updated.messages] == ["hello from cache"]

    snapshot = ChatThread.model_validate_json(
        _thread_path(tmp_path, "wf-1", thread.id).read_text(encoding="utf-8")
    )
    assert snapshot.messages == []

    reloaded = ChatStore(base_dir=tmp_path).get_thread("wf-1", thread.id)
    assert reloaded is not None
    assert [msg.content for msg in reloaded.messages] == ["hello from cache"]


def test_append_message_compacts_from_cached_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DAN_CHAT_STORE_COMPACT_EVERY", "1")
    store = ChatStore(base_dir=tmp_path)
    thread = store.create_thread("wf-2")

    monkeypatch.setattr(store, "_read_thread_from_path", _fail_full_load)

    updated = store.append_message(
        "wf-2",
        thread.id,
        ChatMessage(role="assistant", content="compacted"),
    )

    assert updated is not None
    assert [msg.content for msg in updated.messages] == ["compacted"]
    assert not _journal_path(tmp_path, "wf-2", thread.id).exists()

    snapshot = ChatThread.model_validate_json(
        _thread_path(tmp_path, "wf-2", thread.id).read_text(encoding="utf-8")
    )
    assert [msg.content for msg in snapshot.messages] == ["compacted"]


def test_update_title_and_meta_skip_full_replay_on_cold_store(
    tmp_path: Path, monkeypatch
):
    warm = ChatStore(base_dir=tmp_path)
    thread = warm.create_thread("wf-3", title="Original")
    warm.append_message(
        "wf-3",
        thread.id,
        ChatMessage(role="user", content="existing history"),
    )

    cold = ChatStore(base_dir=tmp_path)
    monkeypatch.setattr(cold, "_read_thread_from_path", _fail_full_load)

    assert cold.update_thread_title("wf-3", thread.id, "Renamed")
    assert cold.set_mode("wf-3", thread.id, "build")
    assert cold.set_pinned("wf-3", thread.id, True)

    reloaded = ChatStore(base_dir=tmp_path).get_thread("wf-3", thread.id)
    assert reloaded is not None
    assert reloaded.title == "Renamed"
    assert [msg.content for msg in reloaded.messages] == ["existing history"]

    meta = cold.get_thread_meta("wf-3", thread.id)
    assert meta == {"mode": "agent", "pinned": True}


def test_get_thread_returns_detached_copy_when_cache_is_hot(tmp_path: Path):
    store = ChatStore(base_dir=tmp_path)
    thread = store.create_thread("wf-4")
    store.append_message(
        "wf-4",
        thread.id,
        ChatMessage(role="user", content="persisted"),
    )

    loaded = store.get_thread("wf-4", thread.id)
    assert loaded is not None
    loaded.messages.append(ChatMessage(role="assistant", content="local only"))
    loaded.title = "mutated in caller"

    refreshed = store.get_thread("wf-4", thread.id)
    assert refreshed is not None
    assert refreshed.title == ""
    assert [msg.content for msg in refreshed.messages] == ["persisted"]
