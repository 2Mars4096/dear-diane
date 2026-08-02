from __future__ import annotations

from dan.keyword_overlap import query_keyword_overlap


def test_query_keyword_overlap_handles_empty_values() -> None:
    assert query_keyword_overlap("", "paper") == 0.0
    assert query_keyword_overlap("paper", "") == 0.0


def test_query_keyword_overlap_counts_shared_query_words() -> None:
    assert query_keyword_overlap("paper latex table", "latex table appendix") == 2 / 3
    assert query_keyword_overlap("machine learning", "supply chain logistics") == 0.0
    assert query_keyword_overlap("machine learning", "machine learning workflow") == 1.0
