import asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace
from dan.server.chat_store import ChatStore, ChatMessage
from dan.server import session_titles
from dan.server.routers.sessions import update_chat_thread


def seeded(tmp_path):
    store = ChatStore(tmp_path)
    thread = store.create_thread('p', title='New chat')
    store.append_message('p', thread.id, ChatMessage(role='user', content='Please review the four research questions about market entry and exit.'))
    return store, store.get_thread('p', thread.id)


def test_first_request_only_and_stable_timestamp(tmp_path, monkeypatch):
    store, thread = seeded(tmp_path)
    store.append_message('p', thread.id, ChatMessage(role='user', content='Now work on something different'))
    before = store.get_thread('p', thread.id).updated_at
    model = AsyncMock(return_value='Review market entry research questions')
    monkeypatch.setattr(session_titles, 'summarize_request', model)
    asyncio.run(session_titles.name_session(store, 'p', thread.id))
    model.assert_awaited_once_with(thread.messages[0].content)
    assert store.get_thread('p', thread.id).updated_at == before
    assert store.get_thread('p', thread.id).title == 'Review market entry research questions'
    asyncio.run(session_titles.name_session(store, 'p', thread.id))
    assert model.await_count == 1


def test_manual_rename_wins_inflight(tmp_path, monkeypatch):
    store, thread = seeded(tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(chat_store=store)))
    async def generate(_):
        await update_chat_thread('p', thread.id, request, {'title':'My chosen title'})
        return 'Generated title'
    monkeypatch.setattr(session_titles, 'summarize_request', generate)
    assert asyncio.run(session_titles.name_session(store, 'p', thread.id)) is None
    assert store.get_thread('p', thread.id).title == 'My chosen title'


def test_deleted_session_not_recreated(tmp_path, monkeypatch):
    store, thread = seeded(tmp_path)
    async def generate(_):
        store.delete_thread('p', thread.id)
        return 'Generated title'
    monkeypatch.setattr(session_titles, 'summarize_request', generate)
    assert asyncio.run(session_titles.name_session(store, 'p', thread.id)) is None
    assert store.get_thread('p', thread.id) is None


def test_custom_titles_and_missing_provider_are_preserved(tmp_path, monkeypatch):
    store, thread = seeded(tmp_path)
    monkeypatch.setattr(session_titles, 'summarize_request', AsyncMock(return_value=None))
    assert asyncio.run(session_titles.name_session(store, 'p', thread.id)) is None
    assert store.get_thread('p', thread.id).title == thread.title
    store.update_thread_title('p', thread.id, 'Custom title')
    assert not store.needs_title_summary(store.get_thread('p',thread.id),{})
