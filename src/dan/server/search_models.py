"""Shared web-search models and helpers."""

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


class CitationRecord(BaseModel):
    claim_text: str
    source_index: int
    source_url: str
    cited_excerpt: str = ""
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


class InlineCitation(BaseModel):
    index: int
    surrounding_text: str = ""
    claim_text: str = ""


class CitationVerification(BaseModel):
    citation_index: int
    claim_text: str
    source_url: str = ""
    source_excerpt_match: str | None = None
    verified: bool
    confidence: float = 0.0
    reason: str = ""


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
