import json
from diane.native_workers.event_pages import event_page, _indexes


def test_pages_append_partial_rotation_and_truncation(tmp_path):
    path = tmp_path / 'events.jsonl'
    path.write_text(''.join(json.dumps({'n': n}) + '\n' for n in range(8)))
    assert [r['cursor'] for r in event_page(path, limit=3)['entries']] == [5, 6, 7]
    cached = _indexes[str(path)]['offsets']
    assert [r['cursor'] for r in event_page(path, before=5, limit=2)['entries']] == [3, 4]
    with path.open('a') as out:
        out.write('{"n":8}\n{"n":')
    assert event_page(path, after=7)['events'] == [{'n': 8}]
    assert _indexes[str(path)]['offsets'] is cached
    with path.open('a') as out:
        out.write('9}\n')
    assert event_page(path, after=8)['events'] == [{'n': 9}]
    path.write_text('{"n":0}\n')
    assert event_page(path)['events'] == [{'n': 0}]
    replacement = tmp_path / 'replacement'
    replacement.write_text('{"n":1}\n')
    replacement.replace(path)
    assert event_page(path)['events'] == [{'n': 1}]


def test_malformed_lines_keep_stable_cursors_and_bounds(tmp_path):
    path = tmp_path / 'events.jsonl'
    path.write_text('{"n":0}\nbroken\n{"n":2}\n')
    assert [r['cursor'] for r in event_page(path)['entries']] == [0, 2]
    assert event_page(path, after=0, limit=1)['entries'] == [{'cursor': 2, 'event': {'n': 2}}]
    assert event_page(path, before=0)['entries'] == []
    assert event_page(path, after=99)['entries'] == []
    assert event_page(tmp_path / 'missing')['entries'] == []


def test_worker_snapshots_refresh_without_mutable_cache_leaks(tmp_path):
    from diane.native_workers.service import read_workers, _worker_snapshots
    path = tmp_path / 'worker.json'
    row = dict(worker_id='worker', parent_run_id='parent', status='completed', created_at=1, profile={'model': 'one'})
    path.write_text(json.dumps(row))
    first = read_workers(tmp_path, 'parent')
    first[0]['profile']['model'] = 'mutated'
    assert read_workers(tmp_path, 'parent')[0]['profile']['model'] == 'one'
    row['profile']['model'] = 'two'
    path.write_text(json.dumps(row))
    assert read_workers(tmp_path, 'parent')[0]['profile']['model'] == 'two'
    assert sum(key == str(path) for key in _worker_snapshots) == 1


def test_event_route_preserves_parent_ownership(tmp_path, monkeypatch):
    import pytest
    from fastapi import HTTPException
    from diane.server.routers import native_workers as routes
    monkeypatch.setattr(routes, 'resolve_graphs_dir', lambda: str(tmp_path))
    base = tmp_path / 'native_workers'
    base.mkdir()
    (base / 'worker.json').write_text(json.dumps({'worker_id': 'worker', 'parent_run_id': 'owner'}))
    (base / 'worker.jsonl').write_text('{"type":"assistant"}\n')
    assert routes.worker_events('owner', 'worker')['entries'][0]['cursor'] == 0
    for parent, worker in [('other', 'worker'), ('owner', '../worker'), ('owner', 'missing')]:
        with pytest.raises(HTTPException) as error:
            routes.worker_events(parent, worker)
        assert error.value.status_code == 404
