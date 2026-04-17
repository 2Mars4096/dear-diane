"""First-party connector discovery for Beacon Search."""

from __future__ import annotations

from dataclasses import dataclass
import httpx
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlsplit

from dan.search.connectors import (
    DiscoveryHit,
    extract_same_site_links,
    parse_feed_xml,
    parse_sitemap_xml,
    rank_discovery_hits,
    seed_static_doc_urls,
    source_profile_for_url,
)
from dan.server.search_models import canonicalize_search_url, domain_from_url

_QUERY_STOP_WORDS = frozenset({
    "a",
    "an",
    "and",
    "api",
    "as",
    "at",
    "by",
    "doc",
    "docs",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "latest",
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
_DEFAULT_SITEMAP_PATHS = ("/sitemap.xml",)
_DEFAULT_FEED_PATHS = ("/feed.xml", "/rss.xml", "/atom.xml")
_QUERY_FAMILY_PRIORITY = {
    "official_docs": 5,
    "regulatory_filings": 4,
    "company_ir_news": 3,
    "github_docs_releases": 3,
    "research_papers": 2,
    "open_web_current_fact": 1,
    "general": 0,
}
_CONNECTOR_FETCH_USER_AGENT = (
    "Mozilla/5.0 (compatible; deep-agent-network/0.1; +https://github.com/deep-agent-network)"
)
_KNOWN_GITHUB_REPOS = {
    ("openai", "python"): ("OpenAI Python", "https://github.com/openai/openai-python"),
    ("openai", "typescript"): ("OpenAI Node", "https://github.com/openai/openai-node"),
    ("openai", "javascript"): ("OpenAI Node", "https://github.com/openai/openai-node"),
    ("openai", "js"): ("OpenAI Node", "https://github.com/openai/openai-node"),
    ("openai", "ts"): ("OpenAI Node", "https://github.com/openai/openai-node"),
    ("anthropic", "python"): (
        "Anthropic Python",
        "https://github.com/anthropics/anthropic-sdk-python",
    ),
    ("anthropic", "typescript"): (
        "Anthropic TypeScript",
        "https://github.com/anthropics/anthropic-sdk-typescript",
    ),
    ("anthropic", "javascript"): (
        "Anthropic TypeScript",
        "https://github.com/anthropics/anthropic-sdk-typescript",
    ),
    ("anthropic", "js"): (
        "Anthropic TypeScript",
        "https://github.com/anthropics/anthropic-sdk-typescript",
    ),
    ("anthropic", "ts"): (
        "Anthropic TypeScript",
        "https://github.com/anthropics/anthropic-sdk-typescript",
    ),
}

FetchFn = Callable[[str], Awaitable[dict[str, Any]]]


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]+", str(query or "").lower()):
        if token in _QUERY_STOP_WORDS:
            continue
        terms.append(token)
    return list(dict.fromkeys(terms))


def _domain_matches(domain: str, constraint: str) -> bool:
    normalized_domain = str(domain or "").strip().lower()
    normalized_constraint = str(constraint or "").strip().lower()
    if not normalized_domain or not normalized_constraint:
        return False
    return normalized_domain == normalized_constraint or normalized_domain.endswith(
        "." + normalized_constraint
    )


def _url_allowed(url: str, *, allowed_domains: tuple[str, ...], blocked_domains: tuple[str, ...]) -> bool:
    domain = domain_from_url(url)
    if not domain:
        return False
    if allowed_domains and not any(_domain_matches(domain, item) for item in allowed_domains):
        return False
    if blocked_domains and any(_domain_matches(domain, item) for item in blocked_domains):
        return False
    return True


def _pretty_title(url: str, *, label: str) -> str:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    if not parts:
        return label
    leaf = parts[-1].split(".", 1)[0].replace("-", " ").replace("_", " ").strip()
    if not leaf:
        return label
    return f"{label}: {leaf.title()}"


def _generic_static_paths(source_family: str) -> tuple[str, ...]:
    if source_family == "official_docs":
        return ("/", "/reference", "/api", "/guide")
    if source_family == "company":
        return ("/", "/investor-relations", "/investors", "/news", "/press-releases")
    if source_family == "github":
        return ("/", "/releases", "/tags")
    if source_family == "research":
        return ("/", "/list/cs/recent")
    if source_family == "regulatory":
        return ("/", "/edgar/search/")
    return ("/",)


def _family_from_allowed_domains(allowed_domains: tuple[str, ...]) -> str | None:
    families: list[str] = []
    for domain in allowed_domains:
        normalized_domain = str(domain or "").strip().lower()
        if not normalized_domain:
            continue
        source_family = source_profile_for_url(f"https://{normalized_domain}").source_family
        if source_family == "official_docs":
            families.append("official_docs")
        elif source_family == "regulatory":
            families.append("regulatory_filings")
        elif source_family == "company":
            families.append("company_ir_news")
        elif source_family == "github":
            families.append("github_docs_releases")
        elif source_family == "research":
            families.append("research_papers")
    if not families:
        return None
    return max(families, key=lambda item: _QUERY_FAMILY_PRIORITY.get(item, 0))


def classify_query_family(query: str, *, allowed_domains: tuple[str, ...] = ()) -> str:
    family_from_domains = _family_from_allowed_domains(allowed_domains)
    if family_from_domains:
        return family_from_domains

    raw_query = str(query or "").lower()
    terms = _query_terms(query)
    term_set = set(terms)

    if term_set.intersection({"sec", "10-k", "10q", "10-q", "8-k", "edgar", "filing", "filings"}):
        return "regulatory_filings"
    if term_set.intersection({"github", "release", "releases", "repo", "repository", "tag", "tags"}):
        return "github_docs_releases"
    if term_set.intersection({"arxiv", "paper", "papers", "research"}):
        return "research_papers"
    if term_set.intersection({"investor", "investors", "earnings", "quarterly", "results", "revenue"}):
        return "company_ir_news"
    if (
        "docs" in raw_query
        or "documentation" in raw_query
        or term_set.intersection({"reference", "guide", "api", "sdk", "tool", "tools"})
    ):
        return "official_docs"
    if term_set.intersection({"latest", "today", "recent", "current", "new", "news"}):
        return "open_web_current_fact"
    return "general"


@dataclass(frozen=True)
class ConnectorSeed:
    label: str
    base_url: str
    source_family: str
    static_paths: tuple[str, ...] = ()
    sitemap_paths: tuple[str, ...] = _DEFAULT_SITEMAP_PATHS
    feed_paths: tuple[str, ...] = _DEFAULT_FEED_PATHS


def _allowed_domain_seeds(domains: tuple[str, ...]) -> list[ConnectorSeed]:
    seeds: list[ConnectorSeed] = []
    for domain in domains:
        normalized_domain = str(domain or "").strip().lower()
        if not normalized_domain:
            continue
        base_url = f"https://{normalized_domain}"
        profile = source_profile_for_url(base_url)
        seeds.append(ConnectorSeed(
            label=normalized_domain,
            base_url=base_url,
            source_family=profile.source_family,
            static_paths=_generic_static_paths(profile.source_family),
        ))
    return seeds


def _github_repo_seeds(query: str, *, terms: list[str]) -> list[ConnectorSeed]:
    seeds: list[ConnectorSeed] = []
    seen: set[str] = set()
    raw_query = str(query or "")

    for match in re.finditer(r"\b([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)\b", raw_query):
        base_url = f"https://github.com/{match.group(1)}/{match.group(2)}"
        canonical = canonicalize_search_url(base_url) or base_url
        if canonical in seen:
            continue
        seen.add(canonical)
        seeds.append(ConnectorSeed(
            label=f"{match.group(1)}/{match.group(2)}",
            base_url=base_url,
            source_family="github",
            static_paths=("/", "/releases", "/tags"),
        ))

    term_set = set(terms)
    for key, value in _KNOWN_GITHUB_REPOS.items():
        if set(key).issubset(term_set):
            label, base_url = value
            canonical = canonicalize_search_url(base_url) or base_url
            if canonical in seen:
                continue
            seen.add(canonical)
            seeds.append(ConnectorSeed(
                label=label,
                base_url=base_url,
                source_family="github",
                static_paths=("/", "/releases", "/tags"),
            ))

    return seeds


def _builtin_seeds(query: str, *, query_family: str) -> list[ConnectorSeed]:
    terms = _query_terms(query)
    term_set = set(terms)
    seeds: list[ConnectorSeed] = []

    if query_family == "official_docs" and "python" in term_set:
        module_paths = tuple(
            f"/3/library/{term}.html"
            for term in terms
            if term
            and term not in {"python", "library", "stdlib"}
            and re.fullmatch(r"[a-z_][a-z0-9_]*", term)
        )
        seeds.append(ConnectorSeed(
            label="Python docs",
            base_url="https://docs.python.org",
            source_family="official_docs",
            static_paths=("/", "/3/library/index.html", *module_paths),
        ))

    if query_family == "official_docs" and "openai" in term_set:
        seeds.append(ConnectorSeed(
            label="OpenAI docs",
            base_url="https://platform.openai.com",
            source_family="official_docs",
            static_paths=(
                "/docs/overview",
                "/docs/api-reference/responses",
                "/docs/guides/tools",
            ),
        ))

    if query_family == "official_docs" and "anthropic" in term_set:
        seeds.append(ConnectorSeed(
            label="Anthropic docs",
            base_url="https://docs.anthropic.com",
            source_family="official_docs",
            static_paths=(
                "/en/docs/overview",
                "/en/docs/agents-and-tools/tool-use/overview",
            ),
        ))

    if query_family == "regulatory_filings":
        seeds.append(ConnectorSeed(
            label="SEC EDGAR",
            base_url="https://www.sec.gov",
            source_family="regulatory",
            static_paths=("/", "/edgar/search/"),
        ))

    if query_family == "research_papers":
        seeds.append(ConnectorSeed(
            label="arXiv",
            base_url="https://arxiv.org",
            source_family="research",
            static_paths=("/", "/list/cs/recent"),
        ))

    if query_family == "github_docs_releases":
        seeds.extend(_github_repo_seeds(query, terms=terms))

    return seeds


def select_query_source_seeds(
    query: str,
    *,
    allowed_domains: tuple[str, ...] = (),
    seed_domains: tuple[str, ...] = (),
) -> list[ConnectorSeed]:
    query_family = classify_query_family(query, allowed_domains=allowed_domains)
    seeds = (
        _allowed_domain_seeds(allowed_domains)
        if allowed_domains
        else _allowed_domain_seeds(seed_domains)
        if seed_domains
        else _builtin_seeds(query, query_family=query_family)
    )
    deduped: list[ConnectorSeed] = []
    seen: set[str] = set()
    for seed in seeds:
        key = canonicalize_search_url(seed.base_url) or seed.base_url
        if key in seen:
            continue
        seen.add(key)
        deduped.append(seed)
    return deduped[:3]


def has_connector_seed_coverage(
    query: str,
    *,
    allowed_domains: tuple[str, ...] = (),
    seed_domains: tuple[str, ...] = (),
) -> bool:
    return bool(
        select_query_source_seeds(
            query,
            allowed_domains=allowed_domains,
            seed_domains=seed_domains,
        )
    )


async def _default_fetcher(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": _CONNECTOR_FETCH_USER_AGENT},
        timeout=httpx.Timeout(10.0, connect=5.0),
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return {
            "url": str(response.url),
            "content": response.text[:250000],
            "content_type": str(response.headers.get("content-type", "") or ""),
        }


async def _urls_from_seed(seed: ConnectorSeed, *, fetcher: FetchFn) -> list[str]:
    static_urls = seed_static_doc_urls(seed.base_url, list(seed.static_paths))
    urls: list[str] = list(static_urls)
    profile = source_profile_for_url(seed.base_url)

    if "sitemap" in profile.supported_ingest_methods:
        for path in seed.sitemap_paths:
            sitemap_url = urljoin(seed.base_url.rstrip("/") + "/", str(path or "").lstrip("/"))
            try:
                fetched = await fetcher(sitemap_url)
            except Exception:
                continue
            parsed = parse_sitemap_xml(str(fetched.get("content", "") or ""))
            if parsed:
                urls.extend(parsed[:50])
                break

    if "feed" in profile.supported_ingest_methods:
        for path in seed.feed_paths:
            feed_url = urljoin(seed.base_url.rstrip("/") + "/", str(path or "").lstrip("/"))
            try:
                fetched = await fetcher(feed_url)
            except Exception:
                continue
            parsed = parse_feed_xml(str(fetched.get("content", "") or ""))
            if parsed:
                urls.extend(parsed[:25])
                break

    if "html_links" in profile.supported_ingest_methods:
        for static_url in static_urls[:3]:
            try:
                fetched = await fetcher(static_url)
            except Exception:
                continue
            urls.extend(
                extract_same_site_links(
                    str(fetched.get("url", "") or "").strip() or static_url,
                    str(fetched.get("content", "") or ""),
                    limit=20,
                )
            )

    deduped: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = canonicalize_search_url(url) or str(url or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


async def discover_connector_hits(
    query: str,
    *,
    allowed_domains: tuple[str, ...] = (),
    seed_domains: tuple[str, ...] = (),
    blocked_domains: tuple[str, ...] = (),
    limit: int = 5,
    fetcher: FetchFn | None = None,
) -> list[DiscoveryHit]:
    seeds = select_query_source_seeds(
        query,
        allowed_domains=allowed_domains,
        seed_domains=seed_domains,
    )
    if not seeds:
        return []

    hits: list[DiscoveryHit] = []
    seen: set[str] = set()
    fetch = fetcher or _default_fetcher

    for seed in seeds:
        for url in await _urls_from_seed(seed, fetcher=fetch):
            if not _url_allowed(url, allowed_domains=allowed_domains, blocked_domains=blocked_domains):
                continue
            canonical = canonicalize_search_url(url) or url
            if canonical in seen:
                continue
            seen.add(canonical)
            hits.append(DiscoveryHit(
                title=_pretty_title(url, label=seed.label),
                url=url,
                snippet=f"Beacon connector discovery from {seed.label}",
                provider="beacon_connector",
                provider_rank=len(hits) + 1,
                canonical_url=canonical,
                source_family=seed.source_family,
                metadata={
                    "ranking_features": {
                        "connector_seed": 1.0,
                    },
                },
            ))

    return rank_discovery_hits(query, hits)[: max(1, int(limit))]
