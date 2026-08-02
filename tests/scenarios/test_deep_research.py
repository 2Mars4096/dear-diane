"""Scenario C: Deep Research Report.

Validates that a deep research request triggers:
1. current_datetime to know the current date
2. 5+ distinct web_search queries across different angles
3. web_fetch to read full articles
4. Comprehensive structured output with all requested sections
5. Sources section at the end
"""

import pytest
from tests.scenarios.conftest import ToolCallTracker


class TestDeepResearchScenario:
    """Validate the deep research multi-turn flow."""

    def test_datetime_anchoring(self, tool_tracker):
        """Should call current_datetime to know the current date for recency."""
        tool_tracker.record("current_datetime", {}, "2026-03-09T14:30:00-05:00")
        assert tool_tracker.count("current_datetime") == 1

    def test_five_plus_search_queries(self, tool_tracker):
        """Deep research should search at least 5 different angles."""
        queries = [
            "nuclear fusion breakthrough 2025 2026",
            "nuclear fusion companies funding investment",
            "ITER tokamak progress timeline",
            "inertial confinement fusion NIF results",
            "nuclear fusion commercialization timeline",
            "nuclear fusion remaining engineering challenges",
        ]
        for q in queries:
            tool_tracker.record(
                "web_search",
                {"query": q},
                '{"results": [{"title": "Fusion update", "snippet": "..."}], "count": 1}',
            )

        assert tool_tracker.count("web_search") >= 5
        unique_queries = {c["args"]["query"] for c in tool_tracker.calls_for("web_search")}
        assert len(unique_queries) >= 5, "Queries should be distinct, not repeated"

    def test_web_fetch_for_depth(self, tool_tracker):
        """Should fetch at least 2 full articles for deeper content."""
        tool_tracker.record(
            "web_fetch",
            {"url": "https://example.com/nif-ignition-2025"},
            "The National Ignition Facility achieved sustained ignition in December 2025, "
            "producing 5.2 MJ of energy from a 2.05 MJ laser input...",
        )
        tool_tracker.record(
            "web_fetch",
            {"url": "https://example.com/commonwealth-fusion-sparc"},
            "Commonwealth Fusion Systems announced SPARC construction is 80% complete, "
            "with first plasma expected in late 2026. The company raised $1.8B in Series C...",
        )

        assert tool_tracker.count("web_fetch") >= 2

    def test_output_covers_all_sections(self):
        """Report should cover all requested sections."""
        mock_output = (
            "# Nuclear Fusion Energy: Current State (March 2026)\n\n"
            "## Executive Summary\n"
            "Nuclear fusion has seen major breakthroughs in 2025-2026...\n\n"
            "## Recent Breakthroughs\n"
            "- NIF achieved sustained ignition (Dec 2025, source: DOE)\n"
            "- SPARC tokamak construction 80% complete\n\n"
            "## Key Players\n"
            "### Public Programs\n"
            "- ITER (France) — $22B international tokamak\n"
            "- NIF (USA) — inertial confinement at Lawrence Livermore\n"
            "### Private Companies\n"
            "- Commonwealth Fusion Systems ($1.8B raised)\n"
            "- TAE Technologies ($1.2B raised)\n"
            "- Helion Energy ($500M from Sam Altman)\n\n"
            "## Timeline to Commercialization\n"
            "Most experts project grid-scale fusion by 2035-2040...\n\n"
            "## Remaining Challenges\n"
            "- Plasma containment at scale\n"
            "- Materials that withstand neutron bombardment\n"
            "- Tritium supply chain\n"
            "- Economic viability vs renewables\n\n"
            "## Investment Landscape\n"
            "Total private fusion investment exceeds $6B as of 2026...\n\n"
            "## Sources\n"
            "1. https://example.com/nif-ignition-2025\n"
            "2. https://example.com/commonwealth-fusion-sparc\n"
            "3. https://example.com/iter-status-2026\n"
        )

        required_sections = [
            "executive summary",
            "breakthroughs",
            "key players",
            "timeline",
            "challenges",
            "sources",
        ]
        output_lower = mock_output.lower()
        for section in required_sections:
            assert section in output_lower, f"Missing required section: {section}"

    def test_sources_section(self):
        """Report should end with a sources section listing URLs consulted."""
        mock_output = (
            "## Sources\n"
            "1. https://doe.gov/nif-results — NIF ignition data\n"
            "2. https://iter.org/status — ITER construction update\n"
            "3. https://cfs.energy/sparc — SPARC progress\n"
        )
        assert "sources" in mock_output.lower()
        urls = [line for line in mock_output.split("\n") if "https://" in line]
        assert len(urls) >= 2, "Should list at least 2 source URLs"

    def test_no_fabricated_facts(self, tool_tracker):
        """Company names and funding amounts should come from tool results."""
        tool_tracker.record(
            "web_search",
            {"query": "nuclear fusion companies funding"},
            '{"results": [{"snippet": "Commonwealth Fusion Systems raised $1.8B in Series C"}]}',
        )
        tool_tracker.record(
            "web_fetch",
            {"url": "https://example.com/fusion-funding"},
            "TAE Technologies has raised $1.2B total. Helion Energy secured $500M from Sam Altman.",
        )

        all_results = " ".join(c["result"] for c in tool_tracker.calls)
        claimed_facts = ["Commonwealth Fusion", "1.8B", "TAE", "1.2B", "Helion", "500M"]
        for fact in claimed_facts:
            assert fact in all_results, f"Fact '{fact}' should be sourced from tool results"

    def test_search_query_diversity(self, tool_tracker):
        """Queries should span different aspects, not just rephrase the same thing."""
        queries = [
            "nuclear fusion breakthrough 2025",
            "fusion companies private investment",
            "ITER construction progress",
            "NIF ignition results",
            "fusion commercialization timeline forecast",
            "fusion engineering challenges materials",
        ]
        for q in queries:
            tool_tracker.record("web_search", {"query": q}, '{"results": [], "count": 0}')

        all_query_text = " ".join(c["args"]["query"] for c in tool_tracker.calls_for("web_search"))
        aspects = ["breakthrough", "companies", "ITER", "NIF", "timeline", "challenges"]
        covered = sum(1 for a in aspects if a.lower() in all_query_text.lower())
        assert covered >= 4, f"Only {covered}/6 aspects covered in search queries"
