from concurrent.futures import ThreadPoolExecutor

import pytest
from dan.server.chat_v2_dispatch import reserve_dispatch
from dan.server.chat_v2_store import ChatV2Store


def reserve(store, **updates):
    return reserve_dispatch(store, **{'key': 'personal:operator:capture:1', 'objective': 'Extract synthetic evidence', 'thread_id': '_personal_test', **updates})


def test_parallel_stores_share_one_admission_and_reject_changed_input(tmp_path):
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(lambda _: reserve(ChatV2Store(tmp_path)), range(8)))
    assert len({run.run_id for run in runs}) == 1
    store = ChatV2Store(tmp_path)
    assert len(store.list_run_records()) == 1
    with pytest.raises(ValueError, match='different inputs'):
        reserve(store, objective='Different objective')
    store.update_run_metadata(runs[0].run_id, {'test': True}, status='completed')
    replay = reserve(ChatV2Store(tmp_path))
    assert replay.status == 'completed' and replay.metadata['test'] is True


def test_crash_after_run_before_task_or_journal_commit_repairs_same_identity(tmp_path, monkeypatch):
    store = ChatV2Store(tmp_path)
    original = store._save_task
    monkeypatch.setattr(store, '_save_task', lambda task: (_ for _ in ()).throw(RuntimeError('simulated crash')))
    with pytest.raises(RuntimeError, match='simulated crash'):
        reserve(store)
    persisted = store.list_run_records()
    assert len(persisted) == 1
    monkeypatch.setattr(store, '_save_task', original)
    recovered = reserve(ChatV2Store(tmp_path))
    assert recovered.run_id == persisted[0].run_id
    assert store.get_task(recovered.task_id).active_run_id == recovered.run_id
    assert len(store.list_run_records()) == 1


def test_missing_admitted_run_is_not_recreated(tmp_path):
    store = ChatV2Store(tmp_path)
    run = reserve(store)
    store._run_path(run.run_id).unlink()
    with pytest.raises(RuntimeError, match='missing'):
        reserve(store)
