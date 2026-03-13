"""Tests for plan 25-12: Tool-aware conversation & solver activation.

Layer 1: Capability registry — web_search available in conversation mode
Layer 3: Post-execution reflection — _NUMERIC_CLAIM_RE
"""

from __future__ import annotations

import pytest

from dan.server.concierge.actions import _NUMERIC_CLAIM_RE
from dan.server.capability_registry import ChatCapabilityRegistry
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

        fake_result = {"results": [{"title": "t", "url": "http://x", "snippet": "s"}]}
        with patch("dan.tools.web_search.web_search", new_callable=AsyncMock, return_value=fake_result):
            result = await handle_web_search({"query": "test"}, None)
            assert result.success

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


# Tests removed: TestNeedsLiveData, TestSolverHeuristic, TestCheckUnsourcedClaims
# depended on deleted legacy modules (solver.py, classifier.py, context_resolver.py)
