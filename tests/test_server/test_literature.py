import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dan.server import literature as intake, paper_library as library, paper_ingest as ingest
from dan.server.chat_store import ChatStore
from dan.server.chat_v2_store import ChatV2Store
from dan.server.routers.literature import router

BIB = '@article{smith2024trade, title={Trade and Networks}, author={Smith, Anne}, year={2024}, doi={10.1234/trade}}'
PDF = b'%PDF-1.4\nexample content'
DRAFT = {'match': 'verified', 'candidate_index': 0, 'reason': 'Title, author, year, and DOI agree with the title page.', 'abstract': 'A study of trade networks.', 'tags': ['trade'], 'notes_markdown': '## Takeaways\n\n- The paper studies trade networks (p. 1).\n\n## Method\n\nA network model links firms through trade (p. 2).\n\n## Q&A\n', 'read_pages': [1, 2]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / 'knowledge'
    for d in ['content/papers', 'static/papers']: (root / d).mkdir(parents=True)
    monkeypatch.setenv('DAN_GRAPHS_DIR', str(tmp_path / 'graphs'))
    library._cache.clear()
    library.save_json(library.storage() / 'settings.json', {'sources': [{'kind': 'hugo', 'name': 'KB', 'path': str(root)}]})
    app = FastAPI()
    app.state.chat_store = ChatStore(base_dir=str(tmp_path / 'graphs'))
    app.state.chat_v2_store = ChatV2Store(base_dir=str(tmp_path / 'graphs/chat_v2'))
    app.include_router(router)
    # Most route tests own worker scheduling explicitly.
    app.state.literature_runner = SimpleNamespace(start=lambda: None)
    return TestClient(app), root, app


def upload(api, root, data=PDF):
    batch = api.post('/api/literature/batches', json={'destination': str(root), 'bibtex': BIB}).json()
    response = api.post(f"/api/literature/batches/{batch['id']}/pdf?name=论文.pdf", content=data)
    assert response.status_code == 200, response.text
    return response.json()


def make_ready(batch):
    item = batch['items'][0]
    item.update(status='ready', pages=3, bibtex=BIB, candidates=[intake.candidate(BIB, 'Supplied BibTeX')], draft=DRAFT, key='smith2024trade')
    intake.save(batch)
    return item


def test_ingest_copies_pdf_and_preserves_bibtex_and_notes(setup):
    api, root, app = setup
    batch = upload(api, root)
    item = make_ready(batch)
    response = api.post(f"/api/literature/batches/{batch['id']}/apply", json={'ids': [item['id']]})
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['status'] == 'imported'
    assert (intake.directory(batch['id']) / (item['id'] + '.pdf')).read_bytes() == PDF
    assert (root / 'static/papers/smith2024trade.pdf').read_bytes() == PDF
    note = root / 'content/papers/smith2024trade/index.md'
    text = note.read_text()
    assert BIB in text and 'pageID: "smith2024trade"' in text
    assert '{{< paperPDF filename="smith2024trade.pdf" height="800px" >}}' in text
    assert 'abstract:' in text and '## Q&A' in text
    assert api.post(f"/api/literature/batches/{batch['id']}/apply", json={'ids': [item['id']]}).status_code == 409
    assert note.read_text() == text
    assert library.catalogue()['papers'][0]['available']


def test_upload_duplicate_invalid_pdf_and_unknown_destination(setup):
    api, root, _ = setup
    assert api.post('/api/literature/batches', json={'destination': str(root.parent)}).status_code == 422
    batch = upload(api, root)
    route = f"/api/literature/batches/{batch['id']}/pdf?name=another.pdf"
    assert api.post(route, content=PDF).status_code == 409
    assert api.post(route, content=b'bad').status_code == 422
    assert len(api.get(f"/api/literature/batches/{batch['id']}").json()['items']) == 1
    item = batch['items'][0]
    response = api.get(f"/api/literature/batches/{batch['id']}/items/{item['id']}/pdf")
    assert response.content == PDF and "filename*=utf-8''" in response.headers['content-disposition']
    assert not list(intake.directory(batch['id']).glob('*.upload'))


def test_collision_blocks_whole_selected_subset_and_preserves_user_notes(setup):
    api, root, _ = setup
    batch = upload(api, root)
    item = make_ready(batch)
    note = root / 'content/papers/smith2024trade/index.md'
    note.parent.mkdir(); note.write_text('Personal notes')
    result = api.post(f"/api/literature/batches/{batch['id']}/apply", json={'ids': [item['id']]})
    assert result.status_code == 409
    assert note.read_text() == 'Personal notes'
    assert not (root / 'static/papers/smith2024trade.pdf').exists()


def test_source_tamper_and_symlink_destination_are_rejected(setup, tmp_path):
    api, root, _ = setup
    batch = upload(api, root); item = make_ready(batch)
    path = intake.directory(batch['id']) / (item['id'] + '.pdf')
    path.write_bytes(PDF + b'changed')
    assert api.post(f"/api/literature/batches/{batch['id']}/apply", json={'ids': [item['id']]}).status_code == 409
    path.write_bytes(PDF)
    outside = tmp_path / 'outside'; outside.mkdir()
    (root / 'content/papers/smith2024trade').symlink_to(outside)
    _, errors = ingest.prepare_plan(root, [intake.raw_item(batch, item)], intake.now())
    assert any('within content/papers' in e for e in errors)
    assert not list(outside.iterdir())


def test_bibtex_parser_and_draft_validation():
    assert intake.bib_entries(BIB + '\n' + BIB.replace('smith2024trade', 'another'))[0] == BIB
    with pytest.raises(ValueError): intake.bib_entries(BIB[:-1])
    with pytest.raises(ValueError): intake.bib_entries(BIB.replace('smith2024trade', '../escape'))
    item = {'candidates': [intake.candidate(BIB, 'Supplied')], 'pages': 3}
    assert intake.parse_draft(json.dumps(DRAFT), item).match == 'verified'
    for change in [{'candidate_index': 4}, {'read_pages': [4]}, {'notes_markdown': 'Too short'}, {'notes_markdown': '# Invalid heading\n' + DRAFT['notes_markdown']}]:
        with pytest.raises(ValueError): intake.parse_draft(json.dumps({**DRAFT, **change}), item)


def test_prepare_retry_and_corrections_invalidate_previous_draft(setup):
    api, root, _ = setup
    batch = upload(api, root); item = make_ready(batch)
    route = f"/api/literature/batches/{batch['id']}"
    result = api.put(f"{route}/items/{item['id']}", json={'bibtex': BIB, 'query': 'Trade', 'scope': 'paper'})
    assert result.status_code == 200
    assert result.json()['items'][0]['status'] == 'staged'
    assert 'draft' not in result.json()['items'][0]
    result = api.post(route + '/prepare', json={'ids': [item['id']], 'execution': {'backend': 'native_codex'}})
    assert result.json()['items'][0]['status'] == 'queued'
    assert api.put(f"{route}/items/{item['id']}", json={'bibtex': BIB}).status_code == 409
    assert api.post(route + '/prepare', json={'ids': [item['id']], 'execution': {}}).status_code == 409
    assert api.post(route + '/stop').json()['items'][0]['status'] == 'staged'


@pytest.mark.asyncio
async def test_worker_runs_selected_lead_and_persists_grounded_draft(setup, monkeypatch):
    api, root, app = setup
    batch = upload(api, root)
    item = batch['items'][0]
    item['status'] = 'queued'; batch['execution'] = {'backend': 'native_codex', 'profile_policy': {'lead_profile': {'model': 'selected-model'}}}
    intake.save(batch)
    monkeypatch.setattr(intake, 'extract_preview', lambda p: {'pages': 3, 'preview': '[Page 1] Trade and Networks. Smith, Anne. 2024.', 'title_hint': 'Trade and Networks'})
    async def lookup(*args): return [intake.candidate(BIB, 'Supplied BibTeX')], ''
    monkeypatch.setattr(intake, 'lookup', lookup)
    async def execute(run_id, execution, request):
        assert execution.backend == 'native_codex'
        assert execution.profile_policy['lead_profile']['model'] == 'selected-model'
        assert execution.profile_policy['permission_mode'] == 'plan'
        assert not execution.background
        run = app.state.chat_v2_store.get_run(run_id)
        assert run.workspace_root == str(intake.directory(batch['id']))
        return {'result': {'status': 'completed', 'summary': json.dumps(DRAFT)}}
    monkeypatch.setattr(intake, 'execute_agent_run', execute)
    worker = intake.ImportRunner(app)
    await worker.run()
    saved = intake.get_batch(batch['id'])['items'][0]
    assert saved['status'] == 'ready', saved
    assert saved['bibtex'] == BIB and saved['run_id']
    assert not list((root / 'content/papers').iterdir())


@pytest.mark.asyncio
async def test_restart_preserves_ready_work_and_flags_interrupted_item(setup):
    api, root, app = setup
    batch = upload(api, root)
    batch['items'][0]['status'] = 'reading'; intake.save(batch)
    worker = intake.ImportRunner(app)
    worker.recover()
    await worker.task
    assert intake.get_batch(batch['id'])['items'][0]['status'] == 'interrupted'
    assert (intake.directory(batch['id']) / (batch['items'][0]['id'] + '.pdf')).exists()


def test_apply_rolls_back_exclusively_created_files_on_late_collision(setup, monkeypatch):
    api, root, _ = setup
    batch = upload(api, root); item = make_ready(batch)
    plans, errors = ingest.prepare_plan(root, [intake.raw_item(batch, item)], intake.now())
    assert not errors
    note = plans[0]['md_path']; note.parent.mkdir(); note.write_text('Created by another writer')
    with pytest.raises(OSError): ingest.apply_plan(plans, copy=True)
    assert note.read_text() == 'Created by another writer'
    assert not plans[0]['pdf_dest'].exists()

@pytest.mark.asyncio
async def test_crossref_incomplete_doi_falls_back_to_title_and_keeps_supplied_key(monkeypatch):
    import httpx
    requested = []
    def handler(request):
        requested.append(str(request.url))
        if '/transform/' in request.url.path:
            return httpx.Response(200, text=BIB.replace('smith2024trade', 'CrossrefKey'))
        if request.url.path.endswith('/works'):
            return httpx.Response(200, json={'message': {'items': [{'DOI': '10.1234/trade', 'title': ['Trade and Networks'], 'author': [{'family': 'Smith'}]}]}})
        return httpx.Response(200, json={'message': {'DOI': '10.1234/trade', 'title': [], 'author': []}})
    original = httpx.AsyncClient
    monkeypatch.setattr(intake.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    candidates, warning = await intake.lookup({'preview': 'Trade and Networks 10.1234/trade', 'title_hint': 'Trade and Networks'}, BIB)
    assert any('query.bibliographic' in url for url in requested)
    assert len(candidates) == 1 and candidates[0]['bibtex'] == BIB
    assert not warning


def test_ready_subset_is_atomic_when_another_item_collides(setup):
    api, root, _ = setup
    batch = upload(api, root)
    bid = batch['id']
    batch = api.post(f'/api/literature/batches/{bid}/pdf?name=second.pdf', content=PDF+b'2').json()
    make_ready(batch)
    batch['items'][1].update(status='ready', pages=3, bibtex=BIB.replace('smith2024trade', 'second').replace('10.1234/trade', '10.1234/other'), draft=DRAFT)
    intake.save(batch)
    target = root / 'content/papers/second/index.md'; target.parent.mkdir(); target.write_text('Keep me')
    result = api.post(f'/api/literature/batches/{bid}/apply', json={'ids':[x['id'] for x in batch['items']]})
    assert result.status_code == 409
    assert target.read_text() == 'Keep me'
    assert not (root/'static/papers/smith2024trade.pdf').exists()
    assert not (root/'content/papers/smith2024trade').exists()

@pytest.mark.asyncio
async def test_queued_items_resume_after_restart_without_replaying_ready_items(setup, monkeypatch):
    api, root, app = setup
    batch = upload(api, root); make_ready(batch)
    batch = api.post(f"/api/literature/batches/{batch['id']}/pdf?name=queued.pdf", content=PDF+b'queued').json()
    batch['items'][1]['status'] = 'queued'; intake.save(batch)
    worker = intake.ImportRunner(app)
    seen = []
    async def prepare(batch, item):
        seen.append(item['id'])
        worker.update(batch['id'], item['id'], status='needs_review', error='Fixture: no match')
    monkeypatch.setattr(worker, 'prepare', prepare)
    worker.recover(); await worker.task
    saved = intake.get_batch(batch['id'])
    assert seen == [batch['items'][1]['id']]
    assert saved['items'][0]['status'] == 'ready'
    assert saved['items'][1]['status'] == 'needs_review'
