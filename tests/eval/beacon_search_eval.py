"""Beacon Search eval helpers for mode comparison and cutover gating."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Awaitable, Callable

from dan.search import classify_discovery_source_counts, normalize_discovery_source_counts


@dataclass(frozen=True)
class BeaconSearchEvalCase:
    query: str
    query_family: str = "general"
    expected_terms: tuple[str, ...] = ()
    expected_url_substrings: tuple[str, ...] = ()
    target_discovery_classification: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class BeaconSearchEvalRow:
    mode: str
    query: str
    query_family: str
    result_count: int
    term_recall: float
    url_recall: float
    citation_support: float
    corpus_hit_rate: float
    freshness_score: float
    external_call_rate: float
    duration_ms: float
    cost_proxy: float
    used_legacy: bool
    discovery_classification: str
    discovery_source_counts: dict[str, int] = field(default_factory=dict)
    target_discovery_match: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


BEACON_PROVIDER_INDEPENDENCE_QUERY_PACK: tuple[BeaconSearchEvalCase, ...] = (
    BeaconSearchEvalCase(
        query="python pathlib relative_to docs",
        query_family="official_docs",
        expected_terms=("pathlib", "relative_to"),
        expected_url_substrings=("docs.python.org",),
        target_discovery_classification="connector_first",
        notes="Static docs/reference queries should migrate to first-party doc discovery.",
    ),
    BeaconSearchEvalCase(
        query="openai python responses api github release",
        query_family="github_docs_releases",
        expected_terms=("responses", "python"),
        expected_url_substrings=("github.com",),
        target_discovery_classification="connector_first",
        notes="GitHub releases/docs should come from a dedicated GitHub connector.",
    ),
    BeaconSearchEvalCase(
        query="firefly aerospace investor relations results",
        query_family="company_ir_news",
        expected_terms=("investor", "results"),
        expected_url_substrings=("investors.", "fireflyspace.com"),
        target_discovery_classification="connector_first",
        notes="Issuer IR/news discovery should not need broad-web providers.",
    ),
    BeaconSearchEvalCase(
        query="microsoft 10-k ai capex sec filing",
        query_family="regulatory_filings",
        expected_terms=("10-k", "capex"),
        expected_url_substrings=("sec.gov",),
        target_discovery_classification="connector_first",
        notes="SEC and filing queries should route into filings/EDGAR discovery.",
    ),
    BeaconSearchEvalCase(
        query="retrieval augmented generation benchmark arxiv",
        query_family="research_papers",
        expected_terms=("retrieval", "benchmark"),
        expected_url_substrings=("arxiv.org",),
        target_discovery_classification="connector_first",
        notes="Paper search should move to paper-index connectors before general search.",
    ),
    BeaconSearchEvalCase(
        query="latest anthropic model docs tool use",
        query_family="open_web_current_fact",
        expected_terms=("tool", "use"),
        expected_url_substrings=("anthropic.com",),
        target_discovery_classification="provider_fallback",
        notes="Time-sensitive open-web lookups can still use providers as cold-start fallback.",
    ),
)

BEACON_PROVIDER_INDEPENDENCE_THRESHOLDS: dict[str, float] = {
    "min_term_recall_ratio": 0.95,
    "min_citation_support": 0.5,
    "min_freshness_score": 0.5,
    "max_external_call_rate": 0.5,
    "min_corpus_only_rate": 0.25,
    "min_connector_first_rate": 0.35,
    "max_provider_fallback_rate": 0.4,
    "max_provider_only_rate": 0.1,
    "min_target_discovery_match_rate": 0.7,
}


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return numerator / denominator


def score_search_payload(
    case: BeaconSearchEvalCase,
    payload: dict[str, Any],
    *,
    mode: str,
    duration_ms: float = 0.0,
) -> BeaconSearchEvalRow:
    results = list(payload.get("results") or [])
    combined_text = " ".join(
        " ".join(
            str(item.get(key, "") or "")
            for key in ("title", "snippet", "fetched_content")
        )
        for item in results
    ).lower()
    urls = " ".join(str(item.get("url", "") or "") for item in results).lower()
    matched_terms = sum(1 for term in case.expected_terms if term.lower() in combined_text)
    matched_urls = sum(1 for value in case.expected_url_substrings if value.lower() in urls)
    citation_supported = sum(
        1
        for item in results
        if item.get("fetched_content") or item.get("document_id") or item.get("chunk_id")
    )
    corpus_hits = sum(
        1
        for item in results
        if str(item.get("evidence_source", "") or "").strip() in {"corpus", "fetched_corpus"}
    )
    fresh_results = sum(
        1
        for item in results
        if str(item.get("freshness_state", "") or "").strip() in {"fresh", "warm"}
    )
    broker = dict(payload.get("search_broker") or {})
    beacon_corpus = dict(payload.get("beacon_corpus") or {})
    discovery_source_counts = normalize_discovery_source_counts(
        broker.get("discovery_source_counts") or beacon_corpus.get("discovery_source_counts")
    )
    discovery_classification = str(
        broker.get("discovery_classification")
        or beacon_corpus.get("discovery_classification")
        or classify_discovery_source_counts(discovery_source_counts)
    ).strip() or "no_results"
    used_legacy = bool(broker.get("used_legacy"))
    result_count = len(results)
    return BeaconSearchEvalRow(
        mode=mode,
        query=case.query,
        query_family=case.query_family,
        result_count=result_count,
        term_recall=_safe_ratio(matched_terms, len(case.expected_terms)),
        url_recall=_safe_ratio(matched_urls, len(case.expected_url_substrings)),
        citation_support=_safe_ratio(citation_supported, result_count),
        corpus_hit_rate=_safe_ratio(corpus_hits, result_count),
        freshness_score=_safe_ratio(fresh_results, result_count),
        external_call_rate=1.0 if used_legacy else 0.0,
        duration_ms=float(duration_ms),
        cost_proxy=1.0 if used_legacy else 0.0,
        used_legacy=used_legacy,
        discovery_classification=discovery_classification,
        discovery_source_counts=discovery_source_counts,
        target_discovery_match=(
            None
            if case.target_discovery_classification is None
            else discovery_classification == case.target_discovery_classification
        ),
        metadata={
            "search_broker": broker,
            "beacon_corpus": beacon_corpus,
            "query_family": case.query_family,
            "target_discovery_classification": case.target_discovery_classification,
        },
    )


async def compare_modes(
    case: BeaconSearchEvalCase,
    *,
    modes: tuple[str, ...] = ("legacy", "hybrid", "beacon"),
    runner: Callable[[str, str], Awaitable[dict[str, Any]]],
) -> list[BeaconSearchEvalRow]:
    rows: list[BeaconSearchEvalRow] = []
    for mode in modes:
        t0 = time.monotonic()
        payload = await runner(mode, case.query)
        duration_ms = (time.monotonic() - t0) * 1000.0
        rows.append(score_search_payload(case, payload, mode=mode, duration_ms=duration_ms))
    return rows


def summarize_eval_rows(rows: list[BeaconSearchEvalRow]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[BeaconSearchEvalRow]] = {}
    for row in rows:
        grouped.setdefault(row.mode, []).append(row)
    summary: dict[str, dict[str, float]] = {}
    for mode, mode_rows in grouped.items():
        count = len(mode_rows) or 1
        target_rows = [row for row in mode_rows if row.target_discovery_match is not None]
        summary[mode] = {
            "queries": float(len(mode_rows)),
            "avg_term_recall": sum(row.term_recall for row in mode_rows) / count,
            "avg_url_recall": sum(row.url_recall for row in mode_rows) / count,
            "avg_citation_support": sum(row.citation_support for row in mode_rows) / count,
            "avg_corpus_hit_rate": sum(row.corpus_hit_rate for row in mode_rows) / count,
            "avg_freshness_score": sum(row.freshness_score for row in mode_rows) / count,
            "avg_external_call_rate": sum(row.external_call_rate for row in mode_rows) / count,
            "avg_duration_ms": sum(row.duration_ms for row in mode_rows) / count,
            "avg_cost_proxy": sum(row.cost_proxy for row in mode_rows) / count,
            "corpus_only_rate": sum(
                1 for row in mode_rows if row.discovery_classification == "corpus_only"
            ) / count,
            "connector_first_rate": sum(
                1 for row in mode_rows if row.discovery_classification == "connector_first"
            ) / count,
            "provider_fallback_rate": sum(
                1 for row in mode_rows if row.discovery_classification == "provider_fallback"
            ) / count,
            "provider_only_rate": sum(
                1 for row in mode_rows if row.discovery_classification == "provider_only"
            ) / count,
            "mixed_paths_rate": sum(
                1 for row in mode_rows if row.discovery_classification == "mixed_paths"
            ) / count,
            "no_results_rate": sum(
                1 for row in mode_rows if row.discovery_classification == "no_results"
            ) / count,
            "target_discovery_match_rate": (
                sum(1 for row in target_rows if row.target_discovery_match) / len(target_rows)
                if target_rows
                else 1.0
            ),
        }
    return summary


def evaluate_cutover_gate(
    summary: dict[str, dict[str, float]],
    *,
    candidate_mode: str = "beacon",
    baseline_mode: str = "legacy",
    min_term_recall_ratio: float = 0.95,
    min_citation_support: float = 0.5,
    min_freshness_score: float = 0.5,
    max_external_call_rate: float = 0.5,
    min_corpus_only_rate: float = 0.0,
    min_connector_first_rate: float = 0.0,
    max_provider_fallback_rate: float = 1.0,
    max_provider_only_rate: float = 1.0,
    min_target_discovery_match_rate: float = 0.0,
) -> dict[str, Any]:
    candidate = dict(summary.get(candidate_mode) or {})
    baseline = dict(summary.get(baseline_mode) or {})

    def _metric(values: dict[str, float], key: str, default: float) -> float:
        value = values.get(key, default)
        if value is None:
            return default
        return float(value)

    baseline_term_recall = _metric(baseline, "avg_term_recall", 0.0)
    candidate_term_recall = _metric(candidate, "avg_term_recall", 0.0)
    required_term_recall = baseline_term_recall * min_term_recall_ratio
    checks = {
        "term_recall": candidate_term_recall >= required_term_recall,
        "citation_support": _metric(candidate, "avg_citation_support", 0.0) >= min_citation_support,
        "freshness_score": _metric(candidate, "avg_freshness_score", 0.0) >= min_freshness_score,
        "external_call_rate": _metric(candidate, "avg_external_call_rate", 1.0) <= max_external_call_rate,
        "corpus_only_rate": _metric(candidate, "corpus_only_rate", 0.0) >= min_corpus_only_rate,
        "connector_first_rate": _metric(candidate, "connector_first_rate", 0.0)
        >= min_connector_first_rate,
        "provider_fallback_rate": _metric(candidate, "provider_fallback_rate", 1.0)
        <= max_provider_fallback_rate,
        "provider_only_rate": _metric(candidate, "provider_only_rate", 1.0) <= max_provider_only_rate,
        "target_discovery_match_rate": _metric(candidate, "target_discovery_match_rate", 0.0)
        >= min_target_discovery_match_rate,
    }
    return {
        "candidate_mode": candidate_mode,
        "baseline_mode": baseline_mode,
        "passed": all(checks.values()),
        "checks": checks,
        "candidate": candidate,
        "baseline": baseline,
    }
