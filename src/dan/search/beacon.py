"""Default Beacon Search executor backed by the local corpus plus legacy fill."""

from __future__ import annotations

import os
from typing import Any

from dan.search.connectors import normalize_legacy_discovery_hits, rank_discovery_hits
from dan.search.connector_discovery import (
    classify_query_family,
    discover_connector_hits,
    has_connector_seed_coverage,
)
from dan.server.search_models import canonicalize_search_url, domain_from_url

from .broker import (
    classify_discovery_source_counts,
    SearchBrokerRequest,
    SearchExecutor,
)
from .corpus import BeaconCorpusStore, get_default_beacon_corpus_store

_PROVIDER_POLICY_ENV = "DAN_BEACON_PROVIDER_POLICY"
_PROVIDER_DISABLED_FAMILIES_ENV = "DAN_BEACON_PROVIDER_DISABLED_FAMILIES"
_DEFAULT_PROVIDER_POLICY = "fallback"
_CONNECTOR_OWNED_FAMILIES = {
    "official_docs",
    "regulatory_filings",
    "github_docs_releases",
    "research_papers",
}


def _provider_policy() -> str:
    raw = str(os.environ.get(_PROVIDER_POLICY_ENV, _DEFAULT_PROVIDER_POLICY) or "").strip().lower()
    if raw in {"fallback", "connectors_only", "off"}:
        return raw
    return _DEFAULT_PROVIDER_POLICY


def _disabled_provider_families() -> set[str]:
    configured = str(os.environ.get(_PROVIDER_DISABLED_FAMILIES_ENV, "") or "").strip()
    if not configured:
        return set()
    return {
        item.strip().lower()
        for item in configured.split(",")
        if item.strip()
    }


def _provider_fallback_allowed(
    *,
    query: str,
    query_family: str,
    allowed_domains: tuple[str, ...],
    seed_domains: tuple[str, ...],
) -> bool:
    policy = _provider_policy()
    if policy == "off":
        return False
    if query_family in _disabled_provider_families():
        return False
    if policy == "connectors_only":
        if allowed_domains:
            return False
        if query_family in _CONNECTOR_OWNED_FAMILIES and has_connector_seed_coverage(
            query,
            allowed_domains=allowed_domains,
            seed_domains=seed_domains,
        ):
            return False
    return True


def _seed_domains_from_local_items(items: list[dict[str, Any]], *, limit: int = 3) -> tuple[str, ...]:
    domains: list[str] = []
    for item in items:
        domain = domain_from_url(
            str(item.get("canonical_url", "") or item.get("url", "") or "").strip()
        )
        if not domain or domain in domains:
            continue
        domains.append(domain)
        if len(domains) >= limit:
            break
    return tuple(domains)


def _normalized_key(item: dict[str, Any]) -> str:
    canonical = canonicalize_search_url(str(item.get("canonical_url", "") or "").strip())
    if canonical:
        return canonical
    return canonicalize_search_url(str(item.get("url", "") or "").strip()) or str(
        item.get("url", "") or ""
    ).strip()


def _corpus_items_from_request(
    request: SearchBrokerRequest,
    *,
    corpus_store: BeaconCorpusStore,
) -> list[dict[str, Any]]:
    if request.url:
        hit = corpus_store.lookup_url(request.url, query=request.query)
        return [hit.to_search_result_item()] if hit is not None else []
    hits = corpus_store.search(
        request.query,
        limit=request.num_results,
        allowed_domains=request.allowed_domains,
        blocked_domains=request.blocked_domains,
    )
    return [hit.to_search_result_item() for hit in hits]


def _merge_results(
    primary_items: list[dict[str, Any]],
    secondary_items: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(primary_items) + list(secondary_items):
        key = _normalized_key(item)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _item_score(item: dict[str, Any]) -> float:
    features = dict(item.get("ranking_features") or {})
    if isinstance(features.get("final"), (int, float)):
        return float(features["final"])
    return float(features.get("semantic", 0.0)) + float(features.get("lexical", 0.0))


def _merge_corpus_items(
    lexical_items: list[dict[str, Any]],
    semantic_items: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    by_document: dict[str, dict[str, Any]] = {}
    for item in list(lexical_items) + list(semantic_items):
        key = str(item.get("document_id", "") or _normalized_key(item))
        current = by_document.get(key)
        if current is None:
            by_document[key] = dict(item)
            continue
        merged = dict(current)
        merged_features = dict(current.get("ranking_features") or {})
        merged_features.update(dict(item.get("ranking_features") or {}))
        merged["ranking_features"] = merged_features
        if _item_score(item) > _item_score(current):
            merged.update(item)
            merged["ranking_features"] = merged_features
        by_document[key] = merged
    return sorted(by_document.values(), key=_item_score, reverse=True)[:limit]


def _providers_for_payload(
    corpus_items: list[dict[str, Any]],
    connector_items: list[dict[str, Any]],
    legacy_payload: dict[str, Any] | None,
) -> list[str]:
    providers: list[str] = []
    if corpus_items:
        providers.append("beacon_corpus")
    if connector_items:
        providers.append("beacon_connector")
    if legacy_payload is None:
        return providers
    primary = str(legacy_payload.get("provider", "") or "").strip()
    if primary and primary not in providers:
        providers.append(primary)
    for item in legacy_payload.get("providers") or []:
        provider = str(item or "").strip()
        if provider and provider not in providers:
            providers.append(provider)
    return providers


async def execute_beacon_search(
    request: SearchBrokerRequest,
    *,
    legacy_executor: SearchExecutor,
    corpus_store: BeaconCorpusStore | None = None,
) -> dict[str, Any]:
    store = corpus_store or get_default_beacon_corpus_store()
    query_family = classify_query_family(request.query, allowed_domains=request.allowed_domains)
    lexical_corpus_items = _corpus_items_from_request(request, corpus_store=store)
    semantic_corpus_items: list[dict[str, Any]] = []
    if request.query and not request.url:
        semantic_corpus_items = [
            hit.to_search_result_item()
            for hit in await store.semantic_search(
                request.query,
                limit=request.num_results,
                allowed_domains=request.allowed_domains,
                blocked_domains=request.blocked_domains,
            )
        ]
    corpus_items = _merge_corpus_items(
        lexical_corpus_items,
        semantic_corpus_items,
        limit=request.num_results,
    )
    seed_domains = _seed_domains_from_local_items(corpus_items)
    provider_policy = _provider_policy()
    provider_fallback_allowed = _provider_fallback_allowed(
        query=request.query,
        query_family=query_family,
        allowed_domains=request.allowed_domains,
        seed_domains=seed_domains,
    )
    connector_items: list[dict[str, Any]] = []
    if request.query and not request.url:
        connector_items = [
            hit.to_result_item(evidence_source="connector_discovery")
            for hit in await discover_connector_hits(
                request.query,
                allowed_domains=request.allowed_domains,
                seed_domains=seed_domains,
                blocked_domains=request.blocked_domains,
                limit=request.num_results,
            )
        ]
    local_results = _merge_results(corpus_items, connector_items, limit=request.num_results)
    need_external_fill = len(local_results) < request.num_results
    legacy_payload: dict[str, Any] | None = None
    if need_external_fill and provider_fallback_allowed:
        legacy_payload = await legacy_executor(request)
    legacy_items = []
    discovery_hits = rank_discovery_hits(
        request.query,
        normalize_legacy_discovery_hits(legacy_payload or {}),
    )
    for hit in discovery_hits:
        legacy_items.append(hit.to_result_item())
    merged_results = _merge_results(local_results, legacy_items, limit=request.num_results)
    discovery_source_counts = {
        "corpus": sum(1 for item in merged_results if item.get("evidence_source") == "corpus"),
        "connector": sum(
            1 for item in merged_results if item.get("evidence_source") == "connector_discovery"
        ),
        "provider": sum(
            1 for item in merged_results if item.get("evidence_source") == "external_discovery"
        ),
    }
    provenance_counts = {
        "corpus": sum(1 for item in merged_results if item.get("evidence_source") == "corpus"),
        "external": sum(1 for item in merged_results if item.get("evidence_source") != "corpus"),
    }
    discovery_classification = classify_discovery_source_counts(discovery_source_counts)
    if discovery_source_counts["provider"] > 0 and discovery_source_counts["connector"] > 0:
        path = "beacon_corpus_plus_connector_plus_legacy"
    elif discovery_source_counts["provider"] > 0:
        path = "beacon_corpus_plus_legacy" if discovery_source_counts["corpus"] > 0 else "beacon_legacy"
    elif discovery_source_counts["connector"] > 0:
        path = "beacon_corpus_plus_connector" if discovery_source_counts["corpus"] > 0 else "beacon_connector"
    elif need_external_fill and not provider_fallback_allowed:
        path = "beacon_local_only"
    else:
        path = "beacon_corpus"
    providers = _providers_for_payload(corpus_items, connector_items, legacy_payload)
    return {
        "results": merged_results,
        "count": len(merged_results),
        "provider": (
            "beacon_corpus"
            if corpus_items
            else "beacon_connector"
            if connector_items
            else str((legacy_payload or {}).get("provider", "") or "")
        ),
        "providers": providers,
        "cache_hit": bool((legacy_payload or {}).get("cache_hit")) and not corpus_items,
        "provider_failures": list((legacy_payload or {}).get("provider_failures") or []),
        "query_family": query_family,
        "provider_policy": provider_policy,
        "provider_fallback_allowed": provider_fallback_allowed,
        "seed_domains": list(seed_domains),
        "discovery_source_counts": discovery_source_counts,
        "discovery_classification": discovery_classification,
        "beacon_corpus": {
            "db_path": str(store.db_path),
            "corpus_hit_count": len(corpus_items),
            "connector_fill_count": discovery_source_counts["connector"],
            "external_fill_count": discovery_source_counts["provider"],
            "provenance_counts": provenance_counts,
            "query_family": query_family,
            "provider_policy": provider_policy,
            "provider_fallback_allowed": provider_fallback_allowed,
            "seed_domains": list(seed_domains),
            "discovery_source_counts": discovery_source_counts,
            "discovery_classification": discovery_classification,
        },
        "_search_broker_path": path,
        "_search_broker_used_legacy": legacy_payload is not None,
    }
