from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from dan.server.routers import attachments


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, 'resolve_graphs_dir', lambda: str(tmp_path / 'graphs'))
    app = FastAPI(); app.include_router(attachments.router)
    return TestClient(app)


def test_upload_preserves_pdf_bytes_name_and_private_permissions(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    data = b'%PDF-1.7\n\x00\xff content'
    response = api.post('/api/chat-attachments', content=data, headers={'x-filename':'%E7%A0%94%E7%A9%B6.pdf'})
    assert response.status_code == 200
    value = response.json(); path = Path(value['path'])
    assert path.parent == tmp_path / 'chat-attachments'
    assert path.read_bytes() == data
    assert value['filename'] == '研究.pdf'
    assert path.stat().st_mode & 0o777 == 0o600
    other = api.post('/api/chat-attachments', content=b'new', headers={'x-filename':'%E7%A0%94%E7%A9%B6.pdf'})
    assert other.json()['path'] != str(path)
    assert path.read_bytes() == data


def test_rejects_escape_names_and_cleans_up_oversized_upload(tmp_path, monkeypatch):
    api = client(tmp_path, monkeypatch)
    for name in ['../secret', '..%2Fsecret', '..%5Csecret', '%00bad', '.']:
        assert api.post('/api/chat-attachments', content=b'x', headers={'x-filename':name}).status_code == 400
    monkeypatch.setattr(attachments, 'MAX_ATTACHMENT_BYTES', 4)
    assert api.post('/api/chat-attachments', content=b'12345', headers={'x-filename':'big.txt'}).status_code == 413
    assert list((tmp_path / 'chat-attachments').iterdir()) == []
