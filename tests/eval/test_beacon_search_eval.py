from __future__ import annotations

import pytest

from tests.eval.beacon_search_eval import (
    BEACON_PROVIDER_INDEPENDENCE_QUERY_PACK,
    BEACON_PROVIDER_INDEPENDENCE_THRESHOLDS,
    BeaconSearchEvalCase,
    compare_modes,
    evaluate_cutover_gate,
    score_search_payload,
    summarize_eval_rows,
)


def test_score_search_payload_tracks_corpus_and_external_usage() -> None:
    case = BeaconSearchEvalCase(
        query="beacon corpus",
        expected_terms=("beacon", "corpus"),
        expected_url_substrings=("docs.example.com",),
    )
    payload = {
        "results": [
            {
                "title": "Beacon docs",
                "url": "https://docs.example.com/beacon",
                "snippet": "Beacon corpus guide",
                "document_id": "doc_1",
                "chunk_id": "doc_1__chunk_0",
                "evidence_source": "corpus",
                "freshness_state": "fresh",
            }
        ],
        "search_broker": {
            "used_legacy": False,
            "discovery_classification": "corpus_only",
            "discovery_source_counts": {"corpus": 1, "connector": 0, "provider": 0},
        },
        "beacon_corpus": {
            "indexed_documents": 1,
            "discovery_classification": "corpus_only",
            "discovery_source_counts": {"corpus": 1, "connector": 0, "provider": 0},
        },
    }

    row = score_search_payload(case, payload, mode="beacon")

    assert row.term_recall == pytest.approx(1.0)
    assert row.url_recall == pytest.approx(1.0)
    assert row.citation_support == pytest.approx(1.0)
    assert row.corpus_hit_rate == pytest.approx(1.0)
    assert row.external_call_rate == pytest.approx(0.0)
    assert row.cost_proxy == pytest.approx(0.0)
    assert row.discovery_classification == "corpus_only"
    assert row.discovery_source_counts == {"corpus": 1, "connector": 0, "provider": 0}


@pytest.mark.asyncio
async def test_compare_modes_and_summary() -> None:
    case = BeaconSearchEvalCase(query="beacon corpus", expected_terms=("beacon",))

    async def _runner(mode: str, query: str) -> dict:
        assert query == "beacon corpus"
        if mode == "legacy":
            return {
                "results": [{"title": "External", "url": "https://example.com", "snippet": "beacon"}],
                "search_broker": {
                    "used_legacy": True,
                    "discovery_classification": "provider_only",
                    "discovery_source_counts": {"corpus": 0, "connector": 0, "provider": 1},
                },
            }
        return {
            "results": [
                {
                    "title": "Beacon docs",
                    "url": "https://docs.example.com/beacon",
                    "snippet": "beacon corpus",
                    "document_id": "doc_1",
                    "chunk_id": "doc_1__chunk_0",
                    "evidence_source": "corpus",
                    "freshness_state": "fresh",
                }
            ],
            "search_broker": {
                "used_legacy": False,
                "discovery_classification": "corpus_only",
                "discovery_source_counts": {"corpus": 1, "connector": 0, "provider": 0},
            },
            "beacon_corpus": {
                "indexed_documents": 1,
                "discovery_classification": "corpus_only",
                "discovery_source_counts": {"corpus": 1, "connector": 0, "provider": 0},
            },
        }

    rows = await compare_modes(case, runner=_runner)
    summary = summarize_eval_rows(rows)

    assert {row.mode for row in rows} == {"legacy", "hybrid", "beacon"}
    assert summary["legacy"]["avg_external_call_rate"] == pytest.approx(1.0)
    assert summary["beacon"]["avg_corpus_hit_rate"] == pytest.approx(1.0)
    assert summary["legacy"]["provider_only_rate"] == pytest.approx(1.0)
    assert summary["beacon"]["corpus_only_rate"] == pytest.approx(1.0)
    gate = evaluate_cutover_gate(summary, max_external_call_rate=0.5)
    assert gate["passed"] is True


def test_provider_independence_query_pack_and_thresholds_are_explicit() -> None:
    families = {case.query_family for case in BEACON_PROVIDER_INDEPENDENCE_QUERY_PACK}

    assert families == {
        "official_docs",
        "github_docs_releases",
        "company_ir_news",
        "regulatory_filings",
        "research_papers",
        "open_web_current_fact",
    }
    assert BEACON_PROVIDER_INDEPENDENCE_THRESHOLDS["min_connector_first_rate"] > 0.0
    assert BEACON_PROVIDER_INDEPENDENCE_THRESHOLDS["max_provider_only_rate"] < 1.0


def test_evaluate_cutover_gate_can_enforce_provider_independence_thresholds() -> None:
    summary = {
        "legacy": {
            "avg_term_recall": 1.0,
            "avg_citation_support": 0.5,
            "avg_freshness_score": 0.5,
            "avg_external_call_rate": 1.0,
        },
        "beacon": {
            "avg_term_recall": 1.0,
            "avg_citation_support": 0.8,
            "avg_freshness_score": 0.9,
            "avg_external_call_rate": 0.2,
            "corpus_only_rate": 0.3,
            "connector_first_rate": 0.4,
            "provider_fallback_rate": 0.2,
            "provider_only_rate": 0.1,
            "target_discovery_match_rate": 0.8,
        },
    }

    gate = evaluate_cutover_gate(summary, **BEACON_PROVIDER_INDEPENDENCE_THRESHOLDS)

    assert gate["passed"] is True
