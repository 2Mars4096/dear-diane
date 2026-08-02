"""Tests for shared web-search models and helpers."""

from __future__ import annotations

from dan.server.search_models import (
    CitationRecord,
    SearchResult,
    SearchResultSet,
    canonicalize_search_url,
    parse_search_result_set,
)


def test_canonicalize_search_url_strips_tracking_params():
    assert canonicalize_search_url(
        "https://Example.com/report/?utm_source=newsletter&gclid=abc&id=42#section"
    ) == "https://example.com/report?id=42"


def test_parse_search_result_set_accepts_wrapped_payload():
    payload = {
        "search_result_set": SearchResultSet(
            query="latest dan",
            provider="serper",
            results=[
                SearchResult(
                    index=1,
                    title="DAN update",
                    url="https://example.com/update",
                    snippet="Latest release notes.",
                    provider="serper",
                )
            ],
            grounded_result_count=1,
        ).model_dump(mode="python")
    }

    parsed = parse_search_result_set(payload)

    assert parsed is not None
    assert parsed.query == "latest dan"
    assert parsed.results[0].title == "DAN update"
    assert parsed.grounded_result_count == 1


def test_citation_record_serializes_verified_state():
    record = CitationRecord(
        claim_text="Revenue grew 24% in 2025.",
        source_index=1,
        source_url="https://example.com/report",
        cited_excerpt="Revenue grew 24% in 2025.",
        verified=True,
    )

    assert record.model_dump(mode="python")["verified"] is True
