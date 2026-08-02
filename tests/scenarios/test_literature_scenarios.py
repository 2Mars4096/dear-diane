"""Chat-level regression scenarios for literature research tasks.

Plan 29-8 tasks 5-3 (PDF literature summary) and 5-4 (literature search
with gated-paper handoff).  Validates tool selection, grounded
summarization, and explicit "ask the user for help" behaviour through
the ChatManager multi-turn tool loop.
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator

import pytest

from dan.providers import CompletionResult, StreamChunk
from dan.providers.registry import ProviderRegistry
from dan.server.capability_registry import (
    ALL_MODES,
    CapabilityContext,
    CapabilityResult,
    ChatCapabilityRegistry,
)
from dan.server.audit import ChatAuditStore
from dan.server.capability_handlers import (
    PDF_READ_CAPABILITY_SCHEMA,
    WEB_SEARCH_CAPABILITY_SCHEMA,
    WEB_FETCH_CAPABILITY_SCHEMA,
    LIST_DIRECTORY_CAPABILITY_SCHEMA,
)
from dan.server.chat_manager import (
    ChatCompleteEvent,
    ChatManager,
    ChatToolCallStartEvent,
    ChatToolCallResultEvent,
)
from dan.server.graph_store import GraphStore


# ── Constants & mock data ─────────────────────────────────────────────

_MINIMAL_GRAPH: dict[str, Any] = {
    "version": "dan_graph_v1",
    "metadata": {"name": "lit-scenario", "description": "stub for scenario tests"},
    "nodes": [
        {
            "id": "inp",
            "name": "Input",
            "node_type": "input",
            "input_ports": [],
            "output_ports": [
                {"name": "input", "json_schema": {"type": "object"}},
            ],
            "position": {"x": 0, "y": 0},
            "ui": {},
            "metadata": {},
            "variables": [],
        },
    ],
    "edges": [],
    "sub_graphs": {},
    "entry_points": ["inp"],
    "exit_points": ["inp"],
    "shared_context": [],
    "artifact_refs": [],
    "hyperedges": [],
}

_MOCK_PDF_TEXT = (
    "This paper examines the impact of trade wars on global supply chains. "
    "Using a panel dataset of 42 countries from 2015-2023, we find that "
    "bilateral tariff increases of 10% lead to a 3.2% decline in intermediate "
    "goods trade within 18 months. The effects are amplified through "
    "network spillovers in complex value chains. Our results suggest that "
    "firms partially offset tariff costs by reshoring production to allied "
    "countries, a pattern we term 'friend-shoring'. The welfare analysis "
    "indicates that small open economies bear disproportionate adjustment costs."
)

_MOCK_SEARCH_RESULTS_1 = (
    "Geopolitics and Trade: A Survey — Johnson & Lee (2024), "
    "Journal of International Economics "
    "— https://doi.org/10.1016/jie.2024.001\n\n"
    "The Political Economy of Trade Policy — Chen, Ramirez & Okonkwo (2023), "
    "American Economic Review "
    "— https://doi.org/10.1257/aer.2023.113\n\n"
    "Sanctions and Trade Diversion — Petrov & Nakamura (2024), "
    "Quarterly Journal of Economics "
    "— https://doi.org/10.1093/qje/2024.139"
)

_MOCK_SEARCH_RESULTS_2 = (
    "Supply Chain Resilience Under Geopolitical Risk — Fernandez & Gupta (2023), "
    "Management Science "
    "— https://www.nber.org/papers/w31234\n\n"
    "Trade Wars and Firm Relocation — Kim & Osei (2024), "
    "Review of Economic Studies "
    "— https://ssrn.com/abstract=4567890"
)

_MOCK_WEB_FETCH_CONTENT = (
    "Geopolitics and Trade: A Survey\n"
    "Authors: Robert Johnson, Wei Lee\n"
    "Abstract: This paper surveys the growing literature on how geopolitical "
    "tensions reshape international trade patterns. We identify three main "
    "channels: direct sanctions, trade policy uncertainty, and strategic "
    "decoupling. The empirical evidence suggests that geopolitical alignment "
    "now explains 15-20% of bilateral trade variation.\n"
    "Published in: Journal of International Economics, 2024\n"
    "Access: Subscription required"
)

# Every author surname present in the mock tool results.
_TOOL_RESULT_AUTHORS = {
    "Smith", "Johnson", "Lee", "Chen", "Ramirez", "Okonkwo",
    "Petrov", "Nakamura", "Fernandez", "Gupta", "Kim", "Osei",
    "Robert", "Wei",
}


# ── Mock provider ─────────────────────────────────────────────────────

class _SequentialMockProvider:
    """LLM provider returning pre-defined CompletionResults in order.

    Records every ``complete()`` call so tests can inspect what messages
    (including tool results) were sent to the LLM on each turn.
    """

    def __init__(self, turns: list[CompletionResult]) -> None:
        self._turns = list(turns)
        self._idx = 0
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> CompletionResult:
        self.calls.append(kwargs)
        if self._idx >= len(self._turns):
            return CompletionResult(text="(exhausted mock turns)")
        result = self._turns[self._idx]
        self._idx += 1
        return result

    async def stream(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        result = await self.complete(**kwargs)
        yield StreamChunk(delta=result.text, accumulated=result.text, done=True)


# ── Helpers ───────────────────────────────────────────────────────────

def _tc(name: str, args: dict[str, Any], call_id: str = "") -> dict[str, Any]:
    """Build an OpenAI-compatible tool_call dict."""
    return {
        "id": call_id or f"call_{name}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args),
        },
    }


async def _collect(stream: Any) -> list[Any]:
    return [evt async for evt in stream]


# ── Mock capability handlers ──────────────────────────────────────────

async def _mock_pdf_read(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    return CapabilityResult(
        success=True,
        message=(
            f"Title: Trade Wars and Supply Chains\n"
            f"Author: Smith et al.\n\n{_MOCK_PDF_TEXT}"
        ),
        data={
            "path": args.get("path", ""),
            "num_pages": 25,
            "metadata": {
                "title": "Trade Wars and Supply Chains",
                "author": "Smith et al.",
            },
        },
    )


async def _mock_web_search(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    q = args.get("query", "").lower()
    if "geopolit" in q or "international trade" in q:
        return CapabilityResult(success=True, message=_MOCK_SEARCH_RESULTS_1)
    return CapabilityResult(success=True, message=_MOCK_SEARCH_RESULTS_2)


async def _mock_web_fetch(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    return CapabilityResult(
        success=True,
        message=_MOCK_WEB_FETCH_CONTENT,
        data={"url": args.get("url", "")},
    )


async def _mock_list_directory(args: dict[str, Any], ctx: CapabilityContext) -> CapabilityResult:
    return CapabilityResult(
        success=True,
        message=f"{args.get('path', '.')}/ (empty)",
    )


# ── Shared fixture builders ──────────────────────────────────────────

def _build_cap_registry() -> ChatCapabilityRegistry:
    reg = ChatCapabilityRegistry()
    modes = list(ALL_MODES)
    reg.register("pdf_read", PDF_READ_CAPABILITY_SCHEMA, _mock_pdf_read, modes=modes)
    reg.register("web_search", WEB_SEARCH_CAPABILITY_SCHEMA, _mock_web_search, modes=modes)
    reg.register("web_fetch", WEB_FETCH_CAPABILITY_SCHEMA, _mock_web_fetch, modes=modes)
    reg.register("list_directory", LIST_DIRECTORY_CAPABILITY_SCHEMA, _mock_list_directory, modes=modes)
    return reg


def _make_manager(
    provider: _SequentialMockProvider,
    graph_store: GraphStore,
) -> ChatManager:
    pr = ProviderRegistry()
    pr.register("default", provider)
    ctx = CapabilityContext(workflow_id="lit-test")
    return ChatManager(
        provider_registry=pr,
        graph_store=graph_store,
        capability_registry=_build_cap_registry(),
        capability_context=ctx,
    )


@pytest.fixture()
def graph_store(tmp_path: Any) -> GraphStore:
    store = GraphStore(str(tmp_path / "graphs"))
    store.save_graph("lit-test", dict(_MINIMAL_GRAPH))
    return store


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test A  — PDF literature summary  (plan 29-8 task 5-3)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_PDF_SUMMARY_FINAL = (
    "## Summary: Trade Wars and Supply Chains (Smith et al.)\n\n"
    "### Key Findings\n"
    "- Bilateral tariff increases of 10% lead to a 3.2% decline in "
    "intermediate goods trade within 18 months.\n"
    "- Network spillovers amplify effects through complex value chains.\n"
    "- Firms partially offset tariff costs via 'friend-shoring' to allied "
    "countries.\n\n"
    "### Methodology\n"
    "Panel dataset covering 42 countries from 2015 to 2023.\n\n"
    "### Policy Implications\n"
    "The authors advocate for plurilateral trade agreements that account "
    "for supply chain interdependencies. Small open economies bear "
    "disproportionate adjustment costs."
)


@pytest.mark.asyncio
async def test_pdf_literature_summary(graph_store: GraphStore, tmp_path, monkeypatch) -> None:
    """pdf_read is called before the summary; response is grounded in PDF content."""
    audit_dir = tmp_path / "audit"
    _orig_audit_init = ChatAuditStore.__init__
    monkeypatch.setattr(
        ChatAuditStore, "__init__",
        lambda self, base_dir=None: _orig_audit_init(self, base_dir=str(audit_dir)),
    )

    provider = _SequentialMockProvider([
        CompletionResult(
            text="",
            tool_calls=[_tc("pdf_read", {"path": "~/papers/trade_flows.pdf"})],
        ),
        CompletionResult(text=_PDF_SUMMARY_FINAL),
    ])

    mgr = _make_manager(provider, graph_store)
    events = await _collect(
        mgr.send_message_with_tools(
            workflow_id="lit-test",
            message="Summarize the paper at ~/papers/trade_flows.pdf",
            history=[],
            mode="conversation",
        )
    )

    tool_starts = [e for e in events if isinstance(e, ChatToolCallStartEvent)]
    tool_results = [e for e in events if isinstance(e, ChatToolCallResultEvent)]

    # -- pdf_read called with the correct path --
    assert any(t.tool_name == "pdf_read" for t in tool_starts), \
        "pdf_read must be called"
    pdf_start = next(t for t in tool_starts if t.tool_name == "pdf_read")
    assert "trade_flows" in pdf_start.args_preview, \
        "pdf_read must receive the user-supplied path"

    # -- pdf_read succeeded --
    assert any(
        t.tool_name == "pdf_read" and t.status == "success" for t in tool_results
    )

    # -- pdf_read precedes final response --
    pdf_idx = next(
        i for i, e in enumerate(events)
        if isinstance(e, ChatToolCallStartEvent) and e.tool_name == "pdf_read"
    )
    complete_idx = next(
        i for i, e in enumerate(events) if isinstance(e, ChatCompleteEvent)
    )
    assert pdf_idx < complete_idx, "pdf_read must happen before the final summary"

    # -- Grounded content: final response references mock PDF data --
    final = next(e for e in events if isinstance(e, ChatCompleteEvent))
    text = final.content.lower()
    assert "smith" in text, "Must reference the paper's author (Smith et al.)"
    assert "supply chain" in text, "Must reference a core concept from the PDF"
    assert "friend-shoring" in text or "friend" in text, \
        "Must reference a specific finding from the PDF"

    # -- Structured response with identifiable sections --
    for keyword in ("finding", "methodology", "implication"):
        assert keyword in text, f"Response should contain a '{keyword}' section"

    # -- Provider received tool results on the second call --
    assert len(provider.calls) == 2, "Two LLM round-trips expected"
    second_call_msgs = provider.calls[1].get("messages", [])
    tool_msgs = [m for m in second_call_msgs if m.get("role") == "tool"]
    assert len(tool_msgs) >= 1, "Tool result must be fed back to the LLM"
    assert "Smith et al." in tool_msgs[0]["content"], \
        "PDF metadata must appear in the tool result fed to the LLM"

    # -- Audit provenance --
    store = ChatAuditStore()
    records = store.load_by_surface("server")
    assert len(records) >= 1, "Audit record should be persisted"
    rec = records[0]
    assert rec.user_message == "Summarize the paper at ~/papers/trade_flows.pdf"
    assert len(rec.tool_calls) >= 1
    assert any(tc.tool_name == "pdf_read" for tc in rec.tool_calls)
    assert rec.assistant_message
    pdf_tc = next(tc for tc in rec.tool_calls if tc.tool_name == "pdf_read")
    assert len(pdf_tc.source_files) >= 1, "pdf_read should record source files"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test B  — Literature search + gated-paper handoff  (plan 29-8 task 5-4)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_LIT_REVIEW_FINAL = (
    "## Literature Review: Geopolitics and International Trade\n\n"
    "### Overview\n"
    "The intersection of geopolitics and international trade has attracted "
    "substantial scholarly attention in recent years.\n\n"
    "### Key Themes\n"
    "1. **Trade Policy Under Geopolitical Tension**: Johnson & Lee (2024) "
    "survey the literature and find that geopolitical alignment explains "
    "15-20% of bilateral trade variation.\n"
    "2. **Supply Chain Resilience**: Fernandez & Gupta (2023) examine "
    "firm-level responses to geopolitical risk.\n"
    "3. **Sanctions and Trade Diversion**: Petrov & Nakamura (2024) "
    "document how sanctions redirect trade flows through third countries.\n\n"
    "### Gaps and Limitations\n"
    "Several highly cited papers were behind institutional paywalls.\n\n"
    "### Papers I Could Not Access\n"
    "I was unable to retrieve the full text of the following papers. "
    "Could you share the PDFs so I can provide a more complete review?\n"
    "- **Chen, Ramirez & Okonkwo (2023)** — 'The Political Economy of "
    "Trade Policy', American Economic Review\n"
    "- **Kim & Osei (2024)** — 'Trade Wars and Firm Relocation', "
    "Review of Economic Studies\n\n"
    "### Sources\n"
    "Johnson & Lee (2024); Chen, Ramirez & Okonkwo (2023); "
    "Petrov & Nakamura (2024); Fernandez & Gupta (2023); Kim & Osei (2024)"
)


@pytest.mark.asyncio
async def test_literature_search_with_handoff(graph_store: GraphStore, tmp_path, monkeypatch) -> None:
    """Multi-search, web fetch, directory check, and gated-paper handoff."""
    audit_dir = tmp_path / "audit"
    _orig_audit_init = ChatAuditStore.__init__
    monkeypatch.setattr(
        ChatAuditStore, "__init__",
        lambda self, base_dir=None: _orig_audit_init(self, base_dir=str(audit_dir)),
    )

    provider = _SequentialMockProvider([
        # Turn 1: two parallel web_search calls
        CompletionResult(
            text="",
            tool_calls=[
                _tc(
                    "web_search",
                    {"query": "geopolitics international trade literature review"},
                    "call_ws1",
                ),
                _tc(
                    "web_search",
                    {"query": "supply chain resilience geopolitical risk recent papers"},
                    "call_ws2",
                ),
            ],
        ),
        # Turn 2: web_fetch on an academic page
        CompletionResult(
            text="",
            tool_calls=[
                _tc(
                    "web_fetch",
                    {"url": "https://doi.org/10.1016/jie.2024.001"},
                    "call_wf1",
                ),
            ],
        ),
        # Turn 3: list_directory to check for local papers
        CompletionResult(
            text="",
            tool_calls=[
                _tc("list_directory", {"path": "~/papers"}, "call_ld1"),
            ],
        ),
        # Turn 4: final structured literature review with handoff
        CompletionResult(text=_LIT_REVIEW_FINAL),
    ])

    mgr = _make_manager(provider, graph_store)
    events = await _collect(
        mgr.send_message_with_tools(
            workflow_id="lit-test",
            message="Do a literature review on geopolitics and international trade",
            history=[],
            mode="conversation",
        )
    )

    tool_starts = [e for e in events if isinstance(e, ChatToolCallStartEvent)]
    tool_results = [e for e in events if isinstance(e, ChatToolCallResultEvent)]

    # -- At least 2 distinct web_search calls --
    ws_starts = [t for t in tool_starts if t.tool_name == "web_search"]
    assert len(ws_starts) >= 2, \
        f"Expected >=2 web_search calls, got {len(ws_starts)}"

    # -- web_fetch called at least once --
    wf_starts = [t for t in tool_starts if t.tool_name == "web_fetch"]
    assert len(wf_starts) >= 1, "web_fetch must be called at least once"

    # -- list_directory called --
    ld_starts = [t for t in tool_starts if t.tool_name == "list_directory"]
    assert len(ld_starts) >= 1, "list_directory must be called to check for local papers"

    # -- All tool calls succeeded --
    for tr in tool_results:
        assert tr.status == "success", \
            f"Tool {tr.tool_name} should succeed but got status={tr.status}"

    # -- Final response --
    final = next(e for e in events if isinstance(e, ChatCompleteEvent))
    text_lower = final.content.lower()

    # Handoff section present
    assert "could not access" in text_lower or "couldn't access" in text_lower, \
        "Response must contain a gated-paper handoff section"

    # Handoff names specific authors from tool results
    assert "chen" in text_lower, "Handoff must name Chen (from web_search)"
    assert "okonkwo" in text_lower, "Handoff must name Okonkwo (from web_search)"
    assert "kim" in text_lower, "Handoff must name Kim (from web_search)"
    assert "osei" in text_lower, "Handoff must name Osei (from web_search)"

    # Accessible-paper authors also present
    assert "johnson" in text_lower, "Must cite Johnson (from web_search/web_fetch)"
    assert "fernandez" in text_lower, "Must cite Fernandez (from web_search)"

    # -- No fabricated citations --
    # Extract capitalized words that look like surnames from the response,
    # then verify every one appears in our mock tool results.
    cited = set(re.findall(r"\b([A-Z][a-z]{2,})\b", final.content))
    non_name_words = {
        "The", "This", "Under", "Trade", "Supply", "Chain", "Review",
        "Literature", "Geopolitics", "International", "Economic", "Journal",
        "American", "Quarterly", "Economics", "Management", "Science",
        "Studies", "Political", "Economy", "Policy", "Overview",
        "Resilience", "Sanctions", "Diversion", "Geopolitical",
        "Could", "Not", "Papers", "Access", "Sources", "Themes", "Gaps",
        "Limitations", "Key", "Firm", "Relocation", "Wars",
        "Tension", "Bilateral", "Variation", "Several", "PDFs",
    }
    candidate_names = cited - non_name_words
    for name in candidate_names:
        assert name in _TOOL_RESULT_AUTHORS, (
            f"Author '{name}' in the response was not found in any tool result "
            "— possible fabricated citation"
        )

    # -- Provider received tool results across turns --
    assert len(provider.calls) == 4, \
        f"Expected 4 LLM round-trips, got {len(provider.calls)}"

    # Second call should contain both web_search tool result messages
    second_msgs = provider.calls[1].get("messages", [])
    tool_msgs = [m for m in second_msgs if m.get("role") == "tool"]
    assert len(tool_msgs) >= 2, \
        "Both web_search results must be fed back on the second call"

    # -- Audit provenance --
    store = ChatAuditStore()
    records = store.load_by_surface("server")
    assert len(records) >= 1, "Audit record should be persisted"
    rec = records[0]
    assert rec.user_message == "Do a literature review on geopolitics and international trade"
    assert len(rec.tool_calls) >= 1
    audit_tool_names = {tc.tool_name for tc in rec.tool_calls}
    assert "web_search" in audit_tool_names
    assert "web_fetch" in audit_tool_names
    assert rec.assistant_message
    assert len(rec.cited_sources) >= 1, "Should capture source URLs from search/fetch"
