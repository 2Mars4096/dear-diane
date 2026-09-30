from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from dan.personal.models import CaptureInput
from dan.personal.store import PersonalStore, Conflict
from dan.server.routers.personal import router


def api(monkeypatch, tmp_path):
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path))
    monkeypatch.setenv('DAN_PERSONAL_ENABLED', '1')
    app = FastAPI(); app.include_router(router)
    return TestClient(app)


def test_capture_survives_restart_and_network_replay(monkeypatch, tmp_path):
    client = api(monkeypatch, tmp_path)
    body = {'operation_id': 'capture-0001', 'text': 'Meet Ada tomorrow.'}
    first = client.post('/api/personal/captures', json=body)
    assert first.status_code == 200
    assert first.json()['commitment']['date'] is None
    restarted = api(monkeypatch, tmp_path)
    assert restarted.post('/api/personal/captures', json=body).json() == first.json()
    assert len(restarted.get('/api/personal/commitments').json()['commitments']) == 1
    assert restarted.post('/api/personal/captures', json={**body, 'text': 'Changed'}).status_code == 409
    assert (tmp_path / 'personal/state.sqlite3').stat().st_mode & 0o777 == 0o600
    assert (tmp_path / 'personal').stat().st_mode & 0o777 == 0o700
    with pytest.raises(KeyError):
        PersonalStore(tmp_path / 'personal').detail('other-owner', first.json()['commitment']['id'])


def test_concurrent_capture_replay_commits_once(tmp_path):
    store = PersonalStore(tmp_path / 'personal')
    body = CaptureInput(operation_id='concurrent-1', text='Test invitation')
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: store.capture('operator', body), range(12)))
    assert len({r['commitment']['id'] for r in results}) == 1
    assert store.snapshot('operator')['cursor'] == 1
    # Rollback after an exception leaves the store writable and no operation row.
    with pytest.raises(RuntimeError):
        with store.connection() as db:
            db.execute("INSERT INTO business_events(owner,entity_id,kind,at) VALUES ('operator','bad','bad','now')")
            raise RuntimeError('crash before commit')
    assert store.snapshot('operator')['cursor'] == 1


def test_review_conflicts_duplicates_and_calendar_receipt(monkeypatch, tmp_path):
    client = api(monkeypatch, tmp_path)
    capture = client.post('/api/personal/captures', json={'operation_id': 'capture-001', 'text': '午餐\nsource'}).json()
    duplicate = client.post('/api/personal/captures', json={'operation_id': 'capture-002', 'text': '午餐\nsource'}).json()
    assert duplicate['capture']['duplicate_candidates'] == [capture['capture']['id']]
    record_id = capture['commitment']['id']; url = '/api/personal/commitments/' + record_id
    assert client.get(url + '/calendar.ics').status_code == 422
    body = {'operation_id': 'review-0001', 'expected_revision': 1, 'title': '午餐, 和朋友;\nATTENDEE:evil',
            'date': '2026-10-02', 'time': '14:00', 'timezone': 'Asia/Hong_Kong', 'decision': 'confirm'}
    confirmed = client.patch(url, json=body)
    assert confirmed.status_code == 200
    assert confirmed.json()['revision'] == 2
    assert client.patch(url, json=body).json() == confirmed.json()
    assert client.patch(url, json={**body, 'operation_id': 'stale-op-1'}).status_code == 409
    content = client.get(url + '/calendar.ics')
    assert content.status_code == 200
    assert 'DTSTART:20261002T060000Z' in content.text
    assert '\r\nATTENDEE:' not in content.text
    assert 'METHOD:REQUEST' not in content.text
    assert 'insertion is not verified' in content.text
    assert content.headers['cache-control'] == 'no-store'
    for line in content.content.split(b'\r\n'):
        assert len(line) <= 75
        line.decode('utf-8')
    assert client.get(url).json()['history'][-1]['kind'] == 'confirm'


@pytest.mark.parametrize('updates', [
    {'date': None}, {'timezone': None}, {'date': '2027-02-29'}, {'timezone': 'Mars/Olympus'},
    {'date': '2027-03-14', 'time': '02:30', 'timezone': 'America/New_York'},
    {'date': '2026-11-01', 'time': '01:30', 'timezone': 'America/New_York'},
    {'time': '10:00+08:00'}, {'all_day': True, 'time': '10:00'},
])
def test_never_confirms_missing_invalid_or_dst_ambiguous_dates(monkeypatch, tmp_path, updates):
    client = api(monkeypatch, tmp_path)
    record = client.post('/api/personal/captures', json={'operation_id': 'capture-001', 'text': 'Meet'}).json()['commitment']
    body = {'operation_id': 'review-0001', 'expected_revision': 1, 'title': 'Meet',
            'date': '2026-10-02', 'time': '14:00', 'timezone': 'Asia/Hong_Kong', 'decision': 'confirm', **updates}
    assert client.patch('/api/personal/commitments/' + record['id'], json=body).status_code == 422
    assert client.get('/api/personal/commitments').json()['commitments'][0]['revision'] == 1


def test_unsupported_intake_owner_injection_disabled_feature_and_all_day(monkeypatch, tmp_path):
    client = api(monkeypatch, tmp_path)
    for text in [' ', '\0', '中' * 30000]:
        assert client.post('/api/personal/captures', json={'operation_id': 'capture-001', 'text': text}).status_code == 422
    assert client.post('/api/personal/captures', json={'operation_id': 'capture-001', 'text': 'Meet', 'owner_id': 'other'}).status_code == 422
    record = client.post('/api/personal/captures', json={'operation_id': 'capture-002', 'text': 'Holiday'}).json()['commitment']
    url = '/api/personal/commitments/' + record['id']
    body = {'operation_id': 'review-001', 'expected_revision': 1, 'title': 'Holiday', 'date': '2026-10-01', 'all_day': True, 'timezone': 'Asia/Hong_Kong', 'decision': 'confirm'}
    assert client.patch(url, json=body).status_code == 200
    assert 'DTSTART;VALUE=DATE:20261001' in client.get(url + '/calendar.ics').text
    assert client.patch(url, json={**body, 'operation_id': 'dismiss-001', 'expected_revision': 2, 'decision': 'dismiss'}).status_code == 200
    assert client.get(url + '/calendar.ics').status_code == 422
    monkeypatch.setenv('DAN_PERSONAL_ENABLED', '0')
    assert client.get('/api/personal/capabilities').json()['enabled'] is False
    assert client.post('/api/personal/captures', json={'operation_id': 'capture-003', 'text': 'Meet'}).status_code == 404
    assert (tmp_path / 'personal/state.sqlite3').exists()
