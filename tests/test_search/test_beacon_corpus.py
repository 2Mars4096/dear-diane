from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from dan.search import (
    BeaconCorpusStore,
    SearchBrokerRequest,
    get_default_beacon_corpus_store,
    reset_default_beacon_corpus_store,
)
from dan.search.beacon import execute_beacon_search
from dan.server.capabilities.web import handle_web_fetch, handle_web_search


def test_beacon_corpus_upsert_and_search_round_trip(tmp_path) -> None:
    store = BeaconCorpusStore(tmp_path / "beacon.db", chunk_size=80, chunk_overlap=10, ttl_seconds=3600)

    record = store.upsert_document(
        url="https://example.com/post?utm_source=newsletter",
        title="Beacon Launch Notes",
        content="Beacon search stores grounded evidence and reuses it for later search queries.",
        query="beacon grounded evidence",
        source_type="web_fetch",
    )

    assert record is not None
    assert record.canonical_url == "https://example.com/post"
    hits = store.search("grounded beacon evidence", limit=3)
    assert len(hits) == 1
    assert hits[0].document_id == record.document_id
    assert hits[0].chunk_id == record.primary_chunk_id
    assert hits[0].canonical_url == record.canonical_url


@pytest.mark.asyncio
async def test_execute_beacon_search_prefers_corpus_then_fills_from_legacy(tmp_path) -> None:
    store = BeaconCorpusStore(tmp_path / "beacon.db", chunk_size=80, chunk_overlap=10, ttl_seconds=3600)
    store.upsert_document(
        url="https://knowledge.example.com/beacon",
        title="Beacon Docs",
        content="Beacon can answer repeated web questions from its own stored corpus.",
        query="beacon stored corpus",
        source_type="web_search_fetch",
    )
    legacy_executor = AsyncMock(
        return_value={
            "results": [
                {
                    "title": "External Result",
                    "url": "https://external.example.com/post",
                    "snippet": "Fresh external discovery result.",
                    "provider": "brave",
                }
            ],
            "provider": "brave",
            "providers": ["brave"],
        }
    )

    payload = await execute_beacon_search(
        SearchBrokerRequest(query="beacon corpus", num_results=3),
        legacy_executor=legacy_executor,
        corpus_store=store,
    )

    assert [item["evidence_source"] for item in payload["results"]] == [
        "corpus",
        "connector_discovery",
        "external_discovery",
    ]
    assert payload["providers"] == ["beacon_corpus", "beacon_connector", "brave"]
    assert payload["discovery_classification"] == "provider_fallback"
    assert payload["discovery_source_counts"] == {"corpus": 1, "connector": 1, "provider": 1}
    assert payload["beacon_corpus"]["discovery_classification"] == "provider_fallback"
    assert payload["_search_broker_path"] == "beacon_corpus_plus_connector_plus_legacy"
    assert payload["_search_broker_used_legacy"] is True


@pytest.mark.asyncio
async def test_execute_beacon_search_can_use_connector_discovery_before_legacy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    store = BeaconCorpusStore(tmp_path / "beacon.db", chunk_size=80, chunk_overlap=10, ttl_seconds=3600)
    legacy_executor = AsyncMock(
        return_value={
            "results": [
                {
                    "title": "External Result",
                    "url": "https://external.example.com/post",
                    "snippet": "Fresh external discovery result.",
                    "provider": "brave",
                }
            ],
            "provider": "brave",
            "providers": ["brave"],
        }
    )

    async def _fake_fetch(url: str, **_kwargs):
        if url == "https://docs.example.com/sitemap.xml":
            return {
                "content": """
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url><loc>https://docs.example.com/library/pathlib.html</loc></url>
                </urlset>
                """,
            }
        return {"content": ""}

    monkeypatch.setattr("dan.search.connector_discovery._default_fetcher", _fake_fetch)

    payload = await execute_beacon_search(
        SearchBrokerRequest(
            query="pathlib docs",
            num_results=1,
            allowed_domains=("docs.example.com",),
        ),
        legacy_executor=legacy_executor,
        corpus_store=store,
    )

    assert payload["provider"] == "beacon_connector"
    assert payload["providers"] == ["beacon_connector"]
    assert payload["results"][0]["url"] == "https://docs.example.com/library/pathlib.html"
    assert payload["results"][0]["evidence_source"] == "connector_discovery"
    assert payload["discovery_classification"] == "connector_first"
    assert payload["discovery_source_counts"] == {"corpus": 0, "connector": 1, "provider": 0}
    assert payload["beacon_corpus"]["connector_fill_count"] == 1
    assert payload["_search_broker_path"] == "beacon_connector"
    assert payload["_search_broker_used_legacy"] is False
    legacy_executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_beacon_search_can_disable_provider_fallback_for_connector_owned_queries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_BEACON_PROVIDER_POLICY", "connectors_only")
    store = BeaconCorpusStore(tmp_path / "beacon.db", chunk_size=80, chunk_overlap=10, ttl_seconds=3600)
    legacy_executor = AsyncMock(
        return_value={
            "results": [
                {
                    "title": "External Result",
                    "url": "https://external.example.com/post",
                    "snippet": "Fresh external discovery result.",
                    "provider": "brave",
                }
            ],
            "provider": "brave",
            "providers": ["brave"],
        }
    )

    payload = await execute_beacon_search(
        SearchBrokerRequest(
            query="openai python github release",
            num_results=2,
        ),
        legacy_executor=legacy_executor,
        corpus_store=store,
    )

    assert payload["query_family"] == "github_docs_releases"
    assert payload["provider_policy"] == "connectors_only"
    assert payload["provider_fallback_allowed"] is False
    assert payload["provider"] == "beacon_connector"
    assert payload["providers"] == ["beacon_connector"]
    assert payload["discovery_classification"] == "connector_first"
    assert payload["_search_broker_path"] == "beacon_connector"
    assert payload["_search_broker_used_legacy"] is False
    legacy_executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_beacon_search_can_expand_corpus_seed_domains_without_allowed_domains(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_BEACON_PROVIDER_POLICY", "connectors_only")
    store = BeaconCorpusStore(tmp_path / "beacon.db", chunk_size=80, chunk_overlap=10, ttl_seconds=3600)
    store.upsert_document(
        url="https://investors.example.com/overview",
        title="Investor Overview",
        content="Quarterly revenue results and investor updates.",
        query="quarterly results",
        source_type="web_search_fetch",
    )
    legacy_executor = AsyncMock(
        return_value={
            "results": [
                {
                    "title": "External Result",
                    "url": "https://external.example.com/post",
                    "snippet": "Fresh external discovery result.",
                    "provider": "brave",
                }
            ],
            "provider": "brave",
            "providers": ["brave"],
        }
    )

    async def _fake_fetch(url: str, **_kwargs):
        if url == "https://investors.example.com/sitemap.xml":
            return {
                "content": """
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url><loc>https://investors.example.com/q1-results</loc></url>
                </urlset>
                """,
            }
        return {"content": ""}

    monkeypatch.setattr("dan.search.connector_discovery._default_fetcher", _fake_fetch)

    payload = await execute_beacon_search(
        SearchBrokerRequest(
            query="quarterly results",
            num_results=2,
        ),
        legacy_executor=legacy_executor,
        corpus_store=store,
    )

    assert payload["provider"] == "beacon_corpus"
    assert payload["seed_domains"] == ["investors.example.com"]
    assert payload["providers"] == ["beacon_corpus", "beacon_connector"]
    assert any(item["evidence_source"] == "connector_discovery" for item in payload["results"])
    assert payload["discovery_source_counts"] == {"corpus": 1, "connector": 1, "provider": 0}
    assert payload["_search_broker_path"] == "beacon_corpus_plus_connector"
    assert payload["_search_broker_used_legacy"] is False
    legacy_executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_web_search_persists_grounded_fetches_into_beacon_corpus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_BEACON_SEARCH_DB", str(tmp_path / "beacon.db"))
    reset_default_beacon_corpus_store()
    fake_search = {
        "results": [{"title": "Report", "url": "https://example.com/report", "snippet": "Quarterly revenue update"}],
        "provider": "brave",
    }
    fake_fetch = {
        "url": "https://example.com/report",
        "content": "Quarterly revenue grew 12 percent year over year.",
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
    }

    with (
        patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_search),
        patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, return_value=fake_fetch),
    ):
        result = await handle_web_search(
            {"query": "quarterly revenue", "num_results": 1, "fetch_content": True},
            None,
        )

    assert result.success
    assert result.data["beacon_corpus"]["saved_documents"] == 1
    assert result.data["results"][0]["document_id"]
    hits = get_default_beacon_corpus_store().search("quarterly revenue", limit=1)
    assert hits
    assert hits[0].document_id == result.data["results"][0]["document_id"]
    reset_default_beacon_corpus_store()


@pytest.mark.asyncio
async def test_handle_web_search_beacon_mode_reads_from_corpus_before_external_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_BEACON_SEARCH_DB", str(tmp_path / "beacon.db"))
    monkeypatch.setenv("DAN_SEARCH_BROKER_MODE", "beacon")
    reset_default_beacon_corpus_store()
    record = get_default_beacon_corpus_store().upsert_document(
        url="https://docs.example.com/beacon",
        title="Beacon Docs",
        content="Beacon can answer repeated questions from its local stored corpus.",
        query="beacon local corpus",
        source_type="web_fetch",
    )
    assert record is not None

    with patch("dan.tools.web_search.web_search", new_callable=AsyncMock) as mock_search:
        result = await handle_web_search({"query": "beacon local corpus", "num_results": 1}, None)

    assert result.success
    assert result.data["provider"] == "beacon_corpus"
    assert result.data["search_broker"]["effective_mode"] == "beacon"
    assert result.data["search_broker"]["discovery_classification"] == "corpus_only"
    assert result.data["search_broker"]["discovery_source_counts"] == {
        "corpus": 1,
        "connector": 0,
        "provider": 0,
    }
    assert result.data["beacon_corpus"]["indexed_documents"] == 1
    assert result.data["beacon_corpus"]["discovery_classification"] == "corpus_only"
    assert result.data["results"][0]["document_id"] == record.document_id
    assert result.data["results"][0]["evidence_source"] == "corpus"
    mock_search.assert_not_awaited()
    reset_default_beacon_corpus_store()


@pytest.mark.asyncio
async def test_handle_web_search_beacon_mode_can_use_connector_discovery_with_allowed_domain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_BEACON_SEARCH_DB", str(tmp_path / "beacon.db"))
    monkeypatch.setenv("DAN_SEARCH_BROKER_MODE", "beacon")
    reset_default_beacon_corpus_store()

    async def _fake_fetch(url: str, **_kwargs):
        if url == "https://docs.example.com/sitemap.xml":
            return {
                "content": """
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url><loc>https://docs.example.com/library/pathlib.html</loc></url>
                </urlset>
                """,
            }
        return {"content": ""}

    with (
        patch("dan.tools.web_search.web_search", new_callable=AsyncMock) as mock_search,
        patch("dan.search.connector_discovery._default_fetcher", new_callable=AsyncMock, side_effect=_fake_fetch),
    ):
        result = await handle_web_search(
            {
                "query": "pathlib docs",
                "num_results": 1,
                "allowed_domains": ["docs.example.com"],
            },
            None,
        )

    assert result.success
    assert result.data["provider"] == "beacon_connector"
    assert result.data["search_broker"]["effective_mode"] == "beacon"
    assert result.data["search_broker"]["discovery_classification"] == "connector_first"
    assert result.data["search_broker"]["discovery_source_counts"] == {
        "corpus": 0,
        "connector": 1,
        "provider": 0,
    }
    assert result.data["beacon_corpus"]["discovery_classification"] == "connector_first"
    assert result.data["results"][0]["evidence_source"] == "connector_discovery"
    mock_search.assert_not_awaited()
    reset_default_beacon_corpus_store()


@pytest.mark.asyncio
async def test_handle_web_search_surfaces_provider_policy_for_connector_owned_queries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_BEACON_SEARCH_DB", str(tmp_path / "beacon.db"))
    monkeypatch.setenv("DAN_SEARCH_BROKER_MODE", "beacon")
    monkeypatch.setenv("DAN_BEACON_PROVIDER_POLICY", "connectors_only")
    reset_default_beacon_corpus_store()

    with patch("dan.tools.web_search.web_search", new_callable=AsyncMock) as mock_search:
        result = await handle_web_search(
            {
                "query": "openai python github release",
                "num_results": 2,
            },
            None,
        )

    assert result.success
    assert result.data["provider"] == "beacon_connector"
    assert result.data["search_broker"]["query_family"] == "github_docs_releases"
    assert result.data["search_broker"]["provider_policy"] == "connectors_only"
    assert result.data["search_broker"]["provider_fallback_allowed"] is False
    assert result.data["search_result_set"]["query_family"] == "github_docs_releases"
    assert result.data["search_result_set"]["provider_policy"] == "connectors_only"
    assert result.data["search_result_set"]["provider_fallback_allowed"] is False
    mock_search.assert_not_awaited()
    reset_default_beacon_corpus_store()


@pytest.mark.asyncio
async def test_handle_web_search_surfaces_seed_domains_for_corpus_expansion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_BEACON_SEARCH_DB", str(tmp_path / "beacon.db"))
    monkeypatch.setenv("DAN_SEARCH_BROKER_MODE", "beacon")
    monkeypatch.setenv("DAN_BEACON_PROVIDER_POLICY", "connectors_only")
    reset_default_beacon_corpus_store()
    record = get_default_beacon_corpus_store().upsert_document(
        url="https://investors.example.com/overview",
        title="Investor Overview",
        content="Quarterly revenue results and investor updates.",
        query="quarterly results",
        source_type="web_fetch",
    )
    assert record is not None

    async def _fake_fetch(url: str, **_kwargs):
        if url == "https://investors.example.com/sitemap.xml":
            return {
                "content": """
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url><loc>https://investors.example.com/q1-results</loc></url>
                </urlset>
                """,
            }
        return {"content": ""}

    with (
        patch("dan.tools.web_search.web_search", new_callable=AsyncMock) as mock_search,
        patch("dan.search.connector_discovery._default_fetcher", new_callable=AsyncMock, side_effect=_fake_fetch),
    ):
        result = await handle_web_search(
            {
                "query": "quarterly results",
                "num_results": 2,
            },
            None,
        )

    assert result.success
    assert result.data["provider"] == "beacon_corpus"
    assert result.data["beacon_corpus"]["seed_domains"] == ["investors.example.com"]
    assert result.data["search_broker"]["query_family"] == "company_ir_news"
    assert any(item["evidence_source"] == "connector_discovery" for item in result.data["results"])
    mock_search.assert_not_awaited()
    reset_default_beacon_corpus_store()


@pytest.mark.asyncio
async def test_handle_web_search_beacon_mode_can_use_semantic_corpus_hits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_BEACON_SEARCH_DB", str(tmp_path / "beacon.db"))
    monkeypatch.setenv("DAN_SEARCH_BROKER_MODE", "beacon")
    monkeypatch.setenv("DAN_BEACON_ENABLE_EMBEDDINGS", "1")
    reset_default_beacon_corpus_store()
    record = get_default_beacon_corpus_store().upsert_document(
        url="https://docs.example.com/broker-boundary",
        title="Broker Boundary",
        content="Adapter seam for provider broker routing.",
        query="adapter seam",
        source_type="web_fetch",
    )
    assert record is not None

    class _FakeEmbedder:
        async def embed(self, texts: list[str], _model: str):
            vectors = []
            for text in texts:
                lowered = text.lower()
                vectors.append([
                    1.0 if "broker" in lowered else 0.0,
                    1.0 if "provider" in lowered else 0.0,
                    1.0 if "adapter" in lowered else 0.0,
                ])
            return SimpleNamespace(vectors=vectors)

    async def _fake_resolver():
        return _FakeEmbedder(), "fake-embedding-model"

    monkeypatch.setattr("dan.search.corpus._resolve_beacon_embedding_provider", _fake_resolver)

    with patch("dan.tools.web_search.web_search", new_callable=AsyncMock) as mock_search:
        result = await handle_web_search({"query": "provider broker", "num_results": 1}, None)

    assert result.success
    assert result.data["results"][0]["document_id"] == record.document_id
    assert result.data["results"][0]["evidence_source"] == "corpus"
    assert result.data["results"][0]["ranking_features"]["semantic"] > 0.0
    mock_search.assert_not_awaited()
    reset_default_beacon_corpus_store()


@pytest.mark.asyncio
async def test_handle_web_fetch_persists_into_beacon_corpus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DAN_BEACON_SEARCH_DB", str(tmp_path / "beacon.db"))
    reset_default_beacon_corpus_store()
    fake_fetch = {
        "url": "https://example.com/page",
        "content": "Beacon fetch persistence stores fetched pages in the local corpus.",
        "status_code": 200,
        "content_type": "text/html; charset=utf-8",
    }

    with patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, return_value=fake_fetch):
        result = await handle_web_fetch({"url": "https://example.com/page"}, None)

    assert result.success
    assert result.data["beacon_corpus"]["saved"] is True
    hits = get_default_beacon_corpus_store().search("fetch persistence", limit=1)
    assert hits
    assert hits[0].document_id == result.data["document_id"]
    reset_default_beacon_corpus_store()
