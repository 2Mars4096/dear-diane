"""Discovery-hit normalization and source-family policies for Beacon Search."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

from dan.server.search_models import canonicalize_search_url, domain_from_url

_SOURCE_FAMILY_PRIORITY = {
    "official_docs": 1.0,
    "regulatory": 0.95,
    "github": 0.9,
    "research": 0.85,
    "company": 0.8,
    "web": 0.5,
}
_SOURCE_FAMILY_TTL_SECONDS = {
    "official_docs": 7 * 24 * 60 * 60,
    "regulatory": 3 * 24 * 60 * 60,
    "github": 2 * 24 * 60 * 60,
    "research": 14 * 24 * 60 * 60,
    "company": 2 * 24 * 60 * 60,
    "web": 7 * 24 * 60 * 60,
}
_QUERY_STOP_WORDS = frozenset({
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
})


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]+", str(query or "").lower()):
        if token in _QUERY_STOP_WORDS:
            continue
        terms.append(token)
    return list(dict.fromkeys(terms))


def classify_source_family(url: str) -> str:
    domain = domain_from_url(url)
    if domain.startswith("www."):
        domain = domain[4:]
    path = urlsplit(str(url or "")).path.lower()
    if domain.endswith(".gov") or domain.endswith(".edu") or domain in {"sec.gov"}:
        return "regulatory"
    if domain in {"github.com", "raw.githubusercontent.com"}:
        return "github"
    if domain in {"arxiv.org", "semanticscholar.org"}:
        return "research"
    if domain.startswith("docs.") or domain.startswith("developer.") or "/docs" in path:
        return "official_docs"
    if (
        domain.startswith("investor.")
        or domain.startswith("investors.")
        or domain.startswith("ir.")
        or "/investors" in path
    ):
        return "company"
    return "web"


def source_family_ttl_seconds(source_family: str) -> int:
    return _SOURCE_FAMILY_TTL_SECONDS.get(str(source_family or "").strip(), _SOURCE_FAMILY_TTL_SECONDS["web"])


@dataclass(frozen=True)
class SourceProfile:
    source_family: str
    ttl_seconds: int
    rate_limit_per_minute: int
    requires_robots_check: bool
    supported_ingest_methods: tuple[str, ...]


def source_profile_for_url(url: str) -> SourceProfile:
    source_family = classify_source_family(url)
    if source_family == "official_docs":
        return SourceProfile(
            source_family=source_family,
            ttl_seconds=source_family_ttl_seconds(source_family),
            rate_limit_per_minute=30,
            requires_robots_check=True,
            supported_ingest_methods=("sitemap", "feed", "static_docs"),
        )
    if source_family == "github":
        return SourceProfile(
            source_family=source_family,
            ttl_seconds=source_family_ttl_seconds(source_family),
            rate_limit_per_minute=20,
            requires_robots_check=False,
            supported_ingest_methods=("api", "feed", "static_docs"),
        )
    if source_family == "research":
        return SourceProfile(
            source_family=source_family,
            ttl_seconds=source_family_ttl_seconds(source_family),
            rate_limit_per_minute=20,
            requires_robots_check=True,
            supported_ingest_methods=("feed", "api"),
        )
    if source_family == "regulatory":
        return SourceProfile(
            source_family=source_family,
            ttl_seconds=source_family_ttl_seconds(source_family),
            rate_limit_per_minute=15,
            requires_robots_check=True,
            supported_ingest_methods=("sitemap", "feed"),
        )
    if source_family == "company":
        return SourceProfile(
            source_family=source_family,
            ttl_seconds=source_family_ttl_seconds(source_family),
            rate_limit_per_minute=20,
            requires_robots_check=True,
            supported_ingest_methods=("sitemap", "feed", "static_docs"),
        )
    return SourceProfile(
        source_family="web",
        ttl_seconds=source_family_ttl_seconds("web"),
        rate_limit_per_minute=10,
        requires_robots_check=True,
        supported_ingest_methods=("sitemap", "feed"),
    )


@dataclass(frozen=True)
class DiscoveryHit:
    title: str
    url: str
    snippet: str = ""
    provider: str = ""
    provider_rank: int = 0
    canonical_url: str = ""
    source_family: str = "web"
    freshness_ttl_seconds: int = _SOURCE_FAMILY_TTL_SECONDS["web"]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_result_item(self, *, evidence_source: str = "external_discovery") -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "provider": self.provider,
            "canonical_url": self.canonical_url or canonicalize_search_url(self.url) or self.url,
            "evidence_source": evidence_source,
            "freshness_state": "fresh",
            "ranking_features": dict(self.metadata.get("ranking_features") or {}),
        }


def normalize_legacy_discovery_hits(payload: dict[str, Any]) -> list[DiscoveryHit]:
    provider = str(payload.get("provider", "") or "").strip()
    hits: list[DiscoveryHit] = []
    for index, item in enumerate(payload.get("results") or [], start=1):
        url = str(item.get("url", "") or "").strip()
        family = classify_source_family(url)
        hits.append(DiscoveryHit(
            title=str(item.get("title", "") or "").strip(),
            url=url,
            snippet=str(item.get("snippet", "") or "").strip(),
            provider=str(item.get("provider", "") or provider).strip(),
            provider_rank=index,
            canonical_url=canonicalize_search_url(url) or url,
            source_family=family,
            freshness_ttl_seconds=source_family_ttl_seconds(family),
            metadata={
                "result_kind": str(item.get("result_kind", "") or "organic").strip() or "organic",
                "ranking_features": {
                    "provider_rank": float(index),
                    "source_family_priority": _SOURCE_FAMILY_PRIORITY.get(family, 0.5),
                },
            },
        ))
    return hits


def rank_discovery_hits(query: str, hits: list[DiscoveryHit]) -> list[DiscoveryHit]:
    terms = _query_terms(query)

    def _score(hit: DiscoveryHit) -> tuple[float, float, float]:
        title = hit.title.lower()
        snippet = hit.snippet.lower()
        lexical = float(
            sum((2.0 if term in title else 0.0) + (1.0 if term in snippet else 0.0) for term in terms)
        )
        family_priority = _SOURCE_FAMILY_PRIORITY.get(hit.source_family, 0.5)
        provider_priority = 1.0 / max(hit.provider_rank, 1)
        return (lexical + family_priority, family_priority, provider_priority)

    return sorted(hits, key=_score, reverse=True)


def parse_sitemap_xml(xml_text: str) -> list[str]:
    if not str(xml_text or "").strip():
        return []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    urls: list[str] = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag != "loc":
            continue
        url = str(node.text or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def parse_feed_xml(xml_text: str) -> list[str]:
    if not str(xml_text or "").strip():
        return []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    urls: list[str] = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag == "link":
            href = str(node.attrib.get("href", "") or "").strip()
            text = str(node.text or "").strip()
            url = href or text
            if url and url not in urls:
                urls.append(url)
    return urls


def seed_static_doc_urls(base_url: str, paths: list[str]) -> list[str]:
    seeded: list[str] = []
    for path in paths:
        url = urljoin(base_url.rstrip("/") + "/", str(path or "").lstrip("/"))
        if url and url not in seeded:
            seeded.append(url)
    return seeded


def build_refresh_queue(corpus_store: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    refresh_candidates = corpus_store.list_refresh_candidates(limit=limit)
    queue: list[dict[str, Any]] = []
    for item in refresh_candidates:
        profile = source_profile_for_url(str(item.get("canonical_url") or item.get("url") or ""))
        queue.append({
            **item,
            "source_profile": {
                "source_family": profile.source_family,
                "ttl_seconds": profile.ttl_seconds,
                "rate_limit_per_minute": profile.rate_limit_per_minute,
                "requires_robots_check": profile.requires_robots_check,
                "supported_ingest_methods": list(profile.supported_ingest_methods),
            },
        })
    return queue
