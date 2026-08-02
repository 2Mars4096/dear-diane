from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from dan.tools.web_search import _finalize_search_result, web_search


def test_web_search_finalize_prefers_primary_sources_over_aggregators() -> None:
    payload = _finalize_search_result(
        {
            "results": [
                {
                    "title": "Yahoo Finance quote",
                    "url": "https://finance.yahoo.com/quote/KOL",
                    "snippet": "Retail quote page.",
                },
                {
                    "title": "SEC notice",
                    "url": "https://www.sec.gov/Archives/edgar/data/example",
                    "snippet": "Primary filing.",
                },
                {
                    "title": "ETFDB profile",
                    "url": "https://etfdb.com/etf/COAL/",
                    "snippet": "Secondary ETF summary.",
                },
            ]
        }
    )

    urls = [row["url"] for row in payload["results"]]
    tiers = [row["authority_tier"] for row in payload["results"]]

    assert urls[0].startswith("https://www.sec.gov/")
    assert tiers[0] == "primary"
    assert tiers[-1] == "aggregator"


def test_web_search_finalize_marks_docs_pages_as_primary() -> None:
    payload = _finalize_search_result(
        {
            "results": [
                {
                    "title": "Community summary",
                    "url": "https://example.com/blog/openai-api-summary",
                    "snippet": "Third-party summary.",
                },
                {
                    "title": "OpenAI docs",
                    "url": "https://platform.openai.com/docs/api-reference",
                    "snippet": "Official API documentation.",
                },
            ]
        }
    )

    assert payload["results"][0]["url"] == "https://platform.openai.com/docs/api-reference"
    assert payload["results"][0]["authority_tier"] == "primary"
    assert payload["results"][0]["authority_reason"]


@pytest.mark.asyncio
async def test_web_search_thorough_mode_fetches_grounded_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_execute_search(
        query: str,
        num_results: int,
        *,
        search_depth: str,
        allowed_domains: list[str],
        blocked_domains: list[str],
        location: dict[str, str],
        multi_provider: bool,
        max_provider_searches: int,
    ) -> dict[str, object]:
        assert query == "current ETF status basic"
        assert num_results == 3
        assert search_depth == "thorough"
        assert allowed_domains == []
        assert blocked_domains == []
        assert location == {}
        assert multi_provider is False
        assert max_provider_searches == 4
        return {
            "results": [
                {
                    "title": "SEC filing",
                    "url": "https://www.sec.gov/Archives/example",
                    "snippet": "Primary filing.",
                    "authority_tier": "primary",
                    "authority_reason": "government or regulatory domain",
                },
                {
                    "title": "Issuer page",
                    "url": "https://issuer.example.com/investors/fund",
                    "snippet": "Issuer summary.",
                    "authority_tier": "primary",
                    "authority_reason": "issuer investor-relations style page",
                },
            ],
            "count": 2,
            "provider": "fake-search",
            "providers": ["fake-search"],
            "provider_failures": [],
            "cache_hit": False,
        }

    fake_fetch = AsyncMock(
        side_effect=[
            {
                "url": "https://www.sec.gov/Archives/example",
                "content": "Primary page content.",
                "status_code": 200,
                "content_type": "text/html",
                "fetch_via": "http",
                "browser_fallback_used": False,
            },
            {
                "url": "https://issuer.example.com/investors/fund",
                "content": "Issuer page content.",
                "status_code": 200,
                "content_type": "text/html",
                "fetch_via": "browser",
                "browser_fallback_used": True,
            },
        ]
    )

    monkeypatch.setattr("dan.tools.web_search._execute_search", _fake_execute_search)
    monkeypatch.setattr("dan.tools.web_fetch.web_fetch", fake_fetch)

    payload = await web_search("current ETF status basic", num_results=3, search_depth="thorough")

    assert payload["fetch_content_requested"] is True
    assert payload["grounded_result_count"] == 2
    assert payload["browser_fallback_count"] == 1
    assert len(payload["fetched_results"]) == 2
    assert payload["fetched_results"][0]["ok"] is True
    assert payload["fetched_results"][1]["browser_fallback_used"] is True
    assert fake_fetch.await_count == 2


@pytest.mark.asyncio
async def test_web_search_thorough_mode_fetches_grounded_results_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_execute_search(
        query: str,
        num_results: int,
        *,
        search_depth: str,
        allowed_domains: list[str],
        blocked_domains: list[str],
        location: dict[str, str],
        multi_provider: bool,
        max_provider_searches: int,
    ) -> dict[str, object]:
        return {
            "results": [
                {
                    "title": "Primary A",
                    "url": "https://primary.example.com/a",
                    "snippet": "A",
                    "authority_tier": "primary",
                    "authority_reason": "primary",
                },
                {
                    "title": "Primary B",
                    "url": "https://primary.example.com/b",
                    "snippet": "B",
                    "authority_tier": "primary",
                    "authority_reason": "primary",
                },
            ],
            "count": 2,
            "provider": "fake-search",
            "providers": ["fake-search"],
            "provider_failures": [],
            "cache_hit": False,
        }

    in_flight = 0
    max_in_flight = 0

    async def _fake_fetch(*, url: str, timeout: int = 0, browser_fallback: bool = False):
        nonlocal in_flight, max_in_flight
        _ = timeout, browser_fallback
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await asyncio.sleep(0.01)
            return {
                "url": url,
                "content": f"Fetched {url}",
                "status_code": 200,
                "content_type": "text/html",
                "fetch_via": "http",
                "browser_fallback_used": False,
            }
        finally:
            in_flight -= 1

    monkeypatch.setattr("dan.tools.web_search._execute_search", _fake_execute_search)
    monkeypatch.setattr("dan.tools.web_fetch.web_fetch", _fake_fetch)

    payload = await web_search("current ETF status concurrent", num_results=2, search_depth="thorough")

    assert payload["grounded_result_count"] == 2
    assert max_in_flight == 2


@pytest.mark.asyncio
async def test_web_search_thorough_mode_records_grounded_fetch_timeout_as_failed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_execute_search(
        query: str,
        num_results: int,
        *,
        search_depth: str,
        allowed_domains: list[str],
        blocked_domains: list[str],
        location: dict[str, str],
        multi_provider: bool,
        max_provider_searches: int,
    ) -> dict[str, object]:
        return {
            "results": [
                {
                    "title": "Primary A",
                    "url": "https://primary.example.com/a",
                    "snippet": "A",
                    "authority_tier": "primary",
                    "authority_reason": "primary",
                },
                {
                    "title": "Primary B",
                    "url": "https://primary.example.com/b",
                    "snippet": "B",
                    "authority_tier": "primary",
                    "authority_reason": "primary",
                },
            ],
            "count": 2,
            "provider": "fake-search",
            "providers": ["fake-search"],
            "provider_failures": [],
            "cache_hit": False,
        }

    async def _fake_fetch(*, url: str, timeout: int = 0, browser_fallback: bool = False):
        _ = timeout, browser_fallback
        if url.endswith("/a"):
            await asyncio.sleep(0.02)
            raise TimeoutError("grounded fetch timeout")
        return {
            "url": url,
            "content": f"Fetched {url}",
            "status_code": 200,
            "content_type": "text/html",
            "fetch_via": "http",
            "browser_fallback_used": False,
        }

    monkeypatch.setattr("dan.tools.web_search._execute_search", _fake_execute_search)
    monkeypatch.setattr("dan.tools.web_fetch.web_fetch", _fake_fetch)

    payload = await web_search("current ETF status timeout", num_results=2, search_depth="thorough")

    assert payload["grounded_result_count"] == 1
    assert payload["fetched_results"][0]["ok"] is False
    assert "TimeoutError" in payload["fetched_results"][0]["error"]
    assert payload["fetched_results"][1]["ok"] is True


@pytest.mark.asyncio
async def test_web_search_thorough_mode_reuses_cached_grounded_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    search_calls = 0
    fetch_calls = 0

    async def _fake_execute_search(
        query: str,
        num_results: int,
        *,
        search_depth: str,
        allowed_domains: list[str],
        blocked_domains: list[str],
        location: dict[str, str],
        multi_provider: bool,
        max_provider_searches: int,
    ) -> dict[str, object]:
        nonlocal search_calls
        search_calls += 1
        return {
            "results": [
                {
                    "title": "SEC filing",
                    "url": "https://www.sec.gov/Archives/example",
                    "snippet": "Primary filing.",
                    "authority_tier": "primary",
                    "authority_reason": "government or regulatory domain",
                },
            ],
            "count": 1,
            "provider": "fake-search",
            "providers": ["fake-search"],
            "provider_failures": [],
            "cache_hit": False,
        }

    async def _fake_fetch(*, url: str, timeout: int = 0, browser_fallback: bool = False):
        nonlocal fetch_calls
        _ = timeout, browser_fallback
        fetch_calls += 1
        return {
            "url": url,
            "content": "Primary page content.",
            "status_code": 200,
            "content_type": "text/html",
            "fetch_via": "http",
            "browser_fallback_used": False,
        }

    monkeypatch.setattr("dan.tools.web_search._execute_search", _fake_execute_search)
    monkeypatch.setattr("dan.tools.web_fetch.web_fetch", _fake_fetch)

    first = await web_search("current ETF status cached", num_results=1, search_depth="thorough")
    second = await web_search("current ETF status cached", num_results=1, search_depth="thorough")

    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert search_calls == 1
    assert fetch_calls == 1
    assert second["grounded_result_count"] == 1


@pytest.mark.asyncio
async def test_web_search_can_fetch_direct_url_via_same_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_fetch = AsyncMock(
        return_value={
            "url": "https://example.com/source",
            "content": "Fetched body text.",
            "status_code": 200,
            "content_type": "text/html",
            "fetch_via": "http",
            "browser_fallback_used": False,
            "cache_hit": False,
            "shared_inflight": False,
        }
    )
    monkeypatch.setattr("dan.tools.web_fetch.web_fetch", fake_fetch)

    payload = await web_search(url="https://example.com/source")

    assert payload["provider"] == "direct_fetch"
    assert payload["result_kind"] == "direct_fetch"
    assert payload["fetch_content_requested"] is True
    assert payload["grounded_result_count"] == 1
    assert payload["results"][0]["url"] == "https://example.com/source"
    assert payload["fetched_results"][0]["excerpt"] == "Fetched body text."
    fake_fetch.assert_awaited_once_with(
        url="https://example.com/source",
        browser_fallback=False,
    )
