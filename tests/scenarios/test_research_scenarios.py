"""Chat-level regression tests for research report scenarios.

Validates the multi-turn tool-call loop in ChatManager.send_message_with_tools()
by mocking the LLM provider to return scripted tool_calls sequences, and mocking
capability handlers to return realistic fake data.  Ensures the system:
  - selects the right tools (web_search, web_fetch, current_datetime)
  - performs multiple searches
  - fetches actual content
  - produces structured, source-backed output

Scenario A: Equity research report
Scenario B: General deep research report
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dan.providers import CompletionResult
from dan.server.audit import ChatAuditStore
from dan.server.capability_registry import (
    CapabilityContext,
    CapabilityResult,
    ChatCapabilityRegistry,
)
from dan.server.chat_manager import (
    ChatCompleteEvent,
    ChatManager,
    ChatToolCallResultEvent,
    ChatToolCallStartEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMPTY_GRAPH_DICT: dict[str, Any] = {
    "version": "dan_graph_v1",
    "metadata": {"name": "test-research", "description": ""},
    "nodes": [],
    "edges": [],
    "sub_graphs": {},
    "entry_points": [],
    "exit_points": [],
}


def _tc(name: str, arguments: dict[str, Any], call_id: str | None = None) -> dict[str, Any]:
    """Build a single OpenAI-style tool_call dict."""
    return {
        "id": call_id or f"call_{uuid.uuid4().hex[:10]}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments),
        },
    }


class _ScriptedProvider:
    """Mock LLM provider that yields pre-configured CompletionResult per call."""

    def __init__(self, turns: list[CompletionResult]) -> None:
        self._turns = list(turns)
        self._call_index = 0

    async def complete(self, **kwargs: Any) -> CompletionResult:
        if self._call_index >= len(self._turns):
            return CompletionResult(text="(exhausted mock turns)", tool_calls=None)
        result = self._turns[self._call_index]
        self._call_index += 1
        return result

    async def stream(self, **kwargs: Any):  # noqa: ANN201
        raise NotImplementedError("streaming not used in tool-call path")


def _build_registry_and_context() -> tuple[ChatCapabilityRegistry, CapabilityContext]:
    """Build a minimal capability registry with mock handlers for research tools."""
    registry = ChatCapabilityRegistry()

    async def _mock_web_search(args: dict, ctx: CapabilityContext) -> CapabilityResult:
        query = args.get("query", "")
        return CapabilityResult(
            success=True,
            message=(
                f"Search results for '{query}':\n\n"
                "1. Bloomberg — AAPL surges 3% on strong Q3 earnings — https://bloomberg.com/aapl-q3\n\n"
                "2. Reuters — Apple reports record services revenue — https://reuters.com/apple-services\n\n"
                "3. Yahoo Finance — AAPL Stock Price Today — https://finance.yahoo.com/AAPL"
            ),
            data={"results": [
                {"title": "Bloomberg", "snippet": "AAPL surges 3%", "url": "https://bloomberg.com/aapl-q3"},
                {"title": "Reuters", "snippet": "Apple record services", "url": "https://reuters.com/apple-services"},
                {"title": "Yahoo Finance", "snippet": "AAPL price", "url": "https://finance.yahoo.com/AAPL"},
            ]},
        )

    async def _mock_web_fetch(args: dict, ctx: CapabilityContext) -> CapabilityResult:
        url = args.get("url", "")
        return CapabilityResult(
            success=True,
            message=(
                f"Content from {url}:\n\n"
                "Apple Inc. (AAPL) reported Q3 2025 earnings of $1.40 per share, "
                "beating analyst estimates of $1.35. Revenue came in at $85.8 billion, "
                "up 8% year-over-year. Services revenue hit a record $24.2 billion. "
                "The company raised its dividend by 4% and announced a $110 billion "
                "share buyback program. CEO Tim Cook noted strong demand across all "
                "product categories."
            ),
            data={"url": url, "content_length": 450},
        )

    async def _mock_current_datetime(args: dict, ctx: CapabilityContext) -> CapabilityResult:
        return CapabilityResult(
            success=True,
            message="Local: Monday, March 09, 2026 10:30 AM | UTC: 2026-03-09 18:30:00",
            data={"local": "2026-03-09T10:30:00", "utc": "2026-03-09T18:30:00"},
        )

    from dan.server.capability_registry import build_tool_schema

    registry.register(
        "web_search",
        build_tool_schema("web_search", "Search the web", {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }),
        _mock_web_search,
        modes=["agent", "conversation"],
    )
    registry.register(
        "web_fetch",
        build_tool_schema("web_fetch", "Fetch a URL", {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        }),
        _mock_web_fetch,
        modes=["agent", "conversation"],
    )
    registry.register(
        "current_datetime",
        build_tool_schema("current_datetime", "Get current datetime", {
            "type": "object", "properties": {},
        }),
        _mock_current_datetime,
        modes=["agent", "conversation"],
    )

    ctx = CapabilityContext(workflow_id="test-research")
    return registry, ctx


def _make_provider_registry(provider: _ScriptedProvider) -> Any:
    """Return a mock ProviderRegistry whose resolve() always returns *provider*."""
    reg = MagicMock()
    reg.resolve.return_value = provider
    return reg


def _make_graph_store() -> Any:
    """Return a mock GraphStore that serves the empty graph dict."""
    store = MagicMock()
    store.get_graph.return_value = dict(EMPTY_GRAPH_DICT)
    return store


async def _collect_events(event_iter):  # noqa: ANN001, ANN202
    """Drain an async iterator of ChatStreamEvents into a list."""
    events = []
    async for evt in event_iter:
        events.append(evt)
    return events


# ---------------------------------------------------------------------------
# Scenario A: Equity Research Report
# ---------------------------------------------------------------------------

EQUITY_FINAL_REPORT = """\
# Apple Inc. (AAPL) — Equity Research Report

**Date:** March 9, 2026

## Executive Summary

Apple continues to demonstrate strong fundamentals with Q3 2025 earnings \
beating analyst expectations. The stock has gained 3% on the back of record \
services revenue and robust product demand.

## Financial Overview

- **EPS:** $1.40 (beat estimate of $1.35)
- **Revenue:** $85.8B (+8% YoY)
- **Services Revenue:** $24.2B (record)
- **Dividend:** Raised 4%
- **Buyback:** $110B program announced

## Market Sentiment

Recent news coverage from Bloomberg and Reuters indicates positive sentiment. \
Analysts remain bullish on the services growth trajectory.

## Valuation & Price Target

Based on current metrics and peer comparison, our 12-month price target is $245.

## Risks

- Regulatory headwinds in EU and US
- Supply chain concentration in Asia
- Currency headwinds from strong USD

## Sources

- Bloomberg: AAPL Q3 Earnings (https://bloomberg.com/aapl-q3)
- Reuters: Apple Services Revenue (https://reuters.com/apple-services)
- Yahoo Finance: AAPL Price Data (https://finance.yahoo.com/AAPL)
"""


def _build_equity_turns() -> list[CompletionResult]:
    """Scripted LLM turns for the equity research scenario."""
    turn1 = CompletionResult(
        text="",
        tool_calls=[
            _tc("current_datetime", {}),
            _tc("web_search", {"query": "AAPL Apple stock price today 2026"}),
        ],
    )
    turn2 = CompletionResult(
        text="",
        tool_calls=[
            _tc("web_search", {"query": "Apple AAPL latest earnings Q3 2025 results"}),
            _tc("web_search", {"query": "AAPL financial statements revenue services 2025"}),
            _tc("web_search", {"query": "Apple stock analyst target price 2026"}),
        ],
    )
    turn3 = CompletionResult(
        text="",
        tool_calls=[
            _tc("web_fetch", {"url": "https://bloomberg.com/aapl-q3"}),
        ],
    )
    turn_final = CompletionResult(
        text=EQUITY_FINAL_REPORT,
        tool_calls=None,
    )
    return [turn1, turn2, turn3, turn_final]


@pytest.mark.asyncio
async def test_equity_research_report(tmp_path, monkeypatch) -> None:
    """Equity research: verifies multi-turn tool usage and structured output."""
    audit_dir = tmp_path / "audit"
    _orig_audit_init = ChatAuditStore.__init__
    monkeypatch.setattr(
        ChatAuditStore, "__init__",
        lambda self, base_dir=None: _orig_audit_init(self, base_dir=str(audit_dir)),
    )

    provider = _ScriptedProvider(_build_equity_turns())
    registry, ctx = _build_registry_and_context()
    cm = ChatManager(
        provider_registry=_make_provider_registry(provider),
        graph_store=_make_graph_store(),
        capability_registry=registry,
        capability_context=ctx,
    )

    events = await _collect_events(
        cm.send_message_with_tools(
            workflow_id="test-research",
            message="Write an equity research report on Apple (AAPL) with current market data.",
            history=[],
            mode="conversation",
            max_tool_turns=10,
        )
    )

    tool_starts = [e for e in events if isinstance(e, ChatToolCallStartEvent)]
    tool_results = [e for e in events if isinstance(e, ChatToolCallResultEvent)]
    completes = [e for e in events if isinstance(e, ChatCompleteEvent)]

    tool_names_called = [e.tool_name for e in tool_starts]

    # current_datetime was called at least once
    assert "current_datetime" in tool_names_called, (
        f"Expected current_datetime call, got: {tool_names_called}"
    )

    # At least 3 distinct web_search calls
    search_calls = [e for e in tool_starts if e.tool_name == "web_search"]
    assert len(search_calls) >= 3, (
        f"Expected >=3 web_search calls, got {len(search_calls)}"
    )

    # Verify distinct queries by parsing args_preview
    search_queries: set[str] = set()
    for sc in search_calls:
        try:
            args = json.loads(sc.args_preview)
            search_queries.add(args.get("query", ""))
        except (json.JSONDecodeError, TypeError):
            pass
    assert len(search_queries) >= 3, (
        f"Expected >=3 distinct queries, got {len(search_queries)}: {search_queries}"
    )

    # web_fetch was called at least once
    fetch_calls = [e for e in tool_starts if e.tool_name == "web_fetch"]
    assert len(fetch_calls) >= 1, "Expected at least 1 web_fetch call"

    # All tool results succeeded
    for tr in tool_results:
        assert tr.status == "success", f"Tool {tr.tool_name} failed: {tr.output_preview}"

    # Final response checks
    assert len(completes) == 1, f"Expected 1 ChatCompleteEvent, got {len(completes)}"
    final = completes[0]
    content = final.content

    # Structured sections — look for headers or bold markers
    assert "## " in content or "**" in content, "Final report lacks section headers"
    assert any(
        keyword in content.lower()
        for keyword in ("financial", "revenue", "earnings", "summary")
    ), "Final report missing expected financial sections"

    # Sources section present
    assert "source" in content.lower() or "http" in content.lower(), (
        "Final report should reference sources/URLs"
    )

    # -- Audit provenance --
    store = ChatAuditStore()
    records = store.load_by_surface("server")
    assert len(records) >= 1, "Audit record should be persisted"
    rec = records[0]
    assert rec.user_message == "Write an equity research report on Apple (AAPL) with current market data."
    assert len(rec.tool_calls) >= 1
    audit_tool_names = {tc.tool_name for tc in rec.tool_calls}
    assert "web_search" in audit_tool_names
    assert "web_fetch" in audit_tool_names
    assert rec.assistant_message
    assert len(rec.cited_sources) >= 1, "Should capture source URLs"


# ---------------------------------------------------------------------------
# Scenario B: Deep Research Report
# ---------------------------------------------------------------------------

DEEP_RESEARCH_FINAL_REPORT = """\
# The Impact of Large Language Models on Scientific Research

## Introduction

Large Language Models (LLMs) are transforming how scientific research is \
conducted across multiple disciplines. This report examines the current \
state of LLM adoption in research, covering methodology, applications, \
limitations, and future directions.

## Current Applications

### Drug Discovery
LLMs are being used to predict protein structures and identify potential \
drug candidates, reducing early-stage discovery time by up to 40%.

### Materials Science
Generative models trained on materials databases can propose novel \
compounds with target properties.

### Climate Modeling
Transformer architectures have improved weather prediction accuracy \
for 10-day forecasts by 15% over traditional numerical methods.

### Literature Review Automation
AI-assisted systematic reviews now process thousands of papers in hours \
rather than months.

## Methodology Concerns

- **Hallucination risk:** LLMs can generate plausible but incorrect scientific claims
- **Reproducibility:** Model outputs vary with temperature and prompt phrasing
- **Bias amplification:** Training data biases propagate into research conclusions

## Ethical Considerations

Authorship attribution, peer review integrity, and data privacy remain \
open questions as LLM usage grows in academia.

## Future Directions

Multi-modal models combining text, images, and structured data will \
likely dominate the next wave of AI-assisted research tools.

## Sources

1. Nature: AI in Drug Discovery (https://nature.com/ai-drug-discovery)
2. Science: LLMs for Materials (https://science.org/llm-materials)
3. PNAS: Climate AI Models (https://pnas.org/climate-ai)
4. arXiv: Systematic Review Automation (https://arxiv.org/review-auto)
5. MIT Technology Review: LLM Research Ethics (https://technologyreview.com/llm-ethics)
"""


def _build_deep_research_turns() -> list[CompletionResult]:
    """Scripted LLM turns for the deep research scenario."""
    turn1 = CompletionResult(
        text="",
        tool_calls=[
            _tc("current_datetime", {}),
            _tc("web_search", {"query": "large language models scientific research impact 2025 2026"}),
            _tc("web_search", {"query": "LLM drug discovery protein structure prediction"}),
            _tc("web_search", {"query": "AI materials science generative models novel compounds"}),
        ],
    )
    turn2 = CompletionResult(
        text="",
        tool_calls=[
            _tc("web_search", {"query": "transformer climate modeling weather prediction accuracy"}),
            _tc("web_search", {"query": "LLM systematic literature review automation research"}),
            _tc("web_search", {"query": "AI research ethics authorship hallucination risk"}),
            _tc("web_fetch", {"url": "https://nature.com/ai-drug-discovery"}),
            _tc("web_fetch", {"url": "https://arxiv.org/review-auto"}),
        ],
    )
    turn3 = CompletionResult(
        text="",
        tool_calls=[
            _tc("web_fetch", {"url": "https://pnas.org/climate-ai"}),
            _tc("web_search", {"query": "LLM hallucination scientific claims reproducibility concerns"}),
        ],
    )
    turn_final = CompletionResult(
        text=DEEP_RESEARCH_FINAL_REPORT,
        tool_calls=None,
    )
    return [turn1, turn2, turn3, turn_final]


@pytest.mark.asyncio
async def test_deep_research_report(tmp_path, monkeypatch) -> None:
    """Deep research: verifies broad multi-angle search and structured output."""
    audit_dir = tmp_path / "audit"
    _orig_audit_init = ChatAuditStore.__init__
    monkeypatch.setattr(
        ChatAuditStore, "__init__",
        lambda self, base_dir=None: _orig_audit_init(self, base_dir=str(audit_dir)),
    )

    provider = _ScriptedProvider(_build_deep_research_turns())
    registry, ctx = _build_registry_and_context()
    cm = ChatManager(
        provider_registry=_make_provider_registry(provider),
        graph_store=_make_graph_store(),
        capability_registry=registry,
        capability_context=ctx,
    )

    events = await _collect_events(
        cm.send_message_with_tools(
            workflow_id="test-research",
            message=(
                "Write a comprehensive research report on the impact of "
                "large language models on scientific research."
            ),
            history=[],
            mode="conversation",
            max_tool_turns=10,
        )
    )

    tool_starts = [e for e in events if isinstance(e, ChatToolCallStartEvent)]
    tool_results = [e for e in events if isinstance(e, ChatToolCallResultEvent)]
    completes = [e for e in events if isinstance(e, ChatCompleteEvent)]

    tool_names_called = [e.tool_name for e in tool_starts]

    # At least 5 distinct web_search queries
    search_calls = [e for e in tool_starts if e.tool_name == "web_search"]
    search_queries: set[str] = set()
    for sc in search_calls:
        try:
            args = json.loads(sc.args_preview)
            search_queries.add(args.get("query", ""))
        except (json.JSONDecodeError, TypeError):
            pass

    assert len(search_queries) >= 5, (
        f"Expected >=5 distinct search queries, got {len(search_queries)}: {search_queries}"
    )

    # web_fetch called on at least 2 URLs
    fetch_calls = [e for e in tool_starts if e.tool_name == "web_fetch"]
    fetch_urls: set[str] = set()
    for fc in fetch_calls:
        try:
            args = json.loads(fc.args_preview)
            fetch_urls.add(args.get("url", ""))
        except (json.JSONDecodeError, TypeError):
            pass
    assert len(fetch_urls) >= 2, (
        f"Expected >=2 distinct fetch URLs, got {len(fetch_urls)}: {fetch_urls}"
    )

    # All tool results succeeded
    for tr in tool_results:
        assert tr.status == "success", f"Tool {tr.tool_name} failed: {tr.output_preview}"

    # Final response checks
    assert len(completes) == 1, f"Expected 1 ChatCompleteEvent, got {len(completes)}"
    final = completes[0]
    content = final.content

    # Has a Sources section
    assert "source" in content.lower(), "Final report should have a Sources section"

    # Multiple sub-topics: at least 3 section headers (## or bold headers)
    section_count = content.count("## ")
    assert section_count >= 3, (
        f"Expected >=3 section headers (##), got {section_count}"
    )

    # Covers multiple domains
    topics_covered = sum(
        1 for topic in ("drug", "climate", "material", "ethic", "literature")
        if topic in content.lower()
    )
    assert topics_covered >= 3, (
        f"Expected report to cover >=3 sub-topics, matched {topics_covered}"
    )

    # -- Audit provenance --
    store = ChatAuditStore()
    records = store.load_by_surface("server")
    assert len(records) >= 1, "Audit record should be persisted"
    rec = records[0]
    assert rec.user_message == (
        "Write a comprehensive research report on the impact of "
        "large language models on scientific research."
    )
    assert len(rec.tool_calls) >= 1
    audit_tool_names = {tc.tool_name for tc in rec.tool_calls}
    assert "web_search" in audit_tool_names
    assert "web_fetch" in audit_tool_names
    assert rec.assistant_message
    assert len(rec.cited_sources) >= 1, "Should capture source URLs"


# ---------------------------------------------------------------------------
# Scenario: Tool loop respects max_tool_turns
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_tool_turns_cap(tmp_path, monkeypatch) -> None:
    """Verify the loop terminates gracefully when max_tool_turns is reached."""
    audit_dir = tmp_path / "audit"
    _orig_audit_init = ChatAuditStore.__init__
    monkeypatch.setattr(
        ChatAuditStore, "__init__",
        lambda self, base_dir=None: _orig_audit_init(self, base_dir=str(audit_dir)),
    )

    infinite_search = CompletionResult(
        text="",
        tool_calls=[_tc("web_search", {"query": "more data please"})],
    )
    final_text = CompletionResult(
        text="Here is a summary based on available data.",
        tool_calls=None,
    )
    # 5 search turns, then a final. With max_tool_turns=3, the loop should
    # cap at 3 and yield a complete event with whatever result is available.
    turns = [infinite_search] * 5 + [final_text]
    provider = _ScriptedProvider(turns)
    registry, ctx = _build_registry_and_context()
    cm = ChatManager(
        provider_registry=_make_provider_registry(provider),
        graph_store=_make_graph_store(),
        capability_registry=registry,
        capability_context=ctx,
    )

    events = await _collect_events(
        cm.send_message_with_tools(
            workflow_id="test-research",
            message="Search for everything.",
            history=[],
            mode="conversation",
            max_tool_turns=3,
        )
    )

    tool_starts = [e for e in events if isinstance(e, ChatToolCallStartEvent)]
    completes = [e for e in events if isinstance(e, ChatCompleteEvent)]

    # Should not exceed 3 tool turns (each turn = 1 search call here)
    assert len(tool_starts) <= 3, (
        f"max_tool_turns=3 but got {len(tool_starts)} tool calls"
    )
    # Should still produce a final event
    assert len(completes) == 1, "Should yield exactly one ChatCompleteEvent"
    assert completes[0].content, "ChatCompleteEvent should have content"
