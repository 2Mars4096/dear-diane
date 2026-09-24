from fastapi import FastAPI
from fastapi.testclient import TestClient
from dan.server.routers.documents import router


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_edit_preserves_bytes_permissions_and_rejects_stale_revision(tmp_path):
    path = tmp_path / 'notes.md'
    path.write_bytes(b'\xef\xbb\xbf# Notes\r\n')
    path.chmod(0o640)
    api = client()
    params = {'path': str(path), 'root_path': str(tmp_path)}
    original = api.get('/api/workspace-files/document', params=params).json()
    assert original['content'] == '\ufeff# Notes\r\n'
    body = {**params, **original, 'content': '\ufeff# Edited\r\n'}
    response = api.put('/api/workspace-files/document', json=body)
    assert response.status_code == 200
    assert path.read_bytes() == b'\xef\xbb\xbf# Edited\r\n'
    assert path.stat().st_mode & 0o777 == 0o640
    assert api.put('/api/workspace-files/document', json=body).status_code == 409
    body['revision'] = response.json()['revision']
    path.write_text('External edit')
    assert api.put('/api/workspace-files/document', json=body).status_code == 409
    assert path.read_text() == 'External edit'


def test_document_rejects_binary_large_missing_and_escaping_files(tmp_path):
    api = client()
    root = tmp_path / 'workspace'; root.mkdir()
    for name, data, status in [('binary', b'abc\0def', 415), ('invalid', b'\xff', 415), ('big', b'a' * 2_000_001, 413)]:
        path = root / name; path.write_bytes(data)
        assert api.get('/api/workspace-files/document', params={'path':name,'root_path':str(root)}).status_code == status
    outside = tmp_path / 'outside.txt'; outside.write_text('private')
    (root / 'link.txt').symlink_to(outside)
    for name in ['../outside.txt', 'link.txt']:
        params = {'path':name,'root_path':str(root)}
        assert api.get('/api/workspace-files/document', params=params).status_code == 400
        assert api.put('/api/workspace-files/document', json={**params,'content':'change','revision':''}).status_code == 400
    assert api.get('/api/workspace-files/document', params={'path':'absent','root_path':str(root)}).status_code == 404
