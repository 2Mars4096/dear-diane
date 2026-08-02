from __future__ import annotations

from dan.engine.domain_taxonomy import (
    format_domain_label,
    normalize_domain_keyword_map,
    normalize_domain_list,
    normalize_domain_name,
)


class TestNormalizeDomainName:
    def test_maps_legacy_aliases_to_canonical_ids(self):
        assert normalize_domain_name("scientific writing") == "paper_rendering"
        assert normalize_domain_name("equity research") == "equity_research"
        assert normalize_domain_name("literature-review") == "literature_review"

    def test_unknown_values_fall_back_to_safe_slug(self):
        assert normalize_domain_name("Machine Learning") == "machine_learning"
        assert normalize_domain_name("Ops / Research") == "ops_research"

    def test_maps_abbreviations_case_insensitively(self):
        assert normalize_domain_name("ML") == "machine_learning"
        assert normalize_domain_name("nlp") == "natural_language_processing"
        assert normalize_domain_name("Cv") == "computer_vision"
        assert normalize_domain_name("RL") == "reinforcement_learning"


class TestNormalizeDomainList:
    def test_deduplicates_after_normalization(self):
        assert normalize_domain_list(
            ["scientific writing", "paper_rendering", "Machine Learning", "machine_learning"]
        ) == ["paper_rendering", "machine_learning"]


class TestNormalizeDomainKeywordMap:
    def test_merges_alias_buckets_under_canonical_key(self):
        result = normalize_domain_keyword_map(
            {
                "equity research": ["portfolio", "alpha"],
                "equity_research": ["alpha", "hedge"],
            }
        )
        assert result == {"equity_research": ["portfolio", "alpha", "hedge"]}


class TestFormatDomainLabel:
    def test_formats_canonical_domain_for_display(self):
        assert format_domain_label("paper_rendering") == "paper rendering"
        assert format_domain_label("Machine Learning") == "machine learning"
