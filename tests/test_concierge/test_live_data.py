"""Tests for plan 25-12: Tool-aware conversation & solver activation.

Layer 1: Capability registry — web_search available in conversation mode
Layer 3: Post-execution reflection — _NUMERIC_CLAIM_RE
"""

from __future__ import annotations

import asyncio
import importlib
import httpx
from unittest.mock import AsyncMock

import pytest

from dan.server.chat.helpers import _missing_action_hints
from dan.server.concierge.actions import _NUMERIC_CLAIM_RE
from dan.server.capability_registry import CapabilityResult, ChatCapabilityRegistry
from dan.server.capability_handlers import (
    WEB_SEARCH_CAPABILITY_SCHEMA,
    handle_web_fetch,
    handle_web_search,
    register_base_capabilities,
)

web_fetch_tool_mod = importlib.import_module("dan.tools.web_fetch")
web_search_tool_mod = importlib.import_module("dan.tools.web_search")


def _clear_tool_state(module, *names: str) -> None:
    for name in names:
        value = getattr(module, name, None)
        if hasattr(value, "clear"):
            value.clear()


@pytest.fixture(autouse=True)
def _clear_web_tool_state():
    _clear_tool_state(web_search_tool_mod, "_SEARCH_CACHE", "_SEARCH_INFLIGHT")
    _clear_tool_state(web_fetch_tool_mod, "_FETCH_CACHE", "_FETCH_INFLIGHT")
    yield
    _clear_tool_state(web_search_tool_mod, "_SEARCH_CACHE", "_SEARCH_INFLIGHT")
    _clear_tool_state(web_fetch_tool_mod, "_FETCH_CACHE", "_FETCH_INFLIGHT")


def _patch_fetch_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(web_fetch_tool_mod.httpx, "AsyncClient", client_factory)


def _http_status_error(url: str, status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"HTTP {status_code}", request=request, response=response)


# ── Layer 1: Capability registry ──────────────────────────────────


class TestWebSearchCapabilityRegistration:
    def _registry(self) -> ChatCapabilityRegistry:
        reg = ChatCapabilityRegistry()
        register_base_capabilities(reg)
        return reg

    def test_web_search_registered_for_conversation_mode(self):
        reg = self._registry()
        assert "web_search" in reg.list_tool_names("conversation")

    @pytest.mark.skip(reason="web_search mode filtering deferred to tiered executor implementation")
    def test_web_search_not_in_ask_mode(self):
        reg = self._registry()
        assert "web_search" not in reg.list_tool_names("ask")

    def test_web_search_not_in_plan_mode(self):
        reg = self._registry()
        assert "web_search" not in reg.list_tool_names("plan")

    @pytest.mark.asyncio
    async def test_web_search_handler_returns_results(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [{"title": "t", "url": "http://x", "snippet": "s"}],
            "provider": "tavily",
        }
        with patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result):
            result = await handle_web_search({"query": "test"}, None)
            assert result.success
            assert '[1] t' in result.message
            assert "Snippet: s" in result.message
            assert "URL: http://x" in result.message
            assert result.data["provider"] == "tavily"
            assert result.data["grounded_result_count"] == 0

    @pytest.mark.asyncio
    async def test_web_search_handler_surfaces_cache_and_fallback_metadata(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [{"title": "t", "url": "http://x", "snippet": "s"}],
            "provider": "brave",
            "cache_hit": True,
            "provider_failures": [{"provider": "tavily", "summary": "HTTP 503"}],
        }
        with patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result):
            result = await handle_web_search({"query": "test"}, None)

        assert result.success
        assert 'Web search results for "test" (provider: brave; cache hit)' in result.message
        assert "Fallbacks: tavily: HTTP 503" in result.message
        assert result.data["provider_failures"] == [{"provider": "tavily", "summary": "HTTP 503"}]

    @pytest.mark.asyncio
    async def test_web_search_handler_fetches_top_results_in_parallel(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [
                {"title": "r1", "url": "http://x1", "snippet": "s1"},
                {"title": "r2", "url": "http://x2", "snippet": "s2"},
                {"title": "r3", "url": "http://x3", "snippet": "s3"},
            ],
            "provider": "tavily",
        }

        async def _fake_fetch(*, url: str):
            return {"content": f"content for {url}"}

        with (
            patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result),
            patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, side_effect=_fake_fetch) as mock_fetch,
        ):
            result = await handle_web_search(
                {"query": "test", "num_results": 3, "fetch_content": True},
                None,
            )

        assert result.success
        assert mock_fetch.await_count == 2
        assert "Fetched page excerpts (top results fetched in parallel)" in result.message
        assert "[1] Fetched content from http://x1" in result.message
        assert "[2] Fetched content from http://x2" in result.message
        assert result.data["grounded_result_count"] == 2
        assert len(result.data["fetched_results"]) == 2

    @pytest.mark.asyncio
    async def test_web_search_handler_surfaces_fetch_failures(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "results": [
                {"title": "r1", "url": "http://x1", "snippet": "s1"},
                {"title": "r2", "url": "http://x2", "snippet": "s2"},
            ]
        }

        async def _fake_fetch(*, url: str):
            if url.endswith("x2"):
                raise RuntimeError("boom")
            return {"content": f"content for {url}"}

        with (
            patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result),
            patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, side_effect=_fake_fetch),
        ):
            result = await handle_web_search(
                {"query": "test", "num_results": 2, "fetch_content": True},
                None,
            )

        assert result.success
        assert "Fetch failed for http://x2: boom" in result.message
        assert result.data["grounded_result_count"] == 1

    @pytest.mark.asyncio
    async def test_web_search_handler_empty_query(self):
        result = await handle_web_search({"query": ""}, None)
        assert not result.success

    @pytest.mark.asyncio
    async def test_web_fetch_handler_surfaces_fetch_metadata(self):
        from unittest.mock import AsyncMock, patch

        fake_result = {
            "url": "https://example.com",
            "content": "hello world",
            "status_code": 200,
            "content_type": "text/html; charset=utf-8",
            "cache_hit": True,
        }
        with patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, return_value=fake_result):
            result = await handle_web_fetch({"url": "https://example.com"}, None)

        assert result.success
        assert result.message.startswith(
            "Fetched https://example.com (HTTP 200; text/html; cache hit)"
        )
        assert result.data["cache_hit"] is True

    @pytest.mark.asyncio
    async def test_web_fetch_handler_http_error_returns_failure(self):
        from unittest.mock import AsyncMock, patch

        request = httpx.Request("GET", "https://example.com/missing")
        response = httpx.Response(404, request=request)
        error = httpx.HTTPStatusError("HTTP 404", request=request, response=response)

        with patch("dan.tools.web_fetch.web_fetch", new_callable=AsyncMock, side_effect=error):
            result = await handle_web_fetch({"url": "https://example.com/missing"}, None)

        assert not result.success
        assert result.error_type == "http_error"
        assert result.data["status_code"] == 404
        assert "HTTP 404" in result.message


class TestDirectWebToolBehavior:
    @pytest.mark.asyncio
    async def test_web_search_cache_hit_behavior(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("DAN_TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("DAN_BRAVE_API_KEY", raising=False)
        monkeypatch.setenv("DAN_WEB_SEARCH_CACHE_TTL_SECONDS", "900")

        mock_ddg = AsyncMock(return_value={
            "results": [{"title": "Original", "url": "https://example.com", "snippet": "snippet"}],
            "count": 1,
            "provider": "duckduckgo",
        })
        monkeypatch.setattr(web_search_tool_mod, "_ddg_search", mock_ddg)

        first = await web_search_tool_mod.web_search("example query", 3)
        second = await web_search_tool_mod.web_search("example query", 3)

        assert mock_ddg.await_count == 1
        assert first["cache_hit"] is False
        assert second["cache_hit"] is True

        second["results"][0]["title"] = "mutated"
        third = await web_search_tool_mod.web_search("example query", 3)
        assert third["results"][0]["title"] == "Original"
        assert third["cache_hit"] is True

    @pytest.mark.asyncio
    async def test_web_search_shares_inflight_request(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("DAN_TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("DAN_BRAVE_API_KEY", raising=False)

        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def slow_ddg(query: str, num_results: int) -> dict:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {
                "results": [{"title": "Shared", "url": "https://example.com", "snippet": "snippet"}],
                "count": 1,
                "provider": "duckduckgo",
            }

        monkeypatch.setattr(web_search_tool_mod, "_ddg_search", slow_ddg)

        first_task = asyncio.create_task(web_search_tool_mod.web_search("shared query", 1))
        await started.wait()
        second_task = asyncio.create_task(web_search_tool_mod.web_search("shared query", 1))
        await asyncio.sleep(0)
        release.set()

        first, second = await asyncio.gather(first_task, second_task)

        assert calls == 1
        assert first["shared_inflight"] is False
        assert second["shared_inflight"] is True
        assert first["cache_hit"] is False
        assert second["cache_hit"] is False

    @pytest.mark.asyncio
    async def test_web_search_records_provider_failures_on_fallback(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DAN_TAVILY_API_KEY", "tavily-key")
        monkeypatch.setenv("DAN_BRAVE_API_KEY", "brave-key")

        monkeypatch.setattr(
            web_search_tool_mod,
            "_tavily_search",
            AsyncMock(side_effect=_http_status_error("https://api.tavily.com/search", 503)),
        )
        monkeypatch.setattr(
            web_search_tool_mod,
            "_brave_search",
            AsyncMock(return_value={
                "results": [{"title": "Brave", "url": "https://brave.example", "snippet": "ok"}],
                "count": 1,
                "provider": "brave",
            }),
        )

        result = await web_search_tool_mod.web_search("fallback query", 2)

        assert result["provider"] == "brave"
        assert result["cache_hit"] is False
        assert len(result["provider_failures"]) == 1
        failure = result["provider_failures"][0]
        assert failure["provider"] == "tavily"
        assert failure["error_type"] == "provider_error"
        assert failure["retryable"] is True
        assert failure["status_code"] == 503
        assert failure["summary"].startswith("HTTP 503")
        assert failure["message"] == "HTTP 503"

    @pytest.mark.asyncio
    async def test_web_fetch_cache_hit_behavior(self, monkeypatch: pytest.MonkeyPatch):
        calls = 0
        user_agents: list[str | None] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            user_agents.append(request.headers.get("User-Agent"))
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<h1>Example Domain</h1><p>Hello world.</p>",
            )

        _patch_fetch_transport(monkeypatch, handler)
        monkeypatch.setenv("DAN_WEB_FETCH_CACHE_TTL_SECONDS", "900")

        first = await web_fetch_tool_mod.web_fetch("https://example.com")
        second = await web_fetch_tool_mod.web_fetch("https://example.com")

        assert calls == 1
        assert user_agents and user_agents[0] and "deep-agent-network" in user_agents[0]
        assert first["cache_hit"] is False
        assert second["cache_hit"] is True

        second["content"] = "mutated"
        third = await web_fetch_tool_mod.web_fetch("https://example.com")
        assert "Example Domain" in third["content"]
        assert third["cache_hit"] is True

    @pytest.mark.asyncio
    async def test_web_fetch_shares_inflight_request(self, monkeypatch: pytest.MonkeyPatch):
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return httpx.Response(
                200,
                request=request,
                headers={"content-type": "text/plain"},
                text="shared content",
            )

        _patch_fetch_transport(monkeypatch, handler)

        first_task = asyncio.create_task(web_fetch_tool_mod.web_fetch("https://example.com/shared"))
        await started.wait()
        second_task = asyncio.create_task(web_fetch_tool_mod.web_fetch("https://example.com/shared"))
        await asyncio.sleep(0)
        release.set()

        first, second = await asyncio.gather(first_task, second_task)

        assert calls == 1
        assert first["shared_inflight"] is False
        assert second["shared_inflight"] is True
        assert first["cache_hit"] is False
        assert second["cache_hit"] is False

    @pytest.mark.asyncio
    async def test_web_fetch_raises_on_http_error(self, monkeypatch: pytest.MonkeyPatch):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                request=request,
                headers={"content-type": "text/html"},
                text="<h1>Not Found</h1>",
            )

        _patch_fetch_transport(monkeypatch, handler)

        with pytest.raises(httpx.HTTPStatusError):
            await web_fetch_tool_mod.web_fetch("https://example.com/missing")


# ── Layer 3: Post-execution reflection ────────────────────────────


class TestNumericClaimRegex:
    def test_matches_dollar_amounts(self):
        assert _NUMERIC_CLAIM_RE.search("The price is $70.11")

    def test_matches_percentages(self):
        assert _NUMERIC_CLAIM_RE.search("Up 15.3% today")

    def test_no_match_plain_text(self):
        assert _NUMERIC_CLAIM_RE.search("Hello world") is None


class TestSearchWebGroundingGate:
    def test_search_web_requires_more_than_snippet_only_search(self):
        missing = _missing_action_hints(
            ["search_web"],
            {"web_search"},
            tool_results=[{
                "tool_name": "web_search",
                "status": "success",
                "cap_result": CapabilityResult(success=True, message="", data={"count": 3}),
            }],
        )

        assert missing == ["search_web"]

    def test_search_web_satisfied_by_grounded_search_results(self):
        missing = _missing_action_hints(
            ["search_web"],
            {"web_search"},
            tool_results=[{
                "tool_name": "web_search",
                "status": "success",
                "cap_result": CapabilityResult(
                    success=True,
                    message="",
                    data={"grounded_result_count": 2},
                ),
            }],
        )

        assert missing == []

    def test_search_web_satisfied_by_web_fetch(self):
        missing = _missing_action_hints(
            ["search_web"],
            {"web_search", "web_fetch"},
            tool_results=[{
                "tool_name": "web_fetch",
                "status": "success",
                "cap_result": CapabilityResult(success=True, message="", data={}),
            }],
        )

        assert missing == []


# Tests removed: TestNeedsLiveData, TestSolverHeuristic, TestCheckUnsourcedClaims
# depended on deleted legacy modules (solver.py, classifier.py, context_resolver.py)
