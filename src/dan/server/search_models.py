"""Shared web-search models, compatibility contracts, and helpers."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_NAMES = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref_src",
    "ref_url",
}

SEARCH_RESULT_CONTRACT_VERSION = "search_result_v1"
SEARCH_RESULT_SET_CONTRACT_VERSION = "search_result_set_v1"

# Fields Beacon must preserve for existing DAN callers even as internal search
# backends change.
SEARCH_RESULT_HARD_COMPATIBILITY_FIELDS = (
    "index",
    "title",
    "url",
    "snippet",
    "fetched_content",
    "provider",
    "result_kind",
)
SEARCH_RESULT_ADAPTER_METADATA_FIELDS = (
    "page_age",
    "credibility_tier",
    "source_query",
    "canonical_url",
    "seen_in_recent_searches",
    "document_id",
    "chunk_id",
    "evidence_source",
    "freshness_state",
    "ranking_features",
)
SEARCH_RESULT_SET_HARD_COMPATIBILITY_FIELDS = (
    "query",
    "results",
    "provider",
    "providers",
    "cache_hit",
    "grounded_result_count",
)
SEARCH_RESULT_SET_ADAPTER_METADATA_FIELDS = (
    "provider_failures",
    "sub_queries",
    "fetch_attempts_made",
    "fetch_target_count",
    "browser_fallback_count",
    "corpus_hit_count",
    "provenance_counts",
    "query_family",
    "provider_policy",
    "provider_fallback_allowed",
    "discovery_classification",
    "discovery_source_counts",
)


def canonicalize_search_url(url: str) -> str:
    raw_url = str(url or "").strip()
    if not raw_url:
        return ""

    try:
        parts = urlsplit(raw_url)
    except ValueError:
        return raw_url

    if not parts.scheme or not parts.netloc:
        return raw_url

    query_pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower.startswith(_TRACKING_PARAM_PREFIXES):
            continue
        if key_lower in _TRACKING_PARAM_NAMES:
            continue
        query_pairs.append((key, value))

    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]

    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((
        scheme,
        netloc,
        path,
        urlencode(query_pairs, doseq=True),
        "",
    ))


def domain_from_url(url: str) -> str:
    raw_url = str(url or "").strip()
    if not raw_url:
        return ""
    try:
        return urlsplit(raw_url).netloc.lower()
    except ValueError:
        return ""


class SearchResult(BaseModel):
    index: int
    title: str = ""
    url: str = ""
    snippet: str = ""
    fetched_content: str | None = None
    page_age: str | None = None
    credibility_tier: str | None = None
    provider: str = ""
    result_kind: str = "organic"
    source_query: str | None = None
    canonical_url: str | None = None
    seen_in_recent_searches: bool = False
    document_id: str | None = None
    chunk_id: str | None = None
    evidence_source: str | None = None
    freshness_state: str | None = None
    ranking_features: dict[str, float] = Field(default_factory=dict)


class CitationRecord(BaseModel):
    claim_text: str
    source_index: int
    source_url: str
    cited_excerpt: str = ""
    document_id: str | None = None
    chunk_id: str | None = None
    evidence_source: str | None = None
    verified: bool | None = None


class SearchResultSet(BaseModel):
    query: str
    results: list[SearchResult] = Field(default_factory=list)
    provider: str = ""
    providers: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    provider_failures: list[dict[str, Any]] = Field(default_factory=list)
    grounded_result_count: int = 0
    sub_queries: list[str] | None = None
    fetch_attempts_made: int = 0
    fetch_target_count: int = 0
    browser_fallback_count: int = 0
    corpus_hit_count: int = 0
    provenance_counts: dict[str, int] = Field(default_factory=dict)
    query_family: str | None = None
    provider_policy: str | None = None
    provider_fallback_allowed: bool | None = None
    discovery_classification: str | None = None
    discovery_source_counts: dict[str, int] = Field(default_factory=dict)


class InlineCitation(BaseModel):
    index: int
    surrounding_text: str = ""
    claim_text: str = ""


class CitationVerification(BaseModel):
    citation_index: int
    claim_text: str
    source_url: str = ""
    source_excerpt_match: str | None = None
    document_id: str | None = None
    chunk_id: str | None = None
    verified: bool
    confidence: float = 0.0
    reason: str = ""


def search_result_contract_manifest() -> dict[str, Any]:
    """Describe the stable Beacon compatibility membrane for search payloads."""
    return {
        "search_result": {
            "version": SEARCH_RESULT_CONTRACT_VERSION,
            "hard_compatibility_fields": list(SEARCH_RESULT_HARD_COMPATIBILITY_FIELDS),
            "adapter_metadata_fields": list(SEARCH_RESULT_ADAPTER_METADATA_FIELDS),
        },
        "search_result_set": {
            "version": SEARCH_RESULT_SET_CONTRACT_VERSION,
            "hard_compatibility_fields": list(SEARCH_RESULT_SET_HARD_COMPATIBILITY_FIELDS),
            "adapter_metadata_fields": list(SEARCH_RESULT_SET_ADAPTER_METADATA_FIELDS),
        },
    }


def parse_search_result_set(data: Any) -> SearchResultSet | None:
    if isinstance(data, SearchResultSet):
        return data
    if not isinstance(data, dict):
        return None
    payload = data.get("search_result_set", data)
    if not isinstance(payload, dict):
        return None
    try:
        return SearchResultSet.model_validate(payload)
    except Exception:
        return None
