import base64
from urllib.parse import quote, unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from diane.server.routers.misc import router


@pytest.mark.parametrize('name', ['notes.pdf', 'Victor Klemperer - 第三帝国的语言 (2013, 商务印书馆).pdf', '阅读 "引文".pdf'])
def test_unicode_preview_filename_and_pdf_byte_ranges(tmp_path, name):
    content = b'%PDF-1.6\n' + b'example bytes' * 100
    (tmp_path / name).write_bytes(content)
    token = base64.urlsafe_b64encode(str(tmp_path).encode()).decode().rstrip('=')
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    url = '/api/workspace-files/preview/' + token + '/' + quote(name, safe='')
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == content
    disposition = response.headers['content-disposition']
    assert disposition.startswith('inline;')
    if 'filename*=' in disposition:
        assert unquote(disposition).endswith(name.replace('"', '_'))
    else:
        assert 'filename="notes.pdf"' in disposition
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'sandbox' in response.headers['content-security-policy']
    ranged = client.get(url, headers={'Range': 'bytes=0-15'})
    assert ranged.status_code == 206
    assert ranged.content == content[:16]
    assert ranged.headers['content-range'] == f'bytes 0-15/{len(content)}'
