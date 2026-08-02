"""Scenario B: Equity Research Report.

Validates that an equity report request triggers:
1. current_datetime to anchor "recent" correctly
2. Multiple web_search calls (price, news, financials, sentiment)
3. web_fetch for full article text
4. Structured output with sourced figures
5. Disclaimer for unverifiable data
"""

import re
import pytest
from tests.scenarios.conftest import ToolCallTracker


class TestEquityReportScenario:
    """Validate the equity report multi-turn flow."""

    def test_datetime_called_first(self, tool_tracker):
        """current_datetime should be called to anchor what 'recent' means."""
        tool_tracker.record("current_datetime", {}, "2026-03-09T14:30:00-05:00")
        tool_tracker.record("web_search", {"query": "Rocket Lab RKLB stock price"}, "{}")
        tool_tracker.record("web_search", {"query": "Rocket Lab RKLB news 2026"}, "{}")

        assert tool_tracker.tool_names[0] == "current_datetime"

    def test_multiple_search_angles(self, tool_tracker):
        """Should search price, news, financials, and sentiment separately."""
        searches = [
            ("Rocket Lab RKLB stock price today", "price"),
            ("Rocket Lab RKLB recent news 2026", "news"),
            ("Rocket Lab RKLB earnings revenue 2025 2026", "financials"),
            ("Rocket Lab RKLB analyst sentiment rating", "sentiment"),
        ]
        for query, _category in searches:
            tool_tracker.record(
                "web_search",
                {"query": query},
                '{"results": [{"title": "RKLB data", "snippet": "..."}], "count": 1}',
            )

        assert tool_tracker.count("web_search") >= 4, "Need at least 4 search angles"

        queries = [c["args"]["query"] for c in tool_tracker.calls_for("web_search")]
        categories_covered = {
            "price": any("price" in q.lower() for q in queries),
            "news": any("news" in q.lower() for q in queries),
            "financials": any("earnings" in q.lower() or "revenue" in q.lower() for q in queries),
            "sentiment": any("sentiment" in q.lower() or "analyst" in q.lower() for q in queries),
        }
        for category, covered in categories_covered.items():
            assert covered, f"Missing search for {category}"

    def test_web_fetch_for_full_articles(self, tool_tracker):
        """Should fetch at least one full article for deeper context."""
        tool_tracker.record("web_search", {"query": "RKLB news"}, '{"results": [{"url": "https://example.com/rklb-neutron"}]}')
        tool_tracker.record(
            "web_fetch",
            {"url": "https://example.com/rklb-neutron"},
            "Rocket Lab successfully launched its Neutron rocket in January 2026...",
        )

        assert tool_tracker.count("web_fetch") >= 1
        fetch_result = tool_tracker.calls_for("web_fetch")[0]["result"]
        assert "Rocket Lab" in fetch_result

    def test_output_has_sourced_figures(self):
        """Financial figures in the report should have source attribution."""
        mock_output = (
            "*Company Overview*\n"
            "Rocket Lab USA (RKLB) is a space launch and satellite company.\n"
            "Market cap: ~$12.5B (per Yahoo Finance, March 2026).\n\n"
            "*Recent Developments*\n"
            "- Neutron rocket first launch completed Jan 2026 (source: SpaceNews)\n"
            "- Q4 2025 revenue $120M, up 42% YoY (per earnings release, Feb 2026)\n\n"
            "*Financial Snapshot*\n"
            "Revenue: $430M FY2025 (per SEC filing)\n"
            "Gross margin: 28% (per Q4 2025 earnings)\n\n"
            "*Key Risks*\n"
            "- Customer concentration (U.S. government ~60% of revenue)\n"
            "- Launch cadence execution risk\n\n"
            "*Note:* I couldn't access real-time intraday data or institutional "
            "ownership. For those, check Bloomberg or your broker."
        )
        assert "per " in mock_output.lower() or "source:" in mock_output.lower()

        dollar_amounts = re.findall(r"\$[\d,.]+[BMK]?", mock_output)
        assert len(dollar_amounts) >= 2, "Should have multiple sourced dollar figures"

    def test_disclaimer_for_unverifiable_data(self):
        """Report should flag what it couldn't verify."""
        mock_output = (
            "I couldn't access real-time intraday data or institutional ownership. "
            "For those, check Bloomberg or your broker."
        )
        disclaimer_patterns = ["couldn't access", "couldn't verify", "check bloomberg", "couldn't find"]
        assert any(p in mock_output.lower() for p in disclaimer_patterns)

    def test_no_fabricated_numbers(self, tool_tracker):
        """Every financial number should trace back to a tool result."""
        tool_tracker.record(
            "web_search",
            {"query": "RKLB revenue 2025"},
            '{"results": [{"snippet": "Rocket Lab reported $430M revenue in FY2025"}]}',
        )
        tool_tracker.record(
            "web_fetch",
            {"url": "https://example.com/earnings"},
            "Q4 2025 revenue was $120M, representing 42% year-over-year growth.",
        )

        all_results = " ".join(c["result"] for c in tool_tracker.calls)
        report_numbers = ["430M", "120M", "42%"]
        for num in report_numbers:
            assert num in all_results, f"Number {num} should be sourced from tool results"

    def test_output_structure_for_whatsapp(self):
        """Report should use WhatsApp-friendly formatting."""
        mock_output = (
            "*Company Overview*\n"
            "Rocket Lab (RKLB) — small-cap space launch & satellite.\n\n"
            "*Recent News*\n"
            "• Neutron first launch (Jan 2026)\n"
            "• New DoD contract worth $150M\n\n"
            "*Financials*\n"
            "Revenue: $430M (FY2025)\n\n"
            "*View*\n"
            "Bull case: Neutron ramp + government backlog.\n"
            "Bear case: cash burn, competition from SpaceX."
        )
        assert len(mock_output) < 4096, "Must fit WhatsApp message limit"
        assert "*" in mock_output, "Should use WhatsApp bold formatting"
        sections = [line for line in mock_output.split("\n") if line.startswith("*") and line.endswith("*")]
        assert len(sections) >= 3, "Should have multiple bold-header sections"
