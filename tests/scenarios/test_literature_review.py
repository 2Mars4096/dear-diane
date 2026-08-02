"""Scenario A: Academic Literature Review.

Validates that a literature review request triggers:
1. Multiple web_search calls for different angles
2. web_fetch for paper metadata
3. list_directory / pdf_read for local papers
4. Structured output with real citations
5. Follow-up for inaccessible papers
"""

import pytest
from tests.scenarios.conftest import ToolCallTracker


class TestLiteratureReviewScenario:
    """Validate the literature review multi-turn flow."""

    def test_expected_tool_sequence(self, tool_tracker):
        """A lit review should require multiple web searches and optionally local file checks."""
        expected_tools = ["web_search", "web_search", "web_fetch", "list_directory", "pdf_read"]
        for tool in expected_tools:
            tool_tracker.record(tool, {}, "mock result")

        assert tool_tracker.count("web_search") >= 2
        assert tool_tracker.count("web_fetch") >= 1

    def test_output_has_citations(self):
        """Final output should contain real-looking citation patterns."""
        mock_output = (
            "## Literature Review: Geopolitics and Trade\n\n"
            "### Trade Diversion\n"
            "Crosignani et al. (2026) find that U.S. export controls result in "
            "$100 billion in market cap losses (Journal of Financial Economics).\n\n"
            "### Supply Chain Effects\n"
            "Fajgelbaum & Khandelwal (2022) document the economic impacts of "
            "the US-China trade war on consumer prices.\n\n"
            "### Papers I couldn't fully access:\n"
            "- Autor et al. (2024) 'The China Shock Revisited' — only abstract available\n"
            "If you have PDFs for any of these, share the path."
        )
        assert "et al." in mock_output or "(" in mock_output
        assert "Journal" in mock_output
        assert "couldn't" in mock_output.lower() or "access" in mock_output.lower()

    def test_no_fabricated_citations(self, tool_tracker):
        """Every citation in the output should be backed by a tool call."""
        tool_tracker.record(
            "web_search",
            {"query": "export controls firms"},
            '{"results": [{"title": "Crosignani et al 2026"}]}',
        )
        tool_tracker.record(
            "web_fetch",
            {"url": "https://scholar.example.com"},
            "Journal of Financial Economics, 2026",
        )

        sourced_authors = ["Crosignani"]
        for author in sourced_authors:
            results_text = " ".join(c["result"] for c in tool_tracker.calls)
            assert author in results_text

    def test_follow_up_for_missing_papers(self):
        """When papers can't be fully accessed, LLM should ask user for PDFs."""
        inaccessible = ["abstract only", "paywalled", "couldn't access"]
        follow_up_patterns = ["share the path", "provide the PDF", "have PDF"]
        assert len(inaccessible) > 0
        assert len(follow_up_patterns) > 0

    def test_multiple_search_angles(self, tool_tracker):
        """Lit review should search different facets, not repeat the same query."""
        queries = [
            "geopolitics international trade literature review",
            "export controls firm-level effects",
            "trade war supply chain disruption",
            "sanctions trade diversion academic papers",
        ]
        for q in queries:
            tool_tracker.record("web_search", {"query": q}, '{"results": [], "count": 0}')

        unique_queries = {c["args"]["query"] for c in tool_tracker.calls_for("web_search")}
        assert len(unique_queries) >= 3, "Should search at least 3 distinct angles"

    def test_local_paper_check(self, tool_tracker, mock_pdf_content):
        """LLM should check local filesystem for papers the user might have."""
        tool_tracker.record(
            "list_directory",
            {"path": "~/Dropbox/Projects/papers/"},
            '["trade_flows_2024.pdf", "crosignani_2026.pdf"]',
        )
        tool_tracker.record(
            "pdf_read",
            {"path": "~/Dropbox/Projects/papers/crosignani_2026.pdf"},
            mock_pdf_content,
        )

        assert tool_tracker.count("list_directory") >= 1
        assert tool_tracker.count("pdf_read") >= 1
        pdf_result = tool_tracker.calls_for("pdf_read")[0]["result"]
        assert "Crosignani" in pdf_result

    def test_output_structure(self):
        """Output should have thematic sections, not just a flat list."""
        mock_output = (
            "## Literature Review\n\n"
            "### 1. Trade Diversion\n"
            "Content about trade diversion...\n\n"
            "### 2. Firm-Level Costs\n"
            "Content about firm costs...\n\n"
            "### 3. Supply Chain Restructuring\n"
            "Content about supply chains...\n\n"
            "### Research Gaps\n"
            "Areas needing further study..."
        )
        assert mock_output.count("###") >= 3, "Should have at least 3 thematic sections"
        assert "gap" in mock_output.lower(), "Should identify research gaps"
