from fastapi.testclient import TestClient


def test_reader_ocr_roundtrip_and_staleness(tmp_path, monkeypatch):
    from dan.server import app as app_module
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path / "graphs"))
    from dan.server.routers import reader
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
