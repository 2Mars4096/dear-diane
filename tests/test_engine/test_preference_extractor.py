"""Tests for PreferenceExtractor heuristics (Plan 26-3)."""

from __future__ import annotations

import pytest

from dan.engine.preference_extractor import PreferenceExtractor


class _StubBehaviorStore:
    def __init__(self, value):
        self._value = value

    def get(self, key: str, default=None):
        if key != "domains/keyword_maps":
            return default
        return self._value if self._value is not None else default


@pytest.fixture
def extractor() -> PreferenceExtractor:
    return PreferenceExtractor()


# ---------------------------------------------------------------------------
# Model preference extraction
# ---------------------------------------------------------------------------

class TestModelPreferences:
    def test_claude_for_drafting(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "Please use Claude Sonnet for drafting"}]
        result = extractor.extract_from_messages(msgs)
        assert "models" in result
        assert "drafting" in result["models"]
        assert "claude" in result["models"]["drafting"].lower()

    def test_gpt_for_review(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "I want GPT-4o to review my work"}]
        result = extractor.extract_from_messages(msgs)
        assert "models" in result
        assert "review" in result["models"]
        assert "gpt" in result["models"]["review"].lower()

    def test_gemini_for_analysis(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "Use Gemini Pro to analyze the data"}]
        result = extractor.extract_from_messages(msgs)
        assert "models" in result
        assert "analysis" in result["models"]
        assert "gemini" in result["models"]["analysis"].lower()

    def test_model_without_task_not_extracted(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "I like Claude Sonnet a lot"}]
        result = extractor.extract_from_messages(msgs)
        models = result.get("models", {})
        assert len(models) == 0 or all(
            k in ("drafting", "review", "coding", "analysis", "summarization")
            for k in models
        )

    def test_multiple_models_in_one_conversation(self, extractor: PreferenceExtractor):
        msgs = [
            {"role": "user", "content": "Use Claude Sonnet for writing the draft"},
            {"role": "assistant", "content": "OK, noted."},
            {"role": "user", "content": "And GPT-4o should handle the code"},
        ]
        result = extractor.extract_from_messages(msgs)
        models = result.get("models", {})
        assert "drafting" in models
        assert "coding" in models

    def test_assistant_messages_ignored(self, extractor: PreferenceExtractor):
        msgs = [
            {"role": "assistant", "content": "I recommend Claude Opus for writing"},
        ]
        result = extractor.extract_from_messages(msgs)
        assert result.get("models", {}) == {}

    def test_case_insensitive_model_match(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "please use CLAUDE SONNET to write the draft"}]
        result = extractor.extract_from_messages(msgs)
        assert "models" in result
        assert "drafting" in result["models"]


# ---------------------------------------------------------------------------
# Domain extraction
# ---------------------------------------------------------------------------

class TestDomainExtraction:
    def test_paper_rendering_keyword(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "I am writing a manuscript in LaTeX with BibTeX citations"}]
        result = extractor.extract_from_messages(msgs)
        assert "paper_rendering" in result.get("domains", [])

    def test_equity_research_keyword(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "Build a portfolio analysis workflow"}]
        result = extractor.extract_from_messages(msgs)
        assert "equity_research" in result.get("domains", [])

    def test_data_analysis_keyword(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "Please merge this pandas dataframe and aggregate the totals"}]
        result = extractor.extract_from_messages(msgs)
        assert "data_analysis" in result.get("domains", [])

    def test_literature_review_keyword(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "I need a systematic literature review with citation synthesis"}]
        result = extractor.extract_from_messages(msgs)
        assert "literature_review" in result.get("domains", [])

    def test_code_generation_keyword(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "Help me refactor and debug this script"}]
        result = extractor.extract_from_messages(msgs)
        assert "code_generation" in result.get("domains", [])

    def test_workflow_building_keyword(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "Can you automate and schedule this DAG for me?"}]
        result = extractor.extract_from_messages(msgs)
        assert "workflow_building" in result.get("domains", [])

    def test_multiple_domains(self, extractor: PreferenceExtractor):
        msgs = [
            {"role": "user", "content": "Use pandas to aggregate returns for an equity portfolio"},
        ]
        result = extractor.extract_from_messages(msgs)
        domains = result.get("domains", [])
        assert "data_analysis" in domains
        assert "equity_research" in domains

    def test_no_domain_match(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "Hello, how are you?"}]
        result = extractor.extract_from_messages(msgs)
        assert "domains" not in result

    def test_behavior_store_domain_map_overrides_seed_domains(self):
        extractor = PreferenceExtractor(
            behavior_store=_StubBehaviorStore(
                {"custom_research": ["needleterm", "rare signal"]}
            )
        )
        msgs = [{"role": "user", "content": "needleterm keeps showing up in my workflow"}]
        result = extractor.extract_from_messages(msgs)
        assert result.get("domains") == ["custom_research"]

    def test_explicit_domain_keywords_override_behavior_store(self):
        extractor = PreferenceExtractor(
            domain_keywords={"injected_domain": ["needle term"]},
            behavior_store=_StubBehaviorStore(
                {"behavior_store_domain": ["other term"]}
            ),
        )
        msgs = [{"role": "user", "content": "needle term is the important phrase"}]
        result = extractor.extract_from_messages(msgs)
        assert result.get("domains") == ["injected_domain"]

    def test_behavior_store_legacy_domain_key_is_canonicalized(self):
        extractor = PreferenceExtractor(
            behavior_store=_StubBehaviorStore(
                {"equity research": ["portfolio", "alpha"]}
            )
        )
        msgs = [{"role": "user", "content": "portfolio alpha signals matter here"}]
        result = extractor.extract_from_messages(msgs)
        assert result.get("domains") == ["equity_research"]

    def test_supply_chain_keywords_are_detected(self, extractor: PreferenceExtractor):
        msgs = [
            {
                "role": "user",
                "content": "I work in supply chain logistics and need help with inventory optimization",
            }
        ]
        result = extractor.extract_from_messages(msgs)
        domains = result.get("domains", [])
        assert "supply_chain_management" in domains
        assert "inventory_optimization" in domains

    def test_machine_learning_abbreviations_are_detected(self, extractor: PreferenceExtractor):
        msgs = [
            {
                "role": "user",
                "content": "My team works on ML systems and NLP evaluation pipelines",
            }
        ]
        result = extractor.extract_from_messages(msgs)
        domains = result.get("domains", [])
        assert "machine_learning" in domains
        assert "natural_language_processing" in domains


# ---------------------------------------------------------------------------
# Output format extraction
# ---------------------------------------------------------------------------

class TestOutputFormatExtraction:
    def test_latex_format(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "Please export as LaTeX"}]
        result = extractor.extract_from_messages(msgs)
        assert result.get("output_format") == "latex"

    def test_markdown_format(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "Output in markdown please"}]
        result = extractor.extract_from_messages(msgs)
        assert result.get("output_format") == "markdown"

    def test_json_format(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "Give me the result as JSON"}]
        result = extractor.extract_from_messages(msgs)
        assert result.get("output_format") == "json"

    def test_no_format_match(self, extractor: PreferenceExtractor):
        msgs = [{"role": "user", "content": "Just give me the results"}]
        result = extractor.extract_from_messages(msgs)
        assert "output_format" not in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_messages(self, extractor: PreferenceExtractor):
        result = extractor.extract_from_messages([])
        assert result == {}

    def test_idempotency(self, extractor: PreferenceExtractor):
        msgs = [
            {"role": "user", "content": "Use Claude Sonnet for writing my manuscript in LaTeX"},
        ]
        r1 = extractor.extract_from_messages(msgs)
        r2 = extractor.extract_from_messages(msgs)
        assert r1 == r2

    def test_all_preferences_in_one_message(self, extractor: PreferenceExtractor):
        msgs = [
            {"role": "user", "content": "Use GPT-4o to debug this pandas transform and export as JSON"},
        ]
        result = extractor.extract_from_messages(msgs)
        assert "coding" in result.get("models", {})
        assert "data_analysis" in result.get("domains", [])
        assert result.get("output_format") == "json"

    def test_path_heavy_memory_message_does_not_create_bogus_preferences(
        self,
        extractor: PreferenceExtractor,
    ):
        msgs = [{
            "role": "user",
            "content": (
                "I save my papers usually under "
                "~/Dropbox/Projects/my-knowledge-base/static/papers/, "
                "paper related notes are in "
                "~/Dropbox/Projects/my-knowledge-base/content/papers, "
                "and the Hugo project uses index.md files."
            ),
        }]
        result = extractor.extract_from_messages(msgs)
        assert result.get("domains", []) == []
        assert "output_format" not in result
