from fastapi.testclient import TestClient


def test_reader_ocr_roundtrip_and_staleness(tmp_path, monkeypatch):
    from diane.server import app as app_module
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path / "graphs"))
    from diane.server.routers import reader
    monkeypatch.setattr(reader, "resolve_graphs_dir", lambda: str(tmp_path / "graphs"))
    from fastapi import FastAPI
    api = FastAPI(); api.include_router(reader.router)
    client = TestClient(api)
    pdf = tmp_path / "scan.pdf"; pdf.write_bytes(b"%PDF-1.4 fake")
    assert client.get("/api/reader/ocr", params={"path": str(pdf)}).json()["pages"] == []
    pages = [{"page_number": 1, "spans": [{"text": "hi", "left": 0.1, "top": 0.1, "width": 0.2, "height": 0.02, "confidence": 0.9}]}]
    assert client.put("/api/reader/ocr", params={"path": str(pdf)}, json={"pages": pages, "junk": 1}).json()["saved"] == 1
    assert client.get("/api/reader/ocr", params={"path": str(pdf)}).json()["pages"] == pages
    pdf.write_bytes(b"%PDF-1.4 changed bytes")
    body = client.get("/api/reader/ocr", params={"path": str(pdf)}).json()
    assert body["pages"] == [] and body.get("stale") is True
    assert client.get("/api/reader/ocr", params={"path": str(tmp_path / "none.pdf")}).status_code == 404


def test_file_tree_skips_symlinks_that_leave_the_root(tmp_path):
    import asyncio
    from diane.server.routers import misc
    root = tmp_path / "project"; (root / "docs").mkdir(parents=True); (root / "docs/a.md").write_text("x")
    outside = tmp_path / "outside"; (outside / "skill").mkdir(parents=True); (outside / "skill/SKILL.md").write_text("y")
    (root / "pool").mkdir(); (root / "pool/skill").symlink_to(outside / "skill", target_is_directory=True)
    (root / "pool/file-link.md").symlink_to(outside / "skill/SKILL.md")
    result = asyncio.run(misc.list_workspace_file_tree(root_path=str(root)))
    names = {entry["relative_path"] for entry in result["entries"]}
    assert "docs/a.md" in names and "pool" in names
    assert not any("skill" in name or "file-link" in name for name in names)
