from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dan.search.connector_discovery import (
    classify_query_family,
    discover_connector_hits,
    has_connector_seed_coverage,
    select_query_source_seeds,
)
from dan.search.connectors import (
    build_refresh_queue,
    classify_source_family,
    DiscoveryHit,
    extract_same_site_links,
    normalize_legacy_discovery_hits,
    parse_feed_xml,
    parse_sitemap_xml,
    rank_discovery_hits,
    seed_static_doc_urls,
    source_profile_for_url,
    source_family_ttl_seconds,
)
from dan.search.corpus import BeaconCorpusStore


def test_classify_source_family_prefers_high_value_verticals() -> None:
    assert classify_source_family("https://docs.python.org/3/library/pathlib.html") == "official_docs"
    assert classify_source_family("https://github.com/openai/openai-python") == "github"
    assert classify_source_family("https://arxiv.org/abs/2401.00001") == "research"
    assert classify_source_family("https://www.sec.gov/ixviewer/ix.html") == "regulatory"
    assert classify_source_family("https://investor.example.com/quarterly-results") == "company"


def test_normalize_and_rank_legacy_discovery_hits() -> None:
    payload = {
        "provider": "brave",
        "results": [
            {
                "title": "General blog post",
                "url": "https://example.com/blog",
                "snippet": "General notes",
            },
            {
                "title": "Python docs pathlib",
                "url": "https://docs.python.org/3/library/pathlib.html",
                "snippet": "pathlib documentation",
            },
        ],
    }

    hits = rank_discovery_hits("python pathlib docs", normalize_legacy_discovery_hits(payload))

    assert hits[0].url == "https://docs.python.org/3/library/pathlib.html"
    assert hits[0].source_family == "official_docs"
    assert hits[0].freshness_ttl_seconds == source_family_ttl_seconds("official_docs")
    assert hits[0].provider == "brave"


def test_rank_discovery_hits_prefers_specific_same_site_pages_over_homepage() -> None:
    hits = rank_discovery_hits(
        "pathlib docs",
        [
            DiscoveryHit(
                title="docs.example.com",
                url="https://docs.example.com/",
                snippet="Beacon connector discovery from docs.example.com",
                provider="beacon_connector",
                provider_rank=1,
                canonical_url="https://docs.example.com/",
                source_family="official_docs",
            ),
            DiscoveryHit(
                title="docs.example.com: Pathlib",
                url="https://docs.example.com/library/pathlib.html",
                snippet="Beacon connector discovery from docs.example.com",
                provider="beacon_connector",
                provider_rank=2,
                canonical_url="https://docs.example.com/library/pathlib.html",
                source_family="official_docs",
            ),
        ],
    )

    assert hits[0].url == "https://docs.example.com/library/pathlib.html"


def test_parse_sitemap_xml_extracts_urls() -> None:
    xml_text = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/docs</loc></url>
      <url><loc>https://example.com/blog</loc></url>
    </urlset>
    """

    assert parse_sitemap_xml(xml_text) == [
        "https://example.com/docs",
        "https://example.com/blog",
    ]


def test_feed_and_static_doc_helpers() -> None:
    feed_xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><link href="https://example.com/posts/1" /></entry>
      <entry><link href="https://example.com/posts/2" /></entry>
    </feed>
    """

    assert parse_feed_xml(feed_xml) == [
        "https://example.com/posts/1",
        "https://example.com/posts/2",
    ]
    assert seed_static_doc_urls("https://docs.example.com", ["/guide", "api/index"]) == [
        "https://docs.example.com/guide",
        "https://docs.example.com/api/index",
    ]


def test_extract_same_site_links_filters_external_and_relative_urls() -> None:
    html = """
    <html><body>
      <a href="/investors/q1-results">Q1</a>
      <a href="https://investors.example.com/news/release">News</a>
      <a href="https://other.example.com/ignore">Ignore</a>
      <a href="#fragment">Ignore fragment</a>
    </body></html>
    """

    assert extract_same_site_links("https://investors.example.com", html) == [
        "https://investors.example.com/investors/q1-results",
        "https://investors.example.com/news/release",
    ]


def test_source_profile_for_url_exposes_ingest_policy() -> None:
    profile = source_profile_for_url("https://docs.python.org/3/library/pathlib.html")
    assert profile.source_family == "official_docs"
    assert profile.requires_robots_check is True
    assert "sitemap" in profile.supported_ingest_methods


def test_classify_query_family_prefers_high_value_families() -> None:
    assert classify_query_family("python pathlib docs") == "official_docs"
    assert classify_query_family("openai python github release") == "github_docs_releases"
    assert classify_query_family("microsoft 10-k sec filing") == "regulatory_filings"
    assert classify_query_family("retrieval paper arxiv") == "research_papers"
    assert classify_query_family("latest model updates today") == "open_web_current_fact"


def test_select_query_source_seeds_prefers_allowed_domains() -> None:
    seeds = select_query_source_seeds(
        "pathlib docs",
        allowed_domains=("docs.example.com",),
    )

    assert len(seeds) == 1
    assert seeds[0].base_url == "https://docs.example.com"
    assert seeds[0].source_family == "official_docs"


def test_select_query_source_seeds_can_infer_known_github_repo() -> None:
    seeds = select_query_source_seeds("openai python github release")

    assert any(seed.base_url == "https://github.com/openai/openai-python" for seed in seeds)
    assert has_connector_seed_coverage("openai python github release") is True


def test_select_query_source_seeds_can_use_seed_domains_without_allowed_domains() -> None:
    seeds = select_query_source_seeds(
        "quarterly results",
        seed_domains=("investors.example.com",),
    )

    assert len(seeds) == 1
    assert seeds[0].base_url == "https://investors.example.com"
    assert seeds[0].source_family == "company"


@pytest.mark.asyncio
async def test_discover_connector_hits_parses_allowed_domain_sitemap() -> None:
    async def _fake_fetch(url: str) -> dict[str, str]:
        if url != "https://docs.example.com/sitemap.xml":
            return {"content": ""}
        return {
            "content": """
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://docs.example.com/library/pathlib.html</loc></url>
              <url><loc>https://docs.example.com/library/asyncio.html</loc></url>
            </urlset>
            """,
        }

    hits = await discover_connector_hits(
        "pathlib docs",
        allowed_domains=("docs.example.com",),
        limit=2,
        fetcher=_fake_fetch,
    )

    assert hits[0].url == "https://docs.example.com/library/pathlib.html"
    assert hits[0].provider == "beacon_connector"
    assert hits[0].source_family == "official_docs"


@pytest.mark.asyncio
async def test_discover_connector_hits_can_use_known_github_repo_static_paths() -> None:
    async def _fake_fetch(_url: str) -> dict[str, str]:
        return {"content": ""}

    hits = await discover_connector_hits(
        "openai python github release",
        limit=3,
        fetcher=_fake_fetch,
    )

    assert any(hit.url == "https://github.com/openai/openai-python/releases" for hit in hits)
    assert all(hit.provider == "beacon_connector" for hit in hits)


@pytest.mark.asyncio
async def test_discover_connector_hits_can_expand_seed_domains() -> None:
    async def _fake_fetch(url: str) -> dict[str, str]:
        if url != "https://investors.example.com/sitemap.xml":
            return {"content": ""}
        return {
            "content": """
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://investors.example.com/q1-results</loc></url>
            </urlset>
            """,
        }

    hits = await discover_connector_hits(
        "quarterly results",
        seed_domains=("investors.example.com",),
        limit=2,
        fetcher=_fake_fetch,
    )

    assert any(hit.url == "https://investors.example.com/q1-results" for hit in hits)
    assert all(hit.provider == "beacon_connector" for hit in hits)


@pytest.mark.asyncio
async def test_discover_connector_hits_can_expand_same_site_html_navigation() -> None:
    async def _fake_fetch(url: str) -> dict[str, str]:
        if url == "https://investors.example.com/":
            return {
                "url": url,
                "content": """
                <html><body>
                  <a href="/q2-results">Quarterly Results</a>
                </body></html>
                """,
            }
        return {"url": url, "content": ""}

    hits = await discover_connector_hits(
        "quarterly results",
        seed_domains=("investors.example.com",),
        limit=3,
        fetcher=_fake_fetch,
    )

    assert any(hit.url == "https://investors.example.com/q2-results" for hit in hits)


def test_beacon_corpus_lists_refresh_candidates(tmp_path) -> None:
    store = BeaconCorpusStore(tmp_path / "beacon.db", ttl_seconds=3600)
    stale_fetched_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    store.upsert_document(
        url="https://docs.example.com/stale",
        title="Stale doc",
        content="This page should be refreshed.",
        source_type="web_fetch",
        fetched_at=stale_fetched_at,
    )

    candidates = store.list_refresh_candidates(limit=5)
    assert len(candidates) == 1
    assert candidates[0]["url"] == "https://docs.example.com/stale"
    assert candidates[0]["metadata"]["source_family"] == "official_docs"
    refresh_queue = build_refresh_queue(store, limit=5)
    assert refresh_queue[0]["source_profile"]["requires_robots_check"] is True
    assert refresh_queue[0]["source_profile"]["source_family"] == "official_docs"
