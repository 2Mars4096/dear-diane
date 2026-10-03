import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from diane.server import paper_library as library
from diane.server.chat_store import ChatStore
from diane.server.routers.papers import router


@pytest.fixture
def collection(tmp_path, monkeypatch):
    root = tmp_path / 'knowledge'
    note = root / 'content/papers/example/index.md'
    note.parent.mkdir(parents=True)
    pdf = root / 'static/papers/论文.pdf'
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b'%PDF-1.4\nfixture')
    note.write_text('''---
title: "Networks and {Trade}"
pageID: example2024
bibtex: >
  @article{example2024,
    title = {Networks and {Trade}},
    author = {Smith, A. and Jones, B.},
    journal = {Journal of Networks},
    year = {2024}
  }
tags: ["trade", "production-networks"]
abstract: "Financial links"
date: 2024-02-01
---
{{< paperPDF filename="论文.pdf" >}}
## Takeaways
Supplier substitution under tariffs.
''')
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path / 'graphs'))
    library._cache.clear()
    library.save_json(library.storage() / 'settings.json', {'sources': [{'kind': 'hugo', 'name': 'Knowledge', 'path': str(root)}]})
    app = FastAPI()
    app.state.chat_store = ChatStore(base_dir=str(tmp_path / 'graphs'))
    app.include_router(router)
    return TestClient(app), root, note, pdf


def test_hugo_metadata_refresh_and_pdf_delivery(collection):
    api, root, note, pdf = collection
    result = api.get('/api/papers').json()
    assert not result['warnings']
    paper = result['papers'][0]
    assert paper['key'] == 'example2024'
    assert paper['authors'] == 'Smith, A. and Jones, B.'
    assert paper['year'] == '2024'
    assert paper['journal'] == 'Journal of Networks'
    assert 'Supplier substitution' in paper['notes']
    assert 'paperPDF' not in paper['notes']
    assert paper['available']
    response = api.get(f"/api/papers/{paper['id']}/pdf")
    assert response.content == pdf.read_bytes()
    assert "filename*=utf-8''" in response.headers['content-disposition']
    note.write_text(note.read_text().replace('Financial links', 'Updated abstract'))
    assert api.get('/api/papers').json()['papers'][0]['abstract'] == 'Updated abstract'
    pdf.unlink()
    assert not api.get('/api/papers').json()['papers'][0]['available']
    assert api.post(f"/api/papers/{paper['id']}/open").status_code == 404


def test_links_and_symlinks_cannot_escape_sources(collection, tmp_path):
    api, root, note, pdf = collection
    outside = tmp_path / 'outside.pdf'; outside.write_bytes(b'secret')
    pdf.unlink(); pdf.symlink_to(outside)
    paper = api.get('/api/papers').json()['papers'][0]
    assert not paper['available']
    assert api.get(f"/api/papers/{paper['id']}/pdf").status_code == 404
    note.write_text(note.read_text().replace('论文.pdf', '../../../outside.pdf'))
    assert not api.get('/api/papers').json()['papers'][0]['available']
    assert api.get('/api/papers/sessions/invalid').status_code == 422


def test_reading_session_reuses_conversation_and_preserves_progress(collection):
    api, root, note, pdf = collection
    identifier = api.get('/api/papers').json()['papers'][0]['id']
    first = api.post(f'/api/papers/{identifier}/open').json()['session']
    assert first['workflow_id'] == '_dan_reading'
    body = {'revision': 0, 'position': {'page': 5, 'zoom': 1.5, 'left': 0, 'top': .25}, 'comments': [{'commentId': 'one', 'text': 'A note'}]}
    saved = api.put(f'/api/papers/sessions/{identifier}/progress', json=body)
    assert saved.status_code == 200
    assert saved.json()['revision'] == 1
    assert api.put(f'/api/papers/sessions/{identifier}/progress', json=body).status_code == 409
    reopened = api.post(f'/api/papers/{identifier}/open').json()['session']
    assert reopened['link'] == first['link']
    assert reopened['comments'] == body['comments']
    assert reopened['position']['page'] == 5
    assert api.put(f'/api/papers/{identifier}/pin', json={'pinned': True}).status_code == 200
    assert api.get('/api/papers').json()['sessions'][0]['pinned']
    assert "A note" in api.get('/api/papers').json()['papers'][0]['reading_notes']
    assert api.put(f'/api/papers/sessions/{identifier}/link', json={'threadId': 'different'}).status_code == 409
    assert api.put(f'/api/papers/sessions/{identifier}/link', json=first['link']).status_code == 200
    # A fresh app process can recover the same durable record.
    app = FastAPI(); app.include_router(router)
    recovered = TestClient(app).get(f'/api/papers/sessions/{identifier}').json()
    assert recovered['link'] == first['link'] and recovered['position']['page'] == 5
    assert pdf.read_bytes() == b'%PDF-1.4\nfixture'


def test_pdf_folder_sources_and_invalid_settings_are_atomic(collection, tmp_path):
    api, *_ = collection
    folder = tmp_path / 'PDFs'; folder.mkdir()
    (folder / 'A Study.PDF').write_bytes(b'%PDF')
    sources = [{'kind': 'pdf', 'path': str(folder), 'name': 'Loose papers'}]
    assert api.put('/api/papers/settings', json={'sources': sources}).status_code == 200
    paper = api.get('/api/papers').json()['papers'][0]
    assert paper['title'] == 'A Study' and paper['available']
    assert api.put('/api/papers/settings', json={'sources': [{'kind': 'hugo', 'path': '/missing'}]}).status_code == 422
    assert api.get('/api/papers').json()['settings']['sources'] == sources
    assert api.put('/api/papers/settings', json={'sources': []}).status_code == 200
    assert not api.get('/api/papers').json()['papers']


def test_corrupt_session_is_not_overwritten(collection):
    api, *_ = collection
    identifier = api.get('/api/papers').json()['papers'][0]['id']
    api.post(f'/api/papers/{identifier}/open')
    path = library.session_path(identifier); path.write_text('{broken')
    assert api.post(f'/api/papers/{identifier}/open').status_code == 503
    assert path.read_text() == '{broken'
