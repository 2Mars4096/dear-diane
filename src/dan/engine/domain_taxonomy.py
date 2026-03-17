from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

_CANONICAL_DOMAIN_ALIASES = {
    "paper_rendering": "paper_rendering",
    "paper rendering": "paper_rendering",
    "scientific writing": "paper_rendering",
    "equity_research": "equity_research",
    "equity research": "equity_research",
    "data_analysis": "data_analysis",
    "data analysis": "data_analysis",
    "literature_review": "literature_review",
    "literature review": "literature_review",
    "lit review": "literature_review",
    "code_generation": "code_generation",
    "code generation": "code_generation",
    "workflow_building": "workflow_building",
    "workflow building": "workflow_building",
}

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def normalize_domain_name(domain: str | None) -> str:
    """Return a canonical domain id, falling back to a safe slug."""
    raw = str(domain or "").strip().lower()
    if not raw:
        return ""
    whitespace_normalized = _NON_ALNUM_RE.sub(" ", raw).strip()
    if not whitespace_normalized:
        return ""
    alias = _CANONICAL_DOMAIN_ALIASES.get(whitespace_normalized)
    if alias:
        return alias
    slug = whitespace_normalized.replace(" ", "_")
    return _CANONICAL_DOMAIN_ALIASES.get(slug, slug)


def normalize_domain_list(domains: Iterable[str] | None) -> list[str]:
    """Canonicalize domains, preserve order, and drop duplicates/empties."""
    if isinstance(domains, (str, bytes)):
        domains = [str(domains)]
    normalized: list[str] = []
    seen: set[str] = set()
    for domain in domains or []:
        canonical = normalize_domain_name(domain)
        if not canonical or canonical in seen:
            continue
        normalized.append(canonical)
        seen.add(canonical)
    return normalized


def normalize_domain_keyword_map(
    raw: Mapping[str, Iterable[str]] | None,
) -> dict[str, list[str]]:
    """Canonicalize domain keys and merge duplicate buckets safely."""
    normalized: dict[str, list[str]] = {}
    if not isinstance(raw, Mapping):
        return normalized
    for domain, keywords in raw.items():
        canonical = normalize_domain_name(str(domain or ""))
        if (
            not canonical
            or isinstance(keywords, (str, bytes))
            or not isinstance(keywords, Iterable)
        ):
            continue
        bucket = normalized.setdefault(canonical, [])
        seen = set(bucket)
        for keyword in keywords:
            cleaned = str(keyword or "").strip().lower()
            if not cleaned or cleaned in seen:
                continue
            bucket.append(cleaned)
            seen.add(cleaned)
    return {domain: keywords for domain, keywords in normalized.items() if keywords}


def format_domain_label(domain: str | None) -> str:
    """Render a canonical domain id as a human-readable label."""
    canonical = normalize_domain_name(domain)
    if canonical:
        return canonical.replace("_", " ")
    return str(domain or "").strip()
