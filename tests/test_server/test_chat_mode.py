"""Tests for per-thread chat mode persistence (Plan 20-1, Task 4)."""

from __future__ import annotations

import pytest

from dan.server.chat_store import ChatStore

WORKFLOW_ID = "wf-mode-test"


@pytest.fixture()
def store(tmp_path) -> ChatStore:
    return ChatStore(base_dir=str(tmp_path))


class TestModePersistence:
    """Mode is stored in thread metadata and surfaced in list/get."""

    def test_default_mode_is_agent(self, store):
        thread = store.create_thread(WORKFLOW_ID, title="t1")
        threads = store.list_threads(WORKFLOW_ID)
        assert len(threads) == 1
        assert threads[0]["mode"] == "agent"

    def test_set_and_get_mode(self, store):
        thread = store.create_thread(WORKFLOW_ID, title="t1")
        store.set_mode(WORKFLOW_ID, thread.id, "debug")
        threads = store.list_threads(WORKFLOW_ID)
        assert threads[0]["mode"] == "debug"

    def test_all_valid_modes(self, store):
        for mode in ("ask", "agent", "plan", "debug"):
            thread = store.create_thread(WORKFLOW_ID, title=f"m-{mode}")
            store.set_mode(WORKFLOW_ID, thread.id, mode)
            meta = store.get_thread_meta(WORKFLOW_ID, thread.id)
            assert meta["mode"] == mode

    def test_mode_survives_round_trip(self, store):
        thread = store.create_thread(WORKFLOW_ID, title="rt")
        store.set_mode(WORKFLOW_ID, thread.id, "plan")
        meta = store.get_thread_meta(WORKFLOW_ID, thread.id)
        assert meta["mode"] == "plan"
        threads = store.list_threads(WORKFLOW_ID)
        match = [t for t in threads if t["id"] == thread.id]
        assert match[0]["mode"] == "plan"


class TestModeNormalization:
    """Legacy and missing mode values normalize to 'agent'."""

    def test_missing_mode_defaults_to_agent(self, store):
        assert store._normalize_mode(None) == "agent"
        assert store._normalize_mode("") == "agent"

    def test_build_alias_normalizes_to_agent(self, store):
        assert store._normalize_mode("build") == "agent"

    def test_mutate_alias_normalizes_to_agent(self, store):
        assert store._normalize_mode("mutate") == "agent"

    def test_unknown_mode_normalizes_to_agent(self, store):
        assert store._normalize_mode("nonexistent") == "agent"

    def test_valid_modes_pass_through(self, store):
        for mode in ("ask", "agent", "plan", "debug"):
            assert store._normalize_mode(mode) == mode

    def test_thread_without_meta_lists_as_agent(self, store):
        thread = store.create_thread(WORKFLOW_ID, title="no-meta")
        threads = store.list_threads(WORKFLOW_ID)
        match = [t for t in threads if t["id"] == thread.id]
        assert match[0]["mode"] == "agent"

    def test_set_mode_normalizes_before_storing(self, store):
        thread = store.create_thread(WORKFLOW_ID, title="legacy")
        store.set_mode(WORKFLOW_ID, thread.id, "build")
        meta = store.get_thread_meta(WORKFLOW_ID, thread.id)
        assert meta["mode"] == "agent"


class TestModeWithPinned:
    """Mode and pinned coexist in the same metadata file."""

    def test_set_mode_preserves_pinned(self, store):
        thread = store.create_thread(WORKFLOW_ID, title="both")
        store.set_pinned(WORKFLOW_ID, thread.id, True)
        store.set_mode(WORKFLOW_ID, thread.id, "debug")
        meta = store.get_thread_meta(WORKFLOW_ID, thread.id)
        assert meta["pinned"] is True
        assert meta["mode"] == "debug"

    def test_set_pinned_preserves_mode(self, store):
        thread = store.create_thread(WORKFLOW_ID, title="both2")
        store.set_mode(WORKFLOW_ID, thread.id, "plan")
        store.set_pinned(WORKFLOW_ID, thread.id, True)
        meta = store.get_thread_meta(WORKFLOW_ID, thread.id)
        assert meta["pinned"] is True
        assert meta["mode"] == "plan"

    def test_list_includes_both_mode_and_pinned(self, store):
        thread = store.create_thread(WORKFLOW_ID, title="list-test")
        store.set_mode(WORKFLOW_ID, thread.id, "ask")
        store.set_pinned(WORKFLOW_ID, thread.id, True)
        threads = store.list_threads(WORKFLOW_ID)
        match = [t for t in threads if t["id"] == thread.id]
        assert match[0]["mode"] == "ask"
        assert match[0]["pinned"] is True


class TestSetModeReturnValue:
    """set_mode returns False for non-existent threads."""

    def test_returns_false_for_missing_thread(self, store):
        assert store.set_mode(WORKFLOW_ID, "nonexistent", "debug") is False

    def test_returns_true_for_existing_thread(self, store):
        thread = store.create_thread(WORKFLOW_ID)
        assert store.set_mode(WORKFLOW_ID, thread.id, "plan") is True


class TestListAllThreads:
    """Cross-workflow history listing preserves workflow IDs and ordering."""

    def test_lists_threads_across_workflows(self, store):
        first = store.create_thread("wf-a", title="a")
        second = store.create_thread("wf-b", title="b")

        store.update_thread_title("wf-a", first.id, "older")
        store.update_thread_title("wf-b", second.id, "newer")

        threads = store.list_all_threads()

        assert [thread["workflow_id"] for thread in threads] == ["wf-b", "wf-a"]
        assert [thread["id"] for thread in threads] == [second.id, first.id]
