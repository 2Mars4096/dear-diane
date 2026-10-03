"""Server startup loads the same .env configuration used by live model requests."""
from dotenv import load_dotenv
from fastapi.testclient import TestClient


def test_catalog_reads_dotenv_gateway_credentials_without_returning_them(monkeypatch, tmp_path):
    from diane.server import app as server
    from diane.native_workers import catalog
    env_file = tmp_path / ".env"
    env_file.write_text("DAN_LLM_API_KEY=test-dotenv-secret\nDAN_LLM_BASE_URL=https://openrouter.ai/api/v1\n")
    for name in ("DAN_LLM_API_KEY", "DAN_LLM_BASE_URL", "DAN_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DAN_GRAPHS_DIR", str(tmp_path / "graphs"))
    monkeypatch.setattr(server, "load_env", lambda: load_dotenv(env_file))
    monkeypatch.setattr(catalog, "binary", lambda runtime: None)
    monkeypatch.setattr(catalog, "accounts", lambda: {runtime: {} for runtime in catalog.RUNTIMES})
    with TestClient(server.create_app()) as client:
        response = client.get("/api/native-workers/catalog")
    assert response.status_code == 200
    assert response.json()["openrouter_configured"] is True
    assert "test-dotenv-secret" not in response.text
