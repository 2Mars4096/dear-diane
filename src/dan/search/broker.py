"""Beacon Search broker boundary.

This module introduces an internal search broker seam without changing the
caller-visible `web_search` / `web_fetch` tool IDs. The initial rollout keeps
the legacy provider-backed tool path as the execution backend while exposing
explicit `legacy` / `hybrid` / `beacon` mode selection for later cutover work.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from enum import Enum
import os
from typing import Any, Awaitable, Callable


class SearchBrokerMode(str, Enum):
    LEGACY = "legacy"
    HYBRID = "hybrid"
    BEACON = "beacon"


_DEFAULT_SEARCH_BROKER_MODE = SearchBrokerMode.LEGACY
_SEARCH_BROKER_MODE_ENV = "DAN_SEARCH_BROKER_MODE"
_SEARCH_BROKER_SHADOW_LEGACY_ENV = "DAN_SEARCH_BROKER_SHADOW_LEGACY"


class SearchBrokerProvidersExhaustedError(RuntimeError):
    """Broker-level provider exhaustion error decoupled from adapter classes."""

    def __init__(self, provider_failures: list[dict[str, Any]], last_error: Exception):
        self.provider_failures = copy.deepcopy(provider_failures)
        self.last_error = last_error
        self.retryable = getattr(last_error, "retryable", False) or any(
            bool(item.get("retryable")) for item in provider_failures
        )
        self.error_type = str(getattr(last_error, "error_type", "") or "").strip() or "provider_error"
        super().__init__(str(last_error).strip() or "All web search providers failed.")


def resolve_search_broker_mode(value: Any = None) -> SearchBrokerMode:
    if isinstance(value, SearchBrokerMode):
        return value
    raw = value
    if raw is None:
        raw = os.environ.get(_SEARCH_BROKER_MODE_ENV, _DEFAULT_SEARCH_BROKER_MODE.value)
    normalized = str(raw or "").strip().lower()
    for mode in SearchBrokerMode:
        if normalized == mode.value:
            return mode
    return _DEFAULT_SEARCH_BROKER_MODE


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SearchBrokerRequest:
    query: str = ""
    url: str = ""
    num_results: int = 5
    search_depth: str = "quick"
    allowed_domains: tuple[str, ...] = ()
    blocked_domains: tuple[str, ...] = ()
    location: dict[str, str] = field(default_factory=dict)
    max_provider_searches: int = 4
    fetch_content: bool | None = None
    max_fetched_results: int = 2
    browser_fallback: bool = False


@dataclass(frozen=True)
class SearchBrokerTrace:
    requested_mode: str
    effective_mode: str
    path: str
    used_legacy: bool
    fallback_reason: str | None = None
    comparison_mode: str = "single_path"
    discovery_role: str = "adapter"
    discovery_backends: tuple[str, ...] = ()
    query_family: str | None = None
    provider_policy: str | None = None
    provider_fallback_allowed: bool | None = None
    discovery_classification: str = "no_results"
    discovery_source_counts: dict[str, int] = field(default_factory=dict)
    declared_stages: tuple[str, ...] = (
        "discover",
        "fetch/render",
        "extract/chunk",
        "retrieve/rank",
        "cite",
    )
    active_stages: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_mode": self.requested_mode,
            "effective_mode": self.effective_mode,
            "path": self.path,
            "used_legacy": self.used_legacy,
            "fallback_reason": self.fallback_reason,
            "comparison_mode": self.comparison_mode,
            "discovery_role": self.discovery_role,
            "discovery_backends": list(self.discovery_backends),
            "query_family": self.query_family,
            "provider_policy": self.provider_policy,
            "provider_fallback_allowed": self.provider_fallback_allowed,
            "discovery_classification": self.discovery_classification,
            "discovery_source_counts": dict(self.discovery_source_counts),
            "declared_stages": list(self.declared_stages),
            "active_stages": list(self.active_stages),
        }


@dataclass(frozen=True)
class SearchBrokerResponse:
    payload: dict[str, Any]
    trace: SearchBrokerTrace


SearchExecutor = Callable[[SearchBrokerRequest], Awaitable[dict[str, Any]]]

DISCOVERY_SOURCE_KEYS = ("corpus", "connector", "provider")


def normalize_discovery_source_counts(value: Any) -> dict[str, int]:
    counts = {key: 0 for key in DISCOVERY_SOURCE_KEYS}
    if not isinstance(value, dict):
        return counts
    for key in DISCOVERY_SOURCE_KEYS:
        raw = value.get(key)
        if isinstance(raw, (int, float)):
            counts[key] = max(0, int(raw))
    raw_external = value.get("external")
    if counts["provider"] <= 0 and isinstance(raw_external, (int, float)):
        counts["provider"] = max(0, int(raw_external))
    return counts


def classify_discovery_source_counts(value: Any) -> str:
    counts = normalize_discovery_source_counts(value)
    corpus = counts["corpus"]
    connector = counts["connector"]
    provider = counts["provider"]
    if provider > 0:
        if corpus > 0 or connector > 0:
            return "provider_fallback"
        return "provider_only"
    if connector > 0:
        return "connector_first"
    if corpus > 0:
        return "corpus_only"
    return "no_results"


def discovery_metadata_from_payload(
    payload: dict[str, Any],
    *,
    used_legacy: bool,
) -> tuple[str, dict[str, int]]:
    direct_counts = normalize_discovery_source_counts(payload.get("discovery_source_counts"))
    beacon_counts = normalize_discovery_source_counts(
        dict(payload.get("beacon_corpus") or {}).get("discovery_source_counts")
    )
    counts = direct_counts if any(direct_counts.values()) else beacon_counts
    if not any(counts.values()):
        results = list(payload.get("results") or [])
        for item in results:
            evidence_source = str(item.get("evidence_source", "") or "").strip()
            if evidence_source in {"corpus", "fetched_corpus"}:
                counts["corpus"] += 1
            elif evidence_source in {"connector", "connector_discovery", "first_party_discovery"}:
                counts["connector"] += 1
            elif evidence_source == "external_discovery":
                counts["provider"] += 1
        if not any(counts.values()) and used_legacy and results:
            counts["provider"] = len(results)
    direct_classification = str(payload.get("discovery_classification", "") or "").strip()
    beacon_classification = str(
        dict(payload.get("beacon_corpus") or {}).get("discovery_classification", "") or ""
    ).strip()
    classification = direct_classification or beacon_classification or classify_discovery_source_counts(
        counts
    )
    return classification, counts


def _active_stages_for_request(request: SearchBrokerRequest) -> tuple[str, ...]:
    stages: list[str] = ["discover"]
    should_fetch = bool(request.url) or bool(request.fetch_content) or str(
        request.search_depth or ""
    ).strip().lower() == "thorough"
    if should_fetch:
        stages.append("fetch/render")
    return tuple(stages)


async def _execute_via_legacy_tool(request: SearchBrokerRequest) -> dict[str, Any]:
    from dan.tools.web_search import WebSearchProvidersExhaustedError, web_search

    kwargs: dict[str, Any] = {
        "query": request.query,
        "num_results": int(request.num_results),
        "search_depth": request.search_depth,
        "allowed_domains": list(request.allowed_domains),
        "blocked_domains": list(request.blocked_domains),
        "location": dict(request.location),
        "max_provider_searches": int(request.max_provider_searches),
        "max_fetched_results": int(request.max_fetched_results),
        "browser_fallback": bool(request.browser_fallback),
    }
    if request.url:
        kwargs["url"] = request.url
    if request.fetch_content is not None:
        kwargs["fetch_content"] = bool(request.fetch_content)
    try:
        return await web_search(**kwargs)
    except WebSearchProvidersExhaustedError as exc:
        raise SearchBrokerProvidersExhaustedError(exc.provider_failures, exc) from exc


def _normalize_discovery_backends(payload: dict[str, Any]) -> tuple[str, ...]:
    backends: list[str] = []
    primary = str(payload.get("provider", "") or "").strip()
    if primary:
        backends.append(primary)
    for item in payload.get("providers") or []:
        provider = str(item or "").strip()
        if provider and provider not in backends:
            backends.append(provider)
    return tuple(backends)


def _make_trace(
    *,
    requested_mode: SearchBrokerMode,
    effective_mode: SearchBrokerMode,
    path: str,
    used_legacy: bool,
    payload: dict[str, Any],
    fallback_reason: str | None = None,
    comparison_mode: str = "single_path",
    active_stages: tuple[str, ...] = (),
) -> SearchBrokerTrace:
    discovery_classification, discovery_source_counts = discovery_metadata_from_payload(
        payload,
        used_legacy=used_legacy,
    )
    return SearchBrokerTrace(
        requested_mode=requested_mode.value,
        effective_mode=effective_mode.value,
        path=path,
        used_legacy=used_legacy,
        fallback_reason=fallback_reason,
        comparison_mode=comparison_mode,
        discovery_backends=_normalize_discovery_backends(payload),
        query_family=str(payload.get("query_family", "") or "").strip() or None,
        provider_policy=str(payload.get("provider_policy", "") or "").strip() or None,
        provider_fallback_allowed=(
            bool(payload.get("provider_fallback_allowed"))
            if payload.get("provider_fallback_allowed") is not None
            else None
        ),
        discovery_classification=discovery_classification,
        discovery_source_counts=discovery_source_counts,
        active_stages=active_stages,
    )


class SearchBroker:
    """Internal broker boundary for Beacon Search rollout."""

    def __init__(
        self,
        *,
        legacy_executor: SearchExecutor | None = None,
        beacon_executor: SearchExecutor | None = None,
    ) -> None:
        self._legacy_executor = legacy_executor or _execute_via_legacy_tool
        self._beacon_executor = beacon_executor

    async def search(
        self,
        request: SearchBrokerRequest,
        *,
        mode: SearchBrokerMode | str | None = None,
    ) -> SearchBrokerResponse:
        requested_mode = resolve_search_broker_mode(mode)
        active_stages = _active_stages_for_request(request)

        if requested_mode is SearchBrokerMode.LEGACY:
            payload = await self._legacy_executor(request)
            return SearchBrokerResponse(
                payload=payload,
                trace=_make_trace(
                    requested_mode=requested_mode,
                    effective_mode=SearchBrokerMode.LEGACY,
                    path="legacy_tool",
                    used_legacy=True,
                    payload=payload,
                    active_stages=active_stages,
                ),
            )

        if self._beacon_executor is None:
            payload = await self._legacy_executor(request)
            return SearchBrokerResponse(
                payload=payload,
                trace=_make_trace(
                    requested_mode=requested_mode,
                    effective_mode=SearchBrokerMode.LEGACY,
                    path="legacy_tool_fallback",
                    used_legacy=True,
                    payload=payload,
                    fallback_reason="beacon_executor_unavailable",
                    comparison_mode="fallback",
                    active_stages=active_stages,
                ),
            )

        shadow_payload: dict[str, Any] | None = None
        if requested_mode is SearchBrokerMode.HYBRID and _env_bool(_SEARCH_BROKER_SHADOW_LEGACY_ENV, False):
            primary, shadow = await asyncio.gather(
                self._beacon_executor(request),
                self._legacy_executor(request),
                return_exceptions=True,
            )
            if isinstance(primary, Exception):
                raise primary
            payload = primary
            if isinstance(shadow, dict):
                shadow_payload = shadow
            elif isinstance(shadow, SearchBrokerProvidersExhaustedError):
                shadow_payload = {
                    "provider_failures": shadow.provider_failures,
                    "error": str(shadow),
                }
        else:
            payload = await self._beacon_executor(request)
        trace_path = str(payload.pop("_search_broker_path", "") or "").strip() or "beacon_broker"
        trace_used_legacy = bool(payload.pop("_search_broker_used_legacy", False))
        trace_fallback_reason = str(payload.pop("_search_broker_fallback_reason", "") or "").strip() or None
        trace_comparison_mode = (
            str(payload.pop("_search_broker_comparison_mode", "") or "").strip() or "single_path"
        )
        if shadow_payload is not None:
            payload["beacon_shadow"] = {
                "mode": SearchBrokerMode.LEGACY.value,
                "provider": str(shadow_payload.get("provider", "") or "").strip(),
                "providers": list(shadow_payload.get("providers") or []),
                "result_count": len(list(shadow_payload.get("results") or [])),
                "provider_failures": list(shadow_payload.get("provider_failures") or []),
                "error": str(shadow_payload.get("error", "") or "").strip() or None,
            }
            trace_comparison_mode = "shadow"
        return SearchBrokerResponse(
            payload=payload,
            trace=_make_trace(
                requested_mode=requested_mode,
                effective_mode=requested_mode,
                path=trace_path,
                used_legacy=trace_used_legacy,
                payload=payload,
                fallback_reason=trace_fallback_reason,
                comparison_mode=trace_comparison_mode,
                active_stages=active_stages,
            ),
        )


_DEFAULT_SEARCH_BROKER: SearchBroker | None = None


def get_default_search_broker() -> SearchBroker:
    global _DEFAULT_SEARCH_BROKER
    if _DEFAULT_SEARCH_BROKER is None:
        from dan.search.beacon import execute_beacon_search
        from dan.search.corpus import get_default_beacon_corpus_store

        async def _default_beacon_executor(request: SearchBrokerRequest) -> dict[str, Any]:
            return await execute_beacon_search(
                request,
                legacy_executor=_execute_via_legacy_tool,
                corpus_store=get_default_beacon_corpus_store(),
            )

        _DEFAULT_SEARCH_BROKER = SearchBroker(beacon_executor=_default_beacon_executor)
    return _DEFAULT_SEARCH_BROKER
