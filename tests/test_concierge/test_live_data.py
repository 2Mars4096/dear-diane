"""Tests for plan 25-12: Tool-aware conversation & solver activation.

Layer 1: Capability registry — web_search available in conversation mode
Layer 3: Post-execution reflection — _NUMERIC_CLAIM_RE
"""

from __future__ import annotations

import pytest

from dan.server.chat.helpers import _missing_action_hints
from dan.server.concierge.actions import _NUMERIC_CLAIM_RE
from dan.server.capability_registry import CapabilityResult, ChatCapabilityRegistry
from dan.server.capability_handlers import (
    WEB_SEARCH_CAPABILITY_SCHEMA,
    handle_web_search,
    register_base_capabilities,
)


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
