from __future__ import annotations

from dan import domain_taxonomy
from dan.engine import domain_taxonomy as legacy_domain_taxonomy


def test_legacy_domain_taxonomy_path_reexports_neutral_helpers() -> None:
    assert legacy_domain_taxonomy.normalize_domain_name is domain_taxonomy.normalize_domain_name
    assert legacy_domain_taxonomy.normalize_domain_list is domain_taxonomy.normalize_domain_list
    assert legacy_domain_taxonomy.preserve_domain_labels is domain_taxonomy.preserve_domain_labels
    assert legacy_domain_taxonomy.normalize_domain_keyword_map is domain_taxonomy.normalize_domain_keyword_map
    assert legacy_domain_taxonomy.format_domain_label is domain_taxonomy.format_domain_label


def test_domain_taxonomy_normalizes_aliases_and_labels() -> None:
    assert domain_taxonomy.normalize_domain_name("Scientific Writing") == "paper_rendering"
    assert domain_taxonomy.normalize_domain_name("Machine Learning") == "machine_learning"
    assert domain_taxonomy.normalize_domain_list(
        ["scientific writing", "paper_rendering", "Machine Learning", ""]
    ) == ["paper_rendering", "machine_learning"]
    assert domain_taxonomy.preserve_domain_labels(
        ["Scientific Writing", "paper_rendering", "Machine Learning"]
    ) == ["Scientific Writing", "Machine Learning"]
    assert domain_taxonomy.format_domain_label("paper_rendering") == "paper rendering"


def test_domain_taxonomy_keyword_map_merges_alias_buckets() -> None:
    assert domain_taxonomy.normalize_domain_keyword_map(
        {
            "scientific writing": ["LaTeX", "journal"],
            "paper_rendering": ["latex", "figures"],
            "": ["ignored"],
        }
    ) == {
        "paper_rendering": ["latex", "journal", "figures"],
    }
