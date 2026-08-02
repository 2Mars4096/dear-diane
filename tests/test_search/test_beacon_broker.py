from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dan.search import (
    SearchBroker,
    SearchBrokerMode,
    SearchBrokerProvidersExhaustedError,
    SearchBrokerRequest,
    resolve_search_broker_mode,
)
from dan.tools.web_search import WebSearchProvidersExhaustedError


def test_resolve_search_broker_mode_defaults_to_legacy_for_unknown_values() -> None:
    assert resolve_search_broker_mode("beacon") is SearchBrokerMode.BEACON
    assert resolve_search_broker_mode("hybrid") is SearchBrokerMode.HYBRID
    assert resolve_search_broker_mode("unknown") is SearchBrokerMode.LEGACY


@pytest.mark.asyncio
async def test_search_broker_beacon_mode_falls_back_to_legacy_when_unconfigured() -> None:
    legacy_executor = AsyncMock(return_value={"results": [], "count": 0, "provider": "legacy"})
    broker = SearchBroker(legacy_executor=legacy_executor)

    result = await broker.search(
        SearchBrokerRequest(query="fallback query", num_results=3),
        mode=SearchBrokerMode.BEACON,
    )

    legacy_executor.assert_awaited_once()
    assert result.payload["provider"] == "legacy"
    assert result.trace.requested_mode == "beacon"
    assert result.trace.effective_mode == "legacy"
    assert result.trace.path == "legacy_tool_fallback"
    assert result.trace.used_legacy is True
    assert result.trace.fallback_reason == "beacon_executor_unavailable"
    assert result.trace.comparison_mode == "fallback"
    assert result.trace.discovery_backends == ("legacy",)
    assert result.trace.query_family is None
    assert result.trace.provider_policy is None
    assert result.trace.provider_fallback_allowed is None
    assert result.trace.discovery_classification == "no_results"
    assert result.trace.discovery_source_counts == {"corpus": 0, "connector": 0, "provider": 0}


@pytest.mark.asyncio
async def test_search_broker_hybrid_mode_uses_beacon_executor_when_available() -> None:
    legacy_executor = AsyncMock(return_value={"results": [], "count": 0, "provider": "legacy"})
    beacon_executor = AsyncMock(return_value={"results": [], "count": 0, "provider": "beacon"})
    broker = SearchBroker(
        legacy_executor=legacy_executor,
        beacon_executor=beacon_executor,
    )

    result = await broker.search(
        SearchBrokerRequest(query="hybrid query", num_results=2, fetch_content=True),
        mode=SearchBrokerMode.HYBRID,
    )

    legacy_executor.assert_not_awaited()
    beacon_executor.assert_awaited_once()
    assert result.payload["provider"] == "beacon"
    assert result.trace.requested_mode == "hybrid"
    assert result.trace.effective_mode == "hybrid"
    assert result.trace.path == "beacon_broker"
    assert result.trace.used_legacy is False
    assert result.trace.discovery_backends == ("beacon",)
    assert result.trace.query_family is None
    assert result.trace.discovery_classification == "no_results"
    assert result.trace.discovery_source_counts == {"corpus": 0, "connector": 0, "provider": 0}
    assert result.trace.active_stages == ("discover", "fetch/render")


@pytest.mark.asyncio
async def test_search_broker_hybrid_mode_can_shadow_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAN_SEARCH_BROKER_SHADOW_LEGACY", "1")
    legacy_executor = AsyncMock(
        return_value={
            "results": [{"title": "legacy", "url": "https://legacy.example.com"}],
            "provider": "legacy",
        }
    )
    beacon_executor = AsyncMock(
        return_value={
            "results": [{"title": "beacon", "url": "https://beacon.example.com"}],
            "provider": "beacon",
        }
    )
    broker = SearchBroker(legacy_executor=legacy_executor, beacon_executor=beacon_executor)

    result = await broker.search(SearchBrokerRequest(query="shadow query"), mode=SearchBrokerMode.HYBRID)

    beacon_executor.assert_awaited_once()
    legacy_executor.assert_awaited_once()
    assert result.trace.comparison_mode == "shadow"
    assert result.payload["beacon_shadow"]["mode"] == "legacy"
    assert result.payload["beacon_shadow"]["result_count"] == 1
    assert result.trace.discovery_classification == "no_results"


@pytest.mark.asyncio
async def test_search_broker_wraps_legacy_provider_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise_failure(**_kwargs):
        failure = RuntimeError("HTTP 429")
        raise WebSearchProvidersExhaustedError(
            [{"provider": "brave", "summary": "HTTP 429", "retryable": True}],
            failure,
        )

    monkeypatch.setattr("dan.tools.web_search.web_search", _raise_failure)
    broker = SearchBroker()

    with pytest.raises(SearchBrokerProvidersExhaustedError) as exc_info:
        await broker.search(SearchBrokerRequest(query="fail"), mode=SearchBrokerMode.LEGACY)

    assert exc_info.value.retryable is True
    assert exc_info.value.error_type == "provider_error"
    assert exc_info.value.provider_failures[0]["provider"] == "brave"
