"""Tests for research-quality prompt additions and source extraction (plan 29-8 task 4)."""

from __future__ import annotations

import pytest

from dan.server.chat_manager import (
    UNIFIED_SYSTEM_PROMPT,
    _RESEARCH_REPORT_PROMPT_HINT,
    _extract_cited_sources,
)
from dan.server.search_models import canonicalize_search_url


# ------------------------------------------------------------------
# Prompt content tests
# ------------------------------------------------------------------


class TestUnifiedPromptResearchSection:
    """Verify the research behaviour section exists in the hint and is
    correctly injected via the {task_hints} placeholder."""

    def test_contains_research_section(self):
        assert "## Research & Report Behavior" in _RESEARCH_REPORT_PROMPT_HINT

    def test_contains_multiple_search_instruction(self):
        assert "3-8 distinct web_search queries" in _RESEARCH_REPORT_PROMPT_HINT

    def test_contains_no_fabrication_rule(self):
        assert "NEVER fabricate" in UNIFIED_SYSTEM_PROMPT

    def test_contains_current_datetime_instruction(self):
        assert "current_datetime" in UNIFIED_SYSTEM_PROMPT

    def test_contains_sources_section_instruction(self):
        rendered = UNIFIED_SYSTEM_PROMPT.format(
            current_date="Today is Monday, 2026-03-15.",
            capability_reference="",
            module_hints=_RESEARCH_REPORT_PROMPT_HINT,
            context_block="",
            workflow_block="",
        )
        assert "Sources" in rendered or "source" in rendered.lower()

    def test_research_hint_injected_before_context(self):
        rendered = UNIFIED_SYSTEM_PROMPT.format(
            current_date="Today is Monday, 2026-03-15.",
            capability_reference="",
            module_hints=_RESEARCH_REPORT_PROMPT_HINT,
            context_block="[CONTEXT_BLOCK]",
            workflow_block="",
        )
        research_pos = rendered.index("## Research & Report Behavior")
        context_pos = rendered.index("[CONTEXT_BLOCK]")
        assert research_pos < context_pos

    def test_research_hint_injected_after_rules(self):
        rendered = UNIFIED_SYSTEM_PROMPT.format(
            current_date="Today is Monday, 2026-03-15.",
            capability_reference="",
            module_hints=_RESEARCH_REPORT_PROMPT_HINT,
            context_block="",
            workflow_block="",
        )
        rules_pos = rendered.index("## Rules")
        research_pos = rendered.index("## Research & Report Behavior")
        assert rules_pos < research_pos

    def test_format_placeholders_still_valid(self):
        rendered = UNIFIED_SYSTEM_PROMPT.format(
            current_date="[date]",
            capability_reference="[capref]",
            module_hints="[modules]",
            context_block="[context]",
            workflow_block="[workflow]",
        )
        assert "[modules]" in rendered
        assert "[context]" in rendered
        assert "[workflow]" in rendered
        assert "{module_hints}" not in rendered
        assert "{context_block}" not in rendered
        assert "{workflow_block}" not in rendered


# ------------------------------------------------------------------
# _extract_cited_sources tests
# ------------------------------------------------------------------


class TestExtractCitedSources:
    """Unit tests for URL / file-path extraction from tool call records."""

    def test_extracts_urls_from_web_search(self):
        records = [
            {
                "tool_name": "web_search",
                "args_preview": '{"query": "test"}',
                "output_preview": "Found: https://example.com/article and https://news.ycombinator.com/item?id=123",
            }
        ]
        sources = _extract_cited_sources(records)
        assert "https://example.com/article" in sources
        assert any("news.ycombinator.com" in s for s in sources)

    def test_extracts_urls_from_web_fetch(self):
        records = [
            {
                "tool_name": "web_fetch",
                "args_preview": '{"url": "https://arxiv.org/abs/2301.00001"}',
                "output_preview": "Content from https://arxiv.org/abs/2301.00001 ...",
            }
        ]
        sources = _extract_cited_sources(records)
        assert "https://arxiv.org/abs/2301.00001" in sources

    def test_extracts_file_path_from_pdf_read(self):
        records = [
            {
                "tool_name": "pdf_read",
                "args_preview": '{"path": "/Users/me/papers/survey.pdf"}',
                "output_preview": "Page 1 of 20...",
            }
        ]
        sources = _extract_cited_sources(records)
        assert "/Users/me/papers/survey.pdf" in sources

    def test_extracts_tilde_path_from_file_read(self):
        records = [
            {
                "tool_name": "file_read",
                "args_preview": '{"path": "~/Documents/report.csv"}',
                "output_preview": "header1,header2",
            }
        ]
        sources = _extract_cited_sources(records)
        assert "~/Documents/report.csv" in sources

    def test_deduplicates_sources(self):
        records = [
            {
                "tool_name": "web_search",
                "args_preview": "",
                "output_preview": "https://example.com https://example.com",
            },
            {
                "tool_name": "web_fetch",
                "args_preview": '{"url": "https://example.com"}',
                "output_preview": "Content from https://example.com",
            },
        ]
        sources = _extract_cited_sources(records)
        assert sources.count(canonicalize_search_url("https://example.com")) == 1

    def test_ignores_non_web_non_file_tools(self):
        records = [
            {
                "tool_name": "shell_command",
                "args_preview": '{"command": "echo hello"}',
                "output_preview": "hello",
            }
        ]
        sources = _extract_cited_sources(records)
        assert sources == []

    def test_empty_input(self):
        assert _extract_cited_sources([]) == []

    def test_multiple_tools_mixed(self):
        records = [
            {
                "tool_name": "web_search",
                "args_preview": "",
                "output_preview": "https://a.com/1 https://b.com/2",
            },
            {
                "tool_name": "pdf_read",
                "args_preview": '{"path": "/tmp/paper.pdf"}',
                "output_preview": "text",
            },
            {
                "tool_name": "current_datetime",
                "args_preview": "",
                "output_preview": "2026-03-09",
            },
        ]
        sources = _extract_cited_sources(records)
        assert "https://a.com/1" in sources
        assert "https://b.com/2" in sources
        assert "/tmp/paper.pdf" in sources
        assert len(sources) == 3
