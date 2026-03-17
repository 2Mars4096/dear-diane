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
    "supply_chain_management": "supply_chain_management",
    "supply chain": "supply_chain_management",
    "supply chain management": "supply_chain_management",
    "supply chain logistics": "supply_chain_management",
    "scm": "supply_chain_management",
    "operations_management": "operations_management",
    "operations management": "operations_management",
    "ops management": "operations_management",
    "om": "operations_management",
    "operations_research": "operations_research",
    "operations research": "operations_research",
    "or": "operations_research",
    "inventory_optimization": "inventory_optimization",
    "inventory optimization": "inventory_optimization",
    "inventory management": "inventory_optimization",
    "marketing_analytics": "marketing_analytics",
    "marketing analytics": "marketing_analytics",
    "product_management": "product_management",
    "product management": "product_management",
    "financial_modeling": "financial_modeling",
    "financial modeling": "financial_modeling",
    "financial modelling": "financial_modeling",
    "healthcare_informatics": "healthcare_informatics",
    "healthcare informatics": "healthcare_informatics",
    "health informatics": "healthcare_informatics",
    "machine_learning": "machine_learning",
    "machine learning": "machine_learning",
    "ml": "machine_learning",
    "natural_language_processing": "natural_language_processing",
    "natural language processing": "natural_language_processing",
    "nlp": "natural_language_processing",
    "computer_vision": "computer_vision",
    "computer vision": "computer_vision",
    "cv": "computer_vision",
    "reinforcement_learning": "reinforcement_learning",
    "reinforcement learning": "reinforcement_learning",
    "rl": "reinforcement_learning",
    "causal_inference": "causal_inference",
    "causal inference": "causal_inference",
    "econometrics": "econometrics",
    "econometric analysis": "econometrics",
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


def preserve_domain_labels(domains: Iterable[str] | None) -> list[str]:
    """Preserve original labels while deduping by canonical domain id."""
    if isinstance(domains, (str, bytes)):
        domains = [str(domains)]
    preserved: list[str] = []
    seen: set[str] = set()
    for domain in domains or []:
        raw = str(domain or "").strip()
        canonical = normalize_domain_name(raw)
        if not raw or not canonical or canonical in seen:
            continue
        preserved.append(raw)
        seen.add(canonical)
    return preserved


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
